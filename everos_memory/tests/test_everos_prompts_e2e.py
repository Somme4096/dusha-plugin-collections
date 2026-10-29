from __future__ import annotations

import json

import pytest
from dusha.config import PromptsConfig

from conftest import client, make_config

EVEROS = {"app_id": "dusha", "project_id": "p1", "instance_namespace": "e2e"}
SENDERS = {"user_sender_id": "user-e2e", "assistant_sender_id": "assistant-e2e"}


def _episodes(body):
    session = (body.get("filters") or {}).get("session_id")
    episode = {"session_id": session, "subject": "Hiking", "episode": "Bukhansan in May."}
    return {"data": {"episodes": [episode]}}


def _config(tmp_path, plugins_dir, everos_url, overlay):
    prompts = tmp_path / "prompts.json"
    prompts.write_text(json.dumps({"plugins": {"everos_memory": overlay}}))
    options = {"interval_seconds": 3600, "everos": {"url": everos_url, **EVEROS, **SENDERS}}
    config = make_config(tmp_path / "data", plugins_dir, {"everos_memory": options})
    config.prompts = PromptsConfig(path=str(prompts))
    return config


async def test_recalled_episodes_use_the_prompts_overlay(tmp_path, plugins_dir, recorder):
    everos = recorder(reply=_episodes)
    async with client(_config(tmp_path, plugins_dir, everos.url, {"open_delimiter": "<episodes>"})) as http:
        message = {"harness": "e2e", "conversation_id": "c1", "role": "user", "content": "remember the hike?"}
        assert (await http.post("/state/v1/messages", json=message)).status_code == 200
        query = {"harness": "e2e", "conversation_id": "c1", "query": ""}
        context = await http.post("/state/v1/context", json=query)
    injection = context.json()["injection"]
    assert "<episodes>\nEpisodes EverOS recalled" in injection
    assert "- Hiking: Bukhansan in May.\n</plugin_context>" in injection


async def test_an_unknown_prompts_key_fails_startup(tmp_path, plugins_dir, recorder):
    everos = recorder(reply=_episodes)
    with pytest.raises(Exception, match="unknown field"):
        async with client(_config(tmp_path, plugins_dir, everos.url, {"opening": "<x>"})):
            pass
