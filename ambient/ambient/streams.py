"""What followed sources share: the command runner, repeat folding, and the actions they return."""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("dusha.ambient")


@dataclass
class Action:
    """What a source wants recorded: `log` a row, open an `alert`, or `resolve` one."""

    kind: str
    text: str = ""
    key: Any = ""  # the alert key, or what repeats fold on
    data: dict[str, Any] = field(default_factory=dict)
    at: float | None = None
    row: str = "log"  # the kind a `log` action is stored as


def mask(text: str) -> str:
    """The same line with its numbers and ids blanked, so repeats fold together."""
    return re.sub(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8,}\b|\d+", "#", text)


class Folder:
    """Repeats of one line inside a window become a count. A line that keeps repeating doubles its window."""

    def __init__(self, minutes: float):
        self.minutes = minutes
        self.lock = threading.Lock()
        self.open: dict[Any, list[Any]] = {}  # key: [started, window seconds, count, last action]

    def add(self, key: Any, action: Action, now: float) -> list[Action]:
        """What to write now: nothing for a repeat, else the line itself."""
        with self.lock:
            out = self._expire(key, now)
            entry = self.open.get(key)
            if entry is not None:
                entry[2] += 1
                entry[3] = action
                return out
            self.open[key] = [now, self.minutes * 60, 0, action]
            return [*out, action]

    def flush(self, now: float) -> list[Action]:
        with self.lock:
            return [summary for key in list(self.open) for summary in self._expire(key, now)]

    def _expire(self, key: Any, now: float) -> list[Action]:
        entry = self.open.get(key)
        if entry is None or now - entry[0] < entry[1]:
            return []
        started, window, count, action = entry
        if not count:
            del self.open[key]
            return []
        # Still repeating: report the count and keep folding, for twice as long, up to a day.
        self.open[key] = [now, min(window * 2, 86400), 0, action]
        text = f"{action.text} (×{count} more in {window / 60:g} min)"
        return [Action("log", text, data={**action.data, "repeats": count}, at=now)]


class Stream:
    """A command followed line by line on a daemon thread, restarted after it exits if `restart`."""

    def __init__(
        self,
        name: str,
        command: Callable[[], list[str]],
        on_line: Callable[[str], None],
        restart: bool = True,
        stderr: bool = False,
    ):
        self.name = name
        self.command = command
        self.on_line = on_line
        self.restart = restart
        self.stderr = stderr
        self.stopped = threading.Event()
        self.process: subprocess.Popen | None = None
        self.thread = threading.Thread(target=self._run, name=f"ambient {name}", daemon=True)

    def start(self) -> Stream:
        self.thread.start()
        return self

    def _run(self) -> None:
        while not self.stopped.is_set():
            pipe = subprocess.PIPE
            try:
                self.process = subprocess.Popen(
                    self.command(),
                    stdout=subprocess.DEVNULL if self.stderr else pipe,
                    stderr=pipe if self.stderr else subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    errors="replace",
                )
            except OSError as error:
                logger.warning("ambient: %s could not start: %s", self.name, error)
            else:
                for line in self.process.stderr if self.stderr else self.process.stdout:
                    if self.stopped.is_set():
                        break
                    try:
                        self.on_line(line.rstrip("\n"))
                    except Exception:
                        logger.exception("ambient: %s failed on a line", self.name)
                self.process.wait()
            if not self.restart:
                return
            self.stopped.wait(30)

    def stop(self) -> None:
        self.stopped.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
