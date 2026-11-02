"""acp: long-lived ACP agent sessions on each device, and the hub's choice of which device runs them."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dusha.commands import arg, option
from dusha.timeutil import isoformat, parse_time, utc_now
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.acp")

PROTOCOL_VERSION = 1
START_SECONDS = 60
CANCEL_GRACE_SECONDS = 5
ALLOW = ("allow_always", "allow_once")
UP_STATES = ("active", "idle", "awake")
OPERATIONS = ("cancel", "reset", "config", "sessions", "resume")
LOG_SIZE = 500
SWITCH_TTL_SECONDS = 15 * 60
NO_ROUTE = "-"


class Target(BaseModel):
    conversation_id: str = Field(min_length=1)
    route: str = ""
    harness: str = ""
    fresh: bool = False
    model: str = ""
    mode: str = ""
    session_id: str = ""


class Turn(Target):
    prompt: list[dict[str, Any]] = Field(min_length=1)
    proactive: bool = False


class Spawn(BaseModel):
    prompt: str = Field(min_length=1)
    agent: str = ""
    cwd: str = ""
    timeout_seconds: float = 0


class Pin(BaseModel):
    device: str = Field(min_length=1)


@dataclass
class Agent:
    command: list[str]
    model: str = ""
    mode: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Options:
    agents: dict[str, Agent] = field(default_factory=dict)
    default: str = ""
    timeout_seconds: float = 1800
    home: str = ""
    switch_after_minutes: float = 3
    check_seconds: float = 30
    startup_grace_seconds: float = 120


def parse_options(raw: dict[str, Any]) -> Options:
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown acp option(s): {unknown}")
    agents = {}
    for name, value in (raw.pop("agents", None) or {}).items():
        if not isinstance(value, dict) or not value.get("command"):
            raise ValueError(f"acp agent {name!r} needs a 'command' list")
        agents[name] = Agent(**value)
    options = Options(agents=agents, **raw)
    options.default = options.default or next(iter(agents), "")
    if options.default and options.default not in agents:
        raise ValueError(f"acp option 'default' names unknown agent {options.default!r}")
    return options


class AgentError(RuntimeError):
    pass


class DeviceError(RuntimeError):
    pass


class Client:
    """One ACP agent process: newline-delimited JSON-RPC over its stdin and stdout."""

    def __init__(self, command: list[str], cwd: str, env: dict[str, str]):
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [shutil.which(command[0]) or command[0], *command[1:]],
            cwd=cwd,
            env={**os.environ, **env},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            encoding="utf-8",
            bufsize=1,
        )
        self.lock = threading.RLock()
        self.pending: dict[int, queue.Queue[dict[str, Any] | None]] = {}
        self.next_id = 0
        self.capabilities: dict[str, Any] = {}
        self.listener: Callable[[dict[str, Any]], None] | None = None
        threading.Thread(target=self._read, name="acp-read", daemon=True).start()

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def start(self) -> None:
        params = {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "dusha-acp", "version": "0.1.0"},
        }
        self.capabilities = self.request("initialize", params, START_SECONDS).get("agentCapabilities") or {}

    def request(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        with self.lock:
            self.next_id += 1
            ident = self.next_id
            box: queue.Queue[dict[str, Any] | None] = queue.Queue(1)
            self.pending[ident] = box
            try:
                self._send({"id": ident, "method": method, "params": params})
            except OSError:
                self.pending.pop(ident, None)
                raise AgentError(f"agent exited before {method}: {self.errors()}") from None
        try:
            reply = box.get(timeout=timeout)
        except queue.Empty:
            self.pending.pop(ident, None)
            raise TimeoutError(method) from None
        if reply is None:
            raise AgentError(f"agent exited during {method}: {self.errors()}")
        if "error" in reply:
            error = reply["error"] or {}
            detail = error.get("data")
            text = str(error.get("message") or f"{method} failed")
            raise AgentError(f"{text}: {detail}" if detail else text)
        return reply.get("result") or {}

    def notify(self, method: str, params: dict[str, Any]) -> None:
        try:
            self._send({"method": method, "params": params})
        except OSError:
            pass

    def _send(self, message: dict[str, Any]) -> None:
        with self.lock:
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            self.process.stdin.flush()

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "method" not in message:
                box = self.pending.pop(message.get("id"), None)
                if box is not None:
                    box.put(message)
            elif "id" in message:
                self._answer(message)
            elif message["method"] == "session/update" and self.listener is not None:
                self.listener((message.get("params") or {}).get("update") or {})
        with self.lock:
            boxes, self.pending = list(self.pending.values()), {}
        for box in boxes:
            box.put(None)

    def _answer(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        try:
            if message["method"] == "session/request_permission":
                offered = params.get("options") or []
                pick = next((item for kind in ALLOW for item in offered if item.get("kind") == kind), None)
                outcome = (
                    {"outcome": "selected", "optionId": pick["optionId"]}
                    if pick
                    else {"outcome": "cancelled"}
                )
                self._send({"id": message["id"], "result": {"outcome": outcome}})
            else:
                error = {"code": -32601, "message": f"method not supported: {message['method']}"}
                self._send({"id": message["id"], "error": error})
        except OSError:
            pass

    def errors(self) -> str:
        if self.stderr.closed:
            return ""
        self.stderr.seek(0, os.SEEK_END)
        self.stderr.seek(max(self.stderr.tell() - 500, 0))
        return self.stderr.read().decode("utf-8", "replace").strip()

    def close(self) -> None:
        if self.alive:
            self.process.kill()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            pass
        self.stderr.close()


class Session:
    def __init__(self, conversation_id: str, agent: str, client: Client, cwd: str):
        self.conversation_id = conversation_id
        self.agent = agent
        self.client = client
        self.cwd = cwd
        self.session_id = ""
        self.modes: dict[str, Any] = {}
        self.options: list[dict[str, Any]] = []
        self.turn = threading.Lock()
        self.on_turn: Callable[[dict[str, Any]], None] | None = None
        client.listener = self._update

    def remember(self, result: dict[str, Any]) -> None:
        if result.get("modes"):
            self.modes = result["modes"]
        self.merge(result.get("configOptions") or [])

    def merge(self, options: list[dict[str, Any]]) -> None:
        known = {item.get("id"): item for item in self.options}
        known.update({item.get("id"): item for item in options})
        self.options = list(known.values())

    def option(self, option_id: str) -> dict[str, Any]:
        return next((item for item in self.options if item.get("id") == option_id), {})

    def info(self) -> dict[str, Any]:
        model = self.option("model")
        return {
            "session_id": self.session_id,
            "agent": self.agent,
            "model": model.get("currentValue"),
            "models": [str(item.get("value")) for item in model.get("options") or []],
            "mode": self.modes.get("currentModeId"),
        }

    def _update(self, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        if kind == "config_option_update":
            self.merge(update.get("configOptions") or [])
        elif kind == "current_mode_update":
            self.modes = {**self.modes, "currentModeId": update.get("currentModeId")}
        listener = self.on_turn
        if listener is not None:
            listener(update)


class Sessions:
    """This device's sessions, one agent process per dusha conversation."""

    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options
        self.live: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.path = ctx.data_dir / "sessions.json"
        self.store = self._load()
        self.log: deque[dict[str, Any]] = deque(maxlen=LOG_SIZE)
        self.log_lock = threading.Lock()
        self.name = ""
        self.watchers: list[queue.Queue[dict[str, Any]]] = []

    def device(self) -> str:
        if not self.name:
            answers = list(self.ctx.collect("presence").values())
            self.name = str(answers[0]["local"]) if answers else socket.gethostname().split(".")[0]
        return self.name

    def available(self) -> list[str]:
        return [name for name, item in self.options.agents.items() if shutil.which(item.command[0])]

    def capabilities(self) -> list[str]:
        return ["acp", "spawn"] if self.available() else []

    # Sessions.

    def _agent(self, name: str) -> Agent:
        chosen = self.options.agents.get(name)
        if chosen is None:
            raise ValueError(f"unknown agent {name!r}, configured: {sorted(self.options.agents)}")
        if name not in self.available():
            raise ValueError(f"agent {name!r} needs {chosen.command[0]!r}, which is not installed here")
        return chosen

    def _open(self, target: Target, agent: str = "", cwd: str = "", keep: bool = True) -> Session:
        conversation = target.conversation_id
        with self.lock:
            if target.fresh:
                self._drop(conversation)
                self.store["conversations"].pop(conversation, None)
            session = self.live.get(conversation)
            if session is not None and session.client.alive:
                return session
            self._drop(conversation)
            saved = self.store["conversations"].get(conversation, {}) if keep else {}
            name = agent or saved.get("agent") or self.options.default
            chosen = self._agent(name)
            folder = str(Path(cwd or chosen.cwd or Path.home()).expanduser())
            if not Path(folder).is_dir():
                raise ValueError(f"no such folder: {folder}")
            env = {
                **chosen.env,
                "DUSHA_CONVERSATION_ID": conversation,
                "DUSHA_ROUTE": target.route or NO_ROUTE,
                "DUSHA_HARNESS": target.harness or "acp",
            }
            client = Client(chosen.command, folder, env)
            try:
                session = Session(conversation, name, client, folder)
                client.start()
                self._begin(session, saved.get("session_id", ""))
                mode = saved.get("mode") or chosen.mode
                if mode and session.modes and session.modes.get("currentModeId") != mode:
                    self._set_mode(session, mode)
                model = self.store["routes"].get(target.route, {}).get("model") or chosen.model
                if model and session.option("model").get("currentValue") not in (None, model):
                    self._set_model(session, model)
            except Exception:
                client.close()
                raise
            if keep:
                self.store["conversations"][conversation] = {
                    "session_id": session.session_id,
                    "agent": name,
                    "mode": saved.get("mode", ""),
                }
                self._save()
            self.live[conversation] = session
            return session

    def _begin(self, session: Session, saved: str) -> None:
        if saved and session.client.capabilities.get("loadSession"):
            params = {"sessionId": saved, "cwd": session.cwd, "mcpServers": []}
            try:
                session.remember(session.client.request("session/load", params, START_SECONDS))
                session.session_id = saved
                return
            except AgentError as error:
                logger.warning("could not load session %s, starting fresh: %s", saved, error)
        result = session.client.request("session/new", {"cwd": session.cwd, "mcpServers": []}, START_SECONDS)
        session.session_id = result["sessionId"]
        session.remember(result)

    def _set_mode(self, session: Session, mode: str) -> None:
        params = {"sessionId": session.session_id, "modeId": mode}
        session.client.request("session/set_mode", params, START_SECONDS)
        session.modes = {**session.modes, "currentModeId": mode}

    def _set_model(self, session: Session, model: str) -> None:
        params = {"sessionId": session.session_id, "configId": "model", "value": model}
        result = session.client.request("session/set_config_option", params, START_SECONDS)
        session.merge(result.get("configOptions") or [])

    def _drop(self, conversation: str) -> None:
        session = self.live.pop(conversation, None)
        if session is not None:
            session.client.close()

    # Turns.

    def turn(self, body: Turn) -> Iterator[dict[str, Any]]:
        conversation = body.conversation_id
        try:
            session = self._open(body)
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            yield self._record(
                conversation, {"event": "error", "message": str(error) or type(error).__name__}
            )
            return
        if not session.turn.acquire(blocking=False):
            yield self._record(conversation, {"event": "error", "message": "a turn is already running"})
            return
        try:
            yield from self._prompt(session, body.prompt, body.route)
        finally:
            session.turn.release()
            if not session.client.alive:
                with self.lock:
                    if self.live.get(conversation) is session:
                        self._drop(conversation)

    def _prompt(self, session: Session, prompt: list[dict[str, Any]], label: str) -> Iterator[dict[str, Any]]:
        conversation = session.conversation_id
        vision = bool((session.client.capabilities.get("promptCapabilities") or {}).get("image"))
        blocks = [block for block in prompt if vision or block.get("type") != "image"]
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        final: list[str] = []

        def listen(update: dict[str, Any]) -> None:
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                content = update.get("content") or {}
                if content.get("type") == "text":
                    final.append(str(content.get("text") or ""))
                    events.put({"event": "chunk", "text": final[-1]})
            elif kind in {"tool_call", "tool_call_update"}:
                if kind == "tool_call":
                    final.clear()
                tool = {"id": str(update.get("toolCallId") or ""), "title": str(update.get("title") or "")}
                events.put({"event": "tool", **tool, "status": str(update.get("status") or "")})

        def run() -> None:
            try:
                params = {"sessionId": session.session_id, "prompt": blocks}
                result = session.client.request("session/prompt", params)
                events.put({"event": "done", "stop_reason": str(result.get("stopReason") or "end_turn")})
            except Exception as error:
                events.put({"event": "error", "message": str(error) or type(error).__name__})

        session.on_turn = listen
        worker = threading.Thread(target=run, name="acp-turn", daemon=True)
        worker.start()
        yield self._record(conversation, {"event": "start", "session_id": session.session_id})
        try:
            while True:
                event = events.get()
                if event["event"] == "done":
                    event["text"] = "".join(final).strip()
                    self._observe(session, label, event["stop_reason"])
                yield self._record(conversation, event)
                if event["event"] in {"done", "error"}:
                    return
        finally:
            session.on_turn = None
            if worker.is_alive():
                session.client.notify("session/cancel", {"sessionId": session.session_id})
                worker.join(CANCEL_GRACE_SECONDS)

    def _observe(self, session: Session, label: str, stop_reason: str) -> None:
        text = f"{session.agent} in {session.cwd} for {label or session.conversation_id}: {stop_reason}"
        self.ctx.emit("ambient.observe", {"source": "acp", "text": text})

    def _record(self, conversation: str, event: dict[str, Any]) -> dict[str, Any]:
        event = {
            **event,
            "conversation_id": conversation,
            "device": self.device(),
            "at": isoformat(utc_now()),
        }
        with self.log_lock:
            self.log.append(event)
            watchers = list(self.watchers)
        for watcher in watchers:
            watcher.put(event)
        return event

    def follow(self) -> Iterator[dict[str, Any] | None]:
        watcher: queue.Queue[dict[str, Any]] = queue.Queue()
        with self.log_lock:
            self.watchers.append(watcher)
        try:
            while True:
                try:
                    yield watcher.get(timeout=15)
                except queue.Empty:
                    yield None
        finally:
            with self.log_lock:
                self.watchers.remove(watcher)

    # Commands on a conversation.

    def cancel(self, target: Target) -> dict[str, Any]:
        session = self.live.get(target.conversation_id)
        if session is None or not session.turn.locked():
            return {"cancelled": False}
        session.client.notify("session/cancel", {"sessionId": session.session_id})
        return {"cancelled": True}

    def reset(self, target: Target) -> dict[str, Any]:
        with self.lock:
            self._drop(target.conversation_id)
            self.store["conversations"].pop(target.conversation_id, None)
            self._save()
        return {"reset": True}

    def config(self, target: Target) -> dict[str, Any]:
        session = self._open(target)
        with self.lock:
            if target.model:
                self._set_model(session, target.model)
                self.store["routes"].setdefault(target.route, {})["model"] = target.model
            if target.mode:
                self._set_mode(session, target.mode)
                self.store["conversations"][target.conversation_id]["mode"] = target.mode
            self._save()
        return session.info()

    def sessions(self, target: Target) -> dict[str, Any]:
        session = self._open(target)
        listed = session.client.request("session/list", {"cwd": session.cwd}, START_SECONDS)
        return {"current": session.session_id, "sessions": list(listed.get("sessions") or [])[:15]}

    def resume(self, target: Target) -> dict[str, Any]:
        if not target.session_id:
            raise ValueError("resume needs a session_id")
        with self.lock:
            self._drop(target.conversation_id)
            saved = self.store["conversations"].get(target.conversation_id, {})
            self.store["conversations"][target.conversation_id] = {**saved, "session_id": target.session_id}
        session = self._open(target.model_copy(update={"fresh": False}))
        return {**session.info(), "loaded": session.session_id == target.session_id}

    # relay's spawn tool: a throwaway session for one prompt.

    def run(self, prompt: str, agent: str = "", cwd: str = "", timeout_seconds: float = 0) -> dict[str, Any]:
        name = agent or self.options.default
        target = Target(conversation_id=f"spawn-{uuid.uuid4().hex[:12]}", route=NO_ROUTE, harness="spawn")
        started = time.monotonic()
        deadline = started + (timeout_seconds or self.options.timeout_seconds)
        try:
            session = self._open(target, agent=name, cwd=cwd, keep=False)
        except (OSError, RuntimeError, TimeoutError) as error:
            raise ValueError(f"{name} failed: {error}") from error
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def collect() -> None:
            for event in self._prompt(session, [{"type": "text", "text": prompt}], "spawn"):
                events.put(event)
            events.put(None)

        threading.Thread(target=collect, name="acp-spawn", daemon=True).start()
        answer: list[str] = []
        tools: dict[str, str] = {}
        stop_reason = "timeout"
        try:
            while (event := events.get(timeout=max(deadline - time.monotonic(), 0.01))) is not None:
                if event["event"] == "chunk":
                    answer.append(event["text"])
                elif event["event"] == "tool" and event["title"]:
                    tools[event["id"]] = event["title"]
                elif event["event"] == "done":
                    stop_reason = event["stop_reason"]
                elif event["event"] == "error":
                    raise ValueError(f"{name} failed: {event['message']}")
        except queue.Empty:
            session.client.notify("session/cancel", {"sessionId": session.session_id})
            time.sleep(min(CANCEL_GRACE_SECONDS, 0.5))
        finally:
            with self.lock:
                self._drop(target.conversation_id)
        return {
            "agent": name,
            "cwd": session.cwd,
            "stop_reason": stop_reason,
            "answer": "".join(answer).strip(),
            "tools": list(tools.values()),
            "seconds": round(time.monotonic() - started, 1),
        }

    def local(self, body: dict[str, Any]) -> dict[str, Any]:
        """relay in this same process, where a raised error would be logged and lost."""
        try:
            return self.run(**body)
        except ValueError as error:
            return {"error": str(error)}

    # State.

    def _load(self) -> dict[str, Any]:
        try:
            store = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            store = {}
        store.setdefault("conversations", {})
        store.setdefault("routes", {})
        return store

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.store, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def close(self) -> None:
        with self.lock:
            for conversation in list(self.live):
                self._drop(conversation)


