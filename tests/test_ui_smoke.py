import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from custicon.ui.editor import hex_from_rgba
from custicon.metadata import read_custom_icon, write_custom_icon
from custicon.service import Service, Settings
from custicon.errors import StoreError
from custicon.store import Entry, Store
from custicon.ui import window as window_module
from custicon.ui.draft import Draft
from custicon.ui.format import percent_text

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme

USER_ICON = "file:///home/someone/my-icon.png"
DISPLAY_AVAILABLE = os.environ.get("GDK_BACKEND") == "broadway" and Gtk.init_check()
IDLE_SETTLE = 0.12


_APPLICATION = []


def shared_application() -> Adw.Application:
    if not _APPLICATION:
        application = Adw.Application(application_id="org.custicon.Tests", flags=Gio.ApplicationFlags.NON_UNIQUE)
        application.register(None)
        _APPLICATION.append(application)
    return _APPLICATION[0]


def settle(condition=None, timeout: float = 4.0) -> bool:
    context = GLib.MainContext.default()
    deadline = time.time() + (timeout if condition is not None else IDLE_SETTLE)
    while time.time() < deadline:
        while context.pending():
            context.iteration(False)
        if condition is not None and condition():
            return True
        time.sleep(0.01)
    return condition is None or condition()


class DisplayRequirementTests(unittest.TestCase):
    def test_the_display_is_available_when_it_is_required(self):
        if os.environ.get("CUSTICON_REQUIRE_DISPLAY"):
            self.assertTrue(DISPLAY_AVAILABLE, "no Broadway display, but CUSTICON_REQUIRE_DISPLAY is set")


@unittest.skipUnless(DISPLAY_AVAILABLE, "interface tests only run on a Broadway display (GDK_BACKEND=broadway)")
class WindowTestCase(WorkspaceTestCase):
    def setUp(self):
        from custicon.ui.window import MainWindow

        Adw.init()
        self.root = self.make_workspace()
        themes = self.root / "themes"
        themes.mkdir()
        glyphs = {"star-symbolic": glyph_svg(), "heart-symbolic": glyph_svg()}
        make_theme(themes, "Blue", folder=folder_svg("#3366cc"), glyphs=glyphs)
        make_theme(themes, "Green", folder=folder_svg("#33cc66"), glyphs=glyphs)
        self.theme = "Blue"
        self.service = Service(Store(self.root / "data"), theme_name=lambda: self.theme, search_dirs=[str(themes)])
        self.workspace = self.root / "folders"
        self.workspace.mkdir()
        self.require_metadata(self.workspace)
        self.created = []
        self.addCleanup(self.clear_metadata)
        self.window = MainWindow(shared_application(), self.service)
        self.addCleanup(self.window.destroy)
        settle()

    def clear_metadata(self):
        for folder in self.created:
            try:
                write_custom_icon(folder, None)
            except Exception:
                pass

    def make_folder(self, name: str = "docs"):
        folder = self.workspace / name
        folder.mkdir()
        self.created.append(folder)
        return folder

    def add_and_apply(self, name: str = "docs", glyph: str = "star-symbolic"):
        folder = self.make_folder(name)
        self.window.add_folder(folder)
        settle()
        self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name(glyph))
        settle()
        self.window._apply()
        settle()
        return folder

    def row_keys(self):
        return list(self.window._rows)


