import json
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

from custicon.ui.editor import hex_from_rgba, rgba_from_hex
from custicon.metadata import read_custom_icon, write_custom_icon
from custicon import service as service_module
from custicon.service import Service, Settings
from custicon.errors import MetadataError, StoreError
from custicon.store import Entry, Store
from custicon.ui import window as window_module
from custicon.ui.draft import Draft
from custicon.ui.format import percent_text

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme

USER_ICON = "file:///home/someone/my-icon.png"
DISPLAY_AVAILABLE = os.environ.get("GDK_BACKEND") == "broadway" and Gtk.init_check()
IDLE_SETTLE = 0.12


_APPLICATION = []
_OPEN_APPLICATION = []


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
        desktop = mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"})
        desktop.start()
        self.addCleanup(desktop.stop)
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


class FakeFoldersDialog:
    def __init__(self, *paths):
        self._store = Gio.ListStore.new(Gio.File)
        for path in paths:
            self._store.append(Gio.File.new_for_path(path))

    def select_multiple_folders_finish(self, _result):
        return self._store


class FakeAnswer:
    def __init__(self, response):
        self._response = response

    def choose_finish(self, _result):
        return self._response


class FakeOpenDialog:
    def __init__(self, path):
        self._file = Gio.File.new_for_path(path)

    def open_finish(self, _result):
        return self._file


class FolderChoiceTests(WindowTestCase):
    def test_a_normal_folder_becomes_a_pending_entry(self):
        folder = self.make_folder()
        self.window._folders_chosen(FakeFoldersDialog(str(folder)), None)
        settle()
        self.assertEqual(self.window._pending.folder, folder)
        self.assertIsNone(self.window.get_visible_dialog())

    def test_a_document_portal_path_is_refused_with_an_explanation(self):
        self.window._folders_chosen(FakeFoldersDialog("/run/user/1000/doc/abcd1234/Projects"), None)
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
        self.editor.color_palette.choose_color("#e62d42")
        self.assertFalse(self.editor.draft.auto_color)
        self.assertEqual(self.editor.color_palette.state(), ("custom", "#e62d42"))

    def test_turning_automatic_color_off_keeps_the_displayed_color(self):
        self.editor.set_computed_color("#aa5500")
        self.editor.color_palette.custom_button.set_rgba(rgba_from_hex("#aa5500"))
        self.assertEqual(self.editor.draft.color, "#aa5500")
        self.assertEqual(self.editor.color_palette.custom_hex(), "#aa5500")
        self.assertEqual(self.editor.draft.to_settings().color, "#aa5500")

    def test_reset_returns_to_the_default_appearance(self):
        self.editor.size_slider.adjustment.set_value(200)
        self.editor.color_palette.choose_color("#e62d42")
        self.assertTrue(self.editor.reset_button.get_sensitive())
        self.editor.reset_button.emit("clicked")
        self.assertTrue(self.editor.draft.has_default_appearance())
        self.assertEqual(self.editor.size_slider.adjustment.get_value(), 100)
        self.assertEqual(self.editor.color_palette.state(), ("auto", None))
        self.assertFalse(self.editor.reset_button.get_sensitive())

    def test_the_tint_switch_only_matters_for_files(self):
        self.assertFalse(self.editor.tint_row.get_sensitive())
        glyph = self.root / "mine.svg"
        glyph.write_text(glyph_svg())
        self.editor.update_glyph(self.editor.draft.with_glyph_file(glyph))
        self.assertTrue(self.editor.tint_row.get_sensitive())
        self.assertEqual(self.editor.glyph_label.get_text(), "mine.svg")

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


class RepairUiTests(WindowTestCase):
    class Answer:
        def __init__(self, response):
            self.response = response

        def choose_finish(self, _result):
            return self.response

    def setUp(self):
        super().setUp()
        self.lost = self.add_and_apply("lost", "star-symbolic")
        self.changed = self.add_and_apply("changed", "heart-symbolic")
        self.healthy = self.add_and_apply("healthy", "star-symbolic")

    def break_two(self):
        self.service.apply(self.lost, Settings(glyph_name="heart-symbolic", color="#ff0000"))
        self.service.store.icon_path(self.service.store.get(str(self.lost)).digest).unlink()
        write_custom_icon(self.changed, USER_ICON)
        self.window.reload()
        settle()

    def checks(self, dialog):
        found = []

        def walk(widget):
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.CheckButton):
                    found.append(child)
                walk(child)
                child = child.get_next_sibling()

        walk(dialog.get_extra_child())
        return found

    def test_the_banner_is_hidden_when_everything_is_fine(self):
        self.assertFalse(self.window.problems_banner.get_revealed())

    def test_the_banner_counts_the_folders_to_check(self):
        self.break_two()
        self.assertTrue(self.window.problems_banner.get_revealed())
        self.assertEqual(self.window.problems_banner.get_title(), "2 folders to check")

    def test_only_the_safe_repair_is_ticked_at_first(self):
        self.break_two()
        self.window._open_repair()
        settle()
        dialog = self.window.get_visible_dialog()
        ticked = [check.get_child().get_first_child().get_label() for check in self.checks(dialog) if check.get_active()]
        self.assertEqual(ticked, ["lost"])
        self.assertEqual(len(self.checks(dialog)), 2)

    def test_repairing_only_touches_the_ticked_folders(self):
        self.break_two()
        keys = [(str(self.lost), Gtk.CheckButton(active=True)), (str(self.changed), Gtk.CheckButton(active=False))]
        self.window._repair_chosen(self.Answer("repair"), None, keys)
        settle()
        self.assertEqual(read_custom_icon(self.changed), USER_ICON)
        self.assertEqual(self.service.entry_status(self.service.store.get(str(self.lost))), "ok")
        self.assertEqual(self.window.problems_banner.get_title(), "1 folder to check")

    def test_cancelling_changes_nothing(self):
        self.break_two()
        keys = [(str(self.lost), Gtk.CheckButton(active=True))]
        self.window._repair_chosen(self.Answer("cancel"), None, keys)
        settle()
        self.assertEqual(self.window.problems_banner.get_title(), "2 folders to check")

    def test_nothing_to_repair_only_informs(self):
        self.window._open_repair()
        settle()
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

    def test_a_query_without_result_says_so(self):
        from custicon.ui.picker import PickerDialog

        dialog = PickerDialog(self.service.icon_theme("Blue"), None, lambda name: None)
        dialog.search.set_text("zzzz")
        settle(lambda: dialog._names == [])
        self.assertEqual(dialog._names, [])
        self.assertEqual(dialog.status.get_text(), "No pictogram found")

    def test_the_count_of_results_is_shown(self):
        from custicon.ui.picker import PickerDialog

        dialog = PickerDialog(self.service.icon_theme("Blue"), None, lambda name: None)
        self.assertEqual(dialog.status.get_text(), "2 pictograms found")
        dialog.search.set_text("hea")
        settle(lambda: dialog._names == ["heart-symbolic"])
        self.assertEqual(dialog.status.get_text(), "1 pictogram found")


class PickerCase(WindowTestCase):
    def dialog(self, current=None, chosen=None, language="en", folder_name="", recent=(), favorites=(), on_favorite=None):
        from unittest import mock

        from custicon import iconsets
        from custicon.service import PickerMemory
        from custicon.ui.picker import PickerDialog

        patcher = mock.patch.object(iconsets, "current_language", return_value=language)
        patcher.start()
        self.addCleanup(patcher.stop)
        return PickerDialog(
            self.service.icon_theme("Blue"),
            current,
            (chosen if chosen is not None else []).append,
            self.service.icon_sets(),
            folder_name,
            PickerMemory(tuple(recent), tuple(favorites)),
            on_favorite or (lambda name, favorite: True),
        )

    def toggles(self, dialog):
        group = dialog.set_toggle
        return [group.get_toggle(index).get_name() for index in range(group.get_n_toggles())]

    def show(self, dialog, key):
        dialog.set_toggle.set_active_name(key)
        settle(lambda: dialog._names and dialog._names[0] != "star-symbolic")

    def search(self, dialog, text, expected_first=None):
        dialog.search.set_text(text)
        settle(lambda: bool(dialog._names) and (expected_first is None or dialog._names[0] == expected_first))


