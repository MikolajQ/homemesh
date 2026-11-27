"""The HomeMesh window: GTK 4 without libadwaita, so it fits any desktop.

One window with a bar on top that always says what to do next, this
computer with its sharing switch, the paired computers and the computers
nearby that can be paired. Networking runs in threads and reports back
through GLib.idle_add.
"""

import os
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import (APP_ID, APP_NAME, __version__, breaks, paths, proto, render, share, state, sync,  # noqa: E402
               system, tailscale, vpn, watch)
from .discovery import Discovery  # noqa: E402
from .i18n import _  # noqa: E402
from .pairing import Session  # noqa: E402
from .proto import PairingError  # noqa: E402
from .util import NAME_RE, hostname, username  # noqa: E402

CSS = """
.hint { border-radius: 12px; padding: 12px 14px; background-color: alpha(@theme_selected_bg_color, 0.14); }
.hint.problem { background-color: alpha(@warning_color, 0.20); }
.hint.done { background-color: alpha(@success_color, 0.16); }
.hint-title { font-weight: bold; }
list.boxed { border: 1px solid alpha(currentColor, 0.15); border-radius: 12px; }
list.boxed > row { padding: 10px 12px; }
list.boxed > row:first-child { border-top-left-radius: 12px; border-top-right-radius: 12px; }
list.boxed > row:last-child { border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }
list.boxed > row:not(:last-child) { border-bottom: 1px solid alpha(currentColor, 0.08); }
.heading { font-weight: bold; }
.dim { opacity: 0.7; }
.small { font-size: 0.9em; }
.big { font-size: 1.3em; font-weight: bold; }
.dot-on { color: #26a269; }
.dot-off { color: alpha(currentColor, 0.3); }
.code { font-size: 34pt; font-weight: bold; font-family: monospace; }
.toast { background-color: rgba(30, 30, 30, 0.94); color: #ffffff; border-radius: 99px;
         padding: 8px 18px; margin: 18px; }
.placeholder { padding: 18px; }
.monospace { font-family: monospace; }
"""


# A check mark: Adwaita no longer has emblem-ok-symbolic.
OK_ICON = "object-select-symbolic"
MANY_ROAMS = 10  # access points in an hour: more than moving around with a laptop


def label(text="", *classes, wrap=False, ellipsize=False, xalign=0.0, chars=56):
    widget = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_max_width_chars(chars)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    for css in classes:
        widget.add_css_class(css)
    return widget


def vbox(spacing=6, *children, margin=0):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(margin)
    for child in children:
        box.append(child)
    return box


def hbox(spacing=12, *children):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    for child in children:
        box.append(child)
    return box


def button(text, callback, *classes):
    widget = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
    widget.connect("clicked", lambda *_args: callback())
    for css in classes:
        widget.add_css_class(css)
    return widget


def clear(listbox):
    """The rows only: remove_all() takes the placeholder too."""
    while (row := listbox.get_row_at_index(0)) is not None:
        listbox.remove(row)


def idle(function, *args):
    def call():
        function(*args)
        return False
    GLib.idle_add(call)


class Toast:
    def __init__(self, overlay):
        self.label = label("", "toast", wrap=True, xalign=0.5)
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.END, can_target=False)
        self.revealer.set_child(self.label)
        overlay.add_overlay(self.revealer)
        self.timer = None

    def show(self, text):
        self.label.set_text(text)
        self.revealer.set_reveal_child(True)
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(4000, self._hide)

    def _hide(self):
        self.revealer.set_reveal_child(False)
        self.timer = None
        return False


class HintBar(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("hint")
        self.icon = Gtk.Image(valign=Gtk.Align.START)
        self.icon.set_pixel_size(24)
        self.title = label("", "hint-title", wrap=True)
        self.text = label("", wrap=True)
        texts = vbox(2, self.title, self.text)
        texts.set_hexpand(True)
        self.action = Gtk.Button(valign=Gtk.Align.CENTER)
        self.action.add_css_class("suggested-action")
        self.action.connect("clicked", lambda *_a: self.callback and self.callback())
        self.callback = None
        for child in (self.icon, texts, self.action):
            self.append(child)

    def set(self, kind, icon, title, text, action=None, callback=None):
        for css in ("problem", "done"):
            self.remove_css_class(css)
        if kind in ("problem", "done"):
            self.add_css_class(kind)
        self.icon.set_from_icon_name(icon)
        self.title.set_text(title)
        self.text.set_text(text)
        self.action.set_visible(bool(action))
        self.action.set_label(action or "")
        self.callback = callback


class Dialog(Gtk.Window):
    """A small modal window with a column of content and buttons at the bottom."""

    def __init__(self, parent, title, width=440):
        super().__init__(transient_for=parent, modal=True, title=title, default_width=width,
                         resizable=False)
        self.body = vbox(12, margin=20)
        self.buttons = hbox(8)
        self.buttons.set_halign(Gtk.Align.END)
        self.buttons.set_margin_top(6)
        self.set_child(self.body)
        self.body.append(self.buttons)

    def content(self, *widgets):
        """Add widgets above the buttons."""
        for widget in widgets:
            self.body.insert_child_after(widget, self.buttons.get_prev_sibling())

    def clear_buttons(self):
        child = self.buttons.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.buttons.remove(child)
            child = following

    def add_button(self, text, callback, *classes):
        widget = button(text, callback, *classes)
        self.buttons.append(widget)
        return widget


def page_stack():
    """Pages of a dialog, all as high as the highest: GTK widens a dialog that
    gets lower, to fit its text in fewer lines."""
    return Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=True)


def page(*children, spacing=12, margin=0):
    box = vbox(spacing, *children, margin=margin)
    box.set_valign(Gtk.Align.CENTER)
    return box


def confirm(parent, title, text, action, callback):
    dialog = Dialog(parent, title)
    dialog.content(label(title, "big", wrap=True), label(text, wrap=True))
    dialog.add_button(_("Cancel"), dialog.close)

    def accept():
        dialog.close()
        callback()
    dialog.add_button(action, accept, "destructive-action")
    dialog.present()


class StepsDialog(Dialog):
    """What the root helper did, when something failed. install: called to
    install what is missing."""

    def __init__(self, parent, result, title, install=None):
        super().__init__(parent, title, 480)
        self.content(label(title, "big", wrap=True))
        for item in result.steps:
            icon = Gtk.Image.new_from_icon_name(OK_ICON if item.get("ok")
                                                else "dialog-warning-symbolic")
            icon.set_valign(Gtk.Align.START)
            self.content(hbox(10, icon, label(system.step_text(item), wrap=True, chars=50)))
        if result.error:
            self.content(label(result.error, wrap=True))
        if not install:
            self.add_button(_("Close"), self.close, "suggested-action")
            return
        self.add_button(_("Close"), self.close)
        self.set_focus(self.add_button(_("Install missing packages"), lambda: (self.close(), install()),
                                       "suggested-action"))