class EmptyAndNewFolderTests(WindowTestCase):
    def test_an_empty_store_shows_the_welcome_page(self):
        self.assertEqual(self.window.stack.get_visible_child_name(), "empty")

    def test_adding_a_folder_opens_the_editor_without_a_pictogram(self):
        folder = self.make_folder()
        self.window.add_folder(folder)
        settle()
        self.assertEqual(self.window.stack.get_visible_child_name(), "main")
        self.assertEqual(self.window.editor.draft.folder, folder)
        self.assertFalse(self.window.editor.draft.ready)
        self.assertFalse(self.window.editor.apply_button.get_sensitive())
        self.assertTrue(self.window.editor.remove_button.get_visible())
        self.assertEqual(self.window.editor.remove_button.get_label(), "Cancel")
        self.assertEqual(self.service.store.all(), {})

    def test_the_new_folder_is_listed_before_being_applied(self):
        folder = self.make_folder()
        self.window.add_folder(folder)
        settle()
        self.assertEqual(self.row_keys(), [str(folder)])

    def test_choosing_a_pictogram_enables_apply(self):
        self.window.add_folder(self.make_folder())
        settle()
        self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("star-symbolic"))
        self.assertTrue(self.window.editor.apply_button.get_sensitive())

    def test_the_preview_is_drawn_for_the_plain_folder_and_with_a_pictogram(self):
        self.window.add_folder(self.make_folder())
        settle(lambda: self.window.editor.big.get_paintable() is not None)
        plain = self.window.editor.big.get_paintable()
        self.assertIsNotNone(plain)
        self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("star-symbolic"))
        settle(lambda: self.window.editor.big.get_paintable() is not plain)
        self.assertIsNot(self.window.editor.big.get_paintable(), plain)
        self.assertEqual(len(self.window.editor.small_pictures), 3)
        self.assertTrue(all(picture.get_paintable() is not None for _, picture in self.window.editor.small_pictures))

    def test_adding_a_customized_folder_selects_it_instead_of_duplicating(self):
        folder = self.add_and_apply()
        self.window.add_folder(folder)
        settle()
        self.assertEqual(self.row_keys(), [str(folder)])
        self.assertIsNone(self.window._pending)


class FakeFolderDialog:
    def __init__(self, path):
        self._file = Gio.File.new_for_path(path)

    def select_folder_finish(self, _result):
        return self._file


class FakeOpenDialog:
    def __init__(self, path):
        self._file = Gio.File.new_for_path(path)

    def open_finish(self, _result):
        return self._file


class FolderChoiceTests(WindowTestCase):
    def test_a_normal_folder_becomes_a_pending_entry(self):
        folder = self.make_folder()
        self.window._folder_chosen(FakeFolderDialog(str(folder)), None)
        settle()
        self.assertEqual(self.window._pending.folder, folder)
        self.assertIsNone(self.window.get_visible_dialog())

    def test_a_document_portal_path_is_refused_with_an_explanation(self):
        self.window._folder_chosen(FakeFolderDialog("/run/user/1000/doc/abcd1234/Projects"), None)
        settle()
        self.assertIsNone(self.window._pending)
        self.assertIsNotNone(self.window.get_visible_dialog())


class ApplyAndRemoveTests(WindowTestCase):
    def test_applying_registers_the_folder_and_sets_the_metadata(self):
        folder = self.add_and_apply()
        entry = self.service.store.get(str(folder))
        self.assertIsNotNone(entry)
        self.assertEqual(read_custom_icon(folder), self.service.store.icon_uri(entry.digest))
        self.assertIsNone(self.window._pending)
        self.assertTrue(self.window.editor.remove_button.get_visible())

    def test_removing_restores_the_folder_and_shows_the_welcome_page(self):
        folder = self.add_and_apply()
        self.window.remove(str(folder), "docs")
        settle()
        self.assertIsNone(read_custom_icon(folder))
        self.assertEqual(self.service.store.all(), {})
        self.assertEqual(self.window.stack.get_visible_child_name(), "empty")

    def test_removing_restores_an_icon_that_existed_before(self):
        folder = self.make_folder()
        write_custom_icon(folder, USER_ICON)
        self.window.add_folder(folder)
        settle()
        self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("star-symbolic"))
        self.window._apply()
        settle()
        self.window.remove(str(folder), "docs")
        self.assertEqual(read_custom_icon(folder), USER_ICON)

    def test_folders_are_listed_by_name_not_by_path(self):
        first_parent, second_parent = self.workspace / "a", self.workspace / "b"
        first_parent.mkdir()
        second_parent.mkdir()
        for parent, name in ((first_parent, "zebra"), (second_parent, "alpha")):
            folder = parent / name
            folder.mkdir()
            self.created.append(folder)
            self.window.add_folder(folder)
            settle()
            self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("star-symbolic"))
            self.window._apply()
            settle()
        self.assertEqual([path.rsplit("/", 1)[-1] for path in self.row_keys()], ["alpha", "zebra"])

    def test_an_apply_failure_keeps_the_editor_and_registers_nothing(self):
        folder = self.make_folder()
        self.window.add_folder(folder)
        settle()
        self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("missing-symbolic"))
        settle()
        self.assertFalse(self.window.editor.apply_button.get_sensitive())
        self.window._apply()
        settle()
        self.assertEqual(self.service.store.all(), {})
        self.assertIsNone(read_custom_icon(folder))


class EditorBehaviourTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.add_and_apply()
        self.editor = self.window.editor

    def test_the_size_slider_updates_the_draft(self):
        self.editor.size_slider.adjustment.set_value(150)
        self.assertAlmostEqual(self.editor.draft.size, 1.5)

    def test_the_position_sliders_update_the_draft(self):
        self.editor.x_slider.adjustment.set_value(20)
        self.editor.y_slider.adjustment.set_value(-10)
        self.assertAlmostEqual(self.editor.draft.offset_x, 0.2)
        self.assertAlmostEqual(self.editor.draft.offset_y, -0.1)

    def test_slider_labels_show_the_value(self):
        self.editor.size_slider.adjustment.set_value(150)
        self.assertEqual(self.editor.size_slider.label.get_text(), "150%")

    def test_turning_automatic_color_off_keeps_the_color(self):
        self.editor.auto_row.set_active(False)
        self.assertFalse(self.editor.draft.auto_color)
        self.assertTrue(self.editor.swatch.get_sensitive())

    def test_turning_automatic_color_off_keeps_the_displayed_color(self):
        self.editor.set_auto_color_text("#aa5500")
        shown = hex_from_rgba(self.editor.swatch.get_rgba())
        self.assertEqual(shown, "#aa5500")
        self.editor.auto_row.set_active(False)
        self.assertEqual(self.editor.draft.color, "#aa5500")
        self.assertEqual(hex_from_rgba(self.editor.swatch.get_rgba()), shown)
        self.assertEqual(self.editor.draft.to_settings().color, shown)

    def test_reset_returns_to_the_default_appearance(self):
        self.editor.size_slider.adjustment.set_value(200)
        self.editor.auto_row.set_active(False)
        self.assertTrue(self.editor.reset_button.get_sensitive())
        self.editor.reset_button.emit("clicked")
        self.assertTrue(self.editor.draft.has_default_appearance())
        self.assertEqual(self.editor.size_slider.adjustment.get_value(), 100)
        self.assertTrue(self.editor.auto_row.get_active())
        self.assertFalse(self.editor.reset_button.get_sensitive())

    def test_the_tint_switch_only_matters_for_files(self):
        self.assertFalse(self.editor.tint_row.get_sensitive())
        glyph = self.root / "mine.svg"
        glyph.write_text(glyph_svg())
        self.editor.update_glyph(self.editor.draft.with_glyph_file(glyph))
        self.assertTrue(self.editor.tint_row.get_sensitive())
        self.assertEqual(self.editor.file_row.get_subtitle(), "mine.svg")

    def test_unsaved_changes_are_detected(self):
        self.assertFalse(self.window.has_unsaved_changes())
        self.editor.size_slider.adjustment.set_value(150)
        self.assertTrue(self.window.has_unsaved_changes())

    def test_regaining_focus_does_not_discard_unsaved_changes(self):
        self.editor.size_slider.adjustment.set_value(150)
        self.window.is_active = lambda: True
        self.window._on_active_changed()
        settle()
        self.assertAlmostEqual(self.editor.draft.size, 1.5)

    def test_regaining_focus_refreshes_the_list_when_nothing_is_unsaved(self):
        self.window.is_active = lambda: True
        write_custom_icon(self.folder, USER_ICON)
        self.window._on_active_changed()
        settle()
        self.assertTrue(self.editor.banner.get_revealed())

    def test_an_unreadable_pictogram_file_blocks_apply(self):
        broken = self.root / "broken.svg"
        broken.write_text("not an svg")
        self.editor.update_glyph(self.editor.draft.with_glyph_file(broken))
        settle(lambda: not self.editor.apply_button.get_sensitive())
        self.assertFalse(self.editor.apply_button.get_sensitive())
        self.editor.update_glyph(self.editor.draft.with_glyph_name("heart-symbolic"))
        settle(lambda: self.editor.apply_button.get_sensitive())
        self.assertTrue(self.editor.apply_button.get_sensitive())


