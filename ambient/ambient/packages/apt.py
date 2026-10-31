"""Debian/Ubuntu upgrades the package lists on disk know about."""

from __future__ import annotations

import os
import shutil

from .base import Status, run

REBOOT_REQUIRED = "/run/reboot-required"


class Apt:
    name = "apt"
    reboots = True

    def available(self) -> bool:
        return bool(shutil.which("apt"))

    def read(self) -> Status:
        # `--upgradable` reads /var/lib/apt/lists, never the network: the answer is
        # only as fresh as the last `apt update` left it.
        out = run("apt", "list", "--upgradable")
        updates = []
        for line in out.splitlines():
            # `package/repo version arch [upgradable from: old]`
            parts = line.split()
            if len(parts) < 2 or line.startswith("Listing"):
                continue
            updates.append(f"{parts[0].split('/', 1)[0]} {parts[1]}")
        pending = "reboot" if os.path.exists(REBOOT_REQUIRED) else ""
        return Status(updates, pending)
