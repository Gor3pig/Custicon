import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from custicon import customsets
from custicon.customsets import MAX_SETS, add_icons, icon_names, import_set, list_sets, remove_icon, remove_set, slugify
from custicon.errors import IconNotFoundError, IconSetError
from custicon.service import Service, Settings
from custicon.store import Store

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme, solid_png


class SetFolderCase(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.sets = self.root / "sets"
        self.source = self.root / "source"
        self.source.mkdir()

    def add(self, name: str, color: str = "#2e3436", folder: Path | None = None) -> Path:
        path = (folder or self.source) / name
        path.write_text(glyph_svg(color))
        return path

    def leftovers(self) -> list[str]:
        return [path.name for path in self.sets.iterdir() if path.name.startswith(".")] if self.sets.is_dir() else []


class ImportTests(SetFolderCase):
    def test_a_folder_of_svg_files_becomes_a_set(self):
        for name in ("star.svg", "Heart Shape.SVG", "été (2).svg"):
            self.add(name)
        report = import_set(self.sets, self.source, "Mes icônes")
        self.assertEqual((report.slug, report.added, report.ignored, report.replaced), ("mes-icones", 3, 0, False))
        info = list_sets(self.sets)
        self.assertEqual([(i.slug, i.name, i.count, i.key) for i in info], [("mes-icones", "Mes icônes", 3, "mine-mes-icones")])
        files = sorted(path.name for path in (self.sets / "mes-icones").glob("*.svg"))
        self.assertEqual(files, ["ete-2.svg", "heart-shape.svg", "star.svg"])
        self.assertEqual(self.leftovers(), [])

    def test_unusable_files_are_ignored_and_counted(self):
        self.add("good.svg")
        (self.source / "broken.svg").write_text("<svg")
        (self.source / "not-svg.svg").write_bytes(solid_png((1, 0, 0)))
        (self.source / "huge.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'>" + b" " * (3 * 1024 * 1024) + b"</svg>")
        (self.source / "notes.txt").write_text("x")
        (self.source / "empty.svg").write_bytes(b"")
        os.symlink(self.source / "good.svg", self.source / "link.svg")
        (self.source / "subdir.svg").mkdir()
        report = import_set(self.sets, self.source, "Mixed")
        self.assertEqual((report.added, report.ignored), (1, 6))

    def test_two_names_that_become_the_same_name_keep_the_first(self):
        self.add("A b.svg")
        self.add("a-b.svg")
        report = import_set(self.sets, self.source, "Dup")
        self.assertEqual((report.added, report.ignored), (1, 1))

    def test_a_folder_without_usable_file_is_refused_and_leaves_nothing(self):
        (self.source / "broken.svg").write_text("nope")
        with self.assertRaises(IconSetError):
            import_set(self.sets, self.source, "Nothing")
        self.assertEqual(list_sets(self.sets), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_missing_folder_or_a_file_is_refused(self):
        for path in (self.root / "nowhere", self.add("x.svg")):
            with self.assertRaises(IconSetError):
                import_set(self.sets, path, "Name")

    def test_importing_again_replaces_the_set_and_raises_its_revision(self):
        self.add("one.svg")
        self.add("two.svg")
        import_set(self.sets, self.source, "Set")
        (self.source / "one.svg").unlink()
        report = import_set(self.sets, self.source, "Set")
        self.assertTrue(report.replaced)
        self.assertEqual(sorted(p.name for p in (self.sets / "set").glob("*.svg")), ["two.svg"])
        self.assertEqual(json.loads((self.sets / "set" / "index.json").read_text())["revision"], 2)

    def test_a_failed_replacement_keeps_the_old_set(self):
        self.add("one.svg")
        import_set(self.sets, self.source, "Set")
        self.add("two.svg")
        real = os.replace
        calls = []

        def second_move_fails(source, target):
            calls.append(1)
            if len(calls) == 2:
                raise OSError(13, "denied")
            return real(source, target)

        with mock.patch.object(customsets.os, "replace", side_effect=second_move_fails):
            with self.assertRaises(IconSetError):
                import_set(self.sets, self.source, "Set")
        self.assertEqual(sorted(p.name for p in (self.sets / "set").glob("*.svg")), ["one.svg"])
        self.assertEqual(self.leftovers(), [])

    def test_at_most_eight_sets_but_one_can_be_replaced(self):
        self.add("a.svg")
        for number in range(MAX_SETS):
            import_set(self.sets, self.source, f"Set {number}")
        with self.assertRaises(IconSetError):
            import_set(self.sets, self.source, "One too many")
        self.assertEqual(import_set(self.sets, self.source, "Set 3").replaced, True)
        self.assertEqual(len(list_sets(self.sets)), MAX_SETS)

    def test_only_the_first_files_are_read_beyond_the_limit(self):
        for number in range(5):
            self.add(f"icon{number}.svg")
        with mock.patch.object(customsets, "MAX_FILES", 3):
            report = import_set(self.sets, self.source, "Big")
        self.assertEqual((report.added, report.ignored), (3, 2))

    def test_names_must_be_usable(self):
        self.add("a.svg")
        for name in ("", "   ", "x" * 41, "bad\x00name", "tab\there\x07", "\ud800", None, 5):
            with self.assertRaisesRegex(IconSetError, "name of the set", msg=repr(name)):
                import_set(self.sets, self.source, name)
        with self.assertRaisesRegex(IconSetError, "letter or digit"):
            import_set(self.sets, self.source, "!!!")

    def test_a_name_with_spaces_is_normalized_and_kept_for_display(self):
        self.add("a.svg")
        import_set(self.sets, self.source, "  Mon   Travail ")
        self.assertEqual([(i.slug, i.name) for i in list_sets(self.sets)], [("mon-travail", "Mon Travail")])

    def test_removing_a_set_deletes_it(self):
        self.add("a.svg")
        import_set(self.sets, self.source, "Gone")
        remove_set(self.sets, "gone")
        self.assertEqual(list_sets(self.sets), [])

    def test_removing_refuses_anything_that_is_not_a_set(self):
        self.add("a.svg")
        import_set(self.sets, self.source, "Real")
        (self.root / "victim").mkdir()
        for slug in ("../victim", "", "nope", "/", ".hidden", None):
            with self.assertRaises(IconSetError, msg=repr(slug)):
                remove_set(self.sets, slug)
        self.assertTrue((self.root / "victim").is_dir())

    def test_slugify(self):
        self.assertEqual(slugify("Été Ñandú!"), "ete-nandu")
        self.assertEqual(slugify("---"), "")
        self.assertEqual(slugify("a" * 100, 10), "a" * 10)

    def test_damaged_metadata_falls_back_to_the_folder_name(self):
        self.add("a.svg")
        import_set(self.sets, self.source, "Fine")
        (self.sets / "fine" / "meta.json").write_text("{not json")
        (self.sets / "fine" / "index.json").write_text("[]")
        self.assertEqual([(i.slug, i.name) for i in list_sets(self.sets)], [("fine", "fine")])


class IconEditingTests(SetFolderCase):
    def setUp(self):
        super().setUp()
        self.add("one.svg")
        self.add("two.svg")
        import_set(self.sets, self.source, "Set")
        self.extra = self.root / "extra"
        self.extra.mkdir()

    def revision(self):
        return json.loads((self.sets / "set" / "index.json").read_text())["revision"]

    def test_icons_are_listed_by_name(self):
        self.assertEqual(icon_names(self.sets, "set"), ["one", "two"])

    def test_new_files_are_added_to_the_set(self):
        three = self.add("Three Icon.svg", folder=self.extra)
        four = self.add("four.svg", folder=self.extra)
        report = add_icons(self.sets, "set", [three, four])
        self.assertEqual((report.added, report.replaced, report.ignored), (2, 0, 0))
        self.assertEqual(icon_names(self.sets, "set"), ["four", "one", "three-icon", "two"])
        self.assertEqual(self.revision(), 1)
        self.assertEqual([path.name for path in self.sets.iterdir() if path.name.startswith(".")], [])

    def test_a_file_with_the_same_name_replaces_the_icon_and_raises_the_revision(self):
        newer = self.extra / "one.svg"
        newer.write_text(glyph_svg().replace("M0 0h16v16H0z", "M3 3h10v10H3z"))
        report = add_icons(self.sets, "set", [newer])
        self.assertEqual((report.added, report.replaced, report.ignored), (0, 1, 0))
        self.assertIn("M3 3h10v10H3z", (self.sets / "set" / "one.svg").read_text())
        self.assertEqual(self.revision(), 2)

    def test_an_identical_file_changes_nothing(self):
        same = self.add("one.svg", folder=self.extra)
        report = add_icons(self.sets, "set", [same])
        self.assertEqual((report.added, report.replaced, report.ignored), (0, 0, 1))
        self.assertEqual(self.revision(), 1)

    def test_unusable_files_are_ignored(self):
        (self.extra / "broken.svg").write_text("<svg")
        (self.extra / "notes.txt").write_text("x")
        (self.extra / "pic.svg").write_bytes(solid_png((1, 0, 0)))
        good = self.add("good.svg", folder=self.extra)
        report = add_icons(self.sets, "set", [self.extra / "broken.svg", self.extra / "notes.txt", self.extra / "pic.svg", good, self.extra / "nowhere.svg", good])
        self.assertEqual((report.added, report.ignored), (1, 5))

    def test_only_svg_files_are_taken_even_when_the_content_is_valid(self):
        disguised = self.extra / "valid.txt"
        disguised.write_text(glyph_svg())
        report = add_icons(self.sets, "set", [disguised])
        self.assertEqual((report.added, report.ignored), (0, 1))

    def test_the_same_name_twice_in_one_call_keeps_the_first(self):
        first = self.add("dup.svg", folder=self.extra)
        other = self.root / "other"
        other.mkdir()
        second = self.add("Dup.svg", folder=other)
        report = add_icons(self.sets, "set", [first, second])
        self.assertEqual((report.added, report.ignored), (1, 1))

    def test_the_file_limit_applies_to_additions_but_not_to_replacements(self):
        files = [self.add(f"n{number}.svg", folder=self.extra) for number in range(3)]
        with mock.patch.object(customsets, "MAX_FILES", 3):
            report = add_icons(self.sets, "set", files)
            self.assertEqual((report.added, report.ignored), (1, 2))
            again = self.extra / "one.svg"
            again.write_text(glyph_svg().replace("M0 0h16v16H0z", "M1 1h5v5H1z"))
            self.assertEqual(add_icons(self.sets, "set", [again]).replaced, 1)

    def test_an_unknown_set_is_refused(self):
        for slug in ("nope", "../x", "", None):
            with self.assertRaises(IconSetError, msg=repr(slug)):
                add_icons(self.sets, slug, [])

    def test_an_icon_can_be_removed_but_never_the_last_one(self):
        remove_icon(self.sets, "set", "one")
        self.assertEqual(icon_names(self.sets, "set"), ["two"])
        with self.assertRaisesRegex(IconSetError, "at least one"):
            remove_icon(self.sets, "set", "two")

    def test_removing_refuses_unknown_names_and_paths(self):
        (self.root / "victim.svg").write_text(glyph_svg())
        for icon in ("absent", "../../victim", "", None, "one.svg"):
            with self.assertRaisesRegex(IconSetError, "unknown icon", msg=repr(icon)):
                remove_icon(self.sets, "set", icon)
        self.assertTrue((self.root / "victim.svg").is_file())


class ServiceSetTests(SetFolderCase):
    def setUp(self):
        super().setUp()
        themes = self.root / "themes"
        themes.mkdir()
        make_theme(themes, "Blue", folder=folder_svg("#3366cc"), glyphs={"star-symbolic": glyph_svg()})
        self.store = Store(self.root / "data")
        self.service = Service(self.store, theme_name=lambda: "Blue", search_dirs=[str(themes)], backend="directory")
        self.folder = self.root / "folder"
        self.folder.mkdir()
        self.add("my-icon.svg", "#ff0000")
        self.add("other.svg", "#00ff00")
        self.report = self.service.import_set(self.source, "Mine")
        self.name = "mine-mine:my-icon"

    def test_the_set_is_known_by_the_icon_sets_and_searchable(self):
        icon_sets = self.service.icon_sets()
        self.assertEqual(icon_sets.user_keys(), ["mine-mine"])
        self.assertEqual(icon_sets.for_name(self.name).key, "mine-mine")
        self.assertEqual(icon_sets["mine-mine"].symbolic_names("my icon"), ["mine-mine:my-icon"])
        self.assertEqual(sorted(icon_sets["mine-mine"].all_names()), ["mine-mine:my-icon", "mine-mine:other"])

    def test_a_pictogram_of_a_set_is_drawn_and_copied(self):
        entry = self.service.apply(self.folder, Settings(glyph_name=self.name))
        self.assertIn("glyph_copy", entry.settings)
        self.assertTrue(self.store.glyph_path(entry.settings["glyph_copy"]).is_file())
        self.assertIn("<image", self.store.icon_path(entry.digest).read_text())

    def test_folders_survive_the_removal_of_their_set(self):
        entry = self.service.apply(self.folder, Settings(glyph_name=self.name))
        self.service.remove_set("mine")
        self.assertEqual(self.service.custom_sets(), [])
        report = self.service.regenerate_all()
        self.assertEqual(report.failed, {})
        self.assertEqual(self.service.entry_status(self.store.get(str(self.folder))), "ok")
        self.assertEqual(self.store.get(str(self.folder)).digest, entry.digest)

    def test_without_the_set_and_without_a_copy_the_error_is_clean(self):
        self.service.remove_set("mine")
        with self.assertRaises(IconNotFoundError):
            self.service.apply(self.folder, Settings(glyph_name=self.name))
        self.assertEqual(self.store.all(), {})

    def test_an_icon_missing_from_the_set_is_an_error(self):
        with self.assertRaises(IconNotFoundError):
            self.service.apply(self.folder, Settings(glyph_name="mine-mine:absent"))

    def test_importing_again_marks_the_folders_as_outdated(self):
        entry = self.service.apply(self.folder, Settings(glyph_name=self.name))
        self.assertEqual(self.service.outdated(), [])
        (self.source / "my-icon.svg").write_text(glyph_svg().replace("M0 0h16v16H0z", "M2 2h12v12H2z"))
        self.service.import_set(self.source, "Mine")
        self.assertEqual(self.service.outdated(), [entry.folder])
        report = self.service.regenerate_all()
        self.assertEqual(report.updated, [entry.folder])

    def test_a_backup_restored_without_the_set_reports_the_folder(self):
        self.service.apply(self.folder, Settings(glyph_name=self.name))
        backup = self.root / "backup.json"
        self.service.export_backup(backup)
        fresh = Service(Store(self.root / "other"), theme_name=lambda: "Blue", search_dirs=[str(self.root / "themes")], backend="directory")
        report = fresh.import_backup(backup)
        self.assertEqual(report.restored, [])
        self.assertEqual(list(report.failed), [str(self.folder)])

    def test_added_icons_are_found_by_the_set_and_removed_ones_keep_working(self):
        entry = self.service.apply(self.folder, Settings(glyph_name=self.name))
        extra = self.root / "extra"
        extra.mkdir()
        (extra / "brand new.svg").write_text(glyph_svg())
        self.assertEqual(self.service.add_icons("mine", [extra / "brand new.svg"]).added, 1)
        self.assertEqual(self.service.icon_sets()["mine-mine"].symbolic_names("brand"), ["mine-mine:brand-new"])
        self.service.remove_icon("mine", "my-icon")
        self.assertEqual(self.service.set_icons("mine"), ["brand-new", "other"])
        self.assertNotIn("mine-mine:my-icon", self.service.icon_sets()["mine-mine"].all_names())
        report = self.service.regenerate_all()
        self.assertEqual(report.failed, {})
        self.assertEqual(self.store.get(str(self.folder)).digest, entry.digest)

    def test_replacing_an_icon_marks_the_folders_that_use_the_set_as_outdated(self):
        entry = self.service.apply(self.folder, Settings(glyph_name=self.name))
        extra = self.root / "extra"
        extra.mkdir()
        (extra / "my-icon.svg").write_text(glyph_svg().replace("M0 0h16v16H0z", "M2 2h12v12H2z"))
        self.assertEqual(self.service.add_icons("mine", [extra / "my-icon.svg"]).replaced, 1)
        self.assertEqual(self.service.outdated(), [entry.folder])

    def test_the_builtin_sets_are_unchanged(self):
        icon_sets = self.service.icon_sets()
        self.assertEqual(list(icon_sets)[:3], ["phosphor", "emoji", "brands"])
        self.assertIs(icon_sets.for_name("phosphor:star"), icon_sets["phosphor"])


if __name__ == "__main__":
    unittest.main()
