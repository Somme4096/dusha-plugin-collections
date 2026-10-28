"""notification: reach the user on this machine's desktop, or on the phone through ntfy."""

from __future__ import annotations

import logging
import platform
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

import httpx
from dusha.commands import arg, option
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.notification")

URGENCIES = ("low", "normal", "critical")
CHANNELS = ("auto", "desktop", "phone", "all")
NTFY_PRIORITY = {"low": 2, "normal": 3, "critical": 5}


class Notice(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=4000)
    urgency: str = "normal"
    channel: str = "auto"


@dataclass
class Ntfy:
    topic: str
    server: str = "https://ntfy.sh"
    token: str = ""


@dataclass
class Options:
    device: str
    desktop: bool = True
    desktop_command: list[str] | None = None
    ntfy: Ntfy | None = None
    click_wait_seconds: float = 600


def parse_options(raw: dict[str, Any]) -> Options:
    raw = dict(raw or {})
    device = str(raw.pop("device", "") or socket.gethostname().split(".")[0])
    ntfy = raw.pop("ntfy", None)
    if ntfy is not None:
        if not isinstance(ntfy, dict) or not ntfy.get("topic"):
            raise ValueError("notification option 'ntfy' needs at least a 'topic'")
        ntfy = Ntfy(**ntfy)
    unknown = sorted(set(raw) - {"desktop", "desktop_command", "click_wait_seconds"})
    if unknown:
        raise ValueError(f"unknown notification option(s): {unknown}")
    return Options(device=device, ntfy=ntfy, **raw)


class Notifier:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options

    def channels(self, channel: str) -> list[str]:
        available = (["desktop"] if self.options.desktop else []) + (["phone"] if self.options.ntfy else [])
        if channel == "all":
            return available
        if channel != "auto":
            return [channel] if channel in available else []
        answers = list(self.ctx.collect("presence").values())
        if not answers:
            return available
        here = next((item for item in answers[0].get("devices", []) if item.get("local")), None)
        at_desk = here is not None and here.get("status") == "active"
        if at_desk and "desktop" in available:
            return ["desktop"]
        return ["phone"] if "phone" in available else available

    def send(
        self, title: str, body: str = "", urgency: str = "normal", channel: str = "auto"
    ) -> dict[str, Any]:
        if urgency not in URGENCIES:
            raise ValueError(f"urgency must be one of {list(URGENCIES)}")
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {list(CHANNELS)}")
        body = body or ""
        chosen = self.channels(channel)
        delivered: list[str] = []
        errors: dict[str, str] = {}
        for name in chosen:
            try:
                (self._desktop if name == "desktop" else self._phone)(title, body, urgency)
                delivered.append(name)
            except Exception as error:
                logger.warning("notification over %s failed: %s", name, error)
                errors[name] = str(error)
        if not chosen:
            errors["none"] = f"no channel available for {channel!r}, enable desktop or configure ntfy"
        result = {"device": self.options.device, "delivered": delivered}
        if errors:
            result["errors"] = errors
        self.ctx.emit("notification.sent", {"title": title, "body": body, **result})
        return result

    def _desktop(self, title: str, body: str, urgency: str) -> None:
        if self.options.desktop_command:
            subprocess.run([*self.options.desktop_command, title, body], check=True, timeout=10)
            return
        system = platform.system()
        if system == "Darwin":
            script = [
                "-e",
                "on run argv",
                "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e",
                "end run",
            ]
            subprocess.run(["osascript", *script, title, body], check=True, timeout=10, capture_output=True)
            return
        if not shutil.which("notify-send"):
            raise RuntimeError("notify-send is not installed")
        command = [
            "notify-send",
            "-a",
            "dusha",
            "-u",
            urgency,
            "--action=default=Open",
            "--wait",
            title,
            body,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._await_click, args=(process,), daemon=True).start()

    def _await_click(self, process: subprocess.Popen) -> None:
        try:
            out, err = process.communicate(timeout=self.options.click_wait_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            return
        if process.returncode != 0 and "action" in (err or ""):
            # notify-send older than 0.7.9 has no --action or --wait.
            subprocess.run(process.args[:5] + process.args[-2:], check=False, timeout=10)
        elif out.strip() == "default":
            self.ctx.emit("notification.clicked", self.options.device)

    def _phone(self, title: str, body: str, urgency: str) -> None:
        ntfy = self.options.ntfy
        headers = {"Authorization": f"Bearer {ntfy.token}"} if ntfy.token else {}
        payload = {
            "topic": ntfy.topic,
            "title": title,
            "message": body or title,
            "priority": NTFY_PRIORITY[urgency],
        }
        response = httpx.post(ntfy.server.rstrip("/"), json=payload, headers=headers, timeout=10)
        response.raise_for_status()


def apply(ctx: Any, config: dict[str, Any]) -> None:
    notifier = Notifier(ctx, parse_options(config))
    ctx.on("notification.send", lambda notice: notifier.send(**notice))
    # Only advertise what can actually be delivered here.
    ctx.on("presence.capabilities", lambda: ["notify"] if notifier.channels("all") else [])
    _routes(ctx.api, notifier)
    group = ctx.cli.group("notification", "Notifications on this machine and the phone")
    group.command(
        "send",
        notifier.send,
        help="Send a notification",
        args=[
            arg("title"),
            arg("body", optional=True),
            option("urgency", default="normal", choices=URGENCIES),
            option("channel", default="auto", choices=CHANNELS),
        ],
    )


def _routes(router: APIRouter, notifier: Notifier) -> None:
    @router.post("/state/v1/notification", tags=["notification"])
    def send(notice: Notice) -> dict[str, Any]:
        try:
            return notifier.send(notice.title, notice.body, notice.urgency, notice.channel)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
