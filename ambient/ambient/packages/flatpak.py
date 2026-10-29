from __future__ import annotations

import shutil

from .base import Status, run


class Flatpak:
    def __init__(self, scope: str):
        self.scope = scope
        self.name = f"flatpak-{scope}"
        self.reboots = False

    def available(self) -> bool:
        return bool(shutil.which("flatpak"))

    def read(self) -> Status:
        # --cached reads what the update timers last fetched, never the network.
        scope = f"--{self.scope}"
        out = run("flatpak", "remote-ls", "--updates", "--cached", scope, "--columns=application,version")
        return Status([" ".join(line.split("\t")).strip() for line in out.splitlines() if line.strip()])