class Executor:
    """The hub's choice of the device that runs the next turn, from presence."""

    def __init__(self, ctx: Any, options: Options, sessions: Sessions):
        self.ctx = ctx
        self.options = options
        self.sessions = sessions
        self.lock = threading.RLock()
        self.path = ctx.data_dir / "executor.json"
        self.state = self._load()
        self.running: dict[str, str] = {}
        self.switches: list[dict[str, Any]] = []
        self.started = time.monotonic()

    def presence(self) -> dict[str, Any] | None:
        answers = list(self.ctx.collect("presence").values())
        return answers[0] if answers else None

    def local(self) -> str:
        return self.sessions.device()

    def home(self) -> str:
        return self.options.home or self.local()

    def choose(self, snapshot: dict[str, Any] | None) -> str:
        home = self.home()
        if not snapshot:
            return home
        devices = {item["device"]: item for item in snapshot["devices"]}

        def ready(name: str) -> bool:
            entry = devices.get(name)
            if name == self.local():
                return bool(self.sessions.available())
            return bool(entry) and "acp" in entry["capabilities"] and entry["status"] in UP_STATES

        pinned = self.state["pinned"]
        if pinned != "auto":
            return pinned if ready(pinned) else home
        current = self.state["device"]
        waiting = time.monotonic() - self.started < self.options.startup_grace_seconds
        if waiting and current and current != self.local() and current not in devices:
            return current
        active = snapshot.get("active_device")
        if active and ready(active):
            since = devices[active].get("since")
            settled = not since or (utc_now() - parse_time(since)).total_seconds() >= (
                self.options.switch_after_minutes * 60
            )
            if active == self.state["device"] or settled:
                return active
        current = self.state["device"]
        if current and current in devices and ready(current):
            return current
        return home

    def refresh(self) -> str:
        snapshot = self.presence()
        with self.lock:
            device = self.choose(snapshot)
            old = self.state["device"]
            if device == old:
                return device
            now = utc_now()
            self.state.update(device=device, since=isoformat(now))
            self._save()
            if old:
                self.switches.append({"old": old, "new": device, "at": isoformat(now)})
            self.ctx.emit("acp.switched", {"old": old, "new": device, "at": isoformat(now)})
            return device

    def candidates(self, now: Any = None) -> list[dict[str, Any]]:
        self.refresh()
        current = utc_now()
        with self.lock:
            self.switches = [
                item
                for item in self.switches
                if (current - parse_time(item["at"])).total_seconds() < SWITCH_TTL_SECONDS
            ]
            switches = list(self.switches)
        home = self.home()
        return [
            {
                "source": "acp",
                "reason": "device_switch",
                "topic": (
                    f"They have been on the {item['new']} for a while, so you now run there, "
                    f"with its files and shell, instead of on the {item['old']}."
                    if item["new"] != home
                    else f"They left the {item['old']}, so you run on the {item['new']} again."
                ),
                "score": 0.3,
                "dedup_key": f"acp:switch:{item['old']}:{item['new']}:{item['at']}",
            }
            for item in switches
        ]

    def info(self) -> dict[str, Any]:
        self.refresh()
        with self.lock:
            return {
                "device": self.state["device"],
                "pinned": self.state["pinned"],
                "since": self.state["since"],
                "home": self.home(),
                "switches": list(self.switches),
            }

    def pin(self, device: str) -> dict[str, Any]:
        if device != "auto":
            snapshot = self.presence() or {"devices": []}
            known = {item["device"] for item in snapshot["devices"]} | {self.local()}
            if device not in known:
                raise ValueError(f"unknown device {device!r}, known devices: {sorted(known)}")
        with self.lock:
            self.state["pinned"] = device
            self._save()
        return self.info()

    # Turns and commands, on the chosen device.

    def _place(self, target: Target) -> tuple[str, Target]:
        device = self.refresh()
        with self.lock:
            last = self.state["conversations"].get(target.conversation_id)
        fresh = target.fresh or (last is not None and last != device)
        return device, target.model_copy(update={"fresh": fresh})

    def _ran(self, conversation: str, device: str) -> None:
        with self.lock:
            self.state["conversations"][conversation] = device
            self._save()

    def turn(self, body: Turn) -> Iterator[dict[str, Any]]:
        conversation = body.conversation_id
        device, body = self._place(body)
        self.running[conversation] = device
        started = False
        try:
            try:
                for event in self._stream(device, body):
                    if event.get("event") == "start":
                        started = True
                        self._ran(conversation, device)
                    yield event
                return
            except DeviceError as error:
                home = self.home()
                if started or device == home:
                    yield {"event": "error", "message": str(error), "device": device}
                    return
                logger.warning("acp could not run on %s, falling back to %s: %s", device, home, error)
            with self.lock:
                last = self.state["conversations"].get(conversation)
            body = body.model_copy(update={"fresh": last is not None and last != home})
            device = self.running[conversation] = home
            for event in self._stream(home, body):
                if event.get("event") == "start":
                    self._ran(conversation, home)
                yield event
        except DeviceError as error:
            yield {"event": "error", "message": str(error), "device": device}
        finally:
            self.running.pop(conversation, None)

    def _endpoint(self, device: str) -> tuple[str, dict[str, str]]:
        answers = list(self.ctx.collect("relay.endpoint", device, "acp").values())
        if not answers:
            raise DeviceError(f"acp needs relay to reach {device}")
        if "error" in answers[0]:
            raise DeviceError(answers[0]["error"])
        return answers[0]["url"], answers[0].get("headers") or {}

    def _stream(self, device: str, body: Turn) -> Iterator[dict[str, Any]]:
        if device == self.local():
            yield from self.sessions.turn(body)
            return
        url, headers = self._endpoint(device)
        timeout = httpx.Timeout(30, read=None)
        try:
            with httpx.stream(
                "POST",
                url + "/state/v1/acp/local/turn",
                json=body.model_dump(),
                headers=headers,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise DeviceError(f"{device} answered {response.status_code}: {response.text[:300]}")
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[5:])
        except httpx.HTTPError as error:
            raise DeviceError(f"cannot reach {device} at {url}: {error}") from error

    def call(self, operation: str, target: Target) -> dict[str, Any]:
        if operation == "cancel":
            device = self.running.get(target.conversation_id)
            if device is None:
                return {"cancelled": False}
        elif operation == "reset":
            with self.lock:
                device = self.state["conversations"].pop(target.conversation_id, None)
                self._save()
            if device is None:
                return {"reset": True}
        else:
            device, target = self._place(target)
        try:
            result = self._call(device, operation, target)
        except DeviceError:
            if operation in {"cancel", "reset"}:
                return {"cancelled": False} if operation == "cancel" else {"reset": True}
            raise
        if operation not in {"cancel", "reset"}:
            self._ran(target.conversation_id, device)
        return {"device": device, **result}

    def _call(self, device: str, operation: str, target: Target) -> dict[str, Any]:
        if device == self.local():
            return getattr(self.sessions, operation)(target)
        url, headers = self._endpoint(device)
        try:
            response = httpx.post(
                url + f"/state/v1/acp/local/{operation}",
                json=target.model_dump(),
                headers=headers,
                timeout=120,
            )
        except httpx.HTTPError as error:
            raise DeviceError(f"cannot reach {device} at {url}: {error}") from error
        if response.status_code == 422:
            detail = response.json().get("detail")
            raise ValueError(detail if isinstance(detail, str) else f"{device} refused it: {detail}")
        if response.status_code >= 400:
            raise DeviceError(f"{device} answered {response.status_code}: {response.text[:300]}")
        return response.json()

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("pinned", "auto")
        state.setdefault("device", "")
        state.setdefault("since", None)
        state.setdefault("conversations", {})
        return state

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def _sse(events: Iterator[dict[str, Any] | None]) -> Iterator[str]:
    for event in events:
        yield ": ping\n\n" if event is None else f"data: {json.dumps(event)}\n\n"


