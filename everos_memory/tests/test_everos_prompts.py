from __future__ import annotations

from types import SimpleNamespace

import pytest
from dusha.prompts import PromptsValidationError

from conftest import load_plugin

RECORDS = {"records": [{"text": "We hiked Bukhansan in May."}]}


def _recall(prompts: dict) -> str:
    module = load_plugin("everos_memory")
    mirror = SimpleNamespace(inject_context=lambda **_: RECORDS)
    request = SimpleNamespace(query="hike", conversation_id="c1", harness="test")
    slots = module.delimiters(prompts, "everos_memory")
    return module._recall("everos_memory", slots, mirror, request, {}).text


def test_episodes_use_default_delimiters_without_an_overlay():
    text = _recall({"plugins": {}})
    assert text.startswith("<plugin_context>\n") and text.endswith("\n</plugin_context>")
    assert "- We hiked Bukhansan in May." in text


def test_prompts_overlay_replaces_one_delimiter():
    text = _recall({"plugins": {"everos_memory": {"open_delimiter": "<episodes>"}}})
    assert text.startswith("<episodes>\n") and text.endswith("</plugin_context>")
    with pytest.raises(PromptsValidationError, match="unknown field"):
        _recall({"plugins": {"everos_memory": {"opening": "<x>"}}})
