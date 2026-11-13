import os
import time
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from custicon.metadata import read_custom_icon, write_custom_icon
from custicon.service import Service, Settings
from custicon.store import Store

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
        self.assertFalse(self.window.editor.remove_button.get_visible())
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
