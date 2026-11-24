"""homemesh watch: marks the computers that are off in the file manager,
notices when the local network keeps breaking (see breaks.py) and tells the
group when this computer gets another VPN address.

A bookmark of a computer that is off makes the file manager wait and then
fail, so its name says it: "(off) laptop". One small process per desktop
session looks after that. It starts at login or with the window and ends
with the session; when it ends, the marks go.

It keeps the network quiet. A computer that is on gets one small packet a
minute, sent to its own address (see presence.py), and only one of the two
asks: being asked is a sign of life, too. A computer that is off is looked
up every five minutes. That is enough, because HomeMesh asks everyone when it
starts, when the network changes and after sleep, and so is noticed at once.
"""

import ctypes
import fcntl
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, SimpleQueue

from . import __version__, breaks, paths, presence, render, state, sync, system, wifi

PACE = float(os.environ.get("HOMEMESH_TEST_PACE", "1"))  # tests only
ASK = 60 * PACE  # a computer whose HomeMesh answers
CONNECT = 180 * PACE  # one that takes an SSH connection to check, or a VPN to reach
LOOK = 300 * PACE  # one that is off
AGAIN = 8 * PACE  # a second look before believing that a computer is gone
NETWORK = tuple(PACE * wait for wait in (0, 6, 25))  # after a change: names take a moment
LEAVING = tuple(PACE * wait for wait in (3, 10, 25))  # after "bye": shutting down takes a moment
BACK = tuple(PACE * wait for wait in (10, 20, 30, 45, 60, 90, 150))  # is the local network back?
BREAK = 900 * PACE  # gone for longer, it was no break: the computer has left home
SOON = 60 * PACE  # the VPN takes over within seconds; a computer that starts again takes longer
ROAMED = tuple(PACE * wait for wait in (2, 12, 30))  # after the Wi-Fi joined an access point
PATIENT = 40 * PACE  # so long after that, no answer does not mean that a computer is off
HOUR = 3600
SETTLE = 2 * PACE
NETLINK_IPV4_ADDRESSES = 0x10  # RTMGRP_IPV4_IFADDR


class Seen:
    """What is known about one paired computer, and when to check it next."""

    def __init__(self, now):
        self.on = None  # not known yet
        self.reach = system.NOWHERE
        self.hint = None
        self.local = None  # the local address it answered at last
        self.lost = None  # since when it has not
        self.away = None  # since when it has answered only through the VPN instead
        self.patient = now
        self.misses = 0
        self.due = now
        self.soon = ()
        self.busy = False

    def next(self):
        return min((self.due, *self.soon))

    def hurry(self, now, waits):
        self.soon = tuple(now + wait for wait in waits)

    def checked(self, reach, now):
        """True when the local network between us has just come back after a break."""
        self.busy = False
        self.soon = tuple(when for when in self.soon if when > now)
        if reach.how:
            broke = self._broke(reach, now)
            self.on, self.misses, self.reach, self.hint = True, 0, reach, reach.address
            self.due = now + (ASK if reach.answers and reach.how == "lan" else CONNECT)
            return broke
        self.misses += 1
        if self.lost is None:
            self.lost = now
        if self.on is not False and self.misses < 2 or self.on and now < self.patient:
            self.due = now + (AGAIN if self.on else AGAIN / 4)
            return False
        self.on, self.reach, self.hint, self.away = False, system.NOWHERE, None, None
        self.due = now + LOOK
        return False

    def _broke(self, reach, now):
        if reach.how == "vpn":
            if self.lost is None:
                self.lost = now
            # An answer, not just an open port: only who knows our VPN address
            # answers there, and so accepts our key from there as well.
            if reach.answers and self.local and self.away is None and now - self.lost < SOON:
                self.away = now
                self.hurry(now, BACK)
            return False
        broke = self.away is not None and reach.address == self.local and now - self.away < BREAK
        self.local, self.lost, self.away = reach.address, None, None
        return broke

    def forget(self):
        """It is leaving, or this computer is in another network, perhaps:
        what was found locally says nothing now."""
        self.local = self.lost = self.away = None

    def heard(self, address, now):
        """It asked us, so it is on, and asking it back can wait."""
        if self.on and self.reach.address == address:
            self.reach = self.reach._replace(answers=True)
            self.misses, self.due = 0, now + 1.2 * ASK
        else:
            self.hint = address
            self.hurry(now, NETWORK[:2])


