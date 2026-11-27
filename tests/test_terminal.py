"""Which terminal opens, and how it is told to run ssh."""

import os
import shlex
import subprocess

from support import HomeTestCase

from homemesh import paths, share, terminal


class TerminalTest(HomeTestCase):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        os.environ["PATH"] = str(self.bin)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        for key in ("TERMINAL", "XDG_CURRENT_DESKTOP"):
            os.environ.pop(key, None)

    def install(self, *names):
        for name in names:
            program = self.bin / name
            program.write_text("#!/bin/sh\n")
            program.chmod(0o755)

    def test_nothing_installed(self):
        self.assertIsNone(terminal.find())
        self.assertIsNone(terminal.command_line("beta.local"))

    def test_the_users_choice_comes_first(self):
        self.install("xterm", "konsole", "kgx", "xdg-terminal-exec")
        os.environ["XDG_CURRENT_DESKTOP"] = "GNOME"
        self.assertEqual(terminal.find(), ["xdg-terminal-exec"])
        (self.bin / "xdg-terminal-exec").unlink()
        self.assertEqual(terminal.find(), ["kgx"])
        os.environ["TERMINAL"] = "xterm -fa Mono"
        self.assertEqual(terminal.find(), ["xterm", "-fa", "Mono"])

    def test_plasma_setting(self):
        self.install("konsole", "kitty")
        os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
        config = self.tmp / "config"
        config.mkdir()
        (config / "kdeglobals").write_text("[General]\nTerminalApplication=kitty\n"
                                           "[KDE]\nTerminalApplication=xterm\n")
        self.assertEqual(terminal.find(), ["kitty"])
        (config / "kdeglobals").write_text("[General]\nColorScheme=BreezeDark\n")
        self.assertEqual(terminal.find(), ["konsole"])

    def test_each_terminal_gets_the_command_its_way(self):
        command = ["sh", "-c", "echo 'a b'"]
        self.assertEqual(terminal.wrap(["gnome-terminal"], command), ["gnome-terminal", "--", *command])
        self.assertEqual(terminal.wrap(["konsole"], command), ["konsole", "-e", *command])
        self.assertEqual(terminal.wrap(["xfce4-terminal"], command), ["xfce4-terminal", "-x", *command])
        self.assertEqual(terminal.wrap(["kitty"], command), ["kitty", *command])
        self.assertEqual(terminal.wrap(["/usr/bin/wezterm"], command),
                         ["/usr/bin/wezterm", "start", "--", *command])
        self.assertEqual(terminal.wrap(["tilix"], command), ["tilix", "-e", shlex.join(command)])
        self.assertEqual(terminal.wrap(["unknown-term"], command), ["unknown-term", "-e", *command])
        self.assertEqual(terminal.wrap(["xterm", "-e"], command), ["xterm", "-e", *command])

    def test_ssh_waits_only_when_something_went_wrong(self):
        fake_ssh = self.bin / "ssh"
        fake_ssh.write_text('#!/bin/sh\necho "args: $*"\nexit "$SSH_STATUS"\n')
        fake_ssh.chmod(0o755)
        command = terminal.ssh_command("beta.local")
        self.assertIn(str(paths.ssh_config()), command)
        for status, waits in ((0, False), (1, False), (255, True), (share.REFUSED, True)):
            done = subprocess.run(["/bin/sh", *command[1:]] if command[0] == "sh" else command,
                                  env=dict(os.environ, SSH_STATUS=str(status)), input="\n",
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(done.returncode, status)
            self.assertIn(f"-F {paths.ssh_config()} beta.local", done.stdout)
            self.assertEqual("Press Enter" in done.stdout, waits, status)

    def test_whole_line(self):
        self.install("konsole")
        line = terminal.command_line("beta.local")
        self.assertEqual(line[:3], ["konsole", "-e", "sh"])
        self.assertEqual(line[-2], "beta.local")
        self.assertEqual(line[-3], str(paths.ssh_config()))
