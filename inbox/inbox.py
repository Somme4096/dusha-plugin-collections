"""inbox: what the companion wanted to say while it couldn't, for later."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dusha.commands import option
from dusha.hooks import render
from fastapi import APIRouter

SCHEMA = """
CREATE TABLE IF NOT EXISTS held (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    because TEXT NOT NULL,
    at REAL NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Options:
    keep_days: float = 14


class Inbox:
    def __init__(self, ctx: Any, options: Options):
        self.options = options
        self.lock = threading.Lock()
        self.db = sqlite3.connect(ctx.data_dir / "inbox.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add(self, key: str, source: str, text: str, because: str) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO held(key, source, text, because, at) VALUES(?,?,?,?,?)",
                (key, source, text, because, time.time()),
            )

    def held(self, item: dict[str, Any]) -> None:
        text = item.get("topic") or item.get("reason") or ""
        self.add(item["dedup_key"], item.get("source") or "", text, item.get("because") or "")

    def undelivered(self, notice: dict[str, Any]) -> None:
        if notice.get("delivered"):
            return
        text = " — ".join(part for part in (notice.get("title"), notice.get("body")) if part)
        self.add(f"notice:{time.time()}", "notification", text, "no channel delivered it")

    def list(self, everything: bool = False) -> dict[str, Any]:
        where = "" if everything else "WHERE read=0"
        with self.lock:
            rows = self.db.execute(f"SELECT * FROM held {where} ORDER BY at DESC LIMIT 100").fetchall()
        items = [
            {
                "at": datetime.fromtimestamp(row["at"]).astimezone().isoformat(timespec="minutes"),
                "source": row["source"],
                "text": row["text"],
                "because": row["because"],
            }
            for row in rows
        ]
        return {"items": items}

    def clear(self) -> dict[str, Any]:
        with self.lock, self.db:
            cleared = self.db.execute("UPDATE held SET read=1 WHERE read=0").rowcount
            self.db.execute("DELETE FROM held WHERE at<?", (time.time() - self.options.keep_days * 86400,))
        return {"cleared": cleared}

    def context(self, request: Any) -> Any:
        with self.lock:
            count = self.db.execute("SELECT COUNT(*) FROM held WHERE read=0").fetchone()[0]
        return render("inbox", "<inbox>", {"unread": count}, "</inbox>") if count else None

    def close(self) -> None:
        with self.lock:
            self.db.close()


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown inbox option(s): {unknown}")
    inbox = Inbox(ctx, Options(**raw))
    ctx.on("proactive.held", inbox.held)
    ctx.on("notification.sent", inbox.undelivered)
    ctx.on("context", inbox.context)
    ctx.on_dispose(inbox.close)
    _routes(ctx.api, inbox)
    list_help = "List what you wanted to tell her while you couldn't reach her"
    every = option("everything", bool, default=False, help="Include what was already read")
    ctx.mcp.tool("inbox", inbox.list, help=list_help, args=[every])
    ctx.mcp.tool("inbox_clear", inbox.clear, help="Mark the inbox as read")
    group = ctx.cli.group("inbox", "Things held back")
    group.command("list", inbox.list, help=list_help, args=[every])
    group.command("clear", inbox.clear, help="Mark everything as read")


def _routes(router: APIRouter, inbox: Inbox) -> None:
    @router.get("/state/v1/inbox", tags=["inbox"])
    def items(everything: bool = False) -> dict[str, Any]:
        return inbox.list(everything)
