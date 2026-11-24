"""homemesh: the command line. Without arguments on a desktop it opens the window."""

import argparse
import os
import shutil
import sys
import threading
import time

from . import APP_NAME, PORT, __version__, paths, proto, render, share, state, sync, system, vpn
from .discovery import Discovery
from .i18n import _
from .pairing import Session
from .proto import PairingError


def has_display():
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def print_result(result, ok_text):
    for step in result.steps:
        mark = "✓" if step.get("ok") else "✗"
        print(f"  {mark} {step.get('msg', '')}")
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
        return _("połączony bezpośrednio")
    introducer = st["peers"].get(via)
    if introducer:
        return _("przedstawiony przez {name}").format(name=introducer["name"])
    return _("przedstawiony przez inny komputer")


def cmd_status(args):
    with state.transaction() as st:
        card = state.own_card(st)
    print(_("Ten komputer: {name}  (inne komputery widzą go jako {name}.local)").format(name=card["name"]))
    if not system.sharing_configured():
        print(_("Udostępnianie: wyłączone. Włącz: homemesh enable"))
    elif not system.sshd_running():
        print(_("Udostępnianie: włączone, ale serwer SSH nie działa. Napraw: homemesh enable"))
    else:
        print(_("Udostępnianie: włączone"))
    works, how = system.mdns()
    print(_("Nazwy .local: działają ({how})").format(how=how) if works
          else _("Nazwy .local: nie działają. Napraw: homemesh enable"))
    print(_("Udostępniane: {what}").format(what=share.summary(share.current(st))))
    tunnels = card.get("vpn")
    print(_("Poza domem: przez {vpn}").format(vpn=vpn.describe(tunnels)) if tunnels
          else _("Poza domem: niedostępny (potrzebny VPN, np. Tailscale)"))
    print(_("Po zalogowaniu: uzgadnia grupę i podłącza wybrane komputery") if st.get("login", True)
          else _("Po zalogowaniu: nic (homemesh autostart on włącza)"))
    peers = st["peers"]
    if not peers:
        print(_("Połączone komputery: brak. Połącz: homemesh pair"))
        return 0
    print(_("Połączone komputery:"))
    online = system.reachable(peers)
    names = render.aliases(st)
    for pid, peer in sorted(peers.items(), key=lambda kv: kv[1]["name"]):
        status = {"lan": _("w sieci lokalnej"), "vpn": _("przez VPN")}.get(online.get(pid), _("niedostępny"))
        print(f"  {peer['name']:<16} {peer['user']}@{names[pid]:<24} {status:<12} {via_text(st, peer)}")
    return 0


def cmd_enable(args):
    return print_result(system.run_helper(["enable"], terminal=True),
                        _("Udostępnianie włączone. Teraz połącz komputery: homemesh pair"))


def cmd_disable(args):
    return print_result(system.run_helper(["disable"], terminal=True), _("Udostępnianie wyłączone."))


def cmd_rename(args):
    code = print_result(system.run_helper(["set-hostname", args.name], terminal=True),
                        _("Nowa nazwa: {name}.local").format(name=args.name))
    if code == 0:
        sync.sync_all()
    return code


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
        print(_("  Kod:  {code}").format(code=code))
        print()
        print(_("Sprawdź, czy na komputerze {name} widać ten sam kod.").format(name=card["name"]))
        print(_("Połączone komputery mają pełny dostęp do swoich katalogów domowych: łącz tylko własne."))
        ok = os.environ.get("HOMEMESH_TEST_AUTOCONFIRM") == "1" or ask_yes(_("Zgadza się? [t/N] "))
        self.session.decide(ok)
        if ok:
            print(_("Czekam na potwierdzenie na komputerze {name}…").format(name=card["name"]))

    def remote_confirmed(self):
        pass

    def paired(self, card):
        print(_("✓ Połączono z {name}.").format(name=card["name"]))

    def failed(self, message):
        print(f"✗ {message}")


def discover(seconds, tcp_port=None):
    try:
        found = Discovery(lambda: own_info(tcp_port))
    except OSError as error:
        print(_("Nie mogę nasłuchiwać w sieci ({error}).").format(error=error))
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
    print(_("Szukam komputerów z otwartym HomeMesh…"))
    _disc, nearby = discover(4)
    with state.transaction() as st:
        known = set(st["peers"])
    nearby = [entry for entry in nearby if entry["id"] not in known]
    if not nearby:
        print(_("Nie widzę żadnego. Otwórz HomeMesh na drugim komputerze (albo uruchom tam "
                "„homemesh pair --wait”) i spróbuj ponownie."))
        return None
    for number, entry in enumerate(nearby, 1):
        note = "" if entry["ready"] else _("  (udostępnianie wyłączone)")
        print(f"  {number}. {entry['name']}  ({entry['user']}, {entry['ip']}){note}")
    try:
        choice = int(input(_("Numer komputera: ")))
        entry = nearby[choice - 1]
    except (ValueError, IndexError, EOFError):
        return None
    return entry["ip"], entry["port"]


def cmd_pair(args):
    if not system.ready():
        print(_("Udostępnianie na tym komputerze jest wyłączone. Najpierw: homemesh enable"))
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
        print(_("Czekam na połączenie (port {port}). Na drugim komputerze kliknij „Połącz” przy nazwie "
                "{name}. Ctrl+C przerywa.").format(port=server.port, name=own_info()["name"]))
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
    print(_("Uzgadniam grupę z pozostałymi komputerami…"))
    sync.sync_all()
    return cmd_status(args)