def apply(ctx: Any, config: dict[str, Any]) -> None:
    options = parse_options(config)
    sessions = Sessions(ctx, options)
    executor = Executor(ctx, options, sessions)
    ctx.on("presence.capabilities", sessions.capabilities)
    ctx.on("spawn.run", sessions.local)
    ctx.on("proactive.candidates", executor.candidates)
    ctx.interval(options.check_seconds, executor.refresh)
    ctx.on_dispose(sessions.close)
    _routes(ctx.api, sessions, executor)
    group = ctx.cli.group("acp", "ACP agent sessions, on the device the user is at")
    group.command("where", executor.info, help="Show which device runs the agent, and why")
    group.command(
        "here",
        executor.pin,
        help="Pin the agent to a device, or auto to follow presence",
        args=[arg("device")],
    )
    group.command(
        "log",
        lambda limit: list(sessions.log)[-limit:],
        help="Show the latest turn events on this device",
        args=[option("limit", int, default=50)],
    )
    group.command("agents", sessions.available, help="List the agents installed here")
    group.command(
        "run",
        sessions.run,
        help="Run an agent on a prompt in a throwaway session and print its answer",
        args=[arg("prompt"), option("agent", default=""), option("cwd", default="")],
    )


def _guard(handler: Callable[[], Any]) -> Any:
    try:
        return handler()
    except (ValueError, RuntimeError, TimeoutError) as error:
        raise HTTPException(status_code=422, detail=str(error) or type(error).__name__) from error


