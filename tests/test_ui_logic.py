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
    RegenerationReport,
    Settings,
)
from custicon.store import Entry
from custicon.ui.draft import DEFAULT_COLOR, Draft
from custicon.ui.format import display_path, folder_name, from_percent, is_portal_path, is_text_path, percent_text, to_percent
from custicon.ui.messages import banner_action, describe_error, report_lines, status_banner, status_label


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path("/home/someone/Projects")

    def test_a_new_draft_has_no_pictogram(self):
        draft = Draft(self.folder)
        self.assertFalse(draft.ready)
        self.assertTrue(draft.auto_color)
        self.assertEqual(draft.color, DEFAULT_COLOR)
        self.assertTrue(draft.has_default_appearance())

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
        for changes in ({"color_mode": "custom"}, {"size": 1.1}, {"offset_x": 0.01}, {"offset_y": -0.01}):
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
        self.assertIsNone(banner_action(STATUS_FOLDER_MISSING))
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
                "1 folder skipped: icon changed from Nautilus",
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


if __name__ == "__main__":
    unittest.main()
