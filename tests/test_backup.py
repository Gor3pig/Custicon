import json
import unittest
from dataclasses import replace
from pathlib import Path

from custicon import backup, dotdirectory
from custicon.errors import StoreError
from custicon.service import Service, Settings
from custicon.store import Store

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme

STAR = Settings(glyph_name="star-symbolic")


class BackupTestCase(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.themes = self.root / "themes"
        self.themes.mkdir()
        glyphs = {"star-symbolic": glyph_svg(), "heart-symbolic": glyph_svg("#ff0000")}
        make_theme(self.themes, "Blue", folder=folder_svg("#3366cc"), glyphs=glyphs)
        self.workspace = self.root / "folders"
        self.workspace.mkdir()
        self.service = self.make_service("data")
        self.file = self.root / "backup.json"

    def make_service(self, name):
        return Service(Store(self.root / name), theme_name=lambda: "Blue", search_dirs=[str(self.themes)], backend="directory")

    def make_folder(self, name):
        folder = self.workspace / name
        folder.mkdir()
        return folder

    def fill(self):
        a, b = self.make_folder("a"), self.make_folder("b")
        self.service.apply(a, replace(STAR, badge_glyph="heart-symbolic", badge_corner="tl"))
        self.service.apply(b, Settings(glyph_text="AB", folder_tint="#e01b24"))
        self.service.save_style("Big", {"size": 1.5})
        self.service.remember_glyph("star-symbolic")
        self.service.set_favorite("heart-symbolic", True)
        self.service.remember_appearance(Settings(glyph_name="x", size=1.25))
        return a, b


class ExportTests(BackupTestCase):
    def test_the_summary_counts_everything(self):
        self.fill()
        summary = self.service.export_backup(self.file)
        self.assertEqual((summary.folders, summary.styles, summary.pictograms), (2, 1, 2))

    def test_the_file_is_a_versioned_json_document(self):
        self.fill()
        self.service.export_backup(self.file)
        document = json.loads(self.file.read_text())
        self.assertEqual((document["format"], document["version"]), ("custicon-backup", 1))
        self.assertEqual(sorted(item["folder"] for item in document["folders"]), sorted(str(self.workspace / name) for name in ("a", "b")))

    def test_the_file_holds_no_image_and_no_internal_copy_name(self):
        self.fill()
        self.service.export_backup(self.file)
        text = self.file.read_text()
        self.assertNotIn("<svg", text)
        self.assertNotIn("base64", text)
        self.assertNotIn("glyph_copy", text)

    def test_an_empty_installation_exports_an_empty_backup(self):
        summary = self.service.export_backup(self.file)
        self.assertEqual((summary.folders, summary.styles, summary.pictograms), (0, 0, 0))
        self.assertEqual(json.loads(self.file.read_text())["folders"], [])

    def test_an_unwritable_destination_is_a_clear_error(self):
        with self.assertRaises(StoreError):
            self.service.export_backup(self.root / "missing" / "backup.json")

    def test_a_folder_without_badge_keeps_the_old_settings_format(self):
        self.fill()
        self.service.export_backup(self.file)
        settings = {item["folder"]: item["settings"] for item in json.loads(self.file.read_text())["folders"]}
        self.assertNotIn("badge_glyph", settings[str(self.workspace / "b")])
        self.assertEqual(settings[str(self.workspace / "a")]["badge_corner"], "tl")


class RestoreTests(BackupTestCase):
    def test_a_fresh_installation_gets_everything_back(self):
        a, b = self.fill()
        self.service.export_backup(self.file)
        fresh = self.make_service("other")
        for folder in (a, b):
            (folder / ".directory").unlink()
        report = fresh.import_backup(self.file)
        self.assertEqual(sorted(report.restored), sorted([str(a), str(b)]))
        self.assertEqual((report.kept, report.missing, report.failed), ([], [], {}))
        self.assertEqual(report.styles_added, 1)
        self.assertEqual(fresh.styles()[0].name, "Big")
        entry = fresh.store.get(str(a))
        self.assertEqual(Settings.from_dict(entry.settings).badge_corner, "tl")
        self.assertTrue(fresh.store.icon_path(entry.digest).is_file())
        self.assertEqual(dotdirectory.read_icon(a), fresh.store.icon_uri(entry.digest))
        self.assertEqual(fresh.remembered_appearance()["size"], 1.25)
        memory = fresh.picker_memory()
        self.assertEqual((memory.recent, memory.favorites), (("star-symbolic",), ("heart-symbolic",)))

    def test_the_restored_icon_is_the_same_as_the_saved_one(self):
        a, _b = self.fill()
        before = self.service.store.get(str(a)).digest
        self.service.export_backup(self.file)
        fresh = self.make_service("other")
        fresh.import_backup(self.file)
        self.assertEqual(fresh.store.get(str(a)).digest, before)

    def test_a_missing_folder_is_reported_and_ignored(self):
        a, b = self.fill()
        self.service.export_backup(self.file)
        fresh = self.make_service("other")
        b.rmdir() if not list(b.iterdir()) else None
        for child in b.iterdir():
            child.unlink()
        b.rmdir()
        report = fresh.import_backup(self.file)
        self.assertEqual(report.restored, [str(a)])
        self.assertEqual(report.missing, [str(b)])
        self.assertIsNone(fresh.store.get(str(b)))

    def test_an_icon_changed_elsewhere_is_left_alone(self):
        a, _b = self.fill()
        self.service.export_backup(self.file)
        dotdirectory.write_icon(a, "file:///usr/share/other.png")
        report = self.service.import_backup(self.file)
        self.assertIn(str(a), report.kept)
        self.assertEqual(dotdirectory.read_icon(a), "file:///usr/share/other.png")

    def test_restoring_twice_gives_the_same_result(self):
        self.fill()
        self.service.export_backup(self.file)
        fresh = self.make_service("other")
        fresh.import_backup(self.file)
        first = {key: entry.digest for key, entry in fresh.store.all().items()}
        report = fresh.import_backup(self.file)
        self.assertEqual({key: entry.digest for key, entry in fresh.store.all().items()}, first)
        self.assertEqual(report.styles_added, 0)
        self.assertEqual(len(fresh.styles()), 1)
        self.assertEqual(len(fresh.picker_memory().favorites), 1)

    def test_one_bad_folder_does_not_stop_the_others(self):
        a, b = self.fill()
        self.service.export_backup(self.file)
        document = json.loads(self.file.read_text())
        document["folders"][0]["settings"]["size"] = 99
        self.file.write_text(json.dumps(document))
        fresh = self.make_service("other")
        report = fresh.import_backup(self.file)
        self.assertEqual(len(report.restored), 1)
        self.assertEqual(len(report.failed), 1)

    def test_a_missing_pictogram_file_fails_only_that_folder(self):
        folder = self.make_folder("c")
        picture = self.root / "pic.svg"
        picture.write_text(glyph_svg())
        self.service.apply(folder, Settings(glyph_file=str(picture)))
        self.service.export_backup(self.file)
        picture.unlink()
        fresh = self.make_service("other")
        report = fresh.import_backup(self.file)
        self.assertEqual(list(report.failed), [str(folder)])

    def test_the_remembered_appearance_is_not_overwritten(self):
        self.fill()
        self.service.export_backup(self.file)
        fresh = self.make_service("other")
        fresh.remember_appearance(Settings(glyph_name="x", size=2.0))
        fresh.import_backup(self.file)
        self.assertEqual(fresh.remembered_appearance()["size"], 2.0)

    def test_current_styles_and_favorites_are_kept_and_completed(self):
        self.fill()
        self.service.export_backup(self.file)
        fresh = self.make_service("other")
        fresh.save_style("Mine", {"size": 0.8})
        fresh.set_favorite("other-symbolic", True)
        fresh.import_backup(self.file)
        self.assertEqual(sorted(style.name for style in fresh.styles()), ["Big", "Mine"])
        self.assertEqual(fresh.picker_memory().favorites[0], "other-symbolic")
        self.assertIn("heart-symbolic", fresh.picker_memory().favorites)

    def test_invalid_styles_are_counted_as_rejected(self):
        self.fill()
        self.service.export_backup(self.file)
        document = json.loads(self.file.read_text())
        document["styles"].append({"name": "", "appearance": {}})
        document["styles"].append("nonsense")
        self.file.write_text(json.dumps(document))
        report = self.make_service("other").import_backup(self.file)
        self.assertEqual((report.styles_added, report.styles_rejected), (1, 2))


class ParseTests(BackupTestCase):
    def import_text(self, text):
        self.file.write_text(text)
        return self.service.import_backup(self.file)

    def test_files_that_are_not_backups_are_refused(self):
        for text in ("", "not json", "[]", '{"format": "other", "version": 1}', '{"format": "custicon-backup"}', '{"format": "custicon-backup", "version": "1"}', '{"format": "custicon-backup", "version": true}', '{"format": "custicon-backup", "version": 0}', '{"format": "custicon-backup", "version": 1, "folders": "x"}'):
            with self.subTest(text=text), self.assertRaises(StoreError):
                self.import_text(text)

    def test_a_newer_backup_is_refused_with_its_own_message(self):
        with self.assertRaisesRegex(StoreError, "newer version"):
            self.import_text('{"format": "custicon-backup", "version": 2, "folders": []}')

    def test_a_huge_file_is_refused(self):
        with self.assertRaises(StoreError):
            backup.parse(b" " * (backup.MAX_BYTES + 1))

    def test_too_many_folders_are_refused(self):
        document = {"format": "custicon-backup", "version": 1, "folders": [{}] * (backup.MAX_FOLDERS + 1)}
        with self.assertRaises(StoreError):
            backup.parse(json.dumps(document).encode())

    def test_a_missing_file_is_a_clear_error(self):
        with self.assertRaises(StoreError):
            self.service.import_backup(self.root / "absent.json")

    def test_a_binary_file_is_refused(self):
        self.file.write_bytes(b"\xff\xfe\x00\x00")
        with self.assertRaises(StoreError):
            self.service.import_backup(self.file)

    def test_a_minimal_backup_restores_nothing_and_changes_nothing(self):
        report = self.import_text('{"format": "custicon-backup", "version": 1}')
        self.assertEqual((report.restored, report.kept, report.missing, report.failed), ([], [], [], {}))

    def test_garbage_items_are_ignored(self):
        items = ["x", {"folder": 3, "settings": {}}, {"folder": "", "settings": {}}, {"folder": "/a\0b", "settings": {}}, {"folder": "/x", "settings": "s"}]
        self.assertEqual(backup.folder_items({"folders": items}), [])

    def test_well_formed_items_are_kept_in_order(self):
        items = [{"folder": "/a", "settings": {"x": 1}}, {"folder": "/b", "settings": {}}]
        self.assertEqual(backup.folder_items({"folders": items}), [("/a", {"x": 1}), ("/b", {})])

    def test_a_non_folder_path_is_reported_as_missing(self):
        document = {"format": "custicon-backup", "version": 1, "folders": [{"folder": str(self.workspace / "none"), "settings": {"glyph_name": "star-symbolic"}}]}
        report = self.import_text(json.dumps(document))
        self.assertEqual(report.missing, [str(self.workspace / "none")])

    def test_the_size_limit_is_exact_and_reported_as_such(self):
        header = b'{"format": "custicon-backup", "version": 1}'
        exact = header + b" " * (backup.MAX_BYTES - len(header))
        self.assertEqual(backup.parse(exact)["version"], 1)
        with self.assertRaisesRegex(StoreError, "too large"):
            backup.parse(exact + b" ")
        self.file.write_bytes(exact + b" ")
        with self.assertRaisesRegex(StoreError, "too large"):
            self.service.import_backup(self.file)
        self.file.write_bytes(exact)
        self.assertEqual(self.service.import_backup(self.file).restored, [])

    def test_the_folder_limit_is_exact(self):
        ok = {"format": "custicon-backup", "version": 1, "folders": [{}] * backup.MAX_FOLDERS}
        self.assertEqual(backup.parse(json.dumps(ok).encode())["version"], 1)

    def test_accents_are_written_as_they_are(self):
        folder = self.make_folder("Écoles")
        self.service.apply(folder, STAR)
        self.service.export_backup(self.file)
        self.assertIn("Écoles", self.file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
