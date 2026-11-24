"""Local networks that keep breaking: noticing it, remembering it, going through the VPN."""

import os
import socket
import struct
import subprocess
import threading
import time
import unittest

from support import HomeTestCase, card
from test_watch import LAN, WatcherCase

from homemesh import breaks, paths, presence, render, state, system, watch, wifi

VPN = system.Reach("vpn", "100.64.0.7", True)
PEER = {**card("b", "beta", 10), "vpn": [{"addr": "100.64.0.7", "kind": "Tailscale"}]}


class MemoryTest(unittest.TestCase):
    def test_two_breaks_in_three_days(self):
        peer = dict(PEER, breaks=[1000])
        self.assertFalse(breaks.breaking(peer, 2000))
        peer["breaks"].append(2000)
        self.assertTrue(breaks.breaking(peer, 2000))
        self.assertTrue(breaks.breaking(peer, 1000 + breaks.KEEP - 1))
        self.assertFalse(breaks.breaking(peer, 1000 + breaks.KEEP))  # one is left
        self.assertFalse(breaks.breaking({**peer, "vpn": []}, 2000))  # no other way
        self.assertFalse(breaks.breaking(PEER, 2000))

    def test_a_break_seen_by_both_counts_once(self):
        peer = dict(PEER, breaks=[1000, 5000])
        self.assertIsNone(breaks.merged(peer, (60, 4100), 5100))
        self.assertIsNone(breaks.merged(peer, (), 5100))
        self.assertEqual(breaks.merged(peer, (2000,), 5100), [1000, 3100, 5000])
        self.assertEqual(breaks.merged(PEER, (0,), 5100), [5100])

    def test_telling_each_other_does_not_keep_a_break_alive(self):
        one, other = dict(PEER, breaks=[1000, 5000]), dict(PEER)
        for now in range(5000, 5000 + 2 * breaks.KEEP, 3600):
            for a, b in ((one, other), (other, one)):
                b["breaks"] = breaks.merged(b, breaks.sound(breaks.ages(a, now)), now) or b.get("breaks", [])
        self.assertEqual(breaks.times(one, now), [])
        self.assertEqual(breaks.times(other, now), [])
        self.assertLess(max(other["breaks"]), 5002)

    def test_only_so_many_are_remembered(self):
        peer = dict(PEER, breaks=[1000 * n for n in range(1, breaks.MOST + 1)])
        kept = breaks.merged(peer, (0,), 1000 * (breaks.MOST + 1))
        self.assertEqual((len(kept), kept[0]), (breaks.MOST, 2000))
        self.assertEqual(len(breaks.ages(dict(peer, breaks=kept), kept[-1])), breaks.ENOUGH)

    def test_odd_values_are_ignored(self):
        for odd in (None, "x", 5, {}, [True], ["1"], [1.5], [-1], [breaks.KEEP], [1, 2, 3], [None]):
            self.assertEqual(breaks.sound(odd), (), odd)
        self.assertEqual(breaks.sound([0, 5]), (0, 5))
        for odd in (None, "x", {}, ["1", None]):
            self.assertEqual(breaks.times(dict(PEER, breaks=odd), 5), [], odd)
        self.assertEqual(breaks.ages(dict(PEER, breaks=[500]), 400), [0])  # the clock was set back


