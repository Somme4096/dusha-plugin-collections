from __future__ import annotations

import json
import sys

import httpx

from conftest import Server, free_port, make_config, wait_for

AGENT = """
import json, sys, time
def send(**message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)
def update(session, **body):
    send(method="session/update", params={"sessionId": session, "update": body})
for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if method == "initialize":
        send(id=ident, result={"protocolVersion": 1, "agentCapabilities": {}})
    elif method == "session/new":
        print(message["params"]["cwd"], file=sys.stderr, flush=True)
        send(id=ident, result={"sessionId": "s1"})
    elif method == "session/prompt":
        text = message["params"]["prompt"][0]["text"]
        if text == "hang":
            time.sleep(60)
        update("s1", sessionUpdate="tool_call", toolCallId="t1", title="write notes.txt")
        send(id=99, method="session/request_permission", params={"options": [
            {"optionId": "no", "kind": "reject_once"}, {"optionId": "yes", "kind": "allow_always"}]})
        send(id=98, method="fs/read_text_file", params={"path": "/etc/hostname"})
        replies = [json.loads(sys.stdin.readline()) for _ in range(2)]
        chosen = next(r["result"]["outcome"]["optionId"] for r in replies if r["id"] == 99)
        refused = next(r["error"]["code"] for r in replies if r["id"] == 98)
        for chunk in (f"did {text}, ", f"{chosen} {refused}"):
            update("s1", sessionUpdate="agent_message_chunk", content={"type": "text", "text": chunk})
        send(id=ident, result={"stopReason": "end_turn"})
"""


def mcp(url: str, tool: str, **arguments) -> tuple[bool, dict]:
    response = httpx.post(
        url + "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        timeout=30,
    )
    result = response.json()["result"]
    return bool(result.get("isError")), json.loads(result["content"][0]["text"])


def devices(url: str) -> dict:
    return {item["device"]: item for item in httpx.get(url + "/state/v1/presence").json()["devices"]}


def test_spawn_runs_an_agent_on_a_device_and_answers_in_one_call(tmp_path, plugins_dir):
    script = tmp_path / "agent.py"
    script.write_text(AGENT)
    hub_config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "relay": {"token": "device-secret", "spawn_timeout_seconds": 1},
            "ambient": {"device": "nas"},
        },
    )
    with Server(hub_config, free_port()) as hub:
        port = free_port()
        device_config = make_config(
            tmp_path / "desktop",
            plugins_dir,
            {
                "presence": {
                    "device": "desktop",
                    "sensor": "none",
                    "interval_seconds": 30,
                    "report_to": {"url": hub.url},
                    "url": f"http://127.0.0.1:{port}",
                },
                "ambient": {"device": "desktop", "forward_to": {"url": hub.url}, "sample_seconds": 1},
                "spawn": {
                    "agents": {
                        "fake": {"command": [sys.executable, str(script)]},
                        "missing": {"command": ["dusha-test-no-such-agent"]},
                    }
                },
            },
            api_token="device-secret",
        )
        with Server(device_config, port):
            wait_for(lambda: devices(hub.url).get("desktop"))
            assert devices(hub.url)["desktop"]["capabilities"] == ["spawn"]

            error, result = mcp(hub.url, "spawn", prompt="the chores", device="desktop", cwd=str(tmp_path))
            assert not error, result
            assert result["device"] == "desktop"
            assert result["agent"] == "fake"
            assert result["stop_reason"] == "end_turn"
            assert result["answer"] == "did the chores, yes -32601"
            assert result["tools"] == ["write notes.txt"]
            assert result["cwd"] == str(tmp_path)

            # A run past its limit is cancelled and still answers.
            error, result = mcp(hub.url, "spawn", prompt="hang", device="desktop")
            assert not error, result
            assert result["stop_reason"] == "timeout"

            error, result = mcp(hub.url, "spawn", prompt="x", device="desktop", agent="missing")
            assert error and "is not installed here" in result["detail"]
            error, result = mcp(hub.url, "spawn", prompt="x", device="desktop", cwd="/dusha-test-nowhere")
            assert error and "no such folder" in result["detail"]

            # The run shows up in ambient, forwarded to the hub like any other row.
            rows = wait_for(
                lambda: httpx.get(hub.url + "/state/v1/ambient", params={"source": "spawn"}).json()[
                    "observations"
                ]
            )
            assert any("the chores" in row["text"] and row["device"] == "desktop" for row in rows)


def test_spawn_is_not_offered_without_an_installed_agent(tmp_path, plugins_dir):
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "relay": {},
            "spawn": {"agents": {"missing": {"command": ["dusha-test-no-such-agent"]}}},
        },
    )
    with Server(config, free_port()) as hub:
        wait_for(lambda: devices(hub.url).get("nas"))
        error, result = mcp(hub.url, "spawn", prompt="x", device="nas")
    assert error and result["detail"] == "nas can't spawn, it offers: nothing"
