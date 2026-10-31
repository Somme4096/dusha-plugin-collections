from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from conftest import client, make_config


def _fake(bin_dir: Path, name: str, output: Path) -> None:
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\ncat '{output}'\n")
    script.chmod(0o755)


async def _rows(http, source: str) -> list[str]:
    rows = (await http.get("/state/v1/ambient", params={"source": source})).json()["observations"]
    return [row["text"] for row in reversed(rows)]


async def _until(http, source: str, count: int, timeout: float = 10.0) -> list[str]:
    for _ in range(int(timeout / 0.2)):
        texts = await _rows(http, source)
        if len(texts) >= count:
            return texts
        await asyncio.sleep(0.2)
    raise AssertionError(f"only {await _rows(http, source)}")


async def test_package_updates_are_metrics_and_events_only_on_change(tmp_path, plugins_dir, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    flatpak, brew = tmp_path / "flatpak.txt", tmp_path / "brew.json"
    flatpak.write_text("org.gimp.GIMP\t3.0.4\ndev.zed.Zed\tv1.25.2\n")
    brew.write_text(json.dumps({"formulae": [{"name": "gh", "current_version": "2.103.2"}], "casks": []}))
    _fake(bin_dir, "flatpak", flatpak)
    _fake(bin_dir, "brew", brew)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    options = {"device": "desktop", "packages": {"backends": ["flatpak-user", "brew"], "every_seconds": 1}}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    async with client(config) as http:
        first = await _until(http, "packages", 2)
        await asyncio.sleep(2.5)
        assert len(await _rows(http, "packages")) == 2
        brew.write_text(json.dumps({"formulae": [], "casks": []}))
        flatpak.write_text("org.gimp.GIMP\t3.0.4\ndev.zed.Zed\tv1.26.0\n")
        texts = await _until(http, "packages", 3)
        metrics = (await http.get("/state/v1/ambient/metrics", params={"device": "desktop"})).json()
    assert sorted(first) == ["brew: 1 updates (gh)", "flatpak-user: 2 updates (org.gimp.GIMP, dev.zed.Zed)"]
    assert texts[2] == "flatpak-user: 2 updates (org.gimp.GIMP, dev.zed.Zed)"
    readings = metrics["metrics"]["desktop"]
    assert readings["pkg.updates:brew"]["last"] == 0.0
    assert readings["pkg.updates:flatpak-user"]["last"] == 2.0
    assert "pkg.reboot_pending" not in readings


async def test_apt_and_mise_are_read_from_local_state(tmp_path, plugins_dir, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    apt, mise = tmp_path / "apt.txt", tmp_path / "mise.json"
    apt.write_text(
        "Listing...\n"
        "curl/stable-security 8.14.1-2+deb13u7 amd64 [upgradable from: 8.14.1-2+deb13u5]\n"
        "gh/trixie 2.103.2 amd64 [upgradable from: 2.102.0]\n"
    )
    mise.write_text(json.dumps({"node": {"name": "node", "current": "20.0.0", "latest": "20.1.0"}}))
    _fake(bin_dir, "apt", apt)
    _fake(bin_dir, "mise", mise)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    options = {"device": "desktop", "packages": {"backends": ["apt", "mise"], "every_seconds": 1}}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    async with client(config) as http:
        texts = await _until(http, "packages", 2)
    assert sorted(texts) == ["apt: 2 updates (curl, gh)", "mise: 1 updates (node)"]


@pytest.mark.skipif(not os.path.exists("/run/ostree-booted"), reason="needs an rpm-ostree host")
async def test_a_staged_deployment_is_reported_once_and_counted_in_days(tmp_path, plugins_dir, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    status = tmp_path / "status.json"
    booted = {"booted": True, "version": "44.20261024.1"}
    status.write_text(json.dumps({"deployments": [{"booted": False, "version": "44.20261028.0"}, booted]}))
    _fake(bin_dir, "rpm-ostree", status)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    options = {"device": "desktop", "packages": {"backends": ["rpm-ostree"], "every_seconds": 1}}
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": options})
    async with client(config) as http:
        [staged] = await _until(http, "packages", 1)
        await asyncio.sleep(2.5)
        metrics = (await http.get("/state/v1/ambient/metrics", params={"name": "pkg.reboot_pending"})).json()
        assert await _rows(http, "packages") == [staged]
    assert staged == "rpm-ostree: 44.20261028.0 staged, reboot to apply"
    days = metrics["metrics"]["desktop"]["pkg.reboot_pending"]
    assert days["n"] >= 2 and 0 <= days["last"] < 0.01
