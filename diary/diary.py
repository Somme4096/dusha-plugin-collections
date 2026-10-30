"""diary: write each day's digest into an Obsidian vault, on the machine where the vault lives."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dusha.client.settings import Connection, connection
from dusha.homes import CONFIG_FILE

logger = logging.getLogger("dusha.diary")


def peer(value: Any, settings: Any, label: str) -> Connection | None:
    """`"proxy"` reuses the server `dusha proxy` saved, a `{url, token}` object names one directly."""
    if value in (None, "", False):
        return None
    if value == "proxy":
        if settings.home is None:
            raise ValueError(f"{label} 'proxy' needs a config home to read the saved server from")
        saved, _ = connection(settings.home / CONFIG_FILE)
        return saved
    if isinstance(value, dict) and isinstance(value.get("url"), str):
        return Connection(value["url"].rstrip("/"), str(value.get("token") or ""))
    raise ValueError(f"{label} must be 'proxy' or an object with 'url' and optional 'token'")


@dataclass
class Options:
    vault: str
    folder: str = "Diary"
    every_seconds: float = 900
    template: str = "# {date}\n\n{text}\n"
    source: Any = None


class Diary:
    def __init__(self, options: Options, source: Connection | None):
        self.options = options
        self.source = source
        self.root = Path(options.vault).expanduser() / options.folder

    def write(self, date: str, text: str) -> None:
        path = self.root / f"{date}.md"
        if path.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        entry = self.options.template.replace("{date}", date).replace("{text}", text)
        path.write_text(entry, encoding="utf-8")

    def fetch(self) -> None:
        headers = {"x-companion-token": self.source.token} if self.source.token else {}
        try:
            response = httpx.get(self.source.url + "/state/v1/digest", headers=headers, timeout=15)
        except httpx.RequestError as error:
            logger.info("diary: the hub isn't answering: %s", error)
            return
        if response.status_code == 200:
            day = response.json()
            self.write(day["date"], day["text"])


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    if not raw.get("vault"):
        raise ValueError("diary needs a 'vault' folder")
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown diary option(s): {unknown}")
    options = Options(**raw)
    diary = Diary(options, peer(options.source, ctx.settings, "diary option 'source'"))
    ctx.on("digest.ready", diary.write)
    if diary.source is not None:
        ctx.interval(options.every_seconds, diary.fetch)
