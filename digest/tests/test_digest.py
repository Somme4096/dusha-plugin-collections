from __future__ import annotations

import httpx
from dusha.providers import parse

from conftest import Server, completion, free_port, make_config, wait_for


def test_digest_summarizes_the_day_and_diary_files_it(tmp_path, plugins_dir, recorder, fake_desktop):
    model = recorder(reply=completion("Shipped spawn. The build broke once."))
    vault = tmp_path / "vault"
    command, desktop_log = fake_desktop
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "ambient": {"device": "nas", "sample_seconds": 3600},
            "notification": {"device": "nas", "desktop_command": command},
            "digest": {"hour": 23, "provider": "fake", "model": "m"},
            "diary": {"vault": str(vault)},
        },
        providers=parse({"fake": {"base_url": model.url + "/v1", "timeout_seconds": 5}}, environ=False),
    )
    with Server(config, free_port()) as hub:
        assert httpx.get(hub.url + "/state/v1/digest").status_code == 404
        httpx.post(hub.url + "/state/v1/ambient", json={"source": "git", "text": "committed spawn"})
        httpx.post(hub.url + "/state/v1/memo/add", json={"text": "call the plumber"})

        day = httpx.post(hub.url + "/state/v1/digest", timeout=10).json()
        assert day["text"] == "Shipped spawn. The build broke once."
        sent = model.requests[-1]
        assert sent["body"]["model"] == "m"
        assert "committed spawn" in sent["body"]["messages"][1]["content"]
        assert "call the plumber" in sent["body"]["messages"][1]["content"]

        assert httpx.get(hub.url + "/state/v1/digest").json() == day
        memos = httpx.get(hub.url + "/state/v1/memo/list").json()["memos"]
        assert any(item["text"].startswith(f"Digest {day['date']}") for item in memos)
        wait_for(lambda: desktop_log.exists() and "Today|Shipped spawn" in desktop_log.read_text())
        entry = vault / "Diary" / f"{day['date']}.md"
        assert entry.read_text() == f"# {day['date']}\n\nShipped spawn. The build broke once.\n"


def test_without_a_model_a_digest_counts_and_a_remote_diary_fetches_it(tmp_path, plugins_dir):
    hub_config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {
            "ambient": {"device": "nas", "sample_seconds": 3600},
            "digest": {"hour": 23, "notify": False, "memo": False},
        },
    )
    vault = tmp_path / "vault"
    with Server(hub_config, free_port()) as hub:
        httpx.post(hub.url + "/state/v1/ambient", json={"source": "git", "text": "committed one"})
        httpx.post(hub.url + "/state/v1/ambient", json={"source": "shell", "text": "failed two"})
        day = httpx.post(hub.url + "/state/v1/digest").json()
        assert day["text"].startswith("2 things happened:") and "1 git" in day["text"]
        device_config = make_config(
            tmp_path / "desk",
            plugins_dir,
            {"diary": {"vault": str(vault), "every_seconds": 1, "source": {"url": hub.url}}},
        )
        with Server(device_config, free_port()):
            wait_for(lambda: (vault / "Diary" / f"{day['date']}.md").exists(), timeout=8)
