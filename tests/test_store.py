import json
import unittest

from custicon.errors import StoreError
from custicon.store import MAX_FAVORITES, MAX_NAME_LENGTH, MAX_RECENT, MAX_WINDOW_SIZE, MIN_WINDOW_SIZE, Entry, Store

from .support import WorkspaceTestCase


def digest(number: int) -> str:
    return f"{number:032x}"


def entry(folder: str, digest: str, previous=None) -> Entry:
    return Entry(folder, digest, "Adwaita", "#438de6", {"glyph_name": "star-symbolic"}, previous)


class StoreTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def test_empty_store_has_no_entries(self):
        self.assertEqual(self.store.all(), {})
        self.assertIsNone(self.store.get("/a"))

    def test_saving_the_same_icon_twice_gives_the_same_file(self):
        first = self.store.save_icon("<svg/>")
        second = self.store.save_icon("<svg/>")
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.store.icons_dir.glob("*.svg"))), 1)

    def test_different_icons_get_different_files(self):
        self.assertNotEqual(self.store.save_icon("<svg>a</svg>"), self.store.save_icon("<svg>b</svg>"))

    def test_icon_content_is_stored_unchanged(self):
        name = self.store.save_icon("<svg>é</svg>")
        self.assertEqual(self.store.icon_path(name).read_text(encoding="utf-8"), "<svg>é</svg>")

    def test_icon_uri_is_a_file_uri_inside_the_store(self):
        name = self.store.save_icon("<svg/>")
        uri = self.store.icon_uri(name)
        self.assertTrue(uri.startswith("file:///"))
        self.assertTrue(self.store.owns_uri(uri))

    def test_foreign_uris_are_not_owned(self):
        self.assertFalse(self.store.owns_uri("file:///somewhere/else.svg"))
        self.assertFalse(self.store.owns_uri(None))
        self.assertFalse(self.store.owns_uri(""))
        self.assertFalse(self.store.owns_uri(self.store.icons_dir.as_uri()))

    def test_entries_survive_a_new_store_instance(self):
        item = entry("/a", digest(1), previous="file:///old.png")
        self.store.put(item)
        self.assertEqual(Store(self.root).get("/a"), item)

    def test_remove_returns_the_entry_and_forgets_it(self):
        item = entry("/a", digest(1))
        self.store.put(item)
        self.assertEqual(self.store.remove("/a"), item)
        self.assertIsNone(self.store.get("/a"))
        self.assertIsNone(self.store.remove("/a"))

    def test_put_replaces_the_entry_of_the_same_folder(self):
        self.store.put(entry("/a", digest(1)))
        self.store.put(entry("/a", digest(2)))
        self.assertEqual(self.store.get("/a").digest, digest(2))
        self.assertEqual(len(self.store.all()), 1)

    def test_prune_removes_only_unreferenced_icons(self):
        kept = self.store.save_icon("<svg>kept</svg>")
        lost = self.store.save_icon("<svg>lost</svg>")
        self.store.put(entry("/a", kept))
        self.assertEqual(self.store.prune(), [lost])
        self.assertTrue(self.store.icon_path(kept).exists())
        self.assertFalse(self.store.icon_path(lost).exists())

    def test_a_shared_icon_stays_while_one_folder_uses_it(self):
        shared = self.store.save_icon("<svg>shared</svg>")
        self.store.put(entry("/a", shared))
        self.store.put(entry("/b", shared))
        self.store.remove("/a")
        self.assertEqual(self.store.prune(), [])
        self.store.remove("/b")
        self.assertEqual(self.store.prune(), [shared])

    def test_prune_without_icons_folder_is_harmless(self):
        self.assertEqual(self.store.prune(), [])

    def test_writes_leave_no_temporary_file(self):
        self.store.save_icon("<svg/>")
        self.store.put(entry("/a", digest(1)))
        names = [path.name for path in self.root.rglob("*") if path.is_file()]
        self.assertFalse([name for name in names if name.endswith(".tmp")])

    def test_a_corrupt_registry_is_recovered_from_the_backup(self):
        self.store.put(entry("/a", digest(1)))
        self.store.put(entry("/b", digest(2)))
        self.store.registry_path.write_text("{not json")
        self.assertEqual(set(self.store.all()), {"/a"})

    def test_saving_after_recovery_repairs_the_registry(self):
        self.store.put(entry("/a", digest(1)))
        self.store.put(entry("/b", digest(2)))
        self.store.registry_path.write_text("{not json")
        self.store.put(entry("/c", digest(3)))
        self.assertEqual(set(Store(self.root).all()), {"/a", "/c"})

    def test_a_corrupt_registry_does_not_overwrite_the_good_backup(self):
        self.store.put(entry("/a", digest(1)))
        self.store.put(entry("/b", digest(2)))
        self.store.registry_path.write_text("{not json")
        backup = self.store.registry_path.with_name("registry.json.bak")
        good = backup.read_text()
        self.store.put(entry("/c", digest(3)))
        self.assertEqual(backup.read_text(), good)

    def test_registry_and_backup_both_corrupt_raise(self):
        self.store.put(entry("/a", digest(1)))
        self.store.put(entry("/b", digest(2)))
        self.store.registry_path.write_text("{not json")
        self.store.registry_path.with_name("registry.json.bak").write_text("also broken")
        with self.assertRaises(StoreError):
            self.store.all()

    def test_corrupt_registry_without_backup_raises(self):
        self.root.mkdir(parents=True)
        self.store.registry_path.write_text("[]")
        with self.assertRaises(StoreError):
            self.store.all()

    def test_unsupported_version_is_refused(self):
        self.root.mkdir(parents=True)
        self.store.registry_path.write_text(json.dumps({"version": 99, "entries": []}))
        with self.assertRaises(StoreError):
            self.store.all()

    def test_registry_is_readable_json(self):
        self.store.put(entry("/a", digest(1), previous="file:///old.png"))
        document = json.loads(self.store.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["entries"][0]["previous"], "file:///old.png")

    def test_unwritable_location_raises_a_store_error(self):
        blocker = self.root.parent / "blocker"
        blocker.write_text("a file, not a folder")
        with self.assertRaises(StoreError):
            Store(blocker / "inside").save_icon("<svg/>")


class DefaultsTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def test_nothing_is_stored_by_default(self):
        self.assertIsNone(self.store.load_defaults())

    def test_values_survive_a_new_store_instance(self):
        self.store.save_defaults({"size": 1.5, "color": None, "tint": False})
        self.assertEqual(Store(self.root).load_defaults(), {"size": 1.5, "color": None, "tint": False})

    def test_unusable_files_are_ignored(self):
        self.root.mkdir(parents=True)
        for text in ("{broken", "[1]", '{"a": [1]}', '{"a": {"b": 1}}', "[" * 100000, ""):
            self.store.defaults_path.write_text(text)
            self.assertIsNone(self.store.load_defaults(), text[:20])

    def test_saving_leaves_no_temporary_file(self):
        self.store.save_defaults({"size": 1.0})
        self.assertFalse([path for path in self.root.rglob("*") if path.name.endswith(".tmp")])

    def test_an_unwritable_location_is_a_store_error(self):
        blocker = self.root.parent / "blocker"
        blocker.write_text("a file")
        with self.assertRaises(StoreError):
            Store(blocker / "inside").save_defaults({"size": 1.0})


class PickerMemoryTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def test_nothing_is_remembered_at_first(self):
        self.assertEqual(self.store.load_picker(), {"recent": [], "favorites": []})

    def test_the_lists_survive_a_new_store_instance(self):
        self.store.save_picker(["phosphor:house", "star-symbolic"], ["emoji:cat-face"])
        self.assertEqual(Store(self.root).load_picker(), {"recent": ["phosphor:house", "star-symbolic"], "favorites": ["emoji:cat-face"]})

    def test_unusable_files_give_empty_lists(self):
        self.root.mkdir(parents=True)
        for text in ("{broken", "[1]", '"text"', "", "[" * 100000, '{"recent": "no", "favorites": 3}'):
            self.store.picker_path.write_text(text)
            self.assertEqual(self.store.load_picker(), {"recent": [], "favorites": []}, text[:20])

    def test_damaged_names_are_dropped_and_the_others_kept(self):
        self.root.mkdir(parents=True)
        document = {"recent": ["ok", 5, None, "", "a\0b", "x" * 101, ["l"], "fine"], "favorites": ["good", {"a": 1}, "good", "other"]}
        self.store.picker_path.write_text(json.dumps(document))
        self.assertEqual(self.store.load_picker(), {"recent": ["ok", "fine"], "favorites": ["good", "other"]})

    def test_duplicates_are_removed_keeping_the_first(self):
        self.store.save_picker(["a", "b", "a", "c", "b"], [])
        self.assertEqual(self.store.load_picker()["recent"], ["a", "b", "c"])

    def test_the_lists_are_limited(self):
        names = [f"icon-{number}" for number in range(200)]
        self.store.save_picker(names, names)
        loaded = self.store.load_picker()
        self.assertEqual(loaded["recent"], names[:MAX_RECENT])
        self.assertEqual(loaded["favorites"], names[:MAX_FAVORITES])

    def test_the_file_itself_is_cleaned_when_saved(self):
        self.store.save_picker(["a", "", "a", "b\0c", "d"], ["x" * 101, "f"])
        document = json.loads(self.store.picker_path.read_text(encoding="utf-8"))
        self.assertEqual(document, {"recent": ["a", "d"], "favorites": ["f"]})

    def test_a_name_of_the_maximum_length_is_kept(self):
        name = "x" * MAX_NAME_LENGTH
        self.store.save_picker([name], [name])
        self.assertEqual(self.store.load_picker(), {"recent": [name], "favorites": [name]})

    def test_saving_leaves_no_temporary_file(self):
        self.store.save_picker(["a"], ["b"])
        self.assertFalse([path for path in self.root.rglob("*") if path.name.endswith(".tmp")])

    def test_an_unwritable_location_is_a_store_error(self):
        blocker = self.root.parent / "blocker"
        blocker.write_text("a file")
        with self.assertRaises(StoreError):
            Store(blocker / "inside").save_picker(["a"], [])


