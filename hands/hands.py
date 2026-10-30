"""hands: run commands, type, press keys, and list windows on this machine. The harness approves."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dusha.commands import arg, option
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.hands")

MAX_OUTPUT = 20000
POWERSHELL = ["powershell", "-NoProfile", "-Command"]
MAC_TYPE = 'tell application "System Events" to keystroke "{text}"'
WIN_TYPE = "Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.SendKeys]::SendWait('{text}')"
WIN_WINDOWS = (
    "Get-Process | Where-Object MainWindowTitle | "
    "ForEach-Object { $_.ProcessName + ': ' + $_.MainWindowTitle }"
)
MAC_WINDOWS = 'tell application "System Events" to get name of every process whose background only is false'
TYPERS = (
    ("wtype", ["wtype", "{text}"]),
    ("ydotool", ["ydotool", "type", "{text}"]),
    ("xdotool", ["xdotool", "type", "--", "{text}"]),
)
KEYERS = (
    ("wtype", ["wtype", "-k", "{text}"]),
    ("ydotool", ["ydotool", "key", "{text}"]),
    ("xdotool", ["xdotool", "key", "--", "{text}"]),
)
LISTERS = (("wmctrl", ["wmctrl", "-l"]),)


class Shell(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = ""
    timeout_seconds: float = 60


class Text(BaseModel):
    text: str = Field(min_length=1)


@dataclass
class Options:
    shell_timeout_seconds: float = 300
    type_command: list[str] = field(default_factory=list)
    key_command: list[str] = field(default_factory=list)
    windows_command: list[str] = field(default_factory=list)


def _pick(own: list[str], linux: tuple, mac: list[str], windows: list[str]) -> list[str]:
    if own:
        return own
    if sys.platform == "darwin":
        return mac
    if sys.platform == "win32":
        return windows
    return next((command for name, command in linux if shutil.which(name)), [])


def _tail(text: str) -> str:
    return text[-MAX_OUTPUT:]


class Hands:
    def __init__(self, options: Options):
        self.options = options
        self.typer = _pick(
            options.type_command, TYPERS, ["osascript", "-e", MAC_TYPE], [*POWERSHELL, WIN_TYPE]
        )
        self.keyer = _pick(
            options.key_command, KEYERS, ["osascript", "-e", MAC_TYPE], [*POWERSHELL, WIN_TYPE]
        )
        self.lister = _pick(
            options.windows_command, LISTERS, ["osascript", "-e", MAC_WINDOWS], [*POWERSHELL, WIN_WINDOWS]
        )

    def capabilities(self) -> list[str]:
        offered = {"type": self.typer, "key": self.keyer, "windows": self.lister}
        return ["shell", *(name for name, command in offered.items() if command)]

    def shell(self, command: str, cwd: str = "", timeout_seconds: float = 60) -> dict[str, Any]:
        folder = str(Path(cwd or Path.home()).expanduser())
        if not Path(folder).is_dir():
            raise ValueError(f"no such folder: {folder}")
        limit = min(timeout_seconds or 60, self.options.shell_timeout_seconds)
        try:
            done = subprocess.run(
                command,
                shell=True,
                cwd=folder,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=limit,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"still running after {limit:.0f}s: {command}") from error
        return {"exit_code": done.returncode, "stdout": _tail(done.stdout), "stderr": _tail(done.stderr)}

    def _run(self, command: list[str], text: str = "") -> str:
        if not command:
            raise ValueError("this machine has no tool for that")
        argv = [part.replace("{text}", text) for part in command]
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=15)
        if done.returncode != 0:
            raise ValueError(done.stderr.strip()[:300] or f"{argv[0]} failed")
        return done.stdout

    def type_text(self, text: str) -> dict[str, Any]:
        self._run(self.typer, text)
        return {"typed": len(text)}

    def press_key(self, text: str) -> dict[str, Any]:
        self._run(self.keyer, text)
        return {"pressed": text}

    def list_windows(self) -> dict[str, Any]:
        return {"windows": [line for line in self._run(self.lister).splitlines() if line.strip()]}


def _local(handler: Any) -> Any:
    """relay in this same process, where a raised error would be logged and lost."""

    def call(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return handler(**body)
        except (ValueError, OSError) as error:
            return {"error": str(error)}

    return call


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown hands option(s): {unknown}")
    hands = Hands(Options(**raw))
    ctx.on("presence.capabilities", hands.capabilities)
    ctx.on("hands.shell", _local(hands.shell))
    ctx.on("hands.type", _local(hands.type_text))
    ctx.on("hands.key", _local(hands.press_key))
    ctx.on("hands.windows", _local(hands.list_windows))
    _routes(ctx.api, hands)
    group = ctx.cli.group("hands", "Control this machine")
    group.command(
        "shell", hands.shell, help="Run a shell command", args=[arg("command"), option("cwd", default="")]
    )
    group.command("windows", hands.list_windows, help="List open windows")


def _routes(router: APIRouter, hands: Hands) -> None:
    def guarded(handler: Any, *args: Any) -> dict[str, Any]:
        try:
            return handler(*args)
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/state/v1/hands/shell", tags=["hands"])
    def shell(body: Shell) -> dict[str, Any]:
        return guarded(hands.shell, body.command, body.cwd, body.timeout_seconds)

    @router.post("/state/v1/hands/type", tags=["hands"])
    def type_text(body: Text) -> dict[str, Any]:
        return guarded(hands.type_text, body.text)

    @router.post("/state/v1/hands/key", tags=["hands"])
    def press_key(body: Text) -> dict[str, Any]:
        return guarded(hands.press_key, body.text)

    @router.post("/state/v1/hands/windows", tags=["hands"])
    def list_windows() -> dict[str, Any]:
        return guarded(hands.list_windows)
