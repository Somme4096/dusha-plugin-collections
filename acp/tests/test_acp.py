from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import httpx

from conftest import Server, free_port, make_config, wait_for

FAKE = Path(__file__).with_name("fake_agent.py")
TOKEN = "device-secret"


def acp(log: Path, **extra) -> dict:
    agent = {"command": [sys.executable, str(FAKE)], "env": {"FAKE_LOG": str(log)}, "mode": "build"}
    return {"agents": {"fake": agent}, **extra}


def hub_config(tmp_path: Path, **extra):
    plugins = {
        "presence": {"device": "nas", "sensor": "none", "interval_seconds": 30},
        "relay": {"token": TOKEN},
        "acp": acp(tmp_path / "nas" / "agent.jsonl", **extra),
    }
    (tmp_path / "nas").mkdir(exist_ok=True)
    return plugins


def mac_config(tmp_path: Path, plugins_dir: Path):
    (tmp_path / "mac").mkdir(exist_ok=True)
    plugins = {
        "presence": {"device": "macbook", "sensor": "none", "interval_seconds": 30},
        "acp": acp(tmp_path / "mac" / "agent.jsonl"),
    }
    return make_config(tmp_path / "mac-home", plugins_dir, plugins, api_token=TOKEN)


def records(log: Path, kind: str) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    return [row for row in rows if row["kind"] == kind]


def turn(url: str, text: str, conversation: str = "c1") -> list[dict]:
    body = {
        "conversation_id": conversation,
        "route": "discord:1",
        "harness": "discord",
        "prompt": [{"type": "text", "text": text}],
    }
    with httpx.stream("POST", url + "/state/v1/acp/turn", json=body, timeout=30) as response:
        assert response.status_code == 200, response.read()
        return [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]


def call(url: str, operation: str, **body) -> httpx.Response:
    return httpx.post(url + f"/state/v1/acp/{operation}", json={"conversation_id": "c1", **body}, timeout=30)


def beat(hub: str, url: str, said: str = "") -> None:
    heartbeat = {
        "device": "macbook",
        "user": "active",
        "idle_seconds": 0,
        "url": url,
        "capabilities": ["acp", "spawn"],
        "said": said,
    }
    httpx.post(hub + "/state/v1/presence/heartbeat", json=heartbeat).raise_for_status()


def test_turns_follow_the_device_after_it_has_been_active_long_enough(tmp_path, plugins_dir):
    nas, mac = tmp_path / "nas" / "agent.jsonl", tmp_path / "mac" / "agent.jsonl"
    config = make_config(
        tmp_path / "hub", plugins_dir, hub_config(tmp_path, switch_after_minutes=1 / 60, check_seconds=3600)
    )
    with Server(config, free_port()) as hub:
        events = turn(hub.url, "use a tool")
        assert [event["event"] for event in events] == ["start", "chunk", "tool", "chunk", "done"]
        assert {event["device"] for event in events} == {"nas"}
        assert events[-1]["text"] == "found it"
        [first] = records(nas, "prompt")
        assert first["env"] == {
            "DUSHA_CONVERSATION_ID": "c1",
            "DUSHA_ROUTE": "discord:1",
            "DUSHA_HARNESS": "discord",
        }

        port = free_port()
        with Server(mac_config(tmp_path, plugins_dir), port):
            beat(hub.url, f"http://127.0.0.1:{port}")
            assert turn(hub.url, "just arrived")[-1]["device"] == "nas"
            time.sleep(1.1)
            events = turn(hub.url, "settled in")
            done = events[-1]
            assert (done["event"], done["device"], done["text"]) == ("done", "macbook", "echo: settled in")
            [on_mac] = records(mac, "prompt")
            assert on_mac["env"]["DUSHA_CONVERSATION_ID"] == "c1"

            where = httpx.get(hub.url + "/state/v1/acp/executor").json()
            assert where["device"] == "macbook" and where["home"] == "nas"
            assert [(item["old"], item["new"]) for item in where["switches"]] == [("nas", "macbook")]

            # Leaving the device brings the turns home, in a new session.
            beat(hub.url, f"http://127.0.0.1:{port}", said="sleeping")
            assert turn(hub.url, "welcome back")[-1]["device"] == "nas"
            sessions = [row["session"] for row in records(nas, "prompt")]
            assert sessions[0] == sessions[1] != sessions[2]
            assert len(records(nas, "new")) == 2


