import unittest
from pathlib import Path

from custicon import aboutinfo
from custicon.config import VERSION

from .support import WorkspaceTestCase

METAINFO = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<component type="desktop-application">'
    '<releases>'
    '<release version="1.3.0" date="2026-11-14">'
    '<description>'
    '<p>Intro.</p>'
    '<p xml:lang="fr">Intro en français.</p>'
    '<ul>'
    '<li>First &amp; best</li>'
    '<li xml:lang="fr">Premier et meilleur</li>'
    '<li>Second</li>'
    '</ul>'
    '</description>'
    '</release>'
    '<release version="1.2.0" date="2026-11-13">'
    '<description>'
    '<p>Older.</p>'
    '</description>'
    '</release>'
    '</releases>'
    '</component>'
)


class ReleaseNotesTests(unittest.TestCase):
    def test_english_notes_use_the_original_texts(self):
        self.assertEqual(
            aboutinfo.release_notes(METAINFO, "1.3.0", False),
            "<p>Intro.</p><ul><li>First &amp; best</li><li>Second</li></ul>",
        )

    def test_french_notes_use_the_translation_when_there_is_one(self):
        self.assertEqual(
            aboutinfo.release_notes(METAINFO, "1.3.0", True),
            "<p>Intro en français.</p><ul><li>Premier et meilleur</li><li>Second</li></ul>",
        )

    def test_only_the_requested_release_is_used(self):
        self.assertEqual(aboutinfo.release_notes(METAINFO, "1.2.0", True), "<p>Older.</p>")

    def test_a_translation_into_another_language_is_not_used_as_french(self):
        text = (
            '<component><releases><release version="1.0.0"><description>'
            '<p>Original.</p><p xml:lang="de">Deutsch.</p>'
            '<ul><li>One</li><li xml:lang="de">Eins</li></ul></description></release></releases></component>'
        )
        self.assertEqual(aboutinfo.release_notes(text, "1.0.0", True), "<p>Original.</p><ul><li>One</li></ul>")

    def test_elements_that_are_not_notes_are_ignored(self):
        text = (
            '<component><releases><release version="1.0.0"><description>'
            '<foo>Stray</foo><p>Kept.</p></description></release></releases></component>'
        )
        self.assertEqual(aboutinfo.release_notes(text, "1.0.0", False), "<p>Kept.</p>")

    def test_an_unknown_release_has_no_notes(self):
        self.assertIsNone(aboutinfo.release_notes(METAINFO, "9.9.9", False))

    def test_a_broken_document_has_no_notes(self):
        self.assertIsNone(aboutinfo.release_notes("<component><release", "1.3.0", False))
        self.assertIsNone(aboutinfo.release_notes("", "1.3.0", False))

    def test_a_release_without_description_has_no_notes(self):
        text = '<component><releases><release version="1.0.0"/></releases></component>'
        self.assertIsNone(aboutinfo.release_notes(text, "1.0.0", False))

    def test_markup_in_the_notes_is_escaped(self):
        text = '<component><releases><release version="1.0.0"><description><p>a &lt;b&gt; "c"</p></description></release></releases></component>'
        self.assertEqual(aboutinfo.release_notes(text, "1.0.0", False), "<p>a &lt;b&gt; \"c\"</p>")

    def test_inline_elements_keep_their_text(self):
        text = '<component><releases><release version="1.0.0"><description><p>Use <code>x</code> now</p></description></release></releases></component>'
        self.assertEqual(aboutinfo.release_notes(text, "1.0.0", False), "<p>Use x now</p>")


class LanguageTests(unittest.TestCase):
    def test_a_french_desktop_is_french(self):
        self.assertTrue(aboutinfo.is_french(["fr_FR.UTF-8", "fr_FR", "fr", "C"]))

    def test_english_first_is_not_french(self):
        self.assertFalse(aboutinfo.is_french(["en_US", "en", "fr", "C"]))

    def test_no_language_is_not_french(self):
        self.assertFalse(aboutinfo.is_french([]))