def cmd_remove(args):
    with state.transaction() as st:
        pid, peer = state.find_peer(st, args.name)
        if not pid:
            print(_("Nie znam komputera „{name}”.").format(name=args.name))
            return 1
        state.remove_peer(st, pid)
        render.apply(st)
    print(_("Usunięto {name}. Pozostałe komputery dowiedzą się o tym przy najbliższym uzgadnianiu.")
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
    """Run at login (XDG autostart): catch up with the group, mount chosen peers."""
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
    if not args.yes and not ask_yes(_("Usunąć ten komputer z grupy i wszystkie ustawienia HomeMesh? [t/N] ")):
        return 1
    with state.transaction() as st:
        # A tombstone for ourselves tells the others to drop us.
        st["removed"][st["id"]] = time.time() + 1
    sync.sync_all(rounds=1)
    with state.transaction() as st:
        st["peers"], st["removed"] = {}, {}
        render.apply(st)
    shutil.rmtree(paths.config_dir(), ignore_errors=True)
    print(_("Gotowe. Udostępnianie wyłączysz poleceniem: homemesh disable"))
    return 0


def save_share(new, stop_sessions=True):
    with state.transaction() as st:
        st["share"] = new
        render.apply(st)
    print(_("Udostępniane: {what}").format(what=share.summary(new)))
    if stop_sessions:
        stopped = share.end_connections()
        if stopped:
            print(_("Zakończono bieżące połączenia ({n}); inne komputery muszą otworzyć folder ponownie.")
                  .format(n=stopped))


def cmd_share(args):
    with state.transaction() as st:
        now = share.current(st)
    if args.action is None:
        print(_("Udostępniane: {what}").format(what=share.summary(now)))
        return 0
    if args.action == "home":
        save_share({"mode": "home", "folders": now["folders"]})
        return 0
    if not args.path:
        print(_("Podaj folder."))
        return 1
    path = os.path.realpath(os.path.expanduser(args.path))
    folders = [f for f in now["folders"] if f["path"] != path]
    if args.action == "add":
        if not os.path.isdir(path):
            print(_("Nie ma takiego folderu: {path}").format(path=path))
            return 1
        if not share.sandbox_available():
            print(_("Do udostępniania wybranych folderów potrzebny jest pakiet bubblewrap."))
            return 1
        folders.append({"path": path, "readonly": args.readonly})
    save_share({"mode": "folders", "folders": folders})
    return 0


def cmd_autostart(args):
    with state.transaction() as st:
        if args.state:
            st["login"] = args.state == "on"
            render.apply(st)
        on = st.get("login", True)
    print(_("Po zalogowaniu: uzgadnia grupę i podłącza wybrane komputery") if on
          else _("Po zalogowaniu: nic"))
    return 0


def cmd_apply(args):
    with state.transaction() as st:
        render.apply(st)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="homemesh",
        description=_("{app}: katalogi domowe twoich komputerów w menedżerze plików. "
                      "Bez argumentów otwiera okno.").format(app=APP_NAME))
    parser.add_argument("--version", action="version", version=f"homemesh {__version__}")
    sub = parser.add_subparsers(dest="command", metavar=_("POLECENIE"))
    sub.add_parser("status", help=_("stan tego komputera i grupy")).set_defaults(func=cmd_status)
    sub.add_parser("enable", help=_("włącz udostępnianie (pyta o hasło administratora)")).set_defaults(func=cmd_enable)
    sub.add_parser("disable", help=_("wyłącz udostępnianie")).set_defaults(func=cmd_disable)
    pair = sub.add_parser("pair", help=_("połącz z innym komputerem (kodem)"))
    pair.add_argument("target", nargs="?", help=_("nazwa albo adres[:port] drugiego komputera"))
    pair.add_argument("--wait", action="store_true", help=_("czekaj, aż drugi komputer zacznie"))
    pair.set_defaults(func=cmd_pair)
    remove = sub.add_parser("remove", help=_("usuń komputer z grupy"))
    remove.add_argument("name")
    remove.set_defaults(func=cmd_remove)
    sub.add_parser("sync", help=_("uzgodnij grupę z dostępnymi komputerami")).set_defaults(func=cmd_sync)
    rename = sub.add_parser("rename", help=_("zmień nazwę tego komputera w sieci"))
    rename.add_argument("name")
    rename.set_defaults(func=cmd_rename)
    shared = sub.add_parser("share", help=_("co widzą inne komputery: cały katalog domowy albo wybrane foldery"))
    shared.add_argument("action", nargs="?", choices=("home", "add", "remove"),
                        help=_("home: cały katalog domowy; add/remove: wybrane foldery"))
    shared.add_argument("path", nargs="?")
    shared.add_argument("--readonly", action="store_true", help=_("folder tylko do odczytu"))
    shared.set_defaults(func=cmd_share)
    autostart = sub.add_parser("autostart", help=_("uzgadnianie grupy i podłączanie po zalogowaniu"))
    autostart.add_argument("state", nargs="?", choices=("on", "off"))
    autostart.set_defaults(func=cmd_autostart)
    reset = sub.add_parser("reset", help=_("opuść grupę i usuń ustawienia HomeMesh"))
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset)
    for hidden, func in (("login", cmd_login), ("sync-serve", lambda a: sync.serve()),
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
