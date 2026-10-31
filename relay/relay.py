"""relay: the hub's tools for other devices. Checks presence first, then calls the device's own plugin."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from dusha.commands import arg, option
from dusha.timeutil import parse_time, utc_now

logger = logging.getLogger("dusha.relay")

DOWN_STATES = ("sleeping", "off", "unreachable")
URGENCIES = ("low", "normal", "critical")
# capability -> (route on the device, local event that does the same in this process)
CAPABILITIES = {
    "look": ("/state/v1/screenshot", "screenshot.look"),
    "notify": ("/state/v1/notification", "notification.send"),
    "spawn": ("/state/v1/spawn", "spawn.run"),
    "music": ("/state/v1/music", "music.control"),
    "shell": ("/state/v1/hands/shell", "hands.shell"),
    "type": ("/state/v1/hands/type", "hands.type"),
    "key": ("/state/v1/hands/key", "hands.key"),
    "windows": ("/state/v1/hands/windows", "hands.windows"),
    "speak": ("/state/v1/voice", "voice.speak"),
}


@dataclass
class Options:
    token: str = ""
    tokens: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60
    mirror_proactive: bool = True
    mirror_title: str = "dusha"
    spawn_timeout_seconds: float = 1800
    mirror_voice: bool = False


def _ago(moment: str | None) -> str:
    if not moment:
        return "a while"
    minutes = int((utc_now() - parse_time(moment)).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _since(moment: str | None) -> str:
    if not moment:
        return ""
    local: datetime = parse_time(moment).astimezone()
    return f" since {local:%H:%M} ({_ago(moment)} ago)"


class Relay:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options

    def presence(self) -> dict[str, Any]:
        answers = list(self.ctx.collect("presence").values())
        if not answers:
            raise ValueError("relay needs the presence plugin to know which devices exist")
        return answers[0]

    def resolve(self, device: str, capability: str) -> dict[str, Any]:
        snapshot = self.presence()
        devices = {item["device"]: item for item in snapshot["devices"]}
        if device not in devices:
            raise ValueError(f"unknown device {device!r}, known devices: {sorted(devices)}")
        entry = devices[device]
        if entry["status"] in DOWN_STATES:
            raise ValueError(f"{device} is {entry['status']}{_since(entry.get('since'))}")
        if capability not in entry.get("capabilities", []):
            offered = ", ".join(entry.get("capabilities", [])) or "nothing"
            raise ValueError(f"{device} can't {capability}, it offers: {offered}")
        return entry

    def call(self, entry: dict[str, Any], capability: str, body: dict[str, Any], timeout: float = 0) -> Any:
        path, event = CAPABILITIES[capability]
        if entry.get("local"):
            answers = list(self.ctx.collect(event, body).values())
            if not answers:
                raise ValueError(f"nothing on this machine answers {capability}")
            if isinstance(answers[0], dict) and "error" in answers[0]:
                raise ValueError(answers[0]["error"])
            return answers[0]
        if not entry.get("url"):
            raise ValueError(f"{entry['device']} did not report a URL to reach it at")
        token = self.options.tokens.get(entry["device"], self.options.token)
        headers = {"x-companion-token": token} if token else {}
        try:
            response = httpx.post(
                entry["url"] + path,
                json=body,
                headers=headers,
                timeout=timeout or self.options.timeout_seconds,
            )
        except httpx.RequestError as error:
            raise ValueError(f"cannot reach {entry['device']} at {entry['url']}: {error}") from error
        if response.status_code >= 400:
            detail = response.text[:300]
            raise ValueError(f"{entry['device']} answered {response.status_code}: {detail}")
        return response.json()

    def look(self, device: str = "auto") -> Any:
        if device == "auto":
            device = self.presence().get("active_device") or ""
            if not device:
                raise ValueError(
                    "they aren't active on any device right now, so there's no screen to look at"
                )
        result = self.call(self.resolve(device, "look"), "look", {})
        return (
            {"device": device, **result} if isinstance(result, dict) else {"device": device, "result": result}
        )

    def notify(self, title: str, body: str = "", device: str = "auto", urgency: str = "normal") -> Any:
        if urgency not in URGENCIES:
            raise ValueError(f"urgency must be one of {list(URGENCIES)}")
        notice = {"title": title, "body": body or "", "urgency": urgency, "channel": "auto"}
        if device == "auto":
            snapshot = self.presence()
            active = snapshot.get("active_device")
            if active:
                device, notice["channel"] = active, "desktop"
            else:
                # Nobody at a desk: the phone, through this machine's notification plugin.
                device, notice["channel"] = snapshot["local"], "phone"
        result = self.call(self.resolve(device, "notify"), "notify", notice)
        if isinstance(result, dict) and not result.get("delivered"):
            raise ValueError(f"{device} couldn't show it: {result.get('errors') or 'no channel delivered'}")
        return result

    def spawn(self, prompt: str, device: str = "auto", agent: str = "", cwd: str = "") -> Any:
        if device == "auto":
            snapshot = self.presence()
            device = snapshot.get("active_device") or snapshot.get("last_active_device") or ""
            if not device:
                raise ValueError("no device has been active yet, so name one")
        seconds = self.options.spawn_timeout_seconds
        body = {"prompt": prompt, "agent": agent, "cwd": cwd, "timeout_seconds": seconds}
        # The device enforces the limit and answers with what it has, so wait a little longer than it does.
        return {"device": device, **self.call(self.resolve(device, "spawn"), "spawn", body, seconds + 30)}

    def _auto(self, device: str) -> str:
        if device != "auto":
            return device
        active = self.presence().get("active_device") or ""
        if not active:
            raise ValueError("they aren't active on any device right now, so name one")
        return active

    def _act(self, device: str, capability: str, body: dict[str, Any], timeout: float = 0) -> Any:
        device = self._auto(device)
        return {"device": device, **self.call(self.resolve(device, capability), capability, body, timeout)}

    def shell(self, command: str, device: str = "auto", cwd: str = "", timeout_seconds: float = 60) -> Any:
        body = {"command": command, "cwd": cwd, "timeout_seconds": timeout_seconds}
        return self._act(device, "shell", body, timeout_seconds + 15)

    def type_text(self, text: str, device: str = "auto") -> Any:
        return self._act(device, "type", {"text": text})

    def press_key(self, key: str, device: str = "auto") -> Any:
        return self._act(device, "key", {"text": key})

    def list_windows(self, device: str = "auto") -> Any:
        return self._act(device, "windows", {})

    def speak(self, text: str, device: str = "auto", mood: str = "") -> Any:
        return self._act(device, "speak", {"text": text, "mood": mood})

    def music(self, action: str = "status", query: str = "", device: str = "auto", player: str = "") -> Any:
        body = {"action": action, "query": query, "player": player}
        return self._act(device, "music", body)

    def mirror(self, text: str, event: Any = None) -> None:
        if not text or not self.options.mirror_proactive:
            return
        try:
            active = self.presence().get("active_device")
        except ValueError:
            return
        if not active:
            return

        def send() -> None:
            try:
                self.notify(self.options.mirror_title, text, device=active)
                if self.options.mirror_voice:
                    feeling = (event or {}).get("ruling_feeling") or {}
                    self.speak(text, active, str(feeling.get("dimension") or ""))
            except ValueError as error:
                logger.info("relay could not mirror a proactive message to %s: %s", active, error)

        threading.Thread(target=send, name="relay-mirror", daemon=True).start()


def parse_options(raw: dict[str, Any]) -> Options:
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown relay option(s): {unknown}")
    return Options(**raw)


def apply(ctx: Any, config: dict[str, Any]) -> None:
    relay = Relay(ctx, parse_options(config))
    ctx.on("proactive.sent", relay.mirror)
    device_arg = option(
        "device", default="auto", help="Device name from presence, or auto for where they are"
    )
    look_help = "See what's on the screen of one of their devices, auto picks the one they are using"
    notify_help = (
        "Show them a notification on one of their devices, auto picks the one they are using, "
        "or their phone when they are away"
    )
    ctx.mcp.tool("look", relay.look, help=look_help, args=[device_arg])
    notify_args = [
        arg("title"),
        option("body", default=""),
        device_arg,
        option("urgency", default="normal", choices=URGENCIES),
    ]
    ctx.mcp.tool("notify", relay.notify, help=notify_help, args=notify_args)
    spawn_help = (
        "Run a coding agent on one of their devices and wait for its answer, it has full run of that machine"
    )
    spawn_args = [arg("prompt"), device_arg, option("agent", default=""), option("cwd", default="")]
    ctx.mcp.tool("spawn", relay.spawn, help=spawn_help, args=spawn_args)
    music_help = (
        "See or control their music: status, play, pause, toggle, next, previous, stop, "
        "volume (query is 0 to 1), find. play with a query starts that song"
    )
    music_args = [
        option("action", default="status"),
        option("query", default=""),
        device_arg,
        option("player", default=""),
    ]
    ctx.mcp.tool("music", relay.music, help=music_help, args=music_args)
    hand_tools = [
        (
            "shell",
            relay.shell,
            "Run a shell command on one of their devices",
            [
                arg("command"),
                device_arg,
                option("cwd", default=""),
                option("timeout_seconds", float, default=60),
            ],
        ),
        ("type_text", relay.type_text, "Type text on one of their devices", [arg("text"), device_arg]),
        ("press_key", relay.press_key, "Press a key on one of their devices", [arg("key"), device_arg]),
        ("list_windows", relay.list_windows, "List open windows on one of their devices", [device_arg]),
        (
            "speak",
            relay.speak,
            "Say something out loud on one of their devices",
            [arg("text"), device_arg, option("mood", default="")],
        ),
    ]
    for name, handler, text, tool_args in hand_tools:
        ctx.mcp.tool(name, handler, help=text, args=tool_args)
    group = ctx.cli.group("relay", "Reach their other devices")
    group.command("look", relay.look, help=look_help, args=[device_arg])
    group.command("notify", relay.notify, help=notify_help, args=notify_args)
    group.command("spawn", relay.spawn, help=spawn_help, args=spawn_args)
    group.command("music", relay.music, help=music_help, args=music_args)
    for name, handler, text, tool_args in hand_tools:
        group.command(name.replace("_", "-"), handler, help=text, args=tool_args)
