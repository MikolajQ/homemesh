"""homemesh: the command line. Without arguments on a desktop it opens the window."""

import argparse
import os
import shutil
import sys
import threading
import time

from . import (APP_NAME, PORT, __version__, breaks, paths, proto, render, share, state, sync, system,
               tailscale, vpn, watch)
from .discovery import Discovery
from .i18n import _
from .pairing import Session
from .proto import PairingError


def has_display():
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def print_steps(result):
    for step in result.steps:
        mark = "✓" if step.get("ok") else "✗"
        print(f"  {mark} {system.step_text(step)}")


def print_result(result, ok_text):
    print_steps(result)
    if result.error:
        print(f"  ✗ {result.error}")
    if result.ok:
        print(ok_text)
    return 0 if result.ok else 1


def own_info(tcp_port=None):
    with state.transaction() as st:
        card = state.own_card(st)
    return {"id": card["id"], "name": card["name"], "user": card["user"],
            "port": tcp_port or PORT, "ready": system.ready()}


def via_text(st, peer):
    via = peer.get("via")
    if via == "direct":
        return _("paired directly")
    introducer = st["peers"].get(via)
    if introducer:
        return _("introduced by {name}").format(name=introducer["name"])
    return _("introduced by another computer")


def cmd_status(args):
    with state.transaction() as st:
        card = state.own_card(st)
    print(_("This computer: {name}  (other computers see it as {name}.local)").format(name=card["name"]))
    if not system.sharing_configured():
        print(_("Sharing: off. Turn on: homemesh enable"))
    elif not system.sshd_running():
        print(_("Sharing: on, but the SSH server is not running. Fix: homemesh enable"))
    else:
        print(_("Sharing: on"))
    works, how = system.mdns()
    print(_(".local names: working ({how})").format(how=how) if works
          else _(".local names: not working. Fix: homemesh enable"))
    print(_("Shared: {what}").format(what=share.summary(share.current(st))))
    warn_commands(share.current(st))
    tunnels = vpn.addresses()  # the card also has those that are down at the moment
    print(_("Away from home: through {vpn}").format(vpn=vpn.describe(tunnels)) if tunnels
          else _("Away from home: not reachable. Set up Tailscale: homemesh away"))
    print(_("At login: syncs the group, connects the chosen computers and marks the ones that are off")
          if st.get("login", True) else _("At login: nothing (turn on: homemesh autostart on)"))
    peers = st["peers"]
    if not peers:
        print(_("Paired computers: none. Pair: homemesh pair"))
        return 0
    print(_("Paired computers:"))
    online = system.reachable(peers, st["id"])
    names = render.aliases(st)
    for pid, peer in sorted(peers.items(), key=lambda kv: kv[1]["name"]):
        status = {"lan": _("on the local network"), "vpn": _("through VPN")}.get(online[pid].how, _("not reachable"))
        if online[pid].how and breaks.breaking(peer):
            status = _("through VPN, as the local network keeps breaking")
        print(f"  {peer['name']:<16} {peer['user']}@{names[pid]:<24} {status:<12} {via_text(st, peer)}")
    return 0


def cmd_enable(args):
    result = system.run_helper(["enable", "--install"] if args.install else ["enable"], terminal=True)
    if result.missing and not args.install and sys.stdin.isatty():
        print_steps(result)
        if not ask_yes(_("Install the missing packages? [y/N] ")):
            return 1
        result = system.run_helper(["enable", "--install"], terminal=True)
    return print_result(result, _("Sharing is on. Now pair your computers: homemesh pair"))


def cmd_disable(args):
    return print_result(system.run_helper(["disable"], terminal=True), _("Sharing is off."))


def cmd_rename(args):
    code = print_result(system.run_helper(["set-hostname", args.name], terminal=True),
                        _("New name: {name}.local").format(name=args.name))
    if code == 0:
        sync.sync_all()
    return code


def open_in_browser(url):
    if not has_display():
        return
    try:
        from gi.repository import Gio
        Gio.AppInfo.launch_default_for_uri(url, None)
    except Exception:
        pass  # the address is printed anyway