class RenameDialog(Dialog):
    def __init__(self, parent, current, callback):
        super().__init__(parent, _("Computer name"))
        self.callback = callback
        self.entry = Gtk.Entry(text=current)
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", lambda *_a: self._apply())
        self.error = label("", "dim", "small", wrap=True)
        self.content(label(_("Computer name on the network"), "big"),
                     label(_("Other computers will see it under this name. Lowercase letters, "
                             "digits and hyphens, for example “annas-laptop”."), wrap=True),
                     self.entry, self.error)
        self.add_button(_("Cancel"), self.close)
        self.ok = self.add_button(_("Change"), self._apply, "suggested-action")
        self._changed()

    def _changed(self, *_args):
        name = self.entry.get_text().strip().lower()
        valid = bool(NAME_RE.match(name))
        self.ok.set_sensitive(valid)
        self.error.set_text("" if valid or not name else _("Only lowercase letters, digits and "
                                                           "hyphens."))

    def _apply(self):
        name = self.entry.get_text().strip().lower()
        if NAME_RE.match(name):
            self.close()
            self.callback(name)


class AwayDialog(Dialog):
    """Access from away: what it takes, and Tailscale set up at a click."""

    def __init__(self, parent, tunnels):
        super().__init__(parent, _("Away from home"), 520)
        self.window = parent
        self.setup = None
        self.url = None
        self.working = False
        self.stack = page_stack()
        self.content(self.stack)
        uplink = label(_("Away from home, files travel over your home's internet uplink, so large "
                         "files are slower than on the local network."), "dim", "small", wrap=True)
        if tunnels:
            self.stack.add_named(vbox(
                12, label(_("Access from away"), "big"),
                label(_("This computer is reachable through {vpn}. Paired computers that also have a "
                        "VPN open it from away, with the same bookmarks as at home.")
                      .format(vpn=vpn.describe(tunnels)), wrap=True),
                label(_("Your other computers need the VPN too. While they are at home, they "
                        "exchange their VPN addresses by themselves."), wrap=True),
                uplink), "info")
        else:
            self.stack.add_named(vbox(
                12, label(_("Access from away"), "big"),
                label(_("At home computers talk over the local network. Away from home they need a "
                        "VPN to reach each other over the internet."), wrap=True),
                label(_("HomeMesh can set up Tailscale for you: it installs it, turns it on and opens "
                        "the login page in your browser. Tailscale is free for personal use."),
                      wrap=True),
                label(_("Do the same on each of your computers and log in with the same account. "
                        "While they are at home, they exchange their VPN addresses by themselves."),
                      wrap=True),
                label(_("Already using NetBird, ZeroTier, Yggdrasil, Nebula or WireGuard? HomeMesh "
                        "finds them by itself; there is nothing to set up."), "dim", "small",
                      wrap=True),
                uplink), "info")

        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        spinner.set_size_request(32, 32)
        self.work_title = label("", "big", wrap=True, xalign=0.5)
        self.work_text = label("", wrap=True, xalign=0.5)
        self.again = button(_("Open the page again"), lambda: self.url and self._open(self.url))
        self.again.set_halign(Gtk.Align.CENTER)
        self.stack.add_named(page(spinner, self.work_title, self.work_text, self.again, margin=12),
                             "work")

        self.result_icon = Gtk.Image(halign=Gtk.Align.CENTER)
        self.result_icon.set_pixel_size(48)
        self.result_title = label("", "big", wrap=True, xalign=0.5)
        self.result_text = label("", wrap=True, xalign=0.5)
        self.result_steps = vbox(8)
        self.stack.add_named(page(self.result_icon, self.result_title, self.result_text, self.result_steps,
                                  spacing=10, margin=6), "result")
        self._info_buttons(bool(tunnels))
        self.connect("close-request", self._closing)

    def _info_buttons(self, connected):
        self.clear_buttons()
        if connected:
            self.set_focus(self.add_button(_("Close"), self.close, "suggested-action"))
        else:
            self.add_button(_("Close"), self.close)
            self.set_focus(self.add_button(_("Set up Tailscale"), self.start, "suggested-action"))

    def _closing(self, *_args):
        if self.working:
            self.working = False
            self.setup.cancel()  # the window still picks up whatever got done
        return False

    def start(self):
        self.working = True
        self.url = None
        self._stage("connect", None)
        self.stack.set_visible_child_name("work")
        self.clear_buttons()
        self.add_button(_("Cancel"), self.close)
        self.setup = tailscale.Setup(system.run_helper, lambda stage, value: idle(self._stage, stage, value))
        setup = self.setup

        def work():
            error = setup.run()
            idle(self._done, setup, error)
        threading.Thread(target=work, daemon=True).start()

    def _stage(self, stage, value):
        if not self.working:
            return
        self.again.set_visible(stage == "login")
        if stage == "admin":
            self.work_title.set_text(_("Installing and starting Tailscale…"))
            self.work_text.set_text(_("If a window asks for the administrator password, enter it. "
                                      "Downloading Tailscale can take a minute."))
        elif stage == "connect":
            self.work_title.set_text(_("Connecting to Tailscale…"))
            self.work_text.set_text("")
        elif stage == "login":
            self.url = value
            if tailscale.approval_page(value):
                self.work_title.set_text(_("Approve this computer"))
                self.work_text.set_text(_("Your Tailscale network wants new computers approved. The "
                                          "admin page has opened in your browser."))
            else:
                self.work_title.set_text(_("Log in to Tailscale in your browser"))
                self.work_text.set_text(_("The login page has opened in your browser. Use the same "
                                          "account on all your computers. This window carries on "
                                          "by itself once you have logged in."))
            self._open(value)

    def _open(self, url):
        Gtk.UriLauncher.new(url).launch(self, None, None, None)

    def _done(self, setup, error):
        self.window.away_changed()
        if not self.working or setup is not self.setup:
            return  # closed in the meantime
        self.working = False
        self.stack.set_visible_child_name("result")
        self.clear_buttons()
        child = self.result_steps.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.result_steps.remove(child)
            child = following
        if error is None:
            self.result_icon.set_from_icon_name(OK_ICON)
            self.result_title.set_text(_("Tailscale is on"))
            reach = _("This computer is reachable through {vpn}.").format(vpn=vpn.describe(setup.addresses)) \
                if setup.addresses else _("This computer is connected to Tailscale.")
            self.result_text.set_text(reach + " " + _("Now set up Tailscale the same way on your other "
                                                      "computers, with the same account."))
            self.set_focus(self.add_button(_("Close"), self.close, "suggested-action"))
            return
        self.result_icon.set_from_icon_name("dialog-warning-symbolic")
        self.result_title.set_text(_("Tailscale is not set up"))
        result = setup.helper_result
        steps = result.steps if result and not result.ok else []
        self.result_text.set_text((result.error or "") if steps else error)  # the steps say it all
        self.result_text.set_visible(bool(self.result_text.get_text()))
        for item in steps:
            icon = Gtk.Image.new_from_icon_name(OK_ICON if item.get("ok")
                                                else "dialog-warning-symbolic")
            icon.set_valign(Gtk.Align.START)
            self.result_steps.append(hbox(10, icon, label(system.step_text(item), wrap=True, chars=50)))
        self.add_button(_("Close"), self.close)
        self.add_button(_("Try again"), self.start, "suggested-action")


