import json
import unittest
from pathlib import Path

from custicon.errors import InvalidSettingsError, StoreError
from custicon.layout import MAX_FILE_BYTES
from custicon.service import Service
from custicon.store import MAX_STYLE_NAME, MAX_STYLES, Store

from .support import WorkspaceTestCase

BLUE = {"color": "#3366cc", "accent": False, "size": 1.5, "offset_x": 0.1, "offset_y": -0.1, "tint": True}
ACCENT = {"color": None, "accent": True, "size": 1.0, "offset_x": 0.0, "offset_y": 0.0, "tint": True}


def document(*styles) -> str:
    return json.dumps({"version": 1, "styles": list(styles)})


def style(name="Blue", appearance=None) -> dict:
    return {"name": name, "appearance": BLUE if appearance is None else appearance}


class StoreStylesTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def write(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.store.styles_path.write_text(text)

    def test_nothing_is_stored_by_default(self):
        self.assertEqual(self.store.load_styles(), [])

    def test_styles_survive_a_new_store_instance(self):
        self.store.save_styles([style("A"), style("B", ACCENT)])
        self.assertEqual(Store(self.root).load_styles(), [style("A"), style("B", ACCENT)])

    def test_accents_and_non_ascii_names_are_kept_readable(self):
        self.store.save_styles([style("Été")])
        self.assertIn("Été", self.store.styles_path.read_text(encoding="utf-8"))

    def test_unusable_files_give_no_styles(self):
        bad = [
            "{broken",
            "[]",
            "",
            json.dumps({"version": 2, "styles": [style("A")]}),
            json.dumps({"styles": [style("A")]}),
            json.dumps({"version": 1, "styles": {}}),
            document(style("")),
            document(style(" padded ")),
            document(style("x" * (MAX_STYLE_NAME + 1))),
            document(style("A"), style("a")),
            document({"name": "A", "appearance": {"size": [1]}}),
            document({"name": "A", "appearance": []}),
            document({"name": 3, "appearance": {}}),
            document({"appearance": {}}),
            document(*[style(f"s{number}") for number in range(MAX_STYLES + 1)]),
            "[" * 100000,
        ]
        for text in bad:
            self.write(text)
            self.assertEqual(self.store.load_styles(), [], text[:40])

    def test_the_maximum_number_of_styles_is_accepted(self):
        self.write(document(*[style(f"s{number}") for number in range(MAX_STYLES)]))
        self.assertEqual(len(self.store.load_styles()), MAX_STYLES)

    def test_saving_leaves_no_temporary_file(self):
        self.store.save_styles([style()])
        self.assertFalse([path for path in self.root.rglob("*") if path.name.endswith(".tmp")])

    def test_an_unwritable_location_is_a_store_error(self):
        blocker = self.root.parent / "blocker"
        blocker.write_text("a file")
        with self.assertRaises(StoreError):
            Store(blocker / "inside").save_styles([style()])


class ServiceStylesTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.store = Store(self.root / "data")
        self.service = Service(self.store)

    def names(self):
        return [item.name for item in self.service.styles()]

    def test_a_saved_style_is_listed_with_its_appearance(self):
        self.service.save_style("Blue", BLUE)
        self.assertEqual(self.names(), ["Blue"])
        self.assertEqual(self.service.styles()[0].appearance, BLUE)

    def test_styles_keep_their_order(self):
        for name in ("B", "A", "C"):
            self.service.save_style(name, BLUE)
        self.assertEqual(self.names(), ["B", "A", "C"])

    def test_saving_the_same_name_replaces_the_style_whatever_the_case(self):
        self.service.save_style("Blue", BLUE)
        self.service.save_style("  blue ", ACCENT)
        self.assertEqual(self.names(), ["blue"])
        self.assertEqual(self.service.styles()[0].appearance, ACCENT)

    def test_invalid_names_are_refused(self):
        for name in ("", "   ", "x" * (MAX_STYLE_NAME + 1), None, 3):
            with self.assertRaises(InvalidSettingsError, msg=repr(name)):
                self.service.save_style(name, BLUE)
        self.assertEqual(self.names(), [])

    def test_a_name_of_the_maximum_length_is_accepted(self):
        self.service.save_style("x" * MAX_STYLE_NAME, BLUE)
        self.assertEqual(len(self.names()), 1)

    def test_an_invalid_appearance_is_refused(self):
        for appearance in ({"size": 99}, {"color": "red"}, {"tint": "yes"}, {"color": "#112233", "accent": True}):
            with self.assertRaises(InvalidSettingsError, msg=str(appearance)):
                self.service.save_style("Bad", appearance)

    def test_unknown_fields_are_not_stored(self):
        self.service.save_style("Odd", {**BLUE, "glyph_name": "star-symbolic", "folder": "/etc"})
        self.assertEqual(self.service.styles()[0].appearance, BLUE)

    def test_the_number_of_styles_is_limited_but_replacing_is_allowed(self):
        for number in range(MAX_STYLES):
            self.service.save_style(f"s{number}", BLUE)
        with self.assertRaises(InvalidSettingsError):
            self.service.save_style("one more", BLUE)
        self.service.save_style("s0", ACCENT)
        self.assertEqual(len(self.names()), MAX_STYLES)

    def test_deleting_removes_only_that_style(self):
        for name in ("A", "B"):
            self.service.save_style(name, BLUE)
        self.service.delete_style("A")
        self.assertEqual(self.names(), ["B"])
        self.service.delete_style("unknown")
        self.assertEqual(self.names(), ["B"])

    def test_a_style_with_a_damaged_appearance_is_ignored(self):
        self.store.save_styles([style("Good"), style("Bad", {"size": 99})])
        self.assertEqual(self.names(), ["Good"])

    def test_export_writes_a_readable_file_and_counts(self):
        self.service.save_style("Blue", BLUE)
        target = self.root / "out.json"
        self.assertEqual(self.service.export_styles(target), 1)
        self.assertEqual(json.loads(target.read_text())["styles"], [style("Blue")])

    def test_exporting_to_an_unwritable_place_is_a_store_error(self):
        with self.assertRaises(StoreError):
            self.service.export_styles(self.root / "missing" / "out.json")

    def test_a_round_trip_restores_the_styles(self):
        self.service.save_style("Blue", BLUE)
        self.service.save_style("Accent", ACCENT)
        target = self.root / "out.json"
        self.service.export_styles(target)
        other = Service(Store(self.root / "other"))
        self.assertEqual(other.import_styles(target), (2, 0))
        self.assertEqual(other.styles(), self.service.styles())

    def write_import(self, text) -> Path:
        path = self.root / "in.json"
        path.write_text(text) if isinstance(text, str) else path.write_bytes(text)
        return path

    def test_import_replaces_styles_with_the_same_name(self):
        self.service.save_style("Blue", ACCENT)
        self.assertEqual(self.service.import_styles(self.write_import(document(style("blue")))), (1, 0))
        self.assertEqual(self.names(), ["blue"])
        self.assertEqual(self.service.styles()[0].appearance, BLUE)

    def test_import_counts_and_skips_invalid_items(self):
        text = document(style("Good"), style("", BLUE), style("Bad", {"size": 99}), {"name": "NoAppearance"}, 5, style("Nested", {"size": [1]}))
        self.assertEqual(self.service.import_styles(self.write_import(text)), (1, 5))
        self.assertEqual(self.names(), ["Good"])

    def test_import_ignores_unknown_fields(self):
        text = document(style("Odd", {**BLUE, "glyph_name": "x"}))
        self.service.import_styles(self.write_import(text))
        self.assertEqual(self.service.styles()[0].appearance, BLUE)

    def test_import_strips_names(self):
        self.service.import_styles(self.write_import(document(style("  Blue  "))))
        self.assertEqual(self.names(), ["Blue"])

    def test_import_respects_the_limit(self):
        for number in range(MAX_STYLES - 1):
            self.service.save_style(f"s{number}", BLUE)
        text = document(style("new1"), style("new2"), style("s0", ACCENT))
        self.assertEqual(self.service.import_styles(self.write_import(text)), (2, 1))
        self.assertEqual(len(self.names()), MAX_STYLES)

    def test_import_of_a_file_that_is_not_a_styles_file_is_a_store_error(self):
        for content in ("{broken", "[]", json.dumps({"styles": "x"}), json.dumps({}), b"\xff\xfe", "[" * 100000, ""):
            with self.assertRaises(StoreError, msg=str(content)[:30]):
                self.service.import_styles(self.write_import(content))

    def test_import_of_a_missing_or_huge_file_is_a_store_error(self):
        with self.assertRaises(StoreError):
            self.service.import_styles(self.root / "missing.json")
        with self.assertRaises(StoreError):
            self.service.import_styles(self.write_import(" " * (MAX_FILE_BYTES + 1)))

    def test_nothing_is_written_when_nothing_is_imported(self):
        self.service.import_styles(self.write_import(document(style("", BLUE))))
        self.assertFalse(self.store.styles_path.exists())


if __name__ == "__main__":
    unittest.main()
