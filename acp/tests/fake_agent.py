"""A scripted ACP agent. It logs what it receives to $FAKE_LOG and answers by keyword."""

from __future__ import annotations

import json
import os
import select
import sys
import time
import uuid

LOG = os.environ["FAKE_LOG"]
SESSIONS = os.path.join(os.path.dirname(LOG), "sessions.json")
MODELS = ["fake/small", "fake/large"]
STDIN = os.fdopen(0, "rb", buffering=0)
state = {"session": "", "model": MODELS[0], "mode": "build", "next": 1000}


def log(kind, **data):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "pid": os.getpid(), **data}) + "\n")


def write(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read():
    line = STDIN.readline()
    return json.loads(line) if line else None


def update(payload):
    write(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": state["session"], "update": payload},
        }
    )


def say(text):
    update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def options():
    choices = [{"value": name, "name": name} for name in MODELS]
    return [
        {"id": "model", "name": "Model", "type": "select", "currentValue": state["model"], "options": choices}
    ]


def modes():
    return {"currentModeId": state["mode"], "availableModes": [{"id": "build"}, {"id": "plan"}]}


def stored():
    return json.load(open(SESSIONS)) if os.path.exists(SESSIONS) else []


def ask(method, params):
    state["next"] += 1
    write({"jsonrpc": "2.0", "id": state["next"], "method": method, "params": params})
    while True:
        message = read()
        if message.get("id") == state["next"] and "method" not in message:
            return message


def cancelled(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([STDIN], [], [], 0.05)
        if ready:
            message = read()
            if message and message.get("method") == "session/cancel":
                return True
    return False


def prompt(params):
    text = " ".join(block.get("text", "") for block in params["prompt"] if block["type"] == "text")
    images = [block["mimeType"] for block in params["prompt"] if block["type"] == "image"]
    log(
        "prompt",
        text=text,
        images=images,
        session=state["session"],
        model=state["model"],
        mode=state["mode"],
        env={key: os.environ.get(key) for key in ("DUSHA_CONVERSATION_ID", "DUSHA_ROUTE", "DUSHA_HARNESS")},
    )
    if "silent" in text:
        return "end_turn"
    if "slow" in text:
        say("thinking")
        return "cancelled" if cancelled(10) else "end_turn"
    if "perm" in text:
        options = [
            {"optionId": "no", "kind": "reject_once"},
            {"optionId": "yes-always", "kind": "allow_always"},
        ]
        answer = ask("session/request_permission", {"sessionId": state["session"], "options": options})
        say(f"permission {answer['result']['outcome']['optionId']}")
        return "end_turn"
    if "tool" in text:
        say("let me look")
        update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "read"})
        say("found it")
        return "end_turn"
    if "crash" in text:
        sys.exit(3)
    say("echo: ")
    say(text)
    return "end_turn"


def handle(message):
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True}},
        }
    if method == "session/new":
        state["session"] = uuid.uuid4().hex
        log("new", session=state["session"])
        sessions = [{"sessionId": state["session"], "title": "chat " + state["session"][:4]}, *stored()]
        with open(SESSIONS, "w") as handle:
            json.dump(sessions, handle)
        return {"sessionId": state["session"], "modes": modes(), "configOptions": options()}
    if method == "session/load":
        if params["sessionId"] not in {item["sessionId"] for item in stored()}:
            return {"error": {"code": -32602, "message": "unknown session"}}
        state["session"] = params["sessionId"]
        log("load", session=state["session"])
        say("replayed history")
        return {"modes": modes(), "configOptions": options()}
    if method == "session/list":
        return {"sessions": stored()}
    if method == "session/set_mode":
        state["mode"] = params["modeId"]
        log("mode", mode=state["mode"])
        return {}
    if method == "session/set_config_option":
        if params["value"] not in MODELS:
            return {"error": {"code": -32602, "message": "unknown model"}}
        state["model"] = params["value"]
        log("model", model=state["model"])
        return {"configOptions": options()}
    if method == "session/prompt":
        return {"stopReason": prompt(params)}
    return {"error": {"code": -32601, "message": "unknown"}}


while (incoming := read()) is not None:
    if "id" not in incoming:
        continue
    result = handle(incoming)
    if "error" in result:
        write({"jsonrpc": "2.0", "id": incoming["id"], **result})
    else:
        write({"jsonrpc": "2.0", "id": incoming["id"], "result": result})