class PhosphorPickerTests(PickerCase):
    def test_the_theme_set_is_shown_first(self):
        dialog = self.dialog()
        self.assertEqual(dialog.set_toggle.get_active_name(), "theme")
        self.assertEqual(sorted(dialog._names), ["heart-symbolic", "star-symbolic"])

    def test_switching_to_phosphor_lists_the_whole_set_without_a_cap(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.assertEqual(len(dialog._names), 1512)
        self.assertTrue(all(name.startswith("phosphor:") for name in dialog._names))

    def test_the_search_applies_to_the_active_set(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        dialog.search.set_text("acor")
        settle(lambda: dialog._names == ["phosphor:acorn"])
        self.assertEqual(dialog._names, ["phosphor:acorn"])

    def test_switching_sets_keeps_the_typed_query(self):
        dialog = self.dialog()
        dialog.search.set_text("acor")
        settle(lambda: dialog._names == [])
        dialog.set_toggle.set_active_name("phosphor")
        settle(lambda: dialog._names == ["phosphor:acorn"])
        self.assertEqual(dialog._names, ["phosphor:acorn"])

    def test_a_word_known_to_the_dictionary_and_to_the_names_lists_each_pictogram_once(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.search(dialog, "camera", "phosphor:camera")
        self.assertEqual(len(dialog._names), len(set(dialog._names)))

    def test_the_search_understands_french_in_phosphor(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.search(dialog, "maison", "phosphor:house")
        self.assertEqual(dialog._names[0], "phosphor:house")
        self.assertIn("phosphor:garage", dialog._names)
        self.assertNotIn("phosphor:acorn", dialog._names)

    def test_a_phosphor_choice_is_reported_with_its_prefix(self):
        chosen = []
        dialog = self.dialog(chosen=chosen)
        self.show(dialog, "phosphor")
        self.search(dialog, "acor", "phosphor:acorn")
        dialog._on_activated(0)
        self.assertEqual(chosen, ["phosphor:acorn"])

    def test_the_current_phosphor_icon_opens_its_set_and_is_selected(self):
        dialog = self.dialog(current="phosphor:acorn")
        self.assertEqual(dialog.set_toggle.get_active_name(), "phosphor")
        self.assertIn("phosphor:acorn", dialog._names)
        self.assertEqual(dialog.selected_name(), "phosphor:acorn")
        self.assertEqual(dialog.grid.get_model().get_selected(), dialog._names.index("phosphor:acorn"))
        self.assertTrue(dialog.choose_button.get_sensitive())


class CategoryTests(PickerCase):
    def categories(self, dialog):
        model = dialog.category_row.get_model()
        return [model.get_string(index) for index in range(model.get_n_items())]

    def choose(self, dialog, label):
        dialog.category_row.set_selected(self.categories(dialog).index(label))

    def test_the_theme_has_no_categories(self):
        dialog = self.dialog()
        self.assertFalse(dialog.category_row.get_visible())

    def test_phosphor_offers_its_categories_after_all(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.assertTrue(dialog.category_row.get_visible())
        labels = self.categories(dialog)
        self.assertEqual(labels[0], "All")
        for wanted in ("Nature", "Development", "Games", "Weather"):
            self.assertIn(wanted, labels)

    def test_a_category_filters_the_list(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.choose(dialog, "Nature")
        settle(lambda: len(dialog._names) < 1512)
        expected = self.service.icon_sets()["phosphor"].names_in_category("nature")
        self.assertEqual(sorted(dialog._names), sorted(expected))

    def test_a_query_typed_first_is_kept_when_a_category_is_chosen(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.search(dialog, "tree")
        settle(lambda: len(dialog._names) < 100)
        self.choose(dialog, "Nature")
        settle(lambda: all(name in self.service.icon_sets()["phosphor"].names_in_category("nature") for name in dialog._names) and dialog._names)
        self.assertIn("phosphor:tree", dialog._names)
        self.assertLess(len(dialog._names), 50)

    def test_a_category_and_a_query_combine(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.choose(dialog, "Nature")
        self.search(dialog, "tree")
        settle(lambda: all(name in self.service.icon_sets()["phosphor"].names_in_category("nature") for name in dialog._names))
        self.assertIn("phosphor:tree", dialog._names)
        self.assertNotIn("phosphor:tree-structure", dialog._names)

    def test_all_brings_the_whole_set_back(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.choose(dialog, "Nature")
        settle(lambda: len(dialog._names) < 1512)
        self.choose(dialog, "All")
        settle(lambda: len(dialog._names) == 1512)
        self.assertEqual(len(dialog._names), 1512)

    def test_changing_the_set_resets_the_category(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        self.choose(dialog, "Nature")
        settle(lambda: len(dialog._names) < 1512)
        dialog.set_toggle.set_active_name("emoji")
        settle(lambda: dialog._names and dialog._names[0].startswith("emoji:"))
        self.assertEqual(dialog.category_row.get_selected(), 0)
        self.assertEqual(len(dialog._names), 1595)

    def test_the_brands_have_no_categories(self):
        dialog = self.dialog()
        self.show(dialog, "brands")
        self.assertFalse(dialog.category_row.get_visible())

    def test_the_labels_are_translated_through_gettext(self):
        from custicon.ui.picker import CATEGORY_LABELS

        for identifier in ("arrows", "brand", "commerce", "communication", "design", "development", "editor", "finance", "games", "health", "map", "media", "nature", "objects", "office", "people", "system", "weather", "smileys", "food", "travel", "activities", "symbols", "flags"):
            self.assertIn(identifier, CATEGORY_LABELS)

    def test_every_category_of_the_sets_has_a_label(self):
        from custicon.ui.picker import CATEGORY_LABELS

        for key in ("phosphor", "emoji"):
            for identifier in self.service.icon_sets()[key].categories():
                self.assertIn(identifier, CATEGORY_LABELS, identifier)


class IconSetPickerTests(PickerCase):
    def test_every_source_is_offered(self):
        self.assertEqual(self.toggles(self.dialog()), ["theme", "phosphor", "emoji", "brands"])

    def test_emoji_are_listed_and_searched_in_english(self):
        dialog = self.dialog()
        self.show(dialog, "emoji")
        self.assertTrue(all(name.startswith("emoji:") for name in dialog._names))
        self.search(dialog, "rocket", "emoji:rocket")
        self.assertEqual(dialog._names[0], "emoji:rocket")

    def test_emoji_are_searched_in_french_without_accents(self):
        dialog = self.dialog(language="fr")
        self.show(dialog, "emoji")
        self.search(dialog, "medaille")
        settle(lambda: "emoji:1st-place-medal" in dialog._names)
        self.assertIn("emoji:1st-place-medal", dialog._names)
        self.search(dialog, "chat")
        settle(lambda: any("cat" in name for name in dialog._names))
        self.assertTrue(any("cat" in name for name in dialog._names))

    def test_brand_logos_are_searched_by_name(self):
        dialog = self.dialog()
        self.show(dialog, "brands")
        self.assertGreaterEqual(len(dialog._names), 3400)
        self.search(dialog, "github", "brand:github")
        self.assertEqual(dialog._names[0], "brand:github")

    def test_the_theme_words_are_only_used_for_the_theme_and_phosphor_has_its_own(self):
        dialog = self.dialog(language="fr")
        self.show(dialog, "emoji")
        self.assertEqual(dialog._fragments("maison"), [])
        self.show(dialog, "brands")
        self.assertEqual(dialog._fragments("maison"), [])
        self.show(dialog, "phosphor")
        self.assertEqual(dialog._fragments("maison")[:1], ["house"])
        dialog.set_toggle.set_active_name("theme")
        self.assertIn("go-home", dialog._fragments("accueil"))

    def test_an_emoji_choice_is_reported_with_its_prefix(self):
        chosen = []
        dialog = self.dialog(chosen=chosen)
        self.show(dialog, "emoji")
        self.search(dialog, "rocket", "emoji:rocket")
        dialog._on_activated(0)
        self.assertEqual(chosen, ["emoji:rocket"])

    def test_the_current_icon_opens_its_own_set(self):
        for current, key in (("emoji:rocket", "emoji"), ("brand:github", "brands"), ("phosphor:acorn", "phosphor"), ("star-symbolic", "theme")):
            dialog = self.dialog(current=current)
            self.assertEqual(dialog.set_toggle.get_active_name(), key, current)

    def test_the_names_are_given_in_the_desktop_language(self):
        dialog = self.dialog(language="fr")
        self.assertEqual(dialog.label_of("emoji:2nd-place-medal"), "médaille d’argent")
        self.assertEqual(dialog.label_of("phosphor:acorn"), "acorn")
        self.assertEqual(self.dialog(language="en").label_of("emoji:2nd-place-medal"), "2nd place medal")

    def test_the_footer_names_the_selected_pictogram(self):
        dialog = self.dialog(current="emoji:2nd-place-medal", language="fr")
        self.assertEqual(dialog.selection_label.get_text(), "médaille d’argent")

    def test_selecting_a_pictogram_updates_the_footer_and_the_choose_button(self):
        dialog = self.dialog(language="fr")
        self.show(dialog, "emoji")
        self.search(dialog, "2nd place", "emoji:2nd-place-medal")
        self.assertFalse(dialog.choose_button.get_sensitive())
        dialog.select(0)
        self.assertEqual(dialog.selection_label.get_text(), "médaille d’argent")
        self.assertTrue(dialog.choose_button.get_sensitive())

    def test_choosing_without_a_selection_does_nothing(self):
        chosen = []
        dialog = self.dialog(chosen=chosen)
        dialog._on_choose(None)
        self.assertEqual(chosen, [])

    def test_the_row_of_the_editor_names_the_pictogram_with_its_set(self):
        from unittest import mock

        from custicon import iconsets

        self.window.add_folder(self.make_folder())
        settle()
        for language, expected in (("fr", "médaille d’argent (Émoji)"), ("en", "2nd place medal (Emoji)")):
            with mock.patch.object(iconsets, "current_language", return_value=language):
                self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("emoji:2nd-place-medal"))
            self.assertEqual(self.window.editor.glyph_label.get_text(), expected)

    def test_the_picker_of_the_window_is_given_the_registry_and_the_memory(self):
        self.window.add_folder(self.make_folder("Photos"))
        settle()
        self.window._choose_glyph()
        settle()
        picker = self.window.get_visible_dialog()
        self.assertIs(picker._sets, self.service.icon_sets())
        self.assertTrue(any(name.startswith("phosphor:") for name in picker.suggestion_names))

    def test_the_recent_pictograms_are_offered_again_by_the_picker_of_the_window(self):
        self.window.add_folder(self.make_folder("Docs"))
        settle()
        self.service.remember_glyph("phosphor:acorn")
        self.window._choose_glyph()
        settle()
        self.assertEqual(self.window.get_visible_dialog().recent_names, ["phosphor:acorn"])

    def test_a_favorite_set_in_the_picker_of_the_window_is_saved(self):
        self.window.add_folder(self.make_folder("Docs"))
        settle()
        self.window._choose_glyph()
        settle()
        picker = self.window.get_visible_dialog()
        picker.pick_name("emoji:rocket")
        picker.favorite_button.set_active(True)
        self.assertEqual(self.service.picker_memory().favorites, ("emoji:rocket",))
        self.window._choose_glyph()
        settle()
        self.assertIn("emoji:rocket", self.window.get_visible_dialog().favorite_names)

    def test_choosing_from_the_window_remembers_the_pictogram(self):
        self.window.add_folder(self.make_folder("Docs"))
        settle()
        self.window._choose_glyph()
        settle()
        picker = self.window.get_visible_dialog()
        picker.set_toggle.set_active_name("phosphor")
        settle(lambda: picker._names and picker._names[0].startswith("phosphor:"))
        picker.search.set_text("acor")
        settle(lambda: picker._names == ["phosphor:acorn"])
        picker._on_activated(0)
        settle()
        self.assertEqual(self.service.picker_memory().recent[0], "phosphor:acorn")
        self.assertEqual(self.window.editor.draft.glyph_name, "phosphor:acorn")


class SidebarTests(PickerCase):
    def test_suggestions_come_from_the_name_of_the_folder(self):
        dialog = self.dialog(folder_name="Photos")
        self.assertIn("phosphor:camera", dialog.suggestion_names)
        self.assertTrue(dialog.suggestions_box.get_visible())

    def test_a_folder_without_a_meaningful_name_has_no_suggestions(self):
        dialog = self.dialog(folder_name="xqzvw")
        self.assertEqual(dialog.suggestion_names, [])
        self.assertFalse(dialog.suggestions_box.get_visible())

    def test_recents_and_favorites_are_listed_and_hidden_when_empty(self):
        empty = self.dialog()
        self.assertFalse(empty.recent_box.get_visible())
        self.assertFalse(empty.favorites_box.get_visible())
        dialog = self.dialog(recent=("phosphor:house", "emoji:rocket"), favorites=("brand:github",))
        self.assertEqual(dialog.recent_names, ["phosphor:house", "emoji:rocket"])
        self.assertEqual(dialog.favorite_names, ["brand:github"])
        self.assertTrue(dialog.recent_box.get_visible())
        self.assertTrue(dialog.favorites_box.get_visible())

    def test_a_remembered_name_that_no_longer_exists_is_skipped(self):
        dialog = self.dialog(recent=("phosphor:house", "phosphor:vanished", "emoji:vanished", "gone-symbolic"), favorites=("brand:vanished",))
        self.assertEqual(dialog.recent_names, ["phosphor:house"])
        self.assertEqual(dialog.favorite_names, [])

    def test_clicking_a_remembered_pictogram_selects_it(self):
        dialog = self.dialog(recent=("phosphor:house",))
        dialog.pick_name("phosphor:house")
        self.assertEqual(dialog.selected_name(), "phosphor:house")
        self.assertTrue(dialog.choose_button.get_sensitive())
        self.assertEqual(dialog.selection_label.get_text(), "house")

    def test_picking_a_name_of_the_current_list_highlights_it_in_the_grid(self):
        dialog = self.dialog()
        self.show(dialog, "phosphor")
        dialog.pick_name("phosphor:house")
        self.assertEqual(dialog.grid.get_model().get_selected(), dialog._names.index("phosphor:house"))

    def test_the_favorite_button_follows_the_selection(self):
        calls = []
        dialog = self.dialog(favorites=("phosphor:house",), on_favorite=lambda name, favorite: calls.append((name, favorite)) or True)
        self.assertFalse(dialog.favorite_button.get_sensitive())
        dialog.pick_name("phosphor:house")
        self.assertTrue(dialog.favorite_button.get_sensitive())
        self.assertTrue(dialog.favorite_button.get_active())
        dialog.pick_name("emoji:rocket")
        self.assertFalse(dialog.favorite_button.get_active())

    def test_toggling_the_favorite_reports_it_and_updates_the_list(self):
        calls = []
        dialog = self.dialog(on_favorite=lambda name, favorite: calls.append((name, favorite)) or True)
        dialog.pick_name("emoji:rocket")
        dialog.favorite_button.set_active(True)
        self.assertEqual(calls, [("emoji:rocket", True)])
        self.assertEqual(dialog.favorite_names, ["emoji:rocket"])
        self.assertTrue(dialog.favorites_box.get_visible())
        dialog.favorite_button.set_active(False)
        self.assertEqual(calls[-1], ("emoji:rocket", False))
        self.assertEqual(dialog.favorite_names, [])
        self.assertFalse(dialog.favorites_box.get_visible())

    def test_a_failed_save_does_not_change_the_list(self):
        dialog = self.dialog(on_favorite=lambda name, favorite: False)
        dialog.pick_name("emoji:rocket")
        dialog.favorite_button.set_active(True)
        self.assertEqual(dialog.favorite_names, [])
        self.assertFalse(dialog.favorite_button.get_active())

    def test_selecting_in_the_grid_updates_the_favorite_state(self):
        dialog = self.dialog(favorites=("emoji:rocket",))
        self.show(dialog, "emoji")
        self.search(dialog, "rocket", "emoji:rocket")
        dialog.select(0)
        self.assertTrue(dialog.favorite_button.get_active())

    def test_the_sidebar_is_hidden_on_a_narrow_window(self):
        dialog = self.dialog(folder_name="Photos")
        self.assertTrue(dialog.sidebar.get_visible())
        self.assertGreaterEqual(dialog.get_content_width(), 640)


@unittest.skipUnless(DISPLAY_AVAILABLE, "needs a display")
class PaletteTests(unittest.TestCase):
    def make(self, base="none"):
        from custicon.ui.palette import Palette

        self.calls = []
        return Palette("Folder Tint", base, lambda mode, color: self.calls.append((mode, color)))

    def active(self, palette):
        return [name for name, button in palette.buttons().items() if button.get_active()]

    def test_the_base_choice_is_active_at_first_and_nothing_is_reported(self):
        palette = self.make("none")
        self.assertEqual(self.active(palette), ["base"])
        self.assertEqual(palette.state(), ("none", None))
        self.assertEqual(self.calls, [])

    def test_the_base_mode_is_the_one_given(self):
        self.assertEqual(self.make("auto").state(), ("auto", None))

    def test_there_are_nine_palette_colors_after_the_base_and_the_accent(self):
        palette = self.make()
        self.assertEqual(list(palette.buttons())[:2], ["base", "accent"])
        colors = [name for name in palette.buttons() if name.startswith("#")]
        self.assertEqual(len(colors), 9)
        self.assertEqual(colors[0], "#3584e4")

    def test_choosing_a_palette_color_reports_a_custom_mode(self):
        palette = self.make()
        palette.choose_color("#e62d42")
        self.assertEqual(self.calls, [("custom", "#e62d42")])
        self.assertEqual(self.active(palette), ["#e62d42"])
        self.assertEqual(palette.state(), ("custom", "#e62d42"))

    def test_choosing_the_accent_or_the_base_reports_their_mode(self):
        palette = self.make("auto")
        palette.choose_accent()
        palette.choose_base()
        self.assertEqual(self.calls, [("accent", None), ("auto", None)])
        self.assertEqual(self.active(palette), ["base"])

    def test_only_one_choice_is_active_at_a_time(self):
        palette = self.make()
        for color in ("#e62d42", "#3a944a", "#9141ac"):
            palette.choose_color(color)
            self.assertEqual(self.active(palette), [color])
        palette.choose_accent()
        self.assertEqual(self.active(palette), ["accent"])

    def test_clicking_the_active_choice_keeps_it_active(self):
        palette = self.make()
        palette.choose_color("#e62d42")
        palette.buttons()["#e62d42"].set_active(False)
        self.assertTrue(palette.buttons()["#e62d42"].get_active())
        self.assertEqual(palette.state(), ("custom", "#e62d42"))

    def test_setting_a_state_selects_the_right_choice_without_reporting(self):
        palette = self.make()
        palette.set_state("custom", "#3584E4")
        self.assertEqual(self.active(palette), ["#3584e4"])
        palette.set_state("accent", None)
        self.assertEqual(self.active(palette), ["accent"])
        palette.set_state("none", None)
        self.assertEqual(self.active(palette), ["base"])
        self.assertEqual(self.calls, [])

    def test_a_color_outside_the_palette_lights_the_custom_button(self):
        palette = self.make()
        palette.set_state("custom", "#123456")
        self.assertEqual(self.active(palette), ["custom"])
        self.assertEqual(palette.custom_hex(), "#123456")
        self.assertEqual(palette.state(), ("custom", "#123456"))
        self.assertEqual(self.calls, [])

    def test_the_custom_button_reports_the_chosen_color(self):
        from custicon.ui.editor import rgba_from_hex

        palette = self.make()
        palette.custom_button.set_rgba(rgba_from_hex("#112233"))
        self.assertEqual(self.calls[-1], ("custom", "#112233"))
        self.assertEqual(self.active(palette), ["custom"])

    def test_a_palette_color_does_not_move_the_custom_color(self):
        palette = self.make()
        palette.set_state("custom", "#123456")
        palette.choose_color("#e62d42")
        self.assertEqual(palette.custom_hex(), "#123456")
        self.assertEqual(palette.state(), ("custom", "#e62d42"))

    def test_an_invalid_state_color_falls_back_to_the_custom_button(self):
        palette = self.make()
        palette.set_state("custom", "not a color")
        self.assertEqual(self.active(palette), ["custom"])

    def test_a_custom_state_without_a_color_keeps_the_last_custom_color(self):
        palette = self.make()
        palette.set_state("custom", "#123456")
        palette.set_state("none", None)
        palette.set_state("custom", None)
        self.assertEqual(palette.state(), ("custom", "#123456"))

    def test_the_accent_swatch_shows_the_accent_color(self):
        palette = self.make()
        self.assertEqual(palette.accent_color(), "#3584e4")
        palette.set_accent("#e66100")
        self.assertEqual(palette.accent_color(), "#e66100")
        palette.set_accent("garbage")
        self.assertEqual(palette.accent_color(), "#e66100")

    def test_every_choice_has_a_tooltip_and_an_accessible_name(self):
        palette = self.make()
        for name, button in palette.buttons().items():
            self.assertTrue(button.get_tooltip_text(), name)
        self.assertEqual(palette.buttons()["#3584e4"].get_tooltip_text(), "Blue")
        self.assertEqual(palette.buttons()["#e62d42"].get_tooltip_text(), "Red")

    def test_the_computed_color_is_added_to_the_tooltips(self):
        palette = self.make("auto")
        palette.set_computed("#438de6")
        self.assertIn("#438de6", palette.buttons()["base"].get_tooltip_text())
        palette.set_state("accent", None)
        palette.set_computed(None)
        self.assertNotIn("#", palette.buttons()["base"].get_tooltip_text())

    def test_the_palette_is_a_labelled_group(self):
        palette = self.make()
        self.assertEqual(palette.get_accessible_role(), Gtk.AccessibleRole.GROUP)


@unittest.skipUnless(DISPLAY_AVAILABLE, "needs a display")
class CustomSwatchTests(unittest.TestCase):
    def setUp(self):
        from custicon.ui import palette as palette_module

        Adw.init()
        self.module = palette_module
        self.swatch = palette_module.CustomSwatch()

    def render(self, chosen, size=48):
        import cairo

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        self.module.draw_rainbow(cairo.Context(surface), size, size, chosen)
        surface.flush()
        data = bytes(surface.get_data())

        def at(x, y):
            offset = y * surface.get_stride() + x * 4
            return data[offset + 2], data[offset + 1], data[offset], data[offset + 3]

        return at

    def test_the_swatch_keeps_the_color_it_is_given(self):
        self.swatch.set_rgba(rgba_from_hex("#123456"))
        self.assertEqual(hex_from_rgba(self.swatch.get_rgba()), "#123456")

    def test_a_new_color_is_announced(self):
        seen = []
        self.swatch.connect("notify::rgba", lambda *_args: seen.append(hex_from_rgba(self.swatch.get_rgba())))
        self.swatch.set_rgba(rgba_from_hex("#abcdef"))
        self.assertEqual(seen, ["#abcdef"])

    def test_the_swatch_starts_not_chosen(self):
        self.assertFalse(self.swatch.is_chosen())

    def test_the_chosen_state_can_be_set_and_cleared(self):
        self.swatch.set_chosen(True)
        self.assertTrue(self.swatch.is_chosen())
        self.swatch.set_chosen(False)
        self.assertFalse(self.swatch.is_chosen())

    def test_without_a_chosen_color_the_whole_disc_is_a_rainbow(self):
        at = self.render(None)
        colors = {at(24 + dx, 24 + dy)[:3] for dx, dy in ((18, 0), (-18, 0), (0, 18), (0, -18), (13, 13), (-13, -13))}
        self.assertGreaterEqual(len(colors), 4)
        self.assertGreaterEqual(at(24 + 6, 24 + 1)[3], 240)

    def test_there_is_no_see_through_seam_between_the_sectors(self):
        import math

        at = self.render(None, 96)
        for degrees in range(0, 360, 5):
            x = 48 + round(36 * math.cos(math.radians(degrees)))
            y = 48 + round(36 * math.sin(math.radians(degrees)))
            self.assertGreaterEqual(at(x, y)[3], 250, degrees)

    def test_the_corners_stay_transparent(self):
        at = self.render(None)
        self.assertEqual(at(0, 0)[3], 0)
        self.assertEqual(at(47, 47)[3], 0)

    def test_a_chosen_color_fills_the_center(self):
        at = self.render("#112233")
        self.assertEqual(at(24, 24)[:3], (0x11, 0x22, 0x33))

    def test_a_white_gap_separates_the_center_from_the_rainbow(self):
        at = self.render("#112233")
        gap = int(23 * self.module.CENTER_GAP) - 1
        self.assertEqual(at(24 + gap, 24)[:3], (255, 255, 255))

    def test_the_rainbow_ring_stays_around_a_chosen_color(self):
        at = self.render("#112233")
        ring = at(24 + 20, 24)
        self.assertNotEqual(ring[:3], (255, 255, 255))
        self.assertNotEqual(ring[:3], (0x11, 0x22, 0x33))
        self.assertEqual(ring[3], 255)

    def test_the_rainbow_covers_every_hue_around_the_ring(self):
        at = self.render(None, 96)
        import colorsys
        import math

        hues = set()
        for degrees in range(0, 360, 20):
            x = 48 + round(40 * math.cos(math.radians(degrees)))
            y = 48 + round(40 * math.sin(math.radians(degrees)))
            red, green, blue, _alpha = at(x, y)
            hues.add(round(colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)[0] * 12) % 12)
        self.assertGreaterEqual(len(hues), 10)

    def draw_swatch(self, size=48):
        import cairo

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        self.swatch._draw(None, cairo.Context(surface), size, size)
        surface.flush()
        data = bytes(surface.get_data())
        offset = (size // 2) * surface.get_stride() + (size // 2) * 4
        return data[offset + 2], data[offset + 1], data[offset]

    def test_the_drawn_swatch_shows_its_color_only_when_it_is_the_chosen_one(self):
        self.swatch.set_rgba(rgba_from_hex("#112233"))
        self.assertNotEqual(self.draw_swatch(), (0x11, 0x22, 0x33))
        self.swatch.set_chosen(True)
        self.assertEqual(self.draw_swatch(), (0x11, 0x22, 0x33))

    def test_the_drawn_center_follows_a_new_color(self):
        self.swatch.set_chosen(True)
        self.swatch.set_rgba(rgba_from_hex("#112233"))
        self.swatch.set_rgba(rgba_from_hex("#445566"))
        self.assertEqual(self.draw_swatch(), (0x44, 0x55, 0x66))

    def test_a_click_opens_the_color_dialog_with_the_current_color(self):
        self.swatch.set_rgba(rgba_from_hex("#445566"))
        with mock.patch.object(Gtk.ColorDialog, "choose_rgba") as choose:
            self.swatch.emit("clicked")
        choose.assert_called_once()
        self.assertEqual(hex_from_rgba(choose.call_args.args[1]), "#445566")

    def test_the_chosen_color_becomes_the_color_of_the_swatch(self):
        dialog = mock.Mock()
        dialog.choose_rgba_finish.return_value = rgba_from_hex("#778899")
        self.swatch._on_chosen(dialog, None)
        self.assertEqual(hex_from_rgba(self.swatch.get_rgba()), "#778899")

    def test_a_cancelled_dialog_changes_nothing(self):
        self.swatch.set_rgba(rgba_from_hex("#445566"))
        dialog = mock.Mock()
        dialog.choose_rgba_finish.side_effect = GLib.Error("dismissed")
        self.swatch._on_chosen(dialog, None)
        self.assertEqual(hex_from_rgba(self.swatch.get_rgba()), "#445566")

    def test_the_palette_marks_the_swatch_only_for_a_custom_color(self):
        from custicon.ui.palette import Palette

        palette = Palette("Color", "none")
        self.assertFalse(palette.custom_button.is_chosen())
        palette.choose_color("#e62d42")
        self.assertFalse(palette.custom_button.is_chosen())
        palette.set_state("custom", "#123456")
        self.assertTrue(palette.custom_button.is_chosen())
        palette.choose_base()
        self.assertFalse(palette.custom_button.is_chosen())

    def test_the_palette_reads_the_swatch_color_back(self):
        from custicon.ui.palette import Palette

        palette = Palette("Color", "none")
        palette.custom_button.set_rgba(rgba_from_hex("#0a141e"))
        self.assertEqual(palette.state(), ("custom", "#0a141e"))
        self.assertTrue(palette.custom_button.is_chosen())

    def test_the_swatch_is_named_for_assistive_technology(self):
        from custicon.ui.palette import Palette

        self.assertEqual(Palette("Color", "none").custom_button.get_tooltip_text(), "Custom")


class ColorModeUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor

    def test_the_pictogram_color_starts_automatic(self):
        self.assertEqual(self.editor.color_palette.state(), ("auto", None))
        self.assertEqual(self.editor.draft.color_mode, "auto")

    def test_choosing_the_system_accent_updates_the_draft(self):
        self.editor.color_palette.choose_accent()
        self.assertEqual(self.editor.draft.color_mode, "accent")
        self.assertTrue(self.editor.draft.to_settings().accent)

    def test_choosing_a_palette_color_sets_a_custom_color(self):
        self.editor.color_palette.choose_color("#e62d42")
        self.assertEqual(self.editor.draft.color_mode, "custom")
        self.assertEqual(self.editor.draft.color, "#e62d42")
        self.assertEqual(self.editor.draft.to_settings().color, "#e62d42")

    def test_the_computed_color_is_shown_in_the_tooltip_of_the_automatic_choice(self):
        self.editor.set_computed_color("#438de6")
        self.assertIn("#438de6", self.editor.color_palette.buttons()["base"].get_tooltip_text())

    def test_the_custom_button_sets_a_custom_color(self):
        self.editor.color_palette.custom_button.set_rgba(rgba_from_hex("#123456"))
        self.assertEqual((self.editor.draft.color_mode, self.editor.draft.color), ("custom", "#123456"))

    def test_switching_back_to_automatic_keeps_the_last_custom_color_in_the_draft(self):
        self.editor.color_palette.choose_color("#e62d42")
        self.editor.color_palette.choose_base()
        self.assertEqual(self.editor.draft.color_mode, "auto")
        self.assertEqual(self.editor.draft.color, "#e62d42")

    def test_the_accent_swatch_follows_the_system_accent_after_a_preview(self):
        self.window._render_preview()
        self.assertEqual(self.editor.color_palette.accent_color(), self.window._service.accent_color())
        self.assertEqual(self.editor.folder_tint_palette.accent_color(), self.window._service.accent_color())


class FolderTintUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor

    def test_the_folder_tint_starts_on_none(self):
        self.assertEqual(self.editor.folder_tint_palette.state(), ("none", None))
        self.assertEqual(self.editor.draft.folder_tint_mode, "none")

    def test_choosing_the_system_accent_updates_the_draft(self):
        self.editor.folder_tint_palette.choose_accent()
        self.assertEqual(self.editor.draft.folder_tint_mode, "accent")
        self.assertTrue(self.editor.draft.to_settings().folder_tint_accent)

    def test_choosing_a_palette_color_sends_its_color(self):
        self.editor.folder_tint_palette.choose_color("#ed5b00")
        self.assertEqual(self.editor.draft.folder_tint_mode, "custom")
        self.assertEqual(self.editor.draft.to_settings().folder_tint, "#ed5b00")

    def test_the_custom_button_sends_its_color(self):
        self.editor.folder_tint_palette.custom_button.set_rgba(rgba_from_hex("#112233"))
        self.assertEqual(self.editor.draft.folder_tint, "#112233")
        self.assertEqual(self.editor.draft.to_settings().folder_tint, "#112233")

    def test_going_back_to_none_removes_the_tint(self):
        self.editor.folder_tint_palette.choose_color("#ed5b00")
        self.editor.folder_tint_palette.choose_base()
        self.assertEqual(self.editor.draft.folder_tint_mode, "none")
        self.assertIsNone(self.editor.draft.to_settings().folder_tint)

    def test_the_computed_tint_is_shown_in_the_tooltip_of_the_base_choice(self):
        self.editor.set_computed_folder_tint("#ff0000")
        self.assertIn("#ff0000", self.editor.folder_tint_palette.buttons()["base"].get_tooltip_text())

    def test_reset_removes_the_folder_tint(self):
        self.editor.folder_tint_palette.choose_color("#ed5b00")
        self.assertTrue(self.editor.reset_button.get_sensitive())
        self.editor.reset_button.emit("clicked")
        self.assertEqual(self.editor.folder_tint_palette.state(), ("none", None))
        self.assertEqual(self.editor.draft.folder_tint_mode, "none")

    def test_the_preview_shows_the_tinted_folder_even_without_a_pictogram(self):
        self.assertFalse(self.editor.draft.ready)
        self.editor.folder_tint_palette.choose_color("#ed5b00")
        self.window._render_preview()
        self.assertIsNotNone(self.editor.big.get_paintable())
        self.assertFalse(self.editor.apply_button.get_sensitive())

    def test_the_preview_uses_the_plain_folder_without_a_tint_and_the_tinted_one_with_it(self):
        from unittest import mock

        from custicon.ui import window as window_module

        with mock.patch.object(window_module, "texture_from_file", wraps=window_module.texture_from_file) as plain, \
                mock.patch.object(window_module, "texture_from_svg_text", wraps=window_module.texture_from_svg_text) as tinted:
            self.window._render_preview()
            self.assertTrue(plain.called)
            self.assertFalse(tinted.called)
            plain.reset_mock()
            self.editor.folder_tint_palette.choose_color("#ed5b00")
            self.window._render_preview()
            self.assertTrue(tinted.called)
            self.assertFalse(plain.called)

    def test_the_preview_reports_the_accent_in_the_accent_swatch(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.folder_tint_palette.choose_accent()
        self.window._render_preview()
        self.assertIn("#", self.editor.folder_tint_palette.buttons()["accent"].get_tooltip_text())

    def test_applying_stores_the_folder_tint(self):
        folder = self.editor.draft.folder
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.folder_tint_palette.custom_button.set_rgba(rgba_from_hex("#e01b24"))
        self.window._apply()
        settle()
        stored = self.window._service.store.get(str(Path(folder).resolve()))
        self.assertIsNotNone(stored)
        self.assertEqual(stored.settings["folder_tint"], "#e01b24")


class DirectManipulationTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))

    def drag(self, dx, dy):
        self.editor._on_drag_begin(None, 0, 0)
        self.editor._on_drag_update(None, dx, dy)
        self.editor._on_drag_end(None, dx, dy)

    def test_dragging_moves_the_pictogram_by_the_fraction_of_the_preview(self):
        from custicon.ui.editor import BIG_SIZE

        self.drag(BIG_SIZE * 0.1, -BIG_SIZE * 0.2)
        self.assertAlmostEqual(self.editor.draft.offset_x, 0.1, places=3)
        self.assertAlmostEqual(self.editor.draft.offset_y, -0.2, places=3)

    def test_the_sliders_follow_the_drag(self):
        from custicon.ui.editor import BIG_SIZE

        self.drag(BIG_SIZE * 0.1, 0)
        self.assertAlmostEqual(self.editor.x_slider.adjustment.get_value(), 10.0, places=1)
        self.assertEqual(self.editor.x_slider.label.get_text(), "10%")

    def test_the_offsets_are_limited(self):
        from custicon.ui.editor import BIG_SIZE

        self.drag(BIG_SIZE * 5, -BIG_SIZE * 5)
        self.assertEqual((self.editor.draft.offset_x, self.editor.draft.offset_y), (0.5, -0.5))

    def test_a_drag_starts_from_the_current_position(self):
        from custicon.ui.editor import BIG_SIZE

        self.drag(BIG_SIZE * 0.1, 0)
        self.drag(BIG_SIZE * 0.05, 0)
        self.assertAlmostEqual(self.editor.draft.offset_x, 0.15, places=3)

    def test_a_drag_without_a_pictogram_does_nothing(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_text(""))
        self.assertFalse(self.editor.draft.ready)
        self.drag(50, 50)
        self.assertEqual((self.editor.draft.offset_x, self.editor.draft.offset_y), (0.0, 0.0))

    def test_an_update_without_a_begin_does_nothing(self):
        self.editor._on_drag_update(None, 50, 50)
        self.assertEqual(self.editor.draft.offset_x, 0.0)

    def test_the_wheel_enlarges_and_reduces(self):
        self.assertTrue(self.editor._on_scroll(None, 0, -1))
        larger = self.editor.draft.size
        self.assertGreater(larger, 1.0)
        self.editor._on_scroll(None, 0, 1)
        self.editor._on_scroll(None, 0, 1)
        self.assertLess(self.editor.draft.size, larger)

    def test_the_wheel_is_limited_and_follows_the_slider(self):
        from custicon.service import MAX_SIZE, MIN_SIZE

        for _ in range(100):
            self.editor._on_scroll(None, 0, -3)
        self.assertEqual(self.editor.draft.size, MAX_SIZE)
        for _ in range(200):
            self.editor._on_scroll(None, 0, 3)
        self.assertEqual(self.editor.draft.size, MIN_SIZE)
        self.assertEqual(self.editor.size_slider.label.get_text(), "20%")

    def test_the_wheel_without_movement_or_without_a_pictogram_is_ignored(self):
        self.assertFalse(self.editor._on_scroll(None, 0, 0))
        self.editor.update_glyph(self.editor.draft.with_glyph_text(""))
        self.assertFalse(self.editor._on_scroll(None, 0, -1))
        self.assertEqual(self.editor.draft.size, 1.0)

    def test_the_reset_button_wakes_up_after_a_drag(self):
        self.assertFalse(self.editor.reset_button.get_sensitive())
        self.drag(30, 0)
        self.assertTrue(self.editor.reset_button.get_sensitive())

    def test_a_drag_reaches_the_preview_through_the_window(self):
        from custicon.ui.editor import BIG_SIZE

        self.drag(BIG_SIZE * 0.1, 0)
        self.window._render_preview()
        self.assertIsNotNone(self.editor.big.get_paintable())

    def test_the_big_preview_has_the_gesture_controllers(self):
        kinds = {type(self.editor.big.observe_controllers().get_item(index)).__name__ for index in range(self.editor.big.observe_controllers().get_n_items())}
        self.assertIn("GestureDrag", kinds)
        self.assertIn("EventControllerScroll", kinds)


class WindowSizeTests(WindowTestCase):
    def make_window(self):
        from custicon.ui.window import MainWindow

        window = MainWindow(shared_application(), self.service)
        self.addCleanup(window.destroy)
        return window

    def test_a_first_launch_uses_the_default_size_fitted_to_the_screen(self):
        from custicon.ui.windowstate import DEFAULT_HEIGHT, DEFAULT_WIDTH, fitted, screen_size

        expected = fitted(DEFAULT_WIDTH, DEFAULT_HEIGHT, screen_size())
        self.assertEqual(self.make_window().get_default_size(), expected)

    def test_everything_is_visible_at_the_default_size(self):
        window = self.make_window()
        window.add_folder(self.make_folder("Photos"))
        window.editor.update_glyph(window.editor.draft.with_glyph_name("star-symbolic"))
        window.present()
        settle(lambda: window.editor.scrolled.get_height() > 100)
        settle(lambda: False, 0.5)
        scrolled = window.editor.scrolled
        content = scrolled.get_child().get_preferred_size()[1].height
        self.assertLessEqual(content, scrolled.get_height() + 1, (content, scrolled.get_height()))
        self.assertEqual(window.editor.columns.get_orientation(), Gtk.Orientation.HORIZONTAL)

    def test_a_remembered_size_is_used(self):
        self.service.remember_window(820, 610, False)
        self.assertEqual(self.make_window().get_default_size(), (820, 610))

    def test_a_remembered_size_too_big_for_the_screen_is_reduced(self):
        from custicon.ui.windowstate import screen_size

        screen = screen_size()
        self.service.remember_window(9000, 9000, False)
        width, height = self.make_window().get_default_size()
        if screen is not None:
            self.assertLess(width, screen[0])
            self.assertLess(height, screen[1])

    def test_a_remembered_maximized_window_is_maximized(self):
        self.service.remember_window(820, 610, True)
        with mock.patch.object(Adw.ApplicationWindow, "maximize") as maximize:
            self.make_window()
        maximize.assert_called_once()

    def test_a_window_that_was_not_maximized_is_not_maximized(self):
        self.service.remember_window(820, 610, False)
        with mock.patch.object(Adw.ApplicationWindow, "maximize") as maximize:
            self.make_window()
        maximize.assert_not_called()

    def test_closing_remembers_the_size(self):
        window = self.make_window()
        window.set_default_size(777, 555)
        window._on_close_request()
        state = self.service.window_state()
        self.assertEqual((state.width, state.height, state.maximized), (777, 555, False))

    def test_closing_a_maximized_window_remembers_it_and_keeps_the_normal_size(self):
        window = self.make_window()
        window.set_default_size(777, 555)
        with mock.patch.object(window, "is_maximized", return_value=True):
            window._on_close_request()
        state = self.service.window_state()
        self.assertEqual((state.width, state.height, state.maximized), (777, 555, True))

    def test_a_full_screen_window_is_not_remembered(self):
        window = self.make_window()
        window.set_default_size(777, 555)
        with mock.patch.object(window, "is_fullscreen", return_value=True):
            window._on_close_request()
        self.assertIsNone(self.service.window_state())

    def test_the_next_window_opens_as_the_last_one_was_closed(self):
        first = self.make_window()
        first.set_default_size(731, 599)
        first._on_close_request()
        self.assertEqual(self.make_window().get_default_size(), (731, 599))

    def test_an_error_while_remembering_does_not_prevent_closing(self):
        window = self.make_window()
        with mock.patch.object(self.service.store, "save_window", side_effect=StoreError("disk full")):
            self.assertFalse(window._on_close_request())

    def test_closing_still_stops_the_pending_preview(self):
        window = self.make_window()
        window.add_folder(self.make_folder("Photos"))
        window._schedule_preview()
        self.assertTrue(window._preview_source)
        window._on_close_request()
        self.assertEqual(window._preview_source, 0)


class ListReuseTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.add_and_apply("a")
        self.add_and_apply("b")
        self.key = str(self.folder.resolve())
        self.window.reload(select=self.key)
        settle()

    def test_an_unchanged_list_keeps_its_rows(self):
        before = dict(self.window._rows)
        self.window.reload(select=self.key)
        self.assertEqual({key: id(row) for key, row in self.window._rows.items()}, {key: id(row) for key, row in before.items()})

    def test_a_change_of_the_registry_rebuilds_the_rows(self):
        before = self.window._rows[self.key]
        self.window.editor.update_glyph(self.window.editor.draft.with_glyph_name("heart-symbolic"))
        self.window._apply()
        settle()
        self.assertIsNot(self.window._rows[self.key], before)

    def test_a_change_of_status_rebuilds_the_rows(self):
        before = self.window._rows[self.key]
        write_custom_icon(self.folder, USER_ICON)
        self.window.reload(select=self.key)
        self.assertIsNot(self.window._rows[self.key], before)

    def test_a_new_pending_folder_rebuilds_the_rows(self):
        count = len(self.window._rows)
        self.window.add_folder(self.make_folder("c"))
        settle()
        self.assertEqual(len(self.window._rows), count + 1)

    def test_a_change_of_theme_rebuilds_the_rows(self):
        before = self.window._rows[self.key]
        self.theme = "Green"
        self.window.reload(select=self.key)
        self.assertIsNot(self.window._rows[self.key], before)

    def test_the_icon_of_a_row_is_read_once_per_image(self):
        self.window._row_textures.clear()
        self.window._rows_shown = None
        with mock.patch.object(window_module, "texture_from_file", wraps=window_module.texture_from_file) as read:
            self.window.reload(select=self.key)
            first = read.call_count
            self.window._rows_shown = None
            self.window.reload(select=self.key)
        self.assertGreater(first, 0)
        self.assertEqual(read.call_count, first)

    def test_regaining_focus_does_not_reload_an_unchanged_editor(self):
        self.window.is_active = lambda: True
        with mock.patch.object(self.window, "_show_key") as show:
            self.window._on_active_changed()
        show.assert_not_called()

    def test_regaining_focus_reloads_the_editor_when_the_folder_changed_outside(self):
        self.window.is_active = lambda: True
        write_custom_icon(self.folder, USER_ICON)
        with mock.patch.object(self.window, "_show_key", wraps=self.window._show_key) as show:
            self.window._on_active_changed()
        show.assert_called_once()
        self.assertTrue(self.window.editor.banner.get_revealed())

    def test_regaining_focus_drops_a_folder_forgotten_elsewhere_without_reloading_the_editor(self):
        other = str(self.workspace.joinpath("b").resolve())
        self.window.reload(select=other)
        settle()
        self.window.is_active = lambda: True
        self.window._service.forget(self.folder)
        with mock.patch.object(self.window, "_show_key") as show:
            self.window._on_active_changed()
        show.assert_not_called()
        self.assertNotIn(self.key, self.window._rows)
        self.assertEqual(self.window._current_key, other)

    def test_regaining_focus_with_unsaved_changes_keeps_them(self):
        self.window.editor.size_slider.adjustment.set_value(150)
        self.window.is_active = lambda: True
        with mock.patch.object(self.window, "_show_key") as show:
            self.window._on_active_changed()
        show.assert_not_called()
        self.assertAlmostEqual(self.window.editor.draft.size, 1.5)


class UnsupportedDesktopUiTests(WindowTestCase):
    def make_window(self, desktop):
        from custicon.ui.window import MainWindow

        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}):
            window = MainWindow(shared_application(), self.service)
        self.addCleanup(window.destroy)
        return window

    def test_the_banner_is_hidden_by_default(self):
        self.assertFalse(self.window.unsupported_banner.get_revealed())

    def test_a_desktop_without_support_shows_the_banner_with_the_file_manager(self):
        window = self.make_window("XFCE")
        self.assertTrue(window.unsupported_banner.get_revealed())
        self.assertIn("Thunar", window.unsupported_banner.get_title())

    def test_lxde_shows_the_banner_too(self):
        window = self.make_window("LXDE")
        self.assertIn("PCManFM", window.unsupported_banner.get_title())

    def test_the_supported_desktops_show_no_banner(self):
        for desktop in ("GNOME", "KDE", "LXQt", "X-Cinnamon", "MATE"):
            with self.subTest(desktop=desktop):
                self.assertFalse(self.make_window(desktop).unsupported_banner.get_revealed())

    def test_the_hint_names_the_file_manager_of_the_desktop(self):
        from custicon.ui.editor import Editor

        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}):
            editor = Editor()
        self.assertIn("Dolphin", editor.hint.get_label())
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "Enlightenment"}):
            other = Editor()
        self.assertIn("your file manager", other.hint.get_label())

    def test_applying_still_works_on_an_unsupported_desktop(self):
        window = self.make_window("XFCE")
        window.add_folder(self.make_folder("x"))
        window.editor.update_glyph(window.editor.draft.with_glyph_name("star-symbolic"))
        window._apply()
        settle()
        self.assertEqual(len(self.service.store.all()), 1)


class BatchRemovalUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.keys = []
        for name in ("a", "b", "c"):
            folder = self.add_and_apply(name)
            self.keys.append(str(folder.resolve()))
        self.window.reload(select=self.keys[0])
        settle()
        self.editor = self.window.editor
        self.window.choose_toggle.set_active(True)
        for key in self.keys[1:]:
            self.window._rows[key].check.set_active(True)

    def test_the_batch_offers_to_remove_the_icons(self):
        self.assertTrue(self.editor.remove_button.get_visible())
        self.assertEqual(self.editor.remove_button.get_label(), "Remove 3 Icons")

    def test_a_batch_of_new_folders_has_nothing_to_remove(self):
        fresh = [self.make_folder("x"), self.make_folder("y")]
        self.window.add_folders(fresh)
        settle()
        self.assertFalse(self.editor.remove_button.get_visible())

    def test_a_mixed_batch_counts_only_the_customized_folders(self):
        fresh = self.make_folder("x")
        self.window.add_folders([Path(self.keys[0]), fresh])
        settle()
        self.assertEqual(self.editor.remove_button.get_label(), "Remove 1 Icon")
        self.assertTrue(self.editor.remove_button.get_visible())

    def test_clicking_asks_for_a_confirmation(self):
        self.editor.remove_button.emit("clicked")
        settle()
        dialog = self.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.get_heading(), "Remove the Icons of 3 Folders?")

    def test_the_confirmation_mentions_the_folders_left_alone(self):
        fresh = self.make_folder("x")
        self.window.add_folders([Path(self.keys[0]), Path(self.keys[1]), fresh])
        settle()
        self.editor.remove_button.emit("clicked")
        settle()
        self.assertIn("1 folder without a Custicon icon is left alone.", self.window.get_visible_dialog().get_body())

    def test_cancelling_removes_nothing(self):
        self.window._remove_batch_confirmed(FakeAnswer("cancel"), None, self.keys)
        settle()
        for key in self.keys:
            self.assertIsNotNone(self.service.store.get(key))

    def test_confirming_removes_every_icon(self):
        self.window._remove_batch_confirmed(FakeAnswer("remove"), None, self.keys)
        settle()
        for key in self.keys:
            self.assertIsNone(self.service.store.get(key))
            self.assertIsNone(read_custom_icon(Path(key)))

    def test_after_removing_the_selection_mode_is_left(self):
        self.window.remove_many(self.keys)
        settle()
        self.assertFalse(self.window.choose_toggle.get_active())
        self.assertIsNone(self.window._batch)

    def test_a_toast_offers_to_undo_the_whole_batch(self):
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove_many(self.keys)
        self.assertIn("Icons removed from 3 folders", toast.call_args.args[0])
        self.assertEqual(toast.call_args.args[1], "Undo")
        toast.call_args.args[2]()
        settle()
        for key in self.keys:
            self.assertIsNotNone(self.service.store.get(key))
            self.assertIsNotNone(read_custom_icon(Path(key)))

    def test_undoing_reports_the_restoration(self):
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove_many(self.keys)
            undo = toast.call_args.args[2]
            undo()
        self.assertIn("Icon restored on 3 folders", toast.call_args.args[0])

    def test_a_partial_result_is_shown_in_a_dialog(self):
        write_custom_icon(Path(self.keys[0]), USER_ICON)
        self.window.remove_many(self.keys)
        settle()
        dialog = self.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        self.assertIn("1 icon kept", dialog.get_body())
        self.assertIn("2 icons removed", dialog.get_body())

    def test_a_partial_result_still_offers_to_undo_the_removed_icons(self):
        write_custom_icon(Path(self.keys[0]), USER_ICON)
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove_many(self.keys)
        self.assertIn("Icons removed from 2 folders", toast.call_args.args[0])
        self.assertEqual(toast.call_args.args[1], "Undo")
        toast.call_args.args[2]()
        settle()
        self.assertIsNotNone(self.service.store.get(self.keys[1]))
        self.assertIsNotNone(self.service.store.get(self.keys[2]))

    def test_a_failure_is_reported_and_the_others_are_removed(self):
        real = service_module.write_custom_icon

        def flaky(target, value):
            if Path(target) == Path(self.keys[1]):
                raise MetadataError("refused")
            return real(target, value)

        with mock.patch.object(service_module, "write_custom_icon", side_effect=flaky):
            self.window.remove_many(self.keys)
        settle()
        self.assertIsNone(self.service.store.get(self.keys[0]))
        self.assertIsNotNone(self.service.store.get(self.keys[1]))
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_the_list_is_rebuilt_without_the_removed_folders(self):
        self.window.remove_many(self.keys[:2])
        settle()
        self.assertNotIn(self.keys[0], self.window._rows)
        self.assertIn(self.keys[2], self.window._rows)


class SuggestionCacheTests(WindowTestCase):
    def test_the_suggestions_of_a_name_are_computed_once(self):
        with mock.patch.object(window_module, "suggest", wraps=window_module.suggest) as compute:
            first = self.window._suggestions_for("Photos", 6)
            second = self.window._suggestions_for("Photos", 6)
        self.assertEqual(first, second)
        self.assertEqual(compute.call_count, 1)

    def test_another_limit_or_name_is_computed_apart(self):
        with mock.patch.object(window_module, "suggest", wraps=window_module.suggest) as compute:
            self.window._suggestions_for("Photos", 6)
            self.window._suggestions_for("Photos", 8)
            self.window._suggestions_for("Music", 6)
        self.assertEqual(compute.call_count, 3)

    def test_a_returned_list_cannot_corrupt_the_cache(self):
        self.window._suggestions_for("Photos", 6).clear()
        self.assertTrue(self.window._suggestions_for("Photos", 6))

    def test_new_sets_empty_the_cache(self):
        self.window._suggestions_for("Photos", 6)
        replacement = mock.Mock()
        with mock.patch.object(self.service, "icon_sets", return_value=replacement), mock.patch.object(window_module, "suggest", return_value=["x"]) as compute:
            self.assertEqual(self.window._suggestions_for("Photos", 6), ["x"])
        compute.assert_called_once()

    def test_the_picker_reuses_the_suggestions_of_the_window(self):
        self.window.add_folder(self.make_folder("Photos"))
        settle()
        with mock.patch("custicon.ui.picker.suggest", side_effect=AssertionError("computed again")):
            self.window._choose_glyph()
            settle()
        picker = self.window.get_visible_dialog()
        self.assertIn("phosphor:camera", picker.suggestion_names)

    def test_the_picker_keeps_its_longer_list_of_suggestions(self):
        self.window.add_folder(self.make_folder("Photos"))
        settle()
        self.window._choose_glyph()
        settle()
        from custicon.suggestions import suggest

        expected = suggest("Photos", self.service.icon_sets(), window_module.PICKER_SUGGESTION_LIMIT)
        self.assertEqual(self.window.get_visible_dialog().suggestion_names, expected)
        self.assertEqual(window_module.PICKER_SUGGESTION_LIMIT, 8)


class PickerReuseTests(PickerCase):
    def test_a_paintable_is_created_once_per_name_and_size(self):
        dialog = self.dialog()
        first = dialog._paintable("phosphor:camera")
        self.assertIs(dialog._paintable("phosphor:camera"), first)
        self.assertIsNot(dialog._paintable("phosphor:camera", 28), first)

    def test_a_missing_name_is_remembered_as_missing(self):
        dialog = self.dialog()
        self.assertIsNone(dialog._paintable("phosphor:nothing-here"))
        self.assertIn(("phosphor:nothing-here", 24), dialog._paintables)

    def test_a_label_is_computed_once_per_name_and_language(self):
        dialog = self.dialog()
        with mock.patch.object(dialog._sets, "for_name", wraps=dialog._sets.for_name) as lookup:
            dialog.label_of("phosphor:camera")
            dialog.label_of("phosphor:camera")
        self.assertEqual(lookup.call_count, 1)

    def test_a_label_follows_the_language(self):
        from custicon import iconsets

        dialog = self.dialog(language="en")
        english = dialog.label_of("emoji:" + dialog._sets["emoji"].all_names()[0].split(":", 1)[1])
        with mock.patch.object(iconsets, "current_language", return_value="fr"):
            french = dialog.label_of("emoji:" + dialog._sets["emoji"].all_names()[0].split(":", 1)[1])
        self.assertNotEqual(english, french)

    def test_the_grid_is_limited_in_columns(self):
        self.assertEqual(self.dialog().grid.get_max_columns(), 12)


class BatchUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.folders = [self.make_folder("a"), self.make_folder("b"), self.make_folder("c")]
        self.window.add_folders(self.folders)
        settle()
        self.editor = self.window.editor

    def test_the_editor_names_the_batch(self):
        self.assertEqual(self.editor.name_label.get_text(), "3 folders")
        self.assertEqual(self.editor.path_label.get_text(), "a, b, c")
        self.assertEqual(self.editor.apply_button.get_label(), "Apply to 3 Folders")
        self.assertFalse(self.editor.remove_button.get_visible())
        self.assertEqual(self.window.content_page.get_title(), "3 folders")

    def test_the_main_page_is_shown_even_without_customized_folders(self):
        self.assertEqual(self.window.stack.get_visible_child_name(), "main")

    def test_applying_customizes_every_folder(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.window._apply()
        settle()
        for folder in self.folders:
            self.assertIsNotNone(self.service.store.get(str(folder.resolve())))
        self.assertIsNone(self.window._batch)
        self.assertEqual(len(self.window._rows), 3)

    def test_apply_needs_a_pictogram(self):
        self.assertFalse(self.editor.apply_button.get_sensitive())

    def test_a_failure_is_reported_and_the_others_are_applied(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.folders[1].rmdir()
        self.window._apply()
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.assertIsNotNone(self.service.store.get(str(self.folders[0].resolve())))
        self.assertIsNotNone(self.service.store.get(str(self.folders[2].resolve())))
        self.assertEqual(len(self.window._rows), 2)

    def test_selecting_a_row_leaves_the_batch(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.window._apply()
        settle()
        self.window.add_folders(self.folders)
        settle()
        self.window.list.select_row(self.window._rows[str(self.folders[0].resolve())])
        settle()
        self.assertIsNone(self.window._batch)
        self.assertEqual(self.editor.name_label.get_text(), "a")
        self.assertEqual(self.editor.apply_button.get_label(), "Apply")

    def test_duplicates_collapse_into_one_folder(self):
        self.window.add_folders([self.folders[0], self.folders[0]])
        settle()
        self.assertIsNone(self.window._batch)
        self.assertEqual(self.window._pending.folder, self.folders[0].resolve())

    def test_items_that_are_not_folders_are_ignored_with_a_toast(self):
        note = self.root / "note.txt"
        note.write_text("x")
        with mock.patch.object(self.window, "toast") as toast:
            self.window.open_files([Gio.File.new_for_path(str(self.folders[0])), Gio.File.new_for_path(str(self.folders[1])), Gio.File.new_for_path(str(note))])
        settle()
        self.assertIn("1 item ignored", toast.call_args.args[0])
        self.assertEqual(len(self.window._batch), 2)

    def test_the_chooser_accepts_several_folders(self):
        self.window._folders_chosen(FakeFoldersDialog(str(self.folders[0]), str(self.folders[1])), None)
        settle()
        self.assertEqual(len(self.window._batch), 2)

    def test_regaining_focus_keeps_the_batch(self):
        self.window.is_active = lambda: True
        self.window._on_active_changed()
        settle()
        self.assertEqual(len(self.window._batch), 3)
        self.assertEqual(self.editor.name_label.get_text(), "3 folders")

    def test_a_batch_counts_as_unsaved_work(self):
        self.assertTrue(self.window.has_unsaved_changes())

    def test_a_batch_has_no_name_suggestions(self):
        self.assertEqual(self.editor.suggestion_names, [])


class ChoosingUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.names = ["a", "b", "c"]
        for name in self.names:
            self.add_and_apply(name)
        self.window.reload(select=str((self.workspace / "a").resolve()))
        settle()
        self.editor = self.window.editor
        self.keys = [str((self.workspace / name).resolve()) for name in self.names]

    def row(self, name):
        return self.window._rows[str((self.workspace / name).resolve())]

    def test_the_checks_are_hidden_until_the_mode_is_turned_on(self):
        self.assertFalse(self.row("a").check.get_visible())
        self.window.choose_toggle.set_active(True)
        self.assertTrue(all(row.check.get_visible() for row in self.window._rows.values()))

    def test_the_current_folder_is_checked_first(self):
        self.window.choose_toggle.set_active(True)
        self.assertEqual(self.window._checked_keys(), [self.keys[0]])
        self.assertIsNone(self.window._batch)

    def test_checking_a_second_folder_opens_a_batch(self):
        self.window.choose_toggle.set_active(True)
        self.row("b").check.set_active(True)
        self.assertEqual(self.window._batch, [Path(self.keys[0]), Path(self.keys[1])])
        self.assertEqual(self.editor.name_label.get_text(), "2 folders")

    def test_clicking_a_row_toggles_its_check(self):
        self.window.choose_toggle.set_active(True)
        self.window._on_row_activated(self.window.list, self.row("c"))
        self.assertTrue(self.row("c").check.get_active())
        self.window._on_row_activated(self.window.list, self.row("c"))
        self.assertFalse(self.row("c").check.get_active())

    def test_unchecking_back_to_one_folder_leaves_the_batch(self):
        self.window.choose_toggle.set_active(True)
        self.row("b").check.set_active(True)
        self.row("a").check.set_active(False)
        self.assertIsNone(self.window._batch)
        self.assertEqual(self.editor.name_label.get_text(), "b")
        self.assertEqual(self.editor.apply_button.get_label(), "Apply")

    def test_applying_to_existing_folders_replaces_their_icon_and_leaves_the_mode(self):
        self.window.choose_toggle.set_active(True)
        self.row("b").check.set_active(True)
        self.row("c").check.set_active(True)
        self.editor.update_glyph(self.editor.draft.with_glyph_name("heart-symbolic"))
        self.window._apply()
        settle()
        for key in self.keys:
            self.assertEqual(self.service.store.get(key).settings["glyph_name"], "heart-symbolic")
        self.assertFalse(self.window.choose_toggle.get_active())
        self.assertIsNone(self.window._batch)
        self.assertFalse(self.row("a").check.get_visible())

    def test_leaving_the_mode_restores_single_selection(self):
        self.window.choose_toggle.set_active(True)
        self.row("b").check.set_active(True)
        self.window.choose_toggle.set_active(False)
        settle()
        self.assertIsNone(self.window._batch)
        self.assertEqual(self.window.list.get_selection_mode(), Gtk.SelectionMode.SINGLE)
        self.assertIsNotNone(self.window.list.get_selected_row())

    def test_a_row_click_does_not_open_the_folder_while_choosing(self):
        self.window.choose_toggle.set_active(True)
        before = self.window._current_key
        self.window._on_row_selected(self.window.list, self.row("c"))
        self.assertEqual(self.window._current_key, before)

    def test_adding_a_folder_leaves_the_mode(self):
        self.window.choose_toggle.set_active(True)
        self.window.add_folder(self.make_folder("fresh"))
        settle()
        self.assertFalse(self.window.choose_toggle.get_active())


class SuggestionUiTests(WindowTestCase):
    def test_a_folder_named_after_a_subject_gets_suggestions(self):
        self.window.add_folder(self.make_folder("Photos"))
        settle()
        names = self.window.editor.suggestion_names
        self.assertIn("phosphor:camera", names)
        self.assertLessEqual(len(names), 6)
        self.assertTrue(self.window.editor.suggestions_row.get_visible())

    def test_a_folder_without_a_meaningful_name_gets_none(self):
        self.window.add_folder(self.make_folder("xqzvw"))
        settle()
        self.assertEqual(self.window.editor.suggestion_names, [])
        self.assertFalse(self.window.editor.suggestions_row.get_visible())

    def test_clicking_a_suggestion_chooses_it(self):
        self.window.add_folder(self.make_folder("Photos"))
        settle()
        self.window.editor.on_pick_suggestion("phosphor:camera")
        self.assertEqual(self.window.editor.draft.glyph_name, "phosphor:camera")
        self.assertEqual(self.service.picker_memory().recent[0], "phosphor:camera")

    def test_the_suggestions_follow_the_selected_folder(self):
        self.window.add_folder(self.make_folder("Photos"))
        self.window.add_folder(self.make_folder("Musique"))
        settle()
        self.assertTrue(any("music" in name for name in self.window.editor.suggestion_names))
        self.assertNotIn("phosphor:camera", self.window.editor.suggestion_names)

    def test_every_suggestion_is_a_button_with_a_name(self):
        self.window.add_folder(self.make_folder("Photos"))
        settle()
        box = self.window.editor.suggestions_box
        buttons = []
        child = box.get_first_child()
        while child is not None:
            buttons.append(child)
            child = child.get_next_sibling()
        self.assertEqual(len(buttons), len(self.window.editor.suggestion_names))
        for button in buttons:
            self.assertTrue(button.get_tooltip_text())


class EditorLayoutTests(WindowTestCase):
    def test_the_controls_do_not_need_a_scrollbar_at_the_default_window_size(self):
        self.window.set_default_size(1000, 720)
        self.window.add_folder(self.make_folder("Photos"))
        self.window.present()
        settle(lambda: self.window.editor.get_height() > 300)
        scrolled = self.window.editor.scrolled
        content = scrolled.get_child().get_preferred_size()[1].height
        self.assertLessEqual(content, scrolled.get_height() + 2, (content, scrolled.get_height()))

    def test_the_two_columns_become_one_on_a_narrow_pane(self):
        editor = self.window.editor
        self.assertEqual(editor.columns.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertEqual(editor.columns.get_first_child().get_visible(), True)


class OutdatedBannerTests(WindowTestCase):
    def test_the_banner_is_hidden_when_every_icon_is_up_to_date(self):
        self.add_and_apply()
        self.assertFalse(self.window.outdated_banner.get_revealed())

    def test_a_theme_change_reveals_the_banner_and_regenerating_hides_it(self):
        self.add_and_apply()
        self.theme = "Green"
        self.window._on_theme_changed()
        settle()
        self.assertTrue(self.window.outdated_banner.get_revealed())
        self.window.outdated_banner.emit("button-clicked")
        self.assertTrue(settle(lambda: self.window._regeneration is None))
        settle()
        self.assertFalse(self.window.outdated_banner.get_revealed())

    def test_an_accent_color_change_is_noticed(self):
        self.add_and_apply()
        self.theme = "Green"
        Adw.StyleManager.get_default().notify("accent-color")
        settle()
        self.assertTrue(self.window.outdated_banner.get_revealed())

    def test_a_damaged_registry_does_not_break_the_banner(self):
        self.add_and_apply()
        self.service.store.registry_path.write_text("{broken")
        self.service.store.registry_path.with_name("registry.json.bak").write_text("{broken")
        self.window._update_outdated_banner()
        self.assertFalse(self.window.outdated_banner.get_revealed())


class TextGlyphUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor

    def test_typing_a_text_selects_it_as_the_pictogram(self):
        self.editor.text_row.set_text("Z")
        self.assertEqual(self.editor.draft.glyph_text, "Z")
        self.assertTrue(self.editor.apply_button.get_sensitive())
        self.assertFalse(self.editor.tint_row.get_sensitive())

    def test_the_text_is_limited_to_three_characters(self):
        self.editor.text_row.set_text("ABCDE")
        self.assertEqual(self.editor.text_row.get_text(), "ABC")
        self.assertEqual(self.editor.draft.glyph_text, "ABC")

    def test_clearing_the_text_disables_apply(self):
        self.editor.text_row.set_text("Z")
        self.editor.text_row.set_text("")
        self.assertIsNone(self.editor.draft.glyph_text)
        self.assertFalse(self.editor.apply_button.get_sensitive())

    def test_choosing_a_system_icon_clears_the_text_field(self):
        self.editor.text_row.set_text("Z")
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.assertEqual(self.editor.text_row.get_text(), "")
        self.assertIsNone(self.editor.draft.glyph_text)

    def test_a_phosphor_icon_is_named_with_its_set_in_the_row(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("phosphor:acorn"))
        self.assertEqual(self.editor.glyph_label.get_text(), "acorn (Phosphor)")

    def test_the_picker_opened_from_the_window_offers_phosphor(self):
        self.window._choose_glyph()
        settle()
        picker = self.window.get_visible_dialog()
        self.assertIsNotNone(picker)
        self.assertIs(picker._sets, self.service.icon_sets())

    def test_typing_a_text_clears_the_other_sources_in_the_rows(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.text_row.set_text("Z")
        self.assertEqual(self.editor.glyph_label.get_text(), "“Z”")
        self.assertIsNone(self.editor.draft.glyph_name)

    def test_the_text_is_previewed_and_applied(self):
        self.editor.text_row.set_text("Z")
        settle(lambda: self.editor.big.get_paintable() is not None)
        self.assertIsNotNone(self.editor.big.get_paintable())
        self.window._apply()
        settle()
        entry = self.service.store.all()[str(self.window._current_key)]
        self.assertEqual(entry.settings["glyph_text"], "Z")

    def test_an_applied_text_reopens_in_the_field(self):
        self.editor.text_row.set_text("Z")
        self.window._apply()
        settle()
        self.window.reload(select=self.window._current_key)
        settle()
        self.assertEqual(self.editor.text_row.get_text(), "Z")


class FakeSaveDialog:
    def __init__(self, path):
        self._file = Gio.File.new_for_path(path)

    def save_finish(self, _result):
        return self._file

    def open_finish(self, _result):
        return self._file


class FakeRemoteDialog:
    def __init__(self):
        self._file = Gio.File.new_for_uri("https://example.org/backup.json")

    def save_finish(self, _result):
        return self._file

    def open_finish(self, _result):
        return self._file


class FakeFailingDialog:
    def save_finish(self, _result):
        raise GLib.Error("dismissed")

    def open_finish(self, _result):
        raise GLib.Error("dismissed")


class StylesUiTests(WindowTestCase):
    BLUE = {"color": "#3366cc", "accent": False, "size": 1.5, "offset_x": 0.1, "offset_y": -0.1, "tint": False}

    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor

    def rows(self):
        names = []
        child = self.editor.styles_list.get_first_child()
        while child is not None:
            names.append(child.get_child().get_first_child().get_label())
            child = child.get_next_sibling()
        return names

    def save(self, name, appearance=None):
        self.service.save_style(name, appearance or self.BLUE)
        self.window._refresh_styles()

    def test_the_styles_button_is_in_the_appearance_header(self):
        self.assertTrue(self.editor.styles_button.get_visible())
        self.assertEqual(self.editor.styles_button.get_label(), "Styles")

    def test_saved_styles_are_listed_in_the_popover(self):
        self.save("Blue")
        self.save("Calm")
        self.assertEqual(self.rows(), ["Blue", "Calm"])

    def test_the_list_follows_the_service(self):
        self.save("Blue")
        self.window.reload()
        settle()
        self.assertEqual(self.rows(), ["Blue"])

    def test_choosing_a_style_applies_its_appearance_and_keeps_the_pictogram(self):
        self.save("Blue")
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.styles_list.get_first_child().get_child().get_first_child().emit("clicked")
        draft = self.editor.draft
        self.assertEqual((draft.color_mode, draft.color, draft.size, draft.offset_x, draft.offset_y, draft.tint), ("custom", "#3366cc", 1.5, 0.1, -0.1, False))
        self.assertEqual(draft.glyph_name, "star-symbolic")
        self.assertAlmostEqual(self.editor.size_slider.adjustment.get_value(), 150)

    def test_choosing_an_accent_style_selects_the_accent_mode(self):
        self.save("Accent", {"color": None, "accent": True, "size": 1.0, "offset_x": 0.0, "offset_y": 0.0, "tint": True})
        self.editor.styles_list.get_first_child().get_child().get_first_child().emit("clicked")
        self.assertEqual(self.editor.draft.color_mode, "accent")
        self.assertEqual(self.editor.color_palette.state(), ("accent", None))

    def test_an_unknown_style_name_changes_nothing(self):
        before = self.editor.draft
        self.window._apply_style("nothing")
        self.assertEqual(self.editor.draft, before)

    def test_deleting_a_style_updates_the_list_and_the_store(self):
        self.save("Blue")
        self.save("Calm")
        self.editor.styles_list.get_first_child().get_child().get_first_child().get_next_sibling().emit("clicked")
        self.assertEqual(self.rows(), ["Calm"])
        self.assertEqual([style.name for style in self.service.styles()], ["Calm"])

    def test_naming_the_current_appearance_saves_it(self):
        self.editor.size_slider.adjustment.set_value(150)
        self.window._style_named(FakeAnswer("save"), None, "  Big  ", self.editor.draft.appearance())
        self.assertEqual([style.name for style in self.service.styles()], ["Big"])
        self.assertEqual(self.service.styles()[0].appearance["size"], 1.5)
        self.assertEqual(self.rows(), ["Big"])

    def test_cancelling_the_name_dialog_saves_nothing(self):
        self.window._style_named(FakeAnswer("cancel"), None, "Big", self.editor.draft.appearance())
        self.assertEqual(self.service.styles(), [])

    def test_an_empty_name_is_reported(self):
        self.window._style_named(FakeAnswer("save"), None, "   ", self.editor.draft.appearance())
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.assertEqual(self.service.styles(), [])

    def test_the_save_button_opens_the_naming_dialog(self):
        self.editor.on_save_style()
        settle()
        dialog = self.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.get_extra_child().get_max_length(), 40)

    def test_the_menu_offers_export_and_import(self):
        self.assertIsNotNone(self.window.lookup_action("export-styles"))
        self.assertIsNotNone(self.window.lookup_action("import-styles"))

    def test_exporting_without_styles_only_informs(self):
        with mock.patch.object(self.window, "toast") as toast:
            self.window._export_styles()
        self.assertIn("no styles", toast.call_args.args[0])
        self.assertIsNone(self.window.get_visible_dialog())

    def test_exporting_writes_the_file_and_reports_the_count(self):
        self.save("Blue")
        target = self.root / "styles.json"
        with mock.patch.object(self.window, "toast") as toast:
            self.window._styles_export_chosen(FakeSaveDialog(str(target)), None)
        self.assertEqual(json.loads(target.read_text())["styles"][0]["name"], "Blue")
        self.assertIn("1", toast.call_args.args[0])

    def test_an_export_failure_is_reported(self):
        self.save("Blue")
        self.window._styles_export_chosen(FakeSaveDialog(str(self.root / "missing" / "styles.json")), None)
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_importing_adds_the_styles_and_reports_ignored_ones(self):
        source = self.root / "in.json"
        source.write_text(json.dumps({"version": 1, "styles": [{"name": "Blue", "appearance": self.BLUE}, {"name": "", "appearance": {}}]}))
        with mock.patch.object(self.window, "toast") as toast:
            self.window._styles_import_chosen(FakeSaveDialog(str(source)), None)
        self.assertEqual(self.rows(), ["Blue"])
        self.assertIn("ignored", toast.call_args.args[0])

    def test_importing_a_wrong_file_is_reported(self):
        source = self.root / "in.json"
        source.write_text("not json")
        self.window._styles_import_chosen(FakeSaveDialog(str(source)), None)
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.assertEqual(self.rows(), [])


class ListSearchTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        for name in ("Musique", "Éléphant", "projects"):
            self.add_and_apply(name)
        self.window._start_search()
        settle()

    def visible(self):
        return sorted(row.key.rsplit("/", 1)[-1] for row in self.window._rows.values() if row.get_child_visible())

    def type(self, text):
        self.window.search_entry.set_text(text)
        settle(lambda: False, timeout=0.4)

    def test_every_folder_is_listed_without_a_query(self):
        self.assertEqual(self.visible(), ["Musique", "projects", "Éléphant"])

    def test_the_query_filters_by_name(self):
        self.type("mus")
        self.assertEqual(self.visible(), ["Musique"])

    def test_accents_and_case_do_not_matter(self):
        self.type("ELEPHANT")
        self.assertEqual(self.visible(), ["Éléphant"])

    def test_the_path_is_searched_too(self):
        self.type("folders")
        self.assertEqual(len(self.visible()), 3)

    def test_a_query_without_result_hides_every_row(self):
        self.type("zzz")
        self.assertEqual(self.visible(), [])

    def test_leaving_the_search_restores_the_list(self):
        self.type("mus")
        self.window.search_bar.set_search_mode(False)
        settle()
        self.assertEqual(self.window.search_entry.get_text(), "")
        self.assertEqual(len(self.visible()), 3)

    def test_the_shortcut_action_opens_the_search_bar(self):
        self.window.search_bar.set_search_mode(False)
        settle()
        self.window.lookup_action("search").activate(None)
        settle()
        self.assertTrue(self.window.search_bar.get_search_mode())

    def test_the_header_toggle_is_linked_to_the_search_bar(self):
        self.window.search_bar.set_search_mode(False)
        self.assertTrue(self.window.search_bar.get_search_mode() is False)
        self.window._start_search()
        self.assertTrue(self.window.search_bar.get_search_mode())


class LocateFolderTests(WindowTestCase):
    def moved(self):
        folder = self.add_and_apply("docs")
        old = str(folder.resolve())
        target = self.workspace / "renamed"
        folder.rename(target)
        self.created.append(target)
        self.window.reload(select=old)
        settle()
        return old, target

    def test_a_missing_folder_offers_to_find_it(self):
        old, _target = self.moved()
        self.assertTrue(self.window.editor.banner.get_revealed())
        self.assertEqual(self.window.editor.banner.get_button_label(), "Find the Folder…")

    def test_the_banner_button_opens_the_folder_chooser_for_a_missing_folder(self):
        old, _target = self.moved()
        with mock.patch.object(self.window, "_choose_new_location") as choose, mock.patch.object(self.window, "_apply") as apply:
            self.window._on_banner_action()
        choose.assert_called_once()
        apply.assert_not_called()

    def test_the_banner_button_applies_again_for_other_states(self):
        folder = self.add_and_apply("docs")
        write_custom_icon(folder, USER_ICON)
        self.window.reload(select=str(folder.resolve()))
        settle()
        with mock.patch.object(self.window, "_choose_new_location") as choose, mock.patch.object(self.window, "_apply") as apply:
            self.window._on_banner_action()
        apply.assert_called_once()
        choose.assert_not_called()

    def test_choosing_the_new_place_reattaches_the_icon(self):
        old, target = self.moved()
        with mock.patch.object(self.window, "toast") as toast:
            self.window._new_location_chosen(FakeFolderDialog(str(target)), None, old)
        settle()
        self.assertIsNone(self.service.store.get(old))
        entry = self.service.store.get(str(target.resolve()))
        self.assertEqual(read_custom_icon(target), self.service.store.icon_uri(entry.digest))
        self.assertEqual(self.window._current_key, entry.folder)
        self.assertIn("renamed", toast.call_args.args[0])

    def test_a_refused_folder_is_reported_and_nothing_changes(self):
        old, _target = self.moved()
        taken = self.add_and_apply("taken")
        self.window._new_location_chosen(FakeFolderDialog(str(taken)), None, old)
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.window.get_visible_dialog().force_close()
        settle()
        self.window._new_location_chosen(FakeFolderDialog(str(self.workspace / "missing")), None, old)
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.assertIsNotNone(self.service.store.get(old))

    def test_a_portal_path_without_access_opens_the_permission_help(self):
        old, _target = self.moved()
        with mock.patch.object(window_module.environment, "home_access", return_value=False):
            self.window._new_location_chosen(FakeFolderDialog("/run/user/1000/doc/abcd1234/x"), None, old)
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog().get_extra_child())


class ResetAllUiTests(WindowTestCase):
    def test_without_folders_it_only_informs(self):
        with mock.patch.object(self.window, "toast") as toast:
            self.window._confirm_reset_all()
        self.assertIn("no customized", toast.call_args.args[0])
        self.assertIsNone(self.window.get_visible_dialog())

    def test_it_asks_for_confirmation(self):
        self.add_and_apply()
        self.window._confirm_reset_all()
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.assertEqual(len(self.service.store.all()), 1)

    def test_cancelling_removes_nothing(self):
        self.add_and_apply()
        self.window._reset_all_confirmed(FakeAnswer("cancel"), None)
        self.assertEqual(len(self.service.store.all()), 1)

    def test_confirming_removes_every_icon_and_shows_a_report(self):
        first, second = self.add_and_apply("a"), self.add_and_apply("b")
        self.window._reset_all_confirmed(FakeAnswer("remove"), None)
        settle()
        self.assertEqual(self.service.store.all(), {})
        self.assertIsNone(read_custom_icon(first))
        self.assertIsNone(read_custom_icon(second))
        self.assertEqual(self.window.stack.get_visible_child_name(), "empty")
        self.assertIsNotNone(self.window.get_visible_dialog())
        self.assertIn("F5", self.window.get_visible_dialog().get_body())

    def test_a_pending_folder_is_dropped(self):
        self.add_and_apply()
        self.window.add_folder(self.make_folder("new"))
        self.window._reset_all_confirmed(FakeAnswer("remove"), None)
        self.assertIsNone(self.window._pending)

    def test_a_registry_error_is_reported(self):
        self.add_and_apply()
        with mock.patch.object(self.service, "reset_all", side_effect=StoreError("unreadable")):
            self.window._reset_all_confirmed(FakeAnswer("remove"), None)
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_the_menu_offers_the_action(self):
        self.assertIsNotNone(self.window.lookup_action("reset-all"))


class UndoRemovalTests(WindowTestCase):
    def test_removing_offers_to_undo_and_undoing_restores_the_icon(self):
        folder = self.add_and_apply()
        key = str(folder.resolve())
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove(key, "docs")
        title, button, callback = toast.call_args.args
        self.assertEqual(button, "Undo")
        self.assertIsNone(read_custom_icon(folder))
        callback()
        settle()
        entry = self.service.store.get(key)
        self.assertEqual(read_custom_icon(folder), self.service.store.icon_uri(entry.digest))
        self.assertEqual(self.window._current_key, key)

    def test_no_undo_is_offered_when_the_icon_was_changed_from_nautilus(self):
        folder = self.add_and_apply()
        write_custom_icon(folder, USER_ICON)
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove(str(folder.resolve()), "docs")
        self.assertEqual(len(toast.call_args.args), 1)

    def test_a_failed_undo_is_reported(self):
        folder = self.add_and_apply()
        with mock.patch.object(self.window, "toast") as toast:
            self.window.remove(str(folder.resolve()), "docs")
        callback = toast.call_args.args[2]
        with mock.patch.object(self.service, "restore", side_effect=MetadataError("no daemon")):
            callback()
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_the_toast_button_calls_the_callback(self):
        calls = []
        with mock.patch.object(self.window.toasts, "add_toast") as add:
            self.window.toast("Hello", "Undo", lambda: calls.append(1))
        toast = add.call_args.args[0]
        self.assertEqual(toast.get_button_label(), "Undo")
        toast.emit("button-clicked")
        self.assertEqual(calls, [1])

    def test_a_toast_without_a_callback_has_no_handler(self):
        with mock.patch.object(self.window.toasts, "add_toast") as add:
            self.window.toast("Hello", "Got It")
        add.call_args.args[0].emit("button-clicked")


class StartupCleanupTests(WindowTestCase):
    def test_orphan_images_are_removed_when_the_window_opens(self):
        from custicon.ui.window import MainWindow

        self.add_and_apply()
        orphan = self.service.store.save_icon("<svg>orphan</svg>")
        stray = self.service.store.icons_dir / "partial.svg.tmp"
        stray.write_text("x")
        window = MainWindow(shared_application(), self.service)
        self.addCleanup(window.destroy)
        self.assertFalse(self.service.store.icon_path(orphan).exists())
        self.assertFalse(stray.exists())


class FrenchSearchTests(WindowTestCase):
    def dialog(self):
        from custicon.themes import IconTheme
        from custicon.ui.picker import PickerDialog

        themes = self.root / "themes"
        glyphs = {name: glyph_svg() for name in ("audio-x-generic-symbolic", "user-home-symbolic", "starred-symbolic", "camera-photo-symbolic")}
        make_theme(themes, "Fr", folder=folder_svg(), glyphs=glyphs)
        return PickerDialog(IconTheme("Fr", [str(themes)]), None, lambda name: None)

    def search(self, dialog, text, expected):
        dialog.search.set_text(text)
        settle(lambda: dialog._names == expected)
        return dialog._names

    def test_a_french_word_finds_english_named_pictograms(self):
        dialog = self.dialog()
        self.assertEqual(self.search(dialog, "musique", ["audio-x-generic-symbolic"]), ["audio-x-generic-symbolic"])

    def test_accents_do_not_matter(self):
        dialog = self.dialog()
        self.assertEqual(self.search(dialog, "Étoile", ["starred-symbolic"]), ["starred-symbolic"])

    def test_english_search_still_works_and_comes_first(self):
        dialog = self.dialog()
        self.assertEqual(self.search(dialog, "camera", ["camera-photo-symbolic"]), ["camera-photo-symbolic"])

    def test_direct_matches_are_listed_before_translated_ones(self):
        dialog = self.dialog()
        names = self.search(dialog, "photo", ["camera-photo-symbolic"])
        self.assertEqual(names, ["camera-photo-symbolic"])

    def test_the_placeholder_mentions_both_languages(self):
        self.assertIn("French", self.dialog().search.get_placeholder_text())

    def test_the_result_list_has_no_duplicates(self):
        dialog = self.dialog()
        dialog.search.set_text("ma")
        settle(lambda: "user-home-symbolic" in dialog._names)
        self.assertEqual(len(dialog._names), len(set(dialog._names)))


class OpenFilesTests(WindowTestCase):
    def test_a_folder_opens_in_the_editor(self):
        folder = self.make_folder()
        self.window.open_files([Gio.File.new_for_path(str(folder))])
        settle()
        self.assertEqual(self.window.editor.draft.folder, folder)
        self.assertEqual(self.window._pending.folder, folder)

    def test_nothing_happens_without_files(self):
        self.window.open_files([])
        self.assertEqual(self.service.store.all(), {})
        self.assertIsNone(self.window._pending)

    def test_a_file_is_refused_with_an_explanation(self):
        path = self.root / "note.txt"
        path.write_text("x")
        self.window.open_files([Gio.File.new_for_path(str(path))])
        settle()
        self.assertIsNone(self.window._pending)
        self.assertIsNotNone(self.window.get_visible_dialog())

    def test_several_folders_are_opened_as_a_batch(self):
        first, second = self.make_folder("a"), self.make_folder("b")
        self.window.open_files([Gio.File.new_for_path(str(first)), Gio.File.new_for_path(str(second))])
        settle()
        self.assertEqual(self.window._batch, [first.resolve(), second.resolve()])
        self.assertIsNone(self.window._pending)

    def test_an_already_customized_folder_is_selected(self):
        folder = self.add_and_apply()
        self.window.open_files([Gio.File.new_for_path(str(folder))])
        settle()
        self.assertIsNone(self.window._pending)
        self.assertEqual(self.window._current_key, str(folder.resolve()))

    def test_a_portal_path_without_access_opens_the_permission_help(self):
        with mock.patch.object(window_module.environment, "home_access", return_value=False):
            self.window.open_files([Gio.File.new_for_path("/run/user/1000/doc/abcd1234/Projects")])
        settle()
        self.assertIsNotNone(self.window.get_visible_dialog().get_extra_child())

    def test_dropping_a_folder_opens_it(self):
        from gi.repository import Gdk

        folder = self.make_folder()
        dropped = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(folder))])
        self.assertTrue(self.window._on_files_dropped(None, dropped, 0, 0))
        settle()
        self.assertEqual(self.window._pending.folder, folder)

    def test_the_window_accepts_file_drops(self):
        from gi.repository import Gdk

        controllers = self.window.observe_controllers()
        drops = [controllers.get_item(index) for index in range(controllers.get_n_items()) if isinstance(controllers.get_item(index), Gtk.DropTarget)]
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0].get_actions(), Gdk.DragAction.COPY)


