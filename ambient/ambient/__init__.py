"""ambient: the everything-log. Events and metrics from every device, merged into one picture for agents."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from dusha.client.settings import Connection, connection
from dusha.commands import arg, option
from dusha.homes import CONFIG_FILE
from dusha.hooks import render
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import gpu as _gpu
from . import packages as _packages
from . import screen as _screen
from .redact import Redactor

try:
    import psutil
except ImportError:  # metrics stay off, events still work
    psutil = None

logger = logging.getLogger("dusha.ambient")

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_recent ON observations(expires_at, created_at);
CREATE TABLE IF NOT EXISTS samples (
    device TEXT NOT NULL,
    name TEXT NOT NULL,
    ts REAL NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_series ON samples(device, name, ts);
CREATE TABLE IF NOT EXISTS rollups (
    device TEXT NOT NULL,
    name TEXT NOT NULL,
    hour INTEGER NOT NULL,
    avg REAL NOT NULL,
    min REAL NOT NULL,
    max REAL NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (device, name, hour)
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


class Observation(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    kind: str = "event"
    device: str = ""
    ttl_seconds: float | None = None
    data: dict[str, Any] = {}
    created_at: float | None = None


class Sample(BaseModel):
    device: str
    name: str
    ts: float
    value: float


class Batch(BaseModel):
    observations: list[Observation] = []
    samples: list[Sample] = []


@dataclass
class Alerts:
    cpu_percent: float = 85
    cpu_minutes: float = 10
    mem_percent: float = 90
    disk_percent: float = 90
    battery_percent: float = 15
    gpu_temp_c: float = 85
    gpu_minutes: float = 3


@dataclass
class Options:
    device: str
    forward_to: Connection | None = None
    sample_seconds: float = 60
    event_ttl_hours: float = 6
    raw_hours: float = 24
    rollup_days: float = 30
    context_hours: float = 2
    context_limit: int = 12
    # "/" is a read-only image at 100% on Fedora Atomic and friends, so home is the default.
    disks: list[str] = field(default_factory=lambda: ["~"])
    alerts: Alerts = field(default_factory=Alerts)
    max_observations: int = 5000
    redact: dict[str, Any] = field(default_factory=dict)
    screen: dict[str, Any] = field(default_factory=dict)
    # Each of these sources is on when its key is present, even as `{}`.
    packages: dict[str, Any] | None = None


def peer(value: Any, settings: Any, label: str) -> Connection | None:
    """`"proxy"` reuses the server `dusha proxy` saved, a `{url, token}` object names one directly."""
    if value in (None, "", False):
        return None
    if value == "proxy":
        if settings.home is None:
            raise ValueError(f"{label} 'proxy' needs a config home to read the saved server from")
        saved, _ = connection(settings.home / CONFIG_FILE)
        return saved
    if isinstance(value, dict) and isinstance(value.get("url"), str):
        return Connection(value["url"].rstrip("/"), str(value.get("token") or ""))
    raise ValueError(f"{label} must be 'proxy' or an object with 'url' and optional 'token'")


def parse_options(raw: dict[str, Any], settings: Any) -> Options:
    raw = dict(raw or {})
    device = str(raw.pop("device", "") or socket.gethostname().split(".")[0])
    forward_to = peer(raw.pop("forward_to", None), settings, "ambient option 'forward_to'")
    alerts = Alerts(**raw.pop("alerts", {}))
    unknown = sorted(set(raw) - set(Options.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown ambient option(s): {unknown}")
    for name, keys in {"screen": SCREEN_KEYS, **SOURCE_KEYS}.items():
        unknown = sorted(set(raw.get(name) or {}) - keys)
        if unknown:
            raise ValueError(f"unknown ambient {name} option(s): {unknown}")
    return Options(device=device, forward_to=forward_to, alerts=alerts, **raw)


SOURCE_KEYS = {
    "packages": {"backends", "every_seconds"},
}

SCREEN_KEYS = {
    "every_seconds",
    "provider",
    "model",
    "base_url",
    "key",
    "auth",
    "headers",
    "timeout_seconds",
    "shortest_side",
    "change_threshold",
    "keep_images",
    "json_mode",
    "max_tokens",
    "extra_body",
    "backend",
    "command",
}


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


class Store:
    def __init__(self, path: str):
        self.db: sqlite3.Connection | None = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def execute(self, sql: str, args: Any = ()) -> list[sqlite3.Row]:
        with self.lock:
            if self.db is None:  # an interval still running after shutdown
                return []
            with self.db:
                return self.db.execute(sql, args).fetchall()

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.lock:
            if self.db is None:
                return
            with self.db:
                self.db.executemany(sql, rows)

    def close(self) -> None:
        # Under the lock: closing while another thread is mid-query can crash the process.
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None


class ScreenWatch:
    """Capture on a timer or on demand, describe what changed, and keep the last few images on this device."""

    def __init__(self, ambient: Ambient, options: dict[str, Any], providers: Any):
        self.ambient = ambient
        self.options = options
        self.providers = providers
        self.prompt = _screen.prompt(getattr(ambient.ctx, "prompts", None))
        self.lock = threading.Lock()
        self.describer: _screen.Describer | None = None
        self.previous: dict[str, Any] | None = None
        self.last_hash: int | None = None
        self.last_at = 0.0
        self.images = ambient.ctx.data_dir / "screens"
        self.images.mkdir(exist_ok=True)
        rows = ambient.recent(device=ambient.options.device, kind="screen", hours=24, limit=1)
        if rows:
            self.previous = {key: rows[0]["data"].get(key, "") for key in _screen.FIELDS}

    @property
    def every(self) -> float:
        return float(self.options.get("every_seconds", 30))

    def _describer(self) -> _screen.Describer:
        if self.describer is None:
            session = f"ambient-screen-{self.ambient.options.device}"
            target = _screen.endpoint(self.options, self.providers, session)
            extra_body = self.options.get("extra_body") or {}
            if not isinstance(extra_body, dict):
                raise ValueError("ambient option 'screen.extra_body' must be an object")
            self.describer = _screen.Describer(
                target,
                bool(self.options.get("json_mode", True)),
                self.prompt,
                int(self.options.get("max_tokens", 1000)),
                extra_body,
            )
        return self.describer

    def tick(self) -> None:
        if self.ambient.user_away():
            return
        try:
            self.capture(force=False)
        except (_screen.ScreenError, ValueError) as error:
            logger.warning("ambient screen capture skipped: %s", error)

    def capture(self, force: bool) -> dict[str, Any]:
        with self.lock:
            png = _screen.capture(self.options.get("backend", "auto"), self.options.get("command"))
            jpeg, digest = _screen.prepare(png, int(self.options.get("shortest_side", 768)))
            threshold = int(self.options.get("change_threshold", 4))
            unchanged = self.last_hash is not None and _screen.distance(digest, self.last_hash) <= threshold
            self.last_at = time.time()
            self._keep(jpeg)
            if unchanged and not force and self.previous is not None:
                return {**self.previous, "changed": False}
            self.last_hash = digest
            description = self._describer().describe(jpeg, self.previous, _screen.frontmost_app())
            if _screen.same(self.previous, description):
                return {**description, "changed": False}
            self.previous = description
        lines = [_screen.headline(description)] + ([description["content"]] if description["content"] else [])
        stored = self.ambient.observe(
            Observation(source="screen", kind="screen", text="\n".join(lines), data=dict(description))
        )
        return {**description, "changed": True, "id": stored["id"]}

    def look(self) -> dict[str, Any]:
        """For relay's look: a fresh capture unless the last one is recent."""
        if self.previous is not None and time.time() - self.last_at < self.every:
            result = dict(self.previous)
        else:
            result = self.capture(force=True)
        return {"at": _clock(self.last_at), **result}

    def _keep(self, jpeg: bytes) -> None:
        (self.images / f"{time.time():.3f}.jpg").write_bytes(jpeg)
        keep = int(self.options.get("keep_images", 5))
        for old in sorted(self.images.glob("*.jpg"))[: -keep or None]:
            old.unlink(missing_ok=True)


