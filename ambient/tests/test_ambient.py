from __future__ import annotations

import asyncio
import json
import time

import httpx

from conftest import FakeCtx, Server, client, free_port, load_plugin, make_config, wait_for


def _options(**extra):
    return {"device": "nas", "sample_seconds": 3600, **extra}


async def test_events_land_in_context_and_presence_changes_are_recorded(tmp_path, plugins_dir):
    config = make_config(
        tmp_path / "data",
        plugins_dir,
        {
            "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
            "ambient": _options(),
        },
    )
    async with client(config) as http:
        stored = await http.post(
            "/state/v1/ambient",
            json={"source": "shell", "device": "desktop", "text": "`cargo build` failed (exit 101)"},
        )
        assert stored.status_code == 200, stored.text
        await http.post("/state/v1/ambient", json={"source": "git", "text": "gone soon", "ttl_seconds": 0})
        await http.post("/state/v1/presence/heartbeat", json={"device": "macbook", "user": "active"})
        await http.post("/state/v1/presence/heartbeat", json={"device": "macbook", "user": "locked"})

        recent = (await http.get("/state/v1/ambient")).json()["observations"]
        texts = [item["text"] for item in recent]
        assert "macbook: active -> locked" in texts
        assert "`cargo build` failed (exit 101)" in texts
        assert "gone soon" not in texts

        context = (await http.post("/state/v1/context", json={"query": "hi"})).json()
        block = context["context"]["blocks"]["ambient"]
        assert any("desktop shell: `cargo build` failed" in line for line in block["recent"])
        assert "<ambient>" in context["injection"]

        tools = (await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})).json()
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert {"ambient_recent", "ambient_metrics", "presence"} <= names


async def test_context_carries_latest_metrics_and_network_per_device(tmp_path, plugins_dir):
    config = make_config(tmp_path / "data", plugins_dir, {"ambient": _options()})
    now = time.time()
    samples = [
        {"device": "desktop", "name": name, "ts": now - age, "value": value}
        for age, values in ((120, {"cpu.percent": 90.0}), (5, {"cpu.percent": 34.4, "disk.percent:~": 72.0}))
        for name, value in values.items()
    ] + [
        {"device": "macbook", "name": "battery.percent", "ts": now, "value": 88.0},
        {"device": "macbook", "name": "battery.plugged", "ts": now, "value": 0.0},
    ]
    talkers = {"firefox": ["a", "b", "c"], "ssh": ["nas"], "curl": ["x", "y"], "git": ["github.com"]}
    windows = [
        {"text": "older window", "data": {"sites": {"old.example": 50}}, "created_at": now - 600},
        {
            "text": "latest window",
            "data": {"sites": {"github.com": 40, "a.io": 9, "b.io": 5, "c.io": 1}, "talkers": talkers},
        },
    ]
    async with client(config) as http:
        observations = [
            {"source": "network", "kind": "network", "device": "desktop", **row} for row in windows
        ]
        stored = await http.post(
            "/state/v1/ambient/ingest", json={"observations": observations, "samples": samples}
        )
        assert stored.status_code == 200, stored.text
        block = (await http.post("/state/v1/context", json={"query": "hi"})).json()["context"]["blocks"][
            "ambient"
        ]
    assert block["metrics_now"] == ["desktop: cpu 34%, disk ~ 72%", "macbook: battery 88% unplugged"]
    [network] = block["network_now"]
    assert network.endswith(
        "desktop: sites github.com (40), a.io (9), b.io (5)"
        " | talking firefox (3 hosts), curl (2 hosts), git (1 host)"
    )


async def test_device_forwards_samples_and_events_to_the_hub(tmp_path, plugins_dir):
    hub_config = make_config(tmp_path / "hub", plugins_dir, {"ambient": _options()})
    with Server(hub_config, free_port()) as hub:
        device_config = make_config(
            tmp_path / "mac",
            plugins_dir,
            {"ambient": {"device": "macbook", "sample_seconds": 1, "forward_to": {"url": hub.url}}},
        )
        async with client(device_config) as http:
            await http.post("/state/v1/ambient", json={"source": "git", "text": "committed `fix: typo`"})

            def hub_has_both():
                metrics = httpx.get(
                    hub.url + "/state/v1/ambient/metrics", params={"device": "macbook"}
                ).json()
                events = httpx.get(
                    hub.url + "/state/v1/ambient", params={"device": "macbook", "source": "git"}
                ).json()
                return metrics["metrics"].get("macbook") and events["observations"] and (metrics, events)

            metrics, events = await asyncio.to_thread(wait_for, hub_has_both, 10)
        text = httpx.get(hub.url + "/state/v1/ambient/prometheus").text
    assert {"cpu.percent", "mem.percent", "disk.percent:~"} <= set(metrics["metrics"]["macbook"])
    assert events["observations"][0]["text"] == "committed `fix: typo`"
    assert 'dusha_ambient{device="macbook",metric="cpu.percent"}' in text


