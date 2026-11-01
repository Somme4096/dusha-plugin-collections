from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import pty
import select
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


def tui():
    spec = importlib.util.spec_from_file_location("maid_tui", ROOT / "tui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Terminal:
    """tui.py on a real pty, collecting everything it paints."""

    def __init__(self, url: str):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 80, 0, 0))
        env = {**os.environ, "COLUMNS": "80", "LINES": "30"}
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

    def close(self) -> None:
        self.process.terminate()
        self.process.wait(5)


def test_breathing_blinking_and_sleeping_in_a_terminal(tmp_path, plugins_dir):
    module = tui()
    sprite = module.load()
    faces = {
        (closed, exhaled): [line.encode() for line in module.scene(sprite, closed, exhaled)]
        for closed in (False, True)
        for exhaled in (False, True)
    }
    eye_row = 12
    config = make_config(
        tmp_path / "data", plugins_dir, {"proactive": False, "maid": {"sleep_after_minutes": 30}}
    )
    with Server(config, free_port()) as server:
        terminal = Terminal(server.url)
        http = httpx.Client(base_url=server.url)
        try:
            assert http.get("/state/v1/maid").json() == {"blinks": 0, "asleep": False}
            wait_for(lambda: faces[(False, False)][eye_row] in terminal.painted)
            assert "▀".encode() in terminal.painted and b"38;2;" in terminal.painted

            wait_for(lambda: faces[(False, True)][eye_row] in terminal.painted, timeout=5)

            mark = len(terminal.painted)
            assert http.post("/state/v1/messages", json=MESSAGE).status_code < 300
            assert http.get("/state/v1/maid").json()["blinks"] >= 1
            shut = (faces[(True, False)][eye_row], faces[(True, True)][eye_row])
            wait_for(lambda: any(face in terminal.painted[mark:] for face in shut))

            before = http.get("/state/v1/maid").json()["blinks"]
            http.post("/state/v1/context", json={"query": "x"})
            http.post("/state/v1/maid/blink")
            assert http.get("/state/v1/maid").json()["blinks"] >= before + 2

            assert http.post("/state/v1/maid/sleep").json()["asleep"] is True
            time.sleep(0.5)
            mark = len(terminal.painted)
            time.sleep(4.5)
            assert not any(
                faces[(False, exhaled)][eye_row] in terminal.painted[mark:] for exhaled in (False, True)
            )

            assert http.post("/state/v1/maid/blink").json()["asleep"] is False
            mark = len(terminal.painted)
            wait_for(
                lambda: any(
                    faces[(False, exhaled)][eye_row] in terminal.painted[mark:] for exhaled in (False, True)
                )
            )
        finally:
            terminal.close()
            http.close()


def test_she_sleeps_after_quiet_and_is_not_bothered_by_a_lost_hub(tmp_path, plugins_dir):
    config = make_config(
        tmp_path / "data", plugins_dir, {"proactive": False, "maid": {"sleep_after_minutes": 0.001}}
    )
    with Server(config, free_port()) as server, httpx.Client(base_url=server.url) as http:
        wait_for(lambda: http.get("/state/v1/maid").json()["asleep"])
        tool = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "maid_blink", "arguments": {}},
        }
        assert '"blinks": 1' in json.dumps(http.post("/mcp", json=tool).json()).replace('\\"', '"')

    module = tui()
    sprite = module.load()
    terminal = Terminal(f"http://127.0.0.1:{free_port()}")
    try:
        closed = module.scene(sprite, True, False)[12].encode()
        wait_for(lambda: closed in terminal.painted)
    finally:
        terminal.close()