class AboutAndHelpTests(WindowTestCase):
    def application(self):
        return ApplicationOpenTests.application(self)

    def menu_actions(self):
        menu = self.window._primary_menu()
        return [menu.get_item_attribute_value(index, "action", None).get_string() for index in range(menu.get_n_items())]

    def test_the_about_dialog_is_complete(self):
        from custicon.config import APP_ID, APP_NAME, VERSION
        from custicon.ui.about import build_about_dialog

        dialog = build_about_dialog(["en_US", "en"], [])
        self.assertEqual(dialog.get_application_name(), APP_NAME)
        self.assertEqual(dialog.get_application_icon(), APP_ID)
        self.assertEqual(dialog.get_version(), VERSION)
        self.assertEqual(dialog.get_developer_name(), "Gor3pig")
        self.assertEqual(dialog.get_license_type(), Gtk.License.GPL_3_0)
        self.assertEqual(dialog.get_copyright(), "© 2026 Gor3pig")
        self.assertEqual(dialog.get_website(), "https://gor3pig.github.io/Custicon/")
        self.assertEqual(dialog.get_issue_url(), "https://github.com/Gor3pig/Custicon/issues")
        self.assertEqual(dialog.get_support_url(), "https://github.com/Gor3pig/Custicon/blob/main/help/en/README.md")
        self.assertTrue(dialog.get_comments())
        self.assertTrue(dialog.get_translator_credits())

    def test_the_legal_sections_credit_every_bundled_set(self):
        from custicon.ui.about import legal_sections

        sections = legal_sections()
        self.assertEqual([title for title, _copyright, _kind, _text in sections], ["Phosphor Icons", "Fluent Emoji", "Simple Icons", "Unicode CLDR"])
        by_title = {title: (copyright, kind, text) for title, copyright, kind, text in sections}
        self.assertEqual(by_title["Phosphor Icons"][1], Gtk.License.MIT_X11)
        self.assertEqual(by_title["Fluent Emoji"], ("© Microsoft Corporation", Gtk.License.MIT_X11, None))
        self.assertEqual(by_title["Simple Icons"][1], Gtk.License.CUSTOM)
        self.assertIn("CC0", by_title["Simple Icons"][2])
        self.assertIn("trademarks", by_title["Simple Icons"][2])
        self.assertEqual(by_title["Unicode CLDR"][1], Gtk.License.CUSTOM)
        self.assertIn("UNICODE LICENSE V3", by_title["Unicode CLDR"][2])
        self.assertIn("Unicode, Inc.", by_title["Unicode CLDR"][0])

    def test_a_missing_license_file_does_not_break_the_dialog(self):
        from pathlib import Path
        from unittest import mock

        from custicon.ui import about as about_module

        with mock.patch.object(about_module, "CLDR_LICENSE", Path("/nonexistent/LICENSE-CLDR")):
            sections = about_module.legal_sections()
        cldr = {title: (copyright_line, text) for title, copyright_line, _kind, text in sections}["Unicode CLDR"]
        self.assertEqual(cldr[0], "© Unicode, Inc.")
        self.assertTrue(cldr[1])

    def test_the_about_dialog_shows_the_release_notes_of_this_version(self):
        from custicon.config import VERSION
        from custicon.ui.about import build_about_dialog

        dialog = build_about_dialog(["en_US", "en"], [])
        self.assertEqual(dialog.get_release_notes_version(), VERSION)
        self.assertIn("<li>", dialog.get_release_notes())

    def test_the_about_dialog_points_to_the_home_page_in_the_desktop_language(self):
        from custicon.ui.about import build_about_dialog

        self.assertEqual(build_about_dialog(["fr_FR.UTF-8", "fr"], []).get_website(), "https://gor3pig.github.io/Custicon/fr/")

    def test_the_about_dialog_points_to_the_guide_in_the_desktop_language(self):
        from custicon.ui.about import build_about_dialog

        self.assertTrue(build_about_dialog(["fr_FR.UTF-8", "fr"], []).get_support_url().endswith("/help/fr/README.md"))

    def test_the_about_action_presents_the_dialog_over_the_window(self):
        application = self.application()
        application.open([Gio.File.new_for_path(str(self.make_folder()))], "")
        settle()
        application.activate_action("about", None)
        settle()
        window = application.props.active_window
        self.assertIsInstance(window.get_visible_dialog(), Adw.AboutDialog)

    def test_the_help_action_opens_the_guide(self):
        from unittest import mock

        from custicon.ui import about as about_module

        application = self.application()
        application.activate()
        settle()
        with mock.patch.object(about_module.Gtk, "UriLauncher") as launcher:
            application.activate_action("help", None)
        launcher.new.assert_called_once()
        self.assertIn("/help/", launcher.new.call_args.args[0])
        launcher.new.return_value.launch.assert_called_once()

    def test_f1_opens_the_help(self):
        application = self.application()
        self.assertEqual(application.get_accels_for_action("app.help"), ["F1"])

    def test_the_main_menu_lists_help_before_about(self):
        actions = self.menu_actions()
        self.assertIn("app.help", actions)
        self.assertLess(actions.index("app.help"), actions.index("app.about"))


