"""Small helpers shared by the rest."""

import ipaddress
import json
import os
import pwd
import re
import socket
import tempfile
from pathlib import Path

# Addresses that never come from the internet. Keys of paired computers are
# accepted only from these, and pairing and discovery ignore everything else.
LAN_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
_LAN = [ipaddress.ip_network(net) for net in LAN_NETS]

NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\Z")


def is_lan(address):
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    if ip.version == 4:
        return any(ip in net for net in _LAN)
    return ip.is_link_local or ip in ipaddress.ip_network("fc00::/7")


def can_pair_from(address):
    """Pairing requests: the local network, or a Tailscale/NetBird address (100.64.0.0/10)."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    return is_lan(address) or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))


def canonical(obj):
    """The one byte form of a JSON value that both ends hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_text(path, text, mode=0o644):
    """Replace a file in one step. An existing file keeps its permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write_if_changed(path, text, mode=0o644):
    if read_text(path) == text and Path(path).exists():
        return False
    write_text(path, text, mode)
    return True


def hostname():
    """This computer's name as other computers see it (name.local)."""
    name = os.environ.get("HOMEMESH_NAME") or socket.gethostname()
    return name.split(".")[0].lower()


def username():
    return pwd.getpwuid(os.getuid()).pw_name


def ssh_port():
    return int(os.environ.get("HOMEMESH_SSH_PORT", "22"))
