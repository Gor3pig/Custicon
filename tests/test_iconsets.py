import json
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


class PlainFolderTests(unittest.TestCase):
    def test_a_folder_without_an_index_still_works(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "one.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" fill="currentColor"/>')
            plain = PhosphorSet(directory)
            self.assertEqual(plain.all_names(), ["phosphor:one"])
            self.assertEqual(plain.revision, 1)
            self.assertEqual(plain.categories(), [])
            self.assertEqual(plain.tags("phosphor:one"), [])

    def folder_with(self, names, index=None):
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = Path(folder.name)
        for name in names:
            (directory / f"{name}.svg").write_text("<svg/>")
        if index is not None:
            (directory / "index.json").write_text(json.dumps(index))
        return directory

    def test_the_names_are_sorted_whatever_the_order_of_the_index(self):
        index = {"revision": 2, "icons": {"c": {}, "a": {}, "b": {}}}
        self.assertEqual(PhosphorSet(self.folder_with(["a", "b", "c"], index)).all_names(), ["phosphor:a", "phosphor:b", "phosphor:c"])
        self.assertEqual(PhosphorSet(self.folder_with(["c", "a", "b"])).all_names(), ["phosphor:a", "phosphor:b", "phosphor:c"])

    def test_an_icon_listed_in_the_index_without_its_file_is_dropped(self):
        index = {"revision": 2, "icons": {"a": {}, "ghost": {}}}
        found = PhosphorSet(self.folder_with(["a"], index))
        self.assertEqual(found.all_names(), ["phosphor:a"])
        with self.assertRaises(IconNotFoundError):
            found.file("phosphor:ghost")

    def test_a_file_missing_from_the_index_is_dropped_too(self):
        index = {"revision": 2, "icons": {"a": {}}}
        self.assertEqual(PhosphorSet(self.folder_with(["a", "extra"], index)).all_names(), ["phosphor:a"])

    def test_an_index_of_the_wrong_shape_is_ignored_with_its_revision(self):
        for index in ({"revision": 2, "icons": []}, {"revision": "2", "icons": {"a": {}}}, {"revision": True, "icons": {"a": {}}}, {"icons": {"a": {}}}, [1, 2]):
            found = PhosphorSet(self.folder_with(["a", "b"], index))
            self.assertEqual(found.all_names(), ["phosphor:a", "phosphor:b"], index)
            self.assertEqual(found.revision, 1, index)

    def test_an_entry_of_the_wrong_shape_is_dropped(self):
        index = {"revision": 2, "icons": {"a": {}, "b": "text"}}
        self.assertEqual(PhosphorSet(self.folder_with(["a", "b"], index)).all_names(), ["phosphor:a"])

    def test_a_damaged_index_is_ignored(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "one.svg").write_text("<svg/>")
            (directory / "index.json").write_text("{not json")
            self.assertEqual(PhosphorSet(directory).all_names(), ["phosphor:one"])


class SetTests(unittest.TestCase):
    def setUp(self):
        self.set = PhosphorSet()

    def test_the_whole_collection_is_shipped(self):
        self.assertEqual(len(self.set.all_names()), 1512)
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
        self.assertIn("phosphor:house-line", names)

    def test_the_exact_name_comes_first_then_names_starting_with_the_query(self):
        names = self.set.symbolic_names("house")
        self.assertEqual(names[0], "phosphor:house")
        starting = [name for name in names if phosphor_name(name).startswith("house")]
        self.assertEqual(names[: len(starting)], starting)
        self.assertEqual(starting, sorted(starting))

    def test_the_limit_applies_to_a_query(self):
        self.assertEqual(len(self.set.symbolic_names("house", limit=3)), 3)
        self.assertEqual(self.set.symbolic_names("house", limit=3), self.set.symbolic_names("house")[:3])

    def test_names_starting_with_the_query_come_before_names_containing_it(self):
        names = [phosphor_name(name) for name in self.set.symbolic_names("up", limit=10_000)]
        starting = [name for name in names if name.startswith("up")]
        self.assertTrue(starting)
        self.assertEqual(names[: len(starting)], starting)
        containing = [name for name in names[len(starting):] if "up" in name]
        self.assertTrue(containing)
        self.assertLess(min(containing), min(starting))

    def test_a_query_also_finds_icons_by_their_tags(self):
        names = self.set.symbolic_names("telephone")
        self.assertIn("phosphor:phone", names)
        self.assertNotIn("telephone", " ".join(phosphor_name(name) for name in names))

    def test_a_name_match_ranks_before_a_tag_match(self):
        names = self.set.symbolic_names("camera")
        self.assertEqual(names[0], "phosphor:camera")
        self.assertGreater(len(names), 3)

    def test_a_very_short_query_only_searches_names(self):
        for name in self.set.symbolic_names("zz"):
            self.assertIn("zz", phosphor_name(name))

    def test_the_tags_of_an_icon_are_available(self):
        self.assertIn("buildings", self.set.tags("phosphor:house"))
        self.assertEqual(self.set.tags("phosphor:nothing-here"), [])

    def test_the_categories_group_the_icons(self):
        categories = self.set.categories()
        self.assertEqual(categories, sorted(categories))
        for wanted in ("nature", "development", "games", "weather"):
            self.assertIn(wanted, categories)
        nature = self.set.names_in_category("nature")
        self.assertIn("phosphor:tree", nature)
        self.assertEqual(nature, sorted(nature))
        self.assertEqual(self.set.names_in_category("no-such-category"), [])

    def test_the_index_and_the_files_agree(self):
        files = {path.stem for path in self.set.directory.glob("*.svg")}
        self.assertEqual(files, {phosphor_name(name) for name in self.set.all_names()})

    def test_the_revision_of_the_set_is_known(self):
        self.assertEqual(self.set.revision, 2)

    def test_the_names_used_by_version_1_3_0_still_open(self):
        for old in ("house", "camera", "music-notes", "star", "acorn"):
            self.assertTrue(self.set.file(f"phosphor:{old}").is_file(), old)

    def test_a_renamed_icon_still_opens(self):
        self.assertEqual(self.set.file("phosphor:phone-handset"), self.set.file("phosphor:phone"))
        self.assertNotIn("phosphor:phone-handset", self.set.all_names())

    def test_a_query_ignores_case_and_spaces(self):
        self.assertEqual(self.set.symbolic_names("  HOUSE "), self.set.symbolic_names("house"))

    def test_an_empty_query_lists_everything_up_to_the_limit(self):
        self.assertEqual(len(self.set.symbolic_names("", limit=10)), 10)
        self.assertEqual(self.set.symbolic_names("", limit=10_000), self.set.all_names())

    def test_the_prefix_is_not_searched(self):
        self.assertEqual(self.set.symbolic_names("phosphor:house"), [])
        self.assertEqual(self.set.symbolic_names("phosphor"), ["phosphor:phosphor-logo"])

    def test_a_file_is_found_by_its_prefixed_name(self):
        path = self.set.file("phosphor:acorn")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "acorn.svg")

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
