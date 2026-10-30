"""Files created, modified, moved, downloaded, and deleted under a few folders, batched per folder."""

from __future__ import annotations

import errno
import fnmatch
import logging
import os
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .streams import Action

logger = logging.getLogger("dusha.ambient")

KEYS = {"roots", "exclude", "window_minutes", "list_up_to"}
ROOTS = ["~/Documents", "~/Downloads", "~/Desktop", "~/Pictures"]
PARTIAL = ["*.part", "*.crdownload", "*.download", "*.partial"]
TEMP = ["*.swp", "*.swx", "*~", ".#*", ".goutputstream-*", "*.tmp", ".~lock.*"]
EXCLUDE = [
    *PARTIAL,
    *TEMP,
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "target",
    "dist",
    "build",
    ".cache",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
]
ORDER = ("created", "downloaded", "modified", "moved", "deleted")


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _shown(path: str) -> str:
    home = os.path.realpath(Path.home())
    return "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path


class Changes:
    """One window of changes, kept per path so a file created and then edited is just created."""

    def __init__(self, roots: list[str], exclude: list[str], minutes: float, list_up_to: int):
        self.roots = sorted(roots, key=len, reverse=True)
        self.exclude = exclude
        self.window = minutes * 60
        self.list_up_to = list_up_to
        self.lock = threading.Lock()
        self.items: dict[str, list[Any]] = {}  # path: [kind, moved from, is dir]
        self.started: float | None = None

    def excluded(self, path: str) -> bool:
        root = next((root for root in self.roots if path.startswith(root + os.sep)), None)
        parts = Path(path[len(root) + 1 :] if root else path).parts
        return any(_matches(part, self.exclude) for part in parts)

    def add(self, kind: str, path: str, src: str | None = None, is_dir: bool = False) -> None:
        with self.lock:
            if self.started is None:
                self.started = time.time()
            if kind == "moved":
                self._moved(src or "", path, is_dir)
            elif not self.excluded(path):
                getattr(self, f"_{kind}")(path, is_dir)

    def _created(self, path: str, is_dir: bool) -> None:
        before = self.items.get(path)
        if before is None or before[0] == "deleted":
            self.items[path] = ["modified" if before else "created", None, is_dir]

    def _modified(self, path: str, is_dir: bool) -> None:
        if not is_dir and self.items.get(path) is None:
            self.items[path] = ["modified", None, False]

    def _deleted(self, path: str, is_dir: bool) -> None:
        before = self.items.pop(path, None)
        if before is None or before[0] == "modified":
            self.items[path] = ["deleted", None, is_dir]
        elif before[0] == "moved":
            self.items[before[1]] = ["deleted", None, is_dir]

    def _moved(self, src: str, dst: str, is_dir: bool) -> None:
        name = os.path.basename(src)
        if self.excluded(dst):
            if not self.excluded(src):
                self._deleted(src, is_dir)
            return
        before = self.items.pop(src, None)
        if before is not None and before[0] in ("created", "downloaded"):
            self.items[dst] = [before[0], None, is_dir]
        elif _matches(name, PARTIAL):
            self.items[dst] = ["downloaded", None, is_dir]
        elif _matches(name, TEMP) or self.items.get(dst, [None])[0] == "deleted":
            # An editor's save: written beside, then renamed over the file.
            if self.items.get(dst, [None])[0] not in ("created", "downloaded"):
                self.items[dst] = ["modified", None, is_dir]
        elif self.excluded(src):
            self.items[dst] = ["created", None, is_dir]
        else:
            self.items[dst] = ["moved", before[1] if before and before[0] == "moved" else src, is_dir]

    def group(self, path: str) -> str:
        root = next((root for root in self.roots if path == root or path.startswith(root + os.sep)), None)
        if root is None:
            return os.path.dirname(path)
        parts = Path(path[len(root) + 1 :]).parts
        return os.path.join(root, parts[0]) if len(parts) > 1 else root

    def flush(self, now: float, force: bool = False) -> list[Action]:
        with self.lock:
            if self.started is None or (not force and now - self.started < self.window):
                return []
            items, self.items, self.started = self.items, {}, None
        groups: dict[str, list[tuple[str, str, str | None, bool]]] = {}
        for path, (kind, src, is_dir) in items.items():
            groups.setdefault(self.group(path), []).append((kind, path, src, is_dir))
        out = []
        for group, changes in sorted(groups.items()):
            changes.sort(key=lambda change: (ORDER.index(change[0]), change[1]))
            counts = Counter(change[0] for change in changes)

            def name(path: str, is_dir: bool = False, group: str = group) -> str:
                inside = path.startswith(group + os.sep)
                return (os.path.relpath(path, group) if inside else _shown(path)) + ("/" if is_dir else "")

            if len(changes) <= self.list_up_to:
                listed = [
                    f"{kind} {name(src)} → {name(path, is_dir)}"
                    if kind == "moved"
                    else f"{kind} {name(path, is_dir)}"
                    for kind, path, src, is_dir in changes
                ]
                text = f"{_shown(group)}: " + ", ".join(listed)
            else:
                summary = ", ".join(f"{counts[kind]} {kind}" for kind in ORDER if counts[kind])
                examples = ", ".join(name(path, is_dir) for _, path, _, is_dir in changes[:3])
                text = f"{_shown(group)}: {summary} ({examples}, …)"
            data = {"folder": _shown(group), "counts": dict(counts)}
            out.append(Action("log", text[:1000], data=data, at=now, row="file"))
        return out