def test_a_running_turn_on_a_device_can_be_stopped_and_the_device_pinned(tmp_path, plugins_dir):
    config = make_config(tmp_path / "hub", plugins_dir, hub_config(tmp_path, switch_after_minutes=0))
    port = free_port()
    with Server(config, free_port()) as hub, Server(mac_config(tmp_path, plugins_dir), port) as device:
        beat(hub.url, device.url)
        result: list = []
        running = threading.Thread(target=lambda: result.extend(turn(hub.url, "be slow")))
        running.start()
        wait_for(lambda: records(tmp_path / "mac" / "agent.jsonl", "prompt"))
        assert call(hub.url, "cancel").json() == {"device": "macbook", "cancelled": True}
        running.join(10)
        assert result[-1]["stop_reason"] == "cancelled" and result[-1]["text"] == "thinking"
        assert call(hub.url, "cancel").json() == {"cancelled": False}

        log = httpx.get(device.url + "/state/v1/acp/log", headers={"x-companion-token": TOKEN}).json()
        assert [event["event"] for event in log["events"]] == ["start", "chunk", "done"]

        assert httpx.put(hub.url + "/state/v1/acp/executor", json={"device": "nowhere"}).status_code == 422
        pinned = httpx.put(hub.url + "/state/v1/acp/executor", json={"device": "nas"}).json()
        assert pinned["device"] == "nas" and pinned["pinned"] == "nas"
        assert turn(hub.url, "pinned")[-1]["device"] == "nas"
        httpx.put(hub.url + "/state/v1/acp/executor", json={"device": "auto"}).raise_for_status()
        assert turn(hub.url, "free")[-1]["device"] == "macbook"


def test_an_unreachable_device_falls_back_to_the_hub(tmp_path, plugins_dir):
    config = make_config(tmp_path / "hub", plugins_dir, hub_config(tmp_path, switch_after_minutes=0))
    with Server(config, free_port()) as hub:
        beat(hub.url, f"http://127.0.0.1:{free_port()}")
        events = turn(hub.url, "anyone")
        assert events[-1] == {**events[-1], "event": "done", "device": "nas", "text": "echo: anyone"}


def test_model_mode_and_sessions_survive_a_restart(tmp_path, plugins_dir):
    log = tmp_path / "nas" / "agent.jsonl"
    config = make_config(tmp_path / "hub", plugins_dir, hub_config(tmp_path))
    with Server(config, free_port()) as hub:
        shown = call(hub.url, "config", route="discord:1").json()
        assert shown["model"] == "fake/small" and shown["models"] == ["fake/small", "fake/large"]
        assert shown["mode"] == "build" and shown["device"] == "nas"
        assert call(hub.url, "config", route="discord:1", model="fake/large").json()["model"] == "fake/large"
        assert call(hub.url, "config", route="discord:1", mode="plan").json()["mode"] == "plan"
        refused = call(hub.url, "config", route="discord:1", model="fake/missing")
        assert refused.status_code == 422 and refused.json()["detail"] == "unknown model"
        [first] = records(log, "new")

    with Server(config, free_port()) as hub:
        turn(hub.url, "again")
        [load] = records(log, "load")
        [prompt] = records(log, "prompt")
        assert load["session"] == first["session"] == prompt["session"]
        assert prompt["model"] == "fake/large" and prompt["mode"] == "plan"

        # A new conversation on the same route keeps the model and starts in the agent's mode.
        turn(hub.url, "fresh start", conversation="c2")
        fresh = records(log, "prompt")[-1]
        assert fresh["model"] == "fake/large" and fresh["mode"] == "build"
        listed = call(hub.url, "sessions", conversation_id="c2").json()
        assert listed["sessions"][0]["sessionId"] == listed["current"] == fresh["session"]

        resumed = call(hub.url, "resume", conversation_id="c2", session_id=first["session"]).json()
        assert resumed["loaded"] and resumed["session_id"] == first["session"]
        assert call(hub.url, "reset", conversation_id="c2").json() == {"device": "nas", "reset": True}


def test_a_crashed_agent_is_reported_and_respawned(tmp_path, plugins_dir):
    config = make_config(tmp_path / "hub", plugins_dir, hub_config(tmp_path))
    with Server(config, free_port()) as hub:
        assert turn(hub.url, "crash now")[-1]["event"] == "error"
        assert turn(hub.url, "hello")[-1]["text"] == "echo: hello"
        assert len({row["pid"] for row in records(tmp_path / "nas" / "agent.jsonl", "prompt")}) == 2
