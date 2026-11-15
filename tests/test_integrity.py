import base64
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock

from custicon import environment
from custicon.color import parse_hex
from custicon import service as service_module
from custicon import store as store_module
from custicon.errors import FolderError, InvalidSettingsError, MetadataError, StoreError
from custicon.metadata import read_custom_icon, write_custom_icon
from custicon.service import RESET_RESTORED, RESET_UNKNOWN, RegenerationReport, Service, Settings
from custicon.store import Entry, Store

from .support import WorkspaceTestCase, glyph_svg
from .test_service import STAR, USER_ICON, ServiceTestCase

GOOD = "0123456789abcdef" * 2


def inner_svg(svg: str) -> str:
    return base64.b64decode(re.search(r"base64,([A-Za-z0-9+/=]+)", svg).group(1)).decode()


def document(**changes) -> str:
    item = {"folder": "/a", "digest": GOOD, "theme": "T", "color": "#112233", "settings": {"glyph_name": "x"}, "previous": None}
    item.update(changes)
    return json.dumps({"version": 1, "entries": [item]})


class StoreIntegrityTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace() / "data"
        self.store = Store(self.root)

    def write_registry(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.store.registry_path.write_text(text)

    def test_only_files_named_after_a_digest_are_owned(self):
        base = self.store.icons_dir.as_uri() + "/"
        self.assertTrue(self.store.owns_uri(base + GOOD + ".svg"))
        for tail in ("../x.svg", "sub/" + GOOD + ".svg", "x.svg", GOOD.upper() + ".svg", GOOD + ".png", GOOD + ".svg/", ""):
            self.assertFalse(self.store.owns_uri(base + tail), tail)

    def test_a_well_formed_registry_is_read(self):
        self.write_registry(document())
        self.assertEqual(self.store.get("/a").digest, GOOD)

    def test_badly_typed_registries_are_refused_with_a_store_error(self):
        bad = [
            document(settings=["a"]),
            document(settings={"a": [1]}),
            document(settings={"a": {"b": 1}}),
            document(digest="zz"),
            document(digest=GOOD.upper()),
            document(digest=5),
            document(folder="relative/path"),
            document(folder="/a\u0000b"),
            document(folder=None),
            document(previous=3),
            document(theme=None),
            document(color=1),
            json.dumps([]),
            json.dumps({"version": 1, "entries": {}}),
            json.dumps({"version": 1, "entries": [1]}),
            json.dumps({"version": 1, "entries": [None]}),
            "[" * 100000,
            '{"version":1,"entries":' + "[" * 100000,
        ]
        for text in bad:
            self.write_registry(text)
            with self.assertRaises(StoreError, msg=text[:60]):
                self.store.all()

    def test_a_good_backup_replaces_a_badly_typed_registry(self):
        self.store.put(Entry("/a", GOOD, "T", "#112233", {"glyph_name": "x"}, None))
        self.store.put(Entry("/b", GOOD, "T", "#112233", {"glyph_name": "x"}, None))
        self.store.registry_path.write_text(document(settings=["no"]))
        self.assertEqual(set(self.store.all()), {"/a"})

    def test_prune_does_nothing_when_the_registry_is_missing(self):
        digest = self.store.save_icon("<svg>other folder</svg>")
        self.assertEqual(self.store.prune(), [])
        self.assertTrue(self.store.icon_path(digest).exists())

    def test_release_removes_only_the_given_icon_and_only_if_unreferenced(self):
        kept = self.store.save_icon("<svg>kept</svg>")
        shared = self.store.save_icon("<svg>shared</svg>")
        orphan = self.store.save_icon("<svg>orphan</svg>")
        gone = self.store.save_icon("<svg>gone</svg>")
        self.store.put(Entry("/a", shared, "T", "#112233", {}, None))
        self.store.release(shared)
        self.store.release(gone)
        self.assertTrue(self.store.icon_path(shared).exists())
        self.assertFalse(self.store.icon_path(gone).exists())
        self.assertTrue(self.store.icon_path(kept).exists())
        self.assertTrue(self.store.icon_path(orphan).exists())

    def test_release_keeps_everything_when_the_registry_is_unreadable(self):
        digest = self.store.save_icon("<svg>x</svg>")
        self.write_registry("{broken")
        self.store.release(digest)
        self.assertTrue(self.store.icon_path(digest).exists())

    def test_pictogram_copies_are_stored_once_and_released_when_unused(self):
        name = self.store.save_glyph(b"<svg/>", ".svg")
        self.assertEqual(name, self.store.save_glyph(b"<svg/>", ".svg"))
        self.assertEqual(len(list(self.store.glyphs_dir.iterdir())), 1)
        digest = self.store.save_icon("<svg/>")
        self.store.put(Entry("/a", digest, "T", "#112233", {"glyph_copy": name}, None))
        self.store.release_glyph(name)
        self.assertTrue(self.store.glyph_path(name).exists())
        self.store.remove("/a")
        self.store.release_glyph(name)
        self.assertFalse(self.store.glyph_path(name).exists())

    def test_only_well_formed_copy_names_resolve_to_a_path(self):
        for name in ("../x.svg", "x.svg", GOOD, GOOD + ".exe", None, 3):
            self.assertIsNone(self.store.glyph_path(name), repr(name))

    def test_prune_also_cleans_unreferenced_pictogram_copies(self):
        used = self.store.save_glyph(b"<svg>used</svg>", ".svg")
        unused = self.store.save_glyph(b"<svg>unused</svg>", ".svg")
        digest = self.store.save_icon("<svg/>")
        self.store.put(Entry("/a", digest, "T", "#112233", {"glyph_copy": used}, None))
        self.assertEqual(self.store.prune(), [unused])
        self.assertTrue(self.store.glyph_path(used).exists())

    def test_a_folder_name_that_is_not_utf8_is_a_store_error(self):
        bad = os.fsdecode(b"/tmp/bad\xff")
        with self.assertRaises(StoreError):
            self.store.put(Entry(bad, GOOD, "T", "#112233", {}, None))

    def test_a_failing_backup_copy_is_a_store_error(self):
        self.store.put(Entry("/a", GOOD, "T", "#112233", {}, None))
        with mock.patch.object(store_module.shutil, "copyfile", side_effect=OSError(28, "No space left")):
            with self.assertRaises(StoreError):
                self.store.put(Entry("/b", GOOD, "T", "#112233", {}, None))

    def test_a_relative_host_data_folder_is_ignored(self):
        with mock.patch.dict(os.environ, {environment.HOST_DATA_VARIABLE: "relative/share"}):
            self.assertTrue(os.path.isabs(environment.host_data_home()))
        with mock.patch.dict(os.environ, {environment.HOST_DATA_VARIABLE: "/srv/share"}):
            self.assertEqual(environment.host_data_home(), "/srv/share")


class SettingsValidationTests(unittest.TestCase):
    def test_wrongly_typed_settings_are_refused_cleanly(self):
        bad = [
            {"size": "big"},
            {"size": True},
            {"size": float("nan")},
            {"offset_x": float("inf")},
            {"offset_y": None},
            {"tint": "yes"},
            {"tint": 1},
            {"color": 5},
            {"color": "#12345\n"},
            {"glyph_name": ["a"]},
            {"glyph_name": "a\u0000b"},
            {"glyph_copy": "../x.svg"},
            {"glyph_name": None, "glyph_file": None},
        ]
        for changes in bad:
            values = {"glyph_name": "star-symbolic", **changes}
            with self.assertRaises(InvalidSettingsError, msg=str(changes)):
                Settings.from_dict(values).validate()


class ApplyIntegrityTests(ServiceTestCase):
    def registry_entry(self, folder: Path):
        return self.store.get(str(folder.resolve()))

    def test_the_registry_is_written_before_the_metadata(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        with mock.patch.object(service_module, "write_custom_icon", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(folder, STAR)
        self.assertEqual(self.registry_entry(folder).previous, USER_ICON)

    def test_an_interrupted_apply_does_not_lose_the_original_icon(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        with mock.patch.object(service_module, "write_custom_icon", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(folder, STAR)
        self.assertEqual(self.service.reset(folder), service_module.RESET_KEPT_USER_ICON)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_a_failed_metadata_write_leaves_registry_files_and_metadata_as_before(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        with mock.patch.object(service_module, "write_custom_icon", side_effect=MetadataError("boom")):
            with self.assertRaises(MetadataError):
                self.service.apply(folder, STAR)
        self.assertIsNone(self.registry_entry(folder))
        self.assertEqual(read_custom_icon(folder), USER_ICON)
        self.assertEqual(list(self.store.icons_dir.glob("*.svg")), [])

    def test_a_failed_reapply_restores_the_previous_entry(self):
        folder = self.make_folder()
        first = self.service.apply(folder, STAR)
        real = service_module.write_custom_icon
        calls = []

        def flaky(target, uri):
            calls.append(uri)
            if len(calls) == 1:
                raise MetadataError("boom")
            real(target, uri)

        with mock.patch.object(service_module, "write_custom_icon", side_effect=flaky):
            with self.assertRaises(MetadataError):
                self.service.apply(folder, Settings(glyph_name="heart-symbolic", color="#ff0000"))
        self.assertEqual(self.registry_entry(folder), first)
        self.assertTrue(self.store.icon_path(first.digest).exists())
        self.assertEqual(read_custom_icon(folder), self.store.icon_uri(first.digest))

    def test_a_write_that_fails_after_storing_the_value_is_rolled_back(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        real = service_module.write_custom_icon
        calls = []

        def stores_then_fails(target, uri):
            calls.append(uri)
            real(target, uri)
            if len(calls) == 1:
                raise MetadataError("read-back mismatch")

        with mock.patch.object(service_module, "write_custom_icon", side_effect=stores_then_fails):
            with self.assertRaises(MetadataError):
                self.service.apply(folder, STAR)
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_a_registry_write_failure_leaves_the_metadata_untouched(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        with mock.patch.object(Store, "put", side_effect=StoreError("disk full")):
            with self.assertRaises(StoreError):
                self.service.apply(folder, STAR)
        self.assertEqual(read_custom_icon(folder), USER_ICON)
        self.assertEqual(list(self.store.icons_dir.glob("*.svg")), [])

    def test_a_lost_registry_does_not_break_the_icons_of_other_folders(self):
        first, second, third = (self.make_folder(name) for name in ("a", "b", "c"))
        entry_a = self.service.apply(first, STAR)
        self.service.apply(second, Settings(glyph_name="heart-symbolic", color="#ff0000"))
        self.store.registry_path.unlink()
        self.store.registry_path.with_name("registry.json.bak").unlink()
        self.service.apply(third, Settings(glyph_name="star-symbolic", color="#00ff00"))
        self.assertTrue(self.store.icon_path(entry_a.digest).exists())
        self.assertEqual(len(list(self.store.icons_dir.glob("*.svg"))), 3)

    def test_a_folder_name_that_is_not_utf8_is_refused_without_side_effects(self):
        bad = os.fsencode(self.workspace) + b"/caf\xe9"
        os.mkdir(bad)
        self.addCleanup(os.rmdir, bad)
        with self.assertRaises(FolderError):
            self.service.apply(os.fsdecode(bad), STAR)
        self.assertEqual(self.store.all(), {})
        self.assertFalse(self.store.icons_dir.exists())

    def test_a_path_with_a_null_character_is_a_folder_error(self):
        with self.assertRaises(FolderError):
            self.service.apply("/tmp/a\u0000b", STAR)

    def test_reset_finds_an_entry_stored_under_an_unresolved_path(self):
        folder = self.make_folder()
        link = self.workspace / "link"
        link.symlink_to(folder)
        entry = self.service.apply(link, STAR)
        self.assertEqual(entry.folder, str(link))
        self.assertEqual(self.service.reset(link), RESET_RESTORED)
        self.assertIsNone(self.store.get(str(link)))
        self.assertIsNone(read_custom_icon(folder))

    def test_forget_finds_an_entry_stored_under_an_unresolved_path(self):
        folder = self.make_folder()
        link = self.workspace / "link2"
        link.symlink_to(folder)
        entry = self.service.apply(folder, STAR)
        self.store.remove(entry.folder)
        self.store.put(Entry(str(link), entry.digest, entry.theme, entry.color, entry.settings, entry.previous))
        self.service.forget(link)
        self.assertEqual(self.store.all(), {})

    def test_unreadable_metadata_is_reported_as_a_status(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        with mock.patch.object(service_module, "read_custom_icon", side_effect=MetadataError("no daemon")):
            self.assertEqual(self.service.entry_status(entry), service_module.STATUS_UNREADABLE)
            listed = self.service.customized()
        self.assertEqual([item.status for item in listed], [service_module.STATUS_UNREADABLE])

    def test_regeneration_skips_nothing_but_fails_cleanly_when_metadata_is_unreadable(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        report = RegenerationReport()
        with mock.patch.object(service_module, "read_custom_icon", side_effect=MetadataError("no daemon")):
            self.service.regenerate_entry(entry, report)
        self.assertIn(entry.folder, report.failed)

    def test_reset_of_an_unknown_folder_reports_it(self):
        self.assertEqual(self.service.reset(self.make_folder()), RESET_UNKNOWN)

    def test_regeneration_never_raises_for_a_damaged_entry(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        broken = Entry(entry.folder, entry.digest, entry.theme, entry.color, {"glyph_name": "star-symbolic", "size": "big"}, entry.previous)
        report = RegenerationReport()
        self.service.regenerate_entry(broken, report)
        self.assertIn(entry.folder, report.failed)

    def test_unexpected_os_errors_during_regeneration_are_reported(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, STAR)
        report = RegenerationReport()
        with mock.patch.object(Service_apply(), "apply", side_effect=OSError(5, "I/O error")):
            self.service.regenerate_entry(entry, report)
        self.assertIn(entry.folder, report.failed)


def Service_apply():
    return service_module.Service


class GlyphCopyTests(ServiceTestCase):
    def pictogram(self, name: str = "mine.svg", color: str = "#123456") -> Path:
        path = self.root / name
        path.write_text(glyph_svg(color))
        return path

    def test_the_pictogram_file_is_copied_into_the_store(self):
        folder = self.make_folder()
        source = self.pictogram()
        entry = self.service.apply(folder, Settings(glyph_file=str(source)))
        copy = self.store.glyph_path(entry.settings["glyph_copy"])
        self.assertEqual(copy.read_bytes(), source.read_bytes())

    def test_regeneration_works_after_the_original_file_is_gone(self):
        folder = self.make_folder()
        source = self.pictogram()
        entry = self.service.apply(folder, Settings(glyph_file=str(source), color="#ff0000"))
        source.unlink()
        report = self.service.regenerate_all()
        self.assertEqual(report.failed, {})
        self.assertEqual(report.unchanged, [entry.folder])

    def test_reapplying_with_a_vanished_file_keeps_the_stored_copy(self):
        folder = self.make_folder()
        source = self.pictogram()
        first = self.service.apply(folder, Settings(glyph_file=str(source)))
        source.unlink()
        second = self.service.apply(folder, Settings(glyph_file=str(source), color="#ff0000"))
        self.assertEqual(second.settings["glyph_copy"], first.settings["glyph_copy"])

    def test_the_stored_copy_is_used_when_the_file_is_unreadable(self):
        source = self.pictogram()
        first = self.service.apply(self.make_folder("a"), Settings(glyph_file=str(source)))
        source.unlink()
        settings = Settings(glyph_file=str(source), glyph_copy=first.settings["glyph_copy"])
        second = self.service.apply(self.make_folder("b"), settings)
        self.assertEqual(second.settings["glyph_copy"], first.settings["glyph_copy"])

    def test_a_vanished_file_without_a_copy_is_an_error(self):
        folder = self.make_folder()
        with self.assertRaises(service_module.InvalidIconError):
            self.service.apply(folder, Settings(glyph_file=str(self.root / "nothing.svg")))
        self.assertEqual(self.store.all(), {})

    def test_the_copy_is_removed_with_the_last_folder_using_it(self):
        first, second = self.make_folder("a"), self.make_folder("b")
        source = self.pictogram()
        entry = self.service.apply(first, Settings(glyph_file=str(source)))
        self.service.apply(second, Settings(glyph_file=str(source)))
        copy = self.store.glyph_path(entry.settings["glyph_copy"])
        self.service.reset(first)
        self.assertTrue(copy.exists())
        self.service.reset(second)
        self.assertFalse(copy.exists())

    def test_replacing_the_pictogram_releases_the_old_copy(self):
        folder = self.make_folder()
        first = self.service.apply(folder, Settings(glyph_file=str(self.pictogram("a.svg", "#111111"))))
        second = self.service.apply(folder, Settings(glyph_file=str(self.pictogram("b.svg", "#222222"))))
        self.assertFalse(self.store.glyph_path(first.settings["glyph_copy"]).exists())
        self.assertTrue(self.store.glyph_path(second.settings["glyph_copy"]).exists())

    def test_switching_to_a_named_icon_releases_the_copy(self):
        folder = self.make_folder()
        first = self.service.apply(folder, Settings(glyph_file=str(self.pictogram())))
        self.service.apply(folder, STAR)
        self.assertFalse(self.store.glyph_path(first.settings["glyph_copy"]).exists())


class RememberedAppearanceTests(ServiceTestCase):
    def test_a_fresh_store_remembers_nothing(self):
        self.assertEqual(self.service.remembered_appearance(), {})

    def test_the_appearance_is_remembered_without_the_pictogram(self):
        self.service.remember_appearance(Settings(glyph_name="star-symbolic", color="#ff8800", size=1.5, offset_x=0.1, offset_y=-0.2, tint=False))
        self.assertEqual(
            self.service.remembered_appearance(),
            {
                "color": "#ff8800",
                "accent": False,
                "size": 1.5,
                "offset_x": 0.1,
                "offset_y": -0.2,
                "tint": False,
                "folder_tint": None,
                "folder_tint_accent": False,
            },
        )
        self.assertNotIn("glyph_name", self.store.load_defaults())

    def test_an_automatic_color_is_remembered_as_automatic(self):
        self.service.remember_appearance(STAR)
        self.assertIn("color", self.service.remembered_appearance())
        self.assertIsNone(self.service.remembered_appearance()["color"])

    def test_damaged_values_fall_back_to_the_factory_appearance(self):
        for values in ({"size": 99}, {"size": "big"}, {"color": "red"}, {"offset_x": 5}, {"tint": "yes"}):
            self.store.save_defaults(values)
            self.assertEqual(self.service.remembered_appearance(), {}, str(values))

    def test_a_non_finite_number_falls_back_to_the_factory_appearance(self):
        self.store.save_defaults({"size": 1.0})
        self.store.defaults_path.write_text('{"size": NaN}')
        self.assertEqual(self.service.remembered_appearance(), {})

    def test_unknown_keys_are_ignored(self):
        self.store.save_defaults({"size": 1.2, "glyph_name": "x", "folder": "/etc"})
        self.assertEqual(self.service.remembered_appearance(), {"size": 1.2})

    def test_a_write_failure_does_not_break_the_caller(self):
        with mock.patch.object(Store, "save_defaults", side_effect=StoreError("disk full")):
            self.service.remember_appearance(STAR)

    def test_applying_an_icon_alone_does_not_change_the_memory(self):
        self.service.remember_appearance(Settings(glyph_name="star-symbolic", size=1.5))
        self.service.apply(self.make_folder(), Settings(glyph_name="heart-symbolic", size=0.5))
        self.assertEqual(self.service.remembered_appearance()["size"], 1.5)


class AccentTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.accent = "#ff0000"
        self.service = Service(
            self.store, theme_name=lambda: self.theme, search_dirs=[str(self.themes)], accent_color=lambda: self.accent
        )

    def test_the_accent_color_is_used_when_requested(self):
        entry = self.service.apply(self.make_folder(), Settings(glyph_name="star-symbolic", accent=True))
        self.assertEqual(entry.color, "#ff0000")
        self.assertTrue(entry.settings["accent"])

    def test_a_folder_tint_is_stored_and_changes_the_icon(self):
        plain = self.service.build(STAR)
        tinted = self.service.build(Settings(glyph_name="star-symbolic", folder_tint="#e01b24"))
        self.assertNotEqual(plain.svg, tinted.svg)
        self.assertEqual(tinted.folder_tint, "#e01b24")
        self.assertIsNone(plain.folder_tint)

    def test_the_automatic_glyph_color_follows_the_tinted_face(self):
        plain = parse_hex(self.service.build(STAR).color)
        tinted = parse_hex(self.service.build(Settings(glyph_name="star-symbolic", folder_tint="#e01b24")).color)
        self.assertGreater(plain[2], plain[0])
        self.assertGreater(tinted[0], tinted[2])

    def test_the_accent_folder_tint_resolves_and_stores_the_accent(self):
        entry = self.service.apply(self.make_folder(), Settings(glyph_name="star-symbolic", folder_tint_accent=True))
        self.assertEqual(entry.settings["folder_tint"], "#ff0000")
        self.assertTrue(entry.settings["folder_tint_accent"])

    def test_the_folder_preview_is_only_built_when_there_is_a_tint(self):
        self.assertEqual(self.service.folder_preview(STAR), (None, None))
        svg, tint = self.service.folder_preview(Settings(glyph_name="star-symbolic", folder_tint="#e01b24"))
        self.assertEqual(tint, "#e01b24")
        self.assertIn("feBlend", svg)
        svg, tint = self.service.folder_preview(Settings(glyph_name="star-symbolic", folder_tint_accent=True))
        self.assertEqual(tint, "#ff0000")

    def test_an_invalid_folder_tint_setting_is_refused(self):
        for value in ("red", "#12345", 5, "#ff0000\0"):
            with self.subTest(value=value), self.assertRaises(InvalidSettingsError):
                Settings(glyph_name="star-symbolic", folder_tint=value).validate()
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="star-symbolic", folder_tint_accent="yes").validate()

    def test_a_changed_accent_makes_an_accent_folder_tint_outdated(self):
        following, fixed = self.make_folder("a"), self.make_folder("b")
        self.service.apply(following, Settings(glyph_name="star-symbolic", folder_tint_accent=True))
        self.service.apply(fixed, Settings(glyph_name="star-symbolic", folder_tint="#e01b24"))
        self.assertEqual(self.service.outdated(), [])
        self.accent = "#00ff00"
        self.assertEqual(self.service.outdated(), [str(following.resolve())])

    def test_regeneration_follows_the_accent_of_the_folder_tint(self):
        folder = self.make_folder()
        self.service.apply(folder, Settings(glyph_name="star-symbolic", folder_tint_accent=True))
        self.accent = "#00ff00"
        self.service.regenerate_all()
        self.assertEqual(self.store.get(str(folder.resolve())).settings["folder_tint"], "#00ff00")
        self.assertEqual(self.service.outdated(), [])

    def test_the_folder_tint_is_part_of_the_remembered_appearance(self):
        self.service.remember_appearance(Settings(glyph_name="star-symbolic", folder_tint="#e01b24"))
        self.assertEqual(self.service.remembered_appearance()["folder_tint"], "#e01b24")

    def test_the_accent_and_a_manual_color_are_exclusive(self):
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="star-symbolic", accent=True, color="#00ff00").validate()

    def test_a_non_boolean_accent_is_refused(self):
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="star-symbolic", accent="yes").validate()

    def test_nothing_is_outdated_right_after_applying(self):
        self.service.apply(self.make_folder(), Settings(glyph_name="star-symbolic", accent=True))
        self.assertEqual(self.service.outdated(), [])

    def test_a_changed_theme_makes_every_icon_outdated(self):
        first, second = self.make_folder("a"), self.make_folder("b")
        self.service.apply(first, STAR)
        self.service.apply(second, STAR)
        self.theme = "Green"
        self.assertEqual(sorted(self.service.outdated()), sorted([str(first.resolve()), str(second.resolve())]))

    def test_a_changed_accent_only_affects_icons_that_follow_it(self):
        following, fixed = self.make_folder("a"), self.make_folder("b")
        self.service.apply(following, Settings(glyph_name="star-symbolic", accent=True))
        self.service.apply(fixed, STAR)
        self.accent = "#00ff00"
        self.assertEqual(self.service.outdated(), [str(following.resolve())])

    def test_regeneration_follows_the_new_accent_and_clears_the_list(self):
        folder = self.make_folder()
        self.service.apply(folder, Settings(glyph_name="star-symbolic", accent=True))
        self.accent = "#00ff00"
        self.service.regenerate_all()
        self.assertEqual(self.store.get(str(folder.resolve())).color, "#00ff00")
        self.assertEqual(self.service.outdated(), [])

    def test_folders_that_cannot_be_regenerated_are_not_reported(self):
        gone = self.make_folder("gone")
        changed = self.make_folder("changed")
        self.service.apply(gone, STAR)
        self.service.apply(changed, STAR)
        shutil.rmtree(gone)
        write_custom_icon(changed, USER_ICON)
        self.theme = "Green"
        self.assertEqual(self.service.outdated(), [])


class TextGlyphServiceTests(ServiceTestCase):
    def test_a_text_and_another_source_cannot_be_combined(self):
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="star-symbolic", glyph_text="A").validate()
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_file="/x.svg", glyph_text="A").validate()
        with self.assertRaises(InvalidSettingsError):
            Settings().validate()

    def test_a_text_alone_is_valid_and_an_empty_text_is_not_a_source(self):
        Settings(glyph_text="AB").validate()
        Settings(glyph_name="star-symbolic", glyph_text="").validate()
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_text="").validate()

    def test_a_text_that_is_too_long_is_refused(self):
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_text="ABCD").validate()

    def test_the_text_is_applied_and_stored_as_a_pictogram_copy(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, Settings(glyph_text="Z", color="#ff8800"))
        self.assertEqual(entry.settings["glyph_text"], "Z")
        copy = self.store.glyph_path(entry.settings["glyph_copy"])
        self.assertTrue(copy.exists())
        self.assertEqual(copy.suffix, ".svg")
        self.assertEqual(read_custom_icon(folder), self.store.icon_uri(entry.digest))

    def test_the_preview_works_without_storing_anything(self):
        built = self.service.build(Settings(glyph_text="Z", color="#ff8800"))
        self.assertIn("<image", built.svg)
        self.assertFalse(self.store.glyphs_dir.exists())

    def test_regeneration_does_not_need_the_font_again(self):
        folder = self.make_folder()
        entry = self.service.apply(folder, Settings(glyph_text="Z", color="#ff8800"))
        with mock.patch.object(service_module, "text_to_svg", side_effect=AssertionError("font used")):
            report = self.service.regenerate_all()
        self.assertEqual(report.failed, {})
        self.assertEqual(report.unchanged, [entry.folder])

    def test_an_unsupported_text_is_refused_without_side_effects(self):
        folder = self.make_folder()
        with mock.patch.object(service_module, "text_to_svg", side_effect=service_module.InvalidIconError("no shape")):
            with self.assertRaises(service_module.InvalidIconError):
                self.service.apply(folder, Settings(glyph_text="Z"))
        self.assertEqual(self.store.all(), {})
        self.assertIsNone(read_custom_icon(folder))

    def test_changing_the_text_releases_the_old_copy(self):
        folder = self.make_folder()
        first = self.service.apply(folder, Settings(glyph_text="A", color="#ff8800"))
        second = self.service.apply(folder, Settings(glyph_text="B", color="#ff8800"))
        self.assertFalse(self.store.glyph_path(first.settings["glyph_copy"]).exists())
        self.assertTrue(self.store.glyph_path(second.settings["glyph_copy"]).exists())

    def test_the_copy_is_shared_and_removed_with_the_last_folder(self):
        first, second = self.make_folder("a"), self.make_folder("b")
        entry = self.service.apply(first, Settings(glyph_text="A", color="#ff8800"))
        self.service.apply(second, Settings(glyph_text="A", color="#ff8800"))
        copy = self.store.glyph_path(entry.settings["glyph_copy"])
        self.service.reset(first)
        self.assertTrue(copy.exists())
        self.service.reset(second)
        self.assertFalse(copy.exists())

    def test_a_text_is_always_tinted(self):
        built = self.service.build(Settings(glyph_text="Z", color="#ff8800", tint=False))
        self.assertIn("#ff8800", inner_svg(built.svg))

    def test_a_theme_icon_is_always_tinted(self):
        built = self.service.build(Settings(glyph_name="star-symbolic", color="#ff8800", tint=False))
        self.assertIn("#ff8800", inner_svg(built.svg))

    def test_a_file_keeps_its_own_colors_when_tinting_is_off(self):
        path = self.root / "mine.svg"
        path.write_text(glyph_svg("#123456"))
        built = self.service.build(Settings(glyph_file=str(path), color="#ff8800", tint=False))
        self.assertIn("#123456", inner_svg(built.svg))
        self.assertNotIn("#ff8800", inner_svg(built.svg))


if __name__ == "__main__":
    unittest.main()
