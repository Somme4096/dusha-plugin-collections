"""What's on their screen, as text for the timeline.

Capture, shrink, skip if unchanged, describe with a vision model.

Capture backends follow ~/src/sophia-connector/services/screen.py: the xdg-desktop-portal on
Wayland (GNOME, KDE), then grim and gnome-screenshot, and screencapture on macOS.
"""

from __future__ import annotations

import base64
import io
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

STATUSES = ("focused", "stuck", "waiting", "communicating", "browsing", "leisure")
PROGRESS = ("new", "continued", "progressed", "switched")
FIELDS = ("app", "title", "content", "activity", "status", "progress", "notable")

PROMPT = f"""You watch their screen for a companion who lives on their computers.
Describe this screenshot as one JSON object with exactly these keys:

- "app": the application in front, by its common name (VS Code, Chrome, Discord, Terminal).
- "title": the window, tab, or document title as shown.
- "content": 1 to 3 sentences on what is actually visible: the code or text they are looking at,
  the page's subject, who a chat is with and what about. Quote short error messages exactly.
- "activity": what they are doing, as a short verb phrase ("debugging a failing pytest").
- "status": one of {", ".join(STATUSES)}. "stuck" means the same problem is still unsolved compared with the
  previous capture. "waiting" means a build, download, install, or spinner.
- "progress": compared with the previous capture, one of {", ".join(PROGRESS)}. Use "new" when there is no
  previous capture.
- "notable": anything a companion would care about (an error, a deadline, a message from a person), or "".

Never copy passwords, keys, or tokens. Answer with the JSON object only."""


class ScreenError(RuntimeError):
    pass


def prompt(prompts: Any) -> str:
    """The vision prompt: `plugins.ambient.screen` in prompts.json, a string or a list of lines."""
    slot = ((prompts or {}).get("plugins") or {}).get("ambient") or {}
    unknown = sorted(set(slot) - {"screen"})
    if unknown:
        raise ValueError(f"unknown field(s) under prompts plugins.ambient: {unknown}")
    text = slot.get("screen", PROMPT)
    if isinstance(text, list) and all(isinstance(line, str) for line in text):
        text = "\n".join(text)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("prompts plugins.ambient.screen must be a non-empty string or a list of lines")
    return text


# Capture. Each backend returns PNG bytes.


def _run(command: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScreenError(f"{command[0]} failed: {error}") from error


def _to_file(command: list[str]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dusha-screen-") as folder:
        path = Path(folder) / "screen.png"
        _run([part.replace("{path}", str(path)) for part in command])
        if not path.exists():
            raise ScreenError(f"{command[0]} wrote no image")
        return path.read_bytes()


def _portal() -> bytes:
    """org.freedesktop.portal.Screenshot, non-interactive. Works on GNOME and KDE Wayland."""
    import asyncio

    async def call() -> str:
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus

        bus = await MessageBus().connect()
        try:
            token = "dusha_" + secrets.token_hex(8)
            sender = bus.unique_name.removeprefix(":").replace(".", "_")
            handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
            done = asyncio.get_running_loop().create_future()

            def on_signal(message: Message) -> None:
                if (
                    message.message_type == MessageType.SIGNAL
                    and message.path == handle
                    and message.member == "Response"
                    and not done.done()
                ):
                    done.set_result(message.body)

            # Subscribe before calling, so a fast reply isn't missed.
            rule = (
                f"type='signal',interface='org.freedesktop.portal.Request',member='Response',path='{handle}'"
            )
            await bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=[rule],
                )
            )
            bus.add_message_handler(on_signal)
            options = {
                "handle_token": Variant("s", token),
                "interactive": Variant("b", False),
                "modal": Variant("b", False),
            }
            reply = await bus.call(
                Message(
                    destination="org.freedesktop.portal.Desktop",
                    path="/org/freedesktop/portal/desktop",
                    interface="org.freedesktop.portal.Screenshot",
                    member="Screenshot",
                    signature="sa{sv}",
                    body=["", options],
                )
            )
            if reply.message_type == MessageType.ERROR:
                raise ScreenError(f"portal refused the screenshot: {reply.body}")
            code, results = await asyncio.wait_for(done, timeout=30)
            if code != 0:
                raise ScreenError(f"portal screenshot was denied or cancelled (code {code})")
            return results["uri"].value
        finally:
            bus.disconnect()

    uri = asyncio.run(call())
    path = Path(unquote(urlparse(uri).path))
    try:
        return path.read_bytes()
    finally:
        # The portal saves into ~/Pictures (GNOME) or /tmp. Don't leave one file per capture behind.
        path.unlink(missing_ok=True)


