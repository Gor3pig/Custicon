import unittest

from custicon.iconsets import IconSets
from custicon.suggestions import emoji_matches, folder_words, phosphor_matches, round_robin, suggest

SETS = IconSets()


class WordTests(unittest.TestCase):
    def test_a_name_is_cut_into_plain_words(self):
        self.assertEqual(folder_words("Mes Photos de vacances"), ["mes", "photos", "vacances"])
        self.assertEqual(folder_words("Musique_2024-été"), ["musique", "ete"])

    def test_accents_and_case_are_removed(self):
        self.assertEqual(folder_words("ÉCOLE"), ["ecole"])

    def test_short_words_and_numbers_are_dropped(self):
        self.assertEqual(folder_words("Un de la 2024 ab"), [])

    def test_a_camel_case_name_is_cut(self):
        self.assertEqual(folder_words("MyPhotoAlbum"), ["photo", "album"])
        self.assertEqual(folder_words("fooBarBaz"), ["foo", "bar", "baz"])

    def test_the_same_word_is_kept_once(self):
        self.assertEqual(folder_words("photos Photos PHOTOS"), ["photos"])

    def test_nothing_gives_no_word(self):
        self.assertEqual(folder_words(""), [])
        self.assertEqual(folder_words("---"), [])


class BuildingBlockTests(unittest.TestCase):
    def test_the_lists_are_merged_one_item_at_a_time_without_duplicates(self):
        self.assertEqual(round_robin([["a", "b"], ["a", "c"]]), ["a", "b", "c"])
        self.assertEqual(round_robin([["a", "b", "c"], ["x"]]), ["a", "x", "b", "c"])
        self.assertEqual(round_robin([[], []]), [])
        self.assertEqual(round_robin([]), [])

    def test_a_word_brings_at_most_three_phosphor_pictograms(self):
        found = phosphor_matches(SETS, "vacances")
        self.assertEqual(len(found), 3)
        self.assertEqual(found, phosphor_matches(SETS, "vacances"))

    def test_emoji_are_not_suggested_for_a_part_of_a_tag(self):
        self.assertTrue(SETS["emoji"].symbolic_names("abai", limit=5, max_rank=4))
        self.assertEqual(emoji_matches(SETS, "abai"), [])

    def test_two_words_giving_the_same_pictogram_do_not_crowd_out_others(self):
        names = suggest("camera photo", SETS, 8)
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len([name for name in names if name.startswith("phosphor:")]), 3)


class SuggestionTests(unittest.TestCase):
    def names(self, folder, limit=8):
        return suggest(folder, SETS, limit)

    def test_a_french_folder_name_suggests_matching_pictograms(self):
        self.assertIn("phosphor:camera", self.names("Photos"))
        self.assertIn("phosphor:music-notes", self.names("Musique"))
        self.assertIn("phosphor:suitcase", self.names("Vacances"))

    def test_an_english_folder_name_works_too(self):
        self.assertIn("phosphor:camera", self.names("Photos backup"))
        self.assertIn("phosphor:rocket", self.names("rocket"))

    def test_a_plural_finds_the_singular(self):
        self.assertIn("phosphor:car", self.names("Voitures"))
        self.assertIn("phosphor:camera", self.names("Photos"))

    def test_every_source_keeps_a_place_among_many_matches(self):
        names = self.names("Photos Musique Voyage Docker")
        self.assertIn("brand:docker", names)
        self.assertTrue(any(name.startswith("phosphor:") for name in names))

    def test_a_plural_ending_in_x_finds_the_singular(self):
        self.assertIn("phosphor:game-controller", self.names("Jeux"))

    def test_a_brand_is_suggested_only_by_its_exact_name(self):
        self.assertIn("brand:docker", self.names("Docker"))
        self.assertNotIn("brand:docker", self.names("Dock"))

    def test_emoji_can_be_suggested(self):
        self.assertTrue(any(name.startswith("emoji:") for name in self.names("chat")), self.names("chat"))

    def test_the_best_matches_of_each_source_come_in_a_fixed_order(self):
        names = self.names("Photos")
        self.assertTrue(names[0].startswith("phosphor:"))
        self.assertEqual(names, self.names("Photos"))

    def test_the_result_is_limited_and_has_no_duplicate(self):
        for limit in (1, 3, 8):
            names = self.names("Photos de voyage en voiture", limit)
            self.assertLessEqual(len(names), limit)
            self.assertEqual(len(names), len(set(names)))

    def test_a_name_without_meaning_suggests_nothing(self):
        self.assertEqual(self.names("xqzvw"), [])
        self.assertEqual(self.names(""), [])
        self.assertEqual(self.names("12 34"), [])

    def test_every_suggestion_exists(self):
        for folder in ("Photos", "Musique", "Docker", "Voyage", "chat", "Projets code"):
            for name in self.names(folder):
                self.assertIsNotNone(SETS.for_name(name), name)
                SETS.for_name(name).file(name)

    def test_several_words_all_contribute(self):
        names = self.names("Photos vacances", 8)
        self.assertIn("phosphor:camera", names)
        self.assertIn("phosphor:suitcase", names)

    def test_the_first_words_come_before_the_later_ones(self):
        names = self.names("Musique Photos", 2)
        self.assertEqual(names[0], "phosphor:music-notes")


if __name__ == "__main__":
    unittest.main()
