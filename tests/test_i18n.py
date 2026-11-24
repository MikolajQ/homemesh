"""Translations: complete, with the same {fields}, and the template up to date."""

import gettext
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

PO_DIR = ROOT / "po"
FIELD_RE = re.compile(r"\{\w*\}")


def entries(path):
    """{msgid: msgstr} of a .po/.pot file, without the header."""
    found, msgid, msgstr, current = {}, None, None, None
    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("msgid "):
            if msgid:
                found[msgid] = msgstr
            msgid, msgstr, current = eval(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, current = eval(line[7:]), "str"
        elif line.startswith('"') and current == "id":
            msgid += eval(line)
        elif line.startswith('"') and current == "str":
            msgstr += eval(line)
        elif not line.strip():
            current = None
    if msgid:
        found[msgid] = msgstr
    return found


class TranslationTest(unittest.TestCase):
    def test_every_language_is_complete_with_the_same_fields(self):
        template = entries(PO_DIR / "homemesh.pot")
        self.assertGreater(len(template), 200)
        for po in PO_DIR.glob("*.po"):
            translated = entries(po)
            for msgid in template:
                with self.subTest(language=po.stem, msgid=msgid[:50]):
                    self.assertTrue(translated.get(msgid), "no translation")
                    self.assertEqual(sorted(FIELD_RE.findall(msgid)),
                                     sorted(FIELD_RE.findall(translated[msgid])))
                    self.assertEqual(msgid.endswith("\n"), translated[msgid].endswith("\n"))

    @unittest.skipUnless(shutil.which("xgettext"), "needs gettext")
    def test_template_is_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh.pot"
            subprocess.run(["xgettext", "--from-code=UTF-8", "--language=Python", "--keyword=_",
                            "--keyword=N_", "-o", str(fresh),
                            *sorted(str(p) for p in (ROOT / "homemesh").glob("*.py")),
                            str(ROOT / "helper" / "homemesh-helper")], check=True)
            self.assertEqual(set(entries(fresh)), set(entries(PO_DIR / "homemesh.pot")),
                             "run: make pot")

    @unittest.skipUnless(shutil.which("msgfmt"), "needs gettext")
    def test_polish_catalogue_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "pl" / "LC_MESSAGES"
            target.mkdir(parents=True)
            subprocess.run(["msgfmt", "--check", "-o", str(target / "homemesh.mo"),
                            str(PO_DIR / "pl.po")], check=True)
            polish = gettext.translation("homemesh", localedir=tmp, languages=["pl"])
            self.assertEqual(polish.gettext("Turn sharing off?"), "Wyłączyć udostępnianie?")
            self.assertEqual(polish.gettext("Code:  {code}".join(["  ", ""])), "  Kod:  {code}")


if __name__ == "__main__":
    unittest.main()
