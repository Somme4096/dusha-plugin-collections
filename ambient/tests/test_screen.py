from __future__ import annotations

import json

import httpx
from dusha.config import PromptsConfig
from dusha.providers import Providers, parse

from conftest import FakeCtx, Server, client, completion, free_port, load_plugin, make_config, wait_for

STUCK = {
    "app": "VS Code",
    "title": "presence.py — dusha-plugin-collections",
    "content": 'pytest shows 1 failed: AssertionError "2h" not in "after 3h".',
    "activity": "debugging a failing presence test",
    "status": "stuck",
    "progress": "continued",
    "notable": "same assertion error as before",
}
READING = {
    **STUCK,
    "content": "The dusha README.",
    "activity": "reading docs",
    "status": "focused",
    "progress": "switched",
    "notable": "",
}


def _providers(vision_url: str):
    headers = {"x-trace": "dusha", "x-session": "{conversation_id}"}
    vlm = {"base_url": vision_url + "/v1", "key": "{env:VISION_KEY}", "headers": headers}
    return Providers(parse({"vlm": vlm}, environ=False))


def _watch(tmp_path, vision_url: str, command, prompts=None, **screen):
    module = load_plugin("ambient")
    ctx = FakeCtx()
    ctx.prompts = prompts
    ctx.data_dir = tmp_path / "data"
    ctx.data_dir.mkdir()
    ctx.providers = _providers(vision_url)
    options = {
        "provider": "vlm",
        "model": "tiny-vl",
        "backend": "command",
        "command": command,
        "keep_images": 2,
        **screen,
    }
    ambient = module.Ambient(ctx, module.Options(device="desktop", screen=options))
    return ambient, ctx


def test_screen_rows_record_changes_only(tmp_path, recorder, fake_screen, monkeypatch):
    monkeypatch.setenv("VISION_KEY", "vk-123")
    replies = iter([completion(READING), completion(STUCK), completion(STUCK)])
    vision = recorder(reply=lambda body: next(replies))
    command, frame = fake_screen
    ambient, _ = _watch(tmp_path, vision.url, command, extra_body={"reasoning_effort": "none"})
    watch = ambient.screen

    first = watch.capture(force=False)
    assert first["changed"] and first["activity"] == "reading docs"
    assert watch.capture(force=False)["changed"] is False  # same pixels, no model call
    assert len(vision.requests) == 1

    frame.write_text("darkred")
    second = watch.capture(force=False)
    assert second["changed"] and second["status"] == "stuck"
    frame.write_text("darkgreen")
    assert watch.capture(force=False)["changed"] is False  # new pixels, same description: no new row
    assert len(vision.requests) == 3

    request = vision.requests[1]
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer vk-123"
    assert request["headers"]["x-trace"] == "dusha"
    assert request["headers"]["x-session"] == "ambient-screen-desktop"
    body = request["body"]
    assert body["model"] == "tiny-vl" and body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 1000 and body["reasoning_effort"] == "none"  # extra_body is merged in
    text, image = body["messages"][1]["content"]
    assert '"activity": "reading docs"' in text["text"]  # the previous capture, for continuity
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")

    rows = ambient.screen_rows()
    assert [row["data"]["status"] for row in rows] == ["focused", "stuck"]
    assert rows[-1]["text"].splitlines()[0] == "VS Code — debugging a failing presence test (stuck)"
    assert len(list((tmp_path / "data" / "screens").glob("*.jpg"))) == 2

    block = ambient.context(None).data
    assert block["screen_now"].endswith('pytest shows 1 failed: AssertionError "2h" not in "after 3h".')
    assert block["screen_before"][0].endswith("VS Code — reading docs (focused)")
    screens = [item for item in ambient.candidates() if item["reason"].startswith("screen_")]
    assert [item["reason"] for item in screens] == ["screen_focused", "screen_stuck"]
    assert all(item["needs_judge"] for item in screens)
    assert "Notable: same assertion error as before" in screens[-1]["topic"]
    assert any("VS Code — debugging" in line for line in ambient.timeline())


