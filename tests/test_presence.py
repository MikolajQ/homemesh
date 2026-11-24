import os
import socket
import threading
import time
import unittest

from support import HomeTestCase, card

from homemesh import presence, state, system


def free_udp_port(address="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((address, 0))
        return sock.getsockname()[1]


class PacketTest(HomeTestCase):
    def setUp(self):
        super().setUp()
        self.st = state.new_state()
        peer = {**card("b", "beta", 10), "vpn": [{"addr": "100.64.0.7", "kind": "Tailscale"}]}
        state.merge(self.st, [peer], {}, via="direct")

    def packet(self, kind="ping", sender="b" * 32, receiver=None, **more):
        return presence.message(kind, sender, receiver or self.st["id"], **more)

    def test_only_paired_computers_are_heard(self):
        def who(data, address="192.168.0.5"):
            note = presence.read(data)
            return presence.sender(self.st, note, address) if note else None
        self.assertEqual(who(self.packet()), "b" * 32)
        self.assertEqual(who(self.packet(), "100.64.0.7"), "b" * 32)  # its VPN address
        self.assertIsNone(who(self.packet(), "100.64.0.8"))  # somebody else's
        self.assertIsNone(who(self.packet(), "8.8.8.8"))
        self.assertIsNone(who(self.packet(sender="c" * 32)))
        self.assertIsNone(who(self.packet(receiver="d" * 32)))

    def test_garbage_is_ignored(self):
        for junk in (b"", b"x", b"[]", b"\xff\xfe", b'{"app":"homemesh","t":"announce"}',
                     b'{"app":"homemesh","t":"ping","from":"zz","to":1}', self.packet(sender="B" * 32)):
            self.assertIsNone(presence.read(junk), junk)

    def test_window_discovery_ignores_presence_packets(self):
        from homemesh.discovery import Discovery
        port = free_udp_port()
        disc = Discovery(lambda: {"id": self.st["id"], "name": "alfa", "user": "x", "port": 1, "ready": True},
                         port=port, targets=[("127.0.0.1", 9)])
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(self.packet(), ("127.0.0.1", port))
            time.sleep(0.3)
            self.assertEqual(disc.nearby(), [])
        finally:
            disc.close()


class AskTest(HomeTestCase):
    def answering(self, reply):
        """A UDP server on loopback; reply(note) -> bytes or None."""
        port = free_udp_port()
        os.environ["HOMEMESH_PORT"] = str(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", port))
        self.addCleanup(sock.close)

        def serve():
            while True:
                try:
                    data, where = sock.recvfrom(4096)
                except OSError:
                    return
                answer = reply(presence.read(data))
                if answer:
                    sock.sendto(answer, where)
        threading.Thread(target=serve, daemon=True).start()

    def ask(self):
        return presence.ask("a" * 32, "b" * 32, "127.0.0.1", tries=(0.2, 0.2))

    def test_answers(self):
        for ready in (True, False):
            self.answering(lambda note, r=ready: presence.pong(note, r))
            self.assertIs(self.ask(), ready)

    def test_an_answer_to_another_question_does_not_count(self):
        self.answering(lambda note: presence.pong({**note, "n": "stale"}, True))
        self.assertIsNone(self.ask())

    def test_another_computer_at_that_address_does_not_count(self):
        self.answering(lambda note: presence.pong({**note, "to": "c" * 32}, True))
        self.assertIsNone(self.ask())

    def test_nobody_listening_is_noticed_at_once(self):
        os.environ["HOMEMESH_PORT"] = str(free_udp_port())
        started = time.monotonic()
        self.assertIsNone(presence.ask("a" * 32, "b" * 32, "127.0.0.1"))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_reach_prefers_the_answer_to_an_ssh_connection(self):
        peer = {**card("b", "beta", 10), "address": "127.0.0.1", "port": 1}  # no SSH there
        self.answering(lambda note: presence.pong(note, True))
        self.assertEqual(system.reach(peer, "a" * 32), system.Reach("lan", "127.0.0.1", True))
        self.assertEqual(system.reach(peer, "a" * 32, "127.0.0.1"), system.Reach("lan", "127.0.0.1", True))
        self.assertEqual(system.reach(peer), system.NOWHERE)
        away = {**peer, "address": "127.0.0.9", "vpn": [{"addr": "127.0.0.1", "kind": "VPN"}]}
        self.assertEqual(system.reach(away, "a" * 32, "127.0.0.1").how, "vpn")  # although the address is private
        self.answering(lambda note: presence.pong(note, False))  # on, but not sharing
        self.assertEqual(system.reach(peer, "a" * 32).how, None)

    def test_listening_port_is_found_without_connecting(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["HOMEMESH_SSH_PORT"] = str(sock.getsockname()[1])
            self.assertFalse(system.ssh_listening())
            sock.listen(1)
            self.assertTrue(system.ssh_listening())


if __name__ == "__main__":
    unittest.main()
