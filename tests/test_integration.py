"""Three computers on one machine: private sshd instances, real pairing and syncing.

Each "computer" has its own home directory, host key, sshd on a high port
(loopback only) and pairing port. sshd runs as the current user, so it only
lets the current user in, which is all the test needs. Nothing touches the
real ~/.ssh.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import ROOT, make_host_key

SSHD = "/usr/bin/sshd" if os.path.exists("/usr/bin/sshd") else "/usr/sbin/sshd"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class Computer:
    def __init__(self, base, name, **env):
        self.name = name
        self.dir = base / name
        self.home = self.dir / "home"
        self.home.mkdir(parents=True)
        self.host_key = make_host_key(self.dir)
        self.ssh_port, self.pair_port = free_port(), free_port()
        dropin = self.dir / "dropin.conf"
        dropin.write_text("# test\n")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("XDG_", "SSH_AUTH_SOCK", "HOMEMESH_"))}
        self.env.update({
            "HOMEMESH_HOME": str(self.home), "HOMEMESH_NAME": name,
            "HOMEMESH_HOSTKEY": f"{self.host_key}.pub", "HOMEMESH_SSH_PORT": str(self.ssh_port),
            "HOMEMESH_ADVERTISE_ADDRESS": "127.0.0.1", "HOMEMESH_PORT": str(self.pair_port),
            "HOMEMESH_SSHD_DROPIN": str(dropin), "HOMEMESH_TEST_AUTOCONFIRM": "1",
            # Keep discovery off the real network.
            "HOMEMESH_DISCOVERY_TARGETS": "127.0.0.1:9",
            "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.env.update(env)
        wrapper = self.dir / "force-command"
        exports = " ".join(f"{k}='{v}'" for k, v in self.env.items() if k.startswith(("HOMEMESH_", "PYTHON")))
        wrapper.write_text("#!/bin/sh\n"
                           f'[ "$SSH_ORIGINAL_COMMAND" = "homemesh sync-serve" ] || exit 64\n'
                           f"exec env {exports} {sys.executable} -m homemesh sync-serve\n")
        wrapper.chmod(0o755)
        config = self.dir / "sshd_config"
        config.write_text("\n".join([
            f"Port {self.ssh_port}", "ListenAddress 127.0.0.1", f"HostKey {self.host_key}",
            f"PidFile {self.dir / 'sshd.pid'}", f"AuthorizedKeysFile {self.home / '.ssh/authorized_keys'}",
            "StrictModes no", "UsePAM no", "PasswordAuthentication no", "KbdInteractiveAuthentication no",
            f"ForceCommand {wrapper}", "LogLevel ERROR", ""]))
        with open(self.dir / "sshd.log", "w") as log:
            self.sshd = subprocess.Popen([SSHD, "-D", "-e", "-f", str(config)],
                                         stdout=subprocess.DEVNULL, stderr=log)
        if not wait_port(self.ssh_port):
            raise unittest.SkipTest(f"sshd did not start: {(self.dir / 'sshd.log').read_text()[-300:]}")

    def run(self, *args, timeout=60):
        return subprocess.run([sys.executable, "-m", "homemesh", *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def wait_for_pairing(self):
        process = subprocess.Popen([sys.executable, "-m", "homemesh", "pair", "--wait"], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if not wait_port(self.pair_port):
            process.kill()
            raise AssertionError(f"{self.name} is not waiting: {process.stdout.read()}")
        return process

    def state(self):
        return json.loads((self.home / ".config/homemesh/state.json").read_text())

    def peers(self):
        return {peer["name"]: peer for peer in self.state()["peers"].values()}

    def authorized(self):
        path = self.home / ".ssh/authorized_keys"
        return path.read_text() if path.exists() else ""

    def stop(self):
        self.sshd.terminate()
        self.sshd.wait(5)


@unittest.skipUnless(os.path.exists(SSHD) and shutil.which("ssh"), "needs OpenSSH")
class GroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="homemesh-group-"))
        cls.computers = []
        try:
            for name in ("alfa", "beta", "gamma"):
                cls.computers.append(Computer(cls.tmp, name))
        except BaseException:
            cls.tearDownClass()
            raise
        cls.alfa, cls.beta, cls.gamma = cls.computers

    @classmethod
    def tearDownClass(cls):
        for computer in cls.computers:
            computer.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pair(self, asker, answerer):
        waiting = answerer.wait_for_pairing()
        asked = asker.run("pair", f"127.0.0.1:{answerer.pair_port}")
        waited, _ = waiting.communicate(timeout=60)
        self.assertEqual(asked.returncode, 0, asked.stdout + asked.stderr)
        self.assertEqual(waiting.returncode, 0, waited)
        codes = [line.split("Code:")[1].strip() for line in (asked.stdout + "\n" + waited).splitlines()
                 if "Code:" in line]
        self.assertEqual(len(codes), 2, codes)
        self.assertEqual(codes[0], codes[1])

    def test_group(self):
        alfa, beta, gamma = self.alfa, self.beta, self.gamma

        self.pair(alfa, beta)
        self.assertEqual(set(alfa.peers()), {"beta"})
        self.assertEqual(alfa.peers()["beta"]["via"], "direct")
        self.assertIn("homemesh:", beta.authorized())

        # gamma pairs with beta only; alfa is introduced.
        self.pair(gamma, beta)
        self.assertEqual(set(gamma.peers()), {"alfa", "beta"})
        self.assertEqual(gamma.peers()["alfa"]["via"], beta.state()["id"])
        self.assertEqual(set(alfa.peers()), {"beta", "gamma"})
        gamma_key = gamma.state()["id"]
        self.assertIn(f"homemesh:{gamma_key}", alfa.authorized())

        # alfa can now reach gamma, whom it never paired with.
        report = alfa.run("sync")
        self.assertEqual(report.returncode, 0)
        self.assertNotIn("✗", report.stdout, report.stdout)

        # Removing gamma on alfa spreads to beta.
        removed = alfa.run("remove", "gamma")
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertEqual(set(alfa.peers()), {"beta"})
        self.assertEqual(set(beta.peers()), {"alfa"})
        self.assertNotIn(f"homemesh:{gamma_key}", beta.authorized())
        self.assertNotIn(f"homemesh:{gamma_key}", alfa.authorized())

        # Pairing gamma again brings it back everywhere.
        self.pair(gamma, alfa)
        self.assertEqual(set(alfa.peers()), {"beta", "gamma"})
        self.assertIn("gamma", beta.peers())
        self.assertIn(f"homemesh:{gamma_key}", beta.authorized())

        # Bookmarks for the file manager.
        bookmarks = (alfa.home / ".config/gtk-3.0/bookmarks").read_text()
        self.assertIn(f"sftp://beta.local{beta.home} beta", bookmarks)


@unittest.skipUnless(os.path.exists(SSHD) and shutil.which("ssh"), "needs OpenSSH")
class AwayTest(unittest.TestCase):
    """beta is "away": its local address does not answer, its VPN address does."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="homemesh-away-"))
        cls.computers = []
        try:
            cls.computers.append(Computer(cls.tmp, "alfa"))
            cls.computers.append(Computer(cls.tmp, "beta", HOMEMESH_ADVERTISE_ADDRESS="127.0.0.3",
                                          HOMEMESH_TEST_VPN="127.0.0.1"))
        except BaseException:
            cls.tearDownClass()
            raise
        cls.alfa, cls.beta = cls.computers

    @classmethod
    def tearDownClass(cls):
        for computer in cls.computers:
            computer.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_reached_through_vpn(self):
        GroupTest.pair(self, self.alfa, self.beta)
        beta = self.alfa.peers()["beta"]
        self.assertEqual(beta["vpn"], [{"addr": "127.0.0.1", "kind": "VPN"}])
        report = self.alfa.run("sync")
        self.assertEqual(report.returncode, 0)
        self.assertNotIn("✗", report.stdout, report.stdout + report.stderr)
        status = self.alfa.run("status")
        self.assertIn("through VPN", status.stdout)


if __name__ == "__main__":
    unittest.main()
