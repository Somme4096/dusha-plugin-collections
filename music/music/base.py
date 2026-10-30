"""What every player backend offers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

ACTIONS = ("play", "pause", "toggle", "next", "previous", "stop")


@dataclass
class Player:
    id: str
    name: str
    status: str = "stopped"  # playing, paused, stopped
    title: str = ""
    artist: str = ""
    album: str = ""
    url: str = ""
    volume: float | None = None

    def public(self) -> dict:
        return asdict(self)


class Backend(Protocol):
    name: str

    def available(self) -> bool: ...

    def players(self) -> list[Player]: ...

    def command(self, player: str, action: str) -> None: ...

    def volume(self, player: str, level: float) -> None: ...

    def open(self, player: str, uri: str) -> None: ...
