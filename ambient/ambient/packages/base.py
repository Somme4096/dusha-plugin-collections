"""What every package backend returns, and how it runs commands."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Status:
    updates: list[str]
    pending: str = ""  # what a reboot would apply


class Backend(Protocol):
    name: str
    reboots: bool

    def available(self) -> bool: ...

    def read(self) -> Status: ...


def run(*command: str, timeout: float = 120) -> str:
    env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "LC_ALL": "C"}
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=True, env=env
    ).stdout
