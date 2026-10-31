"""voice: speech from an OpenAI-compatible TTS server, played on this machine or held for a chat worker."""

from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dusha.commands import arg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("dusha.voice")

PLAYERS = (
    ["pw-play", "{file}"],
    ["paplay", "{file}"],
    ["aplay", "-q", "{file}"],
    ["afplay", "{file}"],
)
WINDOWS_PLAYER = [
    "powershell",
    "-NoProfile",
    "-Command",
    "(New-Object Media.SoundPlayer '{file}').PlaySync()",
]


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@dataclass
class Options:
    base_url: str = ""
    api_key: str = ""
    model: str = "tts-1"
    voice: str = "alloy"
    response_format: str = "wav"
    speed: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120
    player: list[str] = field(default_factory=list)
    clip_ttl_seconds: float = 3600


class Voice:
    def __init__(self, options: Options, outbox: Path):
        self.options = options
        self.outbox = outbox
        self.lock = threading.Lock()  # one sentence at a time

    def player(self) -> list[str]:
        if self.options.player:
            return self.options.player
        if sys.platform == "win32":
            return WINDOWS_PLAYER
        return next((item for item in PLAYERS if shutil.which(item[0])), [])

    def synthesize(self, text: str) -> bytes:
        options = self.options
        if not options.base_url:
            raise ValueError("voice needs base_url, an OpenAI-compatible TTS server")
        body = {
            "model": options.model,
            "voice": options.voice,
            "input": text,
            "response_format": options.response_format,
            **({} if options.speed == 1 else {"speed": options.speed}),
            **options.extra,
        }
        headers = {"Authorization": f"Bearer {options.api_key}"} if options.api_key else {}
        url = options.base_url.rstrip("/") + "/audio/speech"
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=options.timeout_seconds)
        except httpx.RequestError as error:
            raise ValueError(f"cannot reach the TTS server: {error}") from error
        if response.status_code >= 400:
            raise ValueError(f"the TTS server answered {response.status_code}: {response.text[:300]}")
        return response.content

    def _play(self, audio: bytes, player: list[str]) -> None:
        suffix = "." + self.options.response_format
        with self.lock, tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"speech{suffix}"
            path.write_bytes(audio)
            argv = [part.replace("{file}", str(path)) for part in player]
            try:
                subprocess.run(argv, check=True, timeout=self.options.timeout_seconds, capture_output=True)
            except (OSError, subprocess.SubprocessError) as error:
                logger.warning("voice: playing failed: %s", error)

    def speak(self, text: str) -> dict[str, Any]:
        player = self.player()
        if not player:
            raise ValueError("no audio player here, install pw-play, paplay, or aplay, or set player")
        audio = self.synthesize(text)
        threading.Thread(target=self._play, args=(audio, player), name="voice", daemon=True).start()
        return {"spoken": len(text), "bytes": len(audio)}

    def queue(self, text: str, route: str) -> dict[str, Any]:
        audio = self.synthesize(text)
        clip = uuid.uuid4().hex
        self.outbox.mkdir(parents=True, exist_ok=True)
        row = {
            "id": clip,
            "route": route,
            "format": self.options.response_format,
            "text": text,
            "data": base64.b64encode(audio).decode(),
        }
        (self.outbox / f"{clip}.json").write_text(json.dumps(row), encoding="utf-8")
        return {"queued": clip, "route": route, "bytes": len(audio)}

    def clips(self, prefix: str = "") -> list[dict[str, Any]]:
        rows = []
        cutoff = time.time() - self.options.clip_ttl_seconds
        files = self.outbox.glob("*.json") if self.outbox.exists() else []
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if path.stat().st_mtime < cutoff:
                logger.warning("voice: dropped clip %s, nothing collected it in time", path.stem)
                path.unlink(missing_ok=True)
                continue
            row = json.loads(path.read_text(encoding="utf-8"))
            if row["route"].startswith(prefix):
                rows.append(row)
        return rows

    def done(self, clip: str) -> None:
        (self.outbox / f"{clip}.json").unlink(missing_ok=True)

    def guarded(self, action: Any, body: dict[str, Any]) -> dict[str, Any]:
        """relay in this same process, where a raised error would be logged and lost."""
        try:
            return action(**body)
        except ValueError as error:
            return {"error": str(error)}


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown voice option(s): {unknown}")
    options = Options(**raw)
    voice = Voice(options, Path(ctx.data_dir) / "outbox")
    ctx.on("presence.capabilities", lambda: ["speak"] if options.base_url and voice.player() else [])
    ctx.on("voice.speak", lambda body: voice.guarded(voice.speak, body))
    ctx.on("voice.queue", lambda body: voice.guarded(voice.queue, body))
    _routes(ctx.api, voice)
    group = ctx.cli.group("voice", "Speech on this machine")
    group.command("say", voice.speak, help="Say something out loud", args=[arg("text")])


def _routes(router: APIRouter, voice: Voice) -> None:
    @router.post("/state/v1/voice", tags=["voice"])
    def speak(body: Speech) -> dict[str, Any]:
        try:
            return voice.speak(body.text)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/state/v1/voice/outbox", tags=["voice"])
    def outbox(prefix: str = "") -> dict[str, Any]:
        return {"clips": voice.clips(prefix)}

    @router.delete("/state/v1/voice/outbox/{clip}", tags=["voice"])
    def delivered(clip: str) -> dict[str, Any]:
        voice.done(clip)
        return {"id": clip}
