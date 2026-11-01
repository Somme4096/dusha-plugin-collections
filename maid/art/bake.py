"""Bakes the two PNGs into ../sprite.json: uv run --no-project --with numpy --with pillow bake.py"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

HERE = Path(__file__).parent
WIDTH, HEIGHT = 40, 48
SCALE = 12
LINE_COVERAGE = 40
KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
EMPTY = "."
PAPER = 246
BLACK = (40, 40, 50)
SHADOW = (70, 70, 82)
IRIS = (196, 200, 208)
# Hand-placed so the open eyes survive at this size, as (x, y, colour).
EYES = [
    *[(x, 24, "line") for x in range(11, 18)],
    *[(x, 25, "iris") for x in range(13, 17)],
    *[(x, 26, "iris") for x in range(14, 16)],
    (14, 25, "line"),
    *[(x, 21, "line") for x in range(22, 29)],
    *[(x, 22, "iris") for x in range(24, 28)],
    *[(x, 23, "iris") for x in range(25, 27)],
    (25, 22, "line"),
]


def clusters(pixels: np.ndarray, count: int = 12) -> np.ndarray:
    rng = np.random.default_rng(1)
    sample = pixels.reshape(-1, 3)
    sample = sample[rng.choice(len(sample), 40000)]
    centers = sample[rng.choice(len(sample), count, replace=False)]
    for _ in range(25):
        nearest = ((sample[:, None] - centers[None]) ** 2).sum(2).argmin(1)
        centers = np.array(
            [sample[nearest == i].mean(0) if (nearest == i).any() else centers[i] for i in range(count)]
        )
    return centers


def load(name: str) -> tuple[np.ndarray, np.ndarray]:
    image = Image.open(HERE / name).convert("RGB").resize((WIDTH * SCALE, HEIGHT * SCALE), Image.LANCZOS)
    # Hair and skin are almost the same grey until saturation pulls them apart.
    return np.array(ImageEnhance.Color(image).enhance(3.0), float), np.array(image, float)


def regrade(color: np.ndarray) -> tuple[int, ...]:
    light, spread = color.mean(), color.max() - color.min()
    if light < 80:
        return BLACK
    if light < 110:
        return SHADOW
    warm = color[0] - color[2]
    if warm > 12 and light > 190:
        return (246, 226, 216)
    if warm > 12:
        return (214, 158, 148)
    if light > 212 and spread < 14:
        return (244, 244, 248)
    return tuple(int(v) for v in color)


def pixelate(graded: np.ndarray, plain: np.ndarray, centers: np.ndarray, means: list) -> list[list]:
    labels = ((graded[:, :, None] - centers[None, None]) ** 2).sum(3).argmin(2)
    dark = [i for i, color in enumerate(means) if sum(color) < 110 * 3]
    rows = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            cell = labels[y * SCALE : (y + 1) * SCALE, x * SCALE : (x + 1) * SCALE].ravel()
            counts = np.bincount(cell, minlength=len(centers))
            pick = counts.argmax()
            if counts[dark].sum() >= LINE_COVERAGE:
                pick = dark[counts[dark].argmax()]
            row.append(means[pick])
        rows.append(row)
    return rows


def background(plain: np.ndarray) -> np.ndarray:
    """Flood from the edges at full size, where outlines still hold the fill back from the frills."""
    paper = plain.min(2) >= PAPER
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


def clear_background(rows: list[list], plain: np.ndarray) -> None:
    empty = background(plain)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if empty[y * SCALE : (y + 1) * SCALE, x * SCALE : (x + 1) * SCALE].mean() > 0.5:
                rows[y][x] = None


def encode(rows: list[list], palette: list) -> list[str]:
    return ["".join(EMPTY if c is None else KEYS[palette.index(c)] for c in row) for row in rows]


def main() -> None:
    graded, plain = load("open.png")
    centers = clusters(graded)
    labels = ((graded[:, :, None] - centers[None, None]) ** 2).sum(3).argmin(2)
    means = [
        regrade(plain[labels == i].mean(0)) if (labels == i).any() else BLACK for i in range(len(centers))
    ]
    closed_graded, closed_plain = load("closed.png")
    awake = pixelate(graded, plain, centers, means)
    asleep = pixelate(closed_graded, closed_plain, centers, means)
    clear_background(awake, plain)
    clear_background(asleep, closed_plain)
    for x, y, tone in EYES:
        awake[y][x] = BLACK if tone == "line" else IRIS
    palette = sorted({c for rows in (awake, asleep) for row in rows for c in row if c is not None})
    sprite = {
        "palette": {KEYS[i]: list(c) for i, c in enumerate(palette)},
        "open": encode(awake, palette),
        "closed": encode(asleep, palette),
    }
    (HERE.parent / "sprite.json").write_text(json.dumps(sprite, indent=0) + "\n")


if __name__ == "__main__":
    main()