def test_plain_text_answers_and_servers_without_json_mode(tmp_path, recorder, fake_screen):
    def reply(body):
        if "response_format" in body:
            return 400, {"error": "response_format is not supported"}
        return completion("Reading a long article about bees\nIt has diagrams. Token: ghp_" + "x1Y2" * 9)

    vision = recorder(reply=reply)
    prompts = {"plugins": {"ambient": {"screen": ["Say what they read.", "Plain text is fine."]}}}
    ambient, _ = _watch(tmp_path, vision.url, fake_screen[0], prompts, key="override")
    result = ambient.screen.capture(force=True)
    assert vision.requests[0]["body"]["messages"][0]["content"] == "Say what they read.\nPlain text is fine."
    assert result["activity"] == "Reading a long article about bees"
    assert vision.requests[1]["headers"]["authorization"] == "Bearer override"
    assert "response_format" not in vision.requests[1]["body"]
    [row] = ambient.screen_rows()
    assert "ghp_" not in row["text"] and "[redacted:github-token]" in row["text"]
    ambient.screen.capture(force=True)
    assert "response_format" not in vision.requests[-1]["body"]  # remembered


async def test_a_thinking_model_that_runs_out_of_tokens_says_so(tmp_path, plugins_dir, recorder, fake_screen):
    out = {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "hmm"}}]}
    vision = recorder(reply=out)
    screen = {"base_url": vision.url, "model": "thinker", "backend": "command", "command": fake_screen[0]}
    screen |= {"every_seconds": 3600, "max_tokens": 50, "extra_body": {"reasoning_effort": "minimal"}}
    ambient = {"device": "desktop", "sample_seconds": 3600, "screen": screen}
    config = make_config(tmp_path, plugins_dir, {"ambient": ambient})
    async with client(config) as http:
        response = await http.post("/state/v1/screenshot")
    assert response.status_code == 503
    assert "all 50 tokens before answering" in response.json()["detail"]
    assert vision.requests[0]["body"]["reasoning_effort"] == "minimal"


def test_hub_looks_at_a_device_screen_through_relay(tmp_path, plugins_dir, recorder, fake_screen):
    vision = recorder(reply=completion(STUCK))
    hub_config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {"presence": {"device": "nas", "sensor": "none", "interval_seconds": 30}, "relay": {}},
    )
    with Server(hub_config, free_port()) as hub:
        port = free_port()
        device_config = make_config(
            tmp_path / "desk",
            plugins_dir,
            {
                "presence": {
                    "device": "desktop",
                    "sensor": "none",
                    "interval_seconds": 30,
                    "report_to": {"url": hub.url},
                    "url": f"http://127.0.0.1:{port}",
                },
                "ambient": {
                    "device": "desktop",
                    "sample_seconds": 3600,
                    "screen": {
                        "base_url": vision.url,
                        "model": "tiny-vl",
                        "backend": "command",
                        "command": fake_screen[0],
                        "every_seconds": 3600,
                    },
                },
            },
        )
        prompts = tmp_path / "prompts.json"
        prompts.write_text(json.dumps({"plugins": {"ambient": {"screen": "PROMPT_SENTINEL"}}}))
        device_config.prompts = PromptsConfig(path=str(prompts))
        with Server(device_config, port):

            def desktop():
                rows = httpx.get(hub.url + "/state/v1/presence").json()["devices"]
                return next((row for row in rows if row["device"] == "desktop"), None)

            assert wait_for(desktop)["capabilities"] == ["look"]
            reply = httpx.post(
                hub.url + "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "look", "arguments": {"device": "desktop"}},
                },
            ).json()["result"]
    assert not reply.get("isError"), reply
    assert vision.requests[0]["body"]["messages"][0]["content"] == "PROMPT_SENTINEL"
    seen = json.loads(reply["content"][0]["text"])
    assert seen["device"] == "desktop" and seen["status"] == "stuck"
    assert seen["content"].startswith("pytest shows 1 failed")
