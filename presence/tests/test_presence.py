from __future__ import annotations

import asyncio
from datetime import timedelta

from dusha.timeutil import utc_now

from conftest import FakeCtx, Server, client, free_port, load_plugin, make_config, wait_for


def _options(**extra):
    return {"device": "nas", "sensor": "none", "interval_seconds": 1, "down_after_seconds": 2, **extra}


def _beat(device: str, user: str = "active", idle: float = 0, said: str = "") -> dict:
    return {
        "device": device,
        "user": user,
        "idle_seconds": idle,
        "url": f"http://{device}:8765",
        "capabilities": ["notify"],
        "said": said,
    }


async def test_registry_tracks_heartbeats_goodbyes_and_silence(tmp_path, plugins_dir):
    config = make_config(tmp_path / "data", plugins_dir, {"presence": _options()})
    async with client(config) as http:
        assert (await http.post("/state/v1/presence/heartbeat", json=_beat("macbook"))).status_code == 200
        assert (
            await http.post("/state/v1/presence/heartbeat", json=_beat("desktop", "idle", 900))
        ).status_code == 200
        snapshot = (await http.get("/state/v1/presence")).json()
        status = {item["device"]: item["status"] for item in snapshot["devices"]}
        assert status == {"nas": "awake", "macbook": "active", "desktop": "idle"}
        assert snapshot["active_device"] == "macbook"
        assert snapshot["local"] == "nas"

        await http.post("/state/v1/presence/heartbeat", json=_beat("macbook", said="sleeping"))
        snapshot = (await http.get("/state/v1/presence")).json()
        status = {item["device"]: item["status"] for item in snapshot["devices"]}
        assert status["macbook"] == "sleeping"
        assert snapshot["active_device"] is None

        await asyncio.sleep(2.5)
        status = {
            item["device"]: item["status"]
            for item in (await http.get("/state/v1/presence")).json()["devices"]
        }
        assert status["desktop"] == "unreachable"
        assert status["macbook"] == "sleeping"
        assert status["nas"] == "awake"

        context = (await http.post("/state/v1/context", json={"query": "hi"})).json()
        assert "<presence>" in context["injection"]
        assert context["context"]["blocks"]["presence"]["devices"]["desktop"].startswith("unreachable since")

        tools = (await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})).json()
        assert "presence" in {tool["name"] for tool in tools["result"]["tools"]}


async def test_device_reports_to_hub_and_says_goodbye_on_shutdown(tmp_path, plugins_dir):
    hub_config = make_config(tmp_path / "hub", plugins_dir, {"presence": _options(device="nas")})
    with Server(hub_config, free_port()) as hub:
        device_options = _options(device="macbook", report_to={"url": hub.url}, url="http://macbook:9999")
        device_config = make_config(tmp_path / "mac", plugins_dir, {"presence": device_options})

        import httpx

        def hub_status():
            snapshot = httpx.get(hub.url + "/state/v1/presence").json()
            return {item["device"]: item for item in snapshot["devices"]}

        async with client(device_config):
            seen = await asyncio.to_thread(wait_for, lambda: hub_status().get("macbook"))
            assert seen["url"] == "http://macbook:9999"
            assert seen["status"] == "awake"
        assert hub_status()["macbook"]["status"] == "off"


def test_coming_back_after_a_long_absence_offers_a_welcome():
    module = load_plugin("presence")
    ctx = FakeCtx()
    options = module.Options(device="nas", url="http://nas:8765", sensor="none", welcome_after_minutes=60)
    presence = module.Presence(ctx, options)
    start = utc_now() - timedelta(hours=3)
    presence.receive(module.Heartbeat(device="macbook", user="active", idle_seconds=0), start)
    presence.receive(
        module.Heartbeat(device="macbook", user="idle", idle_seconds=600), start + timedelta(minutes=10)
    )
    assert presence.candidates() == []
    presence.receive(module.Heartbeat(device="macbook", user="active", idle_seconds=0), utc_now())
    [candidate] = presence.candidates()
    assert candidate["reason"] == "came_back"
    assert "macbook" in candidate["topic"] and "after 3h" in candidate["topic"]
    changes = [args[0] for event, args in ctx.emitted if event == "presence.changed"]
    assert [(item["old"], item["new"]) for item in changes] == [("active", "idle"), ("idle", "active")]
