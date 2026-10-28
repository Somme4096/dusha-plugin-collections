"""Secrets out of observations before they are stored or forwarded.

Known token formats first, because they are precise, then an entropy catch-all for long random strings.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

# (kind, pattern, group): the group is what gets replaced, 0 for the whole match.
KNOWN: list[tuple[str, str, int]] = [
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", 0),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})", 0),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}", 0),
    ("openai-key", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}", 0),
    ("aws-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", 0),
    ("google-key", r"\bAIza[0-9A-Za-z_-]{35}", 0),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", 0),
    ("bearer", r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{12,})", 1),
    ("url-credentials", r"://[^\s/:@]+:([^\s/@]+)@", 1),
    # KEY=value, KEY: value, "key": "value" for secret-looking keys.
    (
        "secret",
        r"(?i)[\"']?\b[\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key|private[_-]?key"
        r"|credential)[\w.-]*[\"']?\s*[:=]\s*[\"']?((?!\[redacted:)[^\s\"',;]{6,})",
        1,
    ),
    # --token value, --password=value
    (
        "secret",
        r"(?i)--?[\w-]*(?:token|secret|password|api-key|apikey)[\w-]*(?:=|\s+)((?!\[redacted:)[^\s\"']{6,})",
        1,
    ),
]

CANDIDATE = re.compile(r"[A-Za-z0-9+_=-]{24,}")


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _random_looking(text: str) -> bool:
    classes = sum(bool(re.search(pattern, text)) for pattern in ("[a-z]", "[A-Z]", "[0-9]"))
    # Git SHAs and UUIDs are lowercase hex, two classes, so they never get here.
    return classes == 3 and entropy(text) >= 3.8


class Redactor:
    def __init__(self, patterns: Iterable[Any] = (), entropy_check: bool = True):
        self.rules = [(kind, re.compile(pattern), group) for kind, pattern, group in KNOWN]
        for item in patterns:
            if isinstance(item, str):
                self.rules.append(("custom", re.compile(item), 0))
            elif isinstance(item, dict) and isinstance(item.get("pattern"), str):
                self.rules.append(
                    (
                        str(item.get("name") or "custom"),
                        re.compile(item["pattern"]),
                        int(item.get("group", 0)),
                    )
                )
            else:
                raise ValueError(
                    "ambient option 'redact.patterns' takes regex strings or {name, pattern, group}"
                )
        self.entropy_check = entropy_check

    def text(self, value: str) -> tuple[str, list[str]]:
        found: list[str] = []
        for kind, pattern, group in self.rules:

            def replace(match: re.Match, kind: str = kind, group: int = group) -> str:
                if match.group(group).startswith("[redacted:"):
                    return match.group(0)  # a second pass, on the hub, over text a device already cleaned
                found.append(kind)
                if group == 0:
                    return f"[redacted:{kind}]"
                start, end = match.span(group)
                whole = match.group(0)
                offset = match.start(0)
                return whole[: start - offset] + f"[redacted:{kind}]" + whole[end - offset :]

            value = pattern.sub(replace, value)
        if self.entropy_check:

            def random(match: re.Match) -> str:
                if not _random_looking(match.group(0)):
                    return match.group(0)
                found.append("high-entropy")
                return "[redacted:high-entropy]"

            value = CANDIDATE.sub(random, value)
        return value, found

    def value(self, value: Any, found: list[str]) -> Any:
        if isinstance(value, str):
            cleaned, kinds = self.text(value)
            found.extend(kinds)
            return cleaned
        if isinstance(value, dict):
            return {key: self.value(item, found) for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item, found) for item in value]
        return value
