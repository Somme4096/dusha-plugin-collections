"""DNS lookups from systemd-resolved, which processes talk to which hosts, and newly listening ports."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from typing import Any

from .streams import Action

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("dusha.ambient")

KEYS = {"dns", "connections", "window_minutes", "new_after_days", "learn_hours", "ignore"}
MONITOR = "/run/systemd/resolve/io.systemd.Resolve.Monitor"
LOCAL = (".local", ".arpa", ".lan", ".home", ".internal", ".localdomain")
SECOND_LEVEL = {"co", "com", "net", "org", "ac", "gov", "edu", "ne", "or", "go"}
EPHEMERAL = 32768  # ports from here up are picked at random, so they are keyed by process alone
# CDNs, ad and analytics hosts, and connectivity checks: counted as background, never "new".
NOISE = {
    "akamai.net",
    "akamaiedge.net",
    "akamaihd.net",
    "akamaized.net",
    "amazonaws.com",
    "azureedge.net",
    "cloudflare.net",
    "cloudfront.net",
    "doubleclick.net",
    "edgekey.net",
    "edgesuite.net",
    "fastly.net",
    "ggpht.com",
    "google-analytics.com",
    "googleapis.com",
    "googlesyndication.com",
    "googletagmanager.com",
    "googleusercontent.com",
    "googlevideo.com",
    "gstatic.com",
    "jsdelivr.net",
    "msftconnecttest.com",
    "ntp.org",
    "sentry.io",
    "trafficmanager.net",
}


def site(name: str) -> str:
    """The registrable part of a name, near enough without the public suffix list."""
    labels = name.rstrip(".").lower().split(".")
    keep = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


class Network:
    def __init__(
        self, options: dict[str, Any], seen: Callable[[str, str, float], float | None], since: float
    ):
        self.dns = bool(options.get("dns", True)) and os.path.exists(MONITOR)
        self.connections = bool(options.get("connections", True)) and psutil is not None
        self.window = float(options.get("window_minutes", 60)) * 60
        self.new_after = float(options.get("new_after_days", 30)) * 86400
        self.learn_until = since + float(options.get("learn_hours", 24)) * 3600
        self.ignore = {item.lower() for item in options.get("ignore") or ()}
        self.seen = seen
        self.lock = threading.Lock()
        self.started = time.time()
        self.lookups: Counter[str] = Counter()
        self.background = 0
        self.talkers: dict[str, set[str]] = {}
        self.known: set[str] = set()
        self.listening: set[str] | None = None
        self.addresses: OrderedDict[str, str] = OrderedDict()
        self.names: dict[int, str] = {}
        self.denied = False
        self.pending: list[Action] = []
        self.stopped = threading.Event()
        self.sock: socket.socket | None = None
        self.thread = threading.Thread(target=self._follow, name="ambient dns", daemon=True)

    def start(self) -> None:
        if self.dns:
            self.thread.start()

    # DNS: resolved's varlink monitor, the socket `resolvectl monitor` reads.

    def _follow(self) -> None:
        while not self.stopped.is_set():
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(MONITOR)
                request = {
                    "method": "io.systemd.Resolve.Monitor.SubscribeQueryResults",
                    # Never interactive: resolved would pop a password window for every subscription.
                    "parameters": {"allowInteractiveAuthentication": False},
                    "more": True,
                }
                self.sock.sendall(json.dumps(request).encode() + b"\0")
                buffer = b""
                while not self.stopped.is_set():
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\0" in buffer:
                        message, buffer = buffer.split(b"\0", 1)
                        reply = json.loads(message)
                        if reply.get("error"):  # polkit said no, which won't change
                            logger.warning(
                                "ambient: DNS lookups stay off, resolved refused (%s). Allow "
                                "org.freedesktop.resolve1.subscribe-query-results for this user in polkit.",
                                reply["error"],
                            )
                            return
                        self.lookup(reply.get("parameters") or {})
            except (OSError, ValueError) as error:
                if not self.stopped.is_set():
                    logger.warning("ambient: the DNS monitor dropped: %s", error)
            finally:
                if self.sock is not None:
                    self.sock.close()
            self.stopped.wait(30)

    def lookup(self, result: dict[str, Any]) -> None:
        names = {item.get("name", "") for item in result.get("question") or [] if item.get("type") != 12}
        for answer in result.get("answer") or []:
            record = answer.get("rr") or {}
            if record.get("address"):
                try:
                    address = str(ipaddress.ip_address(bytes(record["address"])))
                except ValueError:
                    continue
                with self.lock:
                    self.addresses[address] = site(record.get("key", {}).get("name", ""))
                    while len(self.addresses) > 5000:
                        self.addresses.popitem(last=False)
        for name in names:
            name = name.rstrip(".").lower()
            if (
                "." not in name
                or name.endswith(LOCAL)
                or any(label.startswith("_") for label in name.split("."))
            ):
                continue
            domain = site(name)
            if domain in self.ignore:
                continue
            if domain in NOISE:
                with self.lock:
                    self.background += 1
                continue
            with self.lock:
                self.lookups[domain] += 1
            self._first(f"dns:{domain}", f"new domain: {domain}", {"domain": domain})

    def _first(self, key: str, text: str, data: dict[str, Any]) -> None:
        """An event when this hasn't been seen for `new_after_days`, once learning is over."""
        if key in self.known:
            return
        self.known.add(key)
        now = time.time()
        previous = self.seen("network", key, now)
        if now >= self.learn_until and (previous is None or now - previous >= self.new_after):
            with self.lock:
                self.pending.append(Action("log", text, data=data, at=now, row="event"))

    # Connections: a snapshot on every sample.

    def _name(self, pid: int | None) -> str:
        if pid is None:
            return "?"
        if pid not in self.names:
            try:
                self.names[pid] = psutil.Process(pid).name()
            except psutil.Error:
                return "?"
        return self.names[pid]

    def _snapshot(self) -> list[tuple[Any, str]]:
        if not self.denied:
            try:
                return [(conn, self._name(conn.pid)) for conn in psutil.net_connections(kind="inet")]
            except psutil.AccessDenied:  # macOS without root: fall back to processes we may inspect
                self.denied = True
        found = []
        for process in psutil.process_iter(["name"]):
            try:
                found += [(conn, process.info["name"]) for conn in process.net_connections(kind="inet")]
            except psutil.Error:
                continue
        return found

    def sample(self, now: float) -> tuple[dict[str, float], list[Action]]:
        if not self.connections:
            return {}, self._take()
        established = 0
        listening: dict[str, dict[str, Any]] = {}
        talking: dict[str, set[str]] = {}
        for conn, name in self._snapshot():
            if conn.status == psutil.CONN_LISTEN and conn.laddr:
                ip, port = conn.laddr.ip, conn.laddr.port
                where = "all interfaces" if ip in ("0.0.0.0", "::") else "localhost" if _loopback(ip) else ip
                key = f"{name} ephemeral" if port >= EPHEMERAL else f"{name} {port}"
                listening[key] = {"process": name, "port": port, "where": where}
            elif conn.status == psutil.CONN_ESTABLISHED and conn.raddr and not _loopback(conn.raddr.ip):
                established += 1
                ip = conn.raddr.ip.removeprefix("::ffff:")
                with self.lock:
                    host = self.addresses.get(ip, ip)
                talking.setdefault(name, set()).add(host)
        if self.listening is not None:
            for key in sorted(set(listening) - self.listening):
                port = listening[key]
                shown = "an ephemeral port" if port["port"] >= EPHEMERAL else f"port {port['port']}"
                text = f"new listening {shown} by {port['process']} ({port['where']})"
                self._first(f"listen:{key}", text, port)
        self.listening = set(listening)
        with self.lock:
            for name, hosts in talking.items():
                self.talkers.setdefault(name, set()).update(hosts)
        return {"net.established": float(established)}, self._take()

    def _take(self) -> list[Action]:
        with self.lock:
            out, self.pending = self.pending, []
        return out

    # The window's summary.

    def flush(self, now: float, force: bool = False) -> list[Action]:
        out = self._take()
        if not force and now - self.started < self.window:
            return out
        with self.lock:
            lookups, background, talkers = self.lookups, self.background, self.talkers
            self.lookups, self.background, self.talkers, self.started = Counter(), 0, {}, now
        for domain in lookups:
            self.seen("network", f"dns:{domain}", now)
        parts = []
        if lookups:
            top = ", ".join(f"{domain} ({count})" for domain, count in lookups.most_common(8))
            more = f", background {background}" if background else ""
            parts.append(f"dns: {sum(lookups.values())} lookups of {len(lookups)} sites: {top}{more}")
        elif background:
            parts.append(f"dns: background {background}")
        if talkers:
            busiest = sorted(talkers.items(), key=lambda item: (-len(item[1]), item[0]))[:6]
            shown = []
            for name, hosts in busiest:
                listed = ", ".join(sorted(hosts)[:4]) + (", …" if len(hosts) > 4 else "")
                shown.append(f"{name} ({len(hosts)} host{'s' if len(hosts) > 1 else ''}: {listed})")
            parts.append("talking: " + ", ".join(shown))
        if not parts:
            return out
        data = {
            "sites": dict(lookups),
            "background": background,
            "talkers": {name: sorted(hosts) for name, hosts in talkers.items()},
        }
        out.append(Action("log", " | ".join(parts)[:1500], data=data, at=now, row="network"))
        return out

    def stop(self) -> list[Action]:
        self.stopped.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return self.flush(time.time(), force=True)


def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%", 1)[0].removeprefix("::ffff:")).is_loopback
    except ValueError:
        return False
