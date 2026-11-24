"""Local networks that keep breaking between two computers.

Some home networks lose the way between two computers for half a minute at a
time, although both are on and answer through the VPN: a router goes on
sending one computer's packets to the Wi-Fi band it has just left, a mesh is
slow to notice that it moved to another node.

A break is when a computer that answered locally answers only through the VPN
and then locally again: at the same address, within a quarter of an hour, and
with this computer's own addresses unchanged. Leaving home takes longer, and a
computer that is off or asleep does not answer through the VPN either. One
that does not know our VPN address, and would refuse our key from there, does
not answer there (presence.sender). See watch.Seen, which notices them.

After two breaks SSH tries the VPN first (render.ssh_config_text). At home the
VPN's packets stay at home, and it finds another way within seconds when the
local network breaks, while a plain connection hangs until it is cut. Three
days without a break end that. The two computers tell each other about the
breaks they saw (presence.py), as often only one of them can notice.
"""

import time

KEEP = 3 * 24 * 3600
ENOUGH = 2
SAME = 120  # seconds: closer than that, it is one break seen twice
MOST = 20  # remembered


def times(peer, now=None):
    """When the local network between us broke lately, oldest first."""
    now = time.time() if now is None else now
    known = peer.get("breaks")
    return [when for when in known if isinstance(when, (int, float)) and now - when < KEEP] \
        if isinstance(known, list) else []


def breaking(peer, now=None):
    """Should connections to this computer go through the VPN first?"""
    return bool(peer.get("vpn")) and len(times(peer, now)) >= ENOUGH


def ages(peer, now=None):
    """How long ago, for the other computer: clocks may differ. Only what it needs."""
    now = time.time() if now is None else now
    return [max(0, int(now - when)) for when in times(peer, now)[-ENOUGH:]]


def sound(value):
    """Ages from another computer, or () when they make no sense."""
    if not isinstance(value, (list, tuple)) or len(value) > ENOUGH:
        return ()
    if not all(isinstance(age, int) and not isinstance(age, bool) and 0 <= age < KEEP for age in value):
        return ()
    return tuple(value)


def merged(peer, new_ages, now=None):
    """peer["breaks"] with the breaks that long ago, or None when all were known."""
    now = time.time() if now is None else now
    known = times(peer, now)
    out = list(known)
    for age in new_ages:
        when = now - age
        if all(abs(when - other) >= SAME for other in out):
            out.append(when)
    return sorted(out)[-MOST:] if len(out) > len(known) else None
