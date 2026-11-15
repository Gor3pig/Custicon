import locale
import os
import unittest
from dataclasses import replace
from pathlib import Path

from custicon.errors import (
    CusticonError,
    FolderError,
    IconNotFoundError,
    InvalidIconError,
    InvalidSettingsError,
    MetadataError,
    StoreError,
)
from custicon.service import (
    STATUS_FOLDER_MISSING,
    STATUS_ICON_CHANGED,
    STATUS_ICON_FILE_MISSING,
    STATUS_OK,
    STATUS_UNREADABLE,
    BackupSummary,
    RegenerationReport,
    ResetReport,
    RestoreReport,
    Settings,
)
from custicon.store import Entry
from custicon.ui.draft import DEFAULT_COLOR, DEFAULT_FOLDER_TINT, Draft
from custicon.ui.format import normalize_text, display_path, folder_name, from_percent, is_portal_path, is_text_path, percent_text, to_percent
from custicon.ui.messages import (
    backup_summary_lines,
    banner_action,
    describe_error,
    report_lines,
    reset_report_lines,
    restore_report_lines,
    status_banner,
    status_label,
)


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path("/home/someone/Projects")

    def test_a_new_draft_has_no_pictogram(self):
        draft = Draft(self.folder)
        self.assertFalse(draft.ready)
        self.assertTrue(draft.auto_color)
        self.assertEqual(draft.color, DEFAULT_COLOR)
        self.assertTrue(draft.has_default_appearance())

    def test_a_badge_alone_makes_the_draft_ready_without_a_central_pictogram(self):
        draft = Draft(self.folder, badge_on=True, badge_glyph="heart-symbolic")
        self.assertTrue(draft.ready)
        self.assertFalse(draft.has_glyph)
        self.assertFalse(replace(draft, badge_on=False).ready)

    def test_removing_the_central_pictogram_keeps_the_badge(self):
        draft = Draft(self.folder, glyph_text="ab", badge_on=True, badge_glyph="heart-symbolic").without_glyph()
        self.assertEqual((draft.glyph_name, draft.glyph_file, draft.glyph_text), (None, None, None))
        self.assertIsNone(draft.to_settings().glyph_text)
        self.assertEqual(draft.to_settings().badge_glyph, "heart-symbolic")

    def test_choosing_a_system_icon_replaces_a_file(self):
        draft = Draft(self.folder).with_glyph_file("/x/a.svg").with_glyph_name("star-symbolic")
        self.assertEqual((draft.glyph_name, draft.glyph_file), ("star-symbolic", None))
        self.assertTrue(draft.ready)

    def test_choosing_a_file_replaces_a_system_icon(self):
        draft = Draft(self.folder).with_glyph_name("star-symbolic").with_glyph_file(Path("/x/a.svg"))
        self.assertEqual((draft.glyph_name, draft.glyph_file), (None, "/x/a.svg"))
        self.assertTrue(draft.ready)

    def test_automatic_color_gives_no_color_in_the_settings(self):
        settings = Draft(self.folder, glyph_name="a-symbolic", color="#ff0000").to_settings()
        self.assertIsNone(settings.color)

    def test_manual_color_is_kept_in_the_settings(self):
        settings = Draft(self.folder, glyph_name="a-symbolic", color="#ff0000", color_mode="custom").to_settings()
        self.assertEqual(settings.color, "#ff0000")

    def test_settings_carry_every_field(self):
        draft = Draft(self.folder, glyph_file="/x/a.png", tint=False, size=1.5, offset_x=0.1, offset_y=-0.2)
        self.assertEqual(
            draft.to_settings(),
            Settings(glyph_file="/x/a.png", color=None, size=1.5, offset_x=0.1, offset_y=-0.2, tint=False),
        )

    def test_default_appearance_resets_everything_but_the_pictogram(self):
        draft = Draft(self.folder, glyph_name="a-symbolic", color_mode="custom", size=2.0, offset_x=0.2, offset_y=0.3)
        reset = draft.with_default_appearance()
        self.assertTrue(reset.has_default_appearance())
        self.assertEqual(reset.glyph_name, "a-symbolic")
        self.assertFalse(draft.has_default_appearance())

    def test_each_appearance_field_breaks_the_default(self):
        for changes in (
            {"color_mode": "custom"},
            {"size": 1.1},
            {"offset_x": 0.01},
            {"offset_y": -0.01},
            {"folder_tint_mode": "custom"},
            {"folder_tint_mode": "accent"},
        ):
            self.assertFalse(Draft(self.folder, **changes).has_default_appearance())

    def test_round_trip_through_an_entry(self):
        draft = Draft(self.folder, glyph_name="a-symbolic", color_mode="custom", color="#123456", size=1.2, offset_x=0.1, offset_y=-0.1, tint=False)
        entry = Entry(str(self.folder), "d", "Adwaita", "#123456", draft.to_settings().to_dict(), None)
        self.assertEqual(Draft.from_entry(entry), draft)

    def test_an_automatic_entry_remembers_the_computed_color(self):
        entry = Entry(str(self.folder), "d", "Adwaita", "#438de6", Settings(glyph_name="a-symbolic").to_dict(), None)
        draft = Draft.from_entry(entry)
        self.assertTrue(draft.auto_color)
        self.assertEqual(draft.color, "#438de6")