class ApplicationOpenTests(WindowTestCase):
    def application(self):
        from custicon.app import Application

        if not _OPEN_APPLICATION:
            application = Application(self.service, unique=False)
            application.register(None)
            _OPEN_APPLICATION.append(application)
        application = _OPEN_APPLICATION[0]
        application._service = self.service
        self.addCleanup(lambda: [window.destroy() for window in application.get_windows()])
        return application

    def test_the_application_handles_files(self):
        from custicon.app import Application

        for unique in (True, False):
            self.assertTrue(Application(self.service, unique=unique).get_flags() & Gio.ApplicationFlags.HANDLES_OPEN)

    def test_opening_a_folder_creates_the_window_and_selects_the_folder(self):
        application = self.application()
        folder = self.make_folder()
        application.open([Gio.File.new_for_path(str(folder))], "")
        settle()
        window = application.props.active_window
        self.assertIsNotNone(window)
        self.assertEqual(window._pending.folder, folder)

    def test_a_second_request_reuses_the_same_window(self):
        application = self.application()
        first, second = self.make_folder("a"), self.make_folder("b")
        application.open([Gio.File.new_for_path(str(first))], "")
        settle()
        window = application.props.active_window
        application.open([Gio.File.new_for_path(str(second))], "")
        settle()
        self.assertIs(application.props.active_window, window)
        self.assertEqual(len(application.get_windows()), 1)
        self.assertEqual(window._pending.folder, second)

    def test_activating_without_files_still_opens_the_window(self):
        application = self.application()
        application.activate()
        settle()
        self.assertIsNotNone(application.props.active_window)


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
        self.window._folders_chosen(FakeFoldersDialog(os.fsdecode(bad)), None)
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
        self.apply_with("first", color_mode="custom", color="#ff8800", size=1.5, offset_x=0.1, offset_y=-0.1, tint=False)
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
        picture = self.window._rows[str(folder)].get_child().get_first_child().get_next_sibling()
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
        self.assertEqual(dialog.get_extra_child().get_label(), "flatpak override --user --filesystem=home io.github.gor3pig.Custicon")

    def test_a_portal_path_without_access_opens_the_same_help(self):
        window = self.make_window(False)
        with mock.patch.object(window_module.environment, "home_access", return_value=False):
            window._folders_chosen(FakeFoldersDialog("/run/user/1000/doc/abcd1234/Projects"), None)
        settle()
        self.assertIsNotNone(window.get_visible_dialog().get_extra_child())
        self.assertIsNone(window._pending)

    def test_a_portal_path_with_access_keeps_the_plain_alert(self):
        with mock.patch.object(window_module.environment, "home_access", return_value=True):
            self.window._folders_chosen(FakeFoldersDialog("/run/user/1000/doc/abcd1234/Projects"), None)
        settle()
        self.assertIsNone(self.window.get_visible_dialog().get_extra_child())


class BadgeUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        settle()

    def test_the_badge_is_off_by_default_with_its_settings_hidden(self):
        self.assertFalse(self.editor.badge_switch.get_active())
        self.assertFalse(self.editor.badge_details.get_visible())
        self.assertFalse(self.editor.draft.badge_active)
        self.assertNotIn("badge_glyph", self.editor.draft.to_settings().to_dict())

    def test_turning_it_on_shows_the_settings_and_picks_a_default_pictogram(self):
        self.editor.badge_switch.set_active(True)
        settle()
        self.assertTrue(self.editor.badge_details.get_visible())
        self.assertTrue(self.editor.draft.badge_active)
        self.assertEqual(self.editor.draft.badge_glyph, "phosphor:check")
        self.assertEqual(self.editor.draft.to_settings().badge_corner, "br")

    def test_the_corner_color_and_size_reach_the_settings(self):
        self.editor.badge_switch.set_active(True)
        self.editor.badge_corner_buttons["tl"].set_active(True)
        self.editor.badge_palette.choose_color("#3a944a")
        self.editor.badge_size_slider.adjustment.set_value(40)
        settle()
        settings = self.editor.draft.to_settings()
        self.assertEqual((settings.badge_corner, settings.badge_color), ("tl", "#3a944a"))
        self.assertAlmostEqual(settings.badge_size, 0.4)

    def test_only_one_corner_is_selected_at_a_time(self):
        self.editor.badge_switch.set_active(True)
        for corner in ("tr", "bl"):
            self.editor.badge_corner_buttons[corner].set_active(True)
            active = [name for name, button in self.editor.badge_corner_buttons.items() if button.get_active()]
            self.assertEqual(active, [corner])

    def test_choosing_a_pictogram_turns_the_badge_on(self):
        self.window._on_badge_glyph_chosen("heart-symbolic")
        settle()
        self.assertTrue(self.editor.badge_switch.get_active())
        self.assertEqual(self.editor.draft.badge_glyph, "heart-symbolic")
        self.assertIn("heart", self.editor.badge_glyph_label.get_text().lower())

    def test_the_central_pictogram_is_not_touched_by_the_badge(self):
        self.window._on_badge_glyph_chosen("heart-symbolic")
        settle()
        self.assertEqual(self.editor.draft.glyph_name, "star-symbolic")

    def test_turning_it_off_keeps_the_choices_for_later(self):
        self.window._on_badge_glyph_chosen("heart-symbolic")
        self.editor.badge_corner_buttons["bl"].set_active(True)
        self.editor.badge_switch.set_active(False)
        settle()
        self.assertFalse(self.editor.draft.badge_active)
        self.assertNotIn("badge_glyph", self.editor.draft.to_settings().to_dict())
        self.editor.badge_switch.set_active(True)
        settle()
        self.assertEqual((self.editor.draft.badge_glyph, self.editor.draft.badge_corner), ("heart-symbolic", "bl"))

    def test_applying_stores_the_badge_and_reselecting_the_folder_shows_it(self):
        self.window._on_badge_glyph_chosen("heart-symbolic")
        self.editor.badge_corner_buttons["tr"].set_active(True)
        settle()
        self.window._apply()
        settle()
        key = self.row_keys()[0]
        stored = Settings.from_dict(self.service.store.get(key).settings)
        self.assertEqual((stored.badge_glyph, stored.badge_corner), ("heart-symbolic", "tr"))
        self.assertIn("<circle", self.service.store.icon_path(self.service.store.get(key).digest).read_text())
        other = self.make_folder("other")
        self.window.add_folder(other)
        settle()
        self.assertFalse(self.editor.badge_switch.get_active())
        self.window.reload(select=key)
        settle()
        self.assertTrue(self.editor.badge_switch.get_active())
        self.assertEqual(self.editor.draft.badge_corner, "tr")
        self.assertTrue(self.editor.badge_corner_buttons["tr"].get_active())

    def test_a_folder_without_badge_stays_free_of_badge_settings(self):
        self.window._apply()
        settle()
        key = self.row_keys()[0]
        self.assertNotIn("badge", json.dumps(self.service.store.get(key).settings))

    def test_resetting_the_appearance_leaves_the_badge_alone(self):
        self.window._on_badge_glyph_chosen("heart-symbolic")
        settle()
        self.editor._on_reset_appearance(None)
        settle()
        self.assertTrue(self.editor.draft.badge_active)

    def test_the_badge_is_applied_to_every_folder_of_a_batch(self):
        folders = [self.make_folder(name) for name in ("one", "two")]
        self.window.add_folders(folders)
        settle()
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.window._on_badge_glyph_chosen("heart-symbolic")
        settle()
        self.window._apply()
        settle()
        for folder in folders:
            stored = self.service.store.get(str(folder.resolve()))
            self.assertEqual(Settings.from_dict(stored.settings).badge_glyph, "heart-symbolic")

    def test_regeneration_keeps_the_badge(self):
        self.window._on_badge_glyph_chosen("heart-symbolic")
        settle()
        self.window._apply()
        settle()
        key = self.row_keys()[0]
        self.service.regenerate_all()
        self.assertIn("<circle", self.service.store.icon_path(self.service.store.get(key).digest).read_text())

    def test_a_missing_badge_pictogram_blocks_the_apply_button_with_a_message(self):
        self.editor.update_glyph(replace(self.editor.draft, badge_on=True, badge_glyph="missing-symbolic"))
        settle(lambda: not self.editor.apply_button.get_sensitive())
        self.assertFalse(self.editor.apply_button.get_sensitive())

    def test_the_new_card_does_not_push_the_editor_out_of_the_window(self):
        self.window.set_default_size(960, 680)
        settle()
        self.assertLessEqual(self.editor.columns.get_width(), self.editor.scrolled.get_width())


class BackupUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.target = self.root / "backup.json"

    def customize(self, name="docs"):
        return self.add_and_apply(name)

    def dialog_text(self):
        dialog = self.window.get_visible_dialog()
        self.assertIsNotNone(dialog)
        return dialog.get_heading(), dialog.get_body()

    def test_the_menu_offers_both_actions(self):
        self.assertIsNotNone(self.window.lookup_action("backup"))
        self.assertIsNotNone(self.window.lookup_action("restore"))

    def test_saving_writes_the_file_and_reports_what_it_holds(self):
        self.customize()
        self.window._backup_save_chosen(FakeSaveDialog(str(self.target)), None)
        settle()
        heading, body = self.dialog_text()
        self.assertEqual(heading, "Settings Saved")
        self.assertIn("1 customized folder", body)
        self.assertIn("never images", body)
        self.assertTrue(self.target.is_file())

    def test_cancelling_the_save_dialog_does_nothing(self):
        self.window._backup_save_chosen(FakeFailingDialog(), None)
        settle()
        self.assertFalse(self.target.exists())
        self.assertIsNone(self.window.get_visible_dialog())

    def test_a_destination_that_is_not_on_this_computer_is_refused(self):
        self.window._backup_save_chosen(FakeRemoteDialog(), None)
        settle()
        heading, body = self.dialog_text()
        self.assertEqual(heading, "Could Not Save the Settings")
        self.assertIn("on this computer", body)

    def test_a_file_that_is_not_on_this_computer_is_refused(self):
        self.window._backup_open_chosen(FakeRemoteDialog(), None)
        settle()
        heading, body = self.dialog_text()
        self.assertEqual(heading, "Could Not Restore the Settings")
        self.assertIn("on this computer", body)

    def test_cancelling_the_open_dialog_does_nothing(self):
        self.window._backup_open_chosen(FakeFailingDialog(), None)
        settle()
        self.assertIsNone(self.window.get_visible_dialog())

    def test_an_unwritable_destination_shows_an_error(self):
        self.window._backup_save_chosen(FakeSaveDialog(str(self.root / "absent" / "backup.json")), None)
        settle()
        self.assertEqual(self.dialog_text()[0], "Could Not Save the Settings")

    def test_choosing_a_file_asks_for_confirmation_with_the_folder_count(self):
        self.customize()
        self.service.export_backup(self.target)
        self.window._backup_open_chosen(FakeSaveDialog(str(self.target)), None)
        settle()
        heading, body = self.dialog_text()
        self.assertEqual(heading, "Restore the Settings?")
        self.assertIn("The icon of 1 folder", body)

    def test_a_file_that_is_not_a_backup_is_refused_before_asking(self):
        self.target.write_text("nonsense")
        self.window._backup_open_chosen(FakeSaveDialog(str(self.target)), None)
        settle()
        self.assertEqual(self.dialog_text()[0], "Could Not Restore the Settings")

    def test_cancelling_the_confirmation_changes_nothing(self):
        folder = self.customize()
        self.service.export_backup(self.target)
        self.service.reset(folder)
        self.window.reload()
        self.window._restore_confirmed(FakeAnswer("cancel"), None, str(self.target))
        settle()
        self.assertIsNone(self.service.store.get(str(folder.resolve())))

    def test_confirming_restores_the_icons_and_reports(self):
        folder = self.customize()
        self.service.export_backup(self.target)
        self.service.reset(folder)
        self.window.reload()
        settle()
        self.window._restore_confirmed(FakeAnswer("restore"), None, str(self.target))
        settle()
        self.assertIsNotNone(self.service.store.get(str(folder.resolve())))
        heading, body = self.dialog_text()
        self.assertEqual(heading, "Restoration Finished")
        self.assertIn("1 icon restored", body)
        self.assertIn("Press F5", body)
        self.assertIn(str(folder.resolve()), self.row_keys())

    def test_the_report_lists_missing_and_failed_folders(self):
        folder = self.customize()
        self.service.export_backup(self.target)
        document = json.loads(self.target.read_text())
        document["folders"].append({"folder": str(self.workspace / "gone"), "settings": {"glyph_name": "star-symbolic"}})
        document["folders"].append({"folder": str(folder), "settings": {"glyph_name": "star-symbolic", "size": 99}})
        self.target.write_text(json.dumps(document))
        self.window.restore_from(str(self.target))
        settle()
        body = self.dialog_text()[1]
        self.assertIn("1 folder not found, ignored", body)
        self.assertIn("1 icon could not be restored", body)
        self.assertIn(f"{folder.name}:", body)

    def test_an_empty_backup_says_so(self):
        self.target.write_text('{"format": "custicon-backup", "version": 1}')
        self.window.restore_from(str(self.target))
        settle()
        self.assertIn("nothing to restore", self.dialog_text()[1])
