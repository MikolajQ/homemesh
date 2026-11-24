"""Is a paired computer on? A question and an answer over UDP.

`homemesh watch` answers on the port that finding computers uses, too. A
question is one small packet to one address: nothing is broadcast, and the
other computer's sshd does not log a connection that says nothing.

    ping  {from, to, n}         are you there?
    pong  {from, to, n, ready}  yes; ready: its SSH server is running
    bye   {from, to}            the session there is ending

Only paired computers get an answer, so a stranger cannot find out who uses
HomeMesh. The packets prove nothing and need not: they only decide what a
bookmark is called. SSH still checks the keys.
"""

import json
import os
import socket
import time

from . import PORT, vpn
from .state import ID_RE
from .util import canonical, is_lan

KINDS = ("ping", "pong", "bye")
TRIES = (0.4, 0.6, 1.0)  # seconds to wait for an answer before asking again


def port():
    return int(os.environ.get("HOMEMESH_PORT", PORT))


def message(kind, sender, receiver, **more):
    return canonical({"app": "homemesh", "v": 1, "t": kind, "from": sender, "to": receiver, **more})


def read(data):
    """The packet as a dict, or None if it is not one of ours."""
    try:
        note = json.loads(data)
    except ValueError:
        return None
    if not isinstance(note, dict) or note.get("app") != "homemesh" or note.get("t") not in KINDS:
        return None
    if not all(isinstance(note.get(end), str) and ID_RE.match(note[end]) for end in ("from", "to")):
        return None
    return note


def sender(st, note, address):
    """The id of the paired computer that sent this to us, or None."""
    peer = st["peers"].get(note["from"])
    if not peer or note["to"] != st["id"]:
        return None
    if is_lan(address) or any(item["addr"] == address for item in peer.get("vpn") or []):
        return note["from"]
    return None


def pong(note, ready):
    return message("pong", note["to"], note["from"], n=str(note.get("n"))[:32], ready=bool(ready))


def ask(me, pid, address, tries=TRIES):
    """True or False: the computer `pid` answers at `address`, and its SSH server
    runs or not. None: no answer, which an older HomeMesh would not give either."""
    nonce = os.urandom(8).hex()
    question = message("ping", me, pid, n=nonce)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((address, port()))  # only its packets; an error if nobody listens there
            for wait in tries:
                sock.send(question)
                deadline = time.monotonic() + wait
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    sock.settimeout(left)
                    try:
                        note = read(sock.recv(4096))
                    except socket.timeout:
                        break
                    if note and note["t"] == "pong" and note["from"] == pid and note.get("n") == nonce:
                        return note.get("ready") is True
    except OSError:
        pass
    return None


def own_addresses():
    """This computer's IPv4 addresses that paired computers may ask at."""
    override = os.environ.get("HOMEMESH_ADVERTISE_ADDRESS")  # tests only
    if override:
        return {override}
    links = vpn.network_links()
    found = {item["addr"] for item in vpn.addresses(links) if ":" not in item["addr"]}
    for link in links:
        for info in link.get("addr_info", []):
            if info.get("family") == "inet" and info.get("scope") == "global" and is_lan(info["local"]):
                found.add(info["local"])
    return found
