import unittest
import xml.dom.minidom

from support import HomeTestCase, card

from homemesh import paths, render, state

FOREIGN_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ me@laptop"
KDE_FILE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xbel>
<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" xmlns:kdepriv="http://www.kde.org/kdepriv">
 <bookmark href="file:///home/ania">
  <title>Home</title>
  <info>
   <metadata owner="http://www.kde.org">
    <ID>1/0</ID>
   </metadata>
  </info>
 </bookmark>
</xbel>
"""


class RenderTest(HomeTestCase):
    def group(self, *cards):
        with state.transaction() as st:
            for c in cards:
                state.merge(st, [c], {}, via="direct")
            render.apply(st)
        return state.load()

    def test_ssh_files_for_two_peers(self):
        paths.ssh_dir().mkdir()
        paths.authorized_keys().write_text(FOREIGN_KEY + "\n")
        self.group(card("b", "beta", 10), card("c", "gamma", 11))
        keys = paths.authorized_keys().read_text().splitlines()
        self.assertEqual(keys[0], FOREIGN_KEY)
        self.assertEqual(len(keys), 3)
        self.assertTrue(all(line.startswith('from="10.0.0.0/8,') for line in keys[1:]))
        config = paths.ssh_config().read_text()
        self.assertIn("Host beta.local\n    HostName beta.local\n", config)
        self.assertIn(f"HostKeyAlias homemesh-{'b' * 32}", config)
        self.assertIn(f'IdentityFile "{paths.identity()}"', config)
        known = paths.known_hosts().read_text()
        self.assertIn(f"homemesh-{'c' * 32},gamma.local ssh-ed25519 ", known)
        # An existing file keeps its permissions; one we create is private.
        self.assertEqual(oct(paths.authorized_keys().stat().st_mode & 0o777), "0o644")
        self.assertEqual(oct(paths.ssh_config().stat().st_mode & 0o777), "0o600")

    def test_new_authorized_keys_is_private(self):
        self.group(card("b", "beta", 10))
        self.assertEqual(oct(paths.authorized_keys().stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(paths.ssh_dir().stat().st_mode & 0o777), "0o700")

    def test_apply_twice_changes_nothing_and_removal_cleans_up(self):
        paths.ssh_dir().mkdir()
        original_config = "Host server\n    User admin\n"
        paths.user_ssh_config().write_text(original_config)
        paths.authorized_keys().write_text(FOREIGN_KEY + "\n")
        self.group(card("b", "beta", 10))
        config = paths.user_ssh_config().read_text()
        self.assertTrue(config.startswith("# homemesh:"))
        self.assertIn(f'Include "{paths.ssh_config()}"\n\nHost server', config)
        snapshot = {p: p.read_text() for p in (paths.user_ssh_config(), paths.authorized_keys(),
                                                paths.ssh_config(), paths.gtk_bookmarks())}
        with state.transaction() as st:
            render.apply(st)
        for path, text in snapshot.items():
            self.assertEqual(path.read_text(), text, path)
        with state.transaction() as st:
            state.remove_peer(st, "b" * 32)
            render.apply(st)
        self.assertEqual(paths.user_ssh_config().read_text(), original_config)
        self.assertEqual(paths.authorized_keys().read_text(), FOREIGN_KEY + "\n")
        self.assertFalse(paths.autostart_file().exists())

    def test_nothing_written_into_ssh_without_peers(self):
        with state.transaction() as st:
            render.apply(st)
        self.assertFalse(paths.authorized_keys().exists())
        self.assertFalse(paths.user_ssh_config().exists())

    def test_gtk_bookmarks(self):
        paths.gtk_bookmarks().parent.mkdir(parents=True)
        paths.gtk_bookmarks().write_text("file:///home/ania/Muzyka\n")
        self.group(card("b", "beta", 10))
        lines = paths.gtk_bookmarks().read_text().splitlines()
        self.assertEqual(lines, ["file:///home/ania/Muzyka", "sftp://beta.local/home/ania beta"])
        self.assertTrue(paths.autostart_file().exists())
        # The person deletes our bookmark: it stays deleted.
        paths.gtk_bookmarks().write_text("file:///home/ania/Muzyka\n")
        with state.transaction() as st:
            render.apply(st)
        self.assertEqual(paths.gtk_bookmarks().read_text(), "file:///home/ania/Muzyka\n")
        # A rename gives a new address, which is added; the old one goes.
        self.group(card("b", "beta-nowy", 20))
        self.assertEqual(paths.gtk_bookmarks().read_text().splitlines()[-1],
                         "sftp://beta-nowy.local/home/ania beta-nowy")

    def test_kde_places(self):
        paths.kde_places().parent.mkdir(parents=True)
        paths.kde_places().write_text(KDE_FILE)
        self.group(card("b", "beta", 10))
        text = paths.kde_places().read_text()
        xml.dom.minidom.parseString(text.encode())
        self.assertIn('<bookmark href="sftp://beta.local/home/ania">', text)
        self.assertIn(f"<ID>homemesh-{'b' * 32}</ID>", text)
        with state.transaction() as st:
            state.remove_peer(st, "b" * 32)
            render.apply(st)
        self.assertEqual(paths.kde_places().read_text(), KDE_FILE)

    def mark(self, marks):
        with state.transaction() as st:
            render.bookmarks(st, marks)

    def test_marks_in_gtk_bookmarks(self):
        paths.gtk_bookmarks().parent.mkdir(parents=True)
        paths.gtk_bookmarks().write_text("file:///home/ania/Muzyka (off) Muzyka\n")
        self.group(card("b", "beta", 10), card("c", "gamma", 11))

        def lines():
            return paths.gtk_bookmarks().read_text().splitlines()
        self.mark({"b" * 32: True, "c" * 32: False})
        marked = ["file:///home/ania/Muzyka (off) Muzyka", "sftp://beta.local/home/ania (off) beta",
                  "sftp://gamma.local/home/ania gamma"]
        self.assertEqual(lines(), marked)
        self.mark({"b" * 32: True})  # twice is once; unknown stays as it was
        self.assertEqual(lines(), marked)
        with state.transaction() as st:  # so do other changes of the group
            render.apply(st)
        self.assertEqual(lines(), marked)
        # A name the person gave stays, with and without the mark.
        paths.gtk_bookmarks().write_text(paths.gtk_bookmarks().read_text().replace("(off) beta", "Beta mamy"))
        self.mark({"b" * 32: True})
        self.assertEqual(lines()[1], "sftp://beta.local/home/ania (off) Beta mamy")
        self.mark({"b" * 32: False})
        self.assertEqual(lines()[1], "sftp://beta.local/home/ania Beta mamy")
        # Nobody to take the marks off: no marks.
        self.mark({"b" * 32: True, "c" * 32: True})
        with state.transaction() as st:
            st["login"] = False
            render.apply(st)
        self.assertEqual(lines()[1:], ["sftp://beta.local/home/ania Beta mamy",
                                       "sftp://gamma.local/home/ania gamma"])

    def test_marks_in_kde_places(self):
        paths.kde_places().parent.mkdir(parents=True)
        paths.kde_places().write_text(KDE_FILE)
        self.group(card("b", "beta", 10))
        plain = paths.kde_places().read_text()
        self.assertIn("<title>beta</title>", plain)
        self.mark({"b" * 32: True})
        self.assertEqual(paths.kde_places().read_text(), plain.replace(">beta<", ">(off) beta<"))
        own = paths.kde_places().read_text().replace("(off) beta", "(off) Mama &amp; tata")
        paths.kde_places().write_text(own)
        self.mark({"b" * 32: True})
        self.assertEqual(paths.kde_places().read_text(), own)
        self.mark({"b" * 32: False})
        self.assertEqual(paths.kde_places().read_text(), plain.replace(">beta<", ">Mama &amp; tata<"))
        self.assertIn("<title>Home</title>", paths.kde_places().read_text())

    def test_two_peers_with_one_name(self):
        st = self.group(card("b", "beta", 10), card("c", "beta", 11))
        names = render.aliases(st)
        self.assertEqual(names["c" * 32], "beta.local")  # the newer one
        self.assertEqual(names["b" * 32], "beta-bbbbbb.local")


if __name__ == "__main__":
    unittest.main()
