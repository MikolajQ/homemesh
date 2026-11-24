"""What the computer can do: is sharing on, does name.local work, can the
file manager open SFTP. Plus running the root helper."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import paths
from .i18n import _
from .util import is_lan, ssh_port


def sharing_configured():
    return os.path.exists(os.environ.get("HOMEMESH_SSHD_DROPIN", str(paths.SSHD_DROPIN)))


def port_open(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def sshd_running():
    return port_open("127.0.0.1", ssh_port(), 1)


def ready():
    """Can other computers log in here (and so pair with us)?"""
    return sharing_configured() and sshd_running()


def mdns():
    """(works, by what). Without it other computers won't find name.local."""
    if os.path.exists("/run/avahi-daemon/socket"):
        return True, "avahi"
    try:
        out = subprocess.run(["resolvectl", "mdns"], capture_output=True, text=True,
                             timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    if re.search(r"^Global: yes$", out, re.M) and re.search(r"^Link .*: yes$", out, re.M):
        return True, "systemd-resolved"
    return False, None


def gvfs_sftp():
    try:
        from gi.repository import Gio
        return "sftp" in Gio.Vfs.get_default().get_supported_uri_schemes()
    except Exception:
        return False


def kde():
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")


def peer_host(peer):
    return peer.get("address") or f"{peer['name']}.local"


def _resolve(host, port, timeout=1.5):
    """IPv4 address of host, giving up after `timeout` (name.local away from home)."""
    found = []

    def lookup():
        try:
            found.append(socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0])
        except OSError:
            pass
    thread = threading.Thread(target=lookup, daemon=True)
    thread.start()
    thread.join(timeout)
    return found[0] if found else None


def reach(peer):
    """"lan", "vpn" or None: how this peer can be reached right now."""
    address = _resolve(peer_host(peer), peer["port"])
    if address and is_lan(address) and port_open(address, peer["port"], 1.5):
        return "lan"
    for item in peer.get("vpn") or []:
        if port_open(item["addr"], peer["port"], 2.5):
            return "vpn"
    return None


def reachable(peers):
    """{peer id: "lan", "vpn" or None}, checked in parallel."""
    if not peers:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(peers, pool.map(reach, peers.values())))


class HelperResult:
    def __init__(self, ok, steps, error=None):
        self.ok, self.steps, self.error = ok, steps, error


def run_helper(args, terminal=False):
    """Run the root helper through sudo (in a terminal) or pkexec (anywhere else)."""
    helper = paths.helper()
    if os.geteuid() == 0:
        command = [helper, *args]
    elif terminal and sys.stdin.isatty() and shutil.which("sudo"):
        command = ["sudo", helper, *args]
    elif shutil.which("pkexec"):
        command = ["pkexec", helper, *args]
    else:
        return HelperResult(False, [], _("Brak pkexec (pakiet polkit). Uruchom w terminalu: sudo homemesh enable"))
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        return HelperResult(False, [], str(error))
    steps = []
    for line in done.stdout.splitlines():
        if line.startswith("{"):
            try:
                steps.append(json.loads(line))
            except ValueError:
                pass
    if done.returncode == 0:
        return HelperResult(True, steps)
    if command[0] == "pkexec" and done.returncode in (126, 127) and not steps:
        if "authentication agent" in done.stderr.lower():
            return HelperResult(False, [], _("Brak okna logowania administratora (agenta polkit). "
                                             "Uruchom w terminalu: sudo homemesh {cmd}").format(cmd=args[0]))
        return HelperResult(False, [], _("Nie udzielono uprawnień administratora."))
    error = done.stderr.strip().splitlines()
    return HelperResult(False, steps, error[-1] if error else None)
