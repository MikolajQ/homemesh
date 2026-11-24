"""Turn the group into files: SSH access, file manager bookmarks, autostart.

Everything here can run any number of times: it writes what the group says
and removes what HomeMesh wrote earlier but no longer needs. Lines and
bookmarks that HomeMesh did not write are never touched.
"""

import html
import re
import urllib.parse
import xml.dom.minidom

from . import APP_ID, keys, paths, share
from .i18n import _
from .util import LAN_NETS, read_text, write_if_changed, write_text

KEY_MARK_RE = re.compile(r"\shomemesh:[0-9a-f]{32}(\s|$)")
INCLUDE_TAG = "# homemesh:"
KDE_BOOKMARK_RE = re.compile(r"[ \t]*<bookmark\b[^>]*>.*?</bookmark>[ \t]*\n?", re.S)


def aliases(st):
    """The name each peer goes by in SSH and in bookmarks: name.local."""
    out, taken = {}, set()
    for pid, peer in sorted(st["peers"].items(), key=lambda kv: (-kv[1]["updated"], kv[0])):
        alias = f"{peer['name']}.local"
        if alias in taken:  # two computers with one name; the newer keeps it
            alias = f"{peer['name']}-{pid[:6]}.local"
        taken.add(alias)
        out[pid] = alias
    return out


def peer_uri(peer, alias):
    return f"sftp://{alias}{urllib.parse.quote(peer['home'])}"


def ssh_config_text(st, names):
    lines = ["# Written by homemesh. Do not edit: changes are overwritten.", ""]
    for pid, peer in sorted(st["peers"].items(), key=lambda kv: names[kv[0]]):
        local = peer.get("address") or peer["name"] + ".local"
        tunnels = peer.get("vpn") or []
        if tunnels:
            # Away from home: when the computer does not answer on the local
            # network, use its VPN address. This block comes first because
            # SSH keeps the first value it finds for each option.
            lines += [
                f'Match originalhost {names[pid]} !exec "{paths.route_command()} {local} {peer["port"]}"',
                f"    HostName {tunnels[0]['addr']}",
                "    AddressFamily any",
                "",
            ]
        lines += [
            f"Host {names[pid]}",
            f"    HostName {local}",
            f"    Port {peer['port']}",
            f"    User {peer['user']}",
            f"    HostKeyAlias homemesh-{pid}",
            f'    UserKnownHostsFile "{paths.known_hosts()}"',
            "    StrictHostKeyChecking yes",
            "    UpdateHostKeys no",
            f'    IdentityFile "{paths.identity()}"',
            "    IdentitiesOnly yes",
            # Peers are reached over IPv4: link-local IPv6 from mDNS lacks
            # the interface and fails to connect.
            "    AddressFamily inet",
            "    ConnectTimeout 5",
            # Notice a peer that went to sleep in ~45 s instead of minutes.
            "    ServerAliveInterval 15",
            "    ServerAliveCountMax 3",
            "",
        ]
    return "\n".join(lines)


def known_hosts_text(st, names):
    # OpenSSH looks keys up by HostKeyAlias. The plain names are for other
    # SFTP clients (Dolphin) that read the same file by host name.
    lines = []
    for pid, peer in sorted(st["peers"].items()):
        hosts = [f"homemesh-{pid}", names[pid]]
        if names[pid] != f"{peer['name']}.local":
            hosts.append(f"{peer['name']}.local")
        lines.append(f"{','.join(hosts)} {peer['hostkey']}")
    return "".join(line + "\n" for line in lines)


def key_options(st, peer):
    """From the local network, or from this peer's own VPN addresses.

    Whole home: the peer's key works like your own. Chosen folders: only
    SFTP inside the sandbox (and group syncing), no shell, no forwarding.
    """
    sources = LAN_NETS + ("127.0.0.0/8",) + tuple(item["addr"] for item in peer.get("vpn") or [])
    options = 'from="' + ",".join(sources) + '"'
    if share.current(st)["mode"] == "folders":
        options += f',restrict,command="{share.gate_command()}"'
    return options


def authorized_keys_text(st, current):
    kept = [line for line in current.splitlines() if not KEY_MARK_RE.search(line)]
    ours = [f"{key_options(st, peer)} {peer['userkey']} homemesh:{pid} {peer['name']}"
            for pid, peer in sorted(st["peers"].items())]
    lines = kept + ours
    return "".join(line + "\n" for line in lines)


def include_lines():
    return ["# homemesh: access to paired computers (removed by \"homemesh reset\")",
            f'Include "{paths.ssh_config()}"']


