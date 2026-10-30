"""focus: time on one app and context switches from ambient's screen rows, feeding the mood."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from dusha.hooks import render
from fastapi import APIRouter

KEYS = ("deep", "scattered")


@dataclass
class Options:
    hours: float = 1
    deep_minutes: float = 45
    scattered_switches: int = 12
    deltas: dict[str, dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.deltas) - set(KEYS))
        if unknown:
            raise ValueError(f"focus option 'deltas' takes {list(KEYS)}, not {unknown}")


class Focus:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options

    def rows(self) -> list[dict[str, Any]]:
        found = self.ctx.collect("ambient.screens", self.options.hours, 500)
        return next(iter(found.values()), [])

    def state(self) -> dict[str, Any]:
        """The newest device's streak on one app, and how often the app changed in the window."""
        rows = self.rows()
        if not rows:
            return {"app": "", "streak_minutes": 0, "switches": 0, "state": "unknown"}
        device = rows[-1]["device"]
        apps = [(row["created_at"], row["data"].get("app") or "?") for row in rows if row["device"] == device]
        current = apps[-1][1]
        start = apps[-1][0]
        for moment, app in reversed(apps):
            if app != current:
                break
            start = moment
        switches = sum(1 for one, other in zip(apps, apps[1:], strict=False) if one[1] != other[1])
        streak = (time.time() - start) / 60
        state = "steady"
        if streak >= self.options.deep_minutes:
            state = "deep"
        elif switches >= self.options.scattered_switches:
            state = "scattered"
        return {
            "device": device,
            "app": current,
            "streak_minutes": round(streak),
            "switches": switches,
            "state": state,
        }

    def context(self, request: Any) -> Any:
        state = self.state()
        return render("focus", "<focus>", state, "</focus>") if state["app"] else None

    def decide(self, request: Any) -> dict[str, Any] | None:
        deltas = self.options.deltas.get(self.state()["state"])
        return {"deltas": dict(deltas)} if deltas else None


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown focus option(s): {unknown}")
    focus = Focus(ctx, Options(**raw))
    ctx.on("context", focus.context)
    ctx.on("decide", focus.decide)
    _routes(ctx.api, focus)
    show_help = "See how focused she is right now: the app, the streak, and how often she switched"
    ctx.mcp.tool("focus", focus.state, help=show_help)
    ctx.cli.group("focus", "Focus right now").command("show", focus.state, help=show_help)


def _routes(router: APIRouter, focus: Focus) -> None:
    @router.get("/state/v1/focus", tags=["focus"])
    def show() -> dict[str, Any]:
        return focus.state()
