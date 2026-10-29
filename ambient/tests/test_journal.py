from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time

import pytest

from conftest import client, make_config


def _user_systemd() -> bool:
    if not (shutil.which("journalctl") and shutil.which("systemd-run") and shutil.which("logger")):
        return False
    state = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True)
    return state.stdout.strip() in ("running", "degraded")


pytestmark = pytest.mark.skipif(not _user_systemd(), reason="needs journald and a systemd user session")


def _run(unit: str, command: str) -> None:
    subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", command], check=True)


async def _rows(http, token: str, kind: str = "") -> list[str]:
    params = {"source": "journal", "kind": kind, "limit": 500}
    rows = (await http.get("/state/v1/ambient", params=params)).json()["observations"]
    return [row["text"] for row in reversed(rows) if token in row["text"]]


async def _until(http, token: str, count: int, kind: str = "", timeout: float = 15.0) -> list[str]:
    for _ in range(int(timeout / 0.2)):
        texts = await _rows(http, token, kind)
        if len(texts) >= count:
            return texts
        await asyncio.sleep(0.2)
    raise AssertionError(f"only {await _rows(http, token, kind)}")


async def test_journal_errors_fold_failed_units_alert_and_the_cursor_resumes(tmp_path, plugins_dir):
    token = f"dusha-test-{os.getpid()}-{int(time.time())}"
    unit = f"{token}.service"
    journal = {
        "resume_hours": 0.002,
        "settle_seconds": 1,
        "exclude_patterns": ["ignore-me"],
        "exclude_units": [],
    }
    options = {"device": "desktop", "sample_seconds": 1, "journal": journal}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    async with client(config) as http:
        await asyncio.sleep(1)  # journalctl is following
        for _ in range(3):
            subprocess.run(["logger", "-t", token, "-p", "user.err", "disk on fire"], check=True)
        subprocess.run(["logger", "-t", token, "-p", "user.err", "ignore-me please"], check=True)
        subprocess.run(["logger", "-t", token, "-p", "user.warning", "only a warning"], check=True)
        _run(unit, "/usr/bin/false")
        [alert] = await _until(http, token, 1, kind="alert")
        _run(unit, "/usr/bin/true")
        [resolved] = await _until(http, token, 1, kind="resolved")
        logs = await _rows(http, token, kind="log")
    assert alert == f"`{unit}` failed (exit-code)"
    assert resolved == f"unit:{unit} back to normal"
    assert logs == [f"{token}: disk on fire"]

    subprocess.run(["logger", "-t", token, "-p", "user.err", "while it was down"], check=True)
    async with client(config) as http:
        logs = await _until(http, token, 2, kind="log")
    assert logs == [f"{token}: disk on fire", f"{token}: while it was down"]