class SeenTest(unittest.TestCase):
    """What counts as a break."""

    def setUp(self):
        self.seen = watch.Seen(1000)
        self.assertFalse(self.seen.checked(LAN, 1000))

    def test_locally_through_the_vpn_and_locally_again(self):
        self.assertFalse(self.seen.checked(system.NOWHERE, 1060))  # the VPN needs a moment, too
        self.assertFalse(self.seen.checked(VPN, 1068))
        self.assertEqual(self.seen.next(), 1068 + watch.BACK[0])
        self.assertFalse(self.seen.checked(VPN, 1078))
        self.assertTrue(self.seen.checked(LAN, 1098))
        self.assertFalse(self.seen.checked(LAN, 1158))

    def test_a_vpn_that_may_not_let_us_in_does_not_count(self):
        self.seen.checked(VPN._replace(answers=False), 1060)
        self.assertFalse(self.seen.checked(LAN, 1070))

    def test_leaving_home(self):
        self.seen.checked(VPN, 1060)
        self.assertFalse(self.seen.checked(LAN, 1060 + watch.BREAK))

    def test_back_at_another_address(self):
        self.seen.checked(VPN, 1060)
        self.assertFalse(self.seen.checked(LAN._replace(address="192.168.0.6"), 1070))

    def test_off_in_between(self):
        self.seen.checked(VPN, 1060)
        for now in (1070, 1078):
            self.seen.checked(system.NOWHERE, now)
        self.assertFalse(self.seen.checked(LAN, 1090))

    def test_off_and_on_again(self):
        for now in (1060, 1068):
            self.seen.checked(system.NOWHERE, now)
        self.seen.checked(VPN, 1060 + watch.SOON)
        self.assertFalse(self.seen.checked(LAN, 1070 + watch.SOON))

    def test_this_computer_moved_or_the_other_one_says_bye(self):
        self.seen.checked(VPN, 1060)
        self.seen.forget()
        self.assertFalse(self.seen.checked(LAN, 1070))
        self.seen.forget()
        self.seen.checked(VPN, 1080)
        self.assertFalse(self.seen.checked(LAN, 1090))

    def test_the_vpn_takes_a_moment_and_first_only_lets_connect(self):
        self.seen.checked(system.NOWHERE, 1060)
        self.seen.checked(VPN._replace(answers=False), 1068)
        self.seen.checked(VPN, 1080)
        self.assertTrue(self.seen.checked(LAN, 1098))

    def test_right_after_the_wifi_moved(self):
        """Seen for real: two looks within seconds found nothing, not even the VPN."""
        self.seen.patient = 1060 + watch.PATIENT
        for now in (1062, 1068, 1080):
            self.assertFalse(self.seen.checked(system.NOWHERE, now))
            self.assertTrue(self.seen.on)
        self.assertFalse(self.seen.checked(VPN, 1090))
        self.assertTrue(self.seen.checked(LAN, 1110))
        for now in (1200, 1208):
            self.seen.checked(system.NOWHERE, now)
        self.assertIs(self.seen.on, False)


class WatchTest(WatcherCase):
    def setUp(self):
        super().setUp()
        with state.transaction() as st:
            for pid in st["peers"]:
                st["peers"][pid]["vpn"] = PEER["vpn"]
        self.reach["gamma"] = LAN
        self.watcher.refresh()

    def config(self):
        return paths.ssh_config().read_text()

    def break_once(self):
        """As the watcher sees it. Seconds apart, so let the last one be long ago."""
        with state.transaction() as st:
            peer = st["peers"]["b" * 32]
            peer["breaks"] = [when - 600 for when in peer.get("breaks", [])]
        self.watcher.refresh()
        seen = self.watcher.seen["b" * 32]
        for reach in (LAN, VPN, LAN):
            self.reach["beta"] = reach
            seen.due = 0
            self.settle(lambda: seen.reach == reach and not seen.busy)

    def test_after_two_breaks_the_vpn_comes_first(self):
        usual = f'Match originalhost beta.local !exec "{paths.route_command()} beta.local 22"\n'
        first = f'Match originalhost beta.local exec "{paths.route_command()} --any 100.64.0.7 22"\n'
        self.watcher.step(0)
        self.break_once()
        self.assertIn(usual, self.config())
        self.watcher.roamed = [time.monotonic() - 5] * 12 + [time.monotonic() - 2 * watch.HOUR]
        self.break_once()
        self.settle(lambda: first in self.config())
        self.assertNotIn(usual, self.config())
        self.assertIn("Match originalhost gamma.local !exec", self.config())
        self.assertEqual(state.load()["roamed"], 12)
        with state.transaction() as st:  # three days later
            peer = st["peers"]["b" * 32]
            peer["breaks"] = [when - breaks.KEEP for when in peer["breaks"]]
        self.watcher.refresh()
        self.settle(lambda: usual in self.config())

    def test_a_new_card_keeps_the_breaks(self):
        self.break_once()
        with state.transaction() as st:
            state.merge(st, [{**PEER, "updated": 99}], {}, via="direct")
        peer = state.load()["peers"]["b" * 32]
        self.assertEqual((peer["updated"], len(peer["breaks"])), (99, 1))
        self.assertNotIn("breaks", state.card_of(peer))

    def test_the_other_computer_tells(self):
        self.reach["beta"] = LAN._replace(breaks=(40, 4000))
        self.settle(lambda: "--any" in self.config())
        stamp = os.stat(paths.state_file()).st_mtime_ns
        for _ in range(5):  # the same two again
            self.settle(lambda: not self.watcher.seen["b" * 32].busy)
            self.watcher.step(0.05)
        self.assertEqual(os.stat(paths.state_file()).st_mtime_ns, stamp)

    def test_asking_tells_too(self):
        self.watcher.listen()
        self.settle(lambda: all(seen.on for seen in self.watcher.seen.values()))
        with state.transaction() as st:
            st["peers"]["b" * 32]["breaks"] = [time.time() - 5000]
        self.watcher.refresh()
        answers = []
        thread = threading.Thread(target=lambda: answers.append(presence.ask(
            "b" * 32, self.watcher.st["id"], "127.0.0.1", tries=(0.4,), breaks=(100,))))
        thread.start()
        while thread.is_alive():
            self.settle(lambda: True)
            self.watcher.step(0.05)
        self.assertEqual([round(age, -2) for age in answers[0]["breaks"]], [5000])  # what it knew when asked
        self.settle(lambda: "--any" in self.config())

    def test_joining_an_access_point_is_followed_by_looks(self):
        self.settle(lambda: all(seen.on for seen in self.watcher.seen.values()))
        for seen in self.watcher.seen.values():
            seen.due = 9e9
        for now in (1000, 1000.5, 1100):  # the second event tells of the same
            self.watcher.roam(now)
        self.assertEqual(self.watcher.roamed, [1000, 1100])
        for seen in self.watcher.seen.values():
            self.assertEqual(seen.soon, tuple(1100 + wait for wait in watch.ROAMED))
            self.assertEqual(seen.patient, 1100 + watch.PATIENT)
        self.watcher.roam(1000 + watch.HOUR + 50)
        self.assertEqual(len(self.watcher.roamed), 2)


