"""Whether Windows Update is waiting on a reboot, from the registry. Never asks Microsoft's servers."""

from __future__ import annotations

import sys

from .base import Status

KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"


class WindowsUpdate:
    name = "windows-update"
    reboots = True

    def available(self) -> bool:
        return sys.platform == "win32"

    def read(self) -> Status:
        import winreg

        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY))
        except FileNotFoundError:
            return Status(updates=[])
        return Status(updates=[], pending="Windows Update")
