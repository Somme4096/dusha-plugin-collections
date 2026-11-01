from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import httpx

from conftest import Server, free_port, make_config, wait_for

ROOT = Path(__file__).resolve().parent.parent
MESSAGE = {"harness": "discord", "conversation_id": "dm", "role": "user", "content": "hi", "route": "dm"}
LINES = 60
COLUMNS = 110
EYES = 24


def plugin():
    spec = importlib.util.spec_from_file_location("maid_drawing", ROOT / "tui.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def faces(closed: bool) -> list[bytes]:
    module = plugin()
    rows = json.loads(module.SPRITE.read_text())["closed" if closed else "open"]
    return [
        module.scene(rows, sunk, COLUMNS, LINES)[EYES].encode() for sunk in range(module.BREATH_PIXELS + 1)
    ]


class Terminal:
    """tui.py on a real pty, collecting everything it paints."""

    def __init__(self, url: str):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", LINES, COLUMNS, 0, 0))
        env = {**os.environ, "COLUMNS": str(COLUMNS), "LINES": str(LINES)}
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "tui.py"), "--url", url],
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            env=env,
        )
        os.close(slave)
        self.painted = b""
        threading.Thread(target=self.read, daemon=True).start()

    def read(self) -> None:
        while select.select([self.master], [], [], 10)[0]:
            try:
                self.painted += os.read(self.master, 65536)
            except OSError:
                return

    def since(self, mark: int, wanted: list[bytes]) -> bool:
        return any(face in self.painted[mark:] for face in wanted)

    def leave(self) -> None:
        self.process.send_signal(signal.SIGINT)
        self.process.wait(5)
        wait_for(lambda: b"\x1b[?25h\x1b[?1049l" in self.painted)


def test_the_terminal_breathes_blinks_and_sleeps(tmp_path, plugins_dir):
    open_eyes, closed_eyes = faces(False), faces(True)
    config = make_config(tmp_path / "data", plugins_dir, {"proactive": False, "maid": {}})
    with Server(config, free_port()) as server, httpx.Client(base_url=server.url) as http:
        terminal = Terminal(server.url)
        try:
            wait_for(lambda: terminal.since(0, open_eyes[:1]), timeout=10)
            assert "▀".encode() in terminal.painted and b"38;5;" in terminal.painted
            assert b"38;2;" not in terminal.painted
            wait_for(lambda: terminal.since(0, open_eyes[1:]), timeout=5)

            assert http.post("/state/v1/messages", json=MESSAGE).status_code < 300
            mark = len(terminal.painted)
            wait_for(lambda: terminal.since(mark, closed_eyes))
            mark = len(terminal.painted)
            wait_for(lambda: terminal.since(mark, open_eyes))

            before = http.get("/state/v1/maid").json()["blinks"]
            http.post("/state/v1/context", json={"query": "x"})
            assert http.post("/state/v1/maid/blink").json()["blinks"] >= before + 2

            assert http.post("/state/v1/maid/sleep").json()["asleep"] is True
            time.sleep(0.5)
            mark = len(terminal.painted)
            time.sleep(4)
            assert terminal.since(mark, closed_eyes) and not terminal.since(mark, open_eyes)

            assert http.post("/state/v1/maid/blink").json()["asleep"] is False
            mark = len(terminal.painted)
            wait_for(lambda: terminal.since(mark, open_eyes))
        finally:
            terminal.leave()


def test_she_falls_asleep_after_quiet_and_blinks_by_tool(tmp_path, plugins_dir):
    plugins = {"proactive": False, "maid": {"sleep_after_minutes": 0.001}}
    config = make_config(tmp_path / "data", plugins_dir, plugins)
    with Server(config, free_port()) as server, httpx.Client(base_url=server.url) as http:
        wait_for(lambda: http.get("/state/v1/maid").json()["asleep"])
        tool = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "maid_blink", "arguments": {}},
        }
        assert http.post("/mcp", json=tool).status_code == 200
        assert http.get("/state/v1/maid").json()["blinks"] == 1


def test_she_sleeps_when_the_hub_is_gone():
    terminal = Terminal(f"http://127.0.0.1:{free_port()}")
    try:
        wait_for(lambda: terminal.since(0, faces(True)), timeout=10)
    finally:
        terminal.leave()