def _portal_available() -> bool:
    if platform.system() != "Linux" or os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        return False
    try:
        import dbus_next  # noqa: F401
    except ImportError:
        return False
    return True


def _imagegrab() -> bytes:
    from PIL import ImageGrab

    buffer = io.BytesIO()
    ImageGrab.grab(all_screens=True).save(buffer, "PNG")
    return buffer.getvalue()


BACKENDS = {
    "imagegrab": (lambda: platform.system() == "Windows", _imagegrab),
    "portal": (_portal_available, _portal),
    "grim": (
        lambda: platform.system() == "Linux" and bool(shutil.which("grim")),
        lambda: _to_file(["grim", "{path}"]),
    ),
    "gnome-screenshot": (
        lambda: platform.system() == "Linux" and bool(shutil.which("gnome-screenshot")),
        lambda: _to_file(["gnome-screenshot", "-f", "{path}"]),
    ),
    "screencapture": (
        lambda: platform.system() == "Darwin",
        lambda: _to_file(["screencapture", "-x", "-t", "png", "{path}"]),
    ),
}


def capture(backend: str = "auto", command: list[str] | None = None) -> bytes:
    if backend == "command":
        if not command:
            raise ScreenError("screen backend 'command' needs 'command', an argv with {path}")
        return _to_file(command)
    if backend != "auto":
        if backend not in BACKENDS:
            raise ScreenError(f"unknown screen backend {backend!r}")
        return BACKENDS[backend][1]()
    errors = []
    for name, (available, grab) in BACKENDS.items():
        if available():
            try:
                return grab()
            except ScreenError as error:
                errors.append(f"{name}: {error}")
    raise ScreenError("no screenshot backend worked" + (": " + ". ".join(errors) if errors else ""))


def frontmost_app() -> str:
    """The OS's own answer where it has one. GNOME Wayland has none without a Shell extension."""
    if platform.system() != "Darwin":
        return ""
    try:
        done = _run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application '
                "process whose frontmost is true",
            ],
            timeout=5,
        )
    except ScreenError:
        return ""
    return done.stdout.decode().strip()


# Prepare: shrink for the model, and a 64-bit difference hash to notice "nothing changed".


def prepare(png: bytes, shortest_side: int = 768) -> tuple[bytes, int]:
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    scale = shortest_side / min(image.size)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    small = image.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = small.tobytes()  # one byte per pixel in mode L
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return out.getvalue(), bits


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# Describe, through an OpenAI-compatible chat endpoint from the companion's providers.


@dataclass
class Endpoint:
    url: str
    model: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60


