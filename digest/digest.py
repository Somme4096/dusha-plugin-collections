"""digest: the day's ambient and memos, summarized once a day, kept, noted, and sent."""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dusha.commands import option
from dusha.timeutil import utc_now
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("dusha.digest")

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_TIMELINE_CHARS = 30000
DEFAULT_PROMPT = (
    "You write a short end-of-day digest for one person from their machines' activity log and open "
    "notes. Say what they worked on, what went wrong, and what is still open, in under 150 words of "
    "plain prose. Use only what the log shows."
)


@dataclass
class Options:
    hour: int = 21
    hours: float = 24
    provider: str = ""
    model: str = ""
    prompt: str = DEFAULT_PROMPT
    max_tokens: int = 800
    extra_body: dict[str, Any] = field(default_factory=dict)
    title: str = "Today"
    notify: bool = True
    memo: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError("digest option 'hour' must be from 0 to 23")
        if self.provider and not self.model:
            raise ValueError("digest option 'provider' needs 'model'")


class Digest:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options
        self.zone = ZoneInfo(ctx.settings.timezone)
        self.folder = ctx.data_dir / "days"
        self.folder.mkdir(exist_ok=True)
        self.lock = threading.Lock()

    def today(self) -> datetime:
        return utc_now().astimezone(self.zone)

    def path(self, date: str) -> Any:
        return self.folder / f"{date}.md"

    def timeline(self) -> list[str]:
        found = self.ctx.collect("ambient.timeline", self.options.hours, MAX_TIMELINE_CHARS)
        return next(iter(found.values()), [])

    def _plain(self, lines: list[str], memos: list[str]) -> str:
        sources = Counter(line.split(" ", 3)[2].rstrip(":") for line in lines if line.count(" ") >= 3)
        parts = [f"{count} {name}" for name, count in sources.most_common(8)]
        text = f"{len(lines)} things happened: {', '.join(parts)}." if parts else "A quiet day."
        return text + (f" {len(memos)} open memos." if memos else "")

    def _summarize(self, lines: list[str], memos: list[str]) -> str:
        if not self.options.provider:
            return self._plain(lines, memos)
        profile = self.ctx.providers.get(self.options.provider)
        url = profile.url("chat").rstrip("/")
        url = url if url.endswith("/chat/completions") else url + "/chat/completions"
        body = {
            "model": self.options.model,
            "max_tokens": self.options.max_tokens,
            "messages": [
                {"role": "system", "content": self.options.prompt},
                {"role": "user", "content": "\n".join(["Log:", *lines, "", "Open notes:", *memos])},
            ],
            **self.options.extra_body,
        }
        headers = profile.request_headers("digest")
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=profile.timeout_seconds)
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            logger.warning("digest: the summary failed, falling back to counts: %s", error)
            return self._plain(lines, memos)
        return (text or "").strip() or self._plain(lines, memos)

    def make(self, force: bool = False) -> dict[str, Any]:
        with self.lock:
            date = self.today().date().isoformat()
            path = self.path(date)
            if path.exists() and not force:
                return {"date": date, "text": path.read_text(encoding="utf-8")}
            memos = [item["text"] for item in self.ctx.memo.list()]
            text = self._summarize(self.timeline(), memos)
            path.write_text(text, encoding="utf-8")
        if self.options.memo:
            self.ctx.memo.add(f"Digest {date}: {text}"[:1000])
        if self.options.notify:
            notice = {"title": self.options.title, "body": text, "urgency": "low", "channel": "auto"}
            self.ctx.collect("notification.send", notice)
        self.ctx.emit("digest.ready", date, text)
        return {"date": date, "text": text}

    def tick(self) -> None:
        now = self.today()
        if now.hour >= self.options.hour and not self.path(now.date().isoformat()).exists():
            self.make()

    def show(self, date: str = "") -> dict[str, Any]:
        date = date or self.latest()
        if not DATE.fullmatch(date) or not self.path(date).exists():
            raise ValueError("no digest for that day")
        return {"date": date, "text": self.path(date).read_text(encoding="utf-8")}

    def latest(self) -> str:
        days = sorted(item.stem for item in self.folder.glob("*.md"))
        return days[-1] if days else ""

    def recent(self, days: int = 7) -> list[str]:
        start = (self.today() - timedelta(days=days)).date().isoformat()
        return [item.stem for item in sorted(self.folder.glob("*.md")) if item.stem >= start]


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown digest option(s): {unknown}")
    digest = Digest(ctx, Options(**raw))
    ctx.interval(60, digest.tick)
    _routes(ctx.api, digest)
    date_arg = option("date", default="", help="YYYY-MM-DD, the latest when empty")
    show_help = "Read a day's digest"
    ctx.mcp.tool("digest", digest.show, help=show_help, args=[date_arg])
    group = ctx.cli.group("digest", "Daily digests")
    group.command("show", digest.show, help=show_help, args=[date_arg])
    group.command(
        "make",
        lambda: digest.make(force=True),
        help="Make today's digest now, replacing one already made",
    )
    group.command("days", digest.recent, help="List the last week's digests")


def _routes(router: APIRouter, digest: Digest) -> None:
    @router.get("/state/v1/digest", tags=["digest"])
    def show(date: str = "") -> dict[str, Any]:
        try:
            return digest.show(date)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/state/v1/digest", tags=["digest"])
    def make() -> dict[str, Any]:
        return digest.make(force=True)
