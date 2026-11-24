"""SSH keys: HomeMesh's own login key and this computer's host key."""

import os
import re
import subprocess

from . import paths
from .util import hostname, read_text

# An Ed25519 public key: 51 bytes of key blob, 68 characters of base64.
KEY_RE = re.compile(r"^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}\Z")


def normalize(text):
    """'ssh-ed25519 AAAA…' without the comment, or None if it is not one."""
    parts = text.split()
    if len(parts) >= 2:
        key = f"{parts[0]} {parts[1]}"
        if KEY_RE.match(key):
            return key
    return None


def ensure_identity():
    """Create the login key on first use; return its public half."""
    key, pub = paths.identity(), paths.identity_pub()
    if not key.exists() or not pub.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(key.parent, 0o700)
        for path in (key, pub):
            path.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                        "-C", f"homemesh@{hostname()}", "-f", str(key)],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    public = normalize(read_text(pub))
    if not public:
        raise RuntimeError(f"{pub}: not an Ed25519 public key")
    return public


def host_key():
    """This computer's SSH host key, or None before sshd has ever been set up."""
    try:
        return normalize(paths.host_key_pub().read_text())
    except OSError:
        return None
