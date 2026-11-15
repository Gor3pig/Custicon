import json
import os
import unittest
from pathlib import Path
from unittest import mock

import gi

from custicon import dotdirectory, environment, themes
from custicon.errors import MetadataError
from custicon import service as service_module
from custicon.metadata import read_custom_icon
from custicon.service import (
    RESET_KEPT_USER_ICON,
    RESET_RESTORED,
    STATUS_FOLDER_MISSING,
    STATUS_ICON_CHANGED,
    STATUS_ICON_FILE_MISSING,
    STATUS_OK,
    Service,
    Settings,
)
from custicon.store import Entry, Store

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme, tree_fingerprint

STAR = Settings(glyph_name="star-symbolic")
DOLPHIN_FILE = "[Dolphin]\nTimestamp=2026,11,15,1,2,3.456\nViewMode=1\n"


class DesktopDetectionTests(unittest.TestCase):
    def backend(self, value, probe=lambda: False):
        return environment.detect_backend({environment.DESKTOP_VARIABLE: value}, probe)

    def test_kde_uses_the_directory_file(self):
        self.assertEqual(self.backend("KDE"), environment.BACKEND_DIRECTORY)

    def test_lxqt_uses_the_directory_file(self):
        self.assertEqual(self.backend("LXQt"), environment.BACKEND_DIRECTORY)

    def test_the_gtk_desktops_use_gvfs(self):
        for value in ("GNOME", "ubuntu:GNOME", "Budgie:GNOME", "X-Cinnamon", "MATE", "XFCE", "LXDE", "Pantheon", "COSMIC", "Unity", "Deepin"):
            with self.subTest(value=value):
                self.assertEqual(self.backend(value), environment.BACKEND_GVFS)

    def test_the_name_is_not_case_sensitive(self):
        self.assertEqual(self.backend("kde"), environment.BACKEND_DIRECTORY)
        self.assertEqual(self.backend("gnome"), environment.BACKEND_GVFS)

    def test_a_mix_of_both_families_is_safe_and_uses_gvfs(self):
        self.assertEqual(self.backend("KDE:GNOME"), environment.BACKEND_GVFS)

    def test_an_unknown_desktop_uses_gvfs_without_asking_the_portal(self):
        probe = mock.Mock(return_value=True)
        self.assertEqual(self.backend("Enlightenment", probe), environment.BACKEND_GVFS)
        probe.assert_not_called()

    def test_without_the_variable_the_portal_decides(self):
        self.assertEqual(environment.detect_backend({}, lambda: True), environment.BACKEND_DIRECTORY)
        self.assertEqual(environment.detect_backend({}, lambda: False), environment.BACKEND_GVFS)

    def test_an_empty_variable_counts_as_missing(self):
        self.assertEqual(self.backend("", lambda: True), environment.BACKEND_DIRECTORY)
        self.assertEqual(self.backend(" : ", lambda: False), environment.BACKEND_GVFS)

    def test_the_portal_is_not_asked_when_the_variable_says_kde(self):
        probe = mock.Mock(return_value=False)
        self.assertTrue(environment.is_kde({environment.DESKTOP_VARIABLE: "KDE"}, probe))
        probe.assert_not_called()

    def test_a_gtk_desktop_is_not_kde_even_if_the_portal_says_so(self):
        self.assertFalse(environment.is_kde({environment.DESKTOP_VARIABLE: "GNOME"}, lambda: True))

    def test_the_colon_separated_list_is_split(self):
        self.assertEqual(environment.current_desktops({environment.DESKTOP_VARIABLE: "a:B: c "}), ("A", "B", "C"))

    def test_no_environment_gives_no_desktop(self):
        self.assertEqual(environment.current_desktops({}), ())


