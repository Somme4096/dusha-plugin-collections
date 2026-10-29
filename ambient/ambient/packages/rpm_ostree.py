from __future__ import annotations

import json
import os
import shutil
import sys

from .base import Status, run


class RpmOstree:
    """The OS image. Updates show only once `AutomaticUpdatePolicy=check` caches them."""

    name = "rpm-ostree"
    reboots = True

    def available(self) -> bool:
        booted = os.path.exists("/run/ostree-booted")
        return sys.platform == "linux" and booted and bool(shutil.which("rpm-ostree"))

    def read(self) -> Status:
        status = json.loads(run("rpm-ostree", "status", "--json"))
        deployments = status.get("deployments") or []
        pending = ""
        # The first deployment is what the next boot runs, so a non-booted one there is waiting.
        if deployments and not deployments[0].get("booted"):
            pending = deployments[0].get("version") or deployments[0].get("checksum", "")[:12]
        cached = status.get("cached-update") or {}
        version = cached.get("version") or cached.get("checksum", "")[:12]
        return Status([f"os {version}"] if version and version != pending else [], pending)
