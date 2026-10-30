"""MPRIS players on the session bus, through busctl."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from .base import Player

PREFIX = "org.mpris.MediaPlayer2."
OBJECT = "/org/mpris/MediaPlayer2"
INTERFACE = "org.mpris.MediaPlayer2.Player"
METHODS = {
    "play": "Play",
    "pause": "Pause",
    "toggle": "PlayPause",
    "next": "Next",
    "previous": "Previous",
    "stop": "Stop",
}


def _busctl(*args: str) -> str:
    done = subprocess.run(["busctl", "--user", *args], capture_output=True, text=True, timeout=5, check=False)
    if done.returncode != 0:
        raise ValueError(done.stderr.strip() or "busctl failed")
    return done.stdout


def _value(values: dict, key: str, default: object = "") -> object:
    entry = values.get(key)
    return entry["data"] if isinstance(entry, dict) and "data" in entry else default


class Mpris:
    name = "mpris"

    def available(self) -> bool:
        return sys.platform == "linux" and bool(shutil.which("busctl"))

    def players(self) -> list[Player]:
        listing = _busctl(
            "--json=short",
            "call",
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
        )
        found = []
        for bus_name in json.loads(listing)["data"][0]:
            if not bus_name.startswith(PREFIX):
                continue
            try:
                found.append(self._read(bus_name))
            except (ValueError, KeyError, IndexError, subprocess.SubprocessError):
                continue  # A player can vanish between listing and reading.
        return found

    def _read(self, bus_name: str) -> Player:
        raw = _busctl(
            "--json=short",
            "call",
            bus_name,
            OBJECT,
            "org.freedesktop.DBus.Properties",
            "GetAll",
            "s",
            INTERFACE,
        )
        values = json.loads(raw)["data"][0]
        metadata = _value(values, "Metadata", {})
        artists = _value(metadata, "xesam:artist", [])
        identity = bus_name.removeprefix(PREFIX)
        return Player(
            id=identity,
            name=identity.split(".")[0],
            status=str(_value(values, "PlaybackStatus", "Stopped")).lower(),
            title=str(_value(metadata, "xesam:title")),
            artist=", ".join(artists) if isinstance(artists, list) else str(artists),
            album=str(_value(metadata, "xesam:album")),
            url=str(_value(metadata, "xesam:url")),
            volume=_value(values, "Volume", None),
        )

    def command(self, player: str, action: str) -> None:
        _busctl("call", PREFIX + player, OBJECT, INTERFACE, METHODS[action])

    def volume(self, player: str, level: float) -> None:
        _busctl("set-property", PREFIX + player, OBJECT, INTERFACE, "Volume", "d", str(level))

    def open(self, player: str, uri: str) -> None:
        _busctl("call", PREFIX + player, OBJECT, INTERFACE, "OpenUri", "s", uri)
