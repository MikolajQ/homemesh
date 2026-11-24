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
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

from . import paths, presence
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


def ssh_listening():
    """Like sshd_running, but only looks: sshd logs every connection."""
    wanted = f":{ssh_port():04X}"
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as lines:
                for line in lines:
                    fields = line.split()
                    if len(fields) > 3 and fields[3] == "0A" and fields[1].endswith(wanted):
                        return True
        except OSError:
            continue
    return False


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


def _program(name):
    """Programs for root too, which are not in everyone's PATH."""
    return shutil.which(name, path=os.environ.get("PATH", "") + ":/usr/bin:/usr/sbin:/bin:/sbin")


def missing(mdns_works):
    """What sharing needs and this computer lacks, as the root helper calls it.

    Only what is certain: with systemd-resolved running, the helper first
    tries to do without Avahi.
    """
    found = [] if _program("sshd") else ["sshd"]
    if not mdns_works and not os.path.isdir("/run/systemd/resolve") and not _program("avahi-daemon"):
        found.append("avahi")
    return found


def thing_names(things):
    """For people: what `things` (as the root helper calls them) are."""
    names = {"sshd": _("SSH server (openssh)"), "avahi": "Avahi"}
    return ", ".join(names.get(thing, thing) for thing in things)


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


# How a paired computer can be reached: how ("lan", "vpn" or None), at which
# address, and whether HomeMesh there answers questions (see presence.py).
Reach = namedtuple("Reach", "how address answers")
NOWHERE = Reach(None, None, False)


def reach(peer, me=None, hint=None):
    """How this peer can be reached right now. me: this computer's id; without
    it, or when HomeMesh is not running there, an SSH connection tells. hint:
    the local address it had last time, which saves looking its name up."""
    port, tunnels = peer["port"], [item["addr"] for item in peer.get("vpn") or []]

    def ask(address, how, tries=presence.TRIES):
        ready = presence.ask(me, peer["id"], address, tries) if me and ":" not in address else None
        return None if ready is None else Reach(how if ready else None, address, True)

    answer = ask(hint, "lan") if hint and is_lan(hint) and hint not in tunnels else None
    if answer:
        return answer
    address = _resolve(peer_host(peer), port)
    if address and is_lan(address):
        answer = ask(address, "lan") if address != hint else None
        if answer:
            return answer
        if port_open(address, port, 1.5):
            return Reach("lan", address, False)
    for address in tunnels:
        answer = ask(address, "vpn", presence.TRIES[-1:])
        if answer:
            return answer
        if port_open(address, port, 2.5):
            return Reach("vpn", address, False)
    return NOWHERE


def reachable(peers, me=None, hints=None):
    """{peer id: Reach}, checked in parallel."""
    if not peers:
        return {}
    hints = hints or {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(peers, pool.map(lambda pid: reach(peers[pid], me, hints.get(pid)), peers)))


def step_text(step):
    """A helper step in the person's language (the helper itself cannot know it)."""
    text, args = step.get("text"), step.get("args")
    if isinstance(text, str) and isinstance(args, dict):
        try:
            return _(text).format(**args)
        except (KeyError, IndexError, ValueError):
            pass
    return str(step.get("msg", ""))


class HelperResult:
    def __init__(self, ok, steps, error=None):
        self.ok, self.steps, self.error = ok, steps, error

    @property
    def missing(self):
        """What "homemesh-helper enable --install" would install to make it work."""
        return [thing for step in self.steps if not step.get("ok") for thing in step.get("missing", [])]


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
        return HelperResult(False, [], _("pkexec is missing (polkit package). In a terminal run: "
                                         "sudo homemesh enable"))
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
            return HelperResult(False, [], _("No window to ask for the administrator password "
                                             "(polkit agent). In a terminal run: sudo homemesh "
                                             "{cmd}").format(cmd=args[0]))
        return HelperResult(False, [], _("Administrator permission was not given."))
    error = done.stderr.strip().splitlines()
    return HelperResult(False, steps, error[-1] if error else None)