class Watcher:
    def __init__(self, check=system.reach, sync_all=sync.sync_all):
        self.check = check
        self.sync_all = sync_all
        self.told = None  # the `updated` of our card as the group knows it
        self.telling = threading.Lock()
        self.breaking = None  # who is reached through the VPN because the local network breaks
        self.roamed = []  # when the Wi-Fi joined an access point in the last hour
        self.st = None
        self.stamp = None
        self.seen = {}
        self.marked = {}
        self.sockets = {}
        self.stopping = False
        self.settle = None  # when to act on a change of the network
        self.asleep = self._asleep()
        self.results = SimpleQueue()
        self.selector = selectors.DefaultSelector()
        self.wake_in, self.wake_out = socket.socketpair()
        for sock in (self.wake_in, self.wake_out):
            sock.setblocking(False)
        self.selector.register(self.wake_in, selectors.EVENT_READ, self._woken)

    # ------------------------------------------------------------- events

    def _woken(self, sock):
        try:
            while sock.recv(4096):
                pass
        except BlockingIOError:
            pass

    def wake(self):
        try:
            self.wake_out.send(b"x")
        except OSError:
            pass

    def stop(self, *_args):
        self.stopping = True

    def watch_session(self, sock):
        """End when the desktop closes this connection to it."""
        def data(sock):
            try:
                if not os.read(sock.fileno(), 65536):
                    self.stopping = True
            except BlockingIOError:
                pass
            except OSError:
                self.stopping = True
        self.selector.register(sock, selectors.EVENT_READ, data)

    def watch_network(self):
        try:
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
            sock.bind((0, NETLINK_IPV4_ADDRESSES))
            sock.setblocking(False)
        except OSError:
            return

        def data(sock):
            try:
                while sock.recv(65536):
                    pass
            except OSError:  # nothing more, or too much at once
                pass
            self.settle = time.monotonic() + SETTLE
        self.selector.register(sock, selectors.EVENT_READ, data)

    def watch_wifi(self):
        sock = wifi.events()
        if sock is None:
            return

        def data(sock):
            joined = False
            try:
                while True:
                    joined |= wifi.joined(sock.recv(65536))
            except OSError:  # nothing more, or too much at once
                pass
            if joined:
                self.roam(time.monotonic())
        self.selector.register(sock, selectors.EVENT_READ, data)

    def roam(self, now):
        """The Wi-Fi has moved to another access point, or joined its first one."""
        if self.roamed and now - self.roamed[-1] < 2:  # several events tell of the same
            return
        self.roamed = [when for when in self.roamed if now - when < HOUR] + [now]
        for seen in self.seen.values():
            if seen.on:
                seen.patient = now + PATIENT
                seen.hurry(now, ROAMED)

    @staticmethod
    def _asleep():
        """Seconds this computer has slept since it started."""
        return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()

    def listen(self):
        """Answer at each of this computer's own addresses. Not at all of them
        with one socket: with the window open, packets would go to the window.
        True when the addresses have changed."""
        wanted = presence.own_addresses()
        changed = wanted != set(self.sockets)
        for address in set(self.sockets) - wanted:
            sock = self.sockets.pop(address)
            self.selector.unregister(sock)
            sock.close()
        for address in wanted - set(self.sockets):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((address, presence.port()))
            except OSError:
                sock.close()
                continue
            sock.setblocking(False)
            self.sockets[address] = sock
            self.selector.register(sock, selectors.EVENT_READ, self._packet)
        return changed

    def _packet(self, sock):
        try:
            data, where = sock.recvfrom(4096)
        except OSError:
            return
        note = presence.read(data)
        pid = presence.sender(self.st, note, where[0]) if note else None
        if pid not in self.seen:
            return
        now = time.monotonic()
        if note["t"] == "ping":
            try:
                sock.sendto(presence.pong(note, system.ssh_listening(), breaks.ages(self.st["peers"][pid])), where)
            except OSError:
                pass
            self.seen[pid].heard(where[0], now)
            self.learn(pid, breaks.sound(note.get("breaks")))
        elif note["t"] == "bye":
            self.seen[pid].forget()
            self.seen[pid].hurry(now, LEAVING)

    # ------------------------------------------------------------- checks

    def refresh(self):
        """Take in changes of the group. False when there is nothing to watch."""
        try:
            stat = os.stat(paths.state_file())
        except FileNotFoundError:
            return False
        stamp = (stat.st_ino, stat.st_mtime_ns)
        if stamp != self.stamp:
            self.stamp = stamp
            before = self.st["peers"] if self.st else {}
            self.st = state.load()
            if self.told is None:  # it was told at login or is being told right now
                self.told = self.st["self"]["updated"]
            now = time.monotonic()
            for pid, peer in self.st["peers"].items():
                if pid not in self.seen:
                    self.seen[pid] = Seen(now)
                elif state.card_of(before.get(pid, {})) != state.card_of(peer):
                    self.seen[pid].hurry(now, (0,))
            for pid in set(self.seen) - set(self.st["peers"]):
                del self.seen[pid]
        return bool(self.st["peers"]) and self.st.get("login", True)

    def start_checks(self, now):
        for pid, seen in self.seen.items():
            if seen.busy or seen.next() > now:
                continue
            seen.busy = True
            threading.Thread(target=self._check, args=(pid, dict(self.st["peers"][pid]), self.st["id"], seen.hint),
                             daemon=True).start()

    def _check(self, pid, peer, me, hint):
        try:
            reach = self.check(peer, me, hint)
        except Exception as error:  # must not leave the computer unchecked for good
            print(f"homemesh: {peer['name']}: {error}", file=sys.stderr)
            reach = system.NOWHERE
        self.results.put((pid, reach))
        self.wake()

    def collect(self):
        try:
            while True:
                pid, reach = self.results.get_nowait()
                if pid in self.seen:
                    broke = self.seen[pid].checked(reach, time.monotonic())
                    self.learn(pid, reach.breaks + ((0,) if broke else ()))
        except Empty:
            pass

    def hurry(self, waits):
        for seen in self.seen.values():
            seen.hurry(time.monotonic(), waits)

    def learn(self, pid, ages):
        """Remember breaks of the local network: ours, or those the other one knows of."""
        if not ages or breaks.merged(self.st["peers"].get(pid, {}), ages) is None:
            return
        now = time.monotonic()
        with state.transaction() as st:
            peer = st["peers"].get(pid)
            times = breaks.merged(peer, ages) if peer else None
            if times is not None:
                peer["breaks"] = times
                st["roamed"] = sum(now - when < HOUR for when in self.roamed)
        self.refresh()

    def route(self):
        """Have SSH try the VPN first where the local network keeps breaking, and no longer than that."""
        breaking = {pid for pid, peer in self.st["peers"].items() if breaks.breaking(peer)}
        if breaking == self.breaking or not paths.state_file().exists():
            return
        self.breaking = breaking
        with state.transaction() as st:
            render.apply(st)

    def mark(self, marks):
        if marks == self.marked or not paths.state_file().exists():
            return
        with state.transaction() as st:
            render.bookmarks(st, marks)
        self.marked = marks

    # ---------------------------------------------------------- our card

    def tell(self):
        """Tell the group if our card has changed: the VPN has just connected for
        the first time, or with another address."""
        threading.Thread(target=self._tell, daemon=True).start()

    def _tell(self):
        with self.telling:
            if not paths.state_file().exists():
                return
            with state.transaction() as st:
                updated = state.own_card(st)["updated"]
            if updated == self.told:
                return
            self.told = updated
            left = None  # everyone
            for wait in NETWORK:
                time.sleep(wait)
                left = {pid for pid, error in self.sync_all(only=left).items() if error is not None}
                if not left:
                    break

    # --------------------------------------------------------------- loop

    def step(self, longest=None):
        """Wait for the next thing to happen, or at most `longest` seconds, and deal with it."""
        now = time.monotonic()
        if abs(self._asleep() - self.asleep) > 2:
            self.asleep = self._asleep()
            self.settle = now + SETTLE
        if self.settle is not None and self.settle <= now:
            self.settle = None
            if self.listen():
                for seen in self.seen.values():
                    seen.forget()
            self.hurry(NETWORK)
            self.tell()
        self.start_checks(now)
        times = [seen.next() for seen in self.seen.values() if not seen.busy]
        if self.settle is not None:
            times.append(self.settle)
        if longest is not None:
            times.append(now + longest)
        timeout = max(0, min(times) - time.monotonic()) if times else None
        for key, _events in self.selector.select(timeout):
            key.data(key.fileobj)
        self.collect()
        self.route()
        self.mark({pid: not seen.on for pid, seen in self.seen.items() if seen.on is not None})

    def run(self):
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(number, self.stop)
        signal.set_wakeup_fd(self.wake_out.fileno(), warn_on_full_buffer=False)
        self.watch_network()
        self.watch_wifi()
        if self.refresh():
            self.listen()
        while not self.stopping and self.refresh():
            self.step()
        self.leave()

    def leave(self):
        for pid, seen in self.seen.items():
            if seen.on and seen.reach.answers:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                        sock.sendto(presence.message("bye", self.st["id"], pid),
                                    (seen.reach.address, presence.port()))
                except OSError:
                    pass
        if any(self.marked.values()):
            self.mark(dict.fromkeys(self.marked, False))