def user_ssh_config_text(current, present):
    """~/.ssh/config with our Include as its first lines, or without it.

    An Include works for every host only before the first Host or Match
    line, hence the top of the file.
    """
    include = include_lines()[1]
    lines = current.splitlines()
    removed = [line for line in lines if line.startswith(INCLUDE_TAG) or line.strip() == include]
    lines = [line for line in lines if not (line.startswith(INCLUDE_TAG) or line.strip() == include)]
    if removed and lines and not lines[0].strip():
        lines = lines[1:]  # the blank line we put after our lines
    if present:
        lines = include_lines() + ([""] if lines else []) + lines
    return "".join(line + "\n" for line in lines)


def _sync_list(st, key, desired, present_in_file):
    """Which of `desired` to add: new ones only.

    A bookmark the person deleted stays deleted; it comes back only if its
    address changes (the computer was renamed) or the peer is paired again.
    """
    before = set(st.get(key, []))
    st[key] = sorted(desired)
    return [url for url in desired if url not in present_in_file and url not in before]


def gtk_bookmarks(st, desired):
    """desired: {uri: (label, peer id)}"""
    path = paths.gtk_bookmarks()
    current = read_text(path)
    ours_before = set(st.get("bookmarks", []))
    out, present = [], set()
    for line in current.splitlines():
        url = line.split(" ", 1)[0]
        if url in ours_before or url in desired:
            if url in desired and url not in present:
                out.append(f"{url} {desired[url][0]}")
                present.add(url)
            continue
        out.append(line)
    for url in _sync_list(st, "bookmarks", desired, present):
        out.append(f"{url} {desired[url][0]}")
    text = "".join(line + "\n" for line in out)
    if text != current and (path.exists() or out):
        write_text(path, text)


def _kde_block(url, label, pid, with_icon):
    icon = ('    <bookmark:icon name="folder-remote"/>\n' if with_icon else "")
    return (f' <bookmark href="{html.escape(url)}">\n'
            f"  <title>{html.escape(label)}</title>\n"
            "  <info>\n"
            '   <metadata owner="http://freedesktop.org">\n'
            f"{icon}"
            "   </metadata>\n"
            '   <metadata owner="http://www.kde.org">\n'
            f"    <ID>homemesh-{pid}</ID>\n"
            "    <isSystemItem>false</isSystemItem>\n"
            "   </metadata>\n"
            "  </info>\n"
            " </bookmark>\n")


def kde_places(st, desired):
    """Same as gtk_bookmarks, for KDE. Only if the person uses KDE already."""
    path = paths.kde_places()
    current = read_text(path)
    if "</xbel>" not in current:
        return
    present = set()

    def keep(match):
        block = match.group(0)
        if "<ID>homemesh-" not in block:
            return block
        href = re.search(r'href="([^"]*)"', block)
        url = html.unescape(href.group(1)) if href else ""
        if url in desired and url not in present:
            present.add(url)
            return block
        return ""

    text = KDE_BOOKMARK_RE.sub(keep, current)
    with_icon = "xmlns:bookmark=" in text
    added = "".join(_kde_block(url, desired[url][0], desired[url][1], with_icon)
                    for url in _sync_list(st, "kde", desired, present))
    if added:
        at = text.rfind("</xbel>")
        text = text[:at] + added + text[at:]
    if text == current:
        return
    try:
        xml.dom.minidom.parseString(text.encode())
    except Exception:  # never hand KDE a broken file
        return
    write_text(path, text)


def autostart(present):
    path = paths.autostart_file()
    if not present:
        path.unlink(missing_ok=True)
        return
    write_if_changed(path, "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=HomeMesh",
        "Comment=Syncs the group and connects the chosen computers at login",
        "Comment[pl]=Uzgadnia grupę i podłącza wybrane komputery po zalogowaniu",
        "Exec=homemesh login",
        f"Icon={APP_ID}",
        "NoDisplay=true",
        "X-GNOME-Autostart-Delay=5",
        "",
    ]))


def apply(st):
    keys.ensure_identity()
    names = aliases(st)
    peers = st["peers"]
    write_if_changed(paths.ssh_config(), ssh_config_text(st, names), 0o600)
    write_if_changed(paths.known_hosts(), known_hosts_text(st, names), 0o600)

    ssh_dir = paths.ssh_dir()
    for path, build in ((paths.authorized_keys(), lambda cur: authorized_keys_text(st, cur)),
                        (paths.user_ssh_config(), lambda cur: user_ssh_config_text(cur, bool(peers)))):
        current = read_text(path)
        text = build(current)
        if text != current and (path.exists() or peers):
            if not ssh_dir.exists():
                ssh_dir.mkdir(mode=0o700, parents=True)
            write_text(path, text, 0o600)

    desired = {peer_uri(peer, names[pid]): (peer["name"], pid) for pid, peer in peers.items()}
    gtk_bookmarks(st, desired)
    kde_places(st, desired)
    autostart(bool(peers) and st.get("login", True))
