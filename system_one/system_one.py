from __future__ import annotations

import ipaddress
import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin

import httpx

__all__ = ["SystemOneExternalPluginError", "apply", "decide", "decide_proactive"]

_DEFAULT_TIMEOUT_SECONDS = 10.0
_SYSTEM_ONE_PATH = "/v1/systemone"


class SystemOneExternalPluginError(RuntimeError):
    """Raised when the SystemOne provider fails or answers unusably."""


def _timeout_seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("system_one plugin option 'timeout_seconds' must be a finite positive number")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("system_one plugin option 'timeout_seconds' must be a finite positive number")
    return seconds


def _allowed_ips(value: Any) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("system_one plugin option 'allowed_ips' must be a list of IPv4 or IPv6 addresses")
    addresses = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("system_one plugin option 'allowed_ips' must contain only IP address strings")
        try:
            addresses.add(ipaddress.ip_address(item))
        except ValueError as exc:
            raise ValueError(
                f"system_one plugin option 'allowed_ips' contains invalid address {item!r}"
            ) from exc
    return frozenset(addresses)


def _url_for_request(base_url: Any, allowed: frozenset | None) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("system_one plugin requires a non-empty string option 'base_url'")
    url = base_url.strip().rstrip("/") + _SYSTEM_ONE_PATH
    if allowed is not None:
        parsed = httpx.URL(url)
        try:
            address = ipaddress.ip_address(parsed.host or "")
        except ValueError as exc:
            raise ValueError(
                "system_one plugin requires a literal IP base_url when 'allowed_ips' is set"
            ) from exc
        if address not in allowed:
            raise ValueError(f"system_one plugin base_url address {address} is not in 'allowed_ips'")
    return url


def _headers(options: Mapping[str, Any]) -> dict[str, str] | None:
    token = options.get("api_key")
    if token is None:
        return None
    if not isinstance(token, str) or not token.strip():
        raise ValueError("system_one plugin option 'api_key' must be a non-empty string when set")
    return {"Authorization": f"Bearer {token}"}


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = httpx.URL(url)
    return parsed.scheme, parsed.host or "", parsed.port


def _criteria(emotions: Mapping[str, Any]) -> dict[str, str]:
    criteria: dict[str, str] = {}
    for dimension, definition in emotions.items():
        name = str(dimension)
        description = definition.get("description") if isinstance(definition, Mapping) else None
        criteria[name] = (
            description.strip()
            if isinstance(description, str) and description.strip()
            else name.replace("_", " ")
        )
    return criteria


def _extract_choice(payload: Any, allowed: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise SystemOneExternalPluginError("system_one response must be a JSON object")
    answer = payload.get("answers")
    emotion = answer.get("emotion") if isinstance(answer, Mapping) else None
    if not isinstance(emotion, Mapping):
        raise SystemOneExternalPluginError("system_one response is missing an object 'answers.emotion'")
    if emotion.get("type") != "choice":
        raise SystemOneExternalPluginError("system_one response emotion must have type 'choice'")
    choice = emotion.get("choice")
    if not isinstance(choice, str) or choice not in allowed:
        raise SystemOneExternalPluginError(
            f"system_one chose emotion {choice!r}, which is not an allowed emotion"
        )
    return choice


def _ask(body: dict[str, Any], options: Mapping[str, Any]) -> Any:
    """POST typed questions to the SystemOne server and return its JSON answer."""
    if not isinstance(options, Mapping):
        raise TypeError("system_one plugin options must be a mapping")
    allowed_ips = _allowed_ips(options.get("allowed_ips"))
    url = _url_for_request(options.get("base_url"), allowed_ips)
    timeout = _timeout_seconds(options.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))
    headers = _headers(options)
    model = options.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("system_one plugin option 'model' must be a non-empty string when set")
    if model is not None:
        body = {**body, "model": model.strip()}
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            current = url
            current_headers = headers
            for _ in range(10):
                if allowed_ips is not None:
                    parsed = httpx.URL(current)
                    try:
                        address = ipaddress.ip_address(parsed.host or "")
                    except ValueError as exc:
                        raise SystemOneExternalPluginError(
                            "system_one redirect target must use a literal IP address"
                        ) from exc
                    if address not in allowed_ips:
                        raise SystemOneExternalPluginError(
                            f"system_one redirect address {address} is not in 'allowed_ips'"
                        )
                response = client.post(current, json=body, headers=current_headers)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise SystemOneExternalPluginError(
                            "system_one provider returned a redirect without a location"
                        )
                    next_url = urljoin(current, location)
                    current_scheme, _, _ = _origin(current)
                    next_scheme, _, _ = _origin(next_url)
                    if current_scheme == "https" and next_scheme != "https":
                        raise SystemOneExternalPluginError(
                            "system_one provider HTTPS redirect downgrade is not allowed"
                        )
                    if headers is not None and _origin(current) != _origin(next_url):
                        current_headers = None
                    current = next_url
                    continue
                response.raise_for_status()
                break
            else:
                raise SystemOneExternalPluginError("system_one provider returned too many redirects")
    except SystemOneExternalPluginError:
        raise
    except httpx.HTTPStatusError as exc:
        raise SystemOneExternalPluginError(
            f"system_one provider returned HTTP {exc.response.status_code} for {current}"
        ) from exc
    except httpx.RequestError as exc:
        raise SystemOneExternalPluginError(f"system_one provider request to {current} failed: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise SystemOneExternalPluginError("system_one provider returned a non-JSON response") from exc


def decide(request: Any, options: Mapping[str, Any]) -> dict[str, str] | None:
    emotions = request.emotions
    if not emotions:
        return None
    body: dict[str, Any] = {
        "state": {"message": request.message},
        "questions": {
            "emotion": {
                "type": "choice",
                "instructions": request.instruction,
                "criteria": _criteria(emotions),
            }
        },
    }
    return {"emotion": _extract_choice(_ask(body, options), emotions)}


def _extract_answers(payload: Any, questions: Mapping[str, Any]) -> dict[str, str]:
    answers = payload.get("answers") if isinstance(payload, Mapping) else None
    if not isinstance(answers, Mapping):
        raise SystemOneExternalPluginError("system_one response is missing an object 'answers'")
    result: dict[str, str] = {}
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            raise SystemOneExternalPluginError(f"system_one response is missing a choice for {name!r}")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in question["criteria"]:
            raise SystemOneExternalPluginError(
                f"system_one chose {choice!r} for {name!r}, which is not one of its criteria"
            )
        result[name] = choice
    return result


def decide_proactive(request: Any, options: Mapping[str, Any]) -> dict[str, str] | None:
    """Answers better_proactive's `proactive.decide`: whether to speak up, about which candidate, and how."""
    if not isinstance(request, Mapping) or not isinstance(request.get("questions"), Mapping):
        return None
    questions = request["questions"]
    for name, question in questions.items():
        if not isinstance(question, Mapping) or question.get("type") != "choice":
            raise ValueError(f"proactive.decide question {name!r} must be a choice")
        if not isinstance(question.get("criteria"), Mapping) or not question["criteria"]:
            raise ValueError(f"proactive.decide question {name!r} needs criteria")
    body = {"state": request.get("state", {}), "questions": dict(questions)}
    return _extract_answers(_ask(body, options), questions)


def apply(ctx: Any, config: Mapping[str, Any]) -> None:
    ctx.on("decide", lambda request: decide(request, config))
    ctx.on("proactive.decide", lambda request: decide_proactive(request, config))
