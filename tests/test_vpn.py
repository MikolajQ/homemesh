"""Away from home: VPN addresses on cards, keys, SSH fallback and the route check."""

import os
import socket
import subprocess
import sys
import time
import unittest

from support import ROOT, HomeTestCase, card

from homemesh import paths, render, state, vpn

LINKS = [
    {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "scope": "host"}]},
    {"ifname": "enp2s0", "addr_info": [{"local": "192.168.1.20", "scope": "global"},
                                       {"local": "fe80::1", "scope": "link"}]},
    {"ifname": "tailscale0", "addr_info": [{"local": "fd7a:115c:a1e0::11", "scope": "global"},
                                           {"local": "100.101.102.103", "scope": "global"}]},
    {"ifname": "tun0", "addr_info": [{"local": "201:abcd::1", "scope": "global"}]},
    {"ifname": "tun1", "addr_info": [{"local": "10.8.0.2", "scope": "global"}]},  # a work VPN
    {"ifname": "wwan0", "addr_info": [{"local": "100.70.1.2", "scope": "global"}]},  # mobile CGNAT
]


class DetectTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("HOMEMESH_TEST_VPN", None)

    def test_known_vpns_only_ipv4_first(self):
        self.assertEqual(vpn.addresses(LINKS), [
            {"addr": "100.101.102.103", "kind": "Tailscale"},
            {"addr": "fd7a:115c:a1e0::11", "kind": "Tailscale"},
            {"addr": "201:abcd::1", "kind": "Yggdrasil"}])

    def test_validate(self):
        good = [{"addr": "100.101.102.103", "kind": "Tailscale"}]
        self.assertEqual(vpn.validate(good), good)
        for bad in ([{"addr": "8.8.8.8", "kind": "Tailscale"}],
                    [{"addr": "100.101.102.103\nHost *", "kind": "Tailscale"}],
                    [{"addr": "100.101.102.103", "kind": "Evil\nName"}],
                    "100.101.102.103", [good[0]] * 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                vpn.validate(bad)


class RenderTest(HomeTestCase):
    def test_fallback_block_and_key_sources(self):
        peer = dict(card("b", "beta", 10), vpn=[{"addr": "100.101.102.103", "kind": "Tailscale"}])
        with state.transaction() as st:
            state.merge(st, [peer, card("c", "gamma", 10)], {}, via="direct")
            render.apply(st)
        config = paths.ssh_config().read_text()
        match = f'Match originalhost beta.local !exec "{paths.route_command()} beta.local 22"\n' \
                "    HostName 100.101.102.103\n    AddressFamily any\n"
        self.assertIn(match, config)
        self.assertLess(config.index(match), config.index("Host beta.local"))
        self.assertNotIn("Match originalhost gamma.local", config)
        keys = paths.authorized_keys().read_text().splitlines()
        beta = next(line for line in keys if "homemesh:" + "b" * 32 in line)
        gamma = next(line for line in keys if "homemesh:" + "c" * 32 in line)
        self.assertIn(",127.0.0.0/8,100.101.102.103\" ", beta)
        self.assertNotIn("100.101.102.103", gamma)


def listening_port():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    return server


class RouteTest(unittest.TestCase):
    ROUTE = [sys.executable, str(ROOT / "helper" / "homemesh-route")]

    def route(self, host, port):
        started = time.monotonic()
        code = subprocess.run(self.ROUTE + [host, str(port)], capture_output=True).returncode
        return code, time.monotonic() - started

    def test_answers_on_local_network(self):
        with listening_port() as server:
            self.assertEqual(self.route("127.0.0.1", server.getsockname()[1])[0], 0)

    def test_closed_port_or_unknown_name_is_away_quickly(self):
        with listening_port() as server:
            port = server.getsockname()[1]
        code, took = self.route("127.0.0.1", port)
        self.assertEqual(code, 1)
        code, took = self.route("nie-ma-takiego-komputera.local", 22)
        self.assertEqual(code, 1)
        self.assertLess(took, 3)

    def test_public_address_is_not_local(self):
        self.assertEqual(self.route("192.0.2.1", 22)[0], 1)  # TEST-NET, not a LAN address


if __name__ == "__main__":
    unittest.main()
