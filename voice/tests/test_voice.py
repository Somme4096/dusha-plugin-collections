from __future__ import annotations

import base64
import json
import sys

import httpx

from conftest import Server, free_port, make_config, wait_for


def mcp(url: str, tool: str, **arguments) -> tuple[bool, dict | str]:
    message = {"name": tool, "arguments": arguments}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": message}
    result = httpx.post(url + "/mcp", json=body, timeout=30).json()["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        return True, text
    return False, json.loads(text)


def devices(url: str) -> dict:
    return {item["device"]: item for item in httpx.get(url + "/state/v1/presence").json()["devices"]}


def player(tmp_path) -> tuple[list[str], object]:
    log = tmp_path / "played.log"
    script = tmp_path / "play.py"
    script.write_text("import sys\nopen(sys.argv[1], 'ab').write(open(sys.argv[2], 'rb').read() + b'\\n')\n")
    return [sys.executable, str(script), str(log), "{file}"], log


def test_speak_plays_on_the_active_device(tmp_path, plugins_dir, recorder):
    tts = recorder(reply={"audio": 1})
    command, log = player(tmp_path)
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "desk", "sensor": "none", "interval_seconds": 30},
            "relay": {},
            "voice": {
                "base_url": tts.url + "/v1",
                "api_key": "secret",
                "voice": "maid",
                "extra": {"seed": 7},
                "player": command,
            },
        },
    )
    with Server(config, free_port()) as hub:
        wait_for(lambda: devices(hub.url).get("desk"))
        assert devices(hub.url)["desk"]["capabilities"] == ["speak"]
        error, result = mcp(hub.url, "speak", text="👂good night", device="desk")
        assert not error and result["via"] == "presence" and result["device"] == "desk"
        wait_for(lambda: log.exists() and log.read_bytes())
    [request] = tts.requests
    assert request["path"] == "/v1/audio/speech"
    assert request["headers"]["authorization"] == "Bearer secret"
    assert request["body"] == {
        "model": "tts-1",
        "voice": "maid",
        "input": "👂good night",
        "response_format": "wav",
        "seed": 7,
    }
    assert log.read_bytes().strip() == b'{"audio": 1}'


def hub_config(tmp_path, plugins_dir, tts_url: str, command: list[str], **relay):
    return make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "relay": {"speak_route": "discord:42", **relay},
            "voice": {"base_url": tts_url, "player": command},
        },
    )


def test_speak_falls_back_to_a_clip_the_worker_collects(tmp_path, plugins_dir, recorder):
    tts = recorder(reply={"audio": 2})
    command, log = player(tmp_path)
    with Server(hub_config(tmp_path, plugins_dir, tts.url, command), free_port()) as hub:
        error, result = mcp(hub.url, "speak", text="😭it's okay")
        assert not error and result["via"] == "discord" and result["route"] == "discord:42"
        assert not log.exists()
        outbox = hub.url + "/state/v1/voice/outbox"
        assert httpx.get(outbox, params={"prefix": "telegram:"}).json()["clips"] == []
        [clip] = httpx.get(outbox, params={"prefix": "discord:"}).json()["clips"]
        assert clip["route"] == "discord:42" and clip["format"] == "wav" and clip["text"] == "😭it's okay"
        assert base64.b64decode(clip["data"]) == b'{"audio": 2}'
        httpx.delete(f"{hub.url}/state/v1/voice/outbox/{clip['id']}").raise_for_status()
        assert httpx.get(hub.url + "/state/v1/voice/outbox").json()["clips"] == []


def test_speak_order_is_configurable_and_failures_are_reported(tmp_path, plugins_dir, recorder):
    tts = recorder(reply=(500, {"error": "busy"}))
    command, _ = player(tmp_path)
    config = hub_config(tmp_path, plugins_dir, tts.url, command, speak_order=["discord"])
    with Server(config, free_port()) as hub:
        error, text = mcp(hub.url, "speak", text="hello")
        assert error and "discord: the TTS server answered 500" in text
