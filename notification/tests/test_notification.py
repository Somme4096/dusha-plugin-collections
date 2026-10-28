from __future__ import annotations

import asyncio

from conftest import client, make_config


def _plugins(fake_desktop, ntfy, presence: bool) -> dict:
    command, _ = fake_desktop
    plugins = {}
    if presence:
        plugins["presence"] = {"device": "nas", "sensor": "none", "interval_seconds": 30}
    plugins["notification"] = {
        "device": "nas",
        "desktop_command": command,
        "ntfy": {"topic": "dusha-test", "server": ntfy.url, "token": "tk"},
    }
    return plugins


async def test_without_presence_auto_uses_every_channel(tmp_path, plugins_dir, fake_desktop, recorder):
    ntfy = recorder()
    config = make_config(tmp_path / "data", plugins_dir, _plugins(fake_desktop, ntfy, presence=False))
    async with client(config) as http:
        response = await http.post(
            "/state/v1/notification", json={"title": "Привет", "body": "build is green"}
        )
    assert response.status_code == 200, response.text
    assert response.json() == {"device": "nas", "delivered": ["desktop", "phone"]}
    assert fake_desktop[1].read_text() == "Привет|build is green\n"
    [sent] = ntfy.requests
    assert sent["body"] == {
        "topic": "dusha-test",
        "title": "Привет",
        "message": "build is green",
        "priority": 3,
    }
    assert sent["headers"]["authorization"] == "Bearer tk"


async def test_presence_picks_desktop_when_active_and_phone_when_away(
    tmp_path, plugins_dir, fake_desktop, recorder
):
    ntfy = recorder()
    config = make_config(tmp_path / "data", plugins_dir, _plugins(fake_desktop, ntfy, presence=True))
    async with client(config) as http:
        presence = (await http.get("/state/v1/presence")).json()
        while not presence["devices"]:
            await asyncio.sleep(0.05)
            presence = (await http.get("/state/v1/presence")).json()
        away = await http.post("/state/v1/notification", json={"title": "one", "urgency": "critical"})
        await http.post(
            "/state/v1/presence/heartbeat", json={"device": "nas", "user": "active", "idle_seconds": 3}
        )
        here = await http.post("/state/v1/notification", json={"title": "two"})
        forced = await http.post("/state/v1/notification", json={"title": "three", "channel": "phone"})
        bad = await http.post("/state/v1/notification", json={"title": "four", "urgency": "loud"})
    assert away.json()["delivered"] == ["phone"]
    assert here.json()["delivered"] == ["desktop"]
    assert forced.json()["delivered"] == ["phone"]
    assert bad.status_code == 422
    assert [item["body"]["title"] for item in ntfy.requests] == ["one", "three"]
    assert ntfy.requests[0]["body"]["priority"] == 5
    assert fake_desktop[1].read_text() == "two|\n"
    assert presence["devices"][0]["capabilities"] == ["notify"]


async def test_cli_command_sends(tmp_path, plugins_dir, fake_desktop, recorder):
    ntfy = recorder()
    config = make_config(tmp_path / "data", plugins_dir, _plugins(fake_desktop, ntfy, presence=False))
    async with client(config) as http:
        response = await http.post(
            "/cli/v1/run/notification/send", json={"title": "hi", "channel": "desktop"}
        )
    assert response.status_code == 200, response.text
    assert fake_desktop[1].read_text() == "hi|\n"
    assert ntfy.requests == []
