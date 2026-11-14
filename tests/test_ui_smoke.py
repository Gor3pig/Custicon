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
        self.editor.color_row.set_selected(2)
        self.assertFalse(self.editor.draft.auto_color)
        self.assertTrue(self.editor.swatch.get_sensitive())

    def test_turning_automatic_color_off_keeps_the_displayed_color(self):
        self.editor.set_computed_color("#aa5500")
        shown = hex_from_rgba(self.editor.swatch.get_rgba())
        self.assertEqual(shown, "#aa5500")
        self.editor.color_row.set_selected(2)
        self.assertEqual(self.editor.draft.color, "#aa5500")
        self.assertEqual(hex_from_rgba(self.editor.swatch.get_rgba()), shown)
        self.assertEqual(self.editor.draft.to_settings().color, shown)

    def test_reset_returns_to_the_default_appearance(self):
        self.editor.size_slider.adjustment.set_value(200)
        self.editor.color_row.set_selected(2)
        self.assertTrue(self.editor.reset_button.get_sensitive())
        self.editor.reset_button.emit("clicked")
        self.assertTrue(self.editor.draft.has_default_appearance())
        self.assertEqual(self.editor.size_slider.adjustment.get_value(), 100)
        self.assertEqual(self.editor.color_row.get_selected(), 0)
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
            self.assertEqual(self.window.editor.system_row.get_subtitle(), expected)

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


class ColorModeUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor

    def test_the_color_row_offers_three_modes(self):
        self.assertEqual(self.editor.color_row.get_model().get_n_items(), 3)
        self.assertEqual(self.editor.color_row.get_selected(), 0)

    def test_choosing_the_system_accent_updates_the_draft_and_locks_the_swatch(self):
        self.editor.color_row.set_selected(1)
        self.assertEqual(self.editor.draft.color_mode, "accent")
        self.assertFalse(self.editor.swatch.get_sensitive())
        self.assertTrue(self.editor.draft.to_settings().accent)

    def test_the_computed_color_is_shown_for_the_accent_mode(self):
        self.editor.color_row.set_selected(1)
        self.editor.set_computed_color("#ff0000")
        self.assertIn("#ff0000", self.editor.color_row.get_subtitle())
        self.assertEqual(hex_from_rgba(self.editor.swatch.get_rgba()), "#ff0000")

    def test_the_custom_mode_unlocks_the_swatch_and_keeps_its_color(self):
        self.editor.set_computed_color("#aa5500")
        self.editor.color_row.set_selected(2)
        self.assertTrue(self.editor.swatch.get_sensitive())
        self.assertEqual(self.editor.draft.color, "#aa5500")
        self.editor.set_computed_color("#00ff00")
        self.assertEqual(hex_from_rgba(self.editor.swatch.get_rgba()), "#aa5500")

    def test_switching_back_to_automatic_unlocks_nothing(self):
        self.editor.color_row.set_selected(2)
        self.editor.color_row.set_selected(0)
        self.assertEqual(self.editor.draft.color_mode, "auto")
        self.assertFalse(self.editor.swatch.get_sensitive())


class FolderTintUiTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.add_folder(self.make_folder())
        settle()
        self.editor = self.window.editor

    def test_the_folder_tint_row_offers_three_modes_and_starts_on_none(self):
        self.assertEqual(self.editor.folder_tint_row.get_model().get_n_items(), 3)
        self.assertEqual(self.editor.folder_tint_row.get_selected(), 0)
        self.assertFalse(self.editor.folder_tint_swatch.get_sensitive())

    def test_choosing_the_system_accent_updates_the_draft_and_locks_the_swatch(self):
        self.editor.folder_tint_row.set_selected(1)
        self.assertEqual(self.editor.draft.folder_tint_mode, "accent")
        self.assertFalse(self.editor.folder_tint_swatch.get_sensitive())
        self.assertTrue(self.editor.draft.to_settings().folder_tint_accent)

    def test_the_custom_mode_unlocks_the_swatch_and_sends_its_color(self):
        self.editor.folder_tint_row.set_selected(2)
        self.assertTrue(self.editor.folder_tint_swatch.get_sensitive())
        self.assertEqual(self.editor.draft.folder_tint_mode, "custom")
        self.assertEqual(self.editor.draft.to_settings().folder_tint, self.editor.draft.folder_tint)
        self.editor.folder_tint_swatch.set_rgba(rgba_from_hex("#112233"))
        self.assertEqual(self.editor.draft.folder_tint, "#112233")

    def test_going_back_to_none_removes_the_tint(self):
        self.editor.folder_tint_row.set_selected(2)
        self.editor.folder_tint_row.set_selected(0)
        self.assertEqual(self.editor.draft.folder_tint_mode, "none")
        self.assertIsNone(self.editor.draft.to_settings().folder_tint)

    def test_the_computed_tint_is_shown_for_the_accent_mode(self):
        self.editor.folder_tint_row.set_selected(1)
        self.editor.set_computed_folder_tint("#ff0000")
        self.assertIn("#ff0000", self.editor.folder_tint_row.get_subtitle())

    def test_the_none_mode_explains_that_the_theme_color_is_kept(self):
        self.assertNotEqual(self.editor.folder_tint_row.get_subtitle(), "")

    def test_reset_removes_the_folder_tint(self):
        self.editor.folder_tint_row.set_selected(2)
        self.assertTrue(self.editor.reset_button.get_sensitive())
        self.editor.reset_button.emit("clicked")
        self.assertEqual(self.editor.folder_tint_row.get_selected(), 0)
        self.assertEqual(self.editor.draft.folder_tint_mode, "none")

    def test_the_preview_shows_the_tinted_folder_even_without_a_pictogram(self):
        self.assertFalse(self.editor.draft.ready)
        self.editor.folder_tint_row.set_selected(2)
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
            self.editor.folder_tint_row.set_selected(2)
            self.window._render_preview()
            self.assertTrue(tinted.called)
            self.assertFalse(plain.called)

    def test_the_preview_reports_the_accent_with_a_pictogram(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.folder_tint_row.set_selected(1)
        self.window._render_preview()
        self.assertIn("#", self.editor.folder_tint_row.get_subtitle())

    def test_applying_stores_the_folder_tint(self):
        folder = self.editor.draft.folder
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.folder_tint_row.set_selected(2)
        self.editor.folder_tint_swatch.set_rgba(rgba_from_hex("#e01b24"))
        self.window._apply()
        settle()
        stored = self.window._service.store.get(str(Path(folder).resolve()))
        self.assertIsNotNone(stored)
        self.assertEqual(stored.settings["folder_tint"], "#e01b24")


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
        self.assertEqual(self.editor.system_row.get_subtitle(), "acorn (Phosphor)")

    def test_the_picker_opened_from_the_window_offers_phosphor(self):
        self.window._choose_glyph()
        settle()
        picker = self.window.get_visible_dialog()
        self.assertIsNotNone(picker)
        self.assertIs(picker._sets, self.service.icon_sets())

    def test_typing_a_text_clears_the_other_sources_in_the_rows(self):
        self.editor.update_glyph(self.editor.draft.with_glyph_name("star-symbolic"))
        self.editor.text_row.set_text("Z")
        self.assertEqual(self.editor.system_row.get_subtitle(), "No pictogram chosen")
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


class FakeAnswer:
    def __init__(self, response):
        self._response = response

    def choose_finish(self, _result):
        return self._response


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
        self.assertEqual(self.editor.color_row.get_selected(), 1)

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

    def test_only_the_first_folder_is_opened_and_the_user_is_told(self):
        first, second = self.make_folder("a"), self.make_folder("b")
        with mock.patch.object(self.window, "toast") as toast:
            self.window.open_files([Gio.File.new_for_path(str(first)), Gio.File.new_for_path(str(second))])
        settle()
        self.assertEqual(self.window._pending.folder, first)
        self.assertIn("one folder at a time", toast.call_args.args[0])

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
