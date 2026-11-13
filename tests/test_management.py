import os
import unittest
from pathlib import Path
from unittest import mock

from custicon import service as service_module
from custicon.errors import FolderError, MetadataError, StoreError
from custicon.metadata import read_custom_icon, write_custom_icon
from custicon.service import RESET_RESTORED, Settings
from custicon.store import Store

from .support import glyph_svg, tree_fingerprint
from .test_service import STAR, USER_ICON, ServiceTestCase


class RelocateTests(ServiceTestCase):
    def moved(self, name="docs", new="renamed"):
        folder = self.make_folder(name)
        entry = self.service.apply(folder, STAR)
        target = self.workspace / new
        folder.rename(target)
        self.created.append(target)
        return entry, target

    def test_a_renamed_folder_gets_its_icon_back_at_the_new_place(self):
        entry, target = self.moved()
        moved = self.service.relocate(entry.folder, target)
        self.assertEqual(moved.folder, str(target.resolve()))
        self.assertEqual(moved.digest, entry.digest)
        self.assertEqual(read_custom_icon(target), self.store.icon_uri(entry.digest))
        self.assertIsNone(self.store.get(entry.folder))
        self.assertEqual(self.store.get(moved.folder), moved)

    def test_settings_and_theme_are_kept(self):
        entry, target = self.moved()
        moved = self.service.relocate(entry.folder, target)
        self.assertEqual((moved.settings, moved.theme, moved.color), (entry.settings, entry.theme, entry.color))

    def test_the_metadata_is_not_rewritten_when_it_already_moved_with_the_folder(self):
        entry, target = self.moved()
        write_custom_icon(target, self.store.icon_uri(entry.digest))
        with mock.patch.object(service_module, "write_custom_icon") as write:
            self.service.relocate(entry.folder, target)
        write.assert_not_called()

    def test_the_original_icon_is_still_restored_after_a_move_carrying_the_metadata(self):
        folder = self.make_folder("docs")
        write_custom_icon(folder, USER_ICON)
        entry = self.service.apply(folder, STAR)
        target = self.workspace / "renamed"
        folder.rename(target)
        self.created.append(target)
        write_custom_icon(target, self.store.icon_uri(entry.digest))
        moved = self.service.relocate(entry.folder, target)
        self.assertEqual(moved.previous, USER_ICON)
        self.assertEqual(self.service.reset(target), RESET_RESTORED)
        self.assertEqual(read_custom_icon(target), USER_ICON)

    def test_an_icon_the_new_folder_already_had_becomes_its_original_icon(self):
        entry, target = self.moved()
        write_custom_icon(target, USER_ICON)
        moved = self.service.relocate(entry.folder, target)
        self.assertEqual(moved.previous, USER_ICON)
        self.service.reset(target)
        self.assertEqual(read_custom_icon(target), USER_ICON)

    def test_a_folder_without_any_icon_has_no_original_icon(self):
        entry, target = self.moved()
        self.assertIsNone(self.service.relocate(entry.folder, target).previous)

    def test_an_unknown_entry_is_refused(self):
        with self.assertRaises(FolderError):
            self.service.relocate(str(self.workspace / "nowhere"), self.make_folder("x"))

    def test_a_folder_that_still_exists_is_refused(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        other = self.make_folder("other")
        with self.assertRaises(FolderError):
            self.service.relocate(entry.folder, other)
        self.assertIsNotNone(self.store.get(entry.folder))

    def test_a_folder_that_is_already_customized_is_refused(self):
        entry, target = self.moved()
        self.service.apply(target, Settings(glyph_name="heart-symbolic"))
        with self.assertRaises(FolderError):
            self.service.relocate(entry.folder, target)
        self.assertIsNotNone(self.store.get(entry.folder))

    def test_something_that_is_not_a_folder_is_refused(self):
        entry, _target = self.moved()
        with self.assertRaises(FolderError):
            self.service.relocate(entry.folder, self.workspace / "missing")

    def test_a_name_that_is_not_utf8_is_refused(self):
        entry, _target = self.moved()
        bad = os.fsencode(self.workspace) + b"/caf\xe9"
        os.mkdir(bad)
        self.addCleanup(os.rmdir, bad)
        with self.assertRaises(FolderError):
            self.service.relocate(entry.folder, os.fsdecode(bad))

    def test_a_metadata_failure_leaves_the_old_entry_in_place(self):
        entry, target = self.moved()
        with mock.patch.object(service_module, "write_custom_icon", side_effect=MetadataError("boom")):
            with self.assertRaises(MetadataError):
                self.service.relocate(entry.folder, target)
        self.assertEqual(self.store.get(entry.folder), entry)
        self.assertIsNone(self.store.get(str(target.resolve())))

    def test_a_registry_failure_leaves_the_metadata_alone(self):
        entry, target = self.moved()
        with mock.patch.object(Store, "put", side_effect=StoreError("disk full")):
            with self.assertRaises(StoreError):
                self.service.relocate(entry.folder, target)
        self.assertIsNone(read_custom_icon(target))


class ResetAllTests(ServiceTestCase):
    def test_every_icon_is_removed_and_the_original_ones_come_back(self):
        plain, with_icon = self.make_folder("a"), self.make_folder("b")
        write_custom_icon(with_icon, USER_ICON)
        self.service.apply(plain, STAR)
        self.service.apply(with_icon, STAR)
        report = self.service.reset_all()
        self.assertEqual(len(report.restored), 2)
        self.assertIsNone(read_custom_icon(plain))
        self.assertEqual(read_custom_icon(with_icon), USER_ICON)
        self.assertEqual(self.store.all(), {})

    def test_an_icon_changed_from_nautilus_is_kept(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        write_custom_icon(folder, USER_ICON)
        report = self.service.reset_all()
        self.assertEqual((len(report.kept), len(report.restored)), (1, 0))
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_a_missing_folder_is_forgotten(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()
        report = self.service.reset_all()
        self.assertEqual(len(report.missing), 1)
        self.assertEqual(self.store.all(), {})

    def test_a_failure_does_not_stop_the_others(self):
        first, second = self.make_folder("a"), self.make_folder("b")
        self.service.apply(first, STAR)
        entry = self.service.apply(second, STAR)
        real = self.service.reset

        def flaky(folder):
            if str(folder) == entry.folder:
                raise MetadataError("no daemon")
            return real(folder)

        with mock.patch.object(self.service, "reset", side_effect=flaky):
            report = self.service.reset_all()
        self.assertEqual(len(report.restored), 1)
        self.assertEqual(list(report.failed), [entry.folder])
        self.assertIsNotNone(self.store.get(entry.folder))

    def test_an_empty_registry_gives_an_empty_report(self):
        report = self.service.reset_all()
        self.assertEqual((report.restored, report.kept, report.missing, report.failed), ([], [], [], {}))

    def test_the_content_of_the_folders_is_never_modified(self):
        folder = self.make_folder()
        before = tree_fingerprint(folder)
        self.service.apply(folder, STAR)
        self.service.reset_all()
        self.assertEqual(tree_fingerprint(folder), before)

    def test_an_unreadable_registry_is_a_store_error(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.registry_path.write_text("{broken")
        with self.assertRaises(StoreError):
            self.service.reset_all()


class UndoTests(ServiceTestCase):
    def test_a_removed_icon_comes_back_with_its_settings(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, Settings(glyph_name="star-symbolic", color="#ff8800", size=1.5))
        snapshot = self.service.snapshot(entry.folder)
        self.service.reset(folder)
        self.assertIsNone(read_custom_icon(folder))
        restored = self.service.restore(snapshot)
        self.assertEqual(restored.settings["size"], 1.5)
        self.assertEqual(restored.digest, entry.digest)
        self.assertEqual(read_custom_icon(folder), self.store.icon_uri(entry.digest))

    def test_the_original_icon_is_still_restored_after_an_undo(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        entry = self.service.apply(folder, STAR)
        snapshot = self.service.snapshot(entry.folder)
        self.service.reset(folder)
        self.assertEqual(read_custom_icon(folder), USER_ICON)
        self.service.restore(snapshot)
        self.assertEqual(self.service.store.get(entry.folder).previous, USER_ICON)
        self.service.reset(folder)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_a_pictogram_file_survives_removal_and_undo_even_if_the_original_is_gone(self):
        folder = self.make_folder()
        source = self.root / "mine.svg"
        source.write_text(glyph_svg("#123456"))
        entry = self.service.apply(folder, Settings(glyph_file=str(source), color="#ff8800"))
        snapshot = self.service.snapshot(entry.folder)
        self.service.reset(folder)
        source.unlink()
        self.assertFalse(self.store.glyph_path(entry.settings["glyph_copy"]).exists())
        restored = self.service.restore(snapshot)
        self.assertTrue(self.store.glyph_path(restored.settings["glyph_copy"]).exists())
        self.assertEqual(restored.digest, entry.digest)

    def test_a_text_pictogram_can_be_restored(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, Settings(glyph_text="Z", color="#ff8800"))
        snapshot = self.service.snapshot(entry.folder)
        self.service.reset(folder)
        with mock.patch.object(service_module, "text_to_svg", side_effect=AssertionError("font used")):
            restored = self.service.restore(snapshot)
        self.assertEqual(restored.digest, entry.digest)

    def test_there_is_no_snapshot_of_an_unknown_folder(self):
        self.assertIsNone(self.service.snapshot(str(self.workspace / "nowhere")))

    def test_a_missing_copy_gives_a_snapshot_without_pictogram_data(self):
        folder = self.make_folder()
        source = self.root / "mine.svg"
        source.write_text(glyph_svg())
        entry = self.service.apply(folder, Settings(glyph_file=str(source)))
        self.store.glyph_path(entry.settings["glyph_copy"]).unlink()
        self.assertIsNone(self.service.snapshot(entry.folder).glyph)

    def test_an_unreadable_copy_gives_a_snapshot_without_pictogram_data(self):
        folder = self.make_folder()
        source = self.root / "mine.svg"
        source.write_text(glyph_svg())
        entry = self.service.apply(folder, Settings(glyph_file=str(source)))
        with mock.patch.object(service_module, "read_icon_bytes", side_effect=service_module.InvalidIconError("too large")):
            snapshot = self.service.snapshot(entry.folder)
        self.assertIsNone(snapshot.glyph)
        self.assertEqual(snapshot.entry, entry)

    def test_a_snapshot_of_a_named_icon_has_no_pictogram_data(self):
        entry = self.service.apply(self.make_folder(), STAR)
        self.assertIsNone(self.service.snapshot(entry.folder).glyph)


class CleanupTests(ServiceTestCase):
    def test_orphan_images_and_temporary_files_are_removed(self):
        entry = self.service.apply(self.make_folder(), STAR)
        orphan = self.store.save_icon("<svg>orphan</svg>")
        stray = [self.store.root / "registry.json.tmp", self.store.icons_dir / "x.svg.tmp"]
        for path in stray:
            path.write_text("partial")
        removed = self.service.cleanup()
        self.assertEqual(removed, [orphan])
        self.assertTrue(self.store.icon_path(entry.digest).exists())
        self.assertFalse(any(path.exists() for path in stray))

    def test_nothing_is_removed_when_the_registry_is_missing(self):
        digest = self.store.save_icon("<svg>other</svg>")
        self.assertEqual(self.service.cleanup(), [])
        self.assertTrue(self.store.icon_path(digest).exists())

    def test_an_unreadable_registry_does_not_raise_and_keeps_images(self):
        digest = self.store.save_icon("<svg>other</svg>")
        self.store.registry_path.write_text("{broken")
        self.assertEqual(self.service.cleanup(), [])
        self.assertTrue(self.store.icon_path(digest).exists())

    def test_temporary_files_are_removed_even_when_the_registry_is_unreadable(self):
        self.store.save_icon("<svg/>")
        self.store.registry_path.write_text("{broken")
        stray = self.store.icons_dir / "y.svg.tmp"
        stray.write_text("partial")
        self.service.cleanup()
        self.assertFalse(stray.exists())


if __name__ == "__main__":
    unittest.main()