def cmd_away(args):
    def run_helper(helper_args):
        result = system.run_helper(helper_args, terminal=True)
        print_steps(result)
        return result

    if args.state == "off":
        return away_off(run_helper)
    tunnels = vpn.addresses()
    if tunnels and tailscale.backend_state() == "Running":
        print(_("Away from home: through {vpn}").format(vpn=vpn.describe(tunnels)))
        return 0

    def report(stage, value):
        if stage == "admin":
            print(_("Installing and starting Tailscale (asks for the administrator password)…"))
        elif stage == "connect":
            print(_("Connecting to Tailscale…"))
        elif stage == "login" and tailscale.approval_page(value):
            print(_("Approve this computer in Tailscale's admin page: {url}").format(url=value))
            open_in_browser(value)
        elif stage == "login":
            print(_("Log in to Tailscale in your browser (the same account on all your computers): "
                    "{url}").format(url=value))
            open_in_browser(value)

    setup = tailscale.Setup(run_helper, report)
    try:
        error = setup.run()
    except KeyboardInterrupt:
        setup.cancel()
        raise
    if error:
        print(f"✗ {error}")
        return 1
    print(_("Away from home: through {vpn}").format(vpn=vpn.describe(setup.addresses)) if setup.addresses
          else _("This computer is connected to Tailscale."))
    print(_("Now set up Tailscale the same way on your other computers, with the same account."))
    if state.load()["peers"]:
        sync.sync_all()  # the group learns this computer's VPN address
    return 0


def away_off(run_helper):
    error = tailscale.turn_off(run_helper)
    if error:
        print(f"✗ {error}")
        return 1
    with state.transaction() as st:
        state.forget_vpn(st)
    tunnels = vpn.addresses()
    print(_("Away from home: still through {vpn}, which HomeMesh does not turn off").format(
        vpn=vpn.describe(tunnels)) if tunnels else _("Away from home: off. Turn on: homemesh away"))
    if state.load()["peers"]:
        sync.sync_all()  # the group forgets this computer's VPN address
    return 0


def ask_yes(question):
    try:
        return input(question).strip().lower().startswith(("t", "y"))
    except EOFError:
        return False


class TerminalUI:
    def __init__(self):
        self.session = None

    def code(self, card, code):
        print()
        print(_("  Code:  {code}").format(code=code))
        print()
        print(_("Check that {name} shows the same code.").format(name=card["name"]))
        print(_("Paired computers have full access to each other's home folders: pair only your "
                "own."))
        ok = os.environ.get("HOMEMESH_TEST_AUTOCONFIRM") == "1" or ask_yes(_("Does it match? [y/N] "))
        self.session.decide(ok)
        if ok:
            print(_("Waiting for confirmation on {name}…").format(name=card["name"]))

    def remote_confirmed(self):
        pass

    def paired(self, card):
        print(_("✓ Paired with {name}.").format(name=card["name"]))

    def failed(self, message):
        print(f"✗ {message}")


def discover(seconds, tcp_port=None):
    try:
        found = Discovery(lambda: own_info(tcp_port))
    except OSError as error:
        print(_("Cannot listen on the network ({error}).").format(error=error))
        return None, []
    time.sleep(seconds)
    return found, found.nearby()


def pick_target(args):
    if args.target:
        _disc, nearby = discover(3)
        for entry in nearby:
            if entry["name"] == args.target.lower().removesuffix(".local"):
                return entry["ip"], entry["port"]
        host, _sep, port = args.target.rpartition(":")
        if host and port.isdigit():
            return host, int(port)
        return args.target, PORT
    print(_("Looking for computers with HomeMesh open…"))
    _disc, nearby = discover(4)
    with state.transaction() as st:
        known = set(st["peers"])
    nearby = [entry for entry in nearby if entry["id"] not in known]
    if not nearby:
        print(_("None found. Open HomeMesh on the other computer (or run “homemesh pair --wait” "
                "there) and try again."))
        return None
    for number, entry in enumerate(nearby, 1):
        note = "" if entry["ready"] else _("  (sharing is off)")
        print(f"  {number}. {entry['name']}  ({entry['user']}, {entry['ip']}){note}")
    try:
        choice = int(input(_("Computer number: ")))
        entry = nearby[choice - 1]
    except (ValueError, IndexError, EOFError):
        return None
    return entry["ip"], entry["port"]


def cmd_pair(args):
    if not system.ready():
        print(_("Sharing is off on this computer. First: homemesh enable"))
        return 1
    ui = TerminalUI()
    session = Session(ui)
    ui.session = session
    if args.wait:
        finished = threading.Event()
        busy = threading.Lock()
        outcome = {"ok": False}

        def handler(chan, address):
            try:
                hello = proto.read_hello(chan)
            except PairingError:
                return  # not a pairing request (a port check, a scanner): keep waiting
            if not busy.acquire(blocking=False):
                proto.refuse(chan, "busy")
                return
            try:
                outcome["ok"] = session.answer(chan, hello)
            finally:
                finished.set()

        server = proto.PairServer(handler)
        try:
            Discovery(lambda: own_info(server.port))
        except OSError:
            pass
        print(_("Waiting to be paired (port {port}). On the other computer, click “Connect” next "
                "to {name}. Ctrl+C stops.").format(port=server.port, name=own_info()["name"]))
        try:
            while not finished.wait(0.5):
                pass
        except KeyboardInterrupt:
            print()
            return 1
        ok = outcome["ok"]
    else:
        target = pick_target(args)
        if not target:
            return 1
        ok = session.ask(*target)
    if not ok:
        return 1
    print(_("Catching up with the rest of the group…"))
    sync.sync_all()
    watch.start(state.load())
    return cmd_status(args)


