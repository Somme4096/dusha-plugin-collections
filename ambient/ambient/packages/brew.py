from __future__ import annotations

import json
import os
import shutil

from .base import Status, run

# A user service often runs without brew's shellenv on PATH.
KNOWN = ("/home/linuxbrew/.linuxbrew/bin/brew", "/opt/homebrew/bin/brew", "/usr/local/bin/brew")


class Brew:
    name = "brew"
    reboots = False

    def binary(self) -> str | None:
        return shutil.which("brew") or next((path for path in KNOWN if os.access(path, os.X_OK)), None)

    def available(self) -> bool:
        return self.binary() is not None

    def read(self) -> Status:
        # HOMEBREW_NO_AUTO_UPDATE (set by run) keeps this to what the last `brew update` left on disk.
        outdated = json.loads(run(self.binary() or "brew", "outdated", "--json=v2"))
        items = [*outdated.get("formulae", []), *outdated.get("casks", [])]
        return Status([f"{item['name']} {item.get('current_version', '')}".strip() for item in items])
