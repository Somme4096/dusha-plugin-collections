"""The maid in a terminal, needs nothing but Python: python tui.py --url http://hub:port --token ..."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import threading
import time
import urllib.request
from pathlib import Path

SPRITE = Path(__file__).parent / "sprite.json"
TICK_SECONDS = 0.05
BLINK_SECONDS = 0.16
BREATH_SECONDS = 4.0
SLEEP_BREATH_SECONDS = 7.0
RETRY_SECONDS = 3.0
# Everything above this pixel row sinks by one on the exhale.
CHEST_ROW = 38
EMPTY = "."


def load(path: Path = SPRITE) -> dict:
    sprite = json.loads(path.read_text())
    palette = {key: tuple(value) for key, value in sprite["palette"].items()}
    return {"palette": palette, "open": sprite["open"], "closed": sprite["closed"]}


def breathe(rows: list[str], exhaled: bool) -> list[str]:
    if not exhaled:
        return rows
    return [EMPTY * len(rows[0]), *rows[:CHEST_ROW], *rows[CHEST_ROW + 1 :]]


def cell(top: str, bottom: str, palette: dict) -> str:
    if top == EMPTY and bottom == EMPTY:
        return "\x1b[0m "
    if bottom == EMPTY:
        return "\x1b[0m\x1b[38;2;{};{};{}m▀".format(*palette[top])
    if top == EMPTY:
        return "\x1b[0m\x1b[38;2;{};{};{}m▄".format(*palette[bottom])
    return "\x1b[38;2;{};{};{};48;2;{};{};{}m▀".format(*palette[top], *palette[bottom])


def lines(rows: list[str], palette: dict) -> list[str]:
    pairs = zip(rows[::2], rows[1::2], strict=True)
    return [
        "".join(cell(a, b, palette) for a, b in zip(top, bottom, strict=True)) + "\x1b[0m"
        for top, bottom in pairs
    ]


class Link(threading.Thread):
    """Follows the hub's stream. She sleeps whenever the hub can't be reached."""

    def __init__(self, url: str, token: str):
        super().__init__(daemon=True)
        self.url = url.rstrip("/") + "/state/v1/maid/stream"
        self.token = token
        self.state = {"blinks": 0, "asleep": True}

    def follow(self) -> None:
        request = urllib.request.Request(self.url, headers={"x-companion-token": self.token})
        with urllib.request.urlopen(request, timeout=30) as stream:
            for raw in stream:
                if raw.startswith(b"data: "):
                    self.state = json.loads(raw[6:])

    def run(self) -> None:
        while True:
            try:
                self.follow()
            except (OSError, ValueError):
                pass
            self.state = {**self.state, "asleep": True}
            time.sleep(RETRY_SECONDS)


def scene(sprite: dict, closed: bool, exhaled: bool) -> list[str]:
    return lines(breathe(sprite["closed" if closed else "open"], exhaled), sprite["palette"])


def main() -> None:
    parser = argparse.ArgumentParser(description="The maid in a terminal")
    parser.add_argument("--url", default=os.environ.get("DUSHA_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("DUSHA_TOKEN", ""))
    args = parser.parse_args()
    sprite = load()
    link = Link(args.url, args.token)
    link.start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l")
    shown: list[str] = []
    place = None
    seen = 0
    blink_until = 0.0
    breath_at = 0.0
    try:
        while True:
            now = time.monotonic()
            asleep, blinks = link.state["asleep"], link.state["blinks"]
            if blinks != seen:
                seen = blinks
                blink_until = now + BLINK_SECONDS
            breath_at += TICK_SECONDS / (SLEEP_BREATH_SECONDS if asleep else BREATH_SECONDS)
            frame = scene(sprite, asleep or now < blink_until, breath_at % 1 >= 0.5)
            size = shutil.get_terminal_size()
            top = max(1, (size.lines - len(frame)) // 2 + 1)
            left = max(1, (size.columns - len(sprite["open"][0])) // 2 + 1)
            if (top, left) != place:
                place, shown = (top, left), []
                out.write("\x1b[2J")
            changed = [
                f"\x1b[{top + i};{left}H{line}"
                for i, line in enumerate(frame)
                if top + i <= size.lines and (not shown or shown[i] != line)
            ]
            if changed:
                out.write("\x1b[?2026h" + "".join(changed) + "\x1b[?2026l")
                out.flush()
            shown = frame
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\x1b[?25h\x1b[?1049l")
        out.flush()


if __name__ == "__main__":
    main()
