"""maid: draws her in `dusha maid show`, breathing, blinking when something happens, and sleeping."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter

SPRITE = Path(__file__).parent / "sprite.json"
TICK_SECONDS = 0.05
BLINK_SECONDS = 0.16
BREATH_SECONDS = 4.0
SLEEP_BREATH_SECONDS = 7.0
BREATH_PIXELS = 2
# Everything above this pixel row sinks on the exhale.
CHEST_ROW = 78
EMPTY = "."
GREYS = {"0": 238, "1": 244, "2": 250, "3": 253, "4": 255}


@dataclass
class Options:
    sleep_after_minutes: float = 30


def breathe(rows: list[str], sunk: int) -> list[str]:
    return [EMPTY * len(rows[0])] * sunk + rows[:CHEST_ROW] + rows[CHEST_ROW + sunk :]


def cell(top: str, bottom: str) -> tuple[str, str]:
    if top == EMPTY and bottom == EMPTY:
        return "\x1b[0m", " "
    if bottom == EMPTY:
        return f"\x1b[0;38;5;{GREYS[top]}m", "▀"
    if top == EMPTY:
        return f"\x1b[0;38;5;{GREYS[bottom]}m", "▄"
    return f"\x1b[0;38;5;{GREYS[top]};48;5;{GREYS[bottom]}m", "▀"


def line(top: str, bottom: str) -> str:
    text, style = "", ""
    for pair in zip(top, bottom, strict=True):
        wanted, glyph = cell(*pair)
        text += glyph if wanted == style else wanted + glyph
        style = wanted
    return text + "\x1b[0m"


def scene(rows: list[str], sunk: int, columns: int, lines: int) -> list[str]:
    drawn = breathe(rows, sunk)
    shown = min(columns, len(drawn[0]))
    skip = (len(drawn[0]) - shown) // 2
    pairs = list(zip(drawn[::2], drawn[1::2], strict=True))[:lines]
    return [line(top[skip : skip + shown], bottom[skip : skip + shown]) for top, bottom in pairs]


class Maid:
    def __init__(self, options: Options):
        self.options = options
        self.sprite = json.loads(SPRITE.read_text())
        self.blinks = 0
        self.active_at = time.monotonic()
        self.closed = False

    def heard(self, *_: Any) -> None:
        self.blinks += 1
        self.active_at = time.monotonic()

    def blink(self) -> dict[str, Any]:
        self.heard()
        return self.state()

    def sleep(self) -> dict[str, Any]:
        self.active_at = float("-inf")
        return self.state()

    def asleep(self) -> bool:
        return time.monotonic() - self.active_at >= self.options.sleep_after_minutes * 60

    def state(self) -> dict[str, Any]:
        return {"blinks": self.blinks, "asleep": self.asleep()}

    async def show(self, columns: int, lines: int) -> AsyncIterator[str]:
        top = max(1, (lines - len(self.sprite["open"]) // 2) // 2 + 1)
        left = max(1, (columns - len(self.sprite["open"][0])) // 2 + 1)
        shown: list[str] = []
        seen = self.blinks
        blink_until = 0.0
        phase = 0.0
        yield "\x1b[2J"
        while not self.closed:
            now = time.monotonic()
            asleep = self.asleep()
            if self.blinks != seen:
                seen = self.blinks
                blink_until = now + BLINK_SECONDS
            phase += TICK_SECONDS / (SLEEP_BREATH_SECONDS if asleep else BREATH_SECONDS)
            sunk = round((1 - math.cos(2 * math.pi * phase)) / 2 * BREATH_PIXELS)
            face = "closed" if asleep or now < blink_until else "open"
            frame = scene(self.sprite[face], sunk, columns, lines)
            changed = [
                f"\x1b[{top + i};{left}H{text}"
                for i, text in enumerate(frame)
                if not shown or shown[i] != text
            ]
            if changed:
                yield "\x1b[?2026h" + "".join(changed) + "\x1b[?2026l"
            shown = frame
            await asyncio.sleep(TICK_SECONDS)

    def close(self) -> None:
        self.closed = True


def apply(ctx: Any, config: dict[str, Any]) -> None:
    raw = dict(config or {})
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown maid option(s): {unknown}")
    maid = Maid(Options(**raw))
    ctx.on("message", maid.heard)
    ctx.on("context", maid.heard)
    ctx.on("proactive.sent", maid.heard)
    ctx.on_dispose(maid.close)
    _routes(ctx.api, maid)
    blink_help = "Blink, in every terminal showing the maid"
    sleep_help = "Fall asleep until something happens"
    ctx.mcp.tool("maid_blink", maid.blink, help=blink_help)
    ctx.mcp.tool("maid_sleep", maid.sleep, help=sleep_help)
    group = ctx.cli.group("maid", "The maid in the terminal")
    group.command("show", maid.show, help="Watch her in this terminal, Ctrl-C leaves", terminal=True)
    group.command("blink", maid.blink, help=blink_help)
    group.command("sleep", maid.sleep, help=sleep_help)


def _routes(router: APIRouter, maid: Maid) -> None:
    @router.get("/state/v1/maid", tags=["maid"])
    def state() -> dict[str, Any]:
        return maid.state()

    @router.post("/state/v1/maid/blink", tags=["maid"])
    def blink() -> dict[str, Any]:
        return maid.blink()

    @router.post("/state/v1/maid/sleep", tags=["maid"])
    def sleep() -> dict[str, Any]:
        return maid.sleep()
