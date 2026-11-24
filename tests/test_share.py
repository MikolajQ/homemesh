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

    def test_keys_are_restricted_only_in_folder_mode(self):
        with state.transaction() as st:
            state.merge(st, [card("b", "beta", 10)], {}, via="direct")
            render.apply(st)
        line = paths.authorized_keys().read_text()
        self.assertNotIn("restrict", line)
        with state.transaction() as st:
            st["share"] = {"mode": "folders", "folders": []}
            render.apply(st)
        line = paths.authorized_keys().read_text()
        self.assertIn(',restrict,command="', line)
        self.assertIn(" gate\" ssh-ed25519 ", line)

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
        env = dict(os.environ, PYTHONPATH=str(ROOT), SSH_ORIGINAL_COMMAND="rm -rf ~")
        done = subprocess.run([sys.executable, "-m", "homemesh", "gate"], env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 1)
        self.assertIn("SFTP", done.stderr)
        env["SSH_ORIGINAL_COMMAND"] = "homemesh sync-serve"
        done = subprocess.run([sys.executable, "-m", "homemesh", "gate"], env=env, input="{}",
                              capture_output=True, text=True, timeout=30)
        self.assertIn("HOMEMESH-SYNC ", done.stdout)


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