class BreaksDialog(Dialog):
    """The local network keeps breaking: what HomeMesh does about it, and what helps."""

    def __init__(self, parent, peers, roamed):
        super().__init__(parent, _("Local network"), 520)
        count = max(len(breaks.times(peer)) for peer in peers)
        self.content(
            label(_("The local network keeps breaking"), "big"),
            label(_("In the last three days it broke {n} times between this computer and {names}: both were on "
                    "and answered through {vpn}, but not directly. HomeMesh now connects them through {vpn} "
                    "first. At home that is about as fast, and open folders survive the breaks. After three days "
                    "without a break it goes back to the local network.")
                  .format(n=count, names=", ".join(sorted(peer["name"] for peer in peers)),
                          vpn=peers[0]["vpn"][0]["kind"]), wrap=True))
        if roamed >= MANY_ROAMS:
            self.content(label(_("The Wi-Fi of this computer changed access points {n} times in an hour. It is where "
                                 "several of them are about equally strong, such as the bands of one router.")
                               .format(n=roamed), wrap=True))
        self.content(label(_("What usually helps"), "heading"))
        for text in (_("Update the software (firmware) of the router and of the mesh nodes, then restart them."),
                     _("If the bands of the router (2.4 and 5 GHz) share one name, give each its own and use one of "
                       "them on the computers. They then stop jumping between the bands and still move between mesh "
                       "nodes."),
                     _("Connect a computer that stays in one place with a cable.")):
            self.content(label("• " + text, wrap=True))
        self.add_button(_("More…"), lambda: Gtk.UriLauncher.new(
            # Translators: a page in your language if there is one, such as local-network.pl.md
            _("https://github.com/MikolajQ/homemesh/blob/main/docs/local-network.md")).launch(self, None, None, None))
        self.set_focus(self.add_button(_("Close"), self.close, "suggested-action"))