def cmd_remove(args):
    with state.transaction() as st:
        pid, peer = state.find_peer(st, args.name)
        if not pid:
            print(_("No computer called “{name}”.").format(name=args.name))
            return 1
        state.remove_peer(st, pid)
        render.apply(st)
    print(_("Removed {name}. The other computers will learn about it the next time they sync.")
          .format(name=peer["name"]))
    sync.sync_all()
    return 0


def cmd_sync(args):
    report = sync.sync_all()
    st = state.load()
    for pid, error in report.items():
        name = st["peers"].get(pid, {}).get("name", pid[:8])
        print(f"  {'✓' if error is None else '✗'} {name}" + (f": {error}" if error else ""))
    return 0


def wait_for_network(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with open("/proc/net/route") as routes:
                if any(line.split()[1] == "00000000" for line in list(routes)[1:] if line.split()):
                    return True
        except OSError:
            return True
        time.sleep(1)
    return False


def mount_all(uris, timeout=60):
    from gi.repository import Gio, GLib
    loop = GLib.MainLoop()
    pending = [len(uris)]

    def done(gfile, result):
        try:
            gfile.mount_enclosing_volume_finish(result)
        except GLib.Error as error:
            if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                print(f"homemesh: {gfile.get_uri()}: {error.message}", file=sys.stderr)
        pending[0] -= 1
        if pending[0] == 0:
            loop.quit()

    for uri in uris:
        Gio.File.new_for_uri(uri).mount_enclosing_volume(Gio.MountMountFlags.NONE, None, None, done)
    GLib.timeout_add_seconds(timeout, loop.quit)
    loop.run()


def cmd_login(args):
    """Run at login (XDG autostart): start the watcher, catch up with the group, mount chosen peers."""
    watch.start(state.load())
    wait_for_network(30)
    sync.sync_all()
    st = state.load()
    names = render.aliases(st)
    uris = [render.peer_uri(peer, names[pid]) for pid, peer in st["peers"].items()
            if peer.get("automount")]
    if uris and system.gvfs_sftp():
        mount_all(uris)
    return 0


def cmd_reset(args):
    if not args.yes and not ask_yes(_("Remove this computer from the group and all HomeMesh "
                                      "settings? [y/N] ")):
        return 1
    with state.transaction() as st:
        # A tombstone for ourselves tells the others to drop us.
        st["removed"][st["id"]] = time.time() + 1
    watch.stop()
    sync.sync_all(rounds=1)
    with state.transaction() as st:
        st["peers"], st["removed"] = {}, {}
        render.apply(st)
    shutil.rmtree(paths.config_dir(), ignore_errors=True)
    print(_("Done. To turn sharing off as well: homemesh disable"))
    return 0


def warn_commands(shared):
    if share.allows_commands(shared):
        print(_("  ! Paired computers can run any program on this account, as you. "
                "Turn off: homemesh share terminal off"))


def save_share(new, stop_sessions=True):
    with state.transaction() as st:
        st["share"] = new
        render.apply(st)
    print(_("Shared: {what}").format(what=share.summary(new)))
    warn_commands(new)
    if stop_sessions:
        stopped = share.end_connections()
        if stopped:
            print(_("Ended {n} running connections; other computers have to open the folder again.")
                  .format(n=stopped))


def cmd_share(args):
    with state.transaction() as st:
        now = share.current(st)
    if args.action is None:
        print(_("Shared: {what}").format(what=share.summary(now)))
        warn_commands(now)
        return 0
    if args.action == "home":
        save_share(dict(now, mode="home"))
        return 0
    if args.action == "terminal":
        if args.path not in ("on", "off"):
            print(_("Say on or off: homemesh share terminal on|off"))
            return 1
        if args.path == "on" and now["mode"] != "home":
            print(_("The terminal goes with the whole home folder. First: homemesh share home"))
            return 1
        save_share(dict(now, shell=args.path == "on"), stop_sessions=False)
        print(_("Other computers learn of it when they next sync (homemesh sync does it now)."))
        return 0
    if not args.path:
        print(_("Name a folder."))
        return 1
    path = os.path.realpath(os.path.expanduser(args.path))
    folders = [f for f in now["folders"] if f["path"] != path]
    if args.action == "add":
        if not os.path.isdir(path):
            print(_("No such folder: {path}").format(path=path))
            return 1
        missing = share.sandbox_missing()
        if missing:
            print(_("Sharing chosen folders needs: {packages}.").format(packages=system.thing_names(missing)))
            if not sys.stdin.isatty() or not ask_yes(_("Install the missing packages? [y/N] ")):
                return 1
            if print_result(system.run_helper(["install", *missing], terminal=True), _("Installed.")):
                return 1
        folders.append({"path": path, "readonly": args.readonly})
    save_share(dict(now, mode="folders", folders=folders))
    return 0


def cmd_autostart(args):
    with state.transaction() as st:
        if args.state:
            st["login"] = args.state == "on"
            render.apply(st)
        on = st.get("login", True)
    if args.state:
        watch.start(st) if on else watch.stop()
    print(_("At login: syncs the group, connects the chosen computers and marks the ones that are off")
          if on else _("At login: nothing"))
    return 0


def cmd_apply(args):
    with state.transaction() as st:
        render.apply(st)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="homemesh",
        description=_("{app}: the home folders of your computers in your file manager. Without "
                      "arguments it opens the window.").format(app=APP_NAME))
    parser.add_argument("--version", action="version", version=f"homemesh {__version__}")
    sub = parser.add_subparsers(dest="command", metavar=_("COMMAND"))
    sub.add_parser("status", help=_("the state of this computer and the group")).set_defaults(func=cmd_status)
    enable = sub.add_parser("enable", help=_("turn sharing on (asks for the administrator password)"))
    enable.add_argument("--install", action="store_true", help=_("install missing packages without asking"))
    enable.set_defaults(func=cmd_enable)
    sub.add_parser("disable", help=_("turn sharing off")).set_defaults(func=cmd_disable)
    pair = sub.add_parser("pair", help=_("pair with another computer (with a code)"))
    pair.add_argument("target", nargs="?", help=_("the other computer's name or address[:port]"))
    pair.add_argument("--wait", action="store_true", help=_("wait for the other computer to start"))
    pair.set_defaults(func=cmd_pair)
    remove = sub.add_parser("remove", help=_("remove a computer from the group"))
    remove.add_argument("name")
    remove.set_defaults(func=cmd_remove)
    sub.add_parser("sync", help=_("sync the group with the reachable computers")).set_defaults(func=cmd_sync)
    away = sub.add_parser("away", help=_("reach the computers away from home: sets up Tailscale "
                                         "(asks for the administrator password)"))
    away.add_argument("state", nargs="?", choices=("on", "off"), help=_("off: turns Tailscale off"))
    away.set_defaults(func=cmd_away)
    rename = sub.add_parser("rename", help=_("rename this computer on the network"))
    rename.add_argument("name")
    rename.set_defaults(func=cmd_rename)
    shared = sub.add_parser("share", help=_("what other computers see: the whole home folder or "
                                            "chosen folders"))
    shared.add_argument("action", nargs="?", choices=("home", "add", "remove", "terminal"),
                        help=_("home: the whole home folder; add/remove: chosen folders; "
                               "terminal on/off: whether paired computers may run commands"))
    shared.add_argument("path", nargs="?", help=_("a folder, or on/off after terminal"))
    shared.add_argument("--readonly", action="store_true", help=_("read-only folder"))
    shared.set_defaults(func=cmd_share)
    autostart = sub.add_parser("autostart", help=_("syncing the group, connecting computers and marking "
                                                   "the ones that are off, from login on"))
    autostart.add_argument("state", nargs="?", choices=("on", "off"))
    autostart.set_defaults(func=cmd_autostart)
    reset = sub.add_parser("reset", help=_("leave the group and remove HomeMesh's settings"))
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset)
    for hidden, func in (("login", cmd_login), ("watch", lambda a: watch.run()), ("sync-serve", lambda a: sync.serve()),
                         ("gate", lambda a: share.gate()), ("apply", cmd_apply)):
        sub.add_parser(hidden).set_defaults(func=func)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    window_args = not argv or (argv[0].startswith("--") and argv[0] not in ("--help", "--version"))
    if window_args and has_display():
        from .gui import run
        return run(argv)
    parser = build_parser()
    args = parser.parse_args(argv or ["status"])
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