class UnsupportedDesktopTests(unittest.TestCase):
    def manager(self, value):
        return environment.unsupported_file_manager({environment.DESKTOP_VARIABLE: value})

    def test_xfce_and_lxde_are_reported_with_their_file_manager(self):
        self.assertEqual(self.manager("XFCE"), "Thunar")
        self.assertEqual(self.manager("LXDE"), "PCManFM")
        self.assertEqual(self.manager("COSMIC"), "COSMIC Files")

    def test_the_supported_desktops_are_never_reported(self):
        for value in ("GNOME", "ubuntu:GNOME", "KDE", "LXQt", "X-Cinnamon", "MATE", "Budgie:GNOME", "Unity"):
            with self.subTest(value=value):
                self.assertIsNone(self.manager(value))

    def test_an_unknown_or_missing_desktop_is_not_reported(self):
        self.assertIsNone(self.manager("Enlightenment"))
        self.assertIsNone(self.manager(""))
        self.assertIsNone(environment.unsupported_file_manager({}))

    def test_a_supported_desktop_wins_over_an_unsupported_one(self):
        self.assertIsNone(self.manager("XFCE:GNOME"))

    def test_the_file_manager_names(self):
        names = {"KDE": "Dolphin", "GNOME": "Nautilus", "X-Cinnamon": "Nemo", "MATE": "Caja", "XFCE": "Thunar", "LXQt": "PCManFM-Qt", "LXDE": "PCManFM", "COSMIC": "COSMIC Files", "Budgie:GNOME": "Nautilus"}
        for value, name in names.items():
            with self.subTest(value=value):
                self.assertEqual(environment.file_manager_name({environment.DESKTOP_VARIABLE: value}), name)

    def test_an_unknown_desktop_has_no_file_manager_name(self):
        self.assertIsNone(environment.file_manager_name({environment.DESKTOP_VARIABLE: "Enlightenment"}))

    def test_without_a_variable_a_kde_portal_means_dolphin(self):
        self.assertEqual(environment.file_manager_name({}, lambda: True), "Dolphin")
        self.assertIsNone(environment.file_manager_name({}, lambda: False))


class KdeDetectionTests(unittest.TestCase):
    def kde(self, value, probe=lambda: True):
        return environment.is_kde({environment.DESKTOP_VARIABLE: value}, probe)

    def test_only_a_kde_desktop_is_kde(self):
        self.assertTrue(self.kde("KDE", lambda: False))
        self.assertFalse(self.kde("XFCE"))
        self.assertFalse(self.kde("Enlightenment"))
        self.assertFalse(self.kde("LXQt"))

    def test_a_kde_list_with_a_gtk_desktop_is_not_kde(self):
        self.assertFalse(self.kde("GNOME:KDE"))
        self.assertTrue(self.kde("KDE:Unity8"))

    def test_the_portal_probe_is_asked_once_and_remembered(self):
        with mock.patch.object(environment, "_kde_portal", []):
            with mock.patch.object(themes, "portal_has_kde", side_effect=[True, False]) as probe:
                self.assertTrue(environment.kde_portal_present())
                self.assertTrue(environment.kde_portal_present())
            self.assertEqual(probe.call_count, 1)
        with mock.patch.object(environment, "_kde_portal", []):
            with mock.patch.object(themes, "portal_has_kde", return_value=False):
                self.assertFalse(environment.kde_portal_present())


