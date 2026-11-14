import unittest
from pathlib import Path

from custicon.errors import IconNotFoundError
from custicon.iconsets import PHOSPHOR_PREFIX, PhosphorSet, display_name, is_phosphor, phosphor_name
from custicon.render import check_svg_bytes


class NameTests(unittest.TestCase):
    def test_a_prefixed_name_is_recognised(self):
        self.assertTrue(is_phosphor("phosphor:acorn"))
        self.assertFalse(is_phosphor("camera-symbolic"))
        self.assertFalse(is_phosphor(None))

    def test_the_prefix_is_removed(self):
        self.assertEqual(phosphor_name("phosphor:acorn"), "acorn")

    def test_a_display_name_mentions_the_set(self):
        self.assertEqual(display_name("phosphor:acorn"), "acorn (Phosphor)")
        self.assertEqual(display_name("camera-symbolic"), "camera-symbolic")


class SetTests(unittest.TestCase):
    def setUp(self):
        self.set = PhosphorSet()

    def test_the_whole_collection_is_shipped(self):
        self.assertGreaterEqual(len(self.set.all_names()), 290)
        self.assertEqual(self.set.all_names(), sorted(self.set.all_names()))
        self.assertTrue(all(name.startswith(PHOSPHOR_PREFIX) for name in self.set.all_names()))

    def test_every_icon_is_a_small_valid_svg_that_can_be_tinted(self):
        for name in self.set.all_names():
            data = self.set.file(name).read_bytes()
            check_svg_bytes(data)
            self.assertIn(b'fill="currentColor"', data[:200], name)

    def test_the_license_is_shipped_with_the_icons(self):
        self.assertIn("MIT License", (self.set.directory / "LICENSE").read_text())

    def test_a_query_finds_part_of_a_name(self):
        names = self.set.symbolic_names("hou")
        self.assertIn("phosphor:house", names)
        self.assertTrue(all("hou" in name for name in names))

    def test_a_query_ignores_case_and_spaces(self):
        self.assertEqual(self.set.symbolic_names("  HOUSE "), self.set.symbolic_names("house"))

    def test_an_empty_query_lists_everything_up_to_the_limit(self):
        self.assertEqual(len(self.set.symbolic_names("", limit=10)), 10)
        self.assertEqual(self.set.symbolic_names("", limit=10_000), self.set.all_names())

    def test_the_prefix_is_not_searched(self):
        self.assertEqual(self.set.symbolic_names("phosphor"), [])

    def test_a_file_is_found_by_its_prefixed_name(self):
        path = self.set.file("phosphor:acorn")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "acorn-symbolic.svg")

    def test_an_unknown_name_is_refused(self):
        for name in ("phosphor:nothing-here", "phosphor:", "acorn", "phosphor:../acorn", "phosphor:a/b"):
            with self.assertRaises(IconNotFoundError, msg=name):
                self.set.file(name)

    def test_the_glyph_file_is_the_same_file(self):
        self.assertEqual(self.set.glyph_file("phosphor:acorn"), self.set.file("phosphor:acorn"))

    def test_a_paintable_is_provided_for_the_picker(self):
        self.assertIsNotNone(self.set.paintable("phosphor:acorn", 24))
        with self.assertRaises(IconNotFoundError):
            self.set.paintable("phosphor:nothing-here", 24)


if __name__ == "__main__":
    unittest.main()
