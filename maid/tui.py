"""The maid in a terminal, needs nothing but Python: python tui.py --url http://hub:8765 --token ..."""

from __future__ import annotations

import argparse
import json
import math
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
BREATH_PIXELS = 2
# Everything above this pixel row sinks on the exhale.
CHEST_ROW = 78
EMPTY = "."
GREYS = {"0": 238, "1": 244, "2": 250, "3": 253, "4": 255}


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


def main() -> None:
    parser = argparse.ArgumentParser(description="The maid in a terminal")
    parser.add_argument("--url", default=os.environ.get("DUSHA_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("DUSHA_TOKEN", ""))
    args = parser.parse_args()
    sprite = json.loads(SPRITE.read_text())
    link = Link(args.url, args.token)
    link.start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l")
    shown: list[str] = []
    place = None
    seen = 0
    blink_until = 0.0
    phase = 0.0
    try:
        while True:
            now = time.monotonic()
            asleep, blinks = link.state["asleep"], link.state["blinks"]
            if blinks != seen:
                seen = blinks
                blink_until = now + BLINK_SECONDS
            phase += TICK_SECONDS / (SLEEP_BREATH_SECONDS if asleep else BREATH_SECONDS)
            sunk = round((1 - math.cos(2 * math.pi * phase)) / 2 * BREATH_PIXELS)
            face = "closed" if asleep or now < blink_until else "open"
            size = shutil.get_terminal_size()
            frame = scene(sprite[face], sunk, size.columns, size.lines)
            top = max(1, (size.lines - len(frame)) // 2 + 1)
            left = max(1, (size.columns - len(sprite["open"][0])) // 2 + 1)
            if (top, left, size) != place:
                place, shown = (top, left, size), []
                out.write("\x1b[0m\x1b[2J")
            changed = [
                f"\x1b[{top + i};{left}H{text}"
                for i, text in enumerate(frame)
                if not shown or shown[i] != text
            ]
            if changed:
                out.write("\x1b[?2026h" + "".join(changed) + "\x1b[?2026l")
                out.flush()
            shown = frame
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()


if __name__ == "__main__":
    main()
