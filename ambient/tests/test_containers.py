from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from conftest import client, make_config

IMAGES = ("busybox:latest", "alpine:latest", "python:3.10-slim")


def _image() -> str | None:
    if not shutil.which("podman"):
        return None
    for image in IMAGES:
        if subprocess.run(["podman", "image", "exists", f"docker.io/library/{image}"]).returncode == 0:
            return f"docker.io/library/{image}"
    return None


IMAGE = _image()
pytestmark = pytest.mark.skipif(IMAGE is None, reason=f"needs podman and one of {IMAGES} pulled")


def _podman(*args: str) -> None:
    subprocess.run(["podman", *args], check=True, capture_output=True)


async def _rows(http, name: str, kind: str) -> list[str]:
    params = {"source": "container", "kind": kind, "limit": 500}
    rows = (await http.get("/state/v1/ambient", params=params)).json()["observations"]
    return [row["text"] for row in reversed(rows) if name in row["text"]]


async def _until(http, name: str, kind: str, count: int, timeout: float = 20.0) -> list[str]:
    for _ in range(int(timeout / 0.2)):
        texts = await _rows(http, name, kind)
        if len(texts) >= count:
            return texts
        await asyncio.sleep(0.2)
    raise AssertionError(f"only {await _rows(http, name, kind)}")


async def test_containers_alert_on_crashes_not_stops_and_keep_their_stderr(tmp_path, plugins_dir):
    crash = f"dusha-test-crash-{os.getpid()}"
    calm = f"dusha-test-calm-{os.getpid()}"
    script = "sleep 1; echo fine; echo same-error >&2; echo same-error >&2; echo other >&2; sleep 1; exit 3"
    containers = {"runtimes": ["podman"], "settle_seconds": 2, "exclude": []}
    options = {"device": "desktop", "sample_seconds": 1, "containers": containers}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    try:
        async with client(config) as http:
            await asyncio.sleep(1.5)  # podman events is following
            _podman("run", "-d", "--rm", "--name", crash, IMAGE, "sh", "-c", script)
            [alert] = await _until(http, crash, "alert", 1)
            logs = await _rows(http, crash, "log")

            _podman("run", "-d", "--rm", "--name", calm, IMAGE, "sleep", "30")
            await asyncio.sleep(1)
            _podman("stop", "-t", "0", calm)

            for _ in range(2):
                await asyncio.sleep(1)
                _podman("run", "-d", "--rm", "--name", crash, IMAGE, "sh", "-c", "exit 3")
            alerts = await _until(http, crash, "alert", 2)
            _podman("run", "-d", "--rm", "--name", crash, IMAGE, "sleep", "8")
            resolved = await _until(http, crash, "resolved", 2)
            assert await _rows(http, calm, "alert") == []
    finally:
        subprocess.run(["podman", "rm", "-f", crash, calm], capture_output=True)
    assert alert == f"`{crash}` exited with code 3"
    assert logs == [f"{crash}: same-error", f"{crash}: other"]
    assert alerts[1] == f"`{crash}` is restarting in a loop (3 exits in 10 min)"
    assert sorted(resolved) == [f"container:{crash} back to normal", f"container:{crash}:loop back to normal"]
