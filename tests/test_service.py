import base64
import re
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from custicon import service as service_module
from custicon.errors import FolderError, IconNotFoundError, InvalidSettingsError, MetadataError, StoreError
from custicon.metadata import read_custom_icon, write_custom_icon
from custicon.service import (
    RESET_FOLDER_MISSING,
    RESET_KEPT_USER_ICON,
    RESET_RESTORED,
    REPAIR_FORGET,
    REPAIR_REGENERATE,
    REPAIR_TAKE_BACK,
    RESET_UNKNOWN,
    STATUS_FOLDER_MISSING,
    STATUS_ICON_CHANGED,
    STATUS_ICON_FILE_MISSING,
    STATUS_OK,
    Service,
    Settings,
)
from custicon.store import Store

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme, tree_fingerprint

STAR = Settings(glyph_name="star-symbolic")
USER_ICON = "file:///home/someone/my-icon.png"


class ServiceTestCase(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.themes = self.root / "themes"
        self.themes.mkdir()
        glyphs = {"star-symbolic": glyph_svg(), "heart-symbolic": glyph_svg()}
        make_theme(self.themes, "Blue", folder=folder_svg("#3366cc"), glyphs=glyphs)
        make_theme(self.themes, "Green", folder=folder_svg("#33cc66"), glyphs=glyphs)
        make_theme(self.themes, "Inherit", inherits="Blue")
        self.theme = "Blue"
        self.store = Store(self.root / "data")
        self.service = Service(self.store, theme_name=lambda: self.theme, search_dirs=[str(self.themes)])
        self.workspace = self.root / "folders"
        self.workspace.mkdir()
        self.require_metadata(self.workspace)
        self.created = []
        self.addCleanup(self.clear_metadata)

    def clear_metadata(self):
        for folder in self.created:
            try:
                write_custom_icon(folder, None)
            except MetadataError:
                pass

    def make_folder(self, name: str = "docs") -> Path:
        folder = self.workspace / name
        folder.mkdir()
        (folder / "note.txt").write_text("keep me")
        self.created.append(folder)
        return folder


class ApplyTests(ServiceTestCase):
    def test_apply_points_the_folder_to_a_stored_icon(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.assertEqual(read_custom_icon(folder), self.store.icon_uri(entry.digest))
        self.assertTrue(self.store.icon_path(entry.digest).exists())
        self.assertEqual(self.store.get(str(folder)), entry)

    def test_apply_records_the_theme_and_the_color(self):
        entry = self.service.apply(self.make_folder(), STAR)
        self.assertEqual(entry.theme, "Blue")
        self.assertRegex(entry.color, r"^#[0-9a-f]{6}$")
        self.assertEqual(entry.settings["glyph_name"], "star-symbolic")

    def test_a_manual_color_is_used(self):
        entry = self.service.apply(self.make_folder(), Settings(glyph_name="star-symbolic", color="#ff8800"))
        self.assertEqual(entry.color, "#ff8800")
        text = self.store.icon_path(entry.digest).read_text()
        inner = base64.b64decode(re.search(r"base64,([A-Za-z0-9+/=]+)", text).group(1)).decode()
        self.assertIn("#ff8800", inner)

    def test_the_folder_content_is_never_modified(self):
        folder = self.make_folder()
        before = tree_fingerprint(folder)
        self.service.apply(folder, STAR)
        self.service.reset(folder)
        self.assertEqual(tree_fingerprint(folder), before)

    def test_the_icon_follows_the_theme_folder_color(self):
        blue = self.service.build(STAR, "Blue")
        green = self.service.build(STAR, "Green")
        self.assertNotEqual(blue.svg, green.svg)
        self.assertNotEqual(blue.color, green.color)

    def test_inherited_folder_is_the_parent_one(self):
        inherited = self.service.build(STAR, "Inherit")
        self.assertEqual(inherited.svg, self.service.build(STAR, "Blue").svg)
        self.assertEqual(inherited.folder_file.parts[-4], "Blue")

    def test_apply_to_a_symlinked_folder_marks_the_link_itself(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        entry = self.service.apply(link, STAR)
        self.assertEqual(entry.folder, str(link))
        self.assertIsNotNone(read_custom_icon(link))
        self.assertIsNone(read_custom_icon(folder))

    def test_a_link_and_its_target_keep_their_own_icons(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        self.service.apply(folder, STAR)
        self.service.apply(link, replace(STAR, color="#ff0000"))
        self.assertEqual(sorted(self.store.all()), sorted([str(folder), str(link)]))
        self.assertNotEqual(read_custom_icon(folder), read_custom_icon(link))
        self.service.reset(link)
        self.assertIsNone(read_custom_icon(link))
        self.assertIsNotNone(read_custom_icon(folder))

    def test_a_link_reached_through_dots_keeps_its_name(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        (self.workspace / "x").mkdir()
        entry = self.service.apply(self.workspace / "x" / ".." / "link", STAR)
        self.assertEqual(entry.folder, str(link))

    def test_a_relative_link_is_accepted(self):
        folder = self.make_folder("real")
        link = self.workspace / "rel"
        link.symlink_to("real")
        self.assertEqual(self.service.apply(link, STAR).folder, str(link))

    def test_a_link_to_a_link_marks_the_last_link_only(self):
        folder = self.make_folder("real")
        first = self.workspace / "first"
        second = self.workspace / "second"
        first.symlink_to(folder)
        second.symlink_to(first)
        self.assertEqual(self.service.apply(second, STAR).folder, str(second))
        self.assertIsNone(read_custom_icon(first))

    def test_a_saved_link_is_restored_as_the_link(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        self.service.apply(link, STAR)
        file = self.workspace / "backup.json"
        self.service.export_backup(file)
        self.service.reset(link)
        report = self.service.import_backup(file)
        self.assertEqual(report.restored, [str(link)])
        self.assertIsNotNone(read_custom_icon(link))
        self.assertIsNone(read_custom_icon(folder))

    def test_resetting_a_link_never_touches_the_customized_target(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        self.service.apply(folder, STAR)
        self.assertEqual(self.service.reset(link), RESET_UNKNOWN)
        self.assertIsNotNone(self.store.get(str(folder)))
        self.assertIsNotNone(read_custom_icon(folder))
        self.service.forget(link)
        self.assertIsNotNone(self.store.get(str(folder)))
        self.assertEqual(self.service.reset_many([link]).restored, [])
        self.assertIsNotNone(read_custom_icon(folder))

    def test_a_broken_link_is_not_a_folder(self):
        link = self.workspace / "broken"
        link.symlink_to(self.workspace / "nowhere")
        with self.assertRaises(FolderError):
            self.service.apply(link, STAR)
        self.assertEqual(self.store.all(), {})

    def test_a_link_to_a_file_is_not_a_folder(self):
        file = self.workspace / "plain.txt"
        file.write_text("x")
        link = self.workspace / "tofile"
        link.symlink_to(file)
        with self.assertRaises(FolderError):
            self.service.apply(link, STAR)

    def test_with_the_directory_engine_a_link_is_the_real_folder(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        entry = self.service.apply(link, STAR, backend="directory")
        self.assertEqual(entry.folder, str(folder))

    def test_a_lone_surrogate_in_a_text_setting_is_refused(self):
        for settings in (Settings(glyph_text="\ud800"), Settings(glyph_name="a\udc80"), Settings(glyph_name="phosphor:star", badge_glyph="\ud800")):
            with self.assertRaises(InvalidSettingsError):
                self.service.build(settings)
        self.assertEqual(self.store.all(), {})

    def test_a_file_is_not_a_folder(self):
        file = self.workspace / "plain.txt"
        file.write_text("x")
        with self.assertRaises(FolderError):
            self.service.apply(file, STAR)
        self.assertEqual(self.store.all(), {})

    def test_a_missing_path_is_refused(self):
        with self.assertRaises(FolderError):
            self.service.apply(self.workspace / "nope", STAR)

    def test_unknown_glyph_is_reported_and_leaves_nothing_behind(self):
        folder = self.make_folder()
        with self.assertRaises(IconNotFoundError):
            self.service.apply(folder, Settings(glyph_name="missing-symbolic"))
        self.assertIsNone(read_custom_icon(folder))
        self.assertEqual(self.store.all(), {})
        self.assertFalse(list(self.store.icons_dir.glob("*.svg")) if self.store.icons_dir.exists() else [])

    def test_glyph_missing_from_the_theme_falls_back_to_adwaita(self):
        make_theme(self.themes, "Adwaita", glyphs={"only-here-symbolic": glyph_svg("#111111")})
        built = self.service.build(Settings(glyph_name="only-here-symbolic"), "Blue")
        self.assertIn("<svg", built.svg)

    def test_a_glyph_file_can_be_used(self):
        glyph = self.root / "mine.svg"
        glyph.write_text(glyph_svg())
        entry = self.service.apply(self.make_folder(), Settings(glyph_file=str(glyph)))
        self.assertEqual(entry.settings["glyph_file"], str(glyph))

    def test_a_broken_glyph_file_leaves_nothing_behind(self):
        glyph = self.root / "broken.svg"
        glyph.write_text("not an svg")
        folder = self.make_folder()
        with self.assertRaises(Exception):
            self.service.apply(folder, Settings(glyph_file=str(glyph)))
        self.assertIsNone(read_custom_icon(folder))
        self.assertEqual(self.store.all(), {})

    def test_a_metadata_failure_leaves_nothing_behind(self):
        folder = self.make_folder()
        with mock.patch.object(service_module, "write_custom_icon", side_effect=MetadataError("refused")):
            with self.assertRaises(MetadataError):
                self.service.apply(folder, STAR)
        self.assertEqual(self.store.all(), {})
        self.assertEqual(list(self.store.icons_dir.glob("*.svg")), [])

    def test_a_registry_failure_restores_the_previous_icon(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        with mock.patch.object(Store, "put", side_effect=StoreError("disk full")):
            with self.assertRaises(StoreError):
                self.service.apply(folder, STAR)
        self.assertEqual(read_custom_icon(folder), USER_ICON)
        self.assertEqual(list(self.store.icons_dir.glob("*.svg")), [])

    def test_build_has_no_side_effect(self):
        self.service.build(STAR)
        self.assertEqual(self.store.all(), {})
        self.assertFalse(self.store.icons_dir.exists())

    def test_invalid_settings_are_refused_before_anything_happens(self):
        folder = self.make_folder()
        with self.assertRaises(InvalidSettingsError):
            self.service.apply(folder, Settings())
        self.assertEqual(self.store.all(), {})


class ApplyManyTests(ServiceTestCase):
    def test_every_folder_gets_the_icon(self):
        folders = [self.make_folder("a"), self.make_folder("b"), self.make_folder("c")]
        report = self.service.apply_many(folders, STAR)
        self.assertEqual(report.applied, [str(folder) for folder in folders])
        self.assertEqual(report.failed, {})
        for folder in folders:
            self.assertEqual(read_custom_icon(folder), self.store.icon_uri(self.store.get(str(folder)).digest))

    def test_identical_icons_share_one_stored_file(self):
        folders = [self.make_folder("a"), self.make_folder("b")]
        self.service.apply_many(folders, STAR)
        self.assertEqual(self.store.get(str(folders[0])).digest, self.store.get(str(folders[1])).digest)

    def test_a_missing_folder_fails_without_stopping_the_others(self):
        good, other = self.make_folder("a"), self.make_folder("b")
        missing = self.root / "missing"
        report = self.service.apply_many([good, missing, other], STAR)
        self.assertEqual(report.applied, [str(good), str(other)])
        self.assertEqual(list(report.failed), [str(missing)])
        self.assertIsNotNone(self.store.get(str(other)))

    def test_no_folder_means_an_empty_report(self):
        report = self.service.apply_many([], STAR)
        self.assertEqual((report.applied, report.failed), ([], {}))

    def test_an_invalid_setting_fails_every_folder(self):
        folders = [self.make_folder("a"), self.make_folder("b")]
        report = self.service.apply_many(folders, Settings(glyph_name="star-symbolic", size=99.0))
        self.assertEqual(report.applied, [])
        self.assertEqual(len(report.failed), 2)


class PhosphorTests(ServiceTestCase):
    def test_a_phosphor_pictogram_is_applied_and_tinted(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, Settings(glyph_name="phosphor:acorn", color="#ff8800"))
        self.assertEqual(entry.settings["glyph_name"], "phosphor:acorn")
        text = self.store.icon_path(entry.digest).read_text()
        inner = base64.b64decode(re.search(r"base64,([A-Za-z0-9+/=]+)", text).group(1)).decode()
        self.assertIn("#ff8800", inner)

    def test_a_phosphor_icon_does_not_depend_on_the_theme(self):
        blue = self.service.build(Settings(glyph_name="phosphor:acorn", color="#ff8800"), "Blue")
        green = self.service.build(Settings(glyph_name="phosphor:acorn", color="#ff8800"), "Green")
        self.assertNotEqual(blue.svg, green.svg)
        self.assertEqual(blue.color, green.color)

    def test_an_unknown_phosphor_icon_is_refused_without_falling_back(self):
        with self.assertRaises(IconNotFoundError):
            self.service.build(Settings(glyph_name="phosphor:nothing-here"))

    def test_a_phosphor_icon_is_regenerated(self):
        folder = self.make_folder()
        self.service.apply(folder, Settings(glyph_name="phosphor:acorn"))
        self.theme = "Green"
        report = self.service.regenerate_all()
        self.assertEqual(len(report.updated), 1)


class PhosphorRevisionTests(ServiceTestCase):
    def stale_entry(self, folder):
        entry = self.service.apply(folder, Settings(glyph_name="phosphor:acorn"))
        old = dict(entry.settings)
        old.pop("glyph_revision", None)
        self.store.put(replace(entry, settings=old))

    def test_the_revision_of_the_set_is_stored_with_a_phosphor_pictogram(self):
        entry = self.service.apply(self.make_folder(), Settings(glyph_name="phosphor:acorn"))
        self.assertEqual(entry.settings["glyph_revision"], self.service.phosphor_set().revision)

    def test_other_pictograms_store_no_revision(self):
        entry = self.service.apply(self.make_folder(), STAR)
        self.assertIsNone(entry.settings["glyph_revision"])

    def test_an_invalid_revision_is_refused(self):
        for value in ("2", True, 1.5, [2]):
            with self.subTest(value=value), self.assertRaises(InvalidSettingsError):
                Settings(glyph_name="phosphor:acorn", glyph_revision=value).validate()

    def test_nothing_is_outdated_right_after_applying_a_phosphor_pictogram(self):
        self.service.apply(self.make_folder(), Settings(glyph_name="phosphor:acorn"))
        self.assertEqual(self.service.outdated(), [])

    def test_an_icon_made_with_an_older_set_is_outdated(self):
        folder = self.make_folder()
        self.stale_entry(folder)
        self.assertEqual(self.service.outdated(), [str(folder.resolve())])

    def test_an_icon_made_with_another_revision_is_outdated(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, Settings(glyph_name="phosphor:acorn"))
        self.store.put(replace(entry, settings={**entry.settings, "glyph_revision": 1}))
        self.assertEqual(self.service.outdated(), [str(folder.resolve())])

    def test_only_phosphor_pictograms_follow_the_revision(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        old = dict(entry.settings)
        old.pop("glyph_revision", None)
        self.store.put(replace(entry, settings=old))
        self.assertEqual(self.service.outdated(), [])

    def test_regenerating_brings_the_icon_up_to_date(self):
        folder = self.make_folder()
        self.stale_entry(folder)
        self.service.regenerate_all()
        self.assertEqual(self.service.outdated(), [])
        self.assertEqual(self.store.get(str(folder.resolve())).settings["glyph_revision"], self.service.phosphor_set().revision)

    def test_a_missing_folder_with_an_old_icon_is_not_reported(self):
        folder = self.make_folder()
        self.stale_entry(folder)
        shutil.rmtree(folder)
        self.assertEqual(self.service.outdated(), [])

    def test_the_revision_is_not_part_of_the_remembered_appearance(self):
        self.assertNotIn("glyph_revision", service_module.APPEARANCE_FIELDS)


class IconSetServiceTests(ServiceTestCase):
    def inner(self, entry):
        text = self.store.icon_path(entry.digest).read_text()
        return base64.b64decode(re.search(r"base64,([A-Za-z0-9+/=]+)", text).group(1)).decode()

    def test_an_emoji_is_applied_and_tinted(self):
        entry = self.service.apply(self.make_folder(), Settings(glyph_name="emoji:cat-face", color="#ff8800"))
        self.assertEqual(entry.settings["glyph_name"], "emoji:cat-face")
        self.assertIn("#ff8800", self.inner(entry))
        self.assertNotIn("#212121", self.inner(entry))

    def test_a_brand_logo_is_applied_and_tinted(self):
        entry = self.service.apply(self.make_folder(), Settings(glyph_name="brand:github", color="#00aa55"))
        self.assertIn("#00aa55", self.inner(entry))

    def test_each_set_stores_its_own_revision(self):
        for name in ("phosphor:acorn", "emoji:cat-face", "brand:github"):
            entry = self.service.apply(self.make_folder(name.replace(":", "-")), Settings(glyph_name=name))
            self.assertEqual(entry.settings["glyph_revision"], self.service.icon_sets().for_name(name).revision, name)

    def test_an_unknown_emoji_or_logo_is_refused_without_falling_back(self):
        for name in ("emoji:nothing-here", "brand:nothing-here"):
            with self.assertRaises(IconNotFoundError, msg=name):
                self.service.build(Settings(glyph_name=name))

    def test_an_emoji_name_never_opens_a_theme_icon(self):
        with self.assertRaises(IconNotFoundError):
            self.service.build(Settings(glyph_name="emoji:star-symbolic"))

    def test_the_registry_is_the_one_of_the_picker(self):
        self.assertIs(self.service.phosphor_set(), self.service.icon_sets()["phosphor"])

    def test_icons_of_every_set_are_regenerated(self):
        for name in ("emoji:cat-face", "brand:github"):
            self.service.apply(self.make_folder(name.replace(":", "-")), Settings(glyph_name=name))
        self.theme = "Green"
        self.assertEqual(len(self.service.regenerate_all().updated), 2)

    def test_an_emoji_or_logo_made_with_another_revision_is_outdated(self):
        folders = {}
        for name in ("emoji:cat-face", "brand:github"):
            folder = folders[name] = self.make_folder(name.replace(":", "-"))
            entry = self.service.apply(folder, Settings(glyph_name=name))
            self.store.put(replace(entry, settings={**entry.settings, "glyph_revision": 0}))
        self.assertEqual(sorted(self.service.outdated()), sorted(str(folder.resolve()) for folder in folders.values()))

    def test_an_icon_made_with_the_current_revision_is_not_outdated(self):
        self.service.apply(self.make_folder(), Settings(glyph_name="emoji:cat-face"))
        self.assertEqual(self.service.outdated(), [])


class PickerMemoryServiceTests(ServiceTestCase):
    def test_nothing_is_remembered_at_first(self):
        memory = self.service.picker_memory()
        self.assertEqual((memory.recent, memory.favorites), ((), ()))

    def test_a_chosen_pictogram_goes_first_without_duplicates(self):
        for name in ("a-symbolic", "b-symbolic", "c-symbolic", "a-symbolic"):
            self.assertTrue(self.service.remember_glyph(name))
        self.assertEqual(self.service.picker_memory().recent, ("a-symbolic", "c-symbolic", "b-symbolic"))

    def test_the_recent_list_is_limited(self):
        for number in range(30):
            self.service.remember_glyph(f"icon-{number}")
        recent = self.service.picker_memory().recent
        self.assertEqual(len(recent), 12)
        self.assertEqual(recent[0], "icon-29")

    def test_an_invalid_name_is_not_remembered(self):
        for name in ("", "a\0b", "x" * 101, None, 5):
            self.assertFalse(self.service.remember_glyph(name), repr(name))
            self.assertFalse(self.service.set_favorite(name, True), repr(name))
        self.assertEqual(self.service.picker_memory().recent, ())

    def test_a_favorite_can_be_added_and_removed(self):
        self.service.set_favorite("phosphor:house", True)
        self.service.set_favorite("emoji:cat-face", True)
        self.assertEqual(self.service.picker_memory().favorites, ("emoji:cat-face", "phosphor:house"))
        self.service.set_favorite("phosphor:house", False)
        self.assertEqual(self.service.picker_memory().favorites, ("emoji:cat-face",))

    def test_adding_a_favorite_twice_or_removing_an_unknown_one_changes_nothing(self):
        self.service.set_favorite("a", True)
        self.service.set_favorite("a", True)
        self.service.set_favorite("zzz", False)
        self.assertEqual(self.service.picker_memory().favorites, ("a",))

    def test_favorites_and_recents_are_independent(self):
        self.service.remember_glyph("a")
        self.service.set_favorite("b", True)
        memory = self.service.picker_memory()
        self.assertEqual((memory.recent, memory.favorites), (("a",), ("b",)))

    def test_choosing_a_pictogram_keeps_the_favorites(self):
        self.service.set_favorite("b", True)
        self.service.remember_glyph("a")
        self.assertEqual(self.service.picker_memory().favorites, ("b",))
        self.assertEqual(self.service.picker_memory().recent, ("a",))

    def test_adding_a_favorite_keeps_the_recents(self):
        self.service.remember_glyph("a")
        self.service.set_favorite("b", True)
        self.service.set_favorite("c", True)
        self.assertEqual(self.service.picker_memory().recent, ("a",))

    def test_the_lists_survive_a_new_service(self):
        self.service.remember_glyph("a")
        self.service.set_favorite("b", True)
        other = Service(self.store, theme_name=lambda: self.theme, search_dirs=[str(self.themes)])
        self.assertEqual(other.picker_memory().recent, ("a",))
        self.assertEqual(other.picker_memory().favorites, ("b",))

    def test_a_write_failure_does_not_break_the_caller(self):
        with mock.patch.object(Store, "save_picker", side_effect=StoreError("disk full")):
            self.assertFalse(self.service.remember_glyph("a"))
            self.assertFalse(self.service.set_favorite("b", True))

    def test_the_favorites_are_limited(self):
        for number in range(80):
            self.service.set_favorite(f"icon-{number}", True)
        favorites = self.service.picker_memory().favorites
        self.assertEqual(len(favorites), 60)
        self.assertEqual(favorites[0], "icon-79")


class ResetTests(ServiceTestCase):
    def test_reset_removes_the_icon_when_there_was_none_before(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.assertEqual(self.service.reset(folder), RESET_RESTORED)
        self.assertIsNone(read_custom_icon(folder))
        self.assertEqual(self.store.all(), {})
        self.assertFalse(self.store.icon_path(entry.digest).exists())

    def test_reset_restores_an_icon_chosen_before_custicon(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        self.service.apply(folder, STAR)
        self.assertEqual(self.service.reset(folder), RESET_RESTORED)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_applying_again_keeps_the_original_previous_icon(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        self.service.apply(folder, STAR)
        again = self.service.apply(folder, Settings(glyph_name="heart-symbolic"))
        self.assertEqual(again.previous, USER_ICON)
        self.service.reset(folder)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_reapplying_never_records_its_own_icon_as_previous(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        again = self.service.apply(folder, Settings(glyph_name="heart-symbolic"))
        self.assertIsNone(again.previous)

    def test_reapplying_removes_the_unused_old_icon_file(self):
        folder = self.make_folder()
        first = self.service.apply(folder, STAR)
        second = self.service.apply(folder, Settings(glyph_name="heart-symbolic", color="#ff0000"))
        self.assertNotEqual(first.digest, second.digest)
        self.assertFalse(self.store.icon_path(first.digest).exists())
        self.assertTrue(self.store.icon_path(second.digest).exists())

    def test_an_icon_chosen_later_in_nautilus_is_not_overwritten_by_reset(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        write_custom_icon(folder, USER_ICON)
        self.assertEqual(self.service.reset(folder), RESET_KEPT_USER_ICON)
        self.assertEqual(read_custom_icon(folder), USER_ICON)
        self.assertEqual(self.store.all(), {})

    def test_a_later_user_icon_becomes_the_previous_one_when_applying_again(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        write_custom_icon(folder, USER_ICON)
        again = self.service.apply(folder, STAR)
        self.assertEqual(again.previous, USER_ICON)

    def test_reset_of_an_unknown_folder_does_nothing(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        self.assertEqual(self.service.reset(folder), RESET_UNKNOWN)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_reset_of_a_deleted_folder_forgets_the_entry(self):
        folder = self.make_folder("gone")
        self.service.apply(folder, STAR)
        (folder / "note.txt").unlink()
        folder.rmdir()
        self.assertEqual(self.service.reset(folder), RESET_FOLDER_MISSING)
        self.assertEqual(self.store.all(), {})

    def test_a_shared_icon_file_survives_until_every_folder_is_reset(self):
        first, second = self.make_folder("one"), self.make_folder("two")
        digest = self.service.apply(first, STAR).digest
        self.assertEqual(self.service.apply(second, STAR).digest, digest)
        self.service.reset(first)
        self.assertTrue(self.store.icon_path(digest).exists())
        self.assertEqual(read_custom_icon(second), self.store.icon_uri(digest))
        self.service.reset(second)
        self.assertFalse(self.store.icon_path(digest).exists())

    def test_forget_drops_the_entry_without_touching_the_folder(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.service.forget(folder)
        self.assertEqual(self.store.all(), {})
        self.assertEqual(read_custom_icon(folder), self.store.icon_uri(entry.digest))
        self.assertFalse(self.store.icon_path(entry.digest).exists())


class RegenerationTests(ServiceTestCase):
    def test_regeneration_after_a_theme_change_updates_every_folder(self):
        folders = [self.make_folder("one"), self.make_folder("two")]
        old = [self.service.apply(folder, STAR) for folder in folders]
        self.theme = "Green"
        report = self.service.regenerate_all()
        self.assertEqual(sorted(report.updated), sorted(str(f) for f in folders))
        self.assertEqual((report.unchanged, report.skipped, report.failed), ([], {}, {}))
        for folder, before in zip(folders, old):
            current = self.store.get(str(folder))
            self.assertNotEqual(current.digest, before.digest)
            self.assertEqual(current.theme, "Green")
            self.assertEqual(read_custom_icon(folder), self.store.icon_uri(current.digest))
            self.assertFalse(self.store.icon_path(before.digest).exists())

    def test_regeneration_keeps_the_original_previous_icon(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        self.service.apply(folder, STAR)
        self.theme = "Green"
        self.service.regenerate_all()
        self.service.reset(folder)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_regeneration_keeps_the_user_settings(self):
        folder = self.make_folder()
        settings = Settings(glyph_name="heart-symbolic", color="#ff0000", size=1.5, offset_y=0.05)
        self.service.apply(folder, settings)
        self.theme = "Green"
        self.service.regenerate_all()
        self.assertEqual(Settings.from_dict(self.store.get(str(folder)).settings), settings)

    def test_regeneration_without_change_reports_unchanged(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        report = self.service.regenerate_all()
        self.assertEqual(report.unchanged, [str(folder)])
        self.assertEqual(report.updated, [])

    def test_regeneration_skips_a_deleted_folder(self):
        folder = self.make_folder("gone")
        self.service.apply(folder, STAR)
        (folder / "note.txt").unlink()
        folder.rmdir()
        report = self.service.regenerate_all()
        self.assertEqual(report.skipped, {str(folder): STATUS_FOLDER_MISSING})
        self.assertIn(str(folder), self.store.all())

    def test_regeneration_respects_an_icon_changed_in_nautilus(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        write_custom_icon(folder, USER_ICON)
        self.theme = "Green"
        report = self.service.regenerate_all()
        self.assertEqual(report.skipped, {str(folder): STATUS_ICON_CHANGED})
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_a_failing_folder_does_not_stop_the_others(self):
        good, bad = self.make_folder("good"), self.make_folder("bad")
        self.service.apply(good, STAR)
        self.service.apply(bad, STAR)
        self.theme = "Green"
        real = service_module.write_custom_icon

        def refuse_bad(folder, uri):
            if Path(folder) == bad:
                raise MetadataError("refused")
            real(folder, uri)

        with mock.patch.object(service_module, "write_custom_icon", side_effect=refuse_bad):
            report = self.service.regenerate_all()
        self.assertEqual(report.updated, [str(good)])
        self.assertEqual(list(report.failed), [str(bad)])

    def test_a_missing_theme_folder_is_reported_as_a_failure(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        make_theme(self.themes, "Bare")
        self.theme = "Bare"
        report = self.service.regenerate_all()
        self.assertEqual(list(report.failed), [str(folder)])
        self.assertEqual(read_custom_icon(folder), self.store.icon_uri(self.store.get(str(folder)).digest))


class RepairTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.healthy = self.make_folder("healthy")
        self.lost_file = self.make_folder("lost-file")
        self.changed = self.make_folder("changed")
        self.gone = self.make_folder("gone")
        for folder in (self.healthy, self.changed, self.gone):
            self.service.apply(folder, STAR)
        self.service.apply(self.lost_file, replace(STAR, color="#ff0000"))
        self.store.icon_path(self.store.get(str(self.lost_file)).digest).unlink()
        write_custom_icon(self.changed, USER_ICON)
        shutil.rmtree(self.gone)

    def keys(self, *folders):
        return [str(folder) for folder in folders]

    def test_every_kind_of_problem_comes_with_its_own_action(self):
        found = {problem.folder: (problem.status, problem.action) for problem in self.service.problems()}
        self.assertEqual(
            found,
            {
                str(self.lost_file): (STATUS_ICON_FILE_MISSING, REPAIR_REGENERATE),
                str(self.changed): (STATUS_ICON_CHANGED, REPAIR_TAKE_BACK),
                str(self.gone): (STATUS_FOLDER_MISSING, REPAIR_FORGET),
            },
        )

    def test_a_healthy_list_has_no_problem(self):
        shutil.rmtree(self.store.root)
        self.assertEqual(self.service.problems(), [])

    def test_nothing_is_touched_when_nothing_is_chosen(self):
        report = self.service.repair([])
        self.assertEqual((report.regenerated, report.taken_back, report.forgotten), ([], [], []))
        self.assertEqual(len(self.service.problems()), 3)
        self.assertEqual(read_custom_icon(self.changed), USER_ICON)

    def test_a_missing_icon_file_is_regenerated(self):
        report = self.service.repair(self.keys(self.lost_file))
        self.assertEqual(report.regenerated, self.keys(self.lost_file))
        self.assertEqual(self.service.entry_status(self.store.get(str(self.lost_file))), STATUS_OK)
        self.assertEqual(read_custom_icon(self.changed), USER_ICON)

    def test_taking_back_an_icon_keeps_the_one_the_user_had(self):
        report = self.service.repair(self.keys(self.changed))
        self.assertEqual(report.taken_back, self.keys(self.changed))
        entry = self.store.get(str(self.changed))
        self.assertEqual(entry.previous, USER_ICON)
        self.assertEqual(read_custom_icon(self.changed), self.store.icon_uri(entry.digest))

    def test_a_missing_folder_is_forgotten_only_when_chosen(self):
        self.service.repair(self.keys(self.lost_file))
        self.assertIsNotNone(self.store.get(str(self.gone)))
        report = self.service.repair(self.keys(self.gone))
        self.assertEqual(report.forgotten, self.keys(self.gone))
        self.assertIsNone(self.store.get(str(self.gone)))

    def test_a_folder_that_recovered_is_not_touched(self):
        self.service.repair(self.keys(self.lost_file))
        report = self.service.repair(self.keys(self.lost_file, self.healthy, "/nowhere/else"))
        self.assertEqual(report.skipped, self.keys(self.lost_file, self.healthy, "/nowhere/else"))
        self.assertEqual((report.regenerated, report.taken_back, report.forgotten), ([], [], []))

    def test_one_failure_does_not_stop_the_others(self):
        real_apply = self.service.apply

        def flaky(folder, settings, backend=None):
            if Path(folder) == self.lost_file:
                raise MetadataError("denied")
            return real_apply(folder, settings, backend)

        with mock.patch.object(self.service, "apply", side_effect=flaky):
            report = self.service.repair(self.keys(self.lost_file, self.changed, self.gone))
        self.assertEqual(list(report.failed), self.keys(self.lost_file))
        self.assertEqual(report.taken_back, self.keys(self.changed))
        self.assertEqual(report.forgotten, self.keys(self.gone))

    def test_a_folder_listed_twice_is_repaired_once(self):
        report = self.service.repair(self.keys(self.changed, self.changed))
        self.assertEqual(report.taken_back, self.keys(self.changed))


class ResetManyTests(ServiceTestCase):
    def test_every_customized_folder_gets_its_icon_back(self):
        folders = [self.make_folder(name) for name in ("a", "b", "c")]
        self.service.apply_many(folders, STAR)
        report = self.service.reset_many(folders)
        self.assertEqual(report.restored, [str(folder) for folder in folders])
        self.assertEqual((report.kept, report.missing, report.failed), ([], [], {}))
        for folder in folders:
            self.assertIsNone(read_custom_icon(folder))
            self.assertIsNone(self.store.get(str(folder)))

    def test_an_unusable_path_is_reported_and_the_others_are_still_done(self):
        done = self.make_folder("a")
        self.service.apply(done, STAR)
        report = self.service.reset_many(["bad\0path", done])
        self.assertEqual(report.restored, [str(done)])
        self.assertEqual(list(report.failed), ["bad\0path"])

    def test_a_folder_that_was_never_customized_is_ignored(self):
        done, plain = self.make_folder("a"), self.make_folder("b")
        self.service.apply(done, STAR)
        report = self.service.reset_many([plain, done])
        self.assertEqual(report.restored, [str(done)])
        self.assertEqual((report.kept, report.missing, report.failed), ([], [], {}))

    def test_an_icon_changed_by_the_user_is_kept_and_reported(self):
        a, b = self.make_folder("a"), self.make_folder("b")
        self.service.apply_many([a, b], STAR)
        write_custom_icon(a, USER_ICON)
        report = self.service.reset_many([a, b])
        self.assertEqual(report.kept, [str(a)])
        self.assertEqual(report.restored, [str(b)])
        self.assertEqual(read_custom_icon(a), USER_ICON)

    def test_a_missing_folder_is_forgotten_and_reported(self):
        a, b = self.make_folder("a"), self.make_folder("b")
        self.service.apply_many([a, b], STAR)
        shutil.rmtree(a)
        report = self.service.reset_many([a, b])
        self.assertEqual(report.missing, [str(a)])
        self.assertEqual(report.restored, [str(b)])
        self.assertIsNone(self.store.get(str(a)))

    def test_a_failure_does_not_stop_the_others(self):
        a, b, c = (self.make_folder(name) for name in "abc")
        self.service.apply_many([a, b, c], STAR)
        real = service_module.write_custom_icon
        calls = []

        def flaky(target, value):
            calls.append(target)
            if Path(target) == b:
                raise MetadataError("refused")
            return real(target, value)

        with mock.patch.object(service_module, "write_custom_icon", side_effect=flaky):
            report = self.service.reset_many([a, b, c])
        self.assertEqual(report.restored, [str(a), str(c)])
        self.assertEqual(list(report.failed), [str(b)])

    def test_the_previous_icon_of_each_folder_is_restored(self):
        a, b = self.make_folder("a"), self.make_folder("b")
        write_custom_icon(a, USER_ICON)
        self.service.apply_many([a, b], STAR)
        self.service.reset_many([a, b])
        self.assertEqual(read_custom_icon(a), USER_ICON)
        self.assertIsNone(read_custom_icon(b))

    def test_an_empty_list_changes_nothing(self):
        report = self.service.reset_many([])
        self.assertEqual((report.restored, report.kept, report.missing, report.failed), ([], [], [], {}))

    def test_a_folder_listed_twice_is_restored_once(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        report = self.service.reset_many([folder, folder])
        self.assertEqual(report.restored, [str(folder)])

    def test_the_content_of_the_folders_is_never_touched(self):
        folders = [self.make_folder(name) for name in ("a", "b")]
        before = [tree_fingerprint(folder) for folder in folders]
        self.service.apply_many(folders, STAR)
        self.service.reset_many(folders)
        self.assertEqual([tree_fingerprint(folder) for folder in folders], before)

    def test_reset_all_still_gives_the_same_report(self):
        folders = [self.make_folder(name) for name in ("a", "b")]
        self.service.apply_many(folders, STAR)
        report = self.service.reset_all()
        self.assertEqual(sorted(report.restored), sorted(str(folder) for folder in folders))


class WindowStateServiceTests(ServiceTestCase):
    def test_nothing_is_remembered_at_first(self):
        self.assertIsNone(self.service.window_state())

    def test_a_remembered_window_comes_back(self):
        self.assertTrue(self.service.remember_window(900, 700, True))
        state = self.service.window_state()
        self.assertEqual((state.width, state.height, state.maximized), (900, 700, True))

    def test_an_unusable_size_is_not_remembered(self):
        self.service.remember_window(900, 700, False)
        self.assertFalse(self.service.remember_window(5, 700, False))
        self.assertEqual(self.service.window_state().width, 900)

    def test_a_write_failure_does_not_raise(self):
        with mock.patch.object(self.store, "save_window", side_effect=StoreError("disk full")):
            self.assertFalse(self.service.remember_window(900, 700, False))

    def test_a_damaged_file_gives_nothing(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.window_path.write_text("{")
        self.assertIsNone(self.service.window_state())


class ColorSchemeServiceTests(ServiceTestCase):
    def test_the_system_scheme_is_remembered_at_first(self):
        self.assertEqual(self.service.color_scheme(), "system")

    def test_a_remembered_scheme_comes_back(self):
        self.assertTrue(self.service.remember_color_scheme("dark"))
        self.assertEqual(self.service.color_scheme(), "dark")

    def test_an_unknown_scheme_is_refused_and_the_previous_one_is_kept(self):
        self.service.remember_color_scheme("light")
        self.assertFalse(self.service.remember_color_scheme("purple"))
        self.assertEqual(self.service.color_scheme(), "light")


class OutdatedWithKnownStatusTests(ServiceTestCase):
    def test_known_statuses_give_the_same_answer_as_a_fresh_reading(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        self.theme = "Green"
        self.assertEqual(self.service.outdated(self.service.customized()), self.service.outdated())
        self.assertEqual(self.service.outdated(), [str(folder)])

    def test_the_given_statuses_are_trusted_instead_of_read_again(self):
        from custicon.service import Customized, STATUS_ICON_CHANGED

        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.theme = "Green"
        self.assertEqual(self.service.outdated([Customized(entry, STATUS_ICON_CHANGED)]), [])
        self.assertEqual(self.service.outdated([Customized(entry, STATUS_OK)]), [str(folder)])

    def test_an_empty_list_of_known_statuses_means_nothing_is_stale(self):
        self.service.apply(self.make_folder(), STAR)
        self.theme = "Green"
        self.assertEqual(self.service.outdated([]), [])

    def test_the_known_statuses_avoid_reading_the_metadata_again(self):
        self.service.apply(self.make_folder(), STAR)
        known = self.service.customized()
        with mock.patch("custicon.service.read_custom_icon", side_effect=AssertionError("read again")):
            self.service.outdated(known)


class ListingTests(ServiceTestCase):
    def status(self, folder: Path) -> str:
        return next(item.status for item in self.service.customized() if item.entry.folder == str(folder))

    def test_nothing_is_customized_at_first(self):
        self.assertEqual(self.service.customized(), [])

    def test_a_fresh_customization_is_ok(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        self.assertEqual(self.status(folder), STATUS_OK)

    def test_a_deleted_folder_is_reported(self):
        folder = self.make_folder("gone")
        self.service.apply(folder, STAR)
        (folder / "note.txt").unlink()
        folder.rmdir()
        self.assertEqual(self.status(folder), STATUS_FOLDER_MISSING)

    def test_a_changed_icon_is_reported(self):
        folder = self.make_folder()
        self.service.apply(folder, STAR)
        write_custom_icon(folder, USER_ICON)
        self.assertEqual(self.status(folder), STATUS_ICON_CHANGED)

    def test_a_missing_icon_file_is_reported(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.store.icon_path(entry.digest).unlink()
        self.assertEqual(self.status(folder), STATUS_ICON_FILE_MISSING)

    def test_regeneration_repairs_a_missing_icon_file(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        self.store.icon_path(entry.digest).unlink()
        self.service.regenerate_all()
        self.assertEqual(self.status(folder), STATUS_OK)


class ThemeCacheTests(ServiceTestCase):
    def test_a_theme_is_loaded_once(self):
        self.assertIs(self.service.icon_theme("Blue"), self.service.icon_theme("Blue"))
        self.assertIsNot(self.service.icon_theme("Blue"), self.service.icon_theme("Green"))

    def test_refreshing_drops_the_cached_themes(self):
        before = self.service.icon_theme("Blue")
        self.service.refresh_themes()
        self.assertIsNot(self.service.icon_theme("Blue"), before)

    def test_regenerating_all_refreshes_the_cached_themes(self):
        before = self.service.icon_theme("Blue")
        self.service.regenerate_all()
        self.assertIsNot(self.service.icon_theme("Blue"), before)

    def test_the_plain_folder_file_follows_the_theme(self):
        self.assertEqual(self.service.folder_file("Blue").parts[-4], "Blue")
        self.assertEqual(self.service.folder_file("Green").parts[-4], "Green")
        self.assertEqual(self.service.current_theme(), "Blue")

    def test_regenerate_entry_reports_into_the_given_report(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        report = service_module.RegenerationReport()
        self.service.regenerate_entry(entry, report)
        self.assertEqual(report.unchanged, [str(folder)])


class SettingsTests(unittest.TestCase):
    def test_a_symbolic_name_xor_a_file_is_required(self):
        with self.assertRaises(InvalidSettingsError):
            Settings().validate()
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="a-symbolic", glyph_file="/x.svg").validate()
        Settings(glyph_name="a-symbolic").validate()
        Settings(glyph_file="/x.svg").validate()

    def test_color_must_be_a_hex_value(self):
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="a-symbolic", color="red").validate()

    def test_size_and_offsets_are_bounded(self):
        for bad in (
            Settings(glyph_name="a", size=0.1),
            Settings(glyph_name="a", size=3.5),
            Settings(glyph_name="a", offset_x=0.6),
            Settings(glyph_name="a", offset_y=-0.6),
        ):
            with self.assertRaises(InvalidSettingsError):
                bad.validate()

    def test_bounds_are_inclusive(self):
        Settings(glyph_name="a", size=0.2, offset_x=-0.5, offset_y=0.5).validate()
        Settings(glyph_name="a", size=3.0).validate()

    def test_round_trip_through_a_dictionary(self):
        settings = Settings(glyph_name="a", color="#123456", size=1.2, offset_x=0.1, offset_y=-0.1, tint=False)
        self.assertEqual(Settings.from_dict(settings.to_dict()), settings)

    def test_unknown_keys_are_ignored(self):
        self.assertEqual(Settings.from_dict({"glyph_name": "a", "future": 1}), Settings(glyph_name="a"))


if __name__ == "__main__":
    unittest.main()
