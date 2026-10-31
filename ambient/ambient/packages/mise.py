"""Tool versions mise manages, compared against what each tool's config allows."""

from __future__ import annotations

import json
import os
import shutil

from .base import Status, run

# mise installed through Homebrew or a user prefix is often missing from a service's PATH.
KNOWN = (
    "/home/linuxbrew/.linuxbrew/bin/mise",
    "/opt/homebrew/bin/mise",
    "/usr/local/bin/mise",
    "~/.local/bin/mise",
)


class Mise:
    name = "mise"
    reboots = False

    def binary(self) -> str | None:
        return shutil.which("mise") or next(
            (path for path in map(os.path.expanduser, KNOWN) if os.access(path, os.X_OK)), None
        )

    def available(self) -> bool:
        return self.binary() is not None

    def read(self) -> Status:
        # `outdated` asks each tool's upstream release API for the newest version its
        # config allows; mise's own cache keeps repeats cheap. `--bump` would ignore
        # the configured range, so the plain command mirrors `mise upgrade`.
        outdated = json.loads(run(self.binary() or "mise", "outdated", "--json"))
        updates = []
        for name, item in outdated.items():
            updates.append(f"{item.get('name', name)} {item.get('latest', '')}".strip())
        return Status(updates)
