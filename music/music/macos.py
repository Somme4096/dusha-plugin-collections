"""The system's now-playing session through the `media-control` command."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from .base import Player

COMMANDS = {
    "play": "play",
    "pause": "pause",
    "toggle": "toggle-play-pause",
    "next": "next-track",
    "previous": "previous-track",
    "stop": "stop",
}


def _run(*args: str) -> str:
    return subprocess.run(
        ["media-control", *args], capture_output=True, text=True, timeout=5, check=True
    ).stdout


class MediaControl:
    name = "media-control"

    def available(self) -> bool:
        return sys.platform == "darwin" and bool(shutil.which("media-control"))

    def players(self) -> list[Player]:
        data = json.loads(_run("get") or "null")
        if not data:
            return []
        app = str(data.get("bundleIdentifier") or "system")
        return [
            Player(
                id=app,
                name=app.rsplit(".", 1)[-1],
                status="playing" if data.get("playing") else "paused",
                title=str(data.get("title") or ""),
                artist=str(data.get("artist") or ""),
                album=str(data.get("album") or ""),
            )
        ]

    def command(self, player: str, action: str) -> None:
        _run(COMMANDS[action])

    def volume(self, player: str, level: float) -> None:
        script = f"set volume output volume {round(level * 100)}"
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5, check=True)

    def open(self, player: str, uri: str) -> None:
        subprocess.run(["open", uri], capture_output=True, timeout=10, check=True)