class KdeThemeTests(unittest.TestCase):
    def test_a_kde_desktop_reads_its_own_icon_theme_first(self):
        with mock.patch.object(environment, "is_kde", return_value=True), mock.patch.object(themes, "portal_kde_icon_theme", return_value="breeze"), mock.patch.object(themes, "portal_icon_theme", return_value="Adwaita"):
            self.assertEqual(themes.active_icon_theme(), "breeze")

    def test_a_kde_desktop_without_the_value_falls_back_to_the_usual_chain(self):
        with mock.patch.object(environment, "is_kde", return_value=True), mock.patch.object(themes, "portal_kde_icon_theme", return_value=None), mock.patch.object(themes, "portal_icon_theme", return_value="Papirus"):
            self.assertEqual(themes.active_icon_theme(), "Papirus")

    def test_another_desktop_never_asks_for_the_kde_theme(self):
        with mock.patch.object(environment, "is_kde", return_value=False), mock.patch.object(themes, "portal_kde_icon_theme", side_effect=AssertionError("asked")), mock.patch.object(themes, "portal_icon_theme", return_value="Papirus"):
            self.assertEqual(themes.active_icon_theme(), "Papirus")

    def test_the_kde_theme_is_read_from_the_icons_group(self):
        with mock.patch.object(themes, "read_portal_setting", return_value=mock.Mock(get_type_string=lambda: "s", get_string=lambda: "breeze-dark")) as read:
            self.assertEqual(themes.portal_kde_icon_theme(), "breeze-dark")
        read.assert_called_once_with("org.kde.kdeglobals.Icons", "Theme")

    def test_a_missing_or_wrong_kde_theme_is_ignored(self):
        with mock.patch.object(themes, "read_portal_setting", return_value=None):
            self.assertIsNone(themes.portal_kde_icon_theme())
        with mock.patch.object(themes, "read_portal_setting", return_value=mock.Mock(get_type_string=lambda: "i")):
            self.assertIsNone(themes.portal_kde_icon_theme())
        with mock.patch.object(themes, "read_portal_setting", return_value=mock.Mock(get_type_string=lambda: "s", get_string=lambda: "")):
            self.assertIsNone(themes.portal_kde_icon_theme())

    def test_the_portal_error_means_no_kde(self):
        from gi.repository import GLib

        with mock.patch.object(themes.Gio, "bus_get_sync", side_effect=GLib.Error("no bus")):
            self.assertFalse(themes.portal_has_kde())


class EntryDocumentTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def entry(self, **extra):
        return Entry("/home/u/a", "0" * 32, "Adwaita", "#438de6", {"glyph_name": "star-symbolic"}, None, **extra)

    def saved(self):
        return json.loads(self.store.registry_path.read_text())["entries"][0]

    def test_a_gvfs_entry_is_written_exactly_as_before(self):
        self.store.put(self.entry())
        self.assertEqual(sorted(self.saved()), ["color", "digest", "folder", "previous", "settings", "theme"])

    def test_a_directory_entry_keeps_its_backend(self):
        self.store.put(self.entry(backend="directory", directory_created=True))
        loaded = Store(self.root).get("/home/u/a")
        self.assertEqual((loaded.backend, loaded.directory_created), ("directory", True))

    def test_the_created_flag_is_only_written_when_true(self):
        self.store.put(self.entry(backend="directory"))
        self.assertNotIn("directory_created", self.saved())
        self.assertEqual(self.saved()["backend"], "directory")

    def test_an_old_registry_loads_with_the_defaults(self):
        self.store.put(self.entry())
        loaded = Store(self.root).get("/home/u/a")
        self.assertEqual((loaded.backend, loaded.directory_created), ("gvfs", False))

    def test_an_unknown_backend_makes_the_registry_unreadable(self):
        self.store.put(self.entry())
        document = json.loads(self.store.registry_path.read_text())
        document["entries"][0]["backend"] = "other"
        self.store.registry_path.write_text(json.dumps(document))
        self.store.registry_path.with_name("registry.json.bak").unlink(missing_ok=True)
        with self.assertRaises(Exception):
            Store(self.root).all()

    def test_a_non_boolean_created_flag_is_refused(self):
        self.store.put(self.entry())
        document = json.loads(self.store.registry_path.read_text())
        document["entries"][0]["directory_created"] = "yes"
        self.store.registry_path.write_text(json.dumps(document))
        self.store.registry_path.with_name("registry.json.bak").unlink(missing_ok=True)
        with self.assertRaises(Exception):
            Store(self.root).all()


