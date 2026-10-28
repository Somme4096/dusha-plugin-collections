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
}


@dataclass
class Options:
    token: str = ""
    tokens: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60
    mirror_proactive: bool = True
    mirror_title: str = "dusha"


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

    def call(self, entry: dict[str, Any], capability: str, body: dict[str, Any]) -> Any:
        path, event = CAPABILITIES[capability]
        if entry.get("local"):
            answers = list(self.ctx.collect(event, body).values())
            if not answers:
                raise ValueError(f"nothing on this machine answers {capability}")
            return answers[0]
        if not entry.get("url"):
            raise ValueError(f"{entry['device']} did not report a URL to reach it at")
        token = self.options.tokens.get(entry["device"], self.options.token)
        headers = {"x-companion-token": token} if token else {}
        try:
            response = httpx.post(
                entry["url"] + path, json=body, headers=headers, timeout=self.options.timeout_seconds
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
                raise ValueError("she isn't active on any device right now, so there's no screen to look at")
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
    device_arg = option("device", default="auto", help="Device name from presence, or auto for where she is")
    look_help = "See what's on the screen of one of her devices, auto picks the one she is using"
    notify_help = (
        "Show her a notification on one of her devices, auto picks the one she is using, "
        "or her phone when she is away"
    )
    ctx.mcp.tool("look", relay.look, help=look_help, args=[device_arg])
    notify_args = [
        arg("title"),
        option("body", default=""),
        device_arg,
        option("urgency", default="normal", choices=URGENCIES),
    ]
    ctx.mcp.tool("notify", relay.notify, help=notify_help, args=notify_args)
    group = ctx.cli.group("relay", "Reach her other devices")
    group.command("look", relay.look, help=look_help, args=[device_arg])
    group.command("notify", relay.notify, help=notify_help, args=notify_args)
