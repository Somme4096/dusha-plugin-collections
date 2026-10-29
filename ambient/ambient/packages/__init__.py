"""Package status per backend, read from local state only. Fetching and upgrading are left to the system."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from . import brew, flatpak, macos, rpm_ostree
from .base import Backend

logger = logging.getLogger("dusha.ambient")

BACKENDS: list[Backend] = [
    rpm_ostree.RpmOstree(),
    flatpak.Flatpak("system"),
    flatpak.Flatpak("user"),
    brew.Brew(),
    macos.SoftwareUpdate(),
]


def detect(names: list[str] | None = None) -> list[Backend]:
    known = {backend.name for backend in BACKENDS}
    unknown = sorted(set(names or ()) - known)
    if unknown:
        raise ValueError(f"unknown ambient packages backend(s): {unknown}, known: {sorted(known)}")
    return [
        backend for backend in BACKENDS if (names is None or backend.name in names) and backend.available()
    ]


def _names(updates: list[str], shown: int = 5) -> str:
    names = [item.split(" ", 1)[0] for item in updates[:shown]]
    return ", ".join(names) + (", …" if len(updates) > shown else "")


class Watch:
    def __init__(self, backends: list[Backend]):
        self.backends = backends

    def check(self, before: dict[str, Any], now: float) -> tuple[dict[str, float], list[str], dict[str, Any]]:
        """Metrics, events for what changed since `before`, and the state to keep for next time."""
        values: dict[str, float] = {}
        events: list[str] = []
        after: dict[str, Any] = {}
        pending = False
        for backend in self.backends:
            old = before.get(backend.name) or {}
            try:
                status = backend.read()
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                logger.warning("ambient: reading %s packages failed: %s", backend.name, error)
                if old:
                    after[backend.name] = old
                continue
            values[f"pkg.updates:{backend.name}"] = float(len(status.updates))
            if set(status.updates) - set(old.get("updates") or ()):
                events.append(f"{backend.name}: {len(status.updates)} updates ({_names(status.updates)})")
            if status.pending and status.pending != old.get("pending"):
                events.append(f"{backend.name}: {status.pending} staged, reboot to apply")
            pending = pending or bool(status.pending)
            after[backend.name] = {"updates": status.updates, "pending": status.pending}
        if any(backend.reboots for backend in self.backends):
            since = (before.get("pending_since") or now) if pending else None
            after["pending_since"] = since
            values["pkg.reboot_pending"] = round((now - since) / 86400, 2) if since else 0.0
        return values, events, after
