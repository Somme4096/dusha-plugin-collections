from __future__ import annotations

import os
import plistlib
import sys

from .base import Status

# macOS refreshes this in the background, while `softwareupdate -l` asks Apple and can take a minute.
PLIST = "/Library/Preferences/com.apple.SoftwareUpdate.plist"


class SoftwareUpdate:
    name = "macos"
    reboots = False

    def available(self) -> bool:
        return sys.platform == "darwin" and os.path.exists(PLIST)

    def read(self) -> Status:
        with open(PLIST, "rb") as file:
            recommended = plistlib.load(file).get("RecommendedUpdates") or []
        names = [(item.get("Display Name") or item.get("Identifier") or "?") for item in recommended]
        versions = [item.get("Display Version") or "" for item in recommended]
        return Status([f"{name} {version}".strip() for name, version in zip(names, versions, strict=True)])