def _operation(executor: Executor, sessions: Sessions, operation: str) -> tuple[Callable, Callable]:
    def hub(body: Target) -> dict[str, Any]:
        return _guard(lambda: executor.call(operation, body))

    def local(body: Target) -> dict[str, Any]:
        return _guard(lambda: getattr(sessions, operation)(body))

    return hub, local


def _routes(router: APIRouter, sessions: Sessions, executor: Executor) -> None:
    @router.post("/state/v1/acp/turn", tags=["acp"])
    def turn(body: Turn) -> StreamingResponse:
        return StreamingResponse(_sse(executor.turn(body)), media_type="text/event-stream")

    @router.post("/state/v1/acp/local/turn", tags=["acp"])
    def local_turn(body: Turn) -> StreamingResponse:
        return StreamingResponse(_sse(sessions.turn(body)), media_type="text/event-stream")

    for operation in OPERATIONS:
        hub, local = _operation(executor, sessions, operation)
        router.add_api_route(f"/state/v1/acp/{operation}", hub, methods=["POST"], tags=["acp"])
        router.add_api_route(f"/state/v1/acp/local/{operation}", local, methods=["POST"], tags=["acp"])

    @router.get("/state/v1/acp/executor", tags=["acp"])
    def where() -> dict[str, Any]:
        return executor.info()

    @router.put("/state/v1/acp/executor", tags=["acp"])
    def here(body: Pin) -> dict[str, Any]:
        return _guard(lambda: executor.pin(body.device))

    @router.get("/state/v1/acp/log", tags=["acp"], response_model=None)
    def log(follow: bool = False) -> Any:
        if follow:
            return StreamingResponse(_sse(sessions.follow()), media_type="text/event-stream")
        return {"events": list(sessions.log)}

    @router.post("/state/v1/spawn", tags=["acp"])
    def spawn(body: Spawn) -> dict[str, Any]:
        return _guard(lambda: sessions.run(body.prompt, body.agent, body.cwd, body.timeout_seconds))
