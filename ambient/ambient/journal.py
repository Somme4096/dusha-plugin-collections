"""The systemd journal: errors, failed units, crashes, OOM kills, auth failures, and SELinux denials."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .streams import Action, Folder, mask

UNIT_FAILED = "d9b373ed55a64feb8242e02dbe79a49c"
JOB_DONE = "39f53479d3a045ac8e11786248231fbf"
JOB_FAILED = "be02cf6855d2428ba40df7e9d022f03d"
COREDUMP = "fc2e22bc6ee647b6b90729ab34a250b1"

AUTH = re.compile(r"authentication failure|Failed password|Invalid user|incorrect password attempt")
KERNEL_OOM = re.compile(r"Out of memory: Killed process \d+ \((.+?)\)")
AVC = re.compile(
    r"denied\s+\{ ([^}]+) \}.*?comm=\"([^\"]*)\""
    r".*?scontext=[^:\s]+:[^:\s]+:([^:\s]+)\S* tcontext=[^:\s]+:[^:\s]+:([^:\s]+)\S* tclass=(\S+)"
)
# Units that get a fresh name each run, so an alert on them could never resolve.
TRANSIENT = re.compile(r"^(dbus-:|app-|run-)|\.scope$")

KEYS = {"priority", "exclude_units", "exclude_patterns", "fold_minutes", "resume_hours", "settle_seconds"}


def _text(value: Any) -> str:
    if isinstance(value, list):  # journald sends non-UTF-8 fields as byte arrays
        return bytes(value).decode(errors="replace")
    return str(value or "")


def _name(entry: dict[str, Any]) -> str:
    """Who said it: the unit for services, the program for everything else."""
    unit = entry.get("_SYSTEMD_USER_UNIT") or entry.get("_SYSTEMD_UNIT") or ""
    if unit.endswith(".service") and not TRANSIENT.search(unit) and not unit.startswith("user@"):
        return unit
    return entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or unit or "journal"


class Journal:
    def __init__(self, options: dict[str, Any], resume: dict[str, Any] | None):
        self.priority = int(options.get("priority", 3))
        self.exclude_units = set(options.get("exclude_units") or ())
        self.exclude = [re.compile(pattern) for pattern in options.get("exclude_patterns") or ()]
        self.resume_seconds = float(options.get("resume_hours", 24)) * 3600
        self.folder = Folder(float(options.get("fold_minutes", 60)))
        self.settle_seconds = float(options.get("settle_seconds", 60))
        self.settling: dict[str, float] = {}  # unit alert key: when it last started
        self.position = dict(resume or {})

    def command(self) -> list[str]:
        """Resume after the saved cursor, but never from further back than `resume_hours`."""
        base = ["journalctl", "--follow", "--output=json", "--no-pager"]
        floor = time.time() - self.resume_seconds
        if self.position.get("cursor") and self.position.get("at", 0) >= floor:
            return [*base, f"--after-cursor={self.position['cursor']}"]
        return [*base, "--boot", f"--since=@{int(floor)}"]

    def flush(self, now: float) -> list[Action]:
        """Folded repeats, and resolves for units that stayed up for `settle_seconds` after starting."""
        settled = [key for key, at in list(self.settling.items()) if now - at >= self.settle_seconds]
        for key in settled:
            self.settling.pop(key, None)
        return [*self.folder.flush(now), *(Action("resolve", key=key, at=now) for key in settled)]

    def handle(self, line: str) -> list[Action]:
        try:
            entry = json.loads(line)
        except ValueError:
            return []
        at = int(entry.get("__REALTIME_TIMESTAMP") or time.time() * 1e6) / 1e6
        if entry.get("__CURSOR"):
            self.position = {"cursor": entry["__CURSOR"], "at": at}
        action = self.classify(entry, at)
        if action is None:
            return []
        if action.kind == "log":
            return self.folder.add(action.key, action, at)
        return [action]

    def classify(self, entry: dict[str, Any], at: float) -> Action | None:
        if entry.get("CONTAINER_NAME"):  # the container source follows these
            return None
        message = _text(entry.get("MESSAGE")).strip()
        first = message.splitlines()[0] if message else ""
        unit = entry.get("UNIT") or entry.get("USER_UNIT") or ""
        name = _name(entry)
        if {unit, name, entry.get("_SYSTEMD_UNIT"), entry.get("_SYSTEMD_USER_UNIT")} & self.exclude_units:
            return None
        if any(pattern.search(message) for pattern in self.exclude):
            return None
        priority = int(entry.get("PRIORITY", 6))
        message_id = entry.get("MESSAGE_ID")
        data = {"unit": unit or name, "priority": priority}

        transient = bool(TRANSIENT.search(unit))
        if message_id == UNIT_FAILED and unit:
            result = entry.get("UNIT_RESULT") or "failed"
            if transient:
                return self._log(f"{unit} failed ({result})", data, at)
            self.settling.pop(f"unit:{unit}", None)
            return Action("alert", f"`{unit}` failed ({result})", key=f"unit:{unit}", data=data, at=at)
        if message_id == JOB_DONE and unit and entry.get("JOB_RESULT") == "done" and not transient:
            # A simple service logs this before it can fail, so resolving waits until it stays up.
            self.settling[f"unit:{unit}"] = at
            return None
        if message_id == JOB_FAILED:  # the alert above already says so
            return None
        if message_id == COREDUMP:
            comm = _text(entry.get("COREDUMP_COMM")) or "a process"
            signal = _text(entry.get("COREDUMP_SIGNAL_NAME"))
            text = f"{comm} crashed ({signal})" if signal else f"{comm} crashed"
            return self._log(text, data, at, key=("core", comm))
        if killed := KERNEL_OOM.search(first):
            return self._log(f"kernel OOM killed {killed.group(1)}", data, at, key=("oom", killed.group(1)))
        if name == "systemd-oomd" and first.startswith("Killed"):
            return self._log(f"systemd-oomd: {first}", data, at)
        if entry.get("_TRANSPORT") == "audit":
            if denied := AVC.search(message):
                perms, comm, source, target, tclass = denied.groups()
                text = f"SELinux denied {{{perms}}} on {tclass} for {comm} ({source} → {target})"
                return self._log(text, data, at, key=("avc", perms, comm, source, target, tclass))
            return None
        if str(entry.get("SYSLOG_FACILITY")) in ("4", "10") and AUTH.search(first):
            return self._log(f"auth failure: {name}: {first}", data, at, key=("auth", name))
        if priority <= self.priority and first:
            return self._log(f"{name}: {first}", data, at)
        return None

    def _log(self, text: str, data: dict[str, Any], at: float, key: Any = None) -> Action:
        return Action("log", text[:300], key=key or (data["unit"], mask(text)), data=data, at=at)
