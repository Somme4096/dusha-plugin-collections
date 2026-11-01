"""maid: tells terminals when to blink and when to sleep."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

POLL_SECONDS = 0.05
KEEPALIVE_SECONDS = 15


@dataclass
class Options:
    sleep_after_minutes: float = 30


class Maid:
    def __init__(self, options: Options):
        self.options = options
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

    async def stream(self, request: Request) -> AsyncIterator[str]:
        sent = None
        quiet_since = time.monotonic()
        while not self.closed and not await request.is_disconnected():
            state = self.state()
            if state != sent:
                sent = state
                quiet_since = time.monotonic()
                yield f"data: {json.dumps(state)}\n\n"
            elif time.monotonic() - quiet_since >= KEEPALIVE_SECONDS:
                quiet_since = time.monotonic()
                yield ": still here\n\n"
            await asyncio.sleep(POLL_SECONDS)

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

    @router.get("/state/v1/maid/stream", tags=["maid"])
    def stream(request: Request) -> StreamingResponse:
        return StreamingResponse(maid.stream(request), media_type="text/event-stream")