class DamagedEntryTests(unittest.TestCase):
    def draft(self, **settings):
        entry = Entry("/f", "0" * 32, "Adwaita", "#438de6", settings, None)
        return Draft.from_entry(entry)

    def test_numbers_that_are_not_numbers_fall_back_to_defaults(self):
        draft = self.draft(glyph_name="a-symbolic", size="big", offset_x=None, offset_y=True)
        self.assertEqual((draft.size, draft.offset_x, draft.offset_y), (1.0, 0.0, 0.0))

    def test_out_of_range_numbers_are_clamped(self):
        draft = self.draft(glyph_name="a-symbolic", size=1e9, offset_x=-50, offset_y=float("nan"))
        self.assertEqual(draft.size, 3.0)
        self.assertEqual(draft.offset_x, -0.5)
        self.assertEqual(draft.offset_y, 0.0)

    def test_a_bad_color_or_tint_falls_back(self):
        draft = self.draft(glyph_name="a-symbolic", color="red", tint="yes")
        self.assertTrue(draft.auto_color)
        self.assertEqual(draft.color, "#438de6")
        self.assertIs(draft.tint, True)
        self.assertIs(self.draft(glyph_name="a-symbolic", tint=0).tint, True)

    def test_texts_that_are_not_text_are_ignored(self):
        draft = self.draft(glyph_name=["a"], glyph_file=3)
        self.assertFalse(draft.ready)

    def test_a_text_that_cannot_be_encoded_is_ignored(self):
        draft = self.draft(glyph_text="\ud800", badge_glyph="\udc80")
        self.assertFalse(draft.has_glyph)
        self.assertIsNone(draft.badge_glyph)

    def test_the_kept_copy_of_a_set_pictogram_follows_the_draft_until_the_pictogram_changes(self):
        from custicon.store import Entry

        entry = Entry("/tmp/x", "a" * 16, "Adwaita", "#ffffff", {"glyph_name": "mine-s:i", "glyph_copy": "b" * 16 + ".svg"}, None)
        draft = Draft.from_entry(entry)
        self.assertEqual(draft.to_settings().glyph_copy, "b" * 16 + ".svg")
        self.assertIsNone(draft.with_glyph_name("phosphor:star").to_settings().glyph_copy)
        self.assertIsNone(draft.with_glyph_text("A").to_settings().glyph_copy)

    def test_a_name_wins_over_a_file(self):
        draft = self.draft(glyph_name="a-symbolic", glyph_file="/x.svg")
        self.assertEqual((draft.glyph_name, draft.glyph_file), ("a-symbolic", None))


