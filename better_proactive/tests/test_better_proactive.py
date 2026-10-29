from __future__ import annotations

import asyncio

from conftest import client, make_config, wait_for

ALWAYS = {"quiet_start_hour": 0, "quiet_end_hour": 0}


def _stack(fake_desktop, **proactive) -> dict:
    command, _ = fake_desktop
    return {
        "proactive": False,
        "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30, "welcome_after_minutes": 0},
        "notification": {"device": "nas", "desktop_command": command},
        "relay": {},
        "ambient": {"device": "nas", "sample_seconds": 3600},
        "better_proactive": {**ALWAYS, "min_gap_minutes": 0, **proactive},
    }


async def _say(http, text: str = "hi") -> None:
    response = await http.post(
        "/state/v1/messages",
        json={
            "harness": "discord",
            "conversation_id": "dm",
            "role": "user",
            "content": text,
            "route": "discord-dm",
        },
    )
    assert response.status_code == 200, response.text


async def _beat(http, user: str, device: str = "nas") -> None:
    await http.post(
        "/state/v1/presence/heartbeat",
        json={"device": device, "user": user, "idle_seconds": 0, "capabilities": ["notify"]},
    )


async def test_coming_back_is_delivered_through_the_old_queue_and_mirrored(
    tmp_path, plugins_dir, fake_desktop
):
    config = make_config(tmp_path / "data", plugins_dir, _stack(fake_desktop))
    async with client(config) as http:
        plugins = (await http.get("/state/v1/plugins")).json()
        assert plugins["better_proactive"]["state"] == "active"
        assert plugins["better_proactive"]["provides"] == ["proactive"]
        assert plugins["proactive"]["state"] == "disabled"

        await _say(http)
        await _beat(http, "idle")
        await _beat(http, "active")
        # The scheduler's own evaluate pass may queue it first, either way exactly one event is waiting.
        await http.post("/state/v1/proactive/evaluate")
        assert (await http.post("/state/v1/proactive/evaluate")).json()["event"] is None
        [event] = (await http.get("/state/v1/proactive/events", params={"consumer": "bot"})).json()["events"]
        assert event["reason"] == "came_back" and event["source"] == "presence"
        assert "Why you're reaching out now: She just came back to the nas" in event["generation_instruction"]
        assert event["target"] == {"harness": "discord", "conversation_id": "dm", "route": "discord-dm"}
        ack = await http.post(
            f"/state/v1/proactive/events/{event['id']}/ack",
            json={"consumer": "bot", "outcome": "sent", "text": "welcome back~"},
        )
        assert ack.status_code == 200, ack.text

        # relay mirrors the sent text to the device she's using, here this machine.
        await asyncio.to_thread(wait_for, lambda: fake_desktop[1].exists() and fake_desktop[1].read_text())
        assert fake_desktop[1].read_text() == "dusha|welcome back~\n"
        context = (
            await http.post(
                "/state/v1/context", json={"query": "welcome", "harness": "discord", "conversation_id": "dm"}
            )
        ).json()
    assert "welcome back~" in context["injection"]


async def test_plan_explains_skips_and_alerts_wait_for_her(tmp_path, plugins_dir, fake_desktop):
    config = make_config(tmp_path / "data", plugins_dir, _stack(fake_desktop, min_gap_minutes=30))
    async with client(config) as http:
        await _say(http)
        await http.post(
            "/state/v1/ambient",
            json={"source": "ambient", "kind": "alert", "device": "desktop", "text": "disk ~ at 95%"},
        )
        plan = (await http.post("/cli/v1/run/proactive/plan", json={})).json()
        [candidate] = plan["candidates"]
        assert candidate["reason"] == "ambient_alert"
        assert candidate["skipped"] == "she isn't at any device"
        assert plan["chosen"] is None

        await _beat(http, "active", device="desktop")
        plan = (await http.post("/cli/v1/run/proactive/plan", json={})).json()
        alert = next(item for item in plan["candidates"] if item["reason"] == "ambient_alert")
        assert alert["skipped"] == "she talked just now"