# ------------------------------------------------------------ one per session

def build():
    """Changes when the program is updated."""
    return f"{__version__}-{os.stat(__file__).st_mtime_ns}"


def _holder():
    """(process id, build) of the running watcher, or None."""
    try:
        pid, its_build = paths.watch_lock().read_text().split()
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return None
    return (int(pid), its_build) if b"watch" in command and b"homemesh" in b" ".join(command) else None


def _lock():
    """The lock of the only watcher; an older build of it has to go. None: one runs already."""
    paths.config_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(paths.watch_lock(), "a+")
    deadline = time.monotonic() + 5
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            holder = _holder()
            if holder and holder[1] == build() or time.monotonic() > deadline:
                lock.close()
                return None
            if holder:
                _end(holder[0])
            time.sleep(0.1)
    lock.truncate(0)
    lock.write(f"{os.getpid()} {build()}\n")
    lock.flush()
    return lock


def _end(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _session():
    """A connection to the desktop, which closes it when the session ends. None without a desktop."""
    name = os.environ.get("WAYLAND_DISPLAY")
    if name:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), name))
            sock.setblocking(False)
            return sock
        except OSError:
            sock.close()
    if os.environ.get("DISPLAY"):
        try:
            x11 = ctypes.CDLL("libX11.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
            display = x11.XOpenDisplay(None)
            if display:
                sock = socket.socket(fileno=x11.XConnectionNumber(display))
                sock.setblocking(False)
                return sock
        except (OSError, AttributeError):
            pass
    return None


def run():
    if not paths.state_file().exists():
        return 0
    session = None if os.environ.get("HOMEMESH_HOME") else _session()
    if session is None and not os.environ.get("HOMEMESH_HOME"):
        print("homemesh: no desktop session to watch", file=sys.stderr)
        return 1
    lock = _lock()
    if lock is None:
        return 0
    watcher = Watcher()
    if session is not None:
        watcher.watch_session(session)
    watcher.run()
    lock.close()
    return 0


def start(st):
    """Start the watcher unless it runs (it finds that out itself) or has nothing to do."""
    if not st["peers"] or not st.get("login", True) or os.environ.get("HOMEMESH_HOME"):
        return
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return
    try:
        subprocess.Popen([*paths.program(), "watch"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, cwd="/")
    except OSError:
        pass


def stop(timeout=3):
    """End the watcher; it takes its marks off."""
    holder = _holder()
    if not holder:
        return
    _end(holder[0])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _holder() == holder:
        time.sleep(0.05)
