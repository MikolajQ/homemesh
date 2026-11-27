"""The group: who this computer knows, and how that knowledge spreads.

Every computer describes itself with a card (name, user, home folder, keys).
Cards spread between members whenever two of them talk, so pairing a new
computer with any member is enough for the whole group to learn about it.

Which card wins: the one with the newer `updated`. The owner bumps it when
something on the card changes and at every pairing. A removal leaves a
tombstone with a time; a card older than its tombstone stays removed, and
pairing again revives the computer because pairing bumps its card.
"""

import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager

from . import __version__, keys, paths, vpn
from .util import NAME_RE, canonical, hostname, read_text, ssh_port, username, write_text

FORMAT = 1
ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}\Z")
IPV4_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}\Z")
CARD_FIELDS = ("id", "name", "user", "home", "userkey", "hostkey", "port", "updated",
               "version", "address", "vpn")


def new_state():
    # "share" and "login" are this computer's own settings; they never leave it,
    # and neither do "via", "added", "automount" and "breaks" of the peers.
    return {"format": FORMAT, "id": uuid.uuid4().hex, "self": {"hash": None, "updated": 0},
            "peers": {}, "removed": {}, "bookmarks": [], "kde": [],
            "share": {"mode": "home", "folders": []}, "login": True}


def load():
    path = paths.state_file()
    text = read_text(path)
    if not text:
        return new_state()
    try:
        st = json.loads(text)
        if not isinstance(st, dict) or not ID_RE.match(str(st.get("id"))):
            raise ValueError("no id")
    except ValueError:
        # Keep the damaged file for a look, start over.
        path.rename(path.with_name(f"state.json.broken-{int(time.time())}"))
        return new_state()
    base = new_state()
    for key, value in base.items():
        st.setdefault(key, value)
    return st


def save(st):
    write_text(paths.state_file(), json.dumps(st, indent=1, sort_keys=True) + "\n", 0o600)


@contextmanager
def transaction():
    """Load, change and save the state, one process at a time."""
    directory = paths.config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    with open(paths.lock_file(), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fresh = not paths.state_file().exists()
        st = load()
        before = canonical(st)
        yield st
        if fresh or canonical(st) != before:  # a new state must keep its id
            save(st)


def own_card(st, bump=False):
    """This computer's card. Changes `st` when the card changed or on bump."""
    card = {"id": st["id"], "name": hostname(), "user": username(), "home": str(paths.home()),
            "userkey": keys.ensure_identity(), "hostkey": keys.host_key(), "port": ssh_port()}
    address = os.environ.get("HOMEMESH_ADVERTISE_ADDRESS")  # tests only
    if address:
        card["address"] = address
    mine = st["self"]
    tunnels = vpn.remember(mine.get("vpn") or [])
    if tunnels:
        card["vpn"] = vpn.for_card(tunnels)  # how to reach us away from home
        mine["vpn"] = tunnels
    else:
        mine.pop("vpn", None)
    digest = hashlib.sha256(canonical(card)).hexdigest()
    if bump or mine.get("hash") != digest:
        mine.update(hash=digest, updated=max(time.time(), mine.get("updated", 0) + 0.001))
    card["updated"] = mine["updated"]
    card["version"] = __version__
    return card


def forget_vpn(st):
    """After the VPN was turned off on purpose: its address off the card now,
    not after the 180 days a VPN that is down keeps it (vpn.remember)."""
    st["self"].pop("vpn", None)
    return own_card(st)


def _home_ok(value):
    return (isinstance(value, str) and value.startswith("/") and len(value) < 1024
            and not any(ord(ch) < 32 or ch in '"\\' for ch in value))


_CHECKS = {
    "id": lambda v: isinstance(v, str) and ID_RE.match(v),
    "name": lambda v: isinstance(v, str) and NAME_RE.match(v),
    "user": lambda v: isinstance(v, str) and USER_RE.match(v),
    "home": _home_ok,
    "userkey": lambda v: isinstance(v, str) and keys.KEY_RE.match(v),
    "hostkey": lambda v: isinstance(v, str) and keys.KEY_RE.match(v),
    "port": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 < v < 65536,
    "updated": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
}


def validate_card(card):
    """A card from another computer, checked field by field.

    Everything on it ends up in SSH files and bookmarks, so anything odd
    (a newline, a quote) is refused rather than escaped.
    """
    if not isinstance(card, dict):
        raise ValueError("card is not an object")
    out = {}
    for field, check in _CHECKS.items():
        if not check(card.get(field)):
            raise ValueError(f"bad {field}")
        out[field] = card[field]
    version = card.get("version")
    if isinstance(version, str) and len(version) < 40 and version.isprintable():
        out["version"] = version
    address = card.get("address")
    if address is not None:
        if not (isinstance(address, str) and IPV4_RE.match(address)):
            raise ValueError("bad address")
        out["address"] = address
    if card.get("vpn") is not None:
        out["vpn"] = vpn.validate(card["vpn"])
    return out


def card_of(peer):
    return {key: peer[key] for key in CARD_FIELDS if key in peer}


def member_cards(st):
    return [card_of(peer) for peer in st["peers"].values()]


def merge(st, cards, removed, via):
    """Take in what another member knows. Returns True if anything changed."""
    changed = False
    me = st["id"]
    for pid, when in (removed or {}).items():
        if pid == me or not ID_RE.match(str(pid)) or not isinstance(when, (int, float)):
            continue
        if when > st["removed"].get(pid, 0):
            st["removed"][pid] = when
            changed = True
        peer = st["peers"].get(pid)
        if peer and peer["updated"] <= when:
            del st["peers"][pid]
            changed = True
    for card in cards:
        try:
            card = validate_card(card)
        except ValueError:
            continue
        pid = card["id"]
        if pid == me or st["removed"].get(pid, 0) >= card["updated"]:
            continue
        current = st["peers"].get(pid)
        if current and current["updated"] >= card["updated"]:
            continue
        peer = dict(card)
        peer["via"] = current["via"] if current else via
        peer["added"] = current["added"] if current else time.time()
        peer["automount"] = current.get("automount", False) if current else False
        if current and "breaks" in current:
            peer["breaks"] = current["breaks"]
        st["peers"][pid] = peer
        changed = True
    return changed


def pairing_apply(st, remote_card, remote_members):
    """After both people confirmed the code: add the computer and everyone it knows."""
    card = validate_card(remote_card)
    st["removed"].pop(card["id"], None)
    merge(st, [card], {}, via="direct")
    st["peers"][card["id"]]["via"] = "direct"
    merge(st, remote_members if isinstance(remote_members, list) else [], {}, via=card["id"])
    return card


def remove_peer(st, pid):
    peer = st["peers"].pop(pid, None)
    when = max(time.time(), (peer or {}).get("updated", 0) + 0.001, st["removed"].get(pid, 0))
    st["removed"][pid] = when
    return peer


def view(st):
    """What this computer tells another member when they sync."""
    return {"from": own_card(st), "peers": member_cards(st), "removed": dict(st["removed"])}


def find_peer(st, text):
    """A peer by name, name.local or (a prefix of) its id."""
    text = text.lower().removesuffix(".local")
    for pid, peer in st["peers"].items():
        if peer["name"] == text or (len(text) >= 6 and pid.startswith(text)):
            return pid, peer
    return None, None
