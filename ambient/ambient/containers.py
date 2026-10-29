"""Podman and Docker containers: crashes, OOM kills, health, restart loops, and their stderr."""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .streams import TESTS, Action, Folder, mask

KEYS = {"runtimes", "exclude", "logs", "fold_minutes", "lines_per_minute", "settle_seconds"}
# How a stopped container exits: SIGINT, SIGKILL, SIGTERM. Podman sends no stop event before it dies.
STOPPED = {0, 130, 137, 143}
LOOP_EXITS, LOOP_SECONDS = 3, 600


@dataclass
class Event:
    name: str
    action: str  # start, die, oom, health, stop, remove
    labels: dict[str, str] = field(default_factory=dict)
    exit_code: int | None = None
    health: str = ""
    at: float = 0.0


def parse(runtime: str, line: str) -> Event | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if runtime == "podman":
        action = {"died": "die", "kill": "stop", "health_status": "health"}.get(
            raw.get("Status"), raw.get("Status")
        )
        labels = raw.get("Attributes") or {}
        code = raw.get("ContainerExitCode")
        return Event(
            raw.get("Name", ""), action, labels, code, raw.get("HealthStatus") or "", raw.get("time", 0)
        )
    actor = (raw.get("Actor") or {}).get("Attributes") or {}
    status = raw.get("Action") or raw.get("status") or ""
    health = status.split(": ", 1)[1] if status.startswith("health_status") else ""
    action = {"kill": "stop", "destroy": "remove"}.get(status, "health" if health else status)
    code = int(actor["exitCode"]) if actor.get("exitCode", "").lstrip("-").isdigit() else None
    return Event(actor.get("name", ""), action, actor, code, health, raw.get("time", 0))


def skipped(labels: dict[str, str]) -> bool:
    """Toolbox and distrobox containers are shells, not services."""
    return "com.github.containers.toolbox" in labels or labels.get("manager") == "distrobox"


