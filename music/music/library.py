"""Find songs in local folders by the words in their paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

AUDIO = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".ape", ".alac"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def search(roots: list[str], query: str, limit: int = 5) -> list[Path]:
    wanted = _words(query)
    if not wanted:
        return []
    found: list[tuple[int, int, Path]] = []
    for root in roots:
        base = os.path.expanduser(root)
        for folder, _, names in os.walk(base):
            for name in names:
                path = Path(folder, name)
                if path.suffix.casefold() not in AUDIO:
                    continue
                if wanted <= _words(str(path.relative_to(base))):
                    # Words in the file name beat words only in its folders.
                    found.append((-len(wanted & _words(path.stem)), len(str(path)), path))
    return [item[2] for item in sorted(found)[:limit]]


def albums(roots: list[str], query: str, limit: int = 5) -> list[Path]:
    wanted = _words(query)
    if not wanted:
        return []
    found: list[tuple[int, str, Path]] = []
    for root in roots:
        base = os.path.expanduser(root)
        for folder, _, names in os.walk(base):
            path = Path(folder)
            if str(path) == str(Path(base)):
                continue
            if not any(Path(name).suffix.casefold() in AUDIO for name in names):
                continue
            if wanted <= _words(str(path.relative_to(base))):
                found.append((-len(wanted & _words(path.name)), str(path), path))
    return [item[2] for item in sorted(found)[:limit]]