async def test_silence_still_works_like_the_builtin(tmp_path, plugins_dir, fake_desktop):
    stack = _stack(fake_desktop, min_silence_minutes=0, thresholds={"longing": 0.0})
    config = make_config(tmp_path / "data", plugins_dir, stack)
    async with client(config) as http:
        await _say(http)
        event = (await http.post("/state/v1/proactive/evaluate")).json()["event"]
    assert event["source"] == "silence"
    assert event["reason"] in {"silence", "fear"}
    assert "Why you're reaching out now" not in event["generation_instruction"]


async def test_refuses_to_run_next_to_the_builtin(tmp_path, plugins_dir, fake_desktop):
    stack = _stack(fake_desktop)
    del stack["proactive"]
    config = make_config(tmp_path / "data", plugins_dir, stack)
    async with client(config) as http:
        plugins = (await http.get("/state/v1/plugins")).json()
        evaluated = await http.post("/state/v1/proactive/evaluate")
    assert plugins["better_proactive"]["state"] == "failed"
    assert "disabled" in plugins["better_proactive"]["error"]
    assert plugins["proactive"]["state"] == "active"
    assert evaluated.status_code == 200


def _screen_row(status: str, activity: str, notable: str = "") -> dict:
    data = {
        "app": "VS Code",
        "title": "presence.py",
        "content": f"{activity}.",
        "activity": activity,
        "status": status,
        "progress": "continued",
        "notable": notable,
    }
    text = f"VS Code — {activity} ({status})"
    return {"source": "screen", "kind": "screen", "device": "nas", "text": text, "data": data}


async def _setup_screen(http) -> None:
    await _say(http, "brb, fixing a test")
    await _beat(http, "active")
    await http.post("/state/v1/ambient", json=_screen_row("focused", "writing presence tests"))
    await http.post("/state/v1/ambient", json=_screen_row("stuck", "debugging a failing test", "same error"))


def _system_one(speak_for):
    """A stand-in SystemOne server. It answers affect's emotion question too, since system_one asks both."""

    def reply(body):
        questions = body["questions"]
        if "emotion" in questions:
            first = next(iter(questions["emotion"]["criteria"]))
            return {"answers": {"emotion": {"type": "choice", "choice": first}}}
        speak, kind = speak_for(questions["speak"]["criteria"])
        choice = {"speak": {"type": "choice", "choice": speak}, "kind": {"type": "choice", "choice": kind}}
        return {"answers": choice}

    return reply


def _judged(server) -> list[dict]:
    return [item["body"] for item in server.requests if "speak" in item["body"]["questions"]]


async def test_the_judge_picks_a_screen_moment_and_how(tmp_path, plugins_dir, fake_desktop, recorder):
    def stuck(criteria):
        return next(key for key, text in criteria.items() if "(stuck)" in text), "task"

    server = recorder(reply=_system_one(stuck))
    stack = {**_stack(fake_desktop), "system_one": {"base_url": server.url}}
    config = make_config(tmp_path / "data", plugins_dir, stack)
    async with client(config) as http:
        await _setup_screen(http)
        plan = (await http.post("/cli/v1/run/proactive/plan", json={})).json()
        assert plan["judge"] == {"asked": False, "note": "the judge is asked on the next evaluation"}
        assert _judged(server) == []  # polling the plan never costs a judge call

        await http.post("/state/v1/proactive/evaluate")
        [event] = (await http.get("/state/v1/proactive/events", params={"consumer": "bot"})).json()["events"]
    assert event["reason"] == "screen_stuck" and event["kind"] == "task" and event["judged"]
    assert "Before you write, do this with your tools" in event["generation_instruction"]
    [request] = _judged(server)
    assert request["questions"]["kind"]["criteria"] == {
        "talk": "Just say something",
        "task": "Look into it with tools first",
    }
    assert request["questions"]["speak"]["criteria"]["none"].startswith("Stay quiet")
    assert any("VS Code — debugging a failing test (stuck)" in line for line in request["state"]["timeline"])
    assert request["state"]["recent_messages"] == ["user: brb, fixing a test"]
    assert request["state"]["active_device"] == "nas"


