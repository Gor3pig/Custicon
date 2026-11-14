import re
import unittest
from pathlib import Path

from custicon.compose import parse_svg, recolor, serialize
from custicon.errors import IconNotFoundError
from custicon import iconsets
from custicon.iconsets import IconSet, IconSets, PhosphorSet, current_language, default_sets, display_name
from custicon.render import check_svg_bytes, pixel, surface_from_svg

SETS = IconSets()
NAME = re.compile(r"^[a-z0-9_-]+$")


class RegistryTests(unittest.TestCase):
    def test_the_three_sets_have_their_own_prefix(self):
        self.assertEqual(list(SETS), ["phosphor", "emoji", "brands"])
        self.assertEqual([SETS[key].prefix for key in SETS], ["phosphor:", "emoji:", "brand:"])

    def test_a_name_is_routed_by_its_prefix(self):
        self.assertIs(SETS.for_name("phosphor:house"), SETS["phosphor"])
        self.assertIs(SETS.for_name("emoji:cat-face"), SETS["emoji"])
        self.assertIs(SETS.for_name("brand:github"), SETS["brands"])

    def test_a_theme_name_or_nothing_belongs_to_no_set(self):
        for name in ("camera-symbolic", "", None, "unknown:thing", "Emoji:cat", "emoji"):
            self.assertIsNone(SETS.for_name(name), name)

    def test_the_default_registry_is_shared(self):
        self.assertIs(default_sets(), default_sets())

    def test_phosphor_keeps_its_own_class_for_the_picker(self):
        self.assertIsInstance(SETS["phosphor"], PhosphorSet)
        self.assertIsInstance(SETS["emoji"], IconSet)

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(KeyError):
            SETS["nothing"]


