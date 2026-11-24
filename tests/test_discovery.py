import socket
import time
import unittest

from support import ROOT  # noqa: F401  (puts the checkout on sys.path)

from homemesh.discovery import Discovery


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DiscoveryTest(unittest.TestCase):
    def test_two_computers_find_each_other_and_forget(self):
        port_a, port_b = free_udp_port(), free_udp_port()
        info_a = {"id": "a" * 32, "name": "alfa", "user": "ania", "port": 41753, "ready": True}
        info_b = {"id": "b" * 32, "name": "beta", "user": "ania", "port": 41754, "ready": False}
        a = Discovery(lambda: info_a, port=port_a, targets=[("127.0.0.1", port_b)])
        b = Discovery(lambda: info_b, port=port_b, targets=[("127.0.0.1", port_a)])
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (a.nearby() and b.nearby()):
                time.sleep(0.1)
            self.assertEqual([(e["name"], e["ready"], e["port"]) for e in a.nearby()],
                             [("beta", False, 41754)])
            self.assertEqual([e["name"] for e in b.nearby()], ["alfa"])
            self.assertEqual(a.nearby()[0]["ip"], "127.0.0.1")
        finally:
            a.close()
            b.close()

    def test_garbage_is_ignored(self):
        port = free_udp_port()
        disc = Discovery(lambda: {"id": "a" * 32, "name": "alfa", "user": "x", "port": 1, "ready": True},
                         port=port, targets=[("127.0.0.1", 9)])
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for junk in (b"hello", b"{}", b'{"app":"homemesh","id":"zz","name":"x","port":1}',
                             b'{"app":"homemesh","t":"announce","id":"' + b"c" * 32 + b'","name":"Bad Name","port":1}'):
                    sock.sendto(junk, ("127.0.0.1", port))
            time.sleep(0.3)
            self.assertEqual(disc.nearby(), [])
        finally:
            disc.close()


if __name__ == "__main__":
    unittest.main()