class TextPathTests(unittest.TestCase):
    def test_only_utf8_encodable_paths_are_text(self):
        self.assertTrue(is_text_path("/home/été"))
        self.assertFalse(is_text_path(os.fsdecode(b"/tmp/caf\xe9")))


class FormatTests(unittest.TestCase):
    def test_paths_inside_home_are_shortened(self):
        home = Path("/home/someone")
        self.assertEqual(display_path(home / "Documents" / "A", home), "~/Documents/A")
        self.assertEqual(display_path(home, home), "~")

    def test_paths_outside_home_are_kept(self):
        self.assertEqual(display_path("/srv/data", Path("/home/someone")), "/srv/data")

    def test_a_sibling_with_the_same_prefix_is_not_inside_home(self):
        self.assertEqual(display_path("/home/someone2/x", Path("/home/someone")), "/home/someone2/x")

    def test_folder_name(self):
        self.assertEqual(folder_name("/a/b/Projects"), "Projects")
        self.assertEqual(folder_name("/"), "/")

    def test_percent_conversions(self):
        self.assertEqual(percent_text(1.0), "100%")
        self.assertEqual(percent_text(0.5), "50%")
        self.assertEqual(percent_text(-0.25), "-25%")
        self.assertEqual(to_percent(1.5), 150)
        self.assertEqual(from_percent(150), 1.5)

    def test_portal_paths_are_detected(self):
        self.assertTrue(is_portal_path("/run/user/1000/doc/abcd1234/Projects"))
        self.assertFalse(is_portal_path("/home/someone/doc/Projects"))
        self.assertFalse(is_portal_path("/run/user/1000/gvfs/x"))
        self.assertFalse(is_portal_path("/run/media/someone/disk"))


class MessageTests(unittest.TestCase):
    def test_each_error_type_gets_a_specific_summary_and_the_detail(self):
        errors = (
            IconNotFoundError("detail-a"),
            InvalidIconError("detail-b"),
            InvalidSettingsError("detail-c"),
            FolderError("detail-d"),
            MetadataError("detail-e"),
            StoreError("detail-f"),
        )
        summaries = set()
        for error in errors:
            text = describe_error(error)
            summary, _, detail = text.partition("\n\n")
            self.assertEqual(detail, str(error))
            self.assertTrue(summary)
            summaries.add(summary)
        self.assertEqual(len(summaries), len(errors))

    def test_a_generic_custicon_error_shows_its_message(self):
        self.assertEqual(describe_error(CusticonError("plain")), "plain")

    def test_a_foreign_exception_is_not_leaked(self):
        self.assertNotIn("secret", describe_error(RuntimeError("secret internals")))

    def test_status_labels(self):
        for status in (STATUS_FOLDER_MISSING, STATUS_ICON_CHANGED, STATUS_ICON_FILE_MISSING, STATUS_UNREADABLE):
            self.assertTrue(status_label(status))
            self.assertTrue(status_banner(status))
        self.assertIsNone(status_label(STATUS_OK))
        self.assertIsNone(status_banner(STATUS_OK))

    def test_banner_actions_only_exist_where_they_help(self):
        self.assertTrue(banner_action(STATUS_ICON_CHANGED))
        self.assertTrue(banner_action(STATUS_ICON_FILE_MISSING))
        self.assertEqual(banner_action(STATUS_FOLDER_MISSING), "Find the Folder…")
        self.assertIsNone(banner_action(STATUS_OK))

    def test_an_empty_report_has_no_lines(self):
        self.assertEqual(report_lines(RegenerationReport()), [])

    def test_report_singular_and_plural(self):
        one = report_lines(RegenerationReport(updated=["a"]))
        many = report_lines(RegenerationReport(updated=["a", "b"]))
        self.assertEqual(one, ["1 icon updated"])
        self.assertEqual(many, ["2 icons updated"])

    def test_report_lists_every_category(self):
        report = RegenerationReport(
            updated=["a"],
            unchanged=["b", "c"],
            skipped={"d": STATUS_ICON_CHANGED, "e": STATUS_FOLDER_MISSING, "f": STATUS_FOLDER_MISSING},
            failed={"g": "boom"},
        )
        self.assertEqual(
            report_lines(report),
            [
                "1 icon updated",
                "2 icons already up to date",
                "1 folder skipped: icon changed outside Custicon",
                "2 folders not found",
                "1 folder could not be updated",
            ],
        )


class AppearanceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.draft = Draft(Path("/home/someone/Projects"), glyph_name="a-symbolic")

    def test_nothing_changes_without_remembered_values(self):
        self.assertEqual(self.draft.with_appearance({}), self.draft)

    def test_a_manual_color_is_restored_as_manual(self):
        draft = self.draft.with_appearance({"color": "#ff8800", "size": 1.5, "offset_x": 0.1, "offset_y": -0.1, "tint": False})
        self.assertFalse(draft.auto_color)
        self.assertEqual((draft.color, draft.size, draft.offset_x, draft.offset_y, draft.tint), ("#ff8800", 1.5, 0.1, -0.1, False))

    def test_an_automatic_color_is_restored_as_automatic(self):
        manual = replace(self.draft, color_mode="custom", color="#112233")
        draft = manual.with_appearance({"color": None})
        self.assertTrue(draft.auto_color)
        self.assertEqual(draft.color, "#112233")

    def test_the_pictogram_is_never_touched(self):
        draft = self.draft.with_appearance({"size": 2.0})
        self.assertEqual((draft.glyph_name, draft.glyph_file), ("a-symbolic", None))


class FinePercentTests(unittest.TestCase):
    def setUp(self):
        saved = locale.setlocale(locale.LC_NUMERIC)
        self.addCleanup(locale.setlocale, locale.LC_NUMERIC, saved)
        locale.setlocale(locale.LC_NUMERIC, "C")

    def test_the_decimal_separator_follows_the_locale(self):
        try:
            locale.setlocale(locale.LC_NUMERIC, "fr_FR.UTF-8")
        except locale.Error:
            self.skipTest("the French locale is not installed")
        self.assertEqual(percent_text(0.125), "12,5%")

    def test_half_percent_steps_are_shown(self):
        self.assertEqual(percent_text(0.125), "12.5%")
        self.assertEqual(percent_text(1.005), "100.5%")
        self.assertEqual(percent_text(-0.005), "-0.5%")

    def test_whole_percentages_stay_whole(self):
        self.assertEqual(percent_text(1.5), "150%")
        self.assertEqual(percent_text(0.0), "0%")

    def test_conversion_does_not_accumulate_float_noise(self):
        self.assertEqual(from_percent(100.5), 1.005)
        self.assertEqual(from_percent(12.5), 0.125)
        self.assertEqual(to_percent(from_percent(33.5)), 33.5)
        self.assertEqual(from_percent(33.333333), 0.3333)