class DirectoryServiceTestCase(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.themes = self.root / "themes"
        self.themes.mkdir()
        glyphs = {"star-symbolic": glyph_svg(), "heart-symbolic": glyph_svg()}
        make_theme(self.themes, "Blue", folder=folder_svg("#3366cc"), glyphs=glyphs)
        make_theme(self.themes, "Green", folder=folder_svg("#33cc66"), glyphs=glyphs)
        self.theme = "Blue"
        self.store = Store(self.root / "data")
        self.service = Service(self.store, theme_name=lambda: self.theme, search_dirs=[str(self.themes)], backend="directory")
        self.workspace = self.root / "folders"
        self.workspace.mkdir()

    def make_folder(self, name="docs"):
        folder = self.workspace / name
        folder.mkdir()
        (folder / "note.txt").write_text("keep me")
        return folder

    def icon_of(self, folder):
        return dotdirectory.read_icon(folder)


class DirectoryApplyTests(DirectoryServiceTestCase):
    def test_apply_writes_the_directory_file_with_the_stored_icon(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.assertEqual(self.icon_of(folder), self.store.icon_uri(entry.digest))
        self.assertIn(f"Icon={self.store.icon_path(entry.digest)}", (folder / ".directory").read_text())

    def test_apply_does_not_use_the_gvfs_attribute(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        with mock.patch("custicon.service.write_custom_icon", side_effect=AssertionError("gvfs used")):
            self.service.apply(folder, Settings(glyph_name="heart-symbolic"))

    def test_the_entry_records_the_backend_and_the_creation_of_the_file(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.assertEqual((entry.backend, entry.directory_created), ("directory", True))
        self.assertEqual(self.store.get(str(folder)), entry)

    def test_an_existing_file_is_not_recorded_as_created(self):
        folder = self.make_folder()
        (folder / ".directory").write_text(DOLPHIN_FILE)
        entry = self.service.apply(folder, STAR)
        self.assertFalse(entry.directory_created)

    def test_the_settings_of_the_file_manager_are_kept(self):
        folder = self.make_folder()
        (folder / ".directory").write_text(DOLPHIN_FILE)
        self.service.apply(folder, STAR)
        self.assertTrue((folder / ".directory").read_text().startswith(DOLPHIN_FILE))

    def test_the_other_files_of_the_folder_are_never_touched(self):
        folder = self.make_folder()
        (folder / "sub").mkdir()
        (folder / "sub" / "x.txt").write_text("x")
        before = {path.name: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
        self.service.apply(folder, STAR)
        after = {path.name: path.read_bytes() for path in folder.rglob("*") if path.is_file() and path.name != ".directory"}
        self.assertEqual(after, before)

    def test_applying_again_replaces_the_icon_and_keeps_one_line(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        second = self.service.apply(folder, Settings(glyph_name="heart-symbolic"))
        text = (folder / ".directory").read_text()
        self.assertEqual(text.count("Icon="), 1)
        self.assertEqual(self.icon_of(folder), self.store.icon_uri(second.digest))
        self.assertTrue(second.directory_created)

    def test_a_previous_icon_of_the_file_is_remembered(self):
        folder = self.make_folder()
        (folder / ".directory").write_text("[Desktop Entry]\nIcon=folder-red\n")
        entry = self.service.apply(folder, STAR)
        self.assertEqual(entry.previous, "folder-red")

    def test_an_unwritable_folder_fails_cleanly_and_the_registry_is_unchanged(self):
        folder = self.make_folder()
        with mock.patch.object(dotdirectory, "write_icon", side_effect=MetadataError("cannot write .directory: Permission denied")):
            with self.assertRaises(MetadataError):
                self.service.apply(folder, STAR)
        self.assertIsNone(self.store.get(str(folder)))
        self.assertEqual(list(self.store.icons_dir.glob("*.svg")) if self.store.icons_dir.exists() else [], [])

    def test_a_failure_during_a_second_apply_restores_the_first_one(self):
        folder = self.make_folder()
        first = self.service.apply(folder, STAR)
        with mock.patch.object(dotdirectory, "write_icon", side_effect=[MetadataError("no"), dotdirectory.Written(False, False)]):
            with self.assertRaises(MetadataError):
                self.service.apply(folder, Settings(glyph_name="heart-symbolic"))
        self.assertEqual(self.store.get(str(folder)), first)

    def test_a_corrupt_file_is_not_overwritten(self):
        folder = self.make_folder()
        (folder / ".directory").mkdir()
        with self.assertRaises(MetadataError):
            self.service.apply(folder, STAR)
        self.assertTrue((folder / ".directory").is_dir())

    def test_apply_many_reports_the_failures_and_continues(self):
        good, bad = self.make_folder("good"), self.make_folder("bad")
        (bad / ".directory").mkdir()
        report = self.service.apply_many([bad, good], STAR)
        self.assertEqual(report.applied, [str(good)])
        self.assertEqual(list(report.failed), [str(bad)])


class DirectoryResetTests(DirectoryServiceTestCase):
    def test_removing_deletes_the_file_that_was_created(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        self.assertEqual(self.service.reset(folder), RESET_RESTORED)
        self.assertFalse((folder / ".directory").exists())
        self.assertIsNone(self.store.get(str(folder)))

    def test_removing_gives_back_the_exact_original_file(self):
        folder = self.make_folder()
        (folder / ".directory").write_bytes(DOLPHIN_FILE.encode())
        self.service.apply(folder, STAR)
        self.service.reset(folder)
        self.assertEqual((folder / ".directory").read_bytes(), DOLPHIN_FILE.encode())

    def test_removing_restores_a_previous_icon(self):
        folder = self.make_folder()
        (folder / ".directory").write_text("[Desktop Entry]\nIcon=folder-red\n")
        self.service.apply(folder, STAR)
        self.service.reset(folder)
        self.assertEqual((folder / ".directory").read_text(), "[Desktop Entry]\nIcon=folder-red\n")

    def test_an_icon_changed_by_the_user_is_kept(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        (folder / ".directory").write_text("[Desktop Entry]\nIcon=folder-blue\n")
        self.assertEqual(self.service.reset(folder), RESET_KEPT_USER_ICON)
        self.assertEqual(self.icon_of(folder), "folder-blue")

    def test_the_tree_is_exactly_as_before_after_a_full_cycle(self):
        folder = self.make_folder()
        before = tree_fingerprint(folder)
        self.service.apply(folder, STAR)
        self.service.reset(folder)
        self.assertEqual(tree_fingerprint(folder), before)

    def test_the_cycle_with_existing_settings_is_exact(self):
        folder = self.make_folder()
        (folder / ".directory").write_text(DOLPHIN_FILE)
        before = tree_fingerprint(folder)
        self.service.apply(folder, STAR)
        self.service.reset(folder)
        self.assertEqual(tree_fingerprint(folder), before)

    def test_reset_all_removes_every_icon(self):
        folders = [self.make_folder(name) for name in ("a", "b", "c")]
        self.service.apply_many(folders, STAR)
        report = self.service.reset_all()
        self.assertEqual(len(report.restored), 3)
        self.assertTrue(all(not (folder / ".directory").exists() for folder in folders))

    def test_undoing_a_removal_brings_the_icon_back_with_its_backend(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        snapshot = self.service.snapshot(str(folder))
        self.service.reset(folder)
        restored = self.service.restore(snapshot)
        self.assertEqual(restored.backend, "directory")
        self.assertEqual(self.icon_of(folder), self.store.icon_uri(restored.digest))

    def test_forgetting_leaves_the_file_alone(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        before = (folder / ".directory").read_bytes()
        self.service.forget(folder)
        self.assertEqual((folder / ".directory").read_bytes(), before)


class DirectoryStatusTests(DirectoryServiceTestCase):
    def status(self, folder):
        return next(item.status for item in self.service.customized() if item.entry.folder == str(folder))

    def test_a_fresh_icon_is_ok(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        self.assertEqual(self.status(folder), STATUS_OK)

    def test_a_deleted_folder_is_reported(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()
        self.assertEqual(self.status(folder), STATUS_FOLDER_MISSING)

    def test_a_changed_icon_is_reported(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        (folder / ".directory").write_text("[Desktop Entry]\nIcon=folder-blue\n")
        self.assertEqual(self.status(folder), STATUS_ICON_CHANGED)

    def test_a_deleted_directory_file_is_reported_as_a_changed_icon(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        (folder / ".directory").unlink()
        self.assertEqual(self.status(folder), STATUS_ICON_CHANGED)

    def test_a_missing_image_is_reported(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.store.icon_path(entry.digest).unlink()
        self.assertEqual(self.status(folder), STATUS_ICON_FILE_MISSING)

    def test_a_theme_change_makes_the_icon_outdated(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        self.theme = "Green"
        self.assertEqual(self.service.outdated(), [str(folder)])

    def test_regeneration_rewrites_the_file(self):
        folder = self.make_folder()
        first = self.service.apply(folder, STAR)
        self.theme = "Green"
        report = self.service.regenerate_all()
        self.assertEqual(report.updated, [str(folder)])
        second = self.store.get(str(folder))
        self.assertNotEqual(first.digest, second.digest)
        self.assertEqual(self.icon_of(folder), self.store.icon_uri(second.digest))
        self.assertEqual((second.backend, second.directory_created), ("directory", True))

    def test_regeneration_skips_a_folder_whose_icon_was_changed(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        (folder / ".directory").write_text("[Desktop Entry]\nIcon=folder-blue\n")
        self.theme = "Green"
        report = self.service.regenerate_all()
        self.assertEqual(report.skipped, {str(folder): STATUS_ICON_CHANGED})
        self.assertEqual(self.icon_of(folder), "folder-blue")

    def test_a_moved_folder_can_be_found_again(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        moved = self.workspace / "moved"
        folder.rename(moved)
        entry = self.service.relocate(str(folder), moved)
        self.assertEqual(entry.folder, str(moved))
        self.assertEqual(entry.backend, "directory")
        self.assertEqual(self.status(moved), STATUS_OK)


class MigrationTests(DirectoryServiceTestCase):
    def gvfs_service(self):
        return Service(self.store, theme_name=lambda: "Blue", search_dirs=[str(self.themes)], backend=environment.BACKEND_GVFS)

    def test_an_old_gvfs_entry_moves_to_the_directory_file_on_a_new_apply(self):
        self.require_metadata(self.workspace)
        folder = self.make_folder()
        old = self.gvfs_service()
        old.apply(folder, STAR)
        self.assertIsNotNone(read_custom_icon(folder))
        entry = self.service.apply(folder, STAR)
        self.assertEqual(entry.backend, environment.BACKEND_DIRECTORY)
        self.assertIsNone(read_custom_icon(folder))
        self.assertEqual(self.icon_of(folder), self.store.icon_uri(entry.digest))
        self.service.reset(folder)
        self.assertFalse((folder / ".directory").exists())
        self.assertIsNone(read_custom_icon(folder))

    def test_a_failing_old_engine_does_not_block_the_migration(self):
        folder = self.make_folder()
        self.gvfs_service().apply(folder, STAR)
        with mock.patch("custicon.service.write_custom_icon", side_effect=MetadataError("gone")):
            entry = self.service.apply(folder, STAR)
        self.assertEqual(entry.backend, environment.BACKEND_DIRECTORY)
        self.assertEqual(self.icon_of(folder), self.store.icon_uri(entry.digest))

    def test_an_explicit_backend_is_never_migrated(self):
        self.require_metadata(self.workspace)
        folder = self.make_folder()
        self.gvfs_service().apply(folder, STAR)
        entry = self.service.apply(folder, STAR, environment.BACKEND_GVFS)
        self.assertEqual(entry.backend, environment.BACKEND_GVFS)
        self.assertFalse((folder / ".directory").exists())
        self.gvfs_service().reset(folder)

    def test_a_deleted_and_recreated_directory_file_is_removed_with_the_icon(self):
        folder = self.make_folder()
        (folder / ".directory").write_text(DOLPHIN_FILE)
        self.service.apply(folder, STAR)
        self.assertFalse(self.store.get(str(folder)).directory_created)
        (folder / ".directory").unlink()
        self.service.apply(folder, STAR)
        self.assertTrue(self.store.get(str(folder)).directory_created)
        self.service.reset(folder)
        self.assertFalse((folder / ".directory").exists())


class MixedBackendTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.themes = self.root / "themes"
        self.themes.mkdir()
        make_theme(self.themes, "Blue", folder=folder_svg("#3366cc"), glyphs={"star-symbolic": glyph_svg()})
        self.store = Store(self.root / "data")
        self.workspace = self.root / "folders"
        self.workspace.mkdir()

    def service(self, backend):
        return Service(self.store, theme_name=lambda: "Blue", search_dirs=[str(self.themes)], backend=backend)

    def make_folder(self, name="docs"):
        folder = self.workspace / name
        folder.mkdir()
        return folder

    def test_the_default_backend_never_writes_in_the_folder(self):
        folder = self.make_folder()
        self.require_metadata(self.workspace)
        before = tree_fingerprint(folder)
        service = Service(self.store, theme_name=lambda: "Blue", search_dirs=[str(self.themes)])
        service.apply(folder, STAR)
        self.addCleanup(service.reset, folder)
        self.assertEqual(tree_fingerprint(folder), before)
        self.assertFalse((folder / ".directory").exists())

    def test_a_gvfs_entry_never_claims_to_have_created_a_directory_file(self):
        folder = self.make_folder()
        self.require_metadata(self.workspace)
        service = self.service(environment.BACKEND_GVFS)
        service.apply(folder, STAR)
        self.addCleanup(service.reset, folder)
        entry = self.store.get(str(folder))
        self.assertFalse(entry.directory_created)
        self.assertEqual(entry.backend, environment.BACKEND_GVFS)
        document = json.loads(self.store.registry_path.read_text())
        self.assertNotIn("directory_created", json.dumps(document))
        self.assertNotIn("backend", json.dumps(document))

    def test_an_undone_gvfs_removal_never_claims_a_directory_file(self):
        folder = self.make_folder()
        self.require_metadata(self.workspace)
        service = self.service(environment.BACKEND_GVFS)
        service.apply(folder, STAR)
        self.addCleanup(service.reset, folder)
        snapshot = service.snapshot(str(folder))
        service.reset(folder)
        service.restore(snapshot)
        self.assertFalse(self.store.get(str(folder)).directory_created)
        self.assertNotIn("directory_created", self.store.registry_path.read_text())

    def test_a_new_apply_follows_the_desktop_of_the_moment_and_cleans_the_old_mark(self):
        folder = self.make_folder()
        self.require_metadata(self.workspace)
        self.service("directory").apply(folder, STAR)
        gvfs = self.service("gvfs")
        entry = gvfs.apply(folder, STAR)
        self.addCleanup(gvfs.reset, folder)
        self.assertEqual(entry.backend, "gvfs")
        self.assertFalse((folder / ".directory").exists())
        self.assertIsNotNone(read_custom_icon(folder))

    def test_a_gvfs_entry_with_an_icon_of_the_user_is_not_cleared_by_the_migration(self):
        folder = self.make_folder()
        self.require_metadata(self.workspace)
        gvfs = self.service("gvfs")
        gvfs.apply(folder, STAR)
        user_icon = "file:///usr/share/icons/user.png"
        service_module.write_custom_icon(folder, user_icon)
        self.addCleanup(gvfs.reset, folder)
        self.service("directory").apply(folder, STAR)
        self.assertEqual(read_custom_icon(folder), user_icon)

    def test_a_directory_entry_does_not_touch_gvfs(self):
        folder = self.make_folder()
        self.require_metadata(self.workspace)
        self.service("directory").apply(folder, STAR)
        self.assertIsNone(read_custom_icon(folder))

    def test_each_folder_takes_the_backend_of_the_moment(self):
        a, b = self.make_folder("a"), self.make_folder("b")
        self.require_metadata(self.workspace)
        self.service("directory").apply(a, STAR)
        gvfs = self.service("gvfs")
        gvfs.apply(b, STAR)
        self.addCleanup(gvfs.reset, b)
        self.assertEqual(self.store.get(str(a)).backend, "directory")
        self.assertEqual(self.store.get(str(b)).backend, "gvfs")


if __name__ == "__main__":
    unittest.main()