if __name__ == "__main__":
    unittest.main()


class WindowStateStoreTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def write(self, text: str):
        self.root.mkdir(parents=True, exist_ok=True)
        self.store.window_path.write_text(text, encoding="utf-8")

    def test_nothing_is_remembered_at_first(self):
        self.assertIsNone(self.store.load_window())

    def test_a_size_comes_back_as_saved(self):
        self.store.save_window(800, 600, False)
        self.assertEqual(self.store.load_window(), {"width": 800, "height": 600, "maximized": False})

    def test_the_maximized_state_is_kept(self):
        self.store.save_window(800, 600, True)
        self.assertTrue(self.store.load_window()["maximized"])

    def test_the_last_save_wins(self):
        self.store.save_window(800, 600, False)
        self.store.save_window(900, 700, True)
        self.assertEqual(self.store.load_window(), {"width": 900, "height": 700, "maximized": True})

    def test_the_file_is_named_after_the_window(self):
        self.store.save_window(800, 600, False)
        self.assertEqual(self.store.window_path.name, "window.json")
        self.assertTrue(self.store.window_path.exists())

    def test_no_temporary_file_is_left(self):
        self.store.save_window(800, 600, False)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_damaged_file_gives_nothing(self):
        for text in ("", "not json", "[]", "null", '"text"', "{", '{"width": 800}', '{"height": 600}'):
            with self.subTest(text=text):
                self.write(text)
                self.assertIsNone(self.store.load_window())

    def test_sizes_that_are_not_whole_numbers_are_refused(self):
        for width, height in (("800", 600), (800, "600"), (800.5, 600), (True, 600), (800, None), (None, None), ([800], 600)):
            with self.subTest(width=width, height=height):
                self.write(json.dumps({"width": width, "height": height, "maximized": False}))
                self.assertIsNone(self.store.load_window())

    def test_sizes_out_of_bounds_are_refused(self):
        for width, height in ((MIN_WINDOW_SIZE - 1, 600), (800, MIN_WINDOW_SIZE - 1), (MAX_WINDOW_SIZE + 1, 600), (800, MAX_WINDOW_SIZE + 1), (-5, 600), (0, 0)):
            with self.subTest(width=width, height=height):
                self.write(json.dumps({"width": width, "height": height, "maximized": False}))
                self.assertIsNone(self.store.load_window())

    def test_the_limits_themselves_are_accepted(self):
        for size in (MIN_WINDOW_SIZE, MAX_WINDOW_SIZE):
            self.store.save_window(size, size, False)
            self.assertEqual(self.store.load_window()["width"], size)

    def test_a_maximized_value_that_is_not_true_means_not_maximized(self):
        for value in ("true", 1, None, "yes", [True]):
            with self.subTest(value=value):
                self.write(json.dumps({"width": 800, "height": 600, "maximized": value}))
                self.assertFalse(self.store.load_window()["maximized"])

    def test_saving_an_unusable_size_is_refused_and_keeps_the_old_one(self):
        self.store.save_window(800, 600, False)
        for width, height in ((MIN_WINDOW_SIZE - 1, 600), (800, MAX_WINDOW_SIZE + 1), (True, 600), (800.0, 600)):
            with self.subTest(width=width, height=height):
                with self.assertRaises(StoreError):
                    self.store.save_window(width, height, False)
        self.assertEqual(self.store.load_window()["width"], 800)

    def test_a_write_failure_is_reported(self):
        self.root.mkdir(parents=True)
        self.store.window_path.mkdir()
        with self.assertRaises(StoreError):
            self.store.save_window(800, 600, False)


class ColorSchemeStoreTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def write(self, text: str):
        self.root.mkdir(parents=True, exist_ok=True)
        self.store.appearance_path.write_text(text, encoding="utf-8")

    def test_the_system_scheme_is_the_default(self):
        self.assertEqual(self.store.load_color_scheme(), "system")

    def test_every_scheme_comes_back(self):
        for name in ("light", "dark", "system"):
            self.store.save_color_scheme(name)
            self.assertEqual(self.store.load_color_scheme(), name)

    def test_unreadable_files_fall_back_to_the_system_scheme(self):
        for text in ("", "{", "[]", "null", '{"color_scheme": 3}', '{"color_scheme": "purple"}', '{"color_scheme": ["dark"]}', "[" * 100000):
            self.write(text)
            self.assertEqual(self.store.load_color_scheme(), "system", text[:20])

    def test_an_unknown_scheme_is_refused_and_nothing_is_written(self):
        for value in ("purple", "", None, 3, "Dark"):
            with self.assertRaises(StoreError):
                self.store.save_color_scheme(value)
        self.assertFalse(self.store.appearance_path.exists())

    def test_the_file_is_named_after_the_appearance(self):
        self.store.save_color_scheme("dark")
        self.assertEqual(self.store.appearance_path.name, "appearance.json")