class ShareDialog(Dialog):
    """The whole home folder, or chosen folders, each read-only or not."""

    def __init__(self, parent, current, callback):
        super().__init__(parent, _("What to share"), 540)
        self.window = parent
        self.callback = callback
        self.folders = [dict(folder) for folder in current["folders"]]
        self.missing = share.sandbox_missing()
        available = not self.missing
        self.home = Gtk.CheckButton(label=_("The whole home folder"))
        self.chosen = Gtk.CheckButton(label=_("Only chosen folders"), group=self.home)
        self.home.set_active(current["mode"] == "home" or not available)
        self.chosen.set_active(current["mode"] == "folders" and available)
        self.chosen.set_sensitive(available)
        home_note = label(_("Paired computers get the same access as you: they see and change "
                            "everything, settings and keys included."), "dim", "small", wrap=True)
        chosen_note = label(_("Paired computers see only these folders. The rest of your home "
                              "folder, your settings and your keys are invisible to them.")
                            + ("" if available else "\n" + _("Needs: {packages}.").format(
                                packages=system.thing_names(self.missing))), "dim", "small", wrap=True)
        for note in (home_note, chosen_note):
            note.set_margin_start(28)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed")
        self.list.set_placeholder(label(_("No folder chosen yet."), "dim", "placeholder"))
        self.list.set_margin_start(28)
        self.pick = button(_("Add folder…"), self._pick)
        self.pick.set_halign(Gtk.Align.START)
        self.pick.set_margin_start(28)
        self.content(label(_("What paired computers see"), "big"),
                     self.home, home_note, self.chosen, chosen_note)
        if available:
            self.content(self.list, self.pick)
        else:
            install = button(_("Install missing packages"), self._install)
            install.set_halign(Gtk.Align.START)
            install.set_margin_start(28)
            self.content(install)
        self.add_button(_("Cancel"), self.close)
        self.save = self.add_button(_("Save"), self._save, "suggested-action")
        # Not the other one: of the pair, GTK reports the one switched off first,
        # while the one switched on still reads as off.
        self.chosen.connect("toggled", lambda *_a: self._update())
        self._rebuild()

    def _rebuild(self):
        clear(self.list)
        for folder in self.folders:
            name = label(Path(folder["path"]).name or folder["path"], "heading", ellipsize=True)
            where = label(folder["path"], "dim", "small")
            where.set_ellipsize(Pango.EllipsizeMode.START)
            texts = vbox(2, name, where)
            texts.set_hexpand(True)
            readonly = Gtk.CheckButton(label=_("Read-only"), active=folder["readonly"])
            readonly.connect("toggled", lambda check, f=folder: f.update(readonly=check.get_active()))
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=_("Remove from the list"))
            remove.add_css_class("flat")
            remove.connect("clicked", lambda *_a, f=folder: (self.folders.remove(f), self._rebuild()))
            row = Gtk.ListBoxRow(activatable=False)
            row.set_child(hbox(12, Gtk.Image.new_from_icon_name("folder-symbolic"), texts, readonly, remove))
            self.list.append(row)
        self._update()

    def _update(self):
        chosen = self.chosen.get_active()
        self.list.set_sensitive(chosen)
        self.pick.set_sensitive(chosen)
        self.save.set_sensitive(not chosen or bool(self.folders))

    def _pick(self):
        dialog = Gtk.FileDialog(title=_("Choose a folder to share"), modal=True)
        dialog.set_initial_folder(Gio.File.new_for_path(str(paths.home())))
        dialog.select_folder(self, None, self._picked)

    def _picked(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        path = folder.get_path() if folder else None
        if not path:
            return  # not a local folder
        path = os.path.realpath(path)
        if all(f["path"] != path for f in self.folders):
            self.folders.append({"path": path, "readonly": False})
        self._rebuild()

    def _install(self):
        self.close()
        self.window.run_helper(["install", *self.missing], _("Installed."), then=self.window.ask_share)

    def _save(self):
        self.close()
        self.callback({"mode": "folders" if self.chosen.get_active() else "home", "folders": self.folders})


class PairDialog(Dialog):
    """Connecting → the code → done or failed."""

    def __init__(self, parent, name, asking, session):
        title = _("Pairing with {name}").format(name=name) if asking \
            else _("{name} wants to pair").format(name=name)
        super().__init__(parent, title, 460)
        self.window = parent
        self.session = session
        self.finished = False
        self.name = name
        self.peer = None
        self.stack = page_stack()
        self.content(self.stack)

        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        spinner.set_size_request(32, 32)
        self.wait_text = label(_("Connecting to {name}…").format(name=name), wrap=True, xalign=0.5)
        self.stack.add_named(page(spinner, self.wait_text, margin=12), "wait")

        self.code_title = label(title, "big", wrap=True, xalign=0.5)
        self.code_label = label("", "code", xalign=0.5)
        self.code_text = label("", wrap=True, xalign=0.5)
        self.status = label("", "dim", wrap=True, xalign=0.5)
        warning = label(_("Paired computers have full access to each other's home folders. Pair "
                          "only your own computers."), "dim", "small", wrap=True, xalign=0.5)
        self.stack.add_named(page(self.code_title, self.code_label, self.code_text, warning, self.status,
                                  spacing=10), "code")

        self.result_icon = Gtk.Image(halign=Gtk.Align.CENTER)
        self.result_icon.set_pixel_size(48)
        self.result_title = label("", "big", wrap=True, xalign=0.5)
        self.result_text = label("", wrap=True, xalign=0.5)
        self.stack.add_named(page(self.result_icon, self.result_title, self.result_text, spacing=10, margin=6),
                             "result")
        self.add_button(_("Cancel"), self.close)
        self.connect("close-request", self._closing)

    def _closing(self, *_args):
        if not self.finished:
            self.session.cancel()
        return False

    def show_code(self, card, code):
        self.peer = card
        self.name = card["name"]
        self.code_label.set_text(code)
        self.code_text.set_text(_("Check that {name} shows the same code. If it does, click "
                                  "“Matches” on both computers.").format(name=self.name))
        self.status.set_text("")
        self.stack.set_visible_child_name("code")
        self.clear_buttons()
        self.add_button(_("Does not match"), lambda: self._decide(False))
        self.yes = self.add_button(_("Matches"), lambda: self._decide(True), "suggested-action")
        self.present()

    def _decide(self, ok):
        self.session.decide(ok)
        if ok:
            self.clear_buttons()
            self.add_button(_("Cancel"), self.close)
            if not self.status.get_text():
                self.status.set_text(_("Waiting for confirmation on {name}…").format(name=self.name))

    def remote_confirmed(self):
        if self.session.conf and self.session.conf.local is None:
            self.status.set_text(_("{name} says the code matches.").format(name=self.name))
        else:
            self.status.set_text("")

    def done(self, card):
        self.finished = True
        self.result_icon.set_from_icon_name(OK_ICON)
        self.result_title.set_text(_("Paired with {name}").format(name=card["name"]))
        self.result_text.set_text(_("The home folder of {name} is now in your file manager's "
                                    "sidebar. The other computers in the group will learn about it "
                                    "by themselves.")
                                  .format(name=card["name"]))
        self.stack.set_visible_child_name("result")
        self.clear_buttons()
        self.add_button(_("Close"), self.close)
        self.add_button(_("Open"), lambda: (self.close(), self.window.open_peer(card["id"])),
                        "suggested-action")
        self.present()

    def failed(self, message):
        self.finished = True
        self.result_icon.set_from_icon_name("dialog-warning-symbolic")
        self.result_title.set_text(_("Not paired"))
        self.result_text.set_text(message)
        self.stack.set_visible_child_name("result")
        self.clear_buttons()
        self.add_button(_("Close"), self.close, "suggested-action")
        self.present()


class SessionUI:
    """Passes a pairing session's reports from its thread to the window."""

    def __init__(self, window):
        self.window = window
        self.session = None
        self.dialog = None

    def _dialog(self, name):
        if self.dialog is None:
            self.dialog = PairDialog(self.window, name, asking=False, session=self.session)
        return self.dialog

    def code(self, card, code):
        idle(lambda: self._dialog(card["name"]).show_code(card, code))

    def remote_confirmed(self):
        idle(lambda: self.dialog and self.dialog.remote_confirmed())

    def paired(self, card):
        idle(lambda: self._dialog(card["name"]).done(card))

    def failed(self, message):
        def show():
            if self.dialog:
                self.dialog.failed(message)
            else:
                self.window.toast(message)
        idle(show)


class PeerRow(Gtk.ListBoxRow):
    def __init__(self, window, pid, peer, alias, via):
        super().__init__(activatable=False)
        self.window, self.pid, self.peer, self.alias, self.via = window, pid, peer, alias, via
        self.dot = label("●", xalign=0.5)
        self.title = label(peer["name"], "heading", ellipsize=True)
        self.subtitle = label("", "dim", "small", ellipsize=True)
        texts = vbox(2, self.title, self.subtitle)
        texts.set_hexpand(True)
        open_button = button(_("Open"), lambda: window.open_peer(pid))

        menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=_("More"))
        menu.add_css_class("flat")
        popover = Gtk.Popover()
        automount = Gtk.CheckButton(label=_("Connect at login"), active=peer.get("automount", False))
        automount.connect("toggled", lambda check: window.set_automount(pid, check.get_active()))
        remove = Gtk.Button(label=_("Remove from the group…"))
        remove.add_css_class("flat")
        remove.connect("clicked", lambda *_a: (popover.popdown(), window.ask_remove(pid)))
        popover.set_child(vbox(4, automount, remove, margin=6))
        menu.set_popover(popover)
        self.set_child(hbox(12, self.dot, texts, open_button, menu))
        self.set_online("checking")

    def set_online(self, how):
        """how: "checking", "lan" (or True), "vpn", or None when out of reach."""
        for css in ("dot-on", "dot-off"):
            self.dot.remove_css_class(css)
        self.dot.add_css_class("dot-on" if how in ("lan", "vpn", True) else "dot-off")
        if how == "checking":
            status = _("checking…")
        elif how and breaks.breaking(self.peer):
            status = _("through VPN, as the local network keeps breaking")
        elif how in ("lan", True):
            status = _("reachable on the local network")
        elif how == "vpn":
            status = _("reachable through VPN")
        else:
            status = _("not reachable")
        self.subtitle.set_text(f"{self.peer['user']}@{self.alias} · {status} · {self.via}")