async def test_none_is_remembered_until_something_changes(tmp_path, plugins_dir, fake_desktop, recorder):
    server = recorder(reply=_system_one(lambda criteria: ("none", "talk")))
    stack = {**_stack(fake_desktop), "system_one": {"base_url": server.url}}
    config = make_config(tmp_path / "data", plugins_dir, stack)
    async with client(config) as http:
        await _setup_screen(http)
        for _ in range(3):
            assert (await http.post("/state/v1/proactive/evaluate")).json()["event"] is None
        asked = len(_judged(server))
        await http.post("/state/v1/ambient", json=_screen_row("waiting", "waiting for cargo build"))
        await http.post("/state/v1/proactive/evaluate")
        plan = (await http.post("/cli/v1/run/proactive/plan", json={})).json()
    assert asked == 1
    assert len(_judged(server)) == 2  # a new moment, so the judge is asked again
    assert plan["judge"]["speak"] == "none" and plan["chosen"] is None


async def test_without_a_judge_screen_moments_are_never_raised(tmp_path, plugins_dir, fake_desktop):
    config = make_config(tmp_path / "data", plugins_dir, _stack(fake_desktop))
    async with client(config) as http:
        await _setup_screen(http)
        assert (await http.post("/state/v1/proactive/evaluate")).json()["event"] is None
        plan = (await http.post("/cli/v1/run/proactive/plan", json={})).json()
    screens = [item for item in plan["candidates"] if item["reason"].startswith("screen_")]
    assert len(screens) == 2
    assert plan["chosen"] is None


async def test_a_turn_with_the_claim_marker_writes_the_message_without_counting_as_her(
    tmp_path, plugins_dir, fake_desktop
):
    stack = _stack(fake_desktop, min_silence_minutes=0, thresholds={"longing": 0.0})
    config = make_config(tmp_path / "data", plugins_dir, stack)
    async with client(config) as http:
        await _say(http)
        await http.post("/state/v1/proactive/evaluate")
        [event] = (await http.get("/state/v1/proactive/events", params={"consumer": "bot"})).json()["events"]
        before = (await http.get("/state/v1/affect")).json()
        assert event["claim_marker"] == f'<dusha_proactive event="{event["id"]}"/>'

        turn = f"{event['claim_marker']}\n{event['generation_instruction']}"
        stored = await http.post(
            "/state/v1/messages",
            json={"harness": "discord", "conversation_id": "dm", "role": "user", "content": turn},
        )
        assert stored.json().get("affect") is None
        await http.post(
            "/state/v1/messages",
            json={"harness": "discord", "conversation_id": "dm", "role": "assistant", "content": "miss you"},
        )
        ack = await http.post(
            f"/state/v1/proactive/events/{event['id']}/ack",
            json={"consumer": "bot", "outcome": "sent", "text": "miss you"},
        )
        assert ack.status_code == 200, ack.text
        after = (await http.get("/state/v1/affect")).json()
        recent = (
            await http.post(
                "/state/v1/context", json={"query": "miss", "harness": "discord", "conversation_id": "dm"}
            )
        ).json()["records"]
    assert after["last_user_message_at"] == before["last_user_message_at"]
    assert after["unanswered_proactive"] == 1
    texts = [item["text"] for item in recent if item.get("text") == "miss you"]
    assert len(texts) == 1


async def test_a_marker_for_an_event_nobody_leased_is_her_own_message(tmp_path, plugins_dir, fake_desktop):
    config = make_config(tmp_path / "data", plugins_dir, _stack(fake_desktop))
    async with client(config) as http:
        await _say(http, '<dusha_proactive event="00000000-0000-0000-0000-000000000000"/> hi')
        affect = (await http.get("/state/v1/affect")).json()
    assert affect["last_user_message_at"] is not None
