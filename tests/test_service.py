import re
import base64
import unittest
from pathlib import Path
from unittest import mock

from custicon import service as service_module
from custicon.errors import FolderError, IconNotFoundError, InvalidSettingsError, MetadataError, StoreError
from custicon.metadata import read_custom_icon, write_custom_icon
from custicon.service import (
    RESET_FOLDER_MISSING,
    RESET_KEPT_USER_ICON,
    RESET_RESTORED,
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

    def test_apply_to_a_symlinked_folder_uses_the_real_path(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        entry = self.service.apply(link, STAR)
        self.assertEqual(entry.folder, str(folder))

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
