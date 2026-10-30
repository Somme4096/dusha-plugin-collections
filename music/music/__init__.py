"""music: what's playing on this machine as an ambient source, and optional control of its players."""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from dusha.commands import arg, option
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import library, macos, mpris, windows, youtube
from .base import ACTIONS, Backend, Player

logger = logging.getLogger("dusha.music")

BACKENDS: list[Backend] = [mpris.Mpris(), macos.MediaControl(), windows.Winrt()]
VERBS = (*ACTIONS, "status", "find", "volume")


class Control(BaseModel):
    action: str = "status"
    query: str = ""
    player: str = ""


@dataclass
class Options:
    device: str = ""
    every_seconds: float = 10
    min_listen_seconds: float = 30
    control: bool = False
    players: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    library: list[str] = field(default_factory=lambda: ["~/Music"])
    local_player: str = "audacious"
    local_command: list[str] = field(default_factory=list)
    youtube_music: str = ""


def parse_options(raw: dict[str, Any]) -> Options:
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown music option(s): {unknown}")
    options = Options(**raw)
    options.device = options.device or socket.gethostname().split(".")[0]
    return options


class Music:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options
        self.backend = next((item for item in BACKENDS if item.available()), None)
        self.heard: tuple[tuple[str, str], float] | None = None  # the track on now, since when
        self.logged: tuple[str, str] | None = None
        token = ctx.data_dir / "youtube-music-token"
        address = options.youtube_music
        self.youtube = youtube.YouTubeMusic(address, token) if address else None

    def players(self) -> list[Player]:
        if self.backend is None:
            return []
        wanted, ignored = self.options.players, self.options.ignore
        return [
            player
            for player in self.backend.players()
            if (not wanted or player.name in wanted) and player.name not in ignored
        ]

    def tick(self) -> None:
        now = time.monotonic()
        try:
            player = next((item for item in self.players() if item.status == "playing"), None)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            logger.info("music: reading players failed: %s", error)
            return
        if player is None or not player.title:
            self.heard = None
            return
        track = (player.title, player.artist)
        if self.heard is None or self.heard[0] != track:
            self.heard = (track, now)
        if now - self.heard[1] < self.options.min_listen_seconds or track == self.logged:
            return
        self.logged = track
        text = f"{player.title} — {player.artist}" if player.artist else player.title
        data = {"player": player.name, "album": player.album, "url": player.url}
        self.ctx.emit("ambient.observe", {"source": "music", "kind": "music", "text": text, "data": data})
        self.ctx.emit("music.changed", {"device": self.options.device, "text": text, **data})

    def local(self, body: dict[str, Any]) -> dict[str, Any]:
        """relay in this same process, where a raised error would be logged and lost."""
        try:
            return self.control(**body)
        except ValueError as error:
            return {"error": str(error)}

    def target(self, name: str = "") -> Player:
        players = self.players()
        if name:
            match = [item for item in players if name.casefold() in item.id.casefold()]
            if not match:
                raise ValueError(f"no player {name!r}, running: {[item.name for item in players]}")
            return match[0]
        if not players:
            raise ValueError("no music player is running here")
        return next((item for item in players if item.status == "playing"), players[0])

    def control(self, action: str = "status", query: str = "", player: str = "") -> dict[str, Any]:
        if action not in VERBS:
            raise ValueError(f"action must be one of {list(VERBS)}")
        if self.backend is None:
            raise ValueError("this machine has no way to reach a music player")
        if action == "status":
            return {"players": [item.public() for item in self.players()]}
        if action == "find":
            return {"local": [str(path) for path in library.search(self.options.library, query)]}
        if action == "play" and query:
            return self._song(query, player)
        chosen = self.target(player)
        if action == "volume":
            try:
                level = float(query)
            except ValueError:
                raise ValueError("volume needs a level from 0 to 1 in query") from None
            self.backend.volume(chosen.id, min(max(level, 0.0), 1.0))
        else:
            self.backend.command(chosen.id, action)
        return {"player": chosen.name, "done": action}

    def _song(self, query: str, player: str) -> dict[str, Any]:
        found = library.search(self.options.library, query, 1)
        if found:
            self._local(found[0].as_uri(), str(found[0]), player)
            return {"source": "local", "song": found[0].name}
        if self.youtube is None:
            raise ValueError(f"nothing local matches {query!r}, and youtube_music isn't configured")
        return {"source": "youtube_music", "song": self.youtube.play(query)["title"]}

    def _local(self, uri: str, path: str, player: str) -> None:
        name = player or self.options.local_player
        running = [item for item in self.players() if name.casefold() in item.id.casefold()]
        if running:
            self.backend.open(running[0].id, uri)
        elif self.options.local_command:
            subprocess.Popen(
                [*self.options.local_command, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            raise ValueError(f"{name} isn't running, and local_command doesn't say how to start it")


def apply(ctx: Any, config: dict[str, Any]) -> None:
    music = Music(ctx, parse_options(config))
    ctx.interval(music.options.every_seconds, music.tick)
    group = ctx.cli.group("music", "Music on this machine")
    group.command("status", lambda: music.control("status"), help="Show the players and what they play")
    if not music.options.control:
        return
    ctx.on("presence.capabilities", lambda: ["music"] if music.backend else [])
    ctx.on("music.control", music.local)
    _routes(ctx.api, music)
    group.command(
        "control",
        music.control,
        help="Control a player, or play a song by name",
        args=[
            arg("action", choices=VERBS),
            arg("query", optional=True),
            option("player", default=""),
        ],
    )


def _routes(router: APIRouter, music: Music) -> None:
    @router.post("/state/v1/music", tags=["music"])
    def control(body: Control) -> dict[str, Any]:
        try:
            return music.control(body.action, body.query, body.player)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
