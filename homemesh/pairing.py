"""One pairing from start to finish, for the window and the terminal alike.

Runs in a worker thread and reports to `ui`, an object with the methods
code(card, code), remote_confirmed(), paired(card) and failed(message).
"""

from . import proto, render, state
from .i18n import _
from .proto import PairingError


class Session:
    def __init__(self, ui):
        self.ui = ui
        self.chan = None
        self.conf = None
        self.cancelled = False

    def ask(self, host, port):
        """We start: connect to the other computer."""
        try:
            self.chan = proto.connect(host, port)
        except PairingError as error:
            self.ui.failed(str(error))
            return False
        return self._run(None)

    def answer(self, chan, hello):
        """The other computer started; `hello` is its first message."""
        self.chan = chan
        return self._run(hello)

    def _run(self, hello):
        try:
            with state.transaction() as st:
                offer = {"card": state.own_card(st, bump=True), "members": state.member_cards(st)}
            if offer["card"]["hostkey"] is None:
                raise PairingError(_("Najpierw włącz udostępnianie na tym komputerze."))
            if hello is None:
                theirs, code = proto.initiate(self.chan, offer)
            else:
                theirs, code = proto.respond(self.chan, hello, offer)
            try:
                card = state.validate_card(theirs["card"])
            except ValueError:
                raise PairingError(_("Drugi komputer przysłał nieprawidłowe dane.")) from None
            if card["id"] == offer["card"]["id"]:
                raise PairingError(_("To ten sam komputer."))
            self.conf = proto.Confirmation(self.chan)
            if self.cancelled:
                self.conf.decide(False)
            self.ui.code(card, code)
            if not self.conf.wait_remote():
                raise PairingError(_("Na komputerze {name} kliknięto „Nie zgadza się”. "
                                     "Jeśli kody były różne, ktoś mógł podszyć się pod któryś komputer.")
                                   .format(name=card["name"]))
            self.ui.remote_confirmed()
            if not self.conf.wait_local():
                raise PairingError(_("Anulowano."))
            with state.transaction() as st:
                state.pairing_apply(st, theirs["card"], theirs.get("members", []))
                render.apply(st)
            self.ui.paired(card)
            return True
        except PairingError as error:
            self.ui.failed(str(error))
            return False
        finally:
            if self.chan:
                self.chan.close()

    def decide(self, ok):
        if self.conf:
            self.conf.decide(ok)

    def cancel(self):
        self.cancelled = True
        if self.conf:
            self.conf.decide(False)
        elif self.chan:
            self.chan.close()
