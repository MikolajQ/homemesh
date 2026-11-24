"""Reaching paired computers away from home, through a VPN the person runs.

HomeMesh makes no tunnels of its own: getting through NAT needs a helper
somewhere on the internet, and the existing VPNs do that well. HomeMesh
notices the usual ones on this computer, puts their addresses on its card,
and SSH falls back to them whenever a computer does not answer on the local
network (see render.ssh_config_text and helper/homemesh-route).
"""

import ipaddress
import json
import os
import subprocess

# Interface name prefixes, as each VPN names its interface by default.
KINDS = (("tailscale", "Tailscale"), ("wt", "NetBird"), ("zt", "ZeroTier"), ("ygg", "Yggdrasil"),
         ("nebula", "Nebula"), ("wg", "WireGuard"))
NAMES = {name for _prefix, name in KINDS} | {"VPN"}
YGGDRASIL = ipaddress.ip_network("200::/7")
# Where VPN addresses come from: private ranges, Tailscale's and NetBird's
# 100.64.0.0/10, Yggdrasil's 200::/7 and unique local IPv6. Loopback is for tests.
ALLOWED = [ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
    "200::/7", "fc00::/7", "::1/128")]
MAX_ADDRESSES = 4


def kind_of(interface, address):
    for prefix, name in KINDS:
        if interface.startswith(prefix):
            return name
    try:
        if ipaddress.ip_address(address) in YGGDRASIL:  # Yggdrasil may run as tun0
            return "Yggdrasil"
    except ValueError:
        pass
    return None


def allowed(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip.version == net.version and ip in net for net in ALLOWED)


def _links():
    try:
        out = subprocess.run(["ip", "-j", "addr", "show", "up"], capture_output=True, text=True,
                             timeout=3).stdout
        return json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def addresses(links=None):
    """[{"addr", "kind"}] of the VPNs on this computer, IPv4 first."""
    override = os.environ.get("HOMEMESH_TEST_VPN")
    if override is not None:
        return [{"addr": a, "kind": "VPN"} for a in override.split(",") if a]
    found = []
    for link in links if links is not None else _links():
        interface = link.get("ifname", "")
        for info in link.get("addr_info", []):
            address = info.get("local", "")
            if info.get("scope") != "global" or not allowed(address):
                continue
            kind = kind_of(interface, address)
            if kind and all(item["addr"] != address for item in found):
                found.append({"addr": address, "kind": kind})
    found.sort(key=lambda item: ":" in item["addr"])
    return found[:MAX_ADDRESSES]


def validate(value):
    """The `vpn` field of a card from another computer."""
    if not isinstance(value, list) or len(value) > MAX_ADDRESSES:
        raise ValueError("bad vpn")
    out = []
    for item in value:
        if not (isinstance(item, dict) and isinstance(item.get("addr"), str) and allowed(item["addr"])
                and item.get("kind") in NAMES):
            raise ValueError("bad vpn address")
        out.append({"addr": str(ipaddress.ip_address(item["addr"])), "kind": item["kind"]})
    return out


def describe(items):
    """'Tailscale (100.101.102.103)' for people."""
    return ", ".join(f"{item['kind']} ({item['addr']})" for item in items)
