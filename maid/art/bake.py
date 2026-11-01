"""Bakes the two PNGs into ../sprite.json: uv run --no-project --with numpy --with pillow bake.py"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).parent
WIDTH, HEIGHT = 92, 100
CROP_HEIGHT = 1100
SCALE = 6
PAPER = 246
LINE_COVERAGE = 0.3
# Luminance ceilings for ink, shadow, hair, and skin, anything brighter is white.
TONES = ((95, "0"), (150, "1"), (208, "2"), (236, "3"))
WHITE = "4"
EMPTY = "."


def load(name: str) -> np.ndarray:
    image = Image.open(HERE / name).convert("L")
    image = image.crop((0, 0, image.width, CROP_HEIGHT))
    return np.array(image.resize((WIDTH * SCALE, HEIGHT * SCALE), Image.LANCZOS), float)


def background(gray: np.ndarray) -> np.ndarray:
    """Flood from the edges at full size, where outlines still hold the fill back from the frills."""
    paper = gray >= PAPER
    high, wide = paper.shape
    seen = np.zeros_like(paper)
    queue = deque((x, y) for x in range(wide) for y in (0, high - 1))
    queue.extend((x, y) for y in range(high) for x in (0, wide - 1))
    while queue:
        x, y = queue.popleft()
        if not (0 <= x < wide and 0 <= y < high) or seen[y, x] or not paper[y, x]:
            continue
        seen[y, x] = True
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return seen


def tone(block: np.ndarray) -> str:
    if (block < TONES[0][0]).mean() >= LINE_COVERAGE:
        return TONES[0][1]
    middle = np.median(block)
    return next((key for ceiling, key in TONES if middle < ceiling), WHITE)


def pixelate(name: str) -> list[str]:
    gray = load(name)
    empty = background(gray)
    rows = []
    for y in range(HEIGHT):
        row = ""
        for x in range(WIDTH):
            box = (slice(y * SCALE, (y + 1) * SCALE), slice(x * SCALE, (x + 1) * SCALE))
            row += EMPTY if empty[box].mean() > 0.5 else tone(gray[box])
        rows.append(row)
    return rows


def main() -> None:
    sprite = {"open": pixelate("open.png"), "closed": pixelate("closed.png")}
    (HERE.parent / "sprite.json").write_text(json.dumps(sprite, indent=0) + "\n")


if __name__ == "__main__":
    main()
