"""The terminal setting against a real sshd: what a paired key may do.

One private sshd (as the current user, on loopback) reads the
authorized_keys that HomeMesh writes; a client logs in with the paired
computer's key. Unlike the group tests there is no ForceCommand, so the key
options alone decide.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, before_release, card, make_host_key
from test_integration import SSHD, free_port, wait_port

from homemesh import render, share, state


@before_release
@unittest.skipUnless(os.path.exists(SSHD) and shutil.which("ssh") and shutil.which("sftp")
                     and share.sftp_server(), "needs OpenSSH")
class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="homemesh-commands-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved_env = dict(os.environ)
        host_key = make_host_key(self.tmp)
        os.environ.update({"HOMEMESH_HOME": str(self.home), "HOMEMESH_NAME": "alfa",
                           "HOMEMESH_HOSTKEY": f"{host_key}.pub",
                           "HOMEMESH_GATE": f"env HOMEMESH_HOME={self.home} LANGUAGE=en "
                                            f"PYTHONPATH={ROOT} {sys.executable} -m homemesh gate"})
        self.key = self.tmp / "client_ed25519"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        public = (self.tmp / "client_ed25519.pub").read_text().split()
        self.peer = dict(card("b", "beta", 10), userkey=" ".join(public[:2]))
        self.port = free_port()
        config = self.tmp / "sshd_config"
        config.write_text("\n".join([
            f"Port {self.port}", "ListenAddress 127.0.0.1", f"HostKey {host_key}",
            f"PidFile {self.tmp / 'sshd.pid'}", f"AuthorizedKeysFile {self.home / '.ssh/authorized_keys'}",
            "StrictModes no", "UsePAM no", "PasswordAuthentication no", "KbdInteractiveAuthentication no",
            f"Subsystem sftp {share.sftp_server()}", "LogLevel ERROR", ""]))
        self.log = open(self.tmp / "sshd.log", "w")
        self.sshd = subprocess.Popen([SSHD, "-D", "-e", "-f", str(config)],
                                     stdout=subprocess.DEVNULL, stderr=self.log)
        if not wait_port(self.port):
            self.tearDown()
            self.skipTest("sshd did not start")

    def tearDown(self):
        self.sshd.terminate()
        self.sshd.wait(5)
        self.log.close()
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def share(self, mode, shell):
        with state.transaction() as st:
            st["share"] = {"mode": mode, "folders": [], "shell": shell}
            state.merge(st, [self.peer], {}, via="direct")
            render.apply(st)

    def ssh(self, *args):
        return subprocess.run(["ssh", "-F", "/dev/null", "-i", str(self.key), "-p", str(self.port),
                               "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                               "-o", "LogLevel=ERROR", *args],
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def forwarding_refused(self):
        done = self.ssh("-o", "ExitOnForwardFailure=yes", "-R", f"127.0.0.1:{free_port()}:127.0.0.1:9",
                        "127.0.0.1", "true")
        return done.returncode == 255

    def sftp_works(self):
        done = subprocess.run(["sftp", "-F", "/dev/null", "-i", str(self.key), "-P", str(self.port),
                               "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                               "-b", "-", "127.0.0.1"], input="pwd\n",
                              capture_output=True, text=True, timeout=30)
        return done.returncode == 0

    def test_terminal_on(self):
        self.share("home", True)
        done = self.ssh("127.0.0.1", "echo", "homemesh-ran")
        self.assertIn("homemesh-ran", done.stdout, done.stderr)
        self.assertTrue(self.sftp_works())
        self.assertTrue(self.forwarding_refused())

    def test_terminal_off(self):
        self.share("home", False)
        done = self.ssh("127.0.0.1", "echo", "homemesh-ran")
        self.assertEqual(done.returncode, share.REFUSED, done.stdout + done.stderr)
        self.assertNotIn("homemesh-ran", done.stdout)
        self.assertIn("terminal is off", done.stderr)
        self.assertTrue(self.sftp_works())
        self.assertTrue(self.forwarding_refused())


if __name__ == "__main__":
    unittest.main()
