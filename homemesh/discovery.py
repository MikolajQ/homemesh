"""Finding other computers with HomeMesh open on the local network.

Each one broadcasts a short UDP note every few seconds while its window is
open. This works without Avahi or systemd-resolved, and nothing listens
once HomeMesh is closed.
"""

import json
import os
import socket
import subprocess
import threading
import time

from . import PORT
from .state import ID_RE
from .util import NAME_RE, canonical, is_lan

INTERVAL = 3
EXPIRE = 10


def broadcast_addresses():
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True,
                             text=True, timeout=3).stdout
        links = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    found = []
    for link in links:
        for info in link.get("addr_info", []):
            if info.get("broadcast") and info.get("scope") == "global" and is_lan(info.get("local", "")):
                found.append(info["broadcast"])
    return found


def _targets_from_env():
    """HOMEMESH_DISCOVERY_TARGETS=host:port,... replaces broadcasting (tests)."""
    value = os.environ.get("HOMEMESH_DISCOVERY_TARGETS")
    if not value:
        return None
    targets = []
    for item in value.split(","):
        host, _, port = item.strip().rpartition(":")
        targets.append((host, int(port)))
    return targets


class Discovery:
    def __init__(self, info, on_change=None, port=None, targets=None):
        """info(): {"id", "name", "user", "port", "ready"} of this computer."""
        self.info = info
        self.on_change = on_change
        self.port = port or int(os.environ.get("HOMEMESH_PORT", PORT))
        self.targets = targets or _targets_from_env()
        self.lock = threading.Lock()
        self.seen = {}
        self._stop = threading.Event()
        self._last_answer = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", self.port))
        self.sock = sock
        threading.Thread(target=self._receive, daemon=True, name="discovery-rx").start()
        threading.Thread(target=self._announce, daemon=True, name="discovery-tx").start()

    def _destinations(self):
        if self.targets:
            return self.targets
        return sorted({("255.255.255.255", self.port)}
                      | {(address, self.port) for address in broadcast_addresses()})

    def send(self, kind):
        message = canonical({"app": "homemesh", "v": 1, "t": kind, **self.info()})
        for destination in self._destinations():
            try:
                self.sock.sendto(message, destination)
            except OSError:
                pass

    def _announce(self):
        self.send("query")
        while not self._stop.wait(INTERVAL):
            self.send("announce")
            self._expire()

    def _receive(self):
        while not self._stop.is_set():
            try:
                data, (address, _port) = self.sock.recvfrom(4096)
            except OSError:
                return
            try:
                note = json.loads(data)
            except ValueError:
                continue
            if not isinstance(note, dict) or note.get("app") != "homemesh" or not is_lan(address):
                continue
            pid, name = str(note.get("id")), str(note.get("name"))
            port = note.get("port")
            if not ID_RE.match(pid) or not NAME_RE.match(name) or not isinstance(port, int):
                continue
            if pid == self.info()["id"]:
                continue
            if note.get("t") == "query" and time.monotonic() - self._last_answer > 1:
                self._last_answer = time.monotonic()
                self.send("announce")
            entry = {"id": pid, "name": name, "user": str(note.get("user", ""))[:32],
                     "port": port, "ready": note.get("ready") is True, "ip": address,
                     "seen": time.monotonic()}
            with self.lock:
                old = self.seen.get(pid)
                self.seen[pid] = entry
                changed = old is None or any(old[k] != entry[k] for k in ("name", "ready", "ip", "port"))
            if changed and self.on_change:
                self.on_change()

    def _expire(self):
        now = time.monotonic()
        with self.lock:
            gone = [pid for pid, entry in self.seen.items() if now - entry["seen"] > EXPIRE]
            for pid in gone:
                del self.seen[pid]
        if gone and self.on_change:
            self.on_change()

    def nearby(self):
        with self.lock:
            return sorted(self.seen.values(), key=lambda entry: entry["name"])

    def close(self):
        self._stop.set()
        self.sock.close()