def test_alerts_fire_once_resolve_and_offer_a_candidate(tmp_path):
    module = load_plugin("ambient")
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    ambient = module.Ambient(ctx, module.Options(device="desktop"))
    now = time.time()
    ambient.check_alerts({"mem.percent": 95.0, "disk.percent:~": 50.0}, now)
    ambient.check_alerts({"mem.percent": 96.0, "disk.percent:~": 50.0}, now + 60)
    [alert] = ambient.recent(kind="alert")
    assert alert["text"].startswith("memory at 95%")
    [candidate] = ambient.candidates()
    assert candidate["reason"] == "ambient_alert" and candidate["requires_presence"] == "active"
    assert [event for event, _ in ctx.emitted if event == "ambient.alert"] == ["ambient.alert"]
    assert "alerts" in ambient.context(None).data

    restarted = module.Ambient(ctx, module.Options(device="desktop"))
    restarted.check_alerts({"mem.percent": 40.0, "disk.percent:~": 50.0}, now + 120)
    assert restarted.recent(kind="alert") == []
    assert restarted.candidates() == []
    assert restarted.recent(kind="resolved")[0]["text"] == "mem back to normal"


def test_old_samples_roll_up_by_hour(tmp_path):
    module = load_plugin("ambient")
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    ambient = module.Ambient(ctx, module.Options(device="nas", raw_hours=24))
    old = time.time() - 30 * 3600
    ambient.store_samples(
        [
            module.Sample(device="nas", name="cpu.percent", ts=old + i, value=v)
            for i, v in enumerate((10.0, 20.0, 60.0))
        ]
    )
    ambient.store_samples([module.Sample(device="nas", name="cpu.percent", ts=time.time(), value=30.0)])
    ambient.maintain(time.time())
    assert len(ambient.store.execute("SELECT * FROM samples")) == 1
    [rollup] = ambient.store.execute("SELECT * FROM rollups")
    assert (rollup["avg"], rollup["min"], rollup["max"], rollup["n"]) == (30.0, 10.0, 60.0, 3)
    summary = ambient.metrics("nas", "cpu.percent", hours=48)["nas"]["cpu.percent"]
    assert summary == {**summary, "last": 30.0, "min": 10.0, "max": 60.0, "n": 4, "avg": 30.0}
    assert ambient.metrics("nas", "cpu.percent", hours=1)["nas"]["cpu.percent"]["n"] == 1


async def test_secrets_are_redacted_before_storing_and_forwarding(tmp_path, plugins_dir):
    hub_config = make_config(tmp_path / "hub", plugins_dir, {"ambient": _options()})
    with Server(hub_config, free_port()) as hub:
        device_config = make_config(
            tmp_path / "mac",
            plugins_dir,
            {
                "ambient": {
                    "device": "macbook",
                    "sample_seconds": 1,
                    "forward_to": {"url": hub.url},
                    "redact": {"patterns": [{"name": "codename", "pattern": "project-[A-Z]+"}]},
                }
            },
        )
        async with client(device_config) as http:
            token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
            await http.post(
                "/state/v1/ambient",
                json={
                    "source": "shell",
                    "text": f"`git push https://somme:{token}@github.com` failed for project-ORCA",
                    "data": {"command": f"export GITHUB_TOKEN={token}"},
                },
            )
            local = (await http.get("/state/v1/ambient", params={"source": "shell"})).json()["observations"]

            def forwarded():
                rows = httpx.get(hub.url + "/state/v1/ambient", params={"source": "shell"}).json()
                return rows["observations"]

            [remote] = await asyncio.to_thread(wait_for, forwarded, 10)
    for row in (local[0], remote):
        assert token not in json.dumps(row)
        assert row["text"] == (
            "`git push https://somme:[redacted:github-token]@github.com` failed for [redacted:codename]"
        )
        assert row["data"]["command"] == "export GITHUB_TOKEN=[redacted:github-token]"
        assert set(row["data"]["redacted"]) == {"codename", "github-token"}


def test_redactor_keeps_useful_context():
    load_plugin("ambient")
    from ambient_under_test.redact import Redactor

    redactor = Redactor()
    for kept in (
        "committed 3f2a9c1d8e7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f on main",
        "uuid 123e4567-e89b-12d3-a456-426614174000",
        "editing test_coming_back_after_a_long_absence_offers_a_welcome in presence.py",
        "author: Somme4096, max_tokens=4096",
        "/var/home/somme/src/dusha-plugin-collections/ambient",
    ):
        assert redactor.text(kept) == (kept, [])
    assert redactor.text("blob kJ8sD9fL2mN4pQ7rT1vX3zB6cH5gW0 end") == (
        "blob [redacted:high-entropy] end",
        ["high-entropy"],
    )
    assert redactor.text("Authorization: Bearer abcDEF123456ghiJKL789")[0] == (
        "Authorization: Bearer [redacted:bearer]"
    )