class EachSetTests(unittest.TestCase):
    def test_the_sizes_are_those_of_the_catalogs(self):
        self.assertEqual(len(SETS["phosphor"].all_names()), 1512)
        self.assertEqual(len(SETS["emoji"].all_names()), 1595)
        self.assertGreaterEqual(len(SETS["brands"].all_names()), 3400)

    def test_every_name_is_plain_prefixed_sorted_and_unique(self):
        for key in SETS:
            icon_set = SETS[key]
            names = icon_set.all_names()
            self.assertEqual(names, sorted(names), key)
            self.assertEqual(len(names), len(set(names)), key)
            for name in names:
                self.assertTrue(name.startswith(icon_set.prefix), name)
                self.assertRegex(name[len(icon_set.prefix):], NAME, name)

    def test_every_file_is_a_small_valid_svg(self):
        for key in SETS:
            icon_set = SETS[key]
            for name in icon_set.all_names():
                check_svg_bytes(icon_set.file(name).read_bytes())

    def test_a_sample_of_every_set_can_be_tinted(self):
        for key in SETS:
            icon_set = SETS[key]
            names = icon_set.all_names()
            for name in names[:: max(1, len(names) // 40)]:
                root = parse_svg(icon_set.file(name).read_bytes())
                recolor(root, "#ff0000")
                root.set("fill", "#ff0000")
                surface = surface_from_svg(serialize(root).encode(), 48)
                visible = [pixel(surface, x, y) for x in range(48) for y in range(48) if pixel(surface, x, y)[3] > 0.5]
                self.assertTrue(visible, name)
                self.assertTrue(all(red > 0.9 and green < 0.1 and blue < 0.1 for red, green, blue, _alpha in visible), name)

    def test_the_shipped_logos_respect_the_exclusion_list(self):
        import hashlib
        import importlib.util

        script = Path(__file__).resolve().parent.parent / "build-aux" / "update-icon-sets.py"
        spec = importlib.util.spec_from_file_location("update_icon_sets_for_registry", script)
        update = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(update)
        for name in SETS["brands"].all_names():
            self.assertNotIn(hashlib.sha256(name[len("brand:"):].encode()).hexdigest(), update.EXCLUDED_BRANDS, name)

    def test_the_licenses_travel_with_the_icons(self):
        for key, files in (("phosphor", ("LICENSE",)), ("emoji", ("LICENSE", "LICENSE-CLDR")), ("brands", ("LICENSE", "DISCLAIMER"))):
            for name in files:
                self.assertTrue((SETS[key].directory / name).read_text(encoding="utf-8").strip(), f"{key}/{name}")
        self.assertIn("MIT", (SETS["emoji"].directory / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("Unicode", (SETS["emoji"].directory / "LICENSE-CLDR").read_text(encoding="utf-8"))
        self.assertIn("CC0", (SETS["brands"].directory / "LICENSE").read_text(encoding="utf-8"))

    def test_the_index_records_where_the_icons_come_from(self):
        for key in SETS:
            self.assertGreaterEqual(SETS[key].revision, 1, key)
        self.assertIn("phosphor-icons/core@", SETS["phosphor"].source)
        self.assertIn("fluentui-emoji@", SETS["emoji"].source)
        self.assertIn("unicode-org/cldr@", SETS["emoji"].source)
        self.assertIn("simple-icons/simple-icons@", SETS["brands"].source)

    def test_the_categories_of_every_set_are_sorted(self):
        for key in SETS:
            categories = SETS[key].categories()
            self.assertEqual(categories, sorted(categories), key)

    def test_a_file_belongs_to_its_own_set_only(self):
        with self.assertRaises(IconNotFoundError):
            SETS["brands"].file("emoji:cat-face")
        with self.assertRaises(IconNotFoundError):
            SETS["emoji"].file("brand:github")
        with self.assertRaises(IconNotFoundError):
            SETS["emoji"].file("emoji:nothing-here")
        with self.assertRaises(IconNotFoundError):
            SETS["emoji"].file("emoji:../cat-face")

    def test_the_paintable_of_every_set_can_be_made(self):
        for key in SETS:
            icon_set = SETS[key]
            self.assertIsNotNone(icon_set.paintable(icon_set.all_names()[0], 24))


class EmojiSearchTests(unittest.TestCase):
    emoji = SETS["emoji"]

    def first(self, query, count=12):
        return self.emoji.symbolic_names(query, limit=count)

    def test_an_english_word_finds_the_emoji(self):
        self.assertIn("emoji:cat-face", self.first("cat"))

    def test_the_exact_english_name_comes_first(self):
        self.assertEqual(self.first("rocket")[0], "emoji:rocket")

    def test_a_french_word_finds_the_emoji(self):
        self.assertTrue(any("cat" in name for name in self.first("chat")), self.first("chat"))
        self.assertTrue(any("heart" in name for name in self.first("coeur")), self.first("coeur"))
        self.assertTrue(any("fire" in name for name in self.first("feu")), self.first("feu"))

    def test_the_worst_accepted_rank_can_be_limited(self):
        everything = self.emoji.symbolic_names("cat", limit=10_000)
        names_only = self.emoji.symbolic_names("cat", limit=10_000, max_rank=2)
        words_too = self.emoji.symbolic_names("cat", limit=10_000, max_rank=3)
        self.assertLessEqual(len(names_only), len(words_too))
        self.assertLessEqual(len(words_too), len(everything))
        self.assertEqual(self.emoji.symbolic_names("cat", limit=10_000, max_rank=0), ["emoji:cat"])
        self.assertEqual(everything[: len(names_only)], names_only)

    def test_a_whole_word_ranks_before_a_part_of_a_word(self):
        names = self.emoji.symbolic_names("chat", limit=200)
        cats = [index for index, name in enumerate(names) if "cat" in name]
        castle = [index for index, name in enumerate(names) if "castle" in name]
        self.assertTrue(cats and castle)
        self.assertLess(max(cats), min(castle))

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(self.emoji.symbolic_names("MEDAILLE", limit=50), self.emoji.symbolic_names("médaille", limit=50))
        self.assertIn("emoji:1st-place-medal", self.emoji.symbolic_names("medaille", limit=50))

    def test_a_very_short_query_only_searches_names(self):
        for name in self.emoji.symbolic_names("zz", limit=100):
            self.assertIn("zz", name[len("emoji:"):])

    def test_the_names_are_given_in_the_language_asked(self):
        self.assertEqual(self.emoji.label("emoji:2nd-place-medal", "fr"), "médaille d’argent")
        self.assertEqual(self.emoji.label("emoji:2nd-place-medal", "en"), "2nd place medal")
        self.assertEqual(self.emoji.label("emoji:2nd-place-medal", "de"), "2nd place medal")
        self.assertEqual(self.emoji.label("emoji:2nd-place-medal"), "2nd place medal")

    def test_the_groups_become_categories(self):
        categories = self.emoji.categories()
        for wanted in ("smileys", "people", "nature", "food", "travel", "activities", "objects", "symbols", "flags"):
            self.assertIn(wanted, categories)
        self.assertGreaterEqual(len(self.emoji.names_in_category("flags")), 5)

    def test_the_monochrome_icons_use_neutral_colors_only(self):
        for name in self.emoji.all_names():
            colors = {color.lower() for color in re.findall(rb'(?:fill|stroke)="([^"]+)"', self.emoji.file(name).read_bytes())}
            for color in colors - {b"none", b"black", b"white"}:
                match = re.fullmatch(rb"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", color)
                self.assertIsNotNone(match, f"{name} {color}")
                channels = [int(part, 16) for part in match.groups()]
                self.assertLessEqual(max(channels) - min(channels), 8, f"{name} {color}")


class BrandSearchTests(unittest.TestCase):
    brands = SETS["brands"]

    def test_the_brand_name_finds_the_logo(self):
        self.assertEqual(self.brands.symbolic_names("github")[0], "brand:github")
        self.assertEqual(self.brands.symbolic_names("DOCKER")[0], "brand:docker")

    def test_a_partial_name_finds_several_logos(self):
        names = self.brands.symbolic_names("git", limit=100)
        self.assertIn("brand:github", names)
        self.assertIn("brand:gitlab", names)

    def test_the_title_of_the_logo_is_its_label(self):
        self.assertEqual(self.brands.label("brand:github"), "GitHub")
        self.assertEqual(self.brands.label("brand:github", "fr"), "GitHub")

    def test_an_alias_finds_the_logo(self):
        found = self.brands.symbolic_names("dotenv")
        self.assertTrue(found)
        self.assertEqual(found[0], "brand:dotenv")


class LabelTests(unittest.TestCase):
    def test_a_pictogram_is_shown_with_its_set(self):
        self.assertEqual(display_name("phosphor:camera"), "camera (Phosphor)")
        self.assertEqual(display_name("emoji:2nd-place-medal"), "2nd place medal (Emoji)")
        self.assertEqual(display_name("emoji:2nd-place-medal", "fr"), "médaille d’argent (Émoji)")
        self.assertEqual(display_name("brand:github"), "GitHub (Brand)")
        self.assertEqual(display_name("brand:github", "fr"), "GitHub (Marque)")

    def test_other_names_are_left_alone(self):
        self.assertEqual(display_name("camera-symbolic"), "camera-symbolic")
        self.assertEqual(display_name("unknown:thing"), "unknown:thing")

    def test_an_unknown_name_of_a_set_falls_back_to_its_text(self):
        self.assertEqual(display_name("emoji:not-there"), "not there (Emoji)")


class LanguageTests(unittest.TestCase):
    def test_a_french_desktop_gives_french(self):
        from unittest import mock

        for names, expected in ((["fr_FR.UTF-8", "fr_FR", "fr", "C"], "fr"), (["fr"], "fr"), (["en_US", "en", "fr"], "en"), (["de_DE"], "en"), ([], "en")):
            with mock.patch.object(iconsets.GLib, "get_language_names", return_value=names):
                self.assertEqual(current_language(), expected, names)


class NamesOfAnotherSetTests(unittest.TestCase):
    def sets(self):
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for key in ("phosphor", "emoji", "brands"):
            (root / key).mkdir()
            (root / key / "one.svg").write_text("<svg/>")
        return IconSets(root)

    def test_a_name_of_another_set_is_refused_even_with_a_prefix_of_the_same_length(self):
        sets = self.sets()
        with self.assertRaises(IconNotFoundError):
            sets["brands"].file("emoji:one")
        with self.assertRaises(IconNotFoundError):
            sets["emoji"].file("brand:one")
        self.assertEqual(sets["brands"].short_name("emoji:one"), "")
        self.assertEqual(sets["brands"].tags("emoji:one"), [])
        self.assertEqual(sets["brands"].label("emoji:one"), "")

    def test_a_short_tag_word_is_not_searched(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "alpha.svg").write_text("<svg/>")
            (directory / "index.json").write_text(json.dumps({"revision": 1, "icons": {"alpha": {"t": ["go on", "gone"]}}}))
            found = IconSet("test", "t:", directory)
            self.assertEqual(found.symbolic_names("go"), [])
            self.assertEqual(found.symbolic_names("gon"), ["t:alpha"])


class FolderTests(unittest.TestCase):
    def test_a_registry_can_use_another_folder(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for key in ("phosphor", "emoji", "brands"):
                (root / key).mkdir()
                (root / key / "one.svg").write_text("<svg/>")
            sets = IconSets(root)
            self.assertEqual(sets["emoji"].all_names(), ["emoji:one"])
            self.assertEqual(sets["brands"].all_names(), ["brand:one"])


if __name__ == "__main__":
    unittest.main()
