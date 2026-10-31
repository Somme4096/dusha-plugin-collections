"""better_proactive: the built-in proactive engine, with reasons from any plugin instead of only silence.

candidates (why) -> gate (whether now) -> pick (highest score) -> deliver (the same queue, lease, and ack).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

from dusha.commands import Group
from dusha.config import ProactiveConfig
from dusha.proactive import (
    ProactiveEngine,
    _commands,
    _routes,
    build_proactive_instruction,
    format_silence,
    ladder_line,
    ruling_feeling,
    select_proactive_variant,
)
from dusha.serialization import compact_json
from dusha.timeutil import isoformat, parse_time, utc_now

logger = logging.getLogger("dusha.better_proactive")

KINDS = ("talk", "task")
CLAIM = re.compile(r'<dusha_proactive event="([0-9a-f-]{36})"\s*/>')

JUDGE_INSTRUCTIONS = (
    "You decide whether their companion should reach out to them right now, and about what. Read the "
    "timeline of what they have been doing, how they feel about the companion, and what was said "
    "recently. Pick the one moment that is worth an interruption and that a caring friend would actually "
    "bring up now, or none. Being quiet is often right: don't break deep focus for small things, but "
    "don't let them struggle alone for long either."
)
KIND_INSTRUCTIONS = (
    "How to reach out. 'task' when the companion should first look into the problem with its tools and "
    "come with something useful, 'talk' when a few words are enough."
)


@dataclass
class Extras:
    target: Any = "latest"
    away_target: Any = None
    min_gap_minutes: float = 10
    min_score: float = 0.0
    presence_overrides_quiet_hours: bool = True
    judge_recheck_minutes: float = 10
    judge_timeline_hours: float = 3
    judge_instructions: str = JUDGE_INSTRUCTIONS


def parse_options(raw: dict[str, Any]) -> tuple[ProactiveConfig, Extras]:
    raw = dict(raw or {})
    builtin = {item.name for item in fields(ProactiveConfig)}
    extras = {item.name for item in fields(Extras)}
    unknown = sorted(set(raw) - builtin - extras)
    if unknown:
        raise ValueError(f"unknown better_proactive option(s): {unknown}")
    config = ProactiveConfig(**{key: value for key, value in raw.items() if key in builtin})
    extra = Extras(**{key: value for key, value in raw.items() if key in extras})
    target = extra.target
    if target != "latest" and not (
        isinstance(target, dict) and target.get("harness") and target.get("conversation_id")
    ):
        raise ValueError("better_proactive option 'target' must be 'latest' or {harness, conversation_id}")
    away = extra.away_target
    if away is not None and not (isinstance(away, dict) and away.get("harness")):
        raise ValueError("better_proactive option 'away_target' must be {harness} or {harness, conversation_id}")
    return config, extra


def _candidate(item: Any, source: str) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not item.get("reason") or not item.get("dedup_key"):
        logger.warning("better_proactive ignored a malformed candidate from %s: %r", source, item)
        return None
    try:
        score = float(item.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    kind = item.get("kind", "talk")
    return {
        "source": str(item.get("source") or source),
        "reason": str(item["reason"]),
        "topic": str(item.get("topic") or ""),
        "score": score,
        "kind": kind if kind in KINDS else "talk",
        "instruction": str(item.get("instruction") or ""),
        "context": item.get("context"),
        "dedup_key": str(item["dedup_key"]),
        "requires_presence": item.get("requires_presence") or "",
        # Only a judge may pick these. Without one they are never raised.
        "needs_judge": bool(item.get("needs_judge")),
    }


AWAY = ("they aren't at any device",)


class BetterProactive(ProactiveEngine):
    def __init__(self, ctx: Any, extras: Extras, config: ProactiveConfig):
        super().__init__(ctx.database, config, ctx.settings.timezone, ctx.prompts, ctx.affect, ctx.companion)
        self.ctx = ctx
        self.extras = extras
        self.verdict: dict[str, Any] | None = None  # the judge's last answer, reused while nothing changes

    # Candidates.

    def _silence(self, state: dict[str, Any], current: datetime) -> list[dict[str, Any]]:
        """The built-in reason: they've been quiet and a feeling crossed its trigger."""
        if not state["last_user_message_at"]:
            return []
        silence = (current - parse_time(state["last_user_message_at"])).total_seconds() / 60
        if silence < self.rules.min_silence_minutes:
            return []
        reason = next(
            (
                item["reason"]
                for item in self.triggers
                if state["base"][item["dimension"]] >= item["threshold"]
            ),
            None,
        )
        if reason is None:
            return []
        feeling = ruling_feeling(state["base"], self.affect.spec)
        dedup = ":".join(
            (
                "silence",
                str(state["last_user_message_at"]),
                str(current.astimezone(self.timezone).date()),
                str(self._sent_today(current)),
            )
        )
        return [
            {
                "source": "silence",
                "reason": reason,
                "topic": "",
                "kind": "talk",
                "instruction": "",
                "context": None,
                "score": min(1.0, 0.4 + float(feeling["deviation"])),
                "dedup_key": dedup,
                "requires_presence": "",
                "needs_judge": False,
            }
        ]

    def candidates(self, state: dict[str, Any], current: datetime) -> list[dict[str, Any]]:
        found = self._silence(state, current)
        for plugin, answer in self.ctx.collect("proactive.candidates", isoformat(current)).items():
            for item in answer if isinstance(answer, list) else [answer]:
                candidate = _candidate(item, plugin)
                if candidate is not None:
                    found.append(candidate)
        return found

    # Gate and pick.

    def _presence(self) -> dict[str, Any] | None:
        answers = list(self.ctx.collect("presence").values())
        return answers[0] if answers else None

    def _route(self, target: Any) -> dict[str, Any] | None:
        if target == "latest":
            return self.companion.latest_route()
        with self.database.connect() as db:
            if target.get("conversation_id"):
                row = db.execute(
                    "SELECT * FROM conversations WHERE harness=? AND external_id=?",
                    (target["harness"], target["conversation_id"]),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT * FROM conversations WHERE harness=? AND route != ''
                       ORDER BY updated_at DESC, id DESC LIMIT 1""",
                    (target["harness"],),
                ).fetchone()
        return dict(row) if row else None

    def plan(self, current: datetime, ask_judge: bool = True) -> dict[str, Any]:
        """Everything evaluate decides, without queueing anything. `dusha proactive plan` shows it."""
        state = self.affect.status(current)
        blocked = self._blocked(state, current)
        presence = self._presence()
        here = bool(presence and presence.get("active_device"))
        if blocked is None and self._quiet(current):
            if not (self.extras.presence_overrides_quiet_hours and here):
                blocked = "quiet hours"
        # Away from every device: reach them where they still are, such as Discord on their phone.
        away = self._route(self.extras.away_target) if not here and self.extras.away_target else None
        route = away or self._route(self.extras.target)
        if blocked is None and not route:
            blocked = "no conversation to send to"
        candidates = self.candidates(state, current) if blocked is None else []
        used = self._used({item["dedup_key"] for item in candidates})
        since_user = (
            (current - parse_time(state["last_user_message_at"])).total_seconds() / 60
            if state["last_user_message_at"]
            else None
        )
        for item in candidates:
            item["skipped"] = self._skip(item, used, here or away is not None, since_user)
        eligible = [item for item in candidates if not item["skipped"]]
        judge = self._judge(eligible, state, route, presence, current, ask_judge) if eligible else None
        if judge is not None and judge.get("answered"):
            chosen = judge["candidate"]
            if chosen is not None:
                kind = judge["kind"] if judge["kind"] in KINDS else chosen["kind"]
                chosen = {**chosen, "kind": kind, "judged": True}
        else:
            # No judge: the highest score among what doesn't need one.
            for item in eligible:
                if item["needs_judge"] and (judge is None or judge.get("absent")):
                    item["skipped"] = "only a judge can raise this"
            pool = [item for item in eligible if not item["skipped"] and not item["needs_judge"]]
            chosen = max(pool, key=lambda item: item["score"], default=None)
        return {
            "blocked": blocked,
            "candidates": candidates,
            "chosen": chosen,
            "judge": judge,
            "route": route,
            "state": state,
        }

    def _judge(
        self,
        eligible: list[dict[str, Any]],
        state: dict[str, Any],
        route: dict[str, Any],
        presence: dict[str, Any] | None,
        current: datetime,
        ask: bool,
    ) -> dict[str, Any] | None:
        keys = {f"c{index}": item for index, item in enumerate(eligible, start=1)}
        signature = sorted(item["dedup_key"] for item in eligible)
        cached = self.verdict
        fresh = (
            cached is not None
            and cached["signature"] == signature
            and (
                (current - parse_time(cached["at"])).total_seconds() < self.extras.judge_recheck_minutes * 60
            )
        )
        if fresh or not ask:
            if not fresh:
                return {"asked": False, "note": "the judge is asked on the next evaluation"}
            pick = cached["speak"]
            return {**cached, "candidate": keys.get(pick), "reused": True}
        request = self._judge_request(keys, state, route, presence, current)
        answers = [answer for answer in self.ctx.collect("proactive.decide", request).values() if answer]
        if not answers:
            return {"absent": True}
        answer = answers[0]
        speak, kind = answer.get("speak"), answer.get("kind", "talk")
        if speak != "none" and speak not in keys:
            logger.warning("better_proactive ignored a judge answer outside the choices: %r", answer)
            return {"absent": True}
        self.verdict = {
            "answered": True,
            "signature": signature,
            "at": isoformat(current),
            "speak": speak,
            "kind": kind,
        }
        return {**self.verdict, "candidate": keys.get(speak)}

    def _judge_request(
        self,
        keys: dict[str, dict[str, Any]],
        state: dict[str, Any],
        route: dict[str, Any],
        presence: dict[str, Any] | None,
        current: datetime,
    ) -> dict[str, Any]:
        timelines = list(
            self.ctx.collect("ambient.timeline", self.extras.judge_timeline_hours, 40000).values()
        )
        recent = sorted(
            self.ctx.messages.recent(conversation_id=route["id"], limit=6), key=lambda row: row["id"]
        )
        last_user = state["last_user_message_at"]
        criteria = {"none": "Stay quiet for now. Nothing here is worth interrupting them for."}
        criteria.update({key: item["topic"] or item["reason"] for key, item in keys.items()})
        return {
            "state": {
                "now": current.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M (%A)"),
                "timeline": timelines[0] if timelines else [],
                "devices": {item["device"]: item["status"] for item in (presence or {}).get("devices", [])},
                "active_device": (presence or {}).get("active_device"),
                "feeling": ruling_feeling(state["base"], self.affect.spec),
                "recent_messages": [f"{row['role']}: {str(row.get('text') or '')[:300]}" for row in recent],
                "minutes_since_she_spoke": (
                    round((current - parse_time(last_user)).total_seconds() / 60) if last_user else None
                ),
                "proactive_today": {"sent": self._sent_today(current), "max": self.rules.max_per_day},
                "unanswered_proactive": int(state["unanswered_proactive"]),
                "candidates": {
                    key: {
                        "source": item["source"],
                        "reason": item["reason"],
                        "topic": item["topic"],
                        "context": item["context"],
                    }
                    for key, item in keys.items()
                },
            },
            "questions": {
                "speak": {
                    "type": "choice",
                    "instructions": self.extras.judge_instructions,
                    "criteria": criteria,
                },
                "kind": {
                    "type": "choice",
                    "instructions": KIND_INSTRUCTIONS,
                    "criteria": {"talk": "Just say something", "task": "Look into it with tools first"},
                },
            },
        }

    def _blocked(self, state: dict[str, Any], current: datetime) -> str | None:
        if state["unanswered_proactive"] >= self.rules.max_unanswered:
            return "too many unanswered messages"
        if self._sent_today(current) >= self.rules.max_per_day:
            return "daily cap reached"
        if state["last_proactive_sent_at"]:
            elapsed = (current - parse_time(state["last_proactive_sent_at"])).total_seconds() / 60
            if elapsed < self.rules.cooldown_minutes:
                return "cooling down"
        with self.database.connect() as db:
            if db.execute(
                "SELECT 1 FROM proactive_events WHERE status IN ('pending','leased') LIMIT 1"
            ).fetchone():
                return "a message is already waiting for delivery"
        return None

    def _used(self, keys: set[str]) -> set[str]:
        if not keys:
            return set()
        marks = ",".join("?" * len(keys))
        with self.database.connect() as db:
            rows = db.execute(
                f"SELECT dedup_key FROM proactive_events WHERE dedup_key IN ({marks})", tuple(keys)
            )
            return {row["dedup_key"] for row in rows}

    def _skip(
        self, item: dict[str, Any], used: set[str], reachable: bool, since_user: float | None
    ) -> str:
        if item["dedup_key"] in used:
            return "already raised"
        if item["score"] < self.extras.min_score:
            return "score below min_score"
        if item["requires_presence"] == "active" and not reachable:
            return "they aren't at any device"
        if (
            item["source"] != "silence"
            and since_user is not None
            and since_user < self.extras.min_gap_minutes
        ):
            return "they talked just now"
        return ""

    # Deliver.

    def _hold(self, plan: dict[str, Any], current: datetime) -> None:
        """Tell whoever listens what the gate stopped. Never from `plan`, which the webui polls."""
        blocked = plan["blocked"]
        for item in plan["candidates"] or (self.candidates(plan["state"], current) if blocked else []):
            because = blocked or (item.get("skipped") if item.get("skipped") in AWAY else "")
            if because and not item["needs_judge"] and item["score"] >= self.extras.min_score:
                self.ctx.emit("proactive.held", {**item, "because": because})

    def evaluate(self, now: datetime | None = None) -> dict[str, Any] | None:
        current = now or utc_now()
        plan = self.plan(current)
        chosen, route, state = plan["chosen"], plan["route"], plan["state"]
        self._hold(plan, current)
        if plan["blocked"] or chosen is None:
            return None
        payload = self._payload(chosen, route, state, current)
        try:
            with self.database.connect() as db:
                db.execute(
                    """INSERT INTO proactive_events
                       (id, conversation_id, route, reason, dedup_key, payload_json,
                        status, available_at, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,'pending',?,?,?)""",
                    (
                        payload["id"],
                        route["id"],
                        route["route"],
                        chosen["reason"],
                        chosen["dedup_key"],
                        compact_json(payload),
                        *(isoformat(current),) * 3,
                    ),
                )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                return None
            raise
        return payload

    def _payload(
        self, chosen: dict[str, Any], route: dict[str, Any], state: dict[str, Any], current: datetime
    ) -> dict[str, Any]:
        framing = self.text["framing"]
        spec = self.text["generation_instruction"]
        sent_today = self._sent_today(current)
        base = str(spec["base"])
        variant_index, variant = select_proactive_variant(spec, sent_today)
        feeling = ruling_feeling(state["base"], self.affect.spec)
        ladder_stage, ladder = ladder_line(framing["ladder"], int(state["unanswered_proactive"]))
        instruction = build_proactive_instruction(base, variant, framing, feeling, ladder)
        if chosen["topic"]:
            instruction += f"\nWhy you're reaching out now: {chosen['topic']}"
        if chosen["kind"] == "task":
            todo = chosen["instruction"] or chosen["topic"]
            instruction += (
                f"\nBefore you write, do this with your tools: {todo}\nThen tell them what you found."
            )
        context = self.companion.build_context(
            query=chosen["topic"] or str(framing["context_query"]),
            harness=route["harness"],
            conversation_id=route["external_id"],
        )
        last_user = state["last_user_message_at"]
        silence = (current - parse_time(last_user)).total_seconds() / 60 if last_user else 0.0
        event_id = str(uuid.uuid4())
        return {
            "id": event_id,
            "claim_marker": f'<dusha_proactive event="{event_id}"/>',
            "target": {
                "harness": route["harness"],
                "conversation_id": route["external_id"],
                "route": route["route"],
            },
            "reason": chosen["reason"],
            "source": chosen["source"],
            "kind": chosen["kind"],
            "topic": chosen["topic"],
            "candidate_context": chosen["context"],
            "judged": bool(chosen.get("judged")),
            "generation_instruction": instruction,
            "generation_base": base,
            "generation_variant": variant,
            "generation_variant_index": variant_index,
            "ruling_feeling": feeling,
            "ladder_stage": ladder_stage,
            "unanswered_proactive": int(state["unanswered_proactive"]),
            "silence_minutes": round(silence, 2),
            "silence_text": format_silence(silence),
            "last_user_message_at": last_user,
            "evaluated_at": isoformat(current),
            "context": context,
            "created_at": isoformat(current),
        }

    def claim(self, offer: dict[str, Any]) -> dict[str, Any] | None:
        """A consumer that writes the message through its own agent sends the marker as that agent's turn."""
        match = CLAIM.search(offer["text"])
        if not match:
            return None
        with self.database.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM proactive_events WHERE id=? AND status='leased'", (match[1],)
            ).fetchone()
            if row is None:
                return None
            payload = {**json.loads(row["payload_json"]), "claimed": True}
            db.execute(
                "UPDATE proactive_events SET payload_json=? WHERE id=?", (compact_json(payload), match[1])
            )
        return {"event": match[1]}

    def acknowledge(self, event_id: str, consumer: str, outcome: str, **kwargs: Any) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute("SELECT payload_json FROM proactive_events WHERE id=?", (event_id,)).fetchone()
        payload = json.loads(row["payload_json"]) if row is not None else {}
        text = kwargs.get("text", "")
        if payload.get("claimed"):
            # The agent's reply is already archived with the claimed turn.
            kwargs["text"] = ""
        result = super().acknowledge(event_id, consumer, outcome, **kwargs)
        if outcome == "sent" and row is not None:
            self.ctx.emit("proactive.sent", text, payload)
        return result

    def show_plan(self) -> dict[str, Any]:
        # Never asks the judge: the webui polls this, and polling must not cost model calls.
        plan = self.plan(utc_now(), ask_judge=False)
        return {key: plan[key] for key in ("blocked", "candidates", "chosen", "judge")}


def apply(ctx: Any, config: dict[str, Any]) -> None:
    proactive_config, extras = parse_options(config)
    engine = BetterProactive(ctx, extras, proactive_config)
    try:
        ctx.provide("proactive", engine)
    except ValueError:
        ctx.error = (
            'the built-in proactive plugin is on, set "proactive": {"disabled": true} to use better_proactive'
        )
        return
    ctx.on("message", engine.on_message)
    ctx.on("claim", engine.claim)
    ctx.interval(max(5, proactive_config.poll_interval_seconds), engine.evaluate)
    _routes(ctx.api, engine)
    _commands(ctx.cli, engine)
    Group(ctx.cli, "proactive").command(
        "plan",
        engine.show_plan,
        help="Show why the companion would or wouldn't speak up right now, and about what",
    )