class StateTests(WindowTestCase):
    def test_a_deleted_folder_is_flagged_and_cannot_be_applied(self):
        folder = self.add_and_apply()
        folder.rmdir()
        self.window.reload(select=str(folder))
        settle()
        self.assertTrue(self.window.editor.banner.get_revealed())
        self.assertFalse(self.window.editor.apply_button.get_sensitive())
        self.assertEqual(self.window.editor.remove_button.get_label(), "Forget This Folder")

    def test_forgetting_a_deleted_folder_clears_the_entry(self):
        folder = self.add_and_apply()
        folder.rmdir()
        self.window.remove(str(folder), "docs")
        self.assertEqual(self.service.store.all(), {})

    def test_an_icon_changed_in_nautilus_is_flagged_and_kept_on_removal(self):
        folder = self.add_and_apply()
        write_custom_icon(folder, USER_ICON)
        self.window.reload(select=str(folder))
        settle()
        self.assertTrue(self.window.editor.banner.get_revealed())
        self.assertEqual(self.window.editor.banner.get_button_label(), "Take Back the Custicon Icon")
        self.window.remove(str(folder), "docs")
        self.assertEqual(read_custom_icon(folder), USER_ICON)
        self.assertEqual(self.service.store.all(), {})

    def test_taking_back_the_icon_applies_it_again(self):
        folder = self.add_and_apply()
        write_custom_icon(folder, USER_ICON)
        self.window.reload(select=str(folder))
        settle()
        self.window._apply()
        settle()
        entry = self.service.store.get(str(folder))
        self.assertEqual(read_custom_icon(folder), self.service.store.icon_uri(entry.digest))
        self.assertFalse(self.window.editor.banner.get_revealed())


class RegenerationTests(WindowTestCase):
    def test_regenerating_after_a_theme_change_updates_every_folder(self):
        folders = [self.add_and_apply("one"), self.add_and_apply("two")]
        before = {entry.folder: entry.digest for entry in self.service.store.all().values()}
        self.theme = "Green"
        self.window._start_regeneration()
        self.assertTrue(settle(lambda: self.window._regeneration is None))
        after = {entry.folder: entry.digest for entry in self.service.store.all().values()}
        for folder in folders:
            self.assertNotEqual(before[str(folder)], after[str(folder)])
            self.assertEqual(read_custom_icon(folder), self.service.store.icon_uri(after[str(folder)]))
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_the_regenerate_button_is_available_again_afterwards(self):
        self.add_and_apply()
        self.window._start_regeneration()
        settle(lambda: self.window._regeneration is None)
        self.assertTrue(self.window.regenerate_button.get_sensitive())

    def test_regenerating_without_folders_only_informs(self):
        self.window._start_regeneration()
        settle()
        self.assertIsNone(self.window._regeneration)
        self.assertIsNone(self.window.get_visible_dialog())


class PickerTests(WindowTestCase):
    def test_the_picker_lists_pictograms_and_filters_them(self):
        from custicon.ui.picker import PickerDialog

        chosen = []
        dialog = PickerDialog(self.service.icon_theme("Blue"), None, chosen.append)
        self.assertEqual(sorted(dialog._names), ["heart-symbolic", "star-symbolic"])
        dialog.search.set_text("hea")
        settle(lambda: dialog._names == ["heart-symbolic"])
        self.assertEqual(dialog._names, ["heart-symbolic"])
        self.assertFalse(dialog.choose_button.get_sensitive())

    def test_choosing_reports_the_name(self):
        from custicon.ui.picker import PickerDialog

        chosen = []
        dialog = PickerDialog(self.service.icon_theme("Blue"), "star-symbolic", chosen.append)
        dialog.present(self.window)
        settle()
        self.assertTrue(dialog.choose_button.get_sensitive())
        dialog._on_choose(None)
        self.assertEqual(chosen, ["star-symbolic"])

    def test_a_query_without_result_disables_choosing_nothing_new(self):
        from custicon.ui.picker import PickerDialog

        dialog = PickerDialog(self.service.icon_theme("Blue"), None, lambda name: None)
        dialog.search.set_text("zzzz")
        settle(lambda: dialog._names == [])
        self.assertEqual(dialog._names, [])
        self.assertEqual(dialog.status.get_text(), "No pictogram found")


