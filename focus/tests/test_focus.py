from __future__ import annotations

import httpx

from conftest import Server, free_port, make_config


def test_focus_reads_screen_rows_and_moves_the_mood(tmp_path, plugins_dir):
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "ambient": {"device": "desk", "sample_seconds": 3600},
            "focus": {
                "deep_minutes": 45,
                "scattered_switches": 3,
                "deltas": {"scattered": {"worry": 0.2}},
            },
        },
    )
    with Server(config, free_port()) as hub:
        assert httpx.get(hub.url + "/state/v1/focus").json()["state"] == "unknown"
        for app in ("zed", "zen", "zed", "zen", "zen"):
            row = {"source": "screen", "kind": "screen", "text": app, "data": {"app": app}}
            httpx.post(hub.url + "/state/v1/ambient", json=row)
        state = httpx.get(hub.url + "/state/v1/focus").json()
        assert state["app"] == "zen" and state["switches"] == 3 and state["state"] == "scattered"
        context = httpx.post(hub.url + "/state/v1/context", json={"query": "x"}).text
        assert "scattered" in context

        status = httpx.get(hub.url + "/state/v1/plugins").json()["focus"]
        assert status["state"] == "active" and "decide" in status["events"]
