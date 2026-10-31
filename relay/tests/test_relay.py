from __future__ import annotations

import json

import httpx

from conftest import Server, free_port, make_config, wait_for


def mcp(url: str, tool: str, **arguments) -> tuple[bool, dict]:
    response = httpx.post(
        url + "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    result = response.json()["result"]
    return bool(result.get("isError")), json.loads(result["content"][0]["text"])


def devices(url: str) -> dict:
    return {item["device"]: item for item in httpx.get(url + "/state/v1/presence").json()["devices"]}


def test_notify_and_look_through_the_hub(tmp_path, plugins_dir, fake_desktop, recorder):
    ntfy = recorder()
    command, desktop_log = fake_desktop
    hub_config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "notification": {"device": "nas", "desktop": False, "ntfy": {"topic": "t", "server": ntfy.url}},
            "relay": {"token": "device-secret"},
        },
    )
    with Server(hub_config, free_port()) as hub:
        port = free_port()
        device_config = make_config(
            tmp_path / "mac",
            plugins_dir,
            {
                "presence": {
                    "device": "macbook",
                    "sensor": "none",
                    "interval_seconds": 30,
                    "report_to": {"url": hub.url},
                    "url": f"http://127.0.0.1:{port}",
                },
                "notification": {"device": "macbook", "desktop_command": command},
            },
            api_token="device-secret",
        )
        with Server(device_config, port):
            wait_for(lambda: devices(hub.url).get("macbook"))
            assert devices(hub.url)["macbook"]["capabilities"] == ["notify"]

            # Nobody active anywhere: auto goes to the phone through the hub's own notification plugin.
            error, result = mcp(hub.url, "notify", title="away", body="you there?")
            assert not error, result
            assert result["delivered"] == ["phone"]
            assert ntfy.requests[-1]["body"]["title"] == "away"

            # They sit down at the MacBook.
            beat = {
                **{k: devices(hub.url)["macbook"][k] for k in ("url", "capabilities")},
                "device": "macbook",
                "user": "active",
                "idle_seconds": 1,
            }
            httpx.post(hub.url + "/state/v1/presence/heartbeat", json=beat)
            error, result = mcp(hub.url, "notify", title="here", body="hi!")
            assert not error, result
            assert result == {"device": "macbook", "delivered": ["desktop"]}
            assert desktop_log.read_text() == "here|hi!\n"

            error, result = mcp(hub.url, "look", device="macbook")
            assert error and result["detail"] == "macbook can't look, it offers: notify"
            error, result = mcp(hub.url, "notify", title="x", device="toaster")
            assert error and "unknown device 'toaster'" in result["detail"]

        wait_for(lambda: devices(hub.url)["macbook"]["status"] == "off")
        error, result = mcp(hub.url, "notify", title="gone", device="macbook")
        assert error and result["detail"].startswith("macbook is off since ")


def test_relay_without_presence_says_so(tmp_path, plugins_dir):
    config = make_config(tmp_path / "hub", plugins_dir, {"relay": {}})
    with Server(config, free_port()) as hub:
        error, result = mcp(hub.url, "notify", title="x")
    assert error and result["detail"] == "relay needs the presence plugin to know which devices exist"


def test_device_without_channels_offers_nothing_and_notify_says_so(tmp_path, plugins_dir):
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "notification": {"device": "nas", "desktop": False},
            "relay": {},
        },
    )
    with Server(config, free_port()) as hub:
        wait_for(lambda: devices(hub.url).get("nas"))
        assert devices(hub.url)["nas"]["capabilities"] == []
        error, result = mcp(hub.url, "notify", title="x", device="nas")
    assert error and result["detail"] == "nas can't notify, it offers: nothing"
