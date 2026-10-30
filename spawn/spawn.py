"""spawn: run a configured ACP agent on this machine for one prompt and return its answer."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dusha.commands import arg, option
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.spawn")

PROTOCOL_VERSION = 1
CANCEL_GRACE_SECONDS = 5


class Spawn(BaseModel):
    prompt: str = Field(min_length=1)
    agent: str = ""
    cwd: str = ""
    timeout_seconds: float = 0


@dataclass
class Agent:
    command: list[str]
    model: str = ""
    cwd: str = ""


@dataclass
class Options:
    agents: dict[str, Agent] = field(default_factory=dict)
    default: str = ""
    timeout_seconds: float = 1800


def parse_options(raw: dict[str, Any]) -> Options:
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown spawn option(s): {unknown}")
    agents = {}
    for name, value in (raw.pop("agents", None) or {}).items():
        if not isinstance(value, dict) or not value.get("command"):
            raise ValueError(f"spawn agent {name!r} needs a 'command' list")
        agents[name] = Agent(**value)
    options = Options(agents=agents, **raw)
    options.default = options.default or next(iter(agents), "")
    if options.default and options.default not in agents:
        raise ValueError(f"spawn option 'default' names unknown agent {options.default!r}")
    return options


class Acp:
    """A client for one ACP agent process: newline-delimited JSON-RPC over its stdin and stdout."""

    def __init__(self, command: list[str], cwd: str):
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [shutil.which(command[0]) or command[0], *command[1:]],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            encoding="utf-8",
            bufsize=1,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.next_id = 0
        self.chunks: list[str] = []
        self.tools: dict[str, str] = {}
        threading.Thread(target=self._read, name="spawn-acp", daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], deadline: float) -> dict[str, Any]:
        self.next_id += 1
        self._send({"id": self.next_id, "method": method, "params": params})
        return self.wait(self.next_id, method, deadline)

    def wait(self, ident: int, method: str, deadline: float) -> dict[str, Any]:
        while True:
            try:
                line = self.lines.get(timeout=max(deadline - time.monotonic(), 0.01))
            except queue.Empty:
                raise TimeoutError(method) from None
            if line is None:
                raise RuntimeError(f"agent exited during {method}: {self.errors()}")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == ident and "method" not in message:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error'].get('message')}")
                return message.get("result") or {}
            if "method" in message:
                self._incoming(message)

    def _incoming(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        if message["method"] == "session/update":
            self._update(params.get("update") or {})
        elif "id" not in message:
            return
        elif message["method"] == "session/request_permission":
            self._send({"id": message["id"], "result": {"outcome": _approve(params.get("options") or [])}})
        else:
            self._send({"id": message["id"], "error": {"code": -32601, "message": "not supported"}})

    def _update(self, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                self.chunks.append(content.get("text") or "")
        elif kind in {"tool_call", "tool_call_update"} and update.get("title"):
            self.tools[str(update.get("toolCallId"))] = str(update["title"])

    def errors(self) -> str:
        self.stderr.seek(0, os.SEEK_END)
        self.stderr.seek(max(self.stderr.tell() - 500, 0))
        return self.stderr.read().decode("utf-8", "replace").strip()

    def close(self) -> None:
        self.process.kill()
        self.process.wait(5)
        self.stderr.close()


def _approve(offered: list[dict[str, Any]]) -> dict[str, Any]:
    for kind in ("allow_always", "allow_once"):
        for item in offered:
            if item.get("kind") == kind:
                return {"outcome": "selected", "optionId": item.get("optionId")}
    return {"outcome": "cancelled"}


class Spawner:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options
        self.running: set[Acp] = set()

    def available(self) -> list[str]:
        return [name for name, item in self.options.agents.items() if shutil.which(item.command[0])]

    def run(self, prompt: str, agent: str = "", cwd: str = "", timeout_seconds: float = 0) -> dict[str, Any]:
        name = agent or self.options.default
        chosen = self.options.agents.get(name)
        if chosen is None:
            raise ValueError(f"unknown agent {name!r}, configured: {sorted(self.options.agents)}")
        if name not in self.available():
            raise ValueError(f"agent {name!r} needs {chosen.command[0]!r}, which is not installed here")
        folder = str(Path(cwd or chosen.cwd or Path.home()).expanduser())
        if not Path(folder).is_dir():
            raise ValueError(f"no such folder: {folder}")
        started = time.monotonic()
        deadline = started + (timeout_seconds or self.options.timeout_seconds)
        client = Acp(chosen.command, folder)
        self.running.add(client)
        try:
            stop_reason = self._prompt(client, chosen, prompt, folder, deadline)
            result = {
                "agent": name,
                "cwd": folder,
                "stop_reason": stop_reason,
                "answer": "".join(client.chunks).strip(),
                "tools": list(client.tools.values()),
                "seconds": round(time.monotonic() - started, 1),
            }
        except (OSError, RuntimeError) as error:
            raise ValueError(f"{name} failed: {error}") from error
        finally:
            self.running.discard(client)
            client.close()
        text = f"{name} in {folder}: {stop_reason} after {result['seconds']:.0f}s, {prompt[:200]}"
        self.ctx.emit("ambient.observe", {"source": "spawn", "text": text})
        return result

    def local(self, body: dict[str, Any]) -> dict[str, Any]:
        """relay in this same process, where a raised error would be logged and lost."""
        try:
            return self.run(**body)
        except ValueError as error:
            return {"error": str(error)}

    def _prompt(self, client: Acp, agent: Agent, prompt: str, cwd: str, deadline: float) -> str:
        session = ""
        try:
            client.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {"name": "dusha-spawn", "version": "0.1.0"},
                },
                deadline,
            )
            session = client.request("session/new", {"cwd": cwd, "mcpServers": []}, deadline)["sessionId"]
            if agent.model:
                client.request(
                    "session/set_config_option",
                    {"sessionId": session, "configId": "model", "value": agent.model},
                    deadline,
                )
            content = [{"type": "text", "text": prompt}]
            answer = client.request("session/prompt", {"sessionId": session, "prompt": content}, deadline)
            return str(answer.get("stopReason") or "end_turn")
        except TimeoutError:
            if session:
                client.notify("session/cancel", {"sessionId": session})
                try:
                    client.wait(client.next_id, "session/cancel", time.monotonic() + CANCEL_GRACE_SECONDS)
                except (TimeoutError, RuntimeError):
                    pass
            return "timeout"

    def close(self) -> None:
        for client in list(self.running):
            client.process.kill()


def apply(ctx: Any, config: dict[str, Any]) -> None:
    spawner = Spawner(ctx, parse_options(config))
    ctx.on("presence.capabilities", lambda: ["spawn"] if spawner.available() else [])
    ctx.on("spawn.run", spawner.local)
    ctx.on_dispose(spawner.close)
    _routes(ctx.api, spawner)
    group = ctx.cli.group("spawn", "ACP agents on this machine")
    group.command(
        "run",
        spawner.run,
        help="Run an agent on a prompt and print its answer",
        args=[arg("prompt"), option("agent", default=""), option("cwd", default="")],
    )
    group.command("agents", spawner.available, help="List the agents installed here")


def _routes(router: APIRouter, spawner: Spawner) -> None:
    @router.post("/state/v1/spawn", tags=["spawn"])
    def spawn(body: Spawn) -> dict[str, Any]:
        try:
            return spawner.run(body.prompt, body.agent, body.cwd, body.timeout_seconds)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
