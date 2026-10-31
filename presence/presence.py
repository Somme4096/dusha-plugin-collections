"""presence: is the user here, is the machine up, and the registry of every device that reports in."""

from __future__ import annotations

import logging
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from dusha.client.settings import Connection, connection
from dusha.commands import option
from dusha.homes import CONFIG_FILE
from dusha.hooks import render
from dusha.timeutil import isoformat, parse_time, utc_now
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.presence")

USER_STATES = ("active", "idle", "locked", "unknown")
DOWN_STATES = ("sleeping", "off", "unreachable")
GOODBYES = {"sleeping": "sleeping", "shutting down": "off"}


class Heartbeat(BaseModel):
    device: str = Field(min_length=1, max_length=64)
    user: str = "unknown"
    idle_seconds: float | None = None
    url: str = ""
    capabilities: list[str] = []
    said: str = ""


@dataclass
class Options:
    device: str
    url: str
    interval_seconds: float = 30
    down_after_seconds: float = 0
    idle_after_seconds: float = 300
    welcome_after_minutes: float = 60
    sensor: str = "auto"
    watch_sleep: bool = True
    report_to: Connection | None = None

    def __post_init__(self) -> None:
        if self.interval_seconds < 1:
            raise ValueError("presence option 'interval_seconds' must be at least 1")
        if not self.down_after_seconds:
            self.down_after_seconds = self.interval_seconds * 3
        if self.sensor not in {"auto", "none"}:
            raise ValueError("presence option 'sensor' must be 'auto' or 'none'")


def peer(value: Any, settings: Any, label: str) -> Connection | None:
    """`"proxy"` reuses the server `dusha proxy` saved, a `{url, token}` object names one directly."""
    if value in (None, "", False):
        return None
    if value == "proxy":
        if settings.home is None:
            raise ValueError(f"{label} 'proxy' needs a config home to read the saved server from")
        saved, _ = connection(settings.home / CONFIG_FILE)
        return saved
    if isinstance(value, dict) and isinstance(value.get("url"), str):
        return Connection(value["url"].rstrip("/"), str(value.get("token") or ""))
    raise ValueError(f"{label} must be 'proxy' or an object with 'url' and optional 'token'")


def parse_options(raw: dict[str, Any], settings: Any) -> Options:
    raw = dict(raw or {})
    device = str(raw.pop("device", "") or socket.gethostname().split(".")[0])
    url = str(raw.pop("url", "") or f"http://{device}:{settings.port}").rstrip("/")
    report_to = peer(raw.pop("report_to", None), settings, "presence option 'report_to'")
    known = set(Options.__dataclass_fields__) - {"device", "url", "report_to"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"unknown presence option(s): {unknown}")
    return Options(device=device, url=url, report_to=report_to, **raw)


# Sensors. Each returns (user, idle_seconds) or None when it can't tell.