class Inotify:
    """Linux: one inotify watch per folder, skipping excluded folders before watching them."""

    def __init__(self, changes: Changes, roots: list[str]):
        import inotify_simple

        self.flags = inotify_simple.flags
        self.mask = (
            self.flags.CREATE
            | self.flags.CLOSE_WRITE
            | self.flags.DELETE
            | self.flags.MOVED_FROM
            | self.flags.MOVED_TO
            | self.flags.DONT_FOLLOW
            | self.flags.EXCL_UNLINK
        )
        self.inotify = inotify_simple.INotify()
        self.changes = changes
        self.paths: dict[int, str] = {}
        self.full = False
        self.stopped = threading.Event()
        for root in roots:
            self.watch_tree(root)
        self.thread = threading.Thread(target=self._run, name="ambient files", daemon=True)

    def watch_tree(self, top: str, report: bool = False) -> None:
        for folder, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not _matches(d, self.changes.exclude)]
            try:
                self.paths[self.inotify.add_watch(folder, self.mask)] = folder
            except OSError as error:
                if error.errno == errno.ENOSPC and not self.full:
                    self.full = True
                    logger.warning(
                        "ambient: out of inotify watches at %s, raise fs.inotify.max_user_watches", folder
                    )
                continue
            if report:  # created before its folder's watch existed
                for item in [*dirs, *files]:
                    self.changes.add("created", os.path.join(folder, item), is_dir=item in dirs)

    def start(self) -> Inotify:
        self.thread.start()
        return self

    def _run(self) -> None:
        flags = self.flags
        while not self.stopped.is_set():
            try:
                events = self.inotify.read(timeout=1000, read_delay=50)
            except OSError:
                return
            moves: dict[int, str] = {}
            for event in events:
                if event.mask & flags.Q_OVERFLOW:
                    logger.warning("ambient: file events overflowed, some changes were missed")
                    continue
                if event.mask & flags.IGNORED:
                    self.paths.pop(event.wd, None)
                    continue
                folder = self.paths.get(event.wd)
                if folder is None:
                    continue
                path = os.path.join(folder, event.name)
                is_dir = bool(event.mask & flags.ISDIR)
                if event.mask & flags.MOVED_FROM:
                    moves[event.cookie] = path
                elif event.mask & flags.MOVED_TO:
                    src = moves.pop(event.cookie, None)
                    if src is None:
                        self.changes.add("created", path, is_dir=is_dir)
                    else:
                        self.changes.add("moved", path, src, is_dir)
                        if is_dir:
                            self._renamed(src, path)
                    if is_dir and src is None and not _matches(event.name, self.changes.exclude):
                        self.watch_tree(path)
                elif event.mask & flags.CREATE:
                    self.changes.add("created", path, is_dir=is_dir)
                    if is_dir and not _matches(event.name, self.changes.exclude):
                        self.watch_tree(path, report=True)
                elif event.mask & flags.CLOSE_WRITE:
                    self.changes.add("modified", path)
                elif event.mask & flags.DELETE:
                    self.changes.add("deleted", path, is_dir=is_dir)
            for src in moves.values():  # moved out of everything watched
                self.changes.add("deleted", src)

    def _renamed(self, old: str, new: str) -> None:
        for wd, path in list(self.paths.items()):
            if path == old or path.startswith(old + os.sep):
                self.paths[wd] = new + path[len(old) :]

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=3)
        self.inotify.close()


class FSEvents:
    """macOS and Windows: watchdog, one subscription per root whatever its size."""

    def __init__(self, changes: Changes, roots: list[str]):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        add = changes.add

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: Any) -> None:
                src = os.fsdecode(event.src_path)
                if event.event_type == "moved":
                    add("moved", os.fsdecode(event.dest_path), src, event.is_directory)
                elif event.event_type in ("created", "deleted"):
                    add(event.event_type, src, is_dir=event.is_directory)
                elif event.event_type == "modified" and not event.is_directory:
                    add("modified", src)

        self.observer = Observer()
        for root in roots:
            self.observer.schedule(Handler(), root, recursive=True)

    def start(self) -> FSEvents:
        self.observer.start()
        return self

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join(timeout=3)


class Files:
    def __init__(self, options: dict[str, Any]):
        roots = [os.path.realpath(os.path.expanduser(root)) for root in options.get("roots") or ROOTS]
        self.roots = [root for root in roots if os.path.isdir(root)]
        exclude = [*EXCLUDE, *(options.get("exclude") or ())]
        minutes = float(options.get("window_minutes", 5))
        self.changes = Changes(self.roots, exclude, minutes, int(options.get("list_up_to", 5)))
        self.watcher: Inotify | FSEvents | None = None

    def start(self) -> None:
        backend: Callable[[Changes, list[str]], Any] = Inotify if sys.platform == "linux" else FSEvents
        try:
            self.watcher = backend(self.changes, self.roots).start()
        except ImportError:
            needs = "watchdog" if backend is FSEvents else "inotify_simple"
            logger.warning("ambient: the files source needs %s, so it stays off", needs)

    def flush(self, now: float) -> list[Action]:
        return self.changes.flush(now)

    def stop(self) -> list[Action]:
        if self.watcher is not None:
            self.watcher.stop()
        return self.changes.flush(time.time(), force=True)
