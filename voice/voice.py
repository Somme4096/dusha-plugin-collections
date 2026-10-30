"""voice: speak text aloud on this machine, a little faster or slower by mood."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from dusha.commands import arg, option
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.voice")

ESPEAK_WPM = 170
SAY_WPM = 180
SPEAK_TIMEOUT_SECONDS = 120
PLAYERS = (["pw-play", "-"], ["paplay"], ["aplay", "-q"], ["afplay"])
WINDOWS = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.Rate = {rate}; $s.Speak([Console]::In.ReadToEnd())"
)


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    mood: str = ""


@dataclass
class Options:
    engine: str = "auto"
    piper_model: str = ""
    command: list[str] = field(default_factory=list)
    moods: dict[str, float] = field(default_factory=dict)


class Voice:
    def __init__(self, options: Options):
        self.options = options
        self.lock = threading.Lock()  # one sentence at a time

    def engine(self) -> str:
        if self.options.command:
            return "command"
        if self.options.engine != "auto":
            return self.options.engine
        if self.options.piper_model and shutil.which("piper"):
            return "piper"
        if sys.platform == "win32":
            return "windows"
        for name in ("espeak-ng", "say"):
            if shutil.which(name):
                return name
        return ""

    def _say(self, text: str, speed: float, engine: str) -> None:
        if engine == "command":
            argv = [
                part.replace("{text}", text).replace("{speed}", str(speed)) for part in self.options.command
            ]
            subprocess.run(argv, check=True, timeout=SPEAK_TIMEOUT_SECONDS, capture_output=True)
        elif engine == "piper":
            self._piper(text, speed)
        elif engine == "espeak-ng":
            argv = ["espeak-ng", "-s", str(round(ESPEAK_WPM * speed)), "--stdin"]
            subprocess.run(
                argv, input=text.encode(), check=True, timeout=SPEAK_TIMEOUT_SECONDS, capture_output=True
            )
        elif engine == "say":
            argv = ["say", "-r", str(round(SAY_WPM * speed))]
            subprocess.run(
                argv, input=text.encode(), check=True, timeout=SPEAK_TIMEOUT_SECONDS, capture_output=True
            )
        else:
            rate = max(-10, min(10, round((speed - 1) * 10)))
            argv = ["powershell", "-NoProfile", "-Command", WINDOWS.replace("{rate}", str(rate))]
            subprocess.run(
                argv, input=text.encode(), check=True, timeout=SPEAK_TIMEOUT_SECONDS, capture_output=True
            )

    def _piper(self, text: str, speed: float) -> None:
        player = next((item for item in PLAYERS if shutil.which(item[0])), None)
        if player is None:
            raise ValueError("piper needs pw-play, paplay, aplay, or afplay to play its audio")
        scale = str(round(1 / speed, 3))
        argv = ["piper", "-m", self.options.piper_model, "--length-scale", scale, "--output-file", "-"]
        audio = subprocess.run(
            argv, input=text.encode(), check=True, timeout=SPEAK_TIMEOUT_SECONDS, capture_output=True
        )
        subprocess.run(player, input=audio.stdout, check=True, timeout=SPEAK_TIMEOUT_SECONDS)

    def speak(self, text: str, mood: str = "") -> dict[str, Any]:
        engine = self.engine()
        if not engine:
            raise ValueError("no speech engine here, install espeak-ng or set piper_model or command")
        speed = float(self.options.moods.get(mood, 1.0))

        def run() -> None:
            with self.lock:
                try:
                    self._say(text, speed, engine)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    logger.warning("voice: speaking failed: %s", error)

        threading.Thread(target=run, name="voice", daemon=True).start()
        return {"spoken": len(text), "engine": engine, "speed": speed}

    def local(self, body: dict[str, Any]) -> dict[str, Any]:
        """relay in this same process, where a raised error would be logged and lost."""
        try:
            return self.speak(**body)
        except ValueError as error:
            return {"error": str(error)}


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown voice option(s): {unknown}")
    voice = Voice(Options(**raw))
    ctx.on("presence.capabilities", lambda: ["speak"] if voice.engine() else [])
    ctx.on("voice.speak", voice.local)
    _routes(ctx.api, voice)
    group = ctx.cli.group("voice", "Speech on this machine")
    group.command(
        "say", voice.speak, help="Say something out loud", args=[arg("text"), option("mood", default="")]
    )


def _routes(router: APIRouter, voice: Voice) -> None:
    @router.post("/state/v1/voice", tags=["voice"])
    def speak(body: Speech) -> dict[str, Any]:
        try:
            return voice.speak(body.text, body.mood)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
