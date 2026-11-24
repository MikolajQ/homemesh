"""The root helper against a fake root directory and stand-in commands."""

import functools
import hashlib
import http.server
import io
import json
import os
import pwd
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

from support import ROOT

HELPER = ROOT / "helper" / "homemesh-helper"

SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$LOG"
case "$1" in
  cat) case "$2" in sshd.service|systemd-resolved.service) exit 0;; esac
       [ -e "$HOMEMESH_HELPER_ROOT/usr/lib/systemd/system/$2" ] || [ -e "$HOMEMESH_HELPER_ROOT/etc/systemd/system/$2" ];;
  is-enabled) if [ -e "$STATE_DIR/masked-$2" ]; then echo masked
              elif [ -e "$STATE_DIR/enabled-$2" ]; then echo enabled; else echo disabled; fi;;
  is-active) case "$3" in systemd-resolved.service) exit 0;; *) [ -e "$STATE_DIR/running-$3" ];; esac;;
  enable) touch "$STATE_DIR/enabled-$2";;
  disable) rm -f "$STATE_DIR/enabled-$2";;
  restart|start) touch "$STATE_DIR/running-$2";;
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
# systemd-resolved as it reads its files: in the order of names, the last MulticastDNS= counts.
RESOLVED_CONFIG = """#!/bin/sh
export LC_ALL=C
cd "$HOMEMESH_HELPER_ROOT"
for file in etc/systemd/resolved.conf.d/*.conf; do
  [ -e "$file" ] && { echo "# /$file"; cat "$file"; echo; }
done
"""
RESOLVECTL_FROM_FILES = """#!/bin/sh
value=$(systemd-analyze cat-config systemd/resolved.conf | sed -n 's/^MulticastDNS=//p' | tail -n 1)
[ -e "$STATE_DIR/port-5353-taken" ] && value=no
printf 'Global: %s\\nLink 2 (eth0): %s\\n' "${value:-no}" "${value:-no}"
"""
# Answers once some init system has started tailscaled.
TAILSCALE = """#!/bin/sh
echo "tailscale $*" >> "$LOG"
case "$1" in
  status) ls "$STATE_DIR"/running-tailscale* >/dev/null 2>&1 || { echo "failed to connect" >&2; exit 1; }
          echo '{"BackendState": "NeedsLogin"}';;
esac
"""
# Installs the stand-in tailscale with a systemd unit, as Arch's package does.
PACMAN = """#!/bin/sh
echo "pacman $*" >> "$LOG"
mkdir -p "$HOMEMESH_HELPER_ROOT/usr/bin" "$HOMEMESH_HELPER_ROOT/usr/lib/systemd/system"
cp "$FAKE_TAILSCALE" "$HOMEMESH_HELPER_ROOT/usr/bin/tailscale"
cp "$FAKE_TAILSCALE" "$HOMEMESH_HELPER_ROOT/usr/bin/tailscaled"
touch "$HOMEMESH_HELPER_ROOT/usr/lib/systemd/system/tailscaled.service"
"""
RC_SERVICE = """#!/bin/sh
echo "rc-service $*" >> "$LOG"
case "$2" in
  start) touch "$STATE_DIR/running-$1";;
  status) [ -e "$STATE_DIR/running-$1" ];;
esac
"""
SV = """#!/bin/sh
echo "sv $*" >> "$LOG"
case "$1" in
  up) touch "$STATE_DIR/running-$2";;
  status) [ -e "$STATE_DIR/running-$2" ] && echo "run: $2" || echo "down: $2";;
esac
"""
DINITCTL = """#!/bin/sh
echo "dinitctl $*" >> "$LOG"
case "$1" in
  start) touch "$STATE_DIR/running-$2";;
  status) [ -e "$STATE_DIR/running-$2" ] && echo "State: STARTED" || echo "State: STOPPED";;
esac
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

    def run_helper(self, *args, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin", "HOMEMESH_HELPER_ROOT": str(self.root),
               "HOMEMESH_HELPER_INIT": "systemd", "LOG": str(self.log), "STATE_DIR": str(self.state_dir),
               **extra}
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


class MdnsTest(HelperTest):
    """name.local when systemd-resolved has mDNS off at first."""

    def setUp(self):
        super().setUp()
        self.tool("systemd-analyze", RESOLVED_CONFIG)
        self.tool("resolvectl", RESOLVECTL_FROM_FILES)
        self.tool("nmcli", LOGGER)
        self.resolved = self.root / "etc/systemd/resolved.conf.d"
        self.resolved.mkdir(parents=True)
        self.nm = self.root / "etc/NetworkManager/conf.d"
        (self.root / "etc/nsswitch.conf").write_text("hosts: files resolve [!UNAVAIL=return] dns\n")

    def off_in(self, name):
        (self.resolved / name).write_text("[Resolve]\nDNS=127.0.0.1\nMulticastDNS=no\n")

    def files(self):
        return sorted(path.name for path in self.resolved.iterdir())

    def saved(self):
        return json.loads((self.root / "var/lib/homemesh/state.json").read_text())

    def avahi(self, masked=False):
        (self.root / "usr/lib/systemd/system").mkdir(parents=True)
        (self.root / "usr/lib/systemd/system/avahi-daemon.service").touch()
        (self.root / "usr/lib/libnss_mdns_minimal.so.2").touch()
        if masked:
            (self.state_dir / "masked-avahi-daemon.service").touch()

    def enable(self):
        code, steps = self.run_helper("enable")
        self.assertEqual(steps[-1]["step"], "mdns")
        self.assertEqual(code == 0, steps[-1]["ok"])
        return steps[-1]["ok"], steps[-1]["msg"]

    def test_not_set_anywhere(self):
        ok, message = self.enable()
        self.assertEqual((ok, message), (True, ".local names work (systemd-resolved)."))
        self.assertEqual(self.files(), ["zz-homemesh-mdns.conf"])
        self.assertIn("systemctl restart systemd-resolved.service", self.calls())
        self.run_helper("disable")
        self.assertEqual(self.files(), [])

    def test_overrides_a_later_file_and_says_so(self):
        self.off_in("90-dns.conf")
        ok, message = self.enable()
        self.assertTrue(ok, message)
        self.assertIn("MulticastDNS=no in /etc/systemd/resolved.conf.d/90-dns.conf", message)
        self.run_helper("disable")
        self.assertEqual(self.files(), ["90-dns.conf"])

    def test_takes_over_from_the_old_file_names(self):
        self.off_in("90-dns.conf")
        (self.state_dir / "running-NetworkManager.service").touch()
        self.nm.mkdir(parents=True)
        old = [self.resolved / "10-homemesh-mdns.conf", self.nm / "10-homemesh-mdns.conf"]
        old[0].write_text("[Resolve]\nMulticastDNS=yes\n")
        old[1].write_text("[connection]\nconnection.mdns=2\n")
        (self.root / "var/lib/homemesh").mkdir(parents=True)
        (self.root / "var/lib/homemesh/state.json").write_text(json.dumps(
            {"users": ["ania"], "created": ["/" + str(path.relative_to(self.root)) for path in old] * 2}))
        self.assertTrue(self.enable()[0])
        self.assertTrue(self.enable()[0])
        self.assertEqual(self.files(), ["90-dns.conf", "zz-homemesh-mdns.conf"])
        self.assertEqual(sorted(path.name for path in self.nm.iterdir()), ["zz-homemesh-mdns.conf"])
        self.assertEqual(self.saved()["created"], ["/etc/systemd/resolved.conf.d/zz-homemesh-mdns.conf",
                                                   "/etc/NetworkManager/conf.d/zz-homemesh-mdns.conf"])
        self.assertIn("nmcli general reload conf", self.calls())

    def test_avahi_takes_over_when_resolved_cannot(self):
        (self.state_dir / "port-5353-taken").touch()
        self.avahi()
        ok, message = self.enable()
        self.assertEqual((ok, message), (True, ".local names work (Avahi)."))
        self.assertEqual(self.files(), [])
        self.assertIn("systemctl enable avahi-daemon.service", self.calls())
        self.assertIn("systemctl restart avahi-daemon.service", self.calls())
        nsswitch = self.root / "etc/nsswitch.conf"
        self.assertIn("files mdns_minimal [NOTFOUND=return] resolve", nsswitch.read_text())
        self.run_helper("disable")
        self.assertIn("systemctl stop avahi-daemon.service", self.calls())
        self.assertEqual(nsswitch.read_text(), "hosts: files resolve [!UNAVAIL=return] dns\n")

    def test_masked_avahi_is_left_alone(self):
        (self.state_dir / "port-5353-taken").touch()
        self.avahi(masked=True)
        ok, message = self.enable()
        self.assertFalse(ok)
        self.assertIn("masked", message)
        self.assertEqual(self.files(), [])
        self.assertNotIn("systemctl enable avahi-daemon.service", self.calls())

    def test_names_the_file_it_cannot_override(self):
        self.off_in("zzz-last.conf")
        ok, message = self.enable()
        self.assertFalse(ok)
        self.assertIn("MulticastDNS=no in /etc/systemd/resolved.conf.d/zzz-last.conf", message)
        self.assertEqual(self.files(), ["zzz-last.conf"])


class TailscaleTest(HelperTest):
    """homemesh-helper tailscale: install, a service for any init system, the operator."""

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake-tailscale"
        self.fake.write_text(TAILSCALE)
        self.fake.chmod(0o755)
        self.user = pwd.getpwuid(os.getuid()).pw_name
        for name, script in (("pacman", PACMAN), ("rc-service", RC_SERVICE), ("rc-update", LOGGER),
                             ("sv", SV), ("dinitctl", DINITCTL)):
            self.tool(name, script)

    def installed(self):
        """Tailscale already there, as a package without any service definition."""
        (self.root / "usr/bin").mkdir(parents=True)
        for name in ("tailscale", "tailscaled"):
            shutil.copy(self.fake, self.root / "usr/bin" / name)

    def tailscale(self, **extra):
        return self.run_helper("tailscale", FAKE_TAILSCALE=str(self.fake), **extra)

    def test_pacman_and_systemd(self):
        code, steps = self.tailscale(HOMEMESH_HELPER_PM="pacman")
        self.assertEqual(code, 0, steps)
        self.assertEqual([(s["step"], s["ok"]) for s in steps],
                         [("install", True), ("service", True), ("operator", True)])
        self.assertEqual(steps[0]["msg"], "Installed Tailscale with pacman.")
        calls = self.calls()
        self.assertIn("pacman -S --needed --noconfirm tailscale", calls)
        self.assertIn("systemctl enable tailscaled.service", calls)
        self.assertIn("systemctl start tailscaled.service", calls)
        self.assertIn(f"tailscale set --operator={self.user}", calls)
        self.assertFalse((self.root / "etc/systemd/system/tailscaled.service").exists())

    def test_running_service_is_left_alone(self):
        self.installed()
        (self.root / "usr/lib/systemd/system").mkdir(parents=True)
        (self.root / "usr/lib/systemd/system/tailscaled.service").touch()
        (self.state_dir / "enabled-tailscaled.service").touch()
        (self.state_dir / "running-tailscaled.service").touch()
        code, steps = self.tailscale()
        self.assertEqual(code, 0, steps)
        self.assertEqual(steps[0]["msg"], "Tailscale is already installed.")
        self.assertNotIn("systemctl start", self.calls())
        self.assertNotIn("systemctl restart", self.calls())

    def test_openrc_gets_a_script(self):
        self.installed()
        code, steps = self.tailscale(HOMEMESH_HELPER_INIT="openrc")
        self.assertEqual(code, 0, steps)
        script = self.root / "etc/init.d/tailscaled"
        self.assertTrue(os.access(script, os.X_OK))
        text = script.read_text()
        self.assertTrue(text.startswith("#!/sbin/openrc-run\n"))
        self.assertIn('command="/usr/bin/tailscaled"', text)
        self.assertIn("--state=/var/lib/tailscale/tailscaled.state", text)
        self.assertIn("rc-update add tailscaled default", self.calls())
        self.assertIn("rc-service tailscaled start", self.calls())

    def test_runit_gets_a_service_directory(self):
        self.installed()
        code, steps = self.tailscale(HOMEMESH_HELPER_INIT="runit")
        self.assertEqual(code, 0, steps)
        run = self.root / "etc/sv/tailscaled/run"
        self.assertTrue(os.access(run, os.X_OK))
        self.assertIn("exec /usr/bin/tailscaled --state=", run.read_text())
        self.assertTrue((self.root / "var/service/tailscaled").is_symlink())
        self.assertIn("sv up tailscaled", self.calls())

    def test_dinit_gets_a_service(self):
        self.installed()
        code, steps = self.tailscale(HOMEMESH_HELPER_INIT="dinit")
        self.assertEqual(code, 0, steps)
        self.assertIn("command = /usr/bin/tailscaled --state=",
                      (self.root / "etc/dinit.d/tailscaled").read_text())
        self.assertIn("dinitctl enable tailscaled", self.calls())
        self.assertIn("dinitctl start tailscaled", self.calls())

    def packages(self, checksum=None):
        """A stand-in for pkgs.tailscale.com with one static build."""
        site = self.tmp / "pkgs"
        site.mkdir()
        name = "tailscale_1.2.3_amd64.tgz"
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as tar:
            for program in ("tailscale", "tailscaled"):
                tar.add(self.fake, f"tailscale_1.2.3_amd64/{program}")
        (site / name).write_bytes(data.getvalue())
        (site / f"{name}.sha256").write_text((checksum or hashlib.sha256(data.getvalue()).hexdigest()) + "\n")
        arches = ("amd64", "arm64", "arm", "386", "riscv64")
        index = json.dumps({"TarballsVersion": "1.2.3", "Tarballs": {arch: name for arch in arches}}).encode()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/?mode=json":
                    return super().do_GET()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(index)))
                self.end_headers()
                self.wfile.write(index)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(site)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    def test_static_build_when_no_package(self):
        code, steps = self.tailscale(HOMEMESH_HELPER_PM="none", HOMEMESH_HELPER_TAILSCALE_URL=self.packages())
        self.assertEqual(code, 0, steps)
        self.assertEqual(steps[0]["msg"], "Installed Tailscale 1.2.3 from tailscale.com.")
        self.assertTrue(os.access(self.root / "usr/local/bin/tailscaled", os.X_OK))
        unit = (self.root / "etc/systemd/system/tailscaled.service").read_text()
        self.assertIn("ExecStart=/usr/local/bin/tailscaled --state=", unit)
        self.assertIn("systemctl daemon-reload", self.calls())

    def test_static_build_with_wrong_checksum_is_refused(self):
        code, steps = self.tailscale(HOMEMESH_HELPER_TAILSCALE_URL=self.packages(checksum="0" * 64))
        self.assertEqual(code, 1)
        self.assertEqual([(s["step"], s["ok"]) for s in steps], [("install", False)])
        self.assertIn("checksum", steps[0]["msg"])
        self.assertFalse((self.root / "usr/local/bin/tailscale").exists())

if __name__ == "__main__":
    unittest.main()
