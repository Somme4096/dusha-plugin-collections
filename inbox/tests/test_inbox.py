from __future__ import annotations

from conftest import client, make_config

ALWAYS = {"quiet_start_hour": 0, "quiet_end_hour": 0}


async def test_what_the_gate_stopped_waits_in_the_inbox(tmp_path, plugins_dir, fake_desktop):
    config = make_config(
        tmp_path / "data",
        plugins_dir,
        {
            "proactive": False,
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "notification": {"device": "nas", "desktop": False},
            "ambient": {"device": "nas", "sample_seconds": 3600},
            "better_proactive": {**ALWAYS, "min_gap_minutes": 0},
            "inbox": {},
        },
    )
    async with client(config) as http:
        await http.post(
            "/state/v1/messages",
            json={
                "harness": "discord",
                "conversation_id": "dm",
                "role": "user",
                "content": "hi",
                "route": "discord-dm",
            },
        )
        alert = {"source": "disk", "kind": "alert", "text": "disk at 95%", "data": {"alert_key": "disk"}}
        await http.post("/state/v1/ambient", json=alert)
        for _ in range(2):
            await http.post("/state/v1/proactive/evaluate")
        items = (await http.get("/state/v1/inbox")).json()["items"]
        assert len(items) == 1
        assert "disk at 95%" in items[0]["text"]
        assert items[0]["because"] == "they aren't at any device"
        assert items[0]["source"] == "ambient"

        context = (await http.post("/state/v1/context", json={"query": "x"})).text
        assert "unread" in context

        # A notice nothing could deliver lands here too.
        response = await http.post("/state/v1/notification", json={"title": "lost", "channel": "phone"})
        assert response.json()["delivered"] == []
        items = (await http.get("/state/v1/inbox")).json()["items"]
        assert items[0]["text"] == "lost" and items[0]["source"] == "notification"
