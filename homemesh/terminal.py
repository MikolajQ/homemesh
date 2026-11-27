"""A terminal window logged in to a paired computer.

Every desktop has its own terminal and every terminal its own way of being
told what to run, so: the freedesktop launcher (xdg-terminal-exec) when it
is there, since it follows the user's choice; then the terminal KDE is set
to, $TERMINAL, the desktop's own one, and a list of the common ones.

The command is ssh with HomeMesh's own configuration, which already knows
the address (at home or through the VPN), the key and the host key. When
ssh cannot connect, or the other computer turns the command down, the
window waits for Enter instead of vanishing with the message.
"""

import os
import shlex
import shutil
from pathlib import Path

from . import paths, share
from .i18n import _

# How each terminal takes a command:
#   "args"     the program and its arguments follow the terminal's options
#   "string"   one string, which the terminal splits itself
# and the options in front of it.
STYLES = {
    "xdg-terminal-exec": ("args", []),
    "ptyxis": ("args", ["--new-window", "--"]),
    "kgx": ("args", ["--"]),
    "gnome-terminal": ("args", ["--"]),
    "konsole": ("args", ["-e"]),
    "xfce4-terminal": ("args", ["-x"]),
    "mate-terminal": ("args", ["-x"]),
    "terminator": ("args", ["-x"]),
    "qterminal": ("string", ["-e"]),
    "lxterminal": ("string", ["-e"]),
    "tilix": ("string", ["-e"]),
    "deepin-terminal": ("args", ["-e"]),
    "wezterm": ("args", ["start", "--"]),
    "kitty": ("args", []),
    "foot": ("args", []),
    "ghostty": ("args", ["-e"]),
    "alacritty": ("args", ["-e"]),
    "x-terminal-emulator": ("args", ["-e"]),
    "xterm": ("args", ["-e"]),
}

BY_DESKTOP = {
    "GNOME": ("ptyxis", "kgx", "gnome-terminal"),
    "KDE": ("konsole",),
    "XFCE": ("xfce4-terminal",),
    "MATE": ("mate-terminal",),
    "X-CINNAMON": ("gnome-terminal", "x-terminal-emulator"),
    "LXQT": ("qterminal",),
    "LXDE": ("lxterminal",),
    "BUDGIE": ("tilix", "gnome-terminal"),
    "DEEPIN": ("deepin-terminal",),
}

ANY = ("x-terminal-emulator", "ptyxis", "kgx", "gnome-terminal", "konsole", "xfce4-terminal",
       "mate-terminal", "qterminal", "lxterminal", "tilix", "terminator", "wezterm", "kitty",
       "ghostty", "alacritty", "foot", "xterm")


def kde_terminal():
    """The terminal chosen in Plasma's settings ([General] TerminalApplication)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    try:
        lines = (base / "kdeglobals").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    group = None
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
        elif group == "General" and line.split("=", 1)[0].strip() == "TerminalApplication":
            return line.split("=", 1)[1].strip() or None
    return None


def candidates():
    """Terminal command lines to try, the user's own choice first."""
    out = [["xdg-terminal-exec"]]
    desktops = [d.upper() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]
    if "KDE" in desktops:
        chosen = kde_terminal()
        if chosen:
            out.append(shlex.split(chosen))
    if os.environ.get("TERMINAL"):
        out.append(shlex.split(os.environ["TERMINAL"]))
    for desktop in desktops:
        out += [[name] for name in BY_DESKTOP.get(desktop, ())]
    out += [[name] for name in ANY]
    return out


def find():
    """The first terminal that is installed, as a command line, or None."""
    for command in candidates():
        if command and shutil.which(command[0]):
            return command
    return None


def wrap(terminal, command):
    """The terminal command line running `command`."""
    style, options = STYLES.get(os.path.basename(terminal[0]), ("args", ["-e"]))
    extra = [o for o in options if o not in terminal[1:]]
    if style == "string":
        return terminal + extra + [shlex.join(command)]
    return terminal + extra + command


def offered(peer):
    """Show the Terminal button for this peer? Not where it was turned off.
    Its card says so from 0.8 on; older versions have no such setting and
    allow commands with the whole home folder shared."""
    return peer.get("shell") is not False


def ssh_command(alias):
    """ssh to a peer; on failure, wait for Enter so the message can be read.

    Failure: ssh's own (255), HomeMesh refusing the command (126), or any
    error within the first seconds, which is how a version before 0.8 says
    no. A long session that ends with an error closes as usual.
    """
    script = ('start=$(date +%s); ssh -F "$1" "$2"; status=$?; '
              f'if [ $status -eq 255 ] || [ $status -eq {share.REFUSED} ] || '
              '{ [ $status -ne 0 ] && [ $(($(date +%s) - start)) -lt 5 ]; }; then '
              'printf "\\n%s " "$3"; read -r _; fi; exit $status')
    return ["sh", "-c", script, "homemesh", str(paths.ssh_config()), alias,
            _("Press Enter to close this window.")]


def command_line(alias):
    """The whole command line for a terminal logged in to `alias`, or None
    when no terminal is installed."""
    terminal = find()
    if terminal is None:
        return None
    return wrap(terminal, ssh_command(alias))