class ColorModeTests(unittest.TestCase):
    def setUp(self):
        self.draft = Draft(Path("/home/someone/Projects"), glyph_name="a-symbolic")

    def test_the_default_mode_is_automatic(self):
        self.assertEqual(self.draft.color_mode, "auto")
        settings = self.draft.to_settings()
        self.assertEqual((settings.color, settings.accent), (None, False))

    def test_the_accent_mode_is_sent_as_an_accent_request_without_a_color(self):
        settings = replace(self.draft, color_mode="accent", color="#112233").to_settings()
        self.assertEqual((settings.color, settings.accent), (None, True))
        settings.validate()

    def test_the_custom_mode_sends_the_color_only(self):
        settings = replace(self.draft, color_mode="custom", color="#112233").to_settings()
        self.assertEqual((settings.color, settings.accent), ("#112233", False))

    def test_an_entry_that_follows_the_accent_reopens_in_accent_mode(self):
        entry = Entry("/f", "0" * 32, "Adwaita", "#ff0000", {"glyph_name": "a-symbolic", "accent": True}, None)
        draft = Draft.from_entry(entry)
        self.assertEqual(draft.color_mode, "accent")
        self.assertEqual(draft.color, "#ff0000")

    def test_a_damaged_accent_flag_falls_back_to_automatic(self):
        entry = Entry("/f", "0" * 32, "Adwaita", "#ff0000", {"glyph_name": "a-symbolic", "accent": "yes"}, None)
        self.assertEqual(Draft.from_entry(entry).color_mode, "auto")

    def test_a_manual_color_wins_over_a_stray_accent_flag_when_reading_an_entry(self):
        entry = Entry("/f", "0" * 32, "Adwaita", "#ff0000", {"glyph_name": "a-symbolic", "accent": True, "color": "#00ff00"}, None)
        draft = Draft.from_entry(entry)
        self.assertEqual((draft.color_mode, draft.color), ("custom", "#00ff00"))

    def test_remembered_values_restore_each_mode(self):
        self.assertEqual(self.draft.with_appearance({"color": None, "accent": True}).color_mode, "accent")
        custom = self.draft.with_appearance({"color": "#ff8800", "accent": False})
        self.assertEqual((custom.color_mode, custom.color), ("custom", "#ff8800"))
        self.assertEqual(replace(self.draft, color_mode="accent").with_appearance({"color": None, "accent": False}).color_mode, "auto")

    def test_resetting_the_appearance_goes_back_to_automatic(self):
        self.assertEqual(replace(self.draft, color_mode="accent").with_default_appearance().color_mode, "auto")
        self.assertFalse(replace(self.draft, color_mode="accent").has_default_appearance())


class FolderTintDraftTests(unittest.TestCase):
    def setUp(self):
        self.draft = Draft(Path("/home/someone/Projects"), glyph_name="a-symbolic")

    def entry(self, **settings):
        return Entry("/f", "0" * 32, "Adwaita", "#438de6", {"glyph_name": "a-symbolic", **settings}, None)

    def test_there_is_no_folder_tint_by_default(self):
        settings = self.draft.to_settings()
        self.assertEqual((settings.folder_tint, settings.folder_tint_accent), (None, False))
        self.assertEqual(self.draft.folder_tint, DEFAULT_FOLDER_TINT)

    def test_each_mode_is_sent_as_settings(self):
        custom = replace(self.draft, folder_tint_mode="custom", folder_tint="#112233").to_settings()
        self.assertEqual((custom.folder_tint, custom.folder_tint_accent), ("#112233", False))
        accent = replace(self.draft, folder_tint_mode="accent", folder_tint="#112233").to_settings()
        self.assertEqual((accent.folder_tint, accent.folder_tint_accent), (None, True))
        accent.validate()

    def test_the_appearance_carries_the_folder_tint(self):
        appearance = replace(self.draft, folder_tint_mode="custom", folder_tint="#112233").appearance()
        self.assertEqual((appearance["folder_tint"], appearance["folder_tint_accent"]), ("#112233", False))
        appearance = replace(self.draft, folder_tint_mode="accent").appearance()
        self.assertEqual((appearance["folder_tint"], appearance["folder_tint_accent"]), (None, True))

    def test_remembered_values_restore_each_mode(self):
        custom = self.draft.with_appearance({"folder_tint": "#ff8800", "folder_tint_accent": False})
        self.assertEqual((custom.folder_tint_mode, custom.folder_tint), ("custom", "#ff8800"))
        accent = self.draft.with_appearance({"folder_tint": None, "folder_tint_accent": True})
        self.assertEqual(accent.folder_tint_mode, "accent")
        none = replace(custom, folder_tint_mode="accent").with_appearance({"folder_tint": None, "folder_tint_accent": False})
        self.assertEqual(none.folder_tint_mode, "none")

    def test_an_older_appearance_without_the_fields_keeps_the_current_tint(self):
        draft = replace(self.draft, folder_tint_mode="custom").with_appearance({"size": 1.2})
        self.assertEqual(draft.folder_tint_mode, "custom")

    def test_resetting_the_appearance_removes_the_tint(self):
        draft = replace(self.draft, folder_tint_mode="custom").with_default_appearance()
        self.assertEqual(draft.folder_tint_mode, "none")
        self.assertTrue(draft.has_default_appearance())

    def test_entries_reopen_in_the_right_mode(self):
        self.assertEqual(Draft.from_entry(self.entry()).folder_tint_mode, "none")
        custom = Draft.from_entry(self.entry(folder_tint="#00ff00"))
        self.assertEqual((custom.folder_tint_mode, custom.folder_tint), ("custom", "#00ff00"))
        accent = Draft.from_entry(self.entry(folder_tint="#00ff00", folder_tint_accent=True))
        self.assertEqual((accent.folder_tint_mode, accent.folder_tint), ("accent", "#00ff00"))

    def test_damaged_values_fall_back_to_no_tint(self):
        for settings in ({"folder_tint": "green"}, {"folder_tint": 5}, {"folder_tint_accent": "yes"}):
            with self.subTest(settings=settings):
                draft = Draft.from_entry(self.entry(**settings))
                self.assertEqual(draft.folder_tint_mode, "none")
                self.assertEqual(draft.folder_tint, DEFAULT_FOLDER_TINT)

    def test_round_trip_through_an_entry(self):
        draft = replace(self.draft, folder_tint_mode="custom", folder_tint="#123456")
        entry = Entry("/home/someone/Projects", "d", "Adwaita", "#438de6", draft.to_settings().to_dict(), None)
        self.assertEqual(Draft.from_entry(entry).folder_tint_mode, "custom")
        self.assertEqual(Draft.from_entry(entry).folder_tint, "#123456")


