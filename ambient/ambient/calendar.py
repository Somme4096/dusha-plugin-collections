"""Events from ICS files or URLs: what's on now, and what's about to start."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("dusha.ambient")

KEYS = {"sources", "every_seconds", "notice_minutes"}
HORIZON_DAYS = 2


@dataclass
class Event:
    key: str
    title: str
    start: datetime
    end: datetime
    location: str = ""

    def text(self) -> str:
        minutes = round((self.end - self.start).total_seconds() / 60)
        place = f" ({self.location})" if self.location else ""
        return f"{self.title} at {self.start:%H:%M} for {minutes}m{place}"


def _moment(value: date | datetime, zone: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=zone)
    return datetime(value.year, value.month, value.day, tzinfo=zone)


def _read(source: str) -> str:
    if source.startswith(("http://", "https://")):
        response = httpx.get(source, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.text
    return Path(source).expanduser().read_text(encoding="utf-8")


class Calendar:
    def __init__(self, options: dict[str, Any]):
        self.sources = list(options.get("sources") or ())
        self.every = float(options.get("every_seconds", 900))
        self.notice = timedelta(minutes=float(options.get("notice_minutes", 10)))
        self.events: list[Event] = []
        self.fetched = 0.0

    def fetch(self, now: datetime) -> list[Event]:
        import icalendar
        import recurring_ical_events

        found: list[Event] = []
        for source in self.sources:
            try:
                parsed = icalendar.Calendar.from_ical(_read(source))
            except (OSError, ValueError, httpx.HTTPError) as error:
                logger.warning("ambient: reading calendar %s failed: %s", source, error)
                continue
            until = now + timedelta(days=HORIZON_DAYS)
            for item in recurring_ical_events.of(parsed).between(now - timedelta(days=1), until):
                start = _moment(item["DTSTART"].dt, now.tzinfo)
                ending = item.get("DTEND")
                end = _moment(ending.dt, now.tzinfo) if ending else start + timedelta(hours=1)
                key = f"{item.get('UID', '')}:{start.isoformat()}"
                found.append(
                    Event(key, str(item.get("SUMMARY", "Busy")), start, end, str(item.get("LOCATION", "")))
                )
        return sorted(found, key=lambda event: event.start)

    def check(self, now: datetime, stamp: float) -> list[Event]:
        """Events that just came within notice."""
        if stamp - self.fetched >= self.every:
            self.events = self.fetch(now)
            self.fetched = stamp
        return [event for event in self.events if now <= event.end and event.start - self.notice <= now]

    def on_now(self, now: datetime) -> list[str]:
        return [event.text() for event in self.events if event.start <= now < event.end]