def _run(*command: str) -> str:
    if not shutil.which(command[0]):
        return ""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _dbus_idle_ms() -> float | None:
    calls = (
        (
            "org.gnome.Mutter.IdleMonitor",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor.GetIdletime",
        ),
        (
            "org.freedesktop.ScreenSaver",
            "/org/freedesktop/ScreenSaver",
            "org.freedesktop.ScreenSaver.GetSessionIdleTime",
        ),
    )
    for dest, path, method in calls:
        out = _run("gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method)
        match = re.search(r"uint\d+ (\d+)", out)
        if match:
            return float(match.group(1))
    return None


def _linux(idle_after: float) -> tuple[str, float | None] | None:
    user = os.environ.get("USER") or ""
    session = _run("loginctl", "show-user", user, "-p", "Display", "--value").strip() if user else ""
    session = session or os.environ.get("XDG_SESSION_ID", "")
    hints: dict[str, str] = {}
    if session:
        out = _run(
            "loginctl",
            "show-session",
            session,
            "-p",
            "IdleHint",
            "-p",
            "IdleSinceHintMonotonic",
            "-p",
            "LockedHint",
        )
        hints = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    idle_ms = _dbus_idle_ms()
    if idle_ms is not None:
        idle = idle_ms / 1000
    elif hints.get("IdleHint") == "yes" and hints.get("IdleSinceHintMonotonic", "0").isdigit():
        idle = max(0.0, time.monotonic() - int(hints["IdleSinceHintMonotonic"]) / 1e6)
    elif hints:
        idle = 0.0
    else:
        return None
    if hints.get("LockedHint") == "yes":
        return "locked", idle
    return ("idle" if idle >= idle_after or hints.get("IdleHint") == "yes" else "active"), idle


def _macos(idle_after: float) -> tuple[str, float | None] | None:
    out = _run("ioreg", "-c", "IOHIDSystem", "-d", "4")
    match = re.search(r'"HIDIdleTime" = (\d+)', out)
    if not match:
        return None
    idle = int(match.group(1)) / 1e9
    try:
        root = plistlib.loads(_run("ioreg", "-n", "Root", "-d1", "-a").encode())
        users = root.get("IOConsoleUsers", []) if isinstance(root, dict) else []
        locked = any(item.get("CGSSessionScreenIsLocked") for item in users)
    except Exception:
        locked = False
    if locked:
        return "locked", idle
    return ("idle" if idle >= idle_after else "active"), idle


def _windows(idle_after: float) -> tuple[str, float | None] | None:
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint), ("time", ctypes.c_uint)]

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    info = LastInput(ctypes.sizeof(LastInput), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    idle = ((kernel32.GetTickCount() - info.time) & 0xFFFFFFFF) / 1000
    # The lock screen is a secure desktop: this session can't open or switch to it.
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    desktop = user32.OpenInputDesktop(0, False, 0x0100)
    locked = not desktop
    if desktop:
        handle = ctypes.c_void_p(desktop)
        locked = not user32.SwitchDesktop(handle)
        user32.CloseDesktop(handle)
    if locked:
        return "locked", idle
    return ("idle" if idle >= idle_after else "active"), idle


def sense(options: Options) -> tuple[str, float | None]:
    if options.sensor == "none":
        return "unknown", None
    system = platform.system()
    found = _linux(options.idle_after_seconds) if system == "Linux" else None
    if system == "Darwin":
        found = _macos(options.idle_after_seconds)
    if system == "Windows":
        found = _windows(options.idle_after_seconds)
    return found or ("unknown", None)


@dataclass
class Device:
    name: str
    url: str = ""
    capabilities: list[str] = field(default_factory=list)
    user: str = "unknown"
    idle_seconds: float | None = None
    last_seen: datetime | None = None
    last_active_at: datetime | None = None
    said: str = ""
    said_at: datetime | None = None
    status: str = ""
    since: datetime | None = None


def _ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest:02d}m ago" if hours < 24 else f"{hours // 24}d ago"


class Presence:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options
        self.devices: dict[str, Device] = {}
        self.welcomes: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        self.clicked_at: float = 0.0
        self.last_tick: float = 0.0
        self.watcher: subprocess.Popen | None = None

    # The registry.

    def receive(self, beat: Heartbeat, now: datetime | None = None) -> None:
        now = now or utc_now()
        with self.lock:
            device = self.devices.setdefault(beat.device, Device(beat.device))
            was, prior = device.status, device.last_active_at
            device.url = beat.url or device.url
            device.capabilities = sorted(set(beat.capabilities))
            device.user = beat.user if beat.user in USER_STATES else "unknown"
            device.idle_seconds = beat.idle_seconds
            device.last_seen = now
            if device.user == "active" or (device.user == "idle" and beat.idle_seconds is not None):
                device.last_active_at = now - timedelta(seconds=beat.idle_seconds or 0)
            if beat.said in GOODBYES:
                device.said, device.said_at = GOODBYES[beat.said], now
            else:
                device.said, device.said_at = "", None
            if device.user == "active" and was and was != "active" and prior is not None:
                away = (now - prior).total_seconds() / 60
                if away >= self.options.welcome_after_minutes:
                    self.welcomes.append(
                        {"device": device.name, "away_minutes": round(away), "was": was, "at": isoformat(now)}
                    )
        self.sweep(now)

    def _status(self, device: Device, now: datetime) -> tuple[str, datetime | None]:
        if device.said:
            return device.said, device.said_at
        if (
            device.last_seen is None
            or (now - device.last_seen).total_seconds() > self.options.down_after_seconds
        ):
            return "unreachable", device.last_seen
        if device.user == "idle" and device.idle_seconds is not None:
            return "idle", device.last_seen - timedelta(seconds=device.idle_seconds)
        return (device.user if device.user != "unknown" else "awake"), None

    def sweep(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or utc_now()
        changes: list[dict[str, Any]] = []
        with self.lock:
            for device in self.devices.values():
                status, since = self._status(device, now)
                if status == device.status:
                    continue
                old, old_since = device.status, device.since
                device.status, device.since = status, since or now
                if not old:
                    continue
                change = {
                    "device": device.name,
                    "old": old,
                    "new": status,
                    "since": isoformat(old_since or now),
                    "at": isoformat(now),
                }
                changes.append(change)
            self.welcomes = [
                item for item in self.welcomes if now - parse_time(item["at"]) < timedelta(minutes=15)
            ]
        for change in changes:
            self.ctx.emit("presence.changed", change)
        return changes

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utc_now()
        self.sweep(now)
        with self.lock:
            devices = []
            for device in sorted(self.devices.values(), key=lambda item: item.name):
                devices.append(
                    {
                        "device": device.name,
                        "status": device.status,
                        "since": isoformat(device.since) if device.since else None,
                        "user": device.user,
                        "idle_seconds": device.idle_seconds,
                        "last_seen": isoformat(device.last_seen) if device.last_seen else None,
                        "last_active_at": isoformat(device.last_active_at) if device.last_active_at else None,
                        "url": device.url,
                        "capabilities": device.capabilities,
                        "local": device.name == self.options.device,
                    }
                )
        active = [item for item in devices if item["status"] == "active"]
        active.sort(key=lambda item: item["idle_seconds"] or 0)
        awake = [item for item in devices if item["status"] not in DOWN_STATES and item["last_active_at"]]
        awake.sort(key=lambda item: item["last_active_at"], reverse=True)
        return {
            "local": self.options.device,
            "active_device": active[0]["device"] if active else None,
            "last_active_device": awake[0]["device"] if awake else None,
            "devices": devices,
        }

    # This machine.

    def local_beat(self, said: str = "") -> Heartbeat:
        user, idle = sense(self.options)
        if time.time() - self.clicked_at < self.options.interval_seconds:
            user, idle = "active", 0.0
        capabilities = [
            str(item)
            for result in self.ctx.collect("presence.capabilities").values()
            for item in (result if isinstance(result, (list, tuple)) else [result])
        ]
        return Heartbeat(
            device=self.options.device,
            user=user,
            idle_seconds=idle,
            url=self.options.url,
            capabilities=capabilities,
            said=said,
        )

    def tick(self) -> None:
        now_mono = time.monotonic()
        gap = now_mono - self.last_tick if self.last_tick else 0.0
        self.last_tick = now_mono
        if gap > self.options.interval_seconds * 3:
            # This machine slept through the gap: record it so the change reads sleeping -> active.
            with self.lock:
                device = self.devices.get(self.options.device)
                if device is not None:
                    device.status = "sleeping"
                    device.since = utc_now() - timedelta(seconds=gap)
        beat = self.local_beat()
        self.receive(beat)
        self.report(beat)

    def report(self, beat: Heartbeat) -> bool:
        target = self.options.report_to
        if target is None:
            return False
        try:
            response = httpx.post(
                target.url + "/state/v1/presence/heartbeat",
                json=beat.model_dump(),
                headers=target.headers(),
                timeout=min(target.timeout_seconds, 5.0),
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.debug("presence heartbeat to %s failed: %s", target.url, error)
            return False
        return True

    def goodbye(self, said: str) -> None:
        beat = self.local_beat(said)
        self.receive(beat)
        self.report(beat)

    def watch_sleep(self) -> None:
        """Say goodbye before suspend through logind's PrepareForSleep, Linux only."""
        if platform.system() != "Linux" or not shutil.which("gdbus"):
            return
        try:
            self.watcher = subprocess.Popen(
                [
                    "gdbus",
                    "monitor",
                    "--system",
                    "--dest",
                    "org.freedesktop.login1",
                    "--object-path",
                    "/org/freedesktop/login1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            return

        def read() -> None:
            for line in self.watcher.stdout:
                if "PrepareForSleep (true" in line:
                    self.goodbye("sleeping")
                elif "PrepareForSleep (false" in line:
                    self.tick()

        threading.Thread(target=read, name="presence-sleep-watch", daemon=True).start()

    def close(self) -> None:
        if self.watcher is not None:
            self.watcher.terminate()
        self.goodbye("shutting down")

    # Answers to other plugins.

    def candidates(self, now: Any = None) -> list[dict[str, Any]]:
        with self.lock:
            welcomes = list(self.welcomes)
        return [
            {
                "source": "presence",
                "reason": "came_back",
                "topic": f"They just came back to the {item['device']} "
                f"after {_duration(item['away_minutes'])} "
                f"({item['was']}).",
                "score": 0.6,
                "dedup_key": f"presence:back:{item['device']}:{item['at']}",
            }
            for item in welcomes
        ]

    def context(self, request: Any) -> Any:
        snapshot = self.snapshot()
        if not snapshot["devices"]:
            return None
        now = utc_now()
        lines = {}
        for item in snapshot["devices"]:
            since = parse_time(item["since"]) if item["since"] else None
            text = item["status"]
            if item["status"] in DOWN_STATES or item["status"] == "idle":
                text += f" since {_ago(since, now)}"
            lines[item["device"]] = text
        data = {"devices": lines, "active_device": snapshot["active_device"]}
        return render("presence", "<presence>", data, "</presence>")


def _duration(minutes: float) -> str:
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes} minutes"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest:02d}m" if rest else f"{hours}h"


def apply(ctx: Any, config: dict[str, Any]) -> None:
    options = parse_options(config, ctx.settings)
    presence = Presence(ctx, options)
    ctx.on("presence", lambda *_: presence.snapshot())
    ctx.on("proactive.candidates", presence.candidates)
    ctx.on("context", presence.context)
    ctx.on("notification.clicked", lambda device=None, *_: _clicked(presence, device))
    ctx.interval(options.interval_seconds, presence.tick)
    ctx.on_dispose(presence.close)
    if options.report_to is not None and options.watch_sleep:
        presence.watch_sleep()
    _routes(ctx.api, presence)
    ctx.mcp.tool(
        "presence",
        lambda: presence.snapshot(),
        help="Which devices are up, whether the user is active, idle, or away on each, and since when",
    )
    group = ctx.cli.group("presence", "Device and user presence")
    group.command("status", lambda: presence.snapshot(), help="Show every known device")
    group.command(
        "goodbye",
        lambda said: presence.goodbye(said) or {"said": said},
        help="Tell the registry this device is going away",
        args=[option("said", default="sleeping", choices=tuple(GOODBYES))],
    )


def _clicked(presence: Presence, device: Any) -> None:
    if device in (None, presence.options.device):
        presence.clicked_at = time.time()


def _routes(router: APIRouter, presence: Presence) -> None:
    @router.post("/state/v1/presence/heartbeat", tags=["presence"])
    def heartbeat(beat: Heartbeat) -> dict[str, Any]:
        presence.receive(beat)
        return {"ok": True}

    @router.get("/state/v1/presence", tags=["presence"])
    def status() -> dict[str, Any]:
        return presence.snapshot()

    @router.get("/state/v1/presence/local", tags=["presence"])
    def local() -> dict[str, Any]:
        return presence.local_beat().model_dump()