class TextGlyphDraftTests(unittest.TestCase):
    def setUp(self):
        self.draft = Draft(Path("/home/someone/Projects"))

    def test_a_text_makes_the_draft_ready(self):
        self.assertFalse(self.draft.ready)
        self.assertTrue(self.draft.with_glyph_text("AB").ready)

    def test_an_empty_text_is_not_a_pictogram(self):
        draft = self.draft.with_glyph_text("AB").with_glyph_text("")
        self.assertIsNone(draft.glyph_text)
        self.assertFalse(draft.ready)

    def test_choosing_a_text_clears_the_other_sources(self):
        draft = Draft(self.draft.folder, glyph_name="a-symbolic").with_glyph_text("Z")
        self.assertEqual((draft.glyph_name, draft.glyph_file, draft.glyph_text), (None, None, "Z"))
        draft = Draft(self.draft.folder, glyph_file="/x.svg").with_glyph_text("Z")
        self.assertEqual((draft.glyph_name, draft.glyph_file, draft.glyph_text), (None, None, "Z"))

    def test_choosing_an_icon_or_a_file_clears_the_text(self):
        draft = self.draft.with_glyph_text("Z")
        self.assertIsNone(draft.with_glyph_name("a-symbolic").glyph_text)
        self.assertIsNone(draft.with_glyph_file("/x.svg").glyph_text)

    def test_the_text_is_sent_in_the_settings(self):
        self.assertEqual(self.draft.with_glyph_text("Z").to_settings().glyph_text, "Z")

    def test_an_entry_with_a_text_reopens_with_the_text(self):
        entry = Entry("/f", "0" * 32, "Adwaita", "#ff0000", {"glyph_text": "Z", "color": "#ff0000"}, None)
        draft = Draft.from_entry(entry)
        self.assertEqual((draft.glyph_text, draft.glyph_name, draft.glyph_file), ("Z", None, None))

    def test_a_damaged_entry_keeps_one_source_only(self):
        entry = Entry("/f", "0" * 32, "Adwaita", "#ff0000", {"glyph_name": "a-symbolic", "glyph_text": "Z"}, None)
        draft = Draft.from_entry(entry)
        self.assertEqual((draft.glyph_name, draft.glyph_text), ("a-symbolic", None))
        entry = Entry("/f", "0" * 32, "Adwaita", "#ff0000", {"glyph_text": ["Z"]}, None)
        self.assertFalse(Draft.from_entry(entry).ready)


class AppearanceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.draft = Draft(Path("/home/someone/Projects"), color="#112233", size=1.5, offset_x=0.1, offset_y=-0.2, tint=False)

    def test_the_automatic_mode_has_no_color_and_no_accent(self):
        values = self.draft.appearance()
        self.assertEqual((values["color"], values["accent"]), (None, False))

    def test_the_accent_mode_has_no_color(self):
        values = replace(self.draft, color_mode="accent").appearance()
        self.assertEqual((values["color"], values["accent"]), (None, True))

    def test_the_custom_mode_keeps_its_color(self):
        values = replace(self.draft, color_mode="custom").appearance()
        self.assertEqual((values["color"], values["accent"]), ("#112233", False))

    def test_the_geometry_and_tint_are_included_without_the_pictogram(self):
        values = self.draft.appearance()
        self.assertEqual((values["size"], values["offset_x"], values["offset_y"], values["tint"]), (1.5, 0.1, -0.2, False))
        self.assertEqual(
            set(values), {"color", "accent", "size", "offset_x", "offset_y", "tint", "folder_tint", "folder_tint_accent"}
        )


class SearchTextTests(unittest.TestCase):
    def test_accents_and_case_are_ignored(self):
        self.assertEqual(normalize_text("Éléphant"), "elephant")
        self.assertEqual(normalize_text("ÇA va"), "ca va")
        self.assertEqual(normalize_text("Straße"), "strasse")

    def test_plain_text_is_only_lowercased(self):
        self.assertEqual(normalize_text("Projects/2024"), "projects/2024")
        self.assertEqual(normalize_text(""), "")


class ResetReportTests(unittest.TestCase):
    def test_an_empty_report_has_no_lines(self):
        self.assertEqual(reset_report_lines(ResetReport()), [])

    def test_each_outcome_is_counted(self):
        report = ResetReport(restored=["a", "b"], kept=["c"], missing=["d"], failed={"e": "x", "f": "y", "g": "z"})
        lines = reset_report_lines(report)
        self.assertEqual(len(lines), 4)
        self.assertIn("2 icons removed", lines[0])
        self.assertIn("1 icon kept", lines[1])
        self.assertIn("1 folder not found", lines[2])
        self.assertIn("3 icons could not", lines[3])


if __name__ == "__main__":
    unittest.main()


