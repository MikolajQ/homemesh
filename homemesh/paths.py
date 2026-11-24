"""Where HomeMesh keeps and writes things.

HOMEMESH_HOME replaces the home directory (tests run several "computers"
side by side this way); it also makes the XDG directories follow it.
"""

import os
import sys
from pathlib import Path

from . import APP_ID

SSHD_DROPIN = Path("/etc/ssh/sshd_config.d/10-homemesh.conf")
INSTALLED_HELPER = "/usr/lib/homemesh/homemesh-helper"
INSTALLED_ROUTE = "/usr/lib/homemesh/homemesh-route"


def home():
    override = os.environ.get("HOMEMESH_HOME")
    return Path(override) if override else Path.home()


def _xdg(variable, default):
    if not os.environ.get("HOMEMESH_HOME"):
        value = os.environ.get(variable)
        if value and os.path.isabs(value):
            return Path(value)
    return home() / default


def config_home():
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_home():
    return _xdg("XDG_DATA_HOME", ".local/share")


def config_dir():
    return config_home() / "homemesh"


def state_file():
    return config_dir() / "state.json"


def lock_file():
    return config_dir() / "lock"


def identity():
    """The private key HomeMesh logs in with; the user's own keys stay untouched."""
    return config_dir() / "id_ed25519"


def identity_pub():
    return config_dir() / "id_ed25519.pub"


def known_hosts():
    return config_dir() / "known_hosts"


def ssh_config():
    return config_dir() / "ssh_config"


def ssh_dir():
    return home() / ".ssh"


def user_ssh_config():
    return ssh_dir() / "config"


def authorized_keys():
    return ssh_dir() / "authorized_keys"


def gtk_bookmarks():
    """Read by Nautilus, Nemo, Thunar, Caja and the GTK file chooser."""
    return config_home() / "gtk-3.0" / "bookmarks"


def kde_places():
    """Places panel of Dolphin and the KDE file dialogs."""
    return data_home() / "user-places.xbel"


def autostart_file():
    return config_home() / "autostart" / f"{APP_ID}.Login.desktop"


def host_key_pub():
    return Path(os.environ.get("HOMEMESH_HOSTKEY", "/etc/ssh/ssh_host_ed25519_key.pub"))


def helper():
    override = os.environ.get("HOMEMESH_HELPER")
    if override:
        return override
    if os.path.exists(INSTALLED_HELPER):
        return INSTALLED_HELPER
    # Running from a checkout.
    return str(Path(__file__).resolve().parent.parent / "helper" / "homemesh-helper")


def route_command():
    """The local-network check SSH runs before falling back to a VPN address."""
    if os.path.exists(INSTALLED_ROUTE):
        return INSTALLED_ROUTE
    return f"{sys.executable} {Path(__file__).resolve().parent.parent / 'helper' / 'homemesh-route'}"
