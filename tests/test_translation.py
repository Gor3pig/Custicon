import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from .support import WorkspaceTestCase

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("msgfmt"), "msgfmt is needed to compile the translation")
class FrenchTranslationTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        target = self.root / "locale" / "fr" / "LC_MESSAGES"
        target.mkdir(parents=True)
        subprocess.run(["msgfmt", "--output-file", str(target / "custicon.mo"), str(ROOT / "po" / "fr.po")], check=True)

    def translate(self, language: str, expression: str) -> str:
        environment = dict(os.environ, LANGUAGE=language, CUSTICON_LOCALEDIR=str(self.root / "locale"))
        code = f"from custicon.i18n import _, ngettext\nprint({expression})"
        result = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, env=environment, check=True, cwd=ROOT / "src")
        return result.stdout.strip()

    def test_the_interface_is_translated_into_french(self):
        self.assertEqual(self.translate("fr", '_("Apply")'), "Appliquer")

    def test_plural_forms_follow_the_french_rules(self):
        self.assertEqual(self.translate("fr", 'ngettext("%d icon updated", "%d icons updated", 1) % 1'), "1 icône mise à jour")
        self.assertEqual(self.translate("fr", 'ngettext("%d icon updated", "%d icons updated", 3) % 3'), "3 icônes mises à jour")

    def test_without_a_translation_the_source_text_is_used(self):
        self.assertEqual(self.translate("de", '_("Apply")'), "Apply")


if __name__ == "__main__":
    unittest.main()
