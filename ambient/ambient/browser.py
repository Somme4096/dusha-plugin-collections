"""Browser history from Firefox-family and Chromium-family profiles, read from copies of their databases."""

from __future__ import annotations

import configparser
import logging
import shutil
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger("dusha.ambient")

KEYS = {"profiles", "exclude", "every_seconds", "paths"}
HOME = Path.home()
MAC = HOME / "Library/Application Support"
ROOTS = {
    "zen": [HOME / ".zen", HOME / ".var/app/app.zen_browser.zen/.zen", MAC / "zen"],
    "firefox": [HOME / ".mozilla/firefox", HOME / ".var/app/org.mozilla.firefox/.mozilla/firefox"],
    "chrome": [HOME / ".config/google-chrome", HOME / ".var/app/com.google.Chrome/config/google-chrome"],
    "chromium": [HOME / ".config/chromium", HOME / ".var/app/org.chromium.Chromium/config/chromium"],
}
ROOTS["firefox"].append(MAC / "Firefox")
ROOTS["chrome"].append(MAC / "Google/Chrome")
ROOTS["chromium"].append(MAC / "Chromium")
FIREFOX = {"zen", "firefox"}
# Firefox visit types kept: link, typed, bookmark, download. The rest are embeds, redirects, frames, reloads.
FIREFOX_VISITS = (1, 2, 3, 7)
# Chromium core transitions dropped: subframes and reloads.
CHROMIUM_SKIPPED = (3, 4, 8)
CHROMIUM_EPOCH = 11644473600  # seconds from 1601 to 1970
# Engines whose query is worth keeping: what was looked for, never session tokens.
SEARCH = {
    "google.": "q",
    "duckduckgo.com": "q",
    "bing.com": "q",
    "kagi.com": "q",
    "search.brave.com": "q",
    "perplexity.ai": "q",
    "youtube.com": "search_query",
}


@dataclass
class Profile:
    browser: str
    name: str
    path: Path

    @property
    def key(self) -> str:
        return f"{self.browser}:{self.name}"


def discover(paths: dict[str, list[str]] | None = None) -> list[Profile]:
    found = []
    roots = {browser: [Path(root).expanduser() for root in items] for browser, items in (paths or {}).items()}
    for browser, candidates in (roots or ROOTS).items():
        for root in candidates:
            if browser in FIREFOX:
                ini = configparser.ConfigParser(interpolation=None)
                ini.read(root / "profiles.ini")
                for section in ini.sections():
                    if section.startswith("Profile") and ini[section].get("Path"):
                        relative = ini[section].get("IsRelative", "1") == "1"
                        path = root / ini[section]["Path"] if relative else Path(ini[section]["Path"])
                        if (path / "places.sqlite").exists():
                            found.append(Profile(browser, ini[section].get("Name", path.name), path))
            elif root.is_dir():
                for path in sorted(root.iterdir()):
                    if (path / "History").exists():
                        found.append(Profile(browser, path.name, path))
    return found


def visits(profile: Profile, since: float) -> list[tuple[float, str, str]]:
    """(unix time, url, title) after `since`, from a copy so the browser's lock never matters."""
    firefox = profile.browser in FIREFOX
    name = "places.sqlite" if firefox else "History"
    with tempfile.TemporaryDirectory(prefix="ambient-browser-") as temp:
        for suffix in ("", "-wal"):
            source = profile.path / f"{name}{suffix}"
            if source.exists():
                shutil.copyfile(source, Path(temp) / f"{name}{suffix}")
        db = sqlite3.connect(Path(temp) / name)
        try:
            if firefox:
                rows = db.execute(
                    "SELECT v.visit_date / 1e6, p.url, COALESCE(p.title, '') FROM moz_historyvisits v "
                    "JOIN moz_places p ON p.id = v.place_id WHERE v.visit_date > ? "
                    f"AND v.visit_type IN {FIREFOX_VISITS} ORDER BY v.visit_date",
                    (since * 1e6,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT v.visit_time / 1e6 - ?, u.url, COALESCE(u.title, '') FROM visits v "
                    "JOIN urls u ON u.id = v.url WHERE v.visit_time > ? "
                    f"AND (v.transition & 255) NOT IN {CHROMIUM_SKIPPED} ORDER BY v.visit_time",
                    (CHROMIUM_EPOCH, (since + CHROMIUM_EPOCH) * 1e6),
                ).fetchall()
        finally:
            db.close()
    return rows


def _search(host: str, query: str) -> str:
    for engine, key in SEARCH.items():
        if f".{engine.rstrip('.')}." in f".{host}.":
            terms = parse_qs(query).get(key)
            return terms[0].strip() if terms else ""
    return ""


class Browser:
    def __init__(self, options: dict[str, Any]):
        self.labels: dict[str, str] = dict(options.get("profiles") or {})
        self.exclude = [domain.lower().removeprefix("www.") for domain in options.get("exclude") or ()]
        self.every = float(options.get("every_seconds", 300))
        self.paths = options.get("paths")

    def excluded(self, host: str) -> bool:
        return any(host == domain or host.endswith("." + domain) for domain in self.exclude)

    def check(self, before: dict[str, float], now: float) -> tuple[list[tuple[str, dict[str, Any]]], dict]:
        """One (text, data) row per label with new visits, and each profile's last visit time."""
        after = dict(before)
        groups: dict[str, dict[str, Any]] = {}
        for profile in discover(self.paths):
            since = before.get(profile.key, now - self.every)
            try:
                found = visits(profile, since)
            except (OSError, sqlite3.Error) as error:
                logger.warning("ambient: reading %s history failed: %s", profile.key, error)
                continue
            if not found:
                continue
            after[profile.key] = max(at for at, _, _ in found)
            label = self.labels.get(profile.key, profile.key)
            group = groups.setdefault(
                label, {"profiles": set(), "domains": Counter(), "titles": {}, "searches": []}
            )
            group["profiles"].add(profile.key)
            for _, url, title in found:
                parts = urlsplit(url)
                host = (parts.hostname or "").lower().removeprefix("www.")
                if parts.scheme not in ("http", "https") or not host or self.excluded(host):
                    continue
                if terms := _search(host, parts.query):
                    if terms not in group["searches"]:
                        group["searches"].append(terms)
                    continue
                group["domains"][host] += 1
                titles = group["titles"].setdefault(host, [])
                if title and title not in titles:
                    titles.append(title)
        rows = []
        for label, group in groups.items():
            if not group["domains"] and not group["searches"]:
                continue
            parts = [f'searched "{terms}"' for terms in group["searches"][:5]]
            for host, count in group["domains"].most_common(6):
                titles = " · ".join(title[:60] for title in group["titles"].get(host, [])[:3])
                parts.append(f"{host} ({count}){f' {titles}' if titles else ''}")
            data = {
                "label": label,
                "profiles": sorted(group["profiles"]),
                "domains": dict(group["domains"]),
                "searches": group["searches"],
            }
            rows.append(((f"{label}: " + " | ".join(parts))[:1500], data))
        return rows, after