def endpoint(screen: dict[str, Any], providers: Any, session: str = "ambient-screen") -> Endpoint:
    from dataclasses import replace

    from dusha.providers import Provider

    model = screen.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("ambient option 'screen.model' is required to describe screens")
    name = screen.get("provider")
    try:
        profile = providers.get(name) if name else Provider(base_url="", timeout_seconds=60)
    except KeyError as error:
        raise ValueError(f"ambient option 'screen.provider': {error.args[0]}") from error
    target = replace(
        profile,
        base_url=str(screen.get("base_url") or profile.url("chat")).rstrip("/"),
        endpoints={},
        key=str(screen.get("key", profile.key) or ""),
        auth=str(screen.get("auth", profile.auth)),
        headers={**profile.headers, **(screen.get("headers") or {})},
    )
    if not target.base_url:
        raise ValueError("ambient option 'screen' needs a 'provider' or a 'base_url'")
    try:
        # `session` fills {conversation_id}. Some providers (opencode-go) reject an empty one.
        headers = target.request_headers(session)
    except ValueError as error:
        raise ValueError(f"ambient screen provider: {error}") from error
    url = target.base_url
    url = url if url.endswith("/chat/completions") else url + "/chat/completions"
    return Endpoint(url, model, headers, float(screen.get("timeout_seconds", profile.timeout_seconds)))


def parse(text: str) -> dict[str, str]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    parsed: Any = None
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except ValueError:
            parsed = None
    if not isinstance(parsed, dict):
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            raise ScreenError("the vision model answered with nothing")
        parsed = {"activity": lines[0][:200], "content": " ".join(lines[1:3])[:600]}
    result = {key: str(parsed.get(key) or "").strip() for key in FIELDS}
    if result["status"] not in STATUSES:
        result["status"] = ""
    if result["progress"] not in PROGRESS:
        result["progress"] = ""
    return result


class Describer:
    def __init__(
        self,
        target: Endpoint,
        json_mode: bool = True,
        prompt: str = PROMPT,
        max_tokens: int = 1000,
        extra_body: dict[str, Any] | None = None,
    ):
        self.target = target
        self.json_mode = json_mode
        self.prompt = prompt
        self.max_tokens = max_tokens
        # Provider-specific request fields, such as {"reasoning_effort": "none"} for a thinking model.
        self.extra_body = dict(extra_body or {})

    def describe(self, jpeg: bytes, previous: dict[str, Any] | None, app_hint: str = "") -> dict[str, str]:
        context = [
            "Previous capture: " + (json.dumps(previous, ensure_ascii=False) if previous else "none"),
        ]
        if app_hint:
            context.append(f"The OS says the frontmost app is {app_hint}.")
        image = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        body: dict[str, Any] = {
            "model": self.target.model,
            "messages": [
                {"role": "system", "content": self.prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "\n".join(context)},
                        {"type": "image_url", "image_url": {"url": image}},
                    ],
                },
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
            **self.extra_body,
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        response = self._post(body)
        if response.status_code == 400 and self.json_mode:
            # Some servers reject response_format. Ask again without it, and remember.
            self.json_mode = False
            body.pop("response_format")
            response = self._post(body)
        if response.status_code >= 400:
            raise ScreenError(f"vision model answered {response.status_code}: {response.text[:300]}")
        try:
            choice = response.json()["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ScreenError("vision model answered without a chat completion") from error
        if not text and choice.get("finish_reason") == "length":
            raise ScreenError(
                f"the vision model used all {self.max_tokens} tokens before answering, likely on reasoning. "
                "Raise screen.max_tokens, or turn reasoning off through screen.extra_body"
            )
        result = parse(text if isinstance(text, str) else json.dumps(text))
        if app_hint:
            result["app"] = app_hint
        return result

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return httpx.post(
                self.target.url, json=body, headers=self.target.headers, timeout=self.target.timeout_seconds
            )
        except httpx.RequestError as error:
            raise ScreenError(f"cannot reach the vision model at {self.target.url}: {error}") from error


def headline(description: dict[str, Any]) -> str:
    app = description.get("app") or "screen"
    activity = description.get("activity") or description.get("title") or "something"
    status = f" ({description['status']})" if description.get("status") else ""
    return f"{app} — {activity}{status}"


def same(a: dict[str, Any] | None, b: dict[str, Any]) -> bool:
    keys = ("app", "title", "content", "activity", "status")
    return a is not None and all((a.get(key) or "") == (b.get(key) or "") for key in keys)