class BadgeDraftTests(unittest.TestCase):
    def draft(self, **settings):
        return Draft.from_entry(Entry("/f", "0" * 32, "Adwaita", "#438de6", {"glyph_name": "star-symbolic", **settings}, None))

    def test_a_folder_without_badge_loads_with_the_badge_off(self):
        draft = self.draft()
        self.assertFalse(draft.badge_active)
        self.assertEqual((draft.badge_corner, draft.badge_size), ("br", 0.3))

    def test_a_stored_badge_is_loaded_with_all_its_settings(self):
        draft = self.draft(badge_glyph="heart-symbolic", badge_color="#3a944a", badge_corner="tl", badge_size=0.4)
        self.assertTrue(draft.badge_active)
        self.assertEqual((draft.badge_glyph, draft.badge_color, draft.badge_corner, draft.badge_size), ("heart-symbolic", "#3a944a", "tl", 0.4))

    def test_damaged_badge_values_fall_back(self):
        draft = self.draft(badge_glyph="heart-symbolic", badge_color="green", badge_corner="middle", badge_size="big")
        self.assertEqual((draft.badge_color, draft.badge_corner, draft.badge_size), ("#e62d42", "br", 0.3))
        self.assertEqual(self.draft(badge_glyph="h", badge_size=9).badge_size, 0.5)
        self.assertEqual(self.draft(badge_glyph="h", badge_size=0.01).badge_size, 0.15)
        self.assertEqual(self.draft(badge_glyph="h", badge_size=float("nan")).badge_size, 0.3)
        self.assertEqual(self.draft(badge_glyph="h", badge_size=True).badge_size, 0.3)

    def test_an_unusable_badge_glyph_means_no_badge(self):
        for value in ("", 3, ["a"], "a\0b"):
            self.assertFalse(self.draft(badge_glyph=value).badge_active, value)

    def test_the_settings_carry_the_badge_only_when_it_is_active(self):
        off = Draft(folder=Path("/f"), glyph_name="a-symbolic", badge_glyph="heart-symbolic")
        self.assertIsNone(off.to_settings().badge_glyph)
        self.assertNotIn("badge_glyph", off.to_settings().to_dict())
        on = replace(off, badge_on=True, badge_corner="bl", badge_color="#3a944a", badge_size=0.2)
        settings = on.to_settings()
        self.assertEqual((settings.badge_glyph, settings.badge_corner, settings.badge_color, settings.badge_size), ("heart-symbolic", "bl", "#3a944a", 0.2))

    def test_switching_on_without_a_pictogram_is_not_active(self):
        self.assertFalse(Draft(folder=Path("/f"), badge_on=True).badge_active)

    def test_the_badge_is_not_part_of_the_remembered_appearance(self):
        draft = Draft(folder=Path("/f"), glyph_name="a-symbolic", badge_on=True, badge_glyph="h")
        self.assertNotIn("badge_glyph", draft.appearance())
        self.assertTrue(draft.with_default_appearance().badge_active)
        self.assertTrue(draft.with_appearance({"size": 2.0}).badge_active)

    def test_a_round_trip_through_the_settings_keeps_the_badge(self):
        draft = Draft(folder=Path("/f"), glyph_name="a-symbolic", badge_on=True, badge_glyph="h", badge_corner="tr", badge_color="#3a944a", badge_size=0.25)
        entry = Entry("/f", "0" * 32, "Adwaita", "#438de6", draft.to_settings().to_dict(), None)
        again = Draft.from_entry(entry)
        self.assertEqual((again.badge_on, again.badge_glyph, again.badge_corner, again.badge_color, again.badge_size), (True, "h", "tr", "#3a944a", 0.25))


class BackupMessageTests(unittest.TestCase):
    def test_the_summary_has_one_line_per_kind(self):
        lines = backup_summary_lines(BackupSummary(7, 4, 12))
        self.assertEqual(lines, ["7 customized folders", "4 named styles", "12 recent or favorite pictograms"])
        self.assertEqual(backup_summary_lines(BackupSummary(1, 1, 1)), ["1 customized folder", "1 named style", "1 recent or favorite pictogram"])

    def test_an_empty_restore_report_has_no_lines(self):
        self.assertEqual(restore_report_lines(RestoreReport()), [])

    def test_each_restore_outcome_is_counted(self):
        report = RestoreReport(restored=["a", "b"], missing=["c"], kept=["d"], failed={"e": "x", "f": "y", "g": "z"}, styles_added=4, styles_rejected=2)
        lines = restore_report_lines(report)
        self.assertEqual(len(lines), 6)
        self.assertIn("2 icons restored", lines[0])
        self.assertIn("1 folder not found", lines[1])
        self.assertIn("1 folder whose icon", lines[2])
        self.assertIn("3 icons could not", lines[3])
        self.assertIn("4 styles added", lines[4])
        self.assertIn("2 styles ignored", lines[5])

    def test_singular_forms_are_used(self):
        lines = restore_report_lines(RestoreReport(restored=["a"], styles_added=1, styles_rejected=1))
        self.assertEqual(lines, ["1 icon restored", "1 style added", "1 style ignored because it is not valid"])
