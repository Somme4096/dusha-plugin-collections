from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from conftest import Server, free_port, make_config, wait_for

pytestmark = pytest.mark.skipif(
    not shutil.which("busctl") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
    reason="needs a session bus",
)

PLAYER = """
import asyncio, sys
from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method

log = sys.argv[1]
def note(text):
    with open(log, "a") as handle:
        handle.write(text + "\\n")

class Player(ServiceInterface):
    def __init__(self):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.title = "Rain Song"
        self.level = 1.0
    @method()
    def Next(self):
        note("next")
        self.title = "Other Song"
    @method()
    def Pause(self):
        note("pause")
    @method()
    def OpenUri(self, uri: "s"):
        note("open " + uri)
    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return "Playing"
    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return {"xesam:title": Variant("s", self.title), "xesam:artist": Variant("as", ["Tester"]),
                "xesam:album": Variant("s", "Album"), "xesam:url": Variant("s", "https://example.test/x")}
    @dbus_property()
    def Volume(self) -> "d":
        return self.level
    @Volume.setter
    def Volume(self, value: "d"):
        self.level = value
        note(f"volume {value}")

async def main():
    bus = await MessageBus().connect()
    bus.export("/org/mpris/MediaPlayer2", Player())
    await bus.request_name("org.mpris.MediaPlayer2.dusha_test")
    print("ready", flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
"""


@pytest.fixture
def player(tmp_path):
    script, log = tmp_path / "player.py", tmp_path / "player.log"
    script.write_text(PLAYER)
    log.write_text("")
    process = subprocess.Popen([sys.executable, str(script), str(log)], stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "ready"
    yield log
    process.kill()
    process.wait()


class YouTubeMusicApp:
    """The app's api-server routes, as its bundled source defines them."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.queue: list[dict] = [{"id": "old"}]
        app = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self, status: int, body: dict | None = None) -> None:
                payload = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def handle_any(self) -> None:
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(size) or b"{}")
                app.calls.append((self.command, self.path, body))
                if self.path == "/auth/dusha":
                    return self.answer(200, {"accessToken": "granted"})
                if self.headers.get("Authorization") != "Bearer granted":
                    return self.answer(401)
                if self.path == "/api/v1/search":
                    item = {
                        "flexColumns": [{"runs": [{"text": "Found Song"}]}],
                        "overlay": {"playNavigationEndpoint": {"watchEndpoint": {"videoId": "vid123"}}},
                    }
                    shelf = {"contents": [{"musicResponsiveListItemRenderer": item}]}
                    return self.answer(200, {"contents": {"sectionList": [{"musicShelfRenderer": shelf}]}})
                if self.command == "POST" and self.path == "/api/v1/queue":
                    app.queue.append({"id": body["videoId"]})
                    return self.answer(204)
                if self.command == "GET" and self.path == "/api/v1/queue":
                    return self.answer(200, {"items": app.queue})
                return self.answer(204)

            do_GET = do_POST = do_PATCH = handle_any

            def log_message(self, format, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def mcp(url: str, tool: str, **arguments) -> tuple[bool, dict]:
    message = {"name": tool, "arguments": arguments}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": message}
    result = httpx.post(url + "/mcp", json=body, timeout=30).json()["result"]
    return bool(result.get("isError")), json.loads(result["content"][0]["text"])


def test_a_track_reaches_ambient_and_the_hub_controls_the_player(tmp_path, plugins_dir, player):
    songs = tmp_path / "songs" / "Tester"
    songs.mkdir(parents=True)
    song = songs / "Local Rain.flac"
    song.write_bytes(b"")
    (songs / "Local Rain.txt").write_text("not audio")
    app = YouTubeMusicApp()
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "desk", "sensor": "none", "interval_seconds": 30},
            "relay": {},
            "ambient": {"device": "desk"},
            "music": {
                "device": "desk",
                "every_seconds": 1,
                "min_listen_seconds": 1,
                "control": True,
                "players": ["dusha_test"],
                "library": [str(tmp_path / "songs")],
                "local_player": "dusha_test",
                "youtube_music": app.url,
            },
        },
    )
    with Server(config, free_port()) as hub:

        def rows() -> list:
            params = {"source": "music"}
            return httpx.get(hub.url + "/state/v1/ambient", params=params).json()["observations"]

        row = wait_for(rows, timeout=8)[0]
        assert row["text"] == "Rain Song — Tester"
        assert row["kind"] == "music"
        assert row["data"]["player"] == "dusha_test"
        context = httpx.post(hub.url + "/state/v1/context", json={"query": "x"}).text
        assert "music_now" in context and "Rain Song" in context

        error, result = mcp(hub.url, "music", device="desk")
        assert not error, result
        assert result["players"][0]["title"] == "Rain Song"
        assert result["players"][0]["status"] == "playing"

        error, result = mcp(hub.url, "music", action="next", device="desk")
        assert not error and result == {"device": "desk", "player": "dusha_test", "done": "next"}
        error, result = mcp(hub.url, "music", action="volume", query="0.4", device="desk")
        assert not error, result
        error, result = mcp(hub.url, "music", action="play", query="local rain", device="desk")
        assert not error and result["source"] == "local"
        error, result = mcp(hub.url, "music", action="album", query="tester", device="desk")
        assert not error, result
        assert result == {"device": "desk", "source": "local", "album": "Tester"}
        error, result = mcp(hub.url, "music", action="album", query="nothing here", device="desk")
        assert error and "no album folder matches" in result["detail"]
        assert player.read_text().splitlines() == [
            "next",
            "volume 0.4",
            f"open {song.as_uri()}",
            f"open {songs.as_uri()}/",
        ]

        error, result = mcp(hub.url, "music", action="play", query="found song", device="desk")
        assert not error and result == {"device": "desk", "source": "youtube_music", "song": "Found Song"}
        requests = [(method, path) for method, path, _ in app.calls]
        assert requests[:2] == [("POST", "/auth/dusha"), ("POST", "/api/v1/search")]
        assert ("POST", "/api/v1/queue") in requests
        assert ("PATCH", "/api/v1/queue", {"index": 1}) in app.calls

        error, result = mcp(hub.url, "music", action="dance", device="desk")
        assert error and "action must be one of" in result["detail"]


def test_a_skipped_track_is_not_logged_and_control_stays_off_by_default(tmp_path, plugins_dir, player):
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "desk", "sensor": "none", "interval_seconds": 30},
            "relay": {},
            "ambient": {"device": "desk"},
            "music": {
                "device": "desk",
                "every_seconds": 1,
                "min_listen_seconds": 600,
                "players": ["dusha_test"],
            },
        },
    )
    with Server(config, free_port()) as hub:
        error, result = mcp(hub.url, "music", device="desk")
        assert error and result["detail"] == "desk can't music, it offers: nothing"
        time.sleep(2.5)
        rows = httpx.get(hub.url + "/state/v1/ambient", params={"source": "music"}).json()
        assert rows["observations"] == []