class Ambient:
    def __init__(self, ctx: Any, options: Options):
        self.ctx = ctx
        self.options = options
        self.store = Store(str(ctx.data_dir / "ambient.sqlite3"))
        redact = options.redact or {}
        unknown = sorted(set(redact) - {"patterns", "entropy"})
        if unknown:
            raise ValueError(f"unknown ambient redact option(s): {unknown}")
        self.redactor = Redactor(redact.get("patterns", ()), bool(redact.get("entropy", True)))
        screen = options.screen
        self.screen = ScreenWatch(self, screen, ctx.providers) if screen.get("model") else None
        self.outbox: deque[dict[str, Any]] = deque(maxlen=500)
        self.sample_outbox: deque[dict[str, Any]] = deque(maxlen=5000)
        self.net_before: tuple[float, float, float] | None = None
        self.gpus = _gpu.Gpus()
        self.packages = None
        if options.packages is not None:
            self.packages = _packages.Watch(_packages.detect(options.packages.get("backends")))
        if psutil is not None:
            psutil.cpu_percent(interval=None)  # the first reading is always 0, prime it
        # Alerts still open from before a restart, so they can resolve.
        self.active_alerts: dict[str, int] = {
            row["key"]: row["id"]
            for row in self.store.execute(
                "SELECT id, json_extract(data_json, '$.alert_key') AS key FROM observations "
                "WHERE device=? AND kind='alert' AND expires_at>?",
                (options.device, time.time()),
            )
            if row["key"]
        }

    def get_state(self, key: str, default: Any = None) -> Any:
        rows = self.store.execute("SELECT value_json FROM state WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else default

    def set_state(self, key: str, value: Any) -> None:
        self.store.execute(
            "INSERT INTO state(key, value_json) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )

    # Events.

    def observe(self, obs: Observation) -> dict[str, Any]:
        device = obs.device or self.options.device
        now = obs.created_at or time.time()
        ttl = obs.ttl_seconds if obs.ttl_seconds is not None else self.options.event_ttl_hours * 3600
        # Every source passes here: the one place secrets get stripped, before storing or forwarding.
        found: list[str] = []
        text = self.redactor.value(obs.text, found)
        data = self.redactor.value(obs.data, found)
        if found:
            data = {**data, "redacted": sorted(set(found) | set(data.get("redacted") or []))}
        obs = obs.model_copy(update={"text": text, "data": data})
        if obs.kind == "resolved" and obs.data.get("alert_key"):
            # Expire the alert this resolves, wherever it came from.
            self.store.execute(
                "UPDATE observations SET expires_at=? WHERE device=? AND kind='alert' AND expires_at>? "
                "AND json_extract(data_json, '$.alert_key')=?",
                (now, device, now, obs.data["alert_key"]),
            )
        rows = self.store.execute(
            "INSERT INTO observations(device, source, kind, text, data_json, created_at, expires_at) "
            "VALUES(?,?,?,?,?,?,?) RETURNING id",
            (
                device,
                obs.source,
                obs.kind,
                obs.text,
                json.dumps(obs.data, ensure_ascii=False),
                now,
                now + ttl,
            ),
        )
        if device == self.options.device and self.options.forward_to is not None:
            self.outbox.append(obs.model_copy(update={"device": device, "created_at": now}).model_dump())
        self.ctx.emit(
            "ambient.observed",
            {"id": rows[0]["id"], "device": device, "source": obs.source, "kind": obs.kind, "text": obs.text},
        )
        return {"id": rows[0]["id"], "device": device}

    def recent(
        self, device: str = "", source: str = "", kind: str = "", hours: float = 6, limit: int = 30
    ) -> list[dict[str, Any]]:
        now = time.time()
        clauses, args = ["expires_at>?", "created_at>=?"], [now, now - hours * 3600]
        for column, value in (("device", device), ("source", source), ("kind", kind)):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
        rows = self.store.execute(
            f"SELECT * FROM observations WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (*args, max(1, min(limit, 500))),
        )
        return [
            {
                "id": row["id"],
                "device": row["device"],
                "source": row["source"],
                "kind": row["kind"],
                "text": row["text"],
                "data": json.loads(row["data_json"]),
                "at": _clock(row["created_at"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # Metrics.

    def store_samples(self, samples: list[Sample]) -> None:
        self.store.executemany(
            "INSERT INTO samples(device, name, ts, value) VALUES(?,?,?,?)",
            [(item.device, item.name, item.ts, item.value) for item in samples],
        )

    def read_local(self) -> dict[str, float]:
        values = self.gpus.read()
        if psutil is None:
            return values
        values["cpu.percent"] = psutil.cpu_percent(interval=None)
        values["mem.percent"] = psutil.virtual_memory().percent
        values["swap.percent"] = psutil.swap_memory().percent
        values["uptime.hours"] = round((time.time() - psutil.boot_time()) / 3600, 2)
        if hasattr(os, "getloadavg"):
            values["load.1"] = os.getloadavg()[0]
        for path in self.options.disks:
            try:
                values[f"disk.percent:{path}"] = psutil.disk_usage(os.path.expanduser(path)).percent
            except OSError:
                pass
        counters = psutil.net_io_counters()
        now = time.time()
        if self.net_before is not None:
            seconds = max(now - self.net_before[0], 1e-6)
            values["net.rx_kbps"] = round((counters.bytes_recv - self.net_before[1]) / 1024 / seconds, 2)
            values["net.tx_kbps"] = round((counters.bytes_sent - self.net_before[2]) / 1024 / seconds, 2)
        self.net_before = (now, counters.bytes_recv, counters.bytes_sent)
        battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if battery is not None:
            values["battery.percent"] = battery.percent
            values["battery.plugged"] = 1.0 if battery.power_plugged else 0.0
        return values

    def record(self, values: dict[str, float], now: float) -> None:
        samples = [
            Sample(device=self.options.device, name=name, ts=now, value=value)
            for name, value in values.items()
        ]
        if samples:
            self.store_samples(samples)
            if self.options.forward_to is not None:
                self.sample_outbox.extend(item.model_dump() for item in samples)

    def sample(self) -> None:
        now = time.time()
        values = self.read_local()
        self.record(values, now)
        if values:
            self.check_alerts(values, now)
        self.forward()
        self.maintain(now)

    def check_packages(self) -> None:
        now = time.time()
        values, events, state = self.packages.check(self.get_state("packages", {}), now)
        self.set_state("packages", state)
        self.record(values, now)
        for text in events:
            self.observe(Observation(source="packages", text=text))

    def series(self, device: str, name: str, since: float) -> list[tuple[float, float]]:
        rows = self.store.execute(
            "SELECT ts, value FROM samples WHERE device=? AND name=? AND ts>=? ORDER BY ts",
            (device, name, since),
        )
        return [(row["ts"], row["value"]) for row in rows]

    def metrics(self, device: str = "", name: str = "", hours: float = 1) -> dict[str, Any]:
        since = time.time() - hours * 3600
        clauses, args = ["ts>=?"], [since]
        hourly_clauses, hourly_args = ["hour>=?"], [int(since // 3600 * 3600)]
        for column, value in (("device", device), ("name", name)):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
                hourly_clauses.append(f"{column}=?")
                hourly_args.append(value)
        rows = self.store.execute(
            f"""SELECT device, name, MIN(value) AS min, AVG(value) AS avg, MAX(value) AS max, COUNT(*) AS n,
                       MAX(ts) AS at FROM samples WHERE {" AND ".join(clauses)} GROUP BY device, name""",
            args,
        )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            last = self.store.execute(
                "SELECT value FROM samples WHERE device=? AND name=? ORDER BY ts DESC LIMIT 1",
                (row["device"], row["name"]),
            )[0]["value"]
            result.setdefault(row["device"], {})[row["name"]] = {
                "last": last,
                "min": row["min"],
                "avg": round(row["avg"], 2),
                "max": row["max"],
                "n": row["n"],
                "at": _clock(row["at"]),
            }
        if hours > self.options.raw_hours:
            hourly = self.store.execute(
                f"""SELECT device, name, MIN(min) AS min, SUM(avg * n) / SUM(n) AS avg, MAX(max) AS max,
                           SUM(n) AS n FROM rollups WHERE {" AND ".join(hourly_clauses)}
                    GROUP BY device, name""",
                hourly_args,
            )
            for row in hourly:
                entry = result.setdefault(row["device"], {}).setdefault(
                    row["name"],
                    {"last": None, "min": row["min"], "avg": row["avg"], "max": row["max"], "n": 0},
                )
                total = entry["n"] + row["n"]
                entry["avg"] = round((entry["avg"] * entry["n"] + row["avg"] * row["n"]) / total, 2)
                entry["min"], entry["max"], entry["n"] = (
                    min(entry["min"], row["min"]),
                    max(entry["max"], row["max"]),
                    total,
                )
        return result

    def prometheus(self) -> str:
        rows = self.store.execute(
            """SELECT s.device, s.name, s.value, s.ts FROM samples s
               JOIN (SELECT device, name, MAX(ts) AS ts FROM samples GROUP BY device, name) latest
               ON latest.device=s.device AND latest.name=s.name AND latest.ts=s.ts
               ORDER BY s.device, s.name"""
        )
        lines = ["# TYPE dusha_ambient gauge"]
        for row in rows:
            device = row["device"].replace("\\", "\\\\").replace('"', '\\"')
            name = row["name"].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(
                f'dusha_ambient{{device="{device}",metric="{name}"}} {row["value"]} {int(row["ts"] * 1000)}'
            )
        alerts = len(self.recent(kind="alert", hours=24 * 365, limit=500))
        lines += ["# TYPE dusha_ambient_alerts gauge", f"dusha_ambient_alerts {alerts}"]
        return "\n".join(lines) + "\n"

    # Alerts: noticed where the metrics are read, then forwarded like any other observation.

    def check_alerts(self, values: dict[str, float], now: float) -> None:
        limits = self.options.alerts
        cpu_avg = self._sustained("cpu.percent", limits.cpu_minutes, now)
        checks = {
            "cpu": (
                cpu_avg is not None and cpu_avg >= limits.cpu_percent,
                lambda: f"CPU pinned at {cpu_avg:.0f}% for {limits.cpu_minutes:g}+ min{self._top_process()}",
            ),
            "mem": (
                values.get("mem.percent", 0) >= limits.mem_percent,
                lambda: f"memory at {values['mem.percent']:.0f}%{self._top_process('memory')}",
            ),
            "battery": (
                values.get("battery.percent", 100) <= limits.battery_percent
                and values.get("battery.plugged", 1) == 0,
                lambda: f"battery at {values['battery.percent']:.0f}% and not charging",
            ),
        }
        for path in self.options.disks:
            key = f"disk.percent:{path}"
            checks[f"disk:{path}"] = (
                values.get(key, 0) >= limits.disk_percent,
                lambda key=key, path=path: f"disk {path} at {values[key]:.0f}%",
            )
        for key in values:
            if key.startswith("gpu.temp.c:"):
                index = int(key.rsplit(":", 1)[1])
                temp = self._sustained(key, limits.gpu_minutes, now)
                checks[f"gpu:{index}"] = (
                    temp is not None and temp >= limits.gpu_temp_c,
                    lambda index=index, temp=temp: (
                        f"GPU {index} at {temp:.0f}°C for {limits.gpu_minutes:g}+ min"
                        + self._top_gpu_process(index)
                    ),
                )
        for key, (firing, describe) in checks.items():
            if firing and key not in self.active_alerts:
                self.open_alert(key, describe())
            elif not firing:
                self.resolve_alert(key)

    def open_alert(self, key: str, text: str, source: str = "ambient", data: dict | None = None) -> None:
        if key in self.active_alerts:
            return
        result = self.observe(
            Observation(
                source=source,
                kind="alert",
                text=text,
                ttl_seconds=7 * 24 * 3600,
                data={**(data or {}), "alert_key": key},
            )
        )
        self.active_alerts[key] = result["id"]
        self.ctx.emit("ambient.alert", {"device": self.options.device, "text": text, "key": key})

    def resolve_alert(self, key: str, source: str = "ambient") -> None:
        if self.active_alerts.pop(key, None) is None:
            return
        self.observe(
            Observation(
                source=source,
                kind="resolved",
                text=f"{key} back to normal",
                ttl_seconds=3600,
                data={"alert_key": key},
            )
        )

    def _sustained(self, name: str, minutes: float, now: float) -> float | None:
        """The average over the last `minutes`, or None until samples cover most of that window."""
        window = self.series(self.options.device, name, now - minutes * 60)
        if len(window) < 3 or window[-1][0] - window[0][0] < minutes * 60 * 0.8:
            return None
        return sum(value for _, value in window) / len(window)

    def _top_gpu_process(self, index: int) -> str:
        pid = self.gpus.busiest(index)
        if pid is None or psutil is None:
            return ""
        try:
            return f", mostly `{psutil.Process(pid).name()}`"
        except psutil.Error:
            return ""

    def _top_process(self, by: str = "cpu") -> str:
        if psutil is None:
            return ""
        attribute = "cpu_percent" if by == "cpu" else "memory_percent"
        try:
            processes = list(psutil.process_iter(["name", attribute]))
            if by == "cpu":
                for process in processes:
                    try:
                        process.cpu_percent(None)
                    except psutil.Error:
                        pass
                time.sleep(0.5)
                usage = []
                for process in processes:
                    try:
                        usage.append((process.cpu_percent(None), process.info["name"]))
                    except psutil.Error:
                        pass
            else:
                usage = [(process.info[attribute] or 0, process.info["name"]) for process in processes]
        except psutil.Error:
            return ""
        usage = [item for item in usage if item[1]]
        if not usage:
            return ""
        return f", mostly `{max(usage)[1]}`"

    # Upkeep and forwarding.

    def maintain(self, now: float) -> None:
        self.store.execute("DELETE FROM observations WHERE expires_at<=?", (now,))
        self.store.execute(
            "DELETE FROM observations WHERE id NOT IN (SELECT id FROM observations ORDER BY id DESC LIMIT ?)",
            (self.options.max_observations,),
        )
        cutoff = int((now - self.options.raw_hours * 3600) // 3600 * 3600)
        self.store.execute(
            """INSERT INTO rollups(device, name, hour, avg, min, max, n)
               SELECT device, name, CAST(ts / 3600 AS INTEGER) * 3600,
                      AVG(value), MIN(value), MAX(value), COUNT(*)
               FROM samples WHERE ts<? GROUP BY device, name, CAST(ts / 3600 AS INTEGER)
               ON CONFLICT(device, name, hour) DO UPDATE SET
                 avg=(rollups.avg * rollups.n + excluded.avg * excluded.n) / (rollups.n + excluded.n),
                 min=MIN(rollups.min, excluded.min), max=MAX(rollups.max, excluded.max),
                 n=rollups.n + excluded.n""",
            (cutoff,),
        )
        self.store.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
        self.store.execute("DELETE FROM rollups WHERE hour<?", (now - self.options.rollup_days * 86400,))

    def forward(self) -> None:
        target = self.options.forward_to
        if target is None or not (self.outbox or self.sample_outbox):
            return
        observations, samples = list(self.outbox), list(self.sample_outbox)
        try:
            response = httpx.post(
                target.url + "/state/v1/ambient/ingest",
                json={"observations": observations, "samples": samples},
                headers=target.headers(),
                timeout=min(target.timeout_seconds, 10.0),
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.debug(
                "ambient forward to %s failed, keeping %d items: %s",
                target.url,
                len(observations) + len(samples),
                error,
            )
            return
        for _ in observations:
            self.outbox.popleft()
        for _ in samples:
            self.sample_outbox.popleft()

    def ingest(self, batch: Batch) -> dict[str, int]:
        for obs in batch.observations:
            self.observe(obs)
        self.store_samples(batch.samples)
        return {"observations": len(batch.observations), "samples": len(batch.samples)}

    # Answers to other plugins.

    def on_presence(self, change: dict[str, Any]) -> None:
        # The hub sees every device's changes in its registry, devices would only duplicate them.
        if self.options.forward_to is not None:
            return
        self.observe(
            Observation(
                source="presence",
                kind="presence",
                device=change["device"],
                text=f"{change['device']}: {change['old']} -> {change['new']}",
            )
        )

    def user_away(self) -> bool:
        answers = list(self.ctx.collect("presence").values())
        here = (
            next((item for item in answers[0].get("devices", []) if item.get("local")), None)
            if answers
            else None
        )
        return bool(here and here.get("status") in ("idle", "locked"))

    def screen_rows(self, hours: float = 3, limit: int = 60) -> list[dict[str, Any]]:
        return list(reversed(self.recent(kind="screen", hours=hours, limit=limit)))

    def candidates(self, now: Any = None) -> list[dict[str, Any]]:
        found = [
            {
                "source": "ambient",
                "reason": "ambient_alert",
                "topic": f"Noticed on the {item['device']}: {item['text']} (since {item['at']}).",
                "score": 0.5,
                "dedup_key": f"ambient:alert:{item['device']}:{item['id']}",
                "requires_presence": "active",
            }
            for item in self.recent(kind="alert", hours=24, limit=20)
        ]
        # Screen moments: no thresholds here. Every turn in how her work is going is offered,
        # and only a judge (proactive.decide) can pick one. Without a judge they are never raised.
        before: dict[str, dict[str, Any]] = {}
        moments = []
        for row in self.screen_rows(hours=1):
            data, last = row["data"], before.get(row["device"])
            turned = last is None or data.get("status") != last["data"].get("status")
            if turned or data.get("progress") == "switched" or data.get("notable"):
                moments.append(row)
            before[row["device"]] = row
        for row in moments[-5:]:
            data = row["data"]
            topic = f"On the {row['device']} since {row['at']}: {_screen.headline(data)}."
            if data.get("notable"):
                topic += f" Notable: {data['notable']}."
            found.append(
                {
                    "source": "ambient",
                    "reason": f"screen_{data.get('status') or 'change'}",
                    "topic": topic,
                    "context": {"content": data.get("content", ""), "title": data.get("title", "")},
                    "score": 0.3,
                    "dedup_key": f"ambient:screen:{row['device']}:{row['id']}",
                    "requires_presence": "active",
                    "needs_judge": True,
                }
            )
        return found

    def timeline(self, hours: float = 3, max_chars: int = 40000) -> list[str]:
        """Everything recent, oldest first, one line each, for a judge. Newest lines win the char budget."""
        lines: list[str] = []
        used = 0
        for item in self.recent(hours=hours, limit=500):
            line = f"{item['at']} {item['device']} {item['source']}: {item['text'].replace(chr(10), ' / ')}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
        return list(reversed(lines))

    def context(self, request: Any) -> Any:
        alerts = self.recent(kind="alert", hours=24 * 7, limit=10)
        events = [
            item
            for item in self.recent(hours=self.options.context_hours, limit=self.options.context_limit * 3)
            if item["kind"] not in ("alert", "screen")
        ][: self.options.context_limit]
        screens = self.screen_rows(hours=self.options.context_hours, limit=6)
        if not alerts and not events and not screens:
            return None
        data: dict[str, Any] = {}
        if alerts:
            data["alerts"] = [f"{item['device']}: {item['text']} (since {item['at']})" for item in alerts]
        if screens:
            latest = screens[-1]
            data["screen_now"] = f"{latest['at']} {latest['device']}: {latest['text']}"
            data["screen_before"] = [
                f"{item['at']} {item['device']}: {item['text'].splitlines()[0]}" for item in screens[:-1]
            ]
        if events:
            data["recent"] = [
                f"{item['at']} {item['device']} {item['source']}: {item['text']}" for item in reversed(events)
            ]
        return render("ambient", "<ambient>", data, "</ambient>")


def apply(ctx: Any, config: dict[str, Any]) -> None:
    options = parse_options(config, ctx.settings)
    ambient = Ambient(ctx, options)
    if psutil is None:
        logger.warning("ambient: psutil is not installed, so metrics are off and only events are recorded")
    ctx.on("ambient.observe", lambda obs: ambient.observe(Observation(**obs)))
    ctx.on("presence.changed", ambient.on_presence)
    ctx.on("proactive.candidates", ambient.candidates)
    ctx.on("context", ambient.context)
    ctx.interval(options.sample_seconds, ambient.sample)
    ctx.on("ambient.timeline", lambda hours=3, max_chars=40000: ambient.timeline(hours, max_chars))
    if ambient.screen is not None:
        watch = ambient.screen
        ctx.interval(watch.every, watch.tick)
        ctx.on("presence.capabilities", lambda: ["look"])
        ctx.on("screenshot.look", lambda body=None: watch.look())
    if ambient.packages is not None:
        ctx.interval(options.packages.get("every_seconds", 3 * 3600), ambient.check_packages)
    ctx.on_dispose(ambient.store.close)
    ctx.on_dispose(ambient.gpus.close)
    _routes(ctx.api, ambient)
    _tools(ctx.mcp, ambient)
    _commands(ctx.cli, ambient)


def _routes(router: APIRouter, ambient: Ambient) -> None:
    @router.post("/state/v1/ambient", tags=["ambient"])
    def observe(obs: Observation) -> dict[str, Any]:
        return ambient.observe(obs)

    @router.post("/state/v1/ambient/ingest", tags=["ambient"])
    def ingest(batch: Batch) -> dict[str, int]:
        return ambient.ingest(batch)

    @router.get("/state/v1/ambient", tags=["ambient"])
    def recent(
        device: str = "",
        source: str = "",
        kind: str = "",
        hours: float = 6,
        limit: int = Query(30, ge=1, le=500),
    ) -> dict[str, Any]:
        return {"observations": ambient.recent(device, source, kind, hours, limit)}

    @router.get("/state/v1/ambient/metrics", tags=["ambient"])
    def metrics(device: str = "", name: str = "", hours: float = 1) -> dict[str, Any]:
        return {"metrics": ambient.metrics(device, name, hours)}

    @router.get("/state/v1/ambient/prometheus", tags=["ambient"], response_class=PlainTextResponse)
    def prometheus() -> str:
        return ambient.prometheus()

    if ambient.screen is not None:
        watch = ambient.screen

        @router.post("/state/v1/screenshot", tags=["ambient"])
        def screenshot() -> dict[str, Any]:
            try:
                return watch.look()
            except (_screen.ScreenError, ValueError) as error:
                raise HTTPException(status_code=503, detail=str(error)) from error


def _tools(mcp: Any, ambient: Ambient) -> None:
    mcp.tool(
        "ambient_recent",
        lambda device="", source="", hours=6.0, limit=30: ambient.recent(device, source, "", hours, limit),
        help="What happened recently across her devices: failed commands, commits, alerts, presence changes",
        args=[
            option("device", default=""),
            option("source", default=""),
            option("hours", float, default=6.0),
            option("limit", int, default=30),
        ],
    )
    mcp.tool(
        "ambient_metrics",
        lambda device="", name="", hours=1.0: ambient.metrics(device, name, hours),
        help="Resource metrics per device (cpu, memory, disk, network, battery): last, min, avg, max",
        args=[
            option("device", default=""),
            option("name", default="", help="e.g. cpu.percent"),
            option("hours", float, default=1.0),
        ],
    )


def _commands(cli: Any, ambient: Ambient) -> None:
    group = cli.group("ambient", "Events and metrics from every device")
    group.command(
        "recent",
        lambda device, source, hours, limit: ambient.recent(device, source, "", hours, limit),
        help="Show recent observations",
        args=[
            option("device", default=""),
            option("source", default=""),
            option("hours", float, default=6.0),
            option("limit", int, default=30),
        ],
    )
    group.command(
        "observe",
        lambda source, text: ambient.observe(Observation(source=source, text=text)),
        help="Record an observation",
        args=[arg("source"), arg("text")],
    )
    group.command(
        "metrics",
        lambda device, name, hours: ambient.metrics(device, name, hours),
        help="Summarize metrics",
        args=[option("device", default=""), option("name", default=""), option("hours", float, default=1.0)],
    )
