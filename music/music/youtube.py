"""The YouTube Music desktop app (th-ch), through its api-server plugin."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("dusha.music")

CLIENT = "dusha"


def first_song(node: Any) -> dict[str, str] | None:
    """The first playable item in a search answer, which is deeply nested renderer JSON."""
    if isinstance(node, list):
        return next((song for item in node if (song := first_song(item))), None)
    if not isinstance(node, dict):
        return None
    renderer = node.get("musicResponsiveListItemRenderer")
    if isinstance(renderer, dict):
        video = _find(renderer, "videoId")
        if video:
            return {"video_id": video, "title": _find(renderer, "text") or video}
    return next((song for item in node.values() if (song := first_song(item))), None)


def _find(node: Any, key: str) -> str:
    if isinstance(node, list):
        return next((found for item in node if (found := _find(item, key))), "")
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get(key), str):
        return node[key]
    return next((found for item in node.values() if (found := _find(item, key))), "")


class YouTubeMusic:
    def __init__(self, url: str, token_file: Path):
        self.url = url.rstrip("/")
        self.token_file = token_file

    def _authorize(self) -> str:
        # The first call pops a dialog in the app, so it waits for a click.
        response = httpx.post(f"{self.url}/auth/{CLIENT}", timeout=60)
        response.raise_for_status()
        token = response.json()["accessToken"]
        self.token_file.write_text(token)
        return token

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        token = self.token_file.read_text() if self.token_file.exists() else self._authorize()
        for attempt in (1, 2):
            try:
                response = httpx.request(
                    method,
                    f"{self.url}/api/v1{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
            except httpx.RequestError as error:
                raise ValueError(f"YouTube Music isn't answering at {self.url}: {error}") from error
            if response.status_code in (401, 403) and attempt == 1:
                token = self._authorize()
                continue
            if response.status_code >= 400:
                raise ValueError(f"YouTube Music answered {response.status_code}: {response.text[:200]}")
            return response.json() if response.content else None
        return None

    def play(self, query: str) -> dict[str, str]:
        song = first_song(self._call("POST", "/search", {"query": query}))
        if song is None:
            raise ValueError(f"YouTube Music found nothing for {query!r}")
        self._call("POST", "/queue", {"videoId": song["video_id"]})
        queue = self._call("GET", "/queue") or {}
        items = queue.get("items") or []
        if items:
            self._call("PATCH", "/queue", {"index": len(items) - 1})
        else:
            self._call("POST", "/next")
        return song
