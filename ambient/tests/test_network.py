from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time

import psutil
import pytest

from conftest import client, make_config

MONITOR = "/run/systemd/resolve/io.systemd.Resolve.Monitor"


def _dns_allowed() -> bool:
    """Whether polkit lets this user subscribe without a password, asked without ever prompting."""
    if not (os.path.exists(MONITOR) and shutil.which("resolvectl")):
        return False
    request = {
        "method": "io.systemd.Resolve.Monitor.SubscribeQueryResults",
        "parameters": {"allowInteractiveAuthentication": False},
        "more": True,
    }
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(MONITOR)
        sock.sendall(json.dumps(request).encode() + b"\0")
        return "error" not in json.loads(sock.recv(65536).split(b"\0", 1)[0])


def _lan_ip() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # nothing is sent, this only picks the outgoing address
        except OSError:
            return None
        ip = probe.getsockname()[0]
    return None if ip.startswith("127.") else ip


def _listen() -> socket.socket:
    for port in range(20000, 20100):
        server = socket.socket()
        try:
            server.bind(("0.0.0.0", port))
        except OSError:
            server.close()
            continue
        server.listen()
        return server
    raise RuntimeError("no free port")


async def _rows(http, kind: str) -> list[str]:
    params = {"source": "network", "kind": kind, "limit": 500}
    rows = (await http.get("/state/v1/ambient", params=params)).json()["observations"]
    return [row["text"] for row in reversed(rows)]


async def _until(http, kind: str, needle: str, nudge=None, timeout: float = 20.0) -> list[str]:
    for _ in range(int(timeout / 0.5)):
        if nudge:
            nudge()
        if [text for text in await _rows(http, kind) if needle in text]:
            return await _rows(http, kind)
        await asyncio.sleep(0.5)
    raise AssertionError(f"no {needle!r} in {await _rows(http, kind)}")


async def test_new_ports_and_connections_are_noticed(tmp_path, plugins_dir):
    ip = _lan_ip()
    if ip is None:
        pytest.skip("needs a non-loopback address")
    me = psutil.Process().name()
    network = {"dns": False, "learn_hours": 0, "window_minutes": 0.05}
    config = make_config(
        tmp_path / "data",
        plugins_dir,
        {"ambient": {"device": "desktop", "sample_seconds": 1, "network": network}},
    )
    async with client(config) as http:
        await asyncio.sleep(1.5)  # the first snapshot is what was already listening
        server = _listen()
        port = server.getsockname()[1]
        peer = socket.create_connection((ip, port))
        accepted, _ = server.accept()
        try:
            events = await _until(http, "event", f"port {port}")
            summaries = await _until(http, "network", f"{me} (")
        finally:
            for item in (peer, accepted, server):
                item.close()
    assert f"new listening port {port} by {me} (all interfaces)" in events
    assert any(f"{me} (" in text and ip in text for text in summaries)


@pytest.mark.skipif(not _dns_allowed(), reason="needs resolved's monitor allowed for this user in polkit")
async def test_dns_lookups_are_counted_per_site_and_new_sites_noticed(tmp_path, plugins_dir):
    name = f"dusha-{os.getpid()}-{int(time.time())}.example.org"
    network = {"connections": False, "learn_hours": 0, "window_minutes": 0.1}
    config = make_config(
        tmp_path / "data",
        plugins_dir,
        {"ambient": {"device": "desktop", "sample_seconds": 1, "network": network}},
    )

    def lookup() -> None:
        subprocess.run(["resolvectl", "query", "--cache=no", name], capture_output=True, timeout=10)

    async with client(config) as http:
        events = await _until(http, "event", "example.org", nudge=lookup)
        summaries = await _until(http, "network", "example.org")
    assert events.count("new domain: example.org") == 1
    assert any("example.org (" in text for text in summaries)
