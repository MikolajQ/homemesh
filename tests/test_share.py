"""Sharing only chosen folders: the sandbox, the keys, and the gate end to end."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from support import ROOT, HomeTestCase, card

from homemesh import paths, render, share, state


class FolderModeTest(HomeTestCase):
    def test_mount_points_sit_inside_home_by_name(self):
        home = self.home
        for name in ("Publiczny", "inne/Publiczny", "Muzyka"):
            (self.tmp / name).mkdir(parents=True)
        folders = [{"path": str(self.tmp / "Publiczny"), "readonly": False},
                   {"path": str(self.tmp / "inne/Publiczny"), "readonly": True},
                   {"path": str(self.tmp / "nie-ma"), "readonly": False},
                   {"path": str(home), "readonly": True}]
        points = share.mount_points(folders, home)
        self.assertEqual([(str(t), ro) for _s, t, ro in points],
                         [(str(home), True), (str(home / "Publiczny"), False),
                          (str(home / "Publiczny (2)"), True)])

    def test_keys_by_mode(self):
        """Never forwarding; commands only with the whole home folder and the terminal."""
        with state.transaction() as st:
            state.merge(st, [card("b", "beta", 10)], {}, via="direct")
            render.apply(st)
        line = paths.authorized_keys().read_text()
        self.assertIn('",restrict,pty ssh-ed25519 ', line)
        self.assertNotIn("command=", line)
        for new in ({"mode": "home", "folders": [], "shell": False},
                    {"mode": "folders", "folders": [], "shell": True}):
            with state.transaction() as st:
                st["share"] = new
                render.apply(st)
            line = paths.authorized_keys().read_text()
            self.assertIn(',restrict,command="', line, new)
            self.assertIn(" gate\" ssh-ed25519 ", line, new)
            self.assertNotIn("pty", line, new)

    def test_terminal_is_on_by_default_and_only_with_home(self):
        self.assertEqual(share.current(state.new_state())["shell"], True)
        self.assertEqual(share.current({"share": {"mode": "home", "folders": []}})["shell"], True)
        old = {"share": {"mode": "home", "folders": [], "shell": False}}
        self.assertFalse(share.allows_commands(share.current(old)))
        folders = {"share": {"mode": "folders", "folders": [], "shell": True}}
        self.assertFalse(share.allows_commands(share.current(folders)))
        self.assertTrue(share.allows_commands(share.current(state.new_state())))

    def test_the_card_says_whether_the_terminal_is_allowed(self):
        with state.transaction() as st:
            first = state.own_card(st)
            self.assertIs(first["shell"], True)
            st["share"] = {"mode": "home", "folders": [], "shell": False}
            second = state.own_card(st)
        self.assertIs(second["shell"], False)
        self.assertGreater(second["updated"], first["updated"])  # so that it spreads
        self.assertIs(state.validate_card(dict(card("b", "beta", 10), shell=True))["shell"], True)
        self.assertNotIn("shell", state.validate_card(card("b", "beta", 10)))
        self.assertNotIn("shell", state.validate_card(dict(card("b", "beta", 10), shell="yes")))

    def test_login_setting_controls_autostart(self):
        with state.transaction() as st:
            state.merge(st, [card("b", "beta", 10)], {}, via="direct")
            render.apply(st)
        self.assertTrue(paths.autostart_file().exists())
        with state.transaction() as st:
            st["login"] = False
            render.apply(st)
        self.assertFalse(paths.autostart_file().exists())

    def test_gate_refuses_commands_and_passes_sync(self):
        for mode, said in (("folders", "SFTP"), ("home", "terminal is off")):
            with state.transaction() as st:
                st["share"] = {"mode": mode, "folders": [], "shell": False}
            env = dict(os.environ, PYTHONPATH=str(ROOT), SSH_ORIGINAL_COMMAND="rm -rf ~")
            done = subprocess.run([sys.executable, "-m", "homemesh", "gate"], env=env,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, share.REFUSED, mode)
            self.assertIn(said, done.stderr, mode)
            env["SSH_ORIGINAL_COMMAND"] = "homemesh sync-serve"
            done = subprocess.run([sys.executable, "-m", "homemesh", "gate"], env=env, input="{}",
                                  capture_output=True, text=True, timeout=30)
            self.assertIn("HOMEMESH-SYNC ", done.stdout, mode)


@unittest.skipUnless(shutil.which("bwrap") and shutil.which("sftp") and share.sftp_server(),
                     "needs bubblewrap and OpenSSH")
class SandboxTest(HomeTestCase):
    """A real SFTP session through the gate (sftp -D talks to it directly)."""

    def test_only_chosen_folders_are_there(self):
        home = self.home
        (home / "Publiczny").mkdir()
        (home / "Muzyka").mkdir()
        (home / "Muzyka" / "piosenka.txt").write_text("la la\n")
        (home / ".ssh").mkdir()
        (home / ".ssh" / "id_ed25519").write_text("TAJNE\n")
        with state.transaction() as st:
            st["share"] = {"mode": "folders", "folders": [
                {"path": str(home / "Publiczny"), "readonly": False},
                {"path": str(home / "Muzyka"), "readonly": True}]}
        upload = self.tmp / "nowy.txt"
        upload.write_text("nowy\n")
        download = self.tmp / "pobrane"
        download.mkdir()
        batch = self.tmp / "batch"
        batch.write_text("\n".join([
            f"ls -1a {home}",
            f"put {upload} {home}/Publiczny/nowy.txt",
            f"-put {upload} {home}/Muzyka/nowy.txt",
            f"get {home}/Muzyka/piosenka.txt {download}/",
            f"-get {home}/.ssh/id_ed25519 {download}/",
            "-ls -1 /etc",
            f"get /etc/passwd {download}/",
            ""]))
        env = dict(os.environ, PYTHONPATH=str(ROOT), SSH_ORIGINAL_COMMAND="/usr/lib/ssh/sftp-server")
        done = subprocess.run(["sftp", "-b", str(batch), "-D", f"{sys.executable} -m homemesh gate"],
                              env=env, capture_output=True, text=True, timeout=60)
        lines = [line.strip() for line in done.stdout.splitlines() if not line.startswith("sftp>")]
        if not lines and "bwrap" in done.stderr:
            self.skipTest(f"bubblewrap cannot run here: {done.stderr.strip()[:200]}")
        report = done.stdout + done.stderr
        in_home = sorted(line[len(str(home)) + 1:] for line in lines if line.startswith(f"{home}/"))
        self.assertEqual([name for name in in_home if name not in (".", "..")],
                         ["Muzyka", "Publiczny"], report)
        self.assertEqual((home / "Publiczny" / "nowy.txt").read_text(), "nowy\n")
        self.assertFalse((home / "Muzyka" / "nowy.txt").exists())
        self.assertEqual((download / "piosenka.txt").read_text(), "la la\n")
        self.assertFalse((download / "id_ed25519").exists())
        in_etc = sorted(line for line in lines if line.startswith("/etc/"))
        self.assertTrue(set(in_etc) <= {"/etc/group", "/etc/localtime", "/etc/passwd"}, in_etc)
        self.assertEqual(len((download / "passwd").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
