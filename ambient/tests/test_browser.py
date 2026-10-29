from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from conftest import client, make_config

CHROMIUM_EPOCH = 11644473600


def _zen(root: Path) -> sqlite3.Connection:
    profile = root / "abcd1234.Personal"
    profile.mkdir(parents=True)
    (root / "profiles.ini").write_text("[Profile0]\nName=Personal\nIsRelative=1\nPath=abcd1234.Personal\n")
    db = sqlite3.connect(profile / "places.sqlite", isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")  # kept open, like Firefox, so new visits sit in the WAL
    db.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    db.execute(
        "CREATE TABLE moz_historyvisits "
        "(id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER, visit_type INTEGER)"
    )
    return db


def _chrome(root: Path) -> sqlite3.Connection:
    (root / "Default").mkdir(parents=True)
    db = sqlite3.connect(root / "Default" / "History", isolation_level=None)
    db.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    db.execute(
        "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, transition INTEGER)"
    )
    return db


def _visit_zen(db: sqlite3.Connection, url: str, title: str, visit_type: int = 1) -> None:
    place = db.execute("INSERT INTO moz_places(url, title) VALUES(?,?)", (url, title)).lastrowid
    db.execute(
        "INSERT INTO moz_historyvisits(place_id, visit_date, visit_type) VALUES(?,?,?)",
        (place, int(time.time() * 1e6), visit_type),
    )


def _visit_chrome(db: sqlite3.Connection, url: str, title: str, transition: int = 0) -> None:
    row = db.execute("INSERT INTO urls(url, title) VALUES(?,?)", (url, title)).lastrowid
    db.execute(
        "INSERT INTO visits(url, visit_time, transition) VALUES(?,?,?)",
        (row, int((time.time() + CHROMIUM_EPOCH) * 1e6), transition),
    )


async def _rows(http) -> list[dict]:
    rows = (await http.get("/state/v1/ambient", params={"source": "browser"})).json()["observations"]
    return list(reversed(rows))


async def _until(http, count: int, timeout: float = 10.0) -> list[dict]:
    for _ in range(int(timeout / 0.2)):
        rows = await _rows(http)
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.2)
    raise AssertionError(f"only {await _rows(http)}")


async def test_browser_visits_are_grouped_per_label_without_queries(tmp_path, plugins_dir):
    zen, chrome = _zen(tmp_path / "zen"), _chrome(tmp_path / "chrome")
    browser = {
        "profiles": {"zen:Personal": "personal", "chrome:Default": "work"},
        "exclude": ["bank.example"],
        "every_seconds": 1,
        "paths": {"zen": [str(tmp_path / "zen")], "chrome": [str(tmp_path / "chrome")]},
    }
    config = make_config(
        tmp_path / "data", plugins_dir, {"ambient": {"device": "desktop", "browser": browser}}
    )
    async with client(config) as http:
        await asyncio.sleep(0.5)
        _visit_zen(zen, "https://www.kagi.com/search?q=sqlite+wal+copy&token=abc", "sqlite wal copy - Kagi")
        _visit_zen(zen, "https://arxiv.org/abs/2601.00001?utm_source=x#section", "Attention Is Still All")
        _visit_zen(zen, "https://arxiv.org/abs/2601.00002", "Another Paper")
        _visit_zen(zen, "https://ads.example/frame", "an embed", visit_type=4)
        _visit_zen(zen, "https://bank.example/account", "Balance")
        _visit_chrome(
            chrome, "https://github.com/acme/api/pull/41?diff=split", "Fix retries by acme · PR #41"
        )
        _visit_chrome(
            chrome, "https://github.com/acme/api/pull/41", "Fix retries by acme · PR #41", transition=8
        )
        first = await _until(http, 2)
        _visit_chrome(chrome, "https://docs.python.org/3/library/sqlite3.html", "sqlite3 — Python docs")
        rows = await _until(http, 3)
    zen.close()
    chrome.close()
    texts = sorted(row["text"] for row in first)
    assert texts == [
        'personal: searched "sqlite wal copy" | arxiv.org (2) Attention Is Still All · Another Paper',
        "work: github.com (1) Fix retries by acme · PR #41",
    ]
    assert rows[2]["text"] == "work: docs.python.org (1) sqlite3 — Python docs"
    assert all("token" not in row["text"] and "utm" not in row["text"] for row in rows)
    assert {row["kind"] for row in rows} == {"browse"}
