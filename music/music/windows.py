"""Windows media sessions through WinRT's GlobalSystemMediaTransportControls."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from .base import Player

STATUS = {4: "playing", 5: "paused"}  # GlobalSystemMediaTransportControlsSessionPlaybackStatus
METHODS = {
    "play": "try_play_async",
    "pause": "try_pause_async",
    "toggle": "try_toggle_play_pause_async",
    "next": "try_skip_next_async",
    "previous": "try_skip_previous_async",
    "stop": "try_stop_async",
}


async def _sessions() -> list[Any]:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    manager = await Manager.request_async()
    return list(manager.get_sessions())


async def _read() -> list[Player]:
    found = []
    for session in await _sessions():
        media = await session.try_get_media_properties_async()
        status = int(session.get_playback_info().playback_status)
        app = session.source_app_user_model_id
        found.append(
            Player(
                id=app,
                name=app.rsplit("!", 1)[-1].removesuffix(".exe").lower(),
                status=STATUS.get(status, "stopped"),
                title=media.title or "",
                artist=media.artist or "",
                album=media.album_title or "",
            )
        )
    return found


async def _command(player: str, action: str) -> None:
    for session in await _sessions():
        if session.source_app_user_model_id == player:
            await getattr(session, METHODS[action])()
            return
    raise ValueError(f"no media session {player!r}")


class Winrt:
    name = "windows"

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winrt.windows.media.control  # noqa: F401
        except ImportError:
            return False
        return True

    def players(self) -> list[Player]:
        return asyncio.run(_read())

    def command(self, player: str, action: str) -> None:
        asyncio.run(_command(player, action))

    def volume(self, player: str, level: float) -> None:
        raise ValueError("Windows media sessions have no volume control")

    def open(self, player: str, uri: str) -> None:
        os.startfile(uri)