class Containers:
    def __init__(self, options: dict[str, Any], follow: Callable[..., Any]):
        wanted = options.get("runtimes") or ["podman", "docker"]
        self.runtimes = [runtime for runtime in wanted if shutil.which(runtime)]
        exclude = options.get("exclude")
        self.exclude = list(TESTS if exclude is None else exclude)  # glob patterns on names
        self.logs = bool(options.get("logs", True))
        self.per_minute = int(options.get("lines_per_minute", 20))
        self.settle_seconds = float(options.get("settle_seconds", 120))
        self.folder = Folder(float(options.get("fold_minutes", 60)))
        self.follow = follow
        self.lock = threading.Lock()
        self.log_streams: dict[str, Any] = {}
        self.stopping: dict[str, float] = {}
        self.exits: dict[str, list[float]] = {}
        self.started: dict[str, float] = {}
        self.lines: dict[str, list[int]] = {}  # name: [minute, written, dropped]

    def start(self) -> None:
        for runtime in self.runtimes:
            command = [runtime, "events", "--format", "json" if runtime == "podman" else "{{json .}}"]
            command += ["--filter", "type=container"]
            self.follow(f"{runtime} events", lambda command=command: command, self.events_handler(runtime))
            if self.logs:
                for name, labels in self.running(runtime):
                    if not self.excluded(name) and not skipped(labels):
                        self.follow_logs(runtime, name)

    def excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.exclude)

    def running(self, runtime: str) -> list[tuple[str, dict[str, str]]]:
        fmt = "json" if runtime == "podman" else "{{json .}}"
        try:
            out = subprocess.run(
                [runtime, "ps", "--format", fmt], capture_output=True, text=True, timeout=30, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        if runtime == "podman":
            return [(item["Names"][0], item.get("Labels") or {}) for item in json.loads(out or "[]")]
        found = []
        for line in out.splitlines():
            item = json.loads(line)
            labels = dict(pair.split("=", 1) for pair in item.get("Labels", "").split(",") if "=" in pair)
            found.append((item.get("Names", ""), labels))
        return found

    def follow_logs(self, runtime: str, name: str) -> None:
        with self.lock:
            if name in self.log_streams:
                return
            command = [runtime, "logs", "--follow", "--tail", "0", name]
            self.log_streams[name] = self.follow(
                f"{name} logs",
                lambda: command,
                lambda line: self.log_line(name, line),
                restart=False,
                stderr=True,
            )

    def stop_logs(self, name: str) -> None:
        with self.lock:
            stream = self.log_streams.pop(name, None)
        if stream is not None:
            stream.stop()

    def events_handler(self, runtime: str) -> Callable[[str], list[Action]]:
        def handle(line: str) -> list[Action]:
            event = parse(runtime, line)
            if event is None or not event.name or self.excluded(event.name) or skipped(event.labels):
                return []
            return self.event(runtime, event)

        return handle

    def event(self, runtime: str, event: Event) -> list[Action]:
        name, at = event.name, event.at or time.time()
        key = f"container:{name}"
        # Quadlet containers are systemd units, so the journal's unit alert already covers them.
        alerts = "PODMAN_SYSTEMD_UNIT" not in event.labels
        data = {"container": name, "runtime": runtime}
        out: list[Action] = []
        if event.action == "start":
            self.started[name] = at
            if self.logs:
                self.follow_logs(runtime, name)
        elif event.action == "stop":
            self.stopping[name] = at
        elif event.action == "oom" and alerts:
            out.append(Action("alert", f"`{name}` was OOM-killed", key=key, data=data, at=at))
        elif event.action == "die":
            self.started.pop(name, None)
            self.stop_logs(name)
            stopped = at - self.stopping.pop(name, 0) < 30
            if alerts and not stopped and event.exit_code not in STOPPED:
                text = f"`{name}` exited with code {event.exit_code}"
                out.append(Action("alert", text, key=key, data={**data, "exit_code": event.exit_code}, at=at))
                exits = [when for when in self.exits.get(name, []) if at - when < LOOP_SECONDS] + [at]
                self.exits[name] = exits
                if len(exits) >= LOOP_EXITS:
                    text = (
                        f"`{name}` is restarting in a loop ({len(exits)} exits in {LOOP_SECONDS // 60} min)"
                    )
                    out.append(Action("alert", text, key=f"{key}:loop", data=data, at=at))
        elif event.action == "health" and alerts:
            if event.health == "unhealthy":
                out.append(Action("alert", f"`{name}` is unhealthy", key=f"{key}:health", data=data, at=at))
            elif event.health == "healthy":
                out.append(Action("resolve", key=f"{key}:health", at=at))
        elif event.action == "remove":
            # Its alerts stay: a container recreated under the same name resolves them once it stays up.
            self.stop_logs(name)
        return out

    def log_line(self, name: str, line: str) -> list[Action]:
        line = line.strip()
        if not line:
            return []
        now = time.time()
        minute = int(now // 60)
        with self.lock:
            counts = self.lines.setdefault(name, [minute, 0, 0])
            if counts[0] != minute:
                counts[:] = [minute, 0, counts[2]]
        action = Action("log", f"{name}: {line}"[:300], data={"container": name, "stream": "stderr"}, at=now)
        out = self.folder.add((name, mask(line)), action, now)
        if not out:
            return []
        with self.lock:
            if counts[1] >= self.per_minute:
                counts[2] += 1
                return []
            counts[1] += 1
        return out

    def flush(self, now: float) -> list[Action]:
        """Folded repeats, dropped-line counts, and resolves for containers up for `settle_seconds`."""
        out = self.folder.flush(now)
        with self.lock:
            for name, counts in self.lines.items():
                if counts[2]:
                    text = f"{name}: {counts[2]} more stderr lines dropped (over {self.per_minute}/min)"
                    out.append(Action("log", text, data={"container": name, "dropped": counts[2]}, at=now))
                    counts[2] = 0
        for name, since in list(self.started.items()):
            if now - since >= self.settle_seconds:
                self.started.pop(name, None)
                self.exits.pop(name, None)
                key = f"container:{name}"
                out += [Action("resolve", key=key, at=now), Action("resolve", key=f"{key}:loop", at=now)]
        return out

    def stop(self) -> None:
        for name in list(self.log_streams):
            self.stop_logs(name)
