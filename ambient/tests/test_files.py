from __future__ import annotations

import asyncio
import importlib.util
import sys

import pytest

from conftest import client, make_config

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or importlib.util.find_spec("inotify_simple") is None,
    reason="needs inotify_simple",
)


async def _rows(http) -> list[str]:
    params = {"source": "files", "kind": "file"}
    rows = (await http.get("/state/v1/ambient", params=params)).json()["observations"]
    return sorted(row["text"] for row in rows)


async def test_file_changes_are_batched_per_folder_with_saves_and_downloads_understood(tmp_path, plugins_dir):
    docs, dl = tmp_path / "docs", tmp_path / "dl"
    docs.mkdir()
    dl.mkdir()
    for name in ("notes.txt", "old.txt", "report.txt", "a.pdf"):
        (docs / name).write_text("before")
    files = {"roots": [str(docs), str(dl)], "window_minutes": 0.03}
    options = {"device": "desktop", "sample_seconds": 1, "files": files}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    async with client(config) as http:
        (docs / ".goutputstream-ABC").write_text("saved")
        (docs / ".goutputstream-ABC").rename(docs / "notes.txt")
        (docs / "new.md").write_text("# new")
        (docs / "scratch.txt").write_text("gone soon")
        (docs / "scratch.txt").unlink()
        (docs / "old.txt").unlink()
        (docs / "node_modules").mkdir()
        (docs / "node_modules" / "x.js").write_text("ignored")
        (docs / "report.txt").rename(docs / "final.txt")
        (docs / "batch").mkdir()
        for i in range(7):
            (docs / "batch" / f"f{i}.txt").write_text(str(i))
        (dl / "movie.mkv.part").write_text("half")
        (dl / "movie.mkv.part").rename(dl / "movie.mkv")
        (docs / "a.pdf").rename(dl / "a.pdf")
        for _ in range(50):
            await asyncio.sleep(0.2)
            if len(texts := await _rows(http)) >= 3:
                break
    assert texts == [
        f"{dl}: downloaded movie.mkv, moved {docs}/a.pdf → a.pdf",
        f"{docs}/batch: 7 created (f0.txt, f1.txt, f2.txt, …)",
        f"{docs}: created batch/, created new.md, modified notes.txt, "
        "moved report.txt → final.txt, deleted old.txt",
    ]
