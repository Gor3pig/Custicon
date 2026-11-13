import json
import unittest

from custicon.errors import StoreError
from custicon.store import Entry, Store

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


if __name__ == "__main__":
    unittest.main()