class RouteTest(HomeTestCase):
    def test_ssh_chooses(self):
        """The real ssh, with the real check: which address does it pick?"""
        self.addCleanup(setattr, paths, "INSTALLED_ROUTE", paths.INSTALLED_ROUTE)
        paths.INSTALLED_ROUTE = "/none"  # the one in this checkout
        local, vpn = socket.socket(), socket.socket(socket.AF_INET6)
        port = 0
        for server, address in ((local, "127.0.0.1"), (vpn, "::1")):
            self.addCleanup(server.close)
            server.bind((address, port))
            server.listen()
            port = server.getsockname()[1]
        peer = {**card("b", "beta", 10), "port": port, "address": "127.0.0.1",
                "vpn": [{"addr": "::1", "kind": "VPN"}]}

        def chosen(**more):
            with state.transaction() as st:
                st["peers"] = {peer["id"]: {**peer, "via": "direct", "added": 1, **more}}
                render.apply(st)
            done = subprocess.run(["ssh", "-F", str(paths.ssh_config()), "-G", "beta.local"],
                                  capture_output=True, text=True, check=True)
            return next(line.split()[1] for line in done.stdout.splitlines() if line.startswith("hostname "))
        now = time.time()
        self.assertEqual(chosen(), "127.0.0.1")
        self.assertEqual(chosen(breaks=[now - 5000]), "127.0.0.1")
        self.assertEqual(chosen(breaks=[now - 5000, now - 900]), "::1")
        vpn.close()  # it is off
        self.assertEqual(chosen(breaks=[now - 5000, now - 900]), "127.0.0.1")


def attribute(kind, data):
    return struct.pack("HH", 4 + len(data), kind) + data + b"\0" * (-len(data) % 4)


def event(command, kind=28, data=b""):
    return struct.pack("IHHII", 20 + len(data), kind, 0, 0, 0) + struct.pack("BBH", command, 1, 0) + data


class WifiTest(unittest.TestCase):
    def test_events(self):
        for command in (38, 46, 47):  # associate, connect, roam
            self.assertTrue(wifi.joined(event(command)))
        for command in (37, 39, 40, 48, 33, 34):  # authenticate, leaving, scanning
            self.assertFalse(wifi.joined(event(command)))
        self.assertTrue(wifi.joined(event(37, data=attribute(6, b"abcdef")) + event(46)))
        for odd in (b"", b"\0" * 64, b"\xff" * 64, event(46)[:19], struct.pack("I", 9999) + b"\0" * 40):
            self.assertFalse(wifi.joined(odd))

    def test_group(self):
        def group(name, number):
            return attribute(1, name + b"\0") + attribute(2, struct.pack("I", number))
        groups = attribute(1, group(b"scan", 24)) + attribute(2, group(b"mlme", 26))
        answer = event(1, 16, attribute(1, struct.pack("H", 28)) + attribute(7, groups))
        self.assertEqual(wifi.group_id(answer), 26)
        self.assertIsNone(wifi.group_id(event(1, 16, attribute(7, attribute(1, group(b"scan", 24))))))
        self.assertIsNone(wifi.group_id(event(0, wifi.NLMSG_ERROR, b"\0" * 16)))
        self.assertIsNone(wifi.group_id(b""))

    def test_the_kernel_answers_or_there_is_no_wifi(self):
        sock = wifi.events()
        if sock is not None:
            self.assertFalse(sock.getblocking())
            sock.close()


if __name__ == "__main__":
    unittest.main()