class HelpUrlTests(unittest.TestCase):
    BASE = "https://github.com/Gor3pig/Custicon/blob/main/help"

    def test_a_french_desktop_opens_the_french_guide(self):
        self.assertEqual(aboutinfo.help_url(["fr_FR.UTF-8", "fr_FR", "fr", "C"]), f"{self.BASE}/fr/README.md")

    def test_english_comes_first_when_the_user_prefers_it(self):
        self.assertEqual(aboutinfo.help_url(["en_US", "en", "fr", "C"]), f"{self.BASE}/en/README.md")

    def test_a_language_without_a_guide_falls_back_to_english(self):
        self.assertEqual(aboutinfo.help_url(["de_DE.UTF-8", "de", "C"]), f"{self.BASE}/en/README.md")

    def test_a_language_without_a_guide_lets_a_later_translated_one_win(self):
        self.assertEqual(aboutinfo.help_url(["br_FR", "br", "fr_FR", "fr", "C"]), f"{self.BASE}/fr/README.md")

    def test_no_language_gives_the_english_guide(self):
        self.assertEqual(aboutinfo.help_url([]), f"{self.BASE}/en/README.md")
        self.assertEqual(aboutinfo.help_url(["C"]), f"{self.BASE}/en/README.md")

    def test_a_modifier_or_encoding_is_ignored(self):
        for language in ("fr_CA.UTF-8@euro", "fr.UTF-8", "fr@euro", "fr_CA"):
            self.assertEqual(aboutinfo.help_url([language]), f"{self.BASE}/fr/README.md", language)


class WebsiteUrlTests(unittest.TestCase):
    SITE = "https://gor3pig.github.io/Custicon/"

    def test_a_french_desktop_opens_the_french_page(self):
        self.assertEqual(aboutinfo.website_url(["fr_FR.UTF-8", "fr_FR", "fr", "C"]), f"{self.SITE}fr/")

    def test_english_comes_first_when_the_user_prefers_it(self):
        self.assertEqual(aboutinfo.website_url(["en_US", "en", "fr", "C"]), self.SITE)

    def test_a_language_without_a_page_falls_back_to_english(self):
        self.assertEqual(aboutinfo.website_url(["de_DE.UTF-8", "de", "C"]), self.SITE)

    def test_a_language_without_a_page_lets_a_later_translated_one_win(self):
        self.assertEqual(aboutinfo.website_url(["br_FR", "br", "fr_FR", "fr", "C"]), f"{self.SITE}fr/")

    def test_no_language_gives_the_english_page(self):
        self.assertEqual(aboutinfo.website_url([]), self.SITE)

    def test_the_metainfo_homepage_is_the_english_page(self):
        text = aboutinfo.DEVELOPMENT_METAINFO.read_text(encoding="utf-8")
        self.assertIn(f'<url type="homepage">{self.SITE}</url>', text)


class FindingTheMetainfoTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def install(self, name="share"):
        folder = self.root / name / "metainfo"
        folder.mkdir(parents=True)
        (folder / aboutinfo.METAINFO_NAME).write_text(METAINFO, encoding="utf-8")
        return str(self.root / name)

    def test_the_installed_file_is_found_in_the_data_directories(self):
        first, second = str(self.root / "empty"), self.install()
        found = aboutinfo.find_metainfo([first, second])
        self.assertEqual(found, Path(second) / "metainfo" / aboutinfo.METAINFO_NAME)

    def test_the_first_data_directory_wins(self):
        first, second = self.install("a"), self.install("b")
        self.assertEqual(aboutinfo.find_metainfo([first, second]), Path(first) / "metainfo" / aboutinfo.METAINFO_NAME)

    def test_the_development_file_is_used_when_nothing_is_installed(self):
        found = aboutinfo.find_metainfo([str(self.root / "nothing")])
        self.assertEqual(found, aboutinfo.DEVELOPMENT_METAINFO)
        self.assertTrue(found.is_file())

    def test_the_notes_of_the_current_version_are_read_from_the_file(self):
        notes = aboutinfo.current_release_notes("1.3.0", ["fr"], [self.install()])
        self.assertEqual(notes, "<p>Intro en français.</p><ul><li>Premier et meilleur</li><li>Second</li></ul>")

    def test_an_unreadable_file_gives_no_notes(self):
        folder = self.root / "bad" / "metainfo"
        folder.mkdir(parents=True)
        (folder / aboutinfo.METAINFO_NAME).write_bytes(b"\xff\xfe\x00")
        self.assertIsNone(aboutinfo.current_release_notes("1.3.0", ["en"], [str(self.root / "bad")]))


class ShippedMetainfoTests(unittest.TestCase):
    def test_the_shipped_file_describes_the_current_version(self):
        notes = aboutinfo.release_notes(aboutinfo.DEVELOPMENT_METAINFO.read_text(encoding="utf-8"), VERSION, False)
        self.assertTrue(notes and notes.startswith("<p>"))
        self.assertIn("<li>", notes)

    def test_the_shipped_file_declares_the_help_and_bug_tracker_links(self):
        text = aboutinfo.DEVELOPMENT_METAINFO.read_text(encoding="utf-8")
        self.assertIn('<url type="bugtracker">', text)
        self.assertIn('<url type="help">', text)


if __name__ == "__main__":
    unittest.main()
