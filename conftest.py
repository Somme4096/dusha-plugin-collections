from __future__ import annotations

import shutil
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from dusha import api
from dusha.config import BUILTIN_CONFIGS, AppConfig, ExternalPluginConfig

ROOT = Path(__file__).resolve().parent
PLUGINS = (
    "presence",
    "notification",
    "relay",
    "ambient",
    "better_proactive",
    "system_one",
    "everos_memory",
    "spawn",
    "music",
    "hands",
    "voice",
    "digest",
    "diary",
    "inbox",
    "focus",
)


@pytest.fixture(scope="session")
def plugins_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("plugins")
    for name in PLUGINS:
        shutil.copytree(
            ROOT / name, root / name, ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc", "tests")
        )
    return root


def make_config(data_dir: Path, plugins_dir: Path, plugins: dict, **kwargs) -> AppConfig:
    entries = {
        name: ExternalPluginConfig(options=value)
        if isinstance(value, dict) and name not in BUILTIN_CONFIGS
        else value
        for name, value in plugins.items()
    }
    return AppConfig(data_dir=data_dir, plugins_dir=str(plugins_dir), plugins=entries, **kwargs)


@asynccontextmanager
async def client(config: AppConfig):
    app = api.create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """A real dusha gateway on a loopback port, for plugins that talk to another instance."""

    def __init__(self, config: AppConfig, port: int):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(
            uvicorn.Config(api.create_app(config), host="127.0.0.1", port=port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> Server:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("dusha test server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def wait_for(predicate, timeout: float = 5.0, step: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition was not met in time")


def load_plugin(name: str):
    """Import a plugin's module directly, for unit tests that drive it with a fake ctx."""
    import importlib.util
    import sys

    unique = f"{name}_under_test"
    if unique in sys.modules:
        return sys.modules[unique]
    package = ROOT / name / name
    if package.is_dir():
        spec = importlib.util.spec_from_file_location(
            unique, package / "__init__.py", submodule_search_locations=[str(package)]
        )
    else:
        spec = importlib.util.spec_from_file_location(unique, ROOT / name / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Just enough ctx for unit tests: records emits, answers collect from a dict of callables."""

    def __init__(self, answers: dict | None = None):
        self.emitted: list = []
        self.answers = answers or {}

    def emit(self, event, *args):
        self.emitted.append((event, args))

    def collect(self, event, *args):
        answer = self.answers.get(event)
        return {"fake": answer(*args)} if answer else {}


class Recorder:
    """A loopback HTTP server that records JSON bodies, standing in for ntfy or a vision API."""

    def __init__(self, reply=None):
        """`reply` is a dict, or a callable taking the request body and returning a dict or (status, dict)."""
        import json
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.requests: list[dict] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                recorder.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": json.loads(body or b"{}"),
                    }
                )
                answer = reply(recorder.requests[-1]["body"]) if callable(reply) else reply
                status, answer = answer if isinstance(answer, tuple) else (200, answer)
                payload = json.dumps(answer or {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def recorder():
    started: list[Recorder] = []

    def start(**kwargs) -> Recorder:
        started.append(Recorder(**kwargs))
        return started[-1]

    yield start
    for item in started:
        item.close()


@pytest.fixture
def fake_desktop(tmp_path):
    """A stand-in for notify-send that logs its arguments, so tests never pop real notifications."""
    log = tmp_path / "desktop.log"
    script = tmp_path / "fake-notify"
    script.write_text(f'#!/bin/sh\nprintf "%s|%s\\n" "$1" "$2" >> {log}\n')
    script.chmod(0o755)
    return [str(script)], log


@pytest.fixture
def fake_screen(tmp_path):
    """A capture command drawing what the test puts in `frame`, so screen tests never see a real screen."""
    frame = tmp_path / "frame"
    frame.write_text("navy")
    script = tmp_path / "fake-shot.py"
    script.write_text(
        "import sys\n"
        "from PIL import Image, ImageDraw\n"
        "color = open(sys.argv[2]).read().strip()\n"
        "image = Image.new('RGB', (1600, 1000), color)\n"
        "x = sum(map(ord, color)) % 700\n"
        "ImageDraw.Draw(image).rectangle((x, x // 2, x + 600, x // 2 + 400), fill='white')\n"
        "image.save(sys.argv[1])\n"
    )
    import sys

    return [sys.executable, str(script), "{path}", str(frame)], frame


def completion(content) -> dict:
    """An OpenAI-style chat completion carrying `content` (a dict becomes its JSON)."""
    import json

    text = content if isinstance(content, str) else json.dumps(content)
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}
