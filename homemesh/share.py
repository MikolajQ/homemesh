"""What the paired computers may see on this one.

"home"     The whole home folder, with the same rights as your own account
           (that includes running commands: whoever can write to ~ could do
           that anyway, through ~/.bashrc or autostart).
"folders"  Only chosen folders, each read-write or read-only. The keys of
           paired computers then go through `homemesh gate`, which serves
           SFTP from a bubblewrap sandbox holding just those folders, placed
           inside an otherwise empty home folder, and refuses everything else
           except syncing the group. Nothing outside the sandbox exists for
           the other computer: not the rest of ~, not ~/.ssh, not /etc.
"""

import grp
import os
import pwd
import shutil
import signal
import sys
from pathlib import Path

from . import paths, state
from .i18n import _

SFTP_SERVERS = ("/usr/lib/ssh/sftp-server", "/usr/lib/openssh/sftp-server",
                "/usr/libexec/openssh/sftp-server", "/usr/libexec/sftp-server")


def default():
    return {"mode": "home", "folders": []}


def current(st):
    share = st.get("share")
    if not isinstance(share, dict) or share.get("mode") not in ("home", "folders"):
        return default()
    folders = [{"path": f["path"], "readonly": bool(f.get("readonly"))}
               for f in share.get("folders", [])
               if isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"].startswith("/")]
    return {"mode": share["mode"], "folders": folders}


def sftp_server():
    for path in SFTP_SERVERS:
        if os.access(path, os.X_OK):
            return path
    return None


def sandbox_available():
    return shutil.which("bwrap") is not None and sftp_server() is not None


def mount_points(folders, home):
    """[(folder, where the other computer sees it, read-only)], inside the home folder."""
    home = Path(home)
    taken, points = set(), []
    for folder in folders:
        source = Path(folder["path"])
        if not source.is_dir():
            continue  # removed or unmounted since; skip quietly
        if source == home:
            target = home
        else:
            name = source.name or "root"
            label, number = name, 2
            while label in taken:
                label, number = f"{name} ({number})", number + 1
            taken.add(label)
            target = home / label
        points.append((source, target, folder["readonly"]))
    # The home folder itself (if shared) first, so the others land on top of it.
    return sorted(points, key=lambda point: len(point[1].parts))


def _system_dirs():
    args = ["--ro-bind", "/usr", "/usr"]
    for name in ("/bin", "/sbin", "/lib", "/lib64"):
        if os.path.islink(name):
            args += ["--symlink", os.readlink(name), name]
        elif os.path.isdir(name):
            args += ["--ro-bind", name, name]
    return args + ["--ro-bind-try", "/etc/localtime", "/etc/localtime"]


def account_files():
    """/etc/passwd and /etc/group with only this user: sftp-server looks itself
    up at start, and the other computer has no business seeing the other accounts."""
    me = pwd.getpwuid(os.getuid())
    try:
        group = grp.getgrgid(me.pw_gid).gr_name
    except KeyError:
        group = me.pw_name
    passwd = f"{me.pw_name}:x:{me.pw_uid}:{me.pw_gid}::{me.pw_dir}:/usr/bin/nologin\n"
    return {"/etc/passwd": passwd, "/etc/group": f"{group}:x:{me.pw_gid}:\n"}


def _pipe_with(text):
    read, write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    os.set_inheritable(read, True)
    return read


def sandbox_command(share, home, server, files=None):
    """bwrap command line; `files` maps a path in the sandbox to a readable fd."""
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
               *_system_dirs(), "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for destination, fd in (files or {}).items():
        command += ["--file", str(fd), destination]
    command += ["--dir", str(home)]
    for source, target, readonly in mount_points(share["folders"], home):
        command += ["--ro-bind" if readonly else "--bind", str(source), str(target)]
    return command + ["--chdir", str(home), server, "-d", str(home)]


def gate_command():
    """What authorized_keys runs for paired computers in the "folders" mode."""
    override = os.environ.get("HOMEMESH_GATE")
    if override:
        return override
    if os.path.exists("/usr/bin/homemesh"):
        return "/usr/bin/homemesh gate"
    launcher = Path(__file__).resolve().parent.parent / "bin" / "homemesh"
    return f"{sys.executable} {launcher} gate"


def is_sftp(command):
    words = command.split()
    return bool(words) and os.path.basename(words[0]) in ("sftp-server", "internal-sftp", "sftp")


def gate():
    """Run by sshd for a paired computer's key when only some folders are shared."""
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
    if original == "homemesh sync-serve":
        from . import sync
        return sync.serve()
    if is_sftp(original):
        share = current(state.load())
        server = sftp_server()
        if server is None:
            sys.stderr.write("homemesh: no sftp-server\n")
            return 1
        if share["mode"] == "home":
            os.execv(server, [server])
        if not shutil.which("bwrap"):
            sys.stderr.write(_("HomeMesh: the bubblewrap package is missing, so nothing is shared.\n"))
            return 1
        files = {path: _pipe_with(text) for path, text in account_files().items()}
        command = sandbox_command(share, paths.home(), server, files)
        os.execvp(command[0], command)
    sys.stderr.write(_("HomeMesh: this computer shares only chosen folders, over SFTP.\n"))
    return 1


def summary(share):
    if share["mode"] == "home":
        return _("The whole home folder")
    if not share["folders"]:
        return _("Nothing (no folder chosen)")
    parts = []
    for folder in share["folders"]:
        name = Path(folder["path"]).name or folder["path"]
        parts.append(f"{name} ({_("read-only") if folder['readonly'] else _("read-write")})")
    return ", ".join(parts)


def end_connections():
    """Stop running SFTP sessions of this user, so a change applies now.

    A session started before the change would otherwise keep its old view
    until the other computer disconnects. Returns how many were stopped.
    """
    uid, stopped = os.getuid(), 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid or _comm(entry) != "sftp-server":
                continue
            if not _started_by_sshd(entry):
                continue
            os.kill(int(entry), signal.SIGTERM)
            stopped += 1
        except (OSError, ValueError):
            continue
    return stopped


def _comm(pid):
    with open(f"/proc/{pid}/comm") as f:
        return f.read().strip()


def _started_by_sshd(pid):
    for _level in range(6):
        with open(f"/proc/{pid}/stat") as f:
            pid = f.read().rsplit(")", 1)[1].split()[1]  # the parent
        if pid in ("0", "1"):
            return False
        if _comm(pid) in ("sshd", "sshd-session"):
            return True
    return False
