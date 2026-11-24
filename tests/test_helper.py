"""The root helper against a fake root directory and stand-in commands."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

HELPER = ROOT / "helper" / "homemesh-helper"

SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$LOG"
case "$1" in
  cat) case "$2" in sshd.service|systemd-resolved.service) exit 0;; *) exit 1;; esac;;
  is-enabled) [ -e "$STATE_DIR/enabled-$2" ] && echo enabled || echo disabled;;
  is-active) case "$3" in systemd-resolved.service) exit 0;; *) [ -e "$STATE_DIR/running-$3" ];; esac;;
  enable) touch "$STATE_DIR/enabled-$2";;
  disable) rm -f "$STATE_DIR/enabled-$2";;
  restart) touch "$STATE_DIR/running-$2";;
  stop) rm -f "$STATE_DIR/running-$2";;
esac
"""
UFW = """#!/bin/sh
echo "ufw $*" >> "$LOG"
[ "$1" = status ] && echo "Status: active"
exit 0
"""
LOGGER = """#!/bin/sh
echo "$(basename "$0") $*" >> "$LOG"
"""
RESOLVECTL = """#!/bin/sh
printf 'Global: yes\\nLink 2 (eth0): yes\\n'
"""


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="homemesh-helper-"))
        self.root = self.tmp / "root"
        self.bin = self.tmp / "bin"
        self.state_dir = self.tmp / "services"
        for directory in (self.root / "etc/ssh", self.bin, self.state_dir):
            directory.mkdir(parents=True)
        (self.root / "etc/ssh/sshd_config").write_text(
            "Include /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\n")
        (self.root / "etc/hosts").write_text("127.0.0.1 localhost\n127.0.1.1 stary\n")
        self.log = self.tmp / "log"
        self.log.touch()
        for name, script in (("systemctl", SYSTEMCTL), ("ssh-keygen", LOGGER), ("resolvectl", RESOLVECTL)):
            self.tool(name, script)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tool(self, name, script):
        path = self.bin / name
        path.write_text(script)
        path.chmod(0o755)

    def run_helper(self, *args):
        env = {"PATH": f"{self.bin}:/usr/bin", "HOMEMESH_HELPER_ROOT": str(self.root),
               "HOMEMESH_HELPER_INIT": "systemd", "LOG": str(self.log), "STATE_DIR": str(self.state_dir)}
        done = subprocess.run(["python3", str(HELPER), *args], env=env, capture_output=True, text=True)
        steps = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
        return done.returncode, steps

    def calls(self):
        return self.log.read_text()

    def test_enable_then_disable(self):
        code, steps = self.run_helper("enable")
        self.assertEqual(code, 0, steps)
        self.assertEqual([s["step"] for s in steps], ["sshd", "service", "firewall", "mdns"])
        dropin = self.root / "etc/ssh/sshd_config.d/10-homemesh.conf"
        self.assertIn("PasswordAuthentication no", dropin.read_text())
        self.assertIn("systemctl enable sshd.service", self.calls())
        self.assertIn("systemctl restart sshd.service", self.calls())
        saved = json.loads((self.root / "var/lib/homemesh/state.json").read_text())
        self.assertEqual(saved["sshd"], {"enabled": False, "running": False})
        self.assertEqual(saved["users"], [os.environ.get("USER") or saved["users"][0]])

        code, steps = self.run_helper("disable")
        self.assertEqual(code, 0, steps)
        self.assertFalse(dropin.exists())
        self.assertIn("systemctl disable sshd.service", self.calls())
        self.assertIn("systemctl stop sshd.service", self.calls())
        self.assertFalse((self.root / "var/lib/homemesh/state.json").exists())

    def test_sshd_that_was_on_stays_on(self):
        (self.state_dir / "enabled-sshd.service").touch()
        (self.state_dir / "running-sshd.service").touch()
        self.run_helper("enable")
        self.run_helper("disable")
        self.assertNotIn("systemctl disable sshd.service", self.calls())
        self.assertNotIn("systemctl stop sshd.service", self.calls())

    def test_firewall_rules_open_and_close(self):
        self.tool("ufw", UFW)
        self.run_helper("enable")
        allows = [line for line in self.calls().splitlines() if line.startswith("ufw allow")]
        self.assertEqual(len(allows), 13)
        self.assertIn("ufw allow in on tailscale0 to any port 22 proto tcp comment homemesh", allows)
        self.assertIn("ufw allow from 192.168.0.0/16 to any port 22 proto tcp comment homemesh", allows)
        self.run_helper("disable")
        deletes = [line for line in self.calls().splitlines() if line.startswith("ufw delete")]
        self.assertEqual(len(deletes), 13)

    def test_missing_include_stops_early(self):
        (self.root / "etc/ssh/sshd_config").write_text("UsePAM yes\n")
        code, steps = self.run_helper("enable")
        self.assertEqual(code, 1)
        self.assertFalse(steps[0]["ok"])
        self.assertFalse((self.root / "etc/ssh/sshd_config.d/10-homemesh.conf").exists())

    def test_set_hostname(self):
        code, steps = self.run_helper("set-hostname", "Zła nazwa")
        self.assertEqual(code, 1)
        code, steps = self.run_helper("set-hostname", "laptop-ani\n")
        self.assertEqual(code, 1)
        code, steps = self.run_helper("set-hostname", "laptop-ani")
        self.assertEqual(code, 0, steps)
        self.assertEqual((self.root / "etc/hostname").read_text(), "laptop-ani\n")

    def test_refuses_to_run_as_user_without_test_root(self):
        done = subprocess.run(["python3", str(HELPER), "enable"], capture_output=True, text=True,
                              env={"PATH": "/usr/bin"})
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
