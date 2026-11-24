"""Pairing two computers with a six-digit code.

The computers swap their cards (with keys) over a plain TCP connection and
both show a code computed from everything swapped. If the two codes match,
nobody in between changed anything, so the people confirm on both screens.

Somebody in the middle could otherwise try many variants of his own data
until his code matches; so the answering computer first commits to a random
number and reveals it only after the asking one sent its own (the scheme
Bluetooth uses for numeric comparison). A man in the middle then has one
chance in a million.

Messages are JSON objects, one per line:
    asker -> answerer   hello   {card, members}
    answerer -> asker   hello   {card, members, commit = H(nonce_b, card, members)}
    asker -> answerer   nonce   {n: nonce_a}
    answerer -> asker   nonce   {n: nonce_b}            (asker checks commit)
    both                confirm {ok}                    (after the person decides)
"""

import hashlib
import hmac
import json
import os
import socket
import threading

from . import PORT, PORT_SPAN
from .i18n import _
from .util import can_pair_from, canonical

VERSION = 1
MAX_LINE = 512 * 1024
STEP_TIMEOUT = 20
DECIDE_TIMEOUT = 300


class PairingError(Exception):
    """Pairing stopped. The message is a sentence for the person."""


class Channel:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def send(self, obj):
        self.sock.sendall(canonical(obj) + b"\n")

    def recv(self, timeout=STEP_TIMEOUT):
        self.sock.settimeout(timeout)
        try:
            while b"\n" not in self.buffer:
                if len(self.buffer) > MAX_LINE:
                    raise PairingError(_("The other computer sent too much data."))
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise PairingError(_("The other computer closed the connection."))
                self.buffer += chunk
        except socket.timeout:
            raise PairingError(_("The other computer does not answer.")) from None
        except OSError as error:
            raise PairingError(_("Connection lost ({error}).").format(error=error.strerror or error)) from None
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            message = json.loads(line)
        except ValueError:
            raise PairingError(_("The other computer sent something unexpected.")) from None
        if not isinstance(message, dict):
            raise PairingError(_("The other computer sent something unexpected."))
        return message

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def connect(host, port):
    try:
        return Channel(socket.create_connection((host, port), timeout=STEP_TIMEOUT))
    except OSError as error:
        raise PairingError(_("Cannot connect to {host} ({error}). Is HomeMesh open there?")
                           .format(host=host, error=error.strerror or error)) from None


def _commit(nonce, offer):
    return hashlib.sha256(b"homemesh-commit-v1" + nonce + canonical(offer)).hexdigest()


def _code(asker_offer, answerer_offer, nonce_a, nonce_b):
    digest = hashlib.sha256(b"homemesh-code-v1" + canonical(asker_offer) + b"\0"
                            + canonical(answerer_offer) + nonce_a + nonce_b).digest()
    number = f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"
    return f"{number[:3]} {number[3:]}"


def _offer(message):
    return {"card": message.get("card"), "members": message.get("members", [])}


def _nonce(message):
    value = message.get("n")
    if message.get("t") != "nonce" or not isinstance(value, str) or len(value) != 64:
        raise PairingError(_("The other computer sent something unexpected."))
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise PairingError(_("The other computer sent something unexpected.")) from None


REFUSALS = {
    "busy": lambda: _("The other computer is pairing with someone else right now. Try again in a "
                      "moment."),
    "not-ready": lambda: _("Sharing is off on the other computer. Turn it on there first."),
}


def initiate(chan, offer):
    """Ask to pair. Returns (their offer, code)."""
    chan.send({"t": "hello", "v": VERSION, **offer})
    reply = chan.recv()
    kind = reply.get("t")
    if kind == "refuse":
        reason = REFUSALS.get(reply.get("reason"))
        raise PairingError(reason() if reason else _("The other computer refused."))
    if kind != "hello" or not isinstance(reply.get("commit"), str):
        raise PairingError(_("The other computer sent something unexpected."))
    if reply.get("v") != VERSION:
        raise PairingError(_("The other computer has a different version of HomeMesh. Update both."))
    theirs = _offer(reply)
    nonce_a = os.urandom(32)
    chan.send({"t": "nonce", "n": nonce_a.hex()})
    nonce_b = _nonce(chan.recv())
    if not hmac.compare_digest(_commit(nonce_b, theirs), reply["commit"]):
        raise PairingError(_("The data from the other computer does not add up: someone may be "
                             "posing as it. Stopped."))
    return theirs, _code(offer, theirs, nonce_a, nonce_b)


def read_hello(chan):
    hello = chan.recv()
    if hello.get("t") != "hello":
        raise PairingError(_("The other computer sent something unexpected."))
    if hello.get("v") != VERSION:
        refuse(chan, "version")
        raise PairingError(_("The other computer has a different version of HomeMesh. Update both."))
    return hello


def respond(chan, hello, offer):
    """Answer a hello. Returns (their offer, code)."""
    theirs = _offer(hello)
    nonce_b = os.urandom(32)
    chan.send({"t": "hello", "v": VERSION, **offer, "commit": _commit(nonce_b, offer)})
    nonce_a = _nonce(chan.recv())
    chan.send({"t": "nonce", "n": nonce_b.hex()})
    return theirs, _code(theirs, offer, nonce_a, nonce_b)


def refuse(chan, reason):
    try:
        chan.send({"t": "refuse", "reason": reason})
    except OSError:
        pass


class Confirmation:
    """The two people's decisions, which may come in any order."""

    def __init__(self, chan):
        self.chan = chan
        self.local = None
        self._decided = threading.Event()
        self._lock = threading.Lock()

    def decide(self, ok):
        with self._lock:
            if self.local is not None:
                return
            self.local = bool(ok)
        self._decided.set()
        try:
            self.chan.send({"t": "confirm", "ok": bool(ok)})
        except OSError:
            pass

    def wait_remote(self, timeout=DECIDE_TIMEOUT):
        try:
            message = self.chan.recv(timeout)
        except PairingError:
            if self.local is False:
                raise PairingError(_("Cancelled.")) from None
            raise
        if message.get("t") != "confirm":
            raise PairingError(_("The other computer sent something unexpected."))
        return message.get("ok") is True

    def wait_local(self, timeout=DECIDE_TIMEOUT):
        if not self._decided.wait(timeout):
            self.decide(False)
        return self.local


class PairServer:
    """Accepts pairing requests from the local network while HomeMesh is open."""

    def __init__(self, handler, port=None):
        base = port or int(os.environ.get("HOMEMESH_PORT", PORT))
        self.handler = handler
        self.sock = None
        for candidate in range(base, base + PORT_SPAN):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("", candidate))
            except OSError:
                sock.close()
                continue
            sock.listen(4)
            self.sock, self.port = sock, candidate
            break
        if self.sock is None:
            raise OSError(f"no free port in {base}-{base + PORT_SPAN - 1}")
        threading.Thread(target=self._loop, daemon=True, name="pair-server").start()

    def _loop(self):
        while True:
            try:
                conn, (address, _port) = self.sock.accept()
            except OSError:
                return
            if not can_pair_from(address):
                conn.close()
                continue
            threading.Thread(target=self._handle, args=(conn, address), daemon=True).start()

    def _handle(self, conn, address):
        chan = Channel(conn)
        try:
            self.handler(chan, address)
        except Exception as error:  # a broken request must not take the server down
            print(f"homemesh: pairing request from {address}: {error}", flush=True)
        finally:
            chan.close()

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