class NearbyRow(Gtk.ListBoxRow):
    def __init__(self, window, entry, can_pair):
        super().__init__(activatable=False)
        title = label(entry["name"], "heading", ellipsize=True)
        note = _("ready to pair") if entry["ready"] else _("sharing is off")
        subtitle = label(f"{entry['user']} · {entry['ip']} · {note}", "dim", "small", ellipsize=True)
        texts = vbox(2, title, subtitle)
        texts.set_hexpand(True)
        icon = Gtk.Image.new_from_icon_name("computer-symbolic")
        connect = button(_("Connect"), lambda: window.start_pairing(entry), "suggested-action")
        connect.set_sensitive(entry["ready"] and can_pair)
        self.set_child(hbox(12, icon, texts, connect))


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, demo=False):
        super().__init__(application=app, title=APP_NAME, default_width=580, default_height=700)
        self.demo = demo
        self.st = state.new_state()
        self.configured = self.running = False
        self.mdns_ok, self.mdns_how = True, None
        self.missing = []
        self.gvfs = system.gvfs_sftp()
        self.gvfs_installed = False  # by us, but the session does not know it yet
        self.online = {}
        self.hints = {}
        self.nearby = []
        self.peer_rows = {}
        self.session = None
        self.pair_lock = threading.Lock()
        self.syncing = False
        self.discovery = self.server = None
        self.helper_busy = self.installing = False
        self.tunnels = []

        header = Gtk.HeaderBar()
        menu = Gio.Menu()
        menu.append(_("Sync the group now"), "app.sync")
        menu.append(_("About"), "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text=_("Menu")))
        self.set_titlebar(header)

        self.hint = HintBar()

        self.this_name = label("", "big", ellipsize=True)
        self.this_text = label("", "dim", "small", wrap=True)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text=_("Sharing"))
        self.switch.connect("state-set", self._switch_changed)
        self._switch_guard = False
        computer = Gtk.Image.new_from_icon_name("computer-symbolic")
        computer.set_pixel_size(32)
        texts = vbox(2, self.this_name, self.this_text)
        texts.set_hexpand(True)
        this_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        this_list.add_css_class("boxed")
        row = Gtk.ListBoxRow(activatable=False)
        row.set_child(hbox(12, computer, texts, self.switch))
        this_list.append(row)

        self.share_text = label("", "dim", "small", wrap=True)
        share_texts = vbox(2, label(_("What other computers see"), "heading"), self.share_text)
        share_texts.set_hexpand(True)
        folder_icon = Gtk.Image.new_from_icon_name("folder-remote-symbolic")
        folder_icon.set_pixel_size(24)
        row = Gtk.ListBoxRow(activatable=False)
        row.set_child(hbox(12, folder_icon, share_texts, button(_("Change…"), self.ask_share)))
        this_list.append(row)

        self.away_text = label("", "dim", "small", wrap=True)
        away_texts = vbox(2, label(_("Away from home"), "heading"), self.away_text)
        away_texts.set_hexpand(True)
        away_icon = Gtk.Image.new_from_icon_name("network-vpn-symbolic")
        away_icon.set_pixel_size(24)
        row = Gtk.ListBoxRow(activatable=False)
        self.away_button = button(_("Set up…"), self.show_away)
        row.set_child(hbox(12, away_icon, away_texts, self.away_button))
        this_list.append(row)

        self.login_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.login_switch.connect("state-set", self._login_changed)
        login_texts = vbox(2, label(_("At login"), "heading"),
                           label(_("Sync the group, connect the computers marked “Connect at login” "
                                   "and keep the ones that are off marked “(off)” in the file "
                                   "manager. Sharing works without this too."),
                                 "dim", "small", wrap=True))
        login_texts.set_hexpand(True)
        login_icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        login_icon.set_pixel_size(24)
        row = Gtk.ListBoxRow(activatable=False)
        row.set_child(hbox(12, login_icon, login_texts, self.login_switch))
        this_list.append(row)

        self.peer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.peer_list.add_css_class("boxed")
        self.peer_list.set_placeholder(label(_("None yet. Pair with a computer from the list below."),
                                             "dim", "placeholder", wrap=True))
        self.nearby_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.nearby_list.add_css_class("boxed")
        spinner = Gtk.Spinner(spinning=not demo)
        searching = hbox(10, spinner, label(_("Looking for computers with HomeMesh open on this "
                                              "network…"),
                                            "dim", wrap=True))
        searching.add_css_class("placeholder")
        self.nearby_list.set_placeholder(searching)

        content = vbox(18, self.hint,
                       vbox(8, label(_("This computer"), "heading"), this_list),
                       vbox(8, label(_("Paired computers"), "heading"), self.peer_list),
                       vbox(8, label(_("Nearby"), "heading"), self.nearby_list),
                       margin=18)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scrolled.set_child(content)
        overlay = Gtk.Overlay()
        overlay.set_child(scrolled)
        self.toaster = Toast(overlay)
        self.set_child(overlay)
        self.content = content

        if demo:
            self._load_demo()
            return
        with state.transaction() as st:
            state.own_card(st)  # creates the id and the login key on first start
        self.refresh_state()
        watch.start(self.st)
        self._start_network()
        self.check_system()
        self.check_online()
        GLib.timeout_add_seconds(15, self._tick_online)
        GLib.timeout_add_seconds(30, self._tick_system)
        GLib.timeout_add_seconds(120, self._tick_sync)
        GLib.timeout_add_seconds(4, lambda: (self.sync_now(quiet=True), False)[1])
        self.monitor = Gio.File.new_for_path(str(paths.state_file())).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", lambda *_a: self._state_changed_soon())
        self._state_timer = None
        self.connect("close-request", self._closing)

    # ------------------------------------------------------------ network

    def _start_network(self):
        try:
            self.server = proto.PairServer(self.on_pair_request)
        except OSError as error:
            self.toast(_("Cannot accept pairing requests: {error}").format(error=error))
        try:
            self.discovery = Discovery(self._discovery_info, on_change=lambda: idle(self.refresh_nearby))
        except OSError as error:
            self.toast(_("Cannot look for computers on the network: {error}").format(error=error))

    def _discovery_info(self):
        return {"id": self.st["id"], "name": hostname(), "user": username(),
                "port": self.server.port if self.server else 0, "ready": self.is_ready}

    @property
    def is_ready(self):
        return self.configured and self.running

    def _closing(self, *_args):
        if self.discovery:
            self.discovery.close()
        if self.server:
            self.server.close()
        return False

    # -------------------------------------------------------------- state

    def _state_changed_soon(self):
        if self._state_timer:
            GLib.source_remove(self._state_timer)
        self._state_timer = GLib.timeout_add(300, self._state_changed)

    def _state_changed(self):
        self._state_timer = None
        self.refresh_state()
        return False

    def refresh_state(self):
        self.st = state.load()
        self.rebuild_peers()
        self.refresh_nearby()
        self.update_this()

    def rebuild_peers(self):
        clear(self.peer_list)
        self.peer_rows = {}
        names = render.aliases(self.st)
        for pid, peer in sorted(self.st["peers"].items(), key=lambda kv: kv[1]["name"]):
            row = PeerRow(self, pid, peer, names[pid], self._via_text(peer))
            row.set_online(self.online.get(pid, "checking"))
            self.peer_list.append(row)
            self.peer_rows[pid] = row
        self.update_hint()

    def _via_text(self, peer):
        via = peer.get("via")
        if via == "direct":
            return _("paired directly")
        introducer = self.st["peers"].get(via)
        return _("introduced by {name}").format(name=introducer["name"]) if introducer \
            else _("introduced by another computer")

    def refresh_nearby(self):
        if self.discovery:
            self.nearby = self.discovery.nearby()
            for entry in self.nearby:
                if entry["id"] in self.peer_rows and self.online.get(entry["id"]) != "lan":
                    self.online[entry["id"]] = "lan"
                    self.peer_rows[entry["id"]].set_online("lan")
        clear(self.nearby_list)
        for entry in self.nearby:
            if entry["id"] not in self.st["peers"]:
                self.nearby_list.append(NearbyRow(self, entry, self.is_ready))
        self.update_hint()

    def update_this(self):
        name = hostname()
        self.this_name.set_text(name)
        if not self.configured:
            text = _("Sharing is off. Other computers have no access here.")
        elif not self.running:
            text = _("Sharing is on, but the SSH server is not running.")
        else:
            text = _("Sharing is on. Other computers see it as {name}.local.").format(name=name)
        self.this_text.set_text(text)
        self.share_text.set_text(share.summary(share.current(self.st)))
        tunnels = self.tunnels
        if tunnels:
            self.away_text.set_text(_("Reachable through {vpn}. Paired computers that also have a "
                                      "VPN can open this computer from away too.").format(vpn=vpn.describe(tunnels)))
            self.away_button.set_label(_("How does it work?"))
        else:
            self.away_text.set_text(_("Only on the local network. HomeMesh can set up Tailscale, so "
                                      "that your computers reach each other from anywhere."))
            self.away_button.set_label(_("Set up…"))
        self._switch_guard = True
        self.switch.set_active(self.configured)
        self.switch.set_state(self.configured)
        self.switch.set_sensitive(not self.helper_busy)
        login = self.st.get("login", True)
        self.login_switch.set_active(login)
        self.login_switch.set_state(login)
        self._switch_guard = False

    def update_hint(self):
        name = hostname()
        waiting = [e for e in self.nearby if e["id"] not in self.st["peers"]]
        twins = [e for e in self.nearby if e["name"] == name] + \
                [p for p in self.st["peers"].values() if p["name"] == name]
        peers = self.st["peers"]
        hint = self.hint.set
        if self.helper_busy and self.installing:
            hint("step", "content-loading-symbolic", _("Installing packages…"),
                 _("If a window asks for the administrator password, enter it. Installing can take a "
                   "minute."))
        elif self.helper_busy:
            hint("step", "content-loading-symbolic", _("One moment…"),
                 _("Changing settings. If a window asks for the administrator password, enter it."))
        elif self.missing:
            hint("problem", "dialog-warning-symbolic", _("Some needed packages are missing"),
                 _("Missing: {packages}. HomeMesh can install them and turn sharing on. You will be asked "
                   "for the administrator password.").format(packages=system.thing_names(self.missing)),
                 _("Install missing packages"), self.install_and_enable)
        elif not self.configured:
            hint("step", "go-next-symbolic", _("Step 1 of 3: turn sharing on"),
                 _("This lets your other computers open this computer's home folder. You will be "
                   "asked for the administrator password."), _("Turn on"), self.enable)
        elif not self.running:
            hint("problem", "dialog-warning-symbolic", _("The SSH server is not running"),
                 _("Without it, other computers cannot open this computer."), _("Fix"), self.enable)
        elif not self.mdns_ok:
            hint("problem", "dialog-warning-symbolic", _("Other computers cannot find this one by "
                                                         "name"),
                 _("mDNS, which answers to {name}.local, is missing or turned off.").format(name=name),
                 _("Fix"), self.enable)
        elif twins:
            hint("problem", "dialog-warning-symbolic", _("Two computers are called “{name}”").format(name=name),
                 _("Computers on a network need different names. Give this one another name."),
                 _("Rename"), self.ask_rename)
        elif not peers and any(e["ready"] for e in waiting):
            hint("step", "go-next-symbolic", _("Step 3 of 3: pair the computers"),
                 _("Click “Connect” next to a computer below and compare the codes on both screens."))
        elif not peers and waiting:
            hint("step", "go-next-symbolic", _("Step 2 of 3: enable sharing on the other computer"),
                 _("On {name}, click “Turn on” in HomeMesh.").format(name=waiting[0]["name"]))
        elif not peers:
            hint("step", "go-next-symbolic", _("Step 2 of 3: open HomeMesh on the other computer"),
                 _("Install and start HomeMesh on another computer on the same network. It will "
                   "appear below, under “Nearby”."))
        elif not self.gvfs and not system.kde() and self.gvfs_installed:
            hint("step", "go-next-symbolic", _("Log out and back in"),
                 _("After that, your file manager can open the paired computers."))
        elif not self.gvfs and not system.kde():
            hint("problem", "dialog-warning-symbolic", _("Your file manager cannot open the paired "
                                                         "computers"),
                 _("Missing: {packages}. HomeMesh can install it. You will be asked for the administrator "
                   "password.").format(packages="gvfs"),
                 _("Install missing packages"), self.install_gvfs)
        elif any(e["ready"] for e in waiting):
            hint("step", "list-add-symbolic", _("Another computer is nearby"),
                 _("Click “Connect” to add {name} to the group.").format(name=waiting[0]["name"]))
        elif self.breaking():
            broken = self.breaking()
            hint("step", "network-wireless-symbolic", _("The local network keeps breaking"),
                 _("So connections to {names} go through {vpn} first, which survives it.")
                 .format(names=", ".join(peer["name"] for peer in broken), vpn=broken[0]["vpn"][0]["kind"]),
                 _("What helps…"), self.show_breaks)
        else:
            hint("done", OK_ICON, _("All set"),
                 _("Paired computers are in your file manager's sidebar. A new computer only needs "
                   "pairing with one of them: the others learn about it by themselves.")
                 + (" " + _("Computers that are off are marked “(off)” there.")
                    if self.st.get("login", True) else ""))

    def breaking(self):
        """The computers to which the local network keeps breaking."""
        return sorted(filter(breaks.breaking, self.st["peers"].values()), key=lambda peer: peer["name"])

    def show_breaks(self):
        if self.breaking():
            BreaksDialog(self, self.breaking(), self.st.get("roamed", 0)).present()

    # ------------------------------------------------------------- checks

    def check_system(self):
        def work():
            mdns = system.mdns()
            info = (system.sharing_configured(), system.sshd_running(), mdns, vpn.addresses(),
                    system.missing(mdns[0]))
            idle(self._system_checked, *info)
        threading.Thread(target=work, daemon=True).start()

    def _system_checked(self, configured, running, mdns, tunnels, missing):
        self.configured, self.running, self.tunnels = configured, running, tunnels
        self.missing = missing
        self.mdns_ok, self.mdns_how = mdns
        self.update_this()
        self.refresh_nearby()

    def check_online(self):
        peers, me, hints = dict(self.st["peers"]), self.st["id"], dict(self.hints)

        def work():
            result = system.reachable(peers, me, hints)
            idle(self._online_checked, result)
        threading.Thread(target=work, daemon=True).start()

    def _online_checked(self, result):
        seen = {e["id"] for e in self.nearby}
        for pid, reach in result.items():
            self.hints[pid] = reach.address
            self.online[pid] = reach.how or ("lan" if pid in seen else None)
            if pid in self.peer_rows:
                self.peer_rows[pid].set_online(self.online[pid])

    def _tick_online(self):
        self.check_online()
        return True

    def _tick_system(self):
        self.check_system()
        return True

    def _tick_sync(self):
        self.sync_now(quiet=True)
        return True

    def sync_now(self, quiet=False):
        if self.syncing or not self.st["peers"]:
            if not quiet:
                self.toast(_("No paired computers.") if not self.st["peers"] else _("Already syncing…"))
            return
        self.syncing = True

        def work():
            report = sync.sync_all()
            idle(self._synced, report, quiet)
        threading.Thread(target=work, daemon=True).start()

    def _synced(self, report, quiet):
        self.syncing = False
        self.refresh_state()
        if not quiet:
            reached = sum(1 for error in report.values() if error is None)
            self.toast(_("Synced with {n} of {total} computers.").format(n=reached, total=len(report)))

    def toast(self, text):
        self.toaster.show(text)

    # ------------------------------------------------------------ actions

    def _switch_changed(self, _switch, wanted):
        if self._switch_guard:
            return False
        if wanted:
            self.enable()
        else:
            confirm(self, _("Turn sharing off?"),
                    _("Paired computers lose access to this computer. The group stays: turn "
                      "sharing back on and everything works as before."),
                    _("Turn off"), lambda: self.run_helper(["disable"], _("Sharing is off.")))
        return True  # the switch follows the real state once the helper is done

    def enable(self):
        self.run_helper(["enable"], _("Sharing is on."))

    def install_and_enable(self):
        self.run_helper(["enable", "--install"], _("Sharing is on."))

    def install_gvfs(self):
        def then():
            self.gvfs_installed = True
        self.run_helper(["install", "gvfs"], _("Installed."), then)

    def _login_changed(self, switch, wanted):
        if self._switch_guard:
            return False
        with state.transaction() as st:
            st["login"] = wanted
            render.apply(st)
        self.st["login"] = wanted
        watch.start(self.st) if wanted else watch.stop()
        switch.set_state(wanted)
        return True

    def show_away(self):
        AwayDialog(self, self.tunnels).present()

    def away_changed(self):
        """After setting Tailscale up: show its address, and tell the group."""
        self.check_system()
        self.sync_now(quiet=True)

    def ask_share(self):
        ShareDialog(self, share.current(self.st), self.save_share).present()

    def save_share(self, new):
        with state.transaction() as st:
            st["share"] = new
            render.apply(st)
        self.refresh_state()
        stopped = share.end_connections()
        if stopped:
            self.toast(_("Saved. Other computers have to open this computer again."))
        else:
            self.toast(_("Saved."))

    def run_helper(self, args, done_text, then=None):
        if self.helper_busy:
            return
        self.helper_busy = True
        self.installing = args[0] == "install" or "--install" in args
        self.update_this()
        self.update_hint()

        def work():
            result = system.run_helper(args)
            idle(self._helper_done, result, done_text, then)
        threading.Thread(target=work, daemon=True).start()

    def _helper_done(self, result, done_text, then):
        self.helper_busy = False
        if result.ok:
            self.toast(done_text)
            if then:
                then()
        elif result.steps:
            StepsDialog(self, result, _("Not everything worked"),
                        self.install_and_enable if result.missing else None).present()
        else:
            self.toast(result.error or _("That did not work."))
        self.check_system()
        self.update_this()
        self.update_hint()
        if result.ok and self.st["peers"]:
            self.sync_now(quiet=True)

    def ask_rename(self):
        suggestion = hostname()
        RenameDialog(self, suggestion, lambda name: self.run_helper(
            ["set-hostname", name], _("New name: {name}.local").format(name=name))).present()

    def start_pairing(self, entry):
        with self.pair_lock:
            if self.session:
                self.toast(_("Already pairing with another computer."))
                return
            ui = SessionUI(self)
            session = Session(ui)
            ui.session = session
            self.session = session
        ui.dialog = PairDialog(self, entry["name"], asking=True, session=session)
        ui.dialog.present()

        def work():
            ok = False
            try:
                ok = session.ask(entry["ip"], entry["port"])
            finally:
                self._session_ended(ok)
        threading.Thread(target=work, daemon=True).start()

    def on_pair_request(self, chan, _address):
        """Runs in the pairing server's thread."""
        try:
            hello = proto.read_hello(chan)
        except PairingError:
            return  # not a pairing request (a port check, a scanner)
        with self.pair_lock:
            if self.session or not self.is_ready:
                proto.refuse(chan, "busy" if self.session else "not-ready")
                return
            ui = SessionUI(self)
            session = Session(ui)
            ui.session = session
            self.session = session
        ok = False
        try:
            ok = session.answer(chan, hello)
        finally:
            self._session_ended(ok)

    def _session_ended(self, ok):
        with self.pair_lock:
            self.session = None
        if ok:
            idle(self.refresh_state)
            sync.sync_all()
            idle(self.refresh_state)
            idle(self.check_online)
            idle(lambda: watch.start(self.st))

    def open_peer(self, pid):
        peer = self.st["peers"].get(pid)
        if not peer:
            return
        uri = render.peer_uri(peer, render.aliases(self.st)[pid])
        self.toast(_("Opening {name}…").format(name=peer["name"]))
        if not self.gvfs:
            self._show_folder(uri)  # Dolphin and other KIO file managers mount by themselves
            return
        # Mounting first gives a clear message here when the computer is off.
        Gio.File.new_for_uri(uri).mount_enclosing_volume(
            Gio.MountMountFlags.NONE, Gtk.MountOperation.new(self), None, self._mounted, (uri, peer))

    def _mounted(self, gfile, result, data):
        uri, peer = data
        try:
            gfile.mount_enclosing_volume_finish(result)
        except GLib.Error as error:
            if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                self.toast(_("Could not open {name}: {error}").format(name=peer["name"],
                                                                               error=error.message))
                return
        self._show_folder(uri)

    def _show_folder(self, uri):
        """Ask the file manager itself to show the folder.

        Not Gtk.FileLauncher: that goes through the desktop portal, which looks
        for an application for the sftp: scheme, finds none and offers to
        install one. org.freedesktop.FileManager1 is implemented by Nautilus,
        Dolphin, Nemo, Thunar and Caja.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._open_with_default(uri)
            return
        bus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1", "ShowFolders",
                 GLib.Variant("(ass)", ([uri], self._activation_token())), None,
                 Gio.DBusCallFlags.NONE, 10000, None, self._shown, uri)

    def _shown(self, bus, result, uri):
        try:
            bus.call_finish(result)
        except GLib.Error:
            self._open_with_default(uri)

    def _activation_token(self):
        """Lets the file manager's window come to the front instead of a 'ready' notice."""
        app = Gio.AppInfo.get_default_for_type("inode/directory", False)
        if app is None:
            return ""
        try:
            return self.get_display().get_app_launch_context().get_startup_notify_id(app, []) or ""
        except Exception:
            return ""

    def _open_with_default(self, uri):
        """No FileManager1 service: give the address to the default program for folders."""
        app = Gio.AppInfo.get_default_for_type("inode/directory", False)
        try:
            if not app or not app.launch_uris([uri], self.get_display().get_app_launch_context()):
                raise GLib.Error("no file manager")
        except GLib.Error:
            self.toast(_("No file manager could open {uri}.").format(uri=uri))

    def set_automount(self, pid, on):
        with state.transaction() as st:
            if pid in st["peers"]:
                st["peers"][pid]["automount"] = on
        name = self.st["peers"].get(pid, {}).get("name", "?")
        text = _("{name} will be connected at login.") if on \
            else _("{name} will not be connected at login.")
        self.toast(text.format(name=name))

    def ask_remove(self, pid):
        peer = self.st["peers"].get(pid)
        if not peer:
            return

        def remove():
            with state.transaction() as st:
                state.remove_peer(st, pid)
                render.apply(st)
            self.refresh_state()
            self.toast(_("Removed {name} from the group.").format(name=peer["name"]))
            self.sync_now(quiet=True)
        confirm(self, _("Remove {name} from the group?").format(name=peer["name"]),
                _("{name} loses access to this computer, and the other computers in the group "
                  "learn about it the next time they sync.").format(name=peer["name"]),
                _("Remove"), remove)

    # --------------------------------------------------------------- demo

    def _load_demo(self):
        """Made-up computers for screenshots."""
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI" + "A" * 43
        now = time.time()
        peers = {}
        user, workshop, old = demo_names()[:3]
        for number, (name, via, online) in enumerate((("laptop", "direct", True),
                                                      (workshop, "1" * 32, False))):
            pid = str(number + 1) * 32
            peers[pid] = {"id": pid, "name": name, "user": user, "home": f"/home/{user}", "userkey": key,
                          "hostkey": key, "port": 22, "updated": now, "via": via, "added": now}
            self.online[pid] = "lan" if online else None
        self.st = state.new_state()
        self.st["peers"] = peers
        if self.demo == "breaks":
            for peer in peers.values():
                peer["vpn"] = [{"addr": "100.101.102.104", "kind": "Tailscale"}]
            peers["1" * 32]["breaks"] = [now - 3600 * hours for hours in (9, 8, 7, 5, 2)]
            self.st["roamed"] = 34
        self.configured = self.running = True
        self.tunnels = [{"addr": "100.101.102.103", "kind": "Tailscale"}]
        self.nearby = [{"id": "9" * 32, "name": old, "user": user, "port": 41753,
                        "ready": self.demo != "breaks", "ip": "192.168.1.23", "seen": 0}]
        self.rebuild_peers()
        clear(self.nearby_list)
        for entry in self.nearby:
            self.nearby_list.append(NearbyRow(self, entry, True))
        self.update_this()
        self.update_hint()


