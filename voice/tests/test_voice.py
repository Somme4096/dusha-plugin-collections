from __future__ import annotations

import json
import sys

import httpx

from conftest import Server, free_port, make_config, wait_for


def mcp(url: str, tool: str, **arguments) -> tuple[bool, dict]:
    message = {"name": tool, "arguments": arguments}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": message}
    result = httpx.post(url + "/mcp", json=body, timeout=30).json()["result"]
    return bool(result.get("isError")), json.loads(result["content"][0]["text"])


def devices(url: str) -> dict:
    return {item["device"]: item for item in httpx.get(url + "/state/v1/presence").json()["devices"]}


def test_speak_on_a_device_with_a_mood(tmp_path, plugins_dir):
    log = tmp_path / "spoken.log"
    script = tmp_path / "say.py"
    script.write_text("import sys\nopen(sys.argv[1], 'a').write(sys.argv[3] + '@' + sys.argv[2] + '\\n')\n")
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "desk", "sensor": "none", "interval_seconds": 30},
            "relay": {},
            "voice": {
                "command": [sys.executable, str(script), str(log), "{speed}", "{text}"],
                "moods": {"lonely": 0.8},
            },
        },
    )
    with Server(config, free_port()) as hub:
        wait_for(lambda: devices(hub.url).get("desk"))
        assert devices(hub.url)["desk"]["capabilities"] == ["speak"]
        error, result = mcp(hub.url, "speak", text="good night", device="desk", mood="lonely")
        assert not error and result["engine"] == "command" and result["speed"] == 0.8
        error, result = mcp(hub.url, "speak", text="hello", device="desk")
        assert not error and result["speed"] == 1.0
        wait_for(lambda: log.exists() and len(log.read_text().splitlines()) == 2)
        assert sorted(log.read_text().splitlines()) == ["good night@0.8", "hello@1.0"]
