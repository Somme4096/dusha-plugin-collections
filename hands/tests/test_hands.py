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


def test_hub_uses_hands_on_a_device(tmp_path, plugins_dir):
    log = tmp_path / "input.log"
    script = tmp_path / "input.py"
    script.write_text("import sys\nopen(sys.argv[1], 'a').write(' '.join(sys.argv[2:]) + '\\n')\n")
    tool = [sys.executable, str(script), str(log)]
    hub_config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {"presence": {"device": "nas", "sensor": "none", "interval_seconds": 30}, "relay": {"token": "s"}},
    )
    with Server(hub_config, free_port()) as hub:
        port = free_port()
        device_config = make_config(
            tmp_path / "desk",
            plugins_dir,
            {
                "presence": {
                    "device": "desk",
                    "sensor": "none",
                    "interval_seconds": 30,
                    "report_to": {"url": hub.url},
                    "url": f"http://127.0.0.1:{port}",
                },
                "hands": {
                    "type_command": [*tool, "type", "{text}"],
                    "key_command": [*tool, "key", "{text}"],
                    "windows_command": [sys.executable, "-c", "print('1 editor\\n2 browser')"],
                },
            },
            api_token="s",
        )
        with Server(device_config, port):
            wait_for(lambda: devices(hub.url).get("desk"))
            assert devices(hub.url)["desk"]["capabilities"] == ["key", "shell", "type", "windows"]

            error, result = mcp(
                hub.url, "shell", command="pwd; echo oops >&2; exit 3", device="desk", cwd=str(tmp_path)
            )
            assert not error, result
            assert result["exit_code"] == 3
            assert result["stdout"].strip() == str(tmp_path)
            assert result["stderr"].strip() == "oops"

            error, result = mcp(hub.url, "shell", command="sleep 5", device="desk", timeout_seconds=1)
            assert error and "still running after 1s" in result["detail"]

            assert not mcp(hub.url, "type_text", text="hello there", device="desk")[0]
            assert not mcp(hub.url, "press_key", key="Return", device="desk")[0]
            assert log.read_text().splitlines() == ["type hello there", "key Return"]

            error, result = mcp(hub.url, "list_windows", device="desk")
            assert not error and result["windows"] == ["1 editor", "2 browser"]


def test_hands_answer_on_the_hub_itself(tmp_path, plugins_dir):
    log = tmp_path / "key.log"
    script = tmp_path / "key.py"
    script.write_text("import sys\nopen(sys.argv[1], 'a').write(sys.argv[2])\n")
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "relay": {},
            "hands": {"key_command": [sys.executable, str(script), str(log), "{text}"]},
        },
    )
    with Server(config, free_port()) as hub:
        wait_for(lambda: devices(hub.url).get("nas"))
        assert devices(hub.url)["nas"]["capabilities"][:2] == ["key", "shell"]
        error, result = mcp(hub.url, "shell", command="echo local", device="nas")
        assert not error and result["stdout"].strip() == "local"
        error, result = mcp(hub.url, "shell", command="true", device="nas", cwd="/dusha-test-nowhere")
        assert error and "no such folder" in result["detail"]
        assert not mcp(hub.url, "press_key", key="Tab", device="nas")[0]
        assert log.read_text() == "Tab"
