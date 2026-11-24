"""Setting Tailscale up from the window or `homemesh away`, against a stand-in tailscale."""

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import support  # noqa: F401  (the path to homemesh, English messages)

from homemesh import system, tailscale

# State in files under $TS_DIR: "state" holds BackendState (no file: the
# service is not running), "operator" lets this user log in, "logged-in" is
# the person finishing the login in the browser.
FAKE = r"""#!/bin/sh
D="$TS_DIR"
echo "$* USER=$USER" >> "$D/log"
case "$1" in
  status)
    [ -f "$D/state" ] || { echo "failed to connect to local tailscaled; it doesn't appear to be running" >&2; exit 1; }
    printf '{"BackendState": "%s"}\n' "$(cat "$D/state")";;
  up)
    [ -f "$D/operator" ] || {
      printf "Access denied: checkprefs access denied\n\nUse 'sudo tailscale up'.\nTo not require root, use 'sudo tailscale set --operator=\$USER' once.\n" >&2
      exit 1; }
    [ "$(cat "$D/state")" = Running ] && exit 0
    printf '\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/0123abcd\n\n' >&2
    i=0
    while [ ! -f "$D/logged-in" ]; do sleep 0.05; i=$((i+1)); [ $i -gt 200 ] && exit 1; done
    echo Running > "$D/state"
    echo "Success." >&2;;
esac
"""


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="homemesh-tailscale-"))
        self.cli = self.dir / "tailscale"
        self.cli.write_text(FAKE)
        self.cli.chmod(0o755)
        self.saved = dict(os.environ)
        os.environ.update({"TS_DIR": str(self.dir), "HOMEMESH_TAILSCALE": str(self.cli),
                           "HOMEMESH_TEST_VPN": "100.101.102.103"})
        self.stages = []
        self.helper_calls = []
        self.helper_ok = True

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.dir, ignore_errors=True)

    def set_state(self, value):
        (self.dir / "state").write_text(value + "\n")

    def helper(self, args):
        """What homemesh-helper tailscale does: installed, running, this user is the operator."""
        self.helper_calls.append(args)
        if not self.helper_ok:
            return system.HelperResult(False, [{"step": "install", "ok": False, "msg": "no network"}])
        os.environ["HOMEMESH_TAILSCALE"] = str(self.cli)
        if not (self.dir / "state").exists():
            self.set_state("NeedsLogin")
        (self.dir / "operator").touch()
        return system.HelperResult(True, [{"step": "install", "ok": True, "msg": "ok"}])

    def report(self, stage, value):
        self.stages.append((stage, value))
        if stage == "login":
            (self.dir / "logged-in").touch()  # the person logs in in the browser

    def run_setup(self):
        setup = tailscale.Setup(self.helper, self.report)
        return setup, setup.run()

    def test_not_installed(self):
        os.environ["HOMEMESH_TAILSCALE"] = ""
        self.assertEqual(tailscale.backend_state(), "missing")
        setup, error = self.run_setup()
        self.assertIsNone(error)
        self.assertEqual(self.helper_calls, [["tailscale"]])
        self.assertEqual([stage for stage, _value in self.stages], ["admin", "connect", "login"])
        self.assertEqual(self.stages[-1][1], "https://login.tailscale.com/a/0123abcd")
        self.assertEqual(setup.addresses, [{"addr": "100.101.102.103", "kind": "VPN"}])
        self.assertEqual(tailscale.backend_state(), "Running")
        self.assertIn(f"up USER={os.environ.get('USER') or ''}", (self.dir / "log").read_text())

    def test_service_down(self):
        self.assertEqual(tailscale.backend_state(), "down")
        _setup, error = self.run_setup()
        self.assertIsNone(error)
        self.assertEqual(self.helper_calls, [["tailscale"]])

    def test_already_running_needs_nothing(self):
        self.set_state("Running")
        _setup, error = self.run_setup()
        self.assertIsNone(error)
        self.assertEqual(self.helper_calls, [])
        self.assertEqual(self.stages, [])

    def test_operator_already_set_needs_no_password(self):
        self.set_state("NeedsLogin")
        (self.dir / "operator").touch()
        _setup, error = self.run_setup()
        self.assertIsNone(error)
        self.assertEqual(self.helper_calls, [])

    def test_not_the_operator_yet(self):
        self.set_state("NeedsLogin")
        _setup, error = self.run_setup()
        self.assertIsNone(error)
        self.assertEqual(self.helper_calls, [["tailscale"]])
        self.assertEqual([stage for stage, _value in self.stages], ["connect", "admin", "connect", "login"])

    def test_stopped_turns_on_without_login(self):
        self.set_state("Stopped")
        (self.dir / "operator").touch()
        (self.dir / "logged-in").touch()
        _setup, error = self.run_setup()
        self.assertIsNone(error)
        self.assertEqual(tailscale.backend_state(), "Running")

    def test_helper_failure(self):
        self.helper_ok = False
        setup, error = self.run_setup()
        self.assertEqual(error, "Not everything worked")
        self.assertFalse(setup.helper_result.ok)
        self.assertEqual(setup.helper_result.steps[0]["msg"], "no network")

    def test_cancel_while_waiting_for_login(self):
        self.set_state("NeedsLogin")
        (self.dir / "operator").touch()
        holder = {}

        def report(stage, value):
            if stage == "login":
                threading.Thread(target=holder["setup"].cancel).start()
        setup = tailscale.Setup(self.helper, report)
        holder["setup"] = setup
        self.assertEqual(setup.run(), "Cancelled.")
        self.assertEqual(tailscale.backend_state(), "NeedsLogin")

    def test_approval_page(self):
        self.assertTrue(tailscale.approval_page("https://login.tailscale.com/admin/machines"))
        self.assertFalse(tailscale.approval_page("https://login.tailscale.com/a/0123abcd"))


if __name__ == "__main__":
    unittest.main()