def demo_names():
    """Made-up user, computers and folders for screenshots, in the interface's language."""
    if _("Sharing") == "Udostępnianie":
        return "ania", "pracownia", "stary-pc", "Publiczny", "Muzyka"
    return "anna", "workshop", "old-pc", "Public", "Music"


def save_screenshot(widget, path):
    width, height = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    color = widget.get_color()
    background = Gdk.RGBA()
    background.parse("#242424" if (color.red + color.green + color.blue) / 3 > 0.5 else "#fafafa")
    from gi.repository import Graphene
    snapshot.append_color(background, Graphene.Rect().init(0, 0, width, height))
    paintable.snapshot(snapshot, width, height)
    texture = widget.get_native().get_renderer().render_texture(snapshot.to_node(), None)
    texture.save_to_png(path)


class App(Gtk.Application):
    def __init__(self, options):
        flags = Gio.ApplicationFlags.NON_UNIQUE if options.get("screenshot") else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id=APP_ID, flags=flags)
        self.options = options
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if self.options.get("dark"):
            settings = Gtk.Settings.get_default()
            try:
                settings.set_property("gtk-interface-color-scheme", Gtk.InterfaceColorScheme.DARK)
            except (AttributeError, TypeError):
                settings.set_property("gtk-application-prefer-dark-theme", True)
        for name, callback in (("sync", lambda *_a: self.window and self.window.sync_now()),
                               ("about", lambda *_a: self.about()),
                               ("quit", lambda *_a: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(self, demo=self.options.get("demo", False))
        self.window.present()
        shot = self.options.get("screenshot")
        if shot:
            GLib.timeout_add(1200, self._shoot, shot)

    def _shoot(self, path):
        target = self.window.content
        if self.options.get("breaks-help"):
            dialog = BreaksDialog(self.window, self.window.breaking(), self.window.st["roamed"])
            dialog.present()
            GLib.timeout_add(800, lambda: (save_screenshot(dialog.get_child(), path), self.quit(), False)[2])
            return False
        if self.options.get("vpn-help"):
            dialog = AwayDialog(self.window, [])
            dialog.present()
            GLib.timeout_add(800, lambda: (save_screenshot(dialog.get_child(), path), self.quit(), False)[2])
            return False
        if self.options.get("share"):
            user, _workshop, _old, public, music = demo_names()
            dialog = ShareDialog(self.window, {"mode": "folders", "folders": [
                {"path": f"/home/{user}/{public}", "readonly": False},
                {"path": f"/home/{user}/{music}", "readonly": True}]}, lambda new: None)
            dialog.present()
            GLib.timeout_add(800, lambda: (save_screenshot(dialog.get_child(), path), self.quit(), False)[2])
            return False
        if self.options.get("pair"):
            session = Session(None)
            dialog = PairDialog(self.window, "laptop", asking=True, session=session)
            dialog.present()
            dialog.show_code({"name": "laptop", "id": "1" * 32}, "482 913")
            GLib.timeout_add(800, lambda: (save_screenshot(dialog.get_child(), path), self.quit(), False)[2])
            return False
        save_screenshot(target, path)
        self.quit()
        return False

    def about(self):
        Gtk.AboutDialog(transient_for=self.window, modal=True, program_name=APP_NAME,
                        logo_icon_name=APP_ID, version=__version__,
                        comments=_("The home folders of your computers in your file manager."),
                        license_type=Gtk.License.MIT_X11,
                        website="https://github.com/MikolajQ/homemesh").present()


def run(argv):
    options = {"demo": "--demo" in argv, "dark": "--dark" in argv, "pair": "--pair" in argv,
               "share": "--share" in argv, "vpn-help": "--vpn-help" in argv, "breaks-help": "--breaks-help" in argv}
    if "--breaks" in argv or options["breaks-help"]:
        options["demo"] = options["demo"] and "breaks"
    if "--screenshot" in argv:
        options["screenshot"] = argv[argv.index("--screenshot") + 1]
    return App(options).run([sys.argv[0]])