if __name__ == "__main__":
    unittest.main()


class ResilienceTests(WindowTestCase):
    def test_an_unreadable_registry_does_not_prevent_the_window_from_opening(self):
        from custicon.ui.window import MainWindow

        self.service.store.root.mkdir(parents=True, exist_ok=True)
        self.service.store.registry_path.write_text("{broken")
        window = MainWindow(shared_application(), self.service)
        self.addCleanup(window.destroy)
        settle()
        self.assertEqual(window.stack.get_visible_child_name(), "empty")
        self.assertIsNotNone(window.get_visible_dialog())

    def test_the_registry_error_is_shown_once_not_at_every_refresh(self):
        self.service.store.root.mkdir(parents=True, exist_ok=True)
        self.service.store.registry_path.write_text("{broken")
        self.window.reload()
        settle()
        first = self.window.get_visible_dialog()
        first.force_close()
        settle()
        self.window.reload()
        settle()
        self.assertIsNone(self.window.get_visible_dialog())

    def test_a_flag_is_released_when_rebuilding_the_list_fails(self):
        with mock.patch.object(self.window, "_rebuild_rows", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.window.reload()
        self.assertFalse(self.window._reloading)

    def test_the_editor_flag_is_released_when_a_load_fails(self):
        editor = self.window.editor
        with mock.patch.object(editor, "_show_widgets", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                editor.load(Draft(self.workspace, glyph_name="star-symbolic"), False)
        self.assertFalse(editor._loading)

    def test_the_remove_button_is_hidden_for_a_new_folder_without_a_label(self):
        self.window.editor.load(Draft(self.workspace, glyph_name="star-symbolic"), False)
        self.assertFalse(self.window.editor.remove_button.get_visible())
        self.window.editor.load(Draft(self.workspace, glyph_name="star-symbolic"), True)
        self.assertTrue(self.window.editor.remove_button.get_visible())
        self.assertTrue(self.window.editor.remove_button.has_css_class("destructive-action"))
        self.window.editor.load(Draft(self.workspace, glyph_name="star-symbolic"), False, remove_label="Cancel")
        self.assertFalse(self.window.editor.remove_button.has_css_class("destructive-action"))

    def test_closing_the_window_stops_a_running_regeneration(self):
        self.add_and_apply()
        self.window._start_regeneration()
        self.assertIsNotNone(self.window._regeneration)
        self.window._on_close_request()
        self.assertIsNone(self.window._regeneration)
        self.assertEqual(self.window._regeneration_source, 0)
        self.assertTrue(self.window.regenerate_button.get_sensitive())

    def test_the_editor_flag_is_released_when_loading_fails(self):
        editor = self.window.editor
        with mock.patch.object(editor, "_show_widgets", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                editor.update_glyph(Draft(self.workspace, glyph_name="star-symbolic"))
        self.assertFalse(editor._loading)

    def test_clicking_the_selected_row_opens_the_editor_in_narrow_mode(self):
        folder = self.add_and_apply()
        self.window.split.set_collapsed(True)
        self.window.split.set_show_content(False)
        row = self.window._rows[str(folder)]
        self.window.list.emit("row-activated", row)
        self.assertTrue(self.window.split.get_show_content())

    def test_reloading_does_not_switch_the_narrow_view_to_the_editor(self):
        folder = self.add_and_apply()
        self.window.split.set_collapsed(True)
        self.window.split.set_show_content(False)
        self.window.reload(select=str(folder))
        self.assertFalse(self.window.split.get_show_content())

    def test_cancelling_a_pending_folder_forgets_it_without_asking(self):
        self.window.add_folder(self.make_folder())
        settle()
        self.window._confirm_remove()
        settle()
        self.assertIsNone(self.window._pending)
        self.assertEqual(self.window.stack.get_visible_child_name(), "empty")
        self.assertIsNone(self.window.get_visible_dialog())

    def test_removing_an_unknown_folder_says_so(self):
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove(str(self.workspace / "nowhere"), "nowhere")
        toast.assert_called_once()
        self.assertIn("no longer in the list", toast.call_args.args[0])

    def test_a_name_that_is_not_utf8_is_refused_when_choosing_a_folder(self):
        bad = os.fsencode(self.workspace) + b"/caf\xe9"
        os.mkdir(bad)
        self.addCleanup(os.rmdir, bad)
        self.window._folder_chosen(FakeFolderDialog(os.fsdecode(bad)), None)
        settle()
        self.assertIsNone(self.window._pending)
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_a_pictogram_file_name_that_is_not_utf8_is_refused(self):
        bad = os.fsencode(self.workspace) + b"/ic\xf4ne.svg"
        with open(bad, "wb") as handle:
            handle.write(glyph_svg().encode())
        self.addCleanup(os.remove, bad)
        self.window.add_folder(self.make_folder())
        settle()
        self.window._glyph_file_chosen(FakeOpenDialog(os.fsdecode(bad)), None)
        settle()
        self.assertIsNone(self.window.editor.draft.glyph_file)
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_a_folder_that_cannot_be_resolved_is_refused_cleanly(self):
        with mock.patch.object(Path, "resolve", side_effect=OSError("too many levels of symbolic links")):
            self.window.add_folder(self.workspace)
        settle()
        self.assertIsNone(self.window._pending)
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_a_damaged_registry_does_not_break_later_actions(self):
        self.add_and_apply()
        self.service.store.registry_path.write_text("{broken")
        self.service.store.registry_path.with_name("registry.json.bak").write_text("{broken")
        self.window.has_unsaved_changes()
        self.window._start_regeneration()
        settle()
        self.assertIsNone(self.window._regeneration)
        self.assertTrue(self.window.regenerate_button.get_sensitive())
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_closing_the_window_removes_the_pending_timers(self):
        self.window.add_folder(self.make_folder())
        self.window._schedule_preview()
        self.assertNotEqual(self.window._preview_source, 0)
        self.window._on_close_request()
        self.assertEqual(self.window._preview_source, 0)

    def test_regeneration_does_not_discard_unsaved_changes(self):
        self.add_and_apply()
        self.window.editor.size_slider.adjustment.set_value(170)
        self.window._start_regeneration()
        settle(lambda: self.window._regeneration is None)
        settle()
        self.assertAlmostEqual(self.window.editor.draft.size, 1.7)

    def test_an_unexpected_error_during_regeneration_does_not_block_the_button(self):
        self.add_and_apply()
        with mock.patch.object(self.service, "regenerate_entry", side_effect=RuntimeError("boom")):
            self.window._start_regeneration()
            self.assertTrue(settle(lambda: self.window._regeneration is None))
        self.assertTrue(self.window.regenerate_button.get_sensitive())

    def test_a_damaged_registry_entry_still_opens_in_the_editor(self):
        folder = self.add_and_apply()
        entry = self.service.store.get(str(folder))
        damaged = Entry(entry.folder, entry.digest, entry.theme, entry.color, {"glyph_name": "star-symbolic", "size": "huge", "tint": "x"}, None)
        self.service.store.put(damaged)
        self.window.reload(select=str(folder))
        settle()
        self.assertEqual(self.window.editor.draft.size, 1.0)


class MemoryAndLayoutTests(WindowTestCase):
    def apply_with(self, name: str, **changes):
        folder = self.make_folder(name)
        self.window.add_folder(folder)
        settle()
        draft = self.window.editor.draft.with_glyph_name("star-symbolic")
        self.window.editor.update_glyph(replace(draft, **changes))
        settle()
        self.window._apply()
        settle()
        return folder

    def test_a_new_folder_starts_with_the_appearance_of_the_last_applied_one(self):
        self.apply_with("first", auto_color=False, color="#ff8800", size=1.5, offset_x=0.1, offset_y=-0.1, tint=False)
        self.window.add_folder(self.make_folder("second"))
        settle()
        draft = self.window.editor.draft
        self.assertEqual((draft.auto_color, draft.color, draft.size, draft.offset_x, draft.offset_y, draft.tint), (False, "#ff8800", 1.5, 0.1, -0.1, False))
        self.assertFalse(draft.ready)

    def test_remembered_settings_can_still_be_changed(self):
        self.apply_with("first", size=1.5)
        self.window.add_folder(self.make_folder("second"))
        settle()
        self.window.editor.size_slider.adjustment.set_value(80)
        self.assertAlmostEqual(self.window.editor.draft.size, 0.8)

    def test_an_already_customized_folder_keeps_its_own_settings(self):
        first = self.apply_with("first", size=1.5)
        self.apply_with("second", size=0.5)
        self.window.add_folder(first)
        settle()
        self.assertEqual(self.window.editor.draft.size, 1.5)
        self.assertEqual(self.service.store.get(str(first)).settings["size"], 1.5)

    def test_regeneration_does_not_change_the_memory(self):
        self.apply_with("first", size=1.5)
        self.apply_with("second", size=0.5)
        remembered = self.service.remembered_appearance()
        self.theme = "Green"
        self.window._start_regeneration()
        settle(lambda: self.window._regeneration is None)
        self.assertEqual(self.service.remembered_appearance(), remembered)

    def test_a_failed_apply_does_not_change_the_memory(self):
        self.window.add_folder(self.make_folder("broken"))
        settle()
        self.window.editor.update_glyph(replace(self.window.editor.draft.with_glyph_name("star-symbolic"), size=2.0))
        with mock.patch.object(self.service, "apply", side_effect=StoreError("disk full")):
            self.window._apply()
        self.assertEqual(self.service.remembered_appearance(), {})

    def test_the_sliders_move_by_half_a_percent(self):
        slider = self.window.editor.size_slider
        self.assertEqual(slider.adjustment.get_step_increment(), 0.5)
        self.assertEqual(slider.scale.get_digits(), 1)
        self.assertTrue(slider.scale.get_hexpand())

    def test_slider_values_snap_to_half_percents(self):
        self.window.add_folder(self.make_folder())
        settle()
        self.window.editor.size_slider.adjustment.set_value(100.3)
        self.assertAlmostEqual(self.window.editor.draft.size, 1.005)
        self.assertEqual(self.window.editor.size_slider.label.get_text(), percent_text(1.005))

    def test_the_folder_list_uses_compact_icons(self):
        folder = self.add_and_apply()
        picture = self.window._rows[str(folder)].get_child().get_first_child()
        self.assertEqual(picture.get_size_request(), (32, 32))


class PermissionHelpTests(WindowTestCase):
    def make_window(self, access: bool):
        from custicon.ui.window import MainWindow

        with mock.patch.object(window_module.environment, "home_access", return_value=access):
            window = MainWindow(shared_application(), self.service)
        self.addCleanup(window.destroy)
        settle()
        return window

    def test_the_banner_is_hidden_when_the_home_folder_is_visible(self):
        self.assertFalse(self.make_window(True).permission_banner.get_revealed())

    def test_the_banner_is_shown_when_the_home_folder_is_not_visible(self):
        self.assertTrue(self.make_window(False).permission_banner.get_revealed())

    def test_the_banner_button_explains_the_fix_with_the_exact_command(self):
        window = self.make_window(False)
        window.permission_banner.emit("button-clicked")
        settle()
        dialog = window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.get_extra_child().get_label(), "flatpak override --user --filesystem=home:ro io.github.gor3pig.Custicon")

    def test_a_portal_path_without_access_opens_the_same_help(self):
        window = self.make_window(False)
        with mock.patch.object(window_module.environment, "home_access", return_value=False):
            window._folder_chosen(FakeFolderDialog("/run/user/1000/doc/abcd1234/Projects"), None)
        settle()
        self.assertIsNotNone(window.get_visible_dialog().get_extra_child())
        self.assertIsNone(window._pending)

    def test_a_portal_path_with_access_keeps_the_plain_alert(self):
        with mock.patch.object(window_module.environment, "home_access", return_value=True):
            self.window._folder_chosen(FakeFolderDialog("/run/user/1000/doc/abcd1234/Projects"), None)
        settle()
        self.assertIsNone(self.window.get_visible_dialog().get_extra_child())
