import subprocess
import unittest
from pathlib import Path

from custicon.iconsets import PhosphorSet
from custicon.keywords import KEYWORDS, MINIMUM_LENGTH, PHOSPHOR_KEYWORDS, english_fragments
from custicon.textutil import normalize_text

ADWAITA = Path("/usr/share/icons/Adwaita/symbolic")


def installed_names() -> set[str]:
    if not ADWAITA.is_dir():
        return set()
    return {path.name[: -len("-symbolic.svg")] for path in ADWAITA.glob("*/*-symbolic.svg")}


class KeywordTableTests(unittest.TestCase):
    def test_every_key_is_plain_lowercase_text(self):
        for key in KEYWORDS:
            self.assertEqual(key, normalize_text(key), key)
            self.assertGreaterEqual(len(key), MINIMUM_LENGTH, key)

    def test_every_key_has_fragments(self):
        for key, fragments in KEYWORDS.items():
            self.assertTrue(fragments, key)
            self.assertEqual(len(set(fragments)), len(fragments), key)
            for fragment in fragments:
                self.assertEqual(fragment, fragment.lower(), key)

    def test_every_fragment_matches_a_pictogram_of_the_default_theme(self):
        names = installed_names()
        if not names:
            self.skipTest("the Adwaita icon theme is not installed")
        missing = [(key, fragment) for key, fragments in KEYWORDS.items() for fragment in fragments if not any(fragment in name for name in names)]
        self.assertEqual(missing, [])


class FragmentTests(unittest.TestCase):
    def test_a_french_word_gives_english_name_fragments(self):
        self.assertEqual(english_fragments("musique"), ["audio", "folder-music", "multimedia"])

    def test_a_prefix_is_enough(self):
        self.assertIn("folder-music", english_fragments("mus"))
        self.assertIn("starred", english_fragments("eto"))

    def test_accents_and_case_are_ignored(self):
        self.assertEqual(english_fragments("ÉTOILE"), english_fragments("etoile"))
        self.assertIn("starred", english_fragments("Étoile"))

    def test_surrounding_spaces_are_ignored(self):
        self.assertEqual(english_fragments("  maison "), english_fragments("maison"))

    def test_only_the_beginning_of_a_word_matches(self):
        self.assertEqual(english_fragments("sique"), [])
        self.assertEqual(english_fragments("ique"), [])

    def test_several_words_match_as_a_phrase(self):
        self.assertEqual(english_fragments("mot de pa"), ["dialog-password"])

    def test_too_short_or_unknown_queries_give_nothing(self):
        self.assertEqual(english_fragments(""), [])
        self.assertEqual(english_fragments("m"), [])
        self.assertEqual(english_fragments("zzzz"), [])
        self.assertEqual(english_fragments("camera-photo"), [])

    def test_words_sharing_a_fragment_do_not_repeat_it(self):
        self.assertEqual(english_fragments("telecharge"), ["folder-download"])

    def test_fragments_of_several_words_are_merged_without_duplicates(self):
        fragments = english_fragments("ma")
        self.assertEqual(len(set(fragments)), len(fragments))
        self.assertIn("user-home", fragments)
        self.assertIn("mail", fragments)


class TextNormalizationTests(unittest.TestCase):
    def test_accents_and_case_are_ignored(self):
        self.assertEqual(normalize_text("Éléphant"), "elephant")
        self.assertEqual(normalize_text("Straße"), "strasse")

    def test_plain_text_is_only_lowercased(self):
        self.assertEqual(normalize_text("Projects/2024"), "projects/2024")


if __name__ == "__main__":
    unittest.main()


class PhosphorKeywordTests(unittest.TestCase):
    def test_every_key_is_plain_lowercase_text_with_fragments(self):
        for key, fragments in PHOSPHOR_KEYWORDS.items():
            self.assertEqual(key, normalize_text(key), key)
            self.assertGreaterEqual(len(key), MINIMUM_LENGTH, key)
            self.assertTrue(fragments, key)
            self.assertEqual(len(set(fragments)), len(fragments), key)

    def test_every_fragment_matches_a_phosphor_pictogram(self):
        names = PhosphorSet().all_names()
        missing = [(key, fragment) for key, fragments in PHOSPHOR_KEYWORDS.items() for fragment in fragments if not any(fragment in name for name in names)]
        self.assertEqual(missing, [])

    def test_the_phosphor_table_is_used_when_given(self):
        self.assertEqual(english_fragments("maison", PHOSPHOR_KEYWORDS), ["house", "garage", "couch"])
        self.assertNotEqual(english_fragments("maison", PHOSPHOR_KEYWORDS), english_fragments("maison"))

    def test_the_default_table_is_unchanged(self):
        self.assertEqual(english_fragments("musique"), ["audio", "folder-music", "multimedia"])
