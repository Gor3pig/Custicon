from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ..config import APP_ID, APP_NAME
from ..errors import CusticonError
from ..i18n import _
from ..service import (
    RESET_FOLDER_MISSING,
    RESET_KEPT_USER_ICON,
    RESET_UNKNOWN,
    STATUS_FOLDER_MISSING,
    STATUS_ICON_CHANGED,
    STATUS_ICON_FILE_MISSING,
    STATUS_OK,
    RegenerationReport,
    Service,
)
from .draft import Draft
from .editor import BIG_SIZE, SMALL_SIZES, Editor
from .format import display_path, folder_name, is_portal_path, is_text_path
from .messages import banner_action, describe_error, report_lines, status_banner, status_label
from .picker import PickerDialog
from .textures import texture_from_file, texture_from_svg_text

ROW_ICON_SIZE = 48
PREVIEW_DELAY_MS = 50
TOAST_SECONDS = 8
FORGET_STATUSES = (STATUS_FOLDER_MISSING, STATUS_ICON_CHANGED)


class FolderRow(Gtk.ListBoxRow):
    def __init__(self, key: str, name: str, subtitle: str, texture, warning: str | None, pending: bool):
        super().__init__()
        self.key = key
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=False)
        picture.set_size_request(ROW_ICON_SIZE, ROW_ICON_SIZE)
        if texture is not None:
            picture.set_paintable(texture)
        box.append(picture)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=name, xalign=0.0, ellipsize=3)
        title.add_css_class("heading")
        text.append(title)
        line = Gtk.Label(label=warning or subtitle, xalign=0.0, ellipsize=2)
        line.add_css_class("caption")
        line.add_css_class("warning" if warning else "dim-label")
        text.append(line)
        box.append(text)
        self.set_child(box)
        self.set_tooltip_text(key)
        if pending:
            self.add_css_class("pending")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, service: Service):
        super().__init__(application=application, title=APP_NAME, default_width=1000, default_height=720)
        self._service = service
        self._pending: Draft | None = None
        self._current_key: str | None = None
        self._reloading = False
        self._preview_source = 0
        self._regeneration: tuple | None = None
        self._regeneration_source = 0
        self._shown_error: str | None = None
        self._rows: dict[str, FolderRow] = {}

        self._build_actions()
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack()
        self.toasts.set_child(self.stack)
        self.set_content(self.toasts)
        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_main_page(), "main")

        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        self.connect("close-request", self._on_close_request)
        self.connect("notify::is-active", self._on_active_changed)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.connect("notify::gtk-icon-theme-name", self._on_theme_changed)
        self.reload()

    def _build_actions(self) -> None:
        for name, callback in (("add-folder", self._choose_folder), ("regenerate", self._start_regeneration)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_args, cb=callback: cb())
            self.add_action(action)

    def _primary_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        menu.append(_("Regenerate All Icons"), "win.regenerate")
        menu.append(_("About %s") % APP_NAME, "app.about")
        menu.append(_("Quit"), "app.quit")
        return menu

    def _header(self, with_add: bool = True) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        if with_add:
            add = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label=_("Add Folder")))
            add.add_css_class("suggested-action")
            add.set_action_name("win.add-folder")
            add.set_tooltip_text(_("Choose a folder to customize"))
            header.pack_start(add)
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._primary_menu(), primary=True)
        menu.set_tooltip_text(_("Main Menu"))
        header.pack_end(menu)
        return header

    def _build_empty_page(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        view.add_top_bar(self._header())
        page = Adw.StatusPage(
            icon_name=APP_ID,
            title=_("No Customized Folders"),
            description=_("Choose a folder to add a pictogram to it, like the Documents or Music folders have. The content of the folder is never modified."),
        )
        button = Gtk.Button(label=_("Choose a Folder…"), halign=Gtk.Align.CENTER)
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_action_name("win.add-folder")
        page.set_child(button)
        view.set_content(page)
        return view

    def _build_main_page(self) -> Gtk.Widget:
        self.split = Adw.NavigationSplitView(min_sidebar_width=280, max_sidebar_width=380, sidebar_width_fraction=0.3)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        return self.split

    def _build_sidebar(self) -> Adw.NavigationPage:
        view = Adw.ToolbarView()
        sidebar_header = self._header()
        sidebar_header.set_show_title(False)
        view.add_top_bar(sidebar_header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading = Gtk.Label(label=_("Customized Folders"), xalign=0.0, margin_top=10, margin_bottom=4, margin_start=16)
        heading.add_css_class("heading")
        heading.add_css_class("dim-label")
        box.append(heading)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_row_selected)
        self.list.connect("row-activated", self._on_row_activated)
        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(self.list)
        box.append(scrolled)
        view.set_content(box)
        self.regenerate_button = Gtk.Button(child=Adw.ButtonContent(icon_name="view-refresh-symbolic", label=_("Regenerate All Icons")), margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.regenerate_button.set_action_name("win.regenerate")
        view.add_bottom_bar(self.regenerate_button)
        return Adw.NavigationPage.new(view, APP_NAME)

    def _build_content(self) -> Adw.NavigationPage:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        self.editor = Editor()
        self.editor.on_change = self._on_editor_change
        self.editor.on_apply = self._apply
        self.editor.on_remove = self._confirm_remove
        self.editor.on_choose_glyph = self._choose_glyph
        self.editor.on_browse_glyph = self._browse_glyph
        self.editor.on_banner_action = self._apply
        view.set_content(self.editor)
        self.content_page = Adw.NavigationPage.new(view, APP_NAME)
        return self.content_page

    def reload(self, select: str | None = None, keep_editor: bool = False) -> None:
        self._reloading = True
        try:
            self._rebuild_rows()
            if not self._rows:
                self._current_key = None
                self.stack.set_visible_child_name("empty")
                return
            self.stack.set_visible_child_name("main")
            key = select if select in self._rows else (self._current_key if self._current_key in self._rows else next(iter(self._rows)))
            self.list.select_row(self._rows[key])
            self._current_key = key
        finally:
            self._reloading = False
        if not keep_editor:
            self._show_key(key)

    def _rebuild_rows(self) -> None:
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        self._rows.clear()
        scale = self.get_scale_factor()
        items = self._customized()
        if self._pending is not None:
            self._add_row(
                str(self._pending.folder),
                folder_name(self._pending.folder),
                display_path(self._pending.folder),
                self._plain_folder_texture(scale),
                None,
                True,
            )
        for item in items:
            entry = item.entry
            try:
                texture = texture_from_file(self._service.store.icon_path(entry.digest), ROW_ICON_SIZE * scale)
            except CusticonError:
                texture = self._plain_folder_texture(scale)
            self._add_row(entry.folder, folder_name(entry.folder), display_path(entry.folder), texture, status_label(item.status), False)

    def _customized(self) -> list:
        try:
            items = self._service.customized()
        except CusticonError as error:
            self._report_registry_error(error)
            return []
        self._shown_error = None
        return sorted(items, key=lambda item: folder_name(item.entry.folder).lower())

    def _report_registry_error(self, error: CusticonError) -> None:
        message = str(error)
        if message != self._shown_error:
            self._shown_error = message
            self._alert(_("Could Not Read the Saved Data"), describe_error(error))

    def _add_row(self, key, name, subtitle, texture, warning, pending) -> None:
        row = FolderRow(key, name, subtitle, texture, warning, pending)
        self._rows[key] = row
        self.list.append(row)

    def _plain_folder_texture(self, scale: int):
        try:
            return texture_from_file(self._service.folder_file(), ROW_ICON_SIZE * scale)
        except CusticonError:
            return None

    def _on_row_selected(self, _list, row) -> None:
        if self._reloading or row is None:
            return
        self._show_key(row.key)
        self.split.set_show_content(True)

    def _on_row_activated(self, _list, row) -> None:
        if self._reloading or row is None:
            return
        if row.key != self._current_key:
            self._show_key(row.key)
        self.split.set_show_content(True)

    def _entry(self, key: str):
        try:
            return self._service.store.get(key)
        except CusticonError as error:
            self._report_registry_error(error)
            return None

    def _show_key(self, key: str) -> None:
        self._current_key = key
        if self._pending is not None and key == str(self._pending.folder):
            self._load(self._pending, False, STATUS_OK)
            return
        entry = self._entry(key)
        if entry is None:
            return
        self._load(Draft.from_entry(entry), True, self._service.entry_status(entry))

    def _load(self, draft: Draft, existing: bool, status: str) -> None:
        remove_label = _("Forget This Folder") if status in FORGET_STATUSES else (None if existing else _("Cancel"))
        self.content_page.set_title(folder_name(draft.folder))
        self.editor.load(draft, existing, status, status_banner(status), banner_action(status), remove_label)
        self._schedule_preview()

    def _on_editor_change(self, draft: Draft) -> None:
        if self._pending is not None and draft.folder == self._pending.folder:
            self._pending = draft
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        if self._preview_source:
            GLib.source_remove(self._preview_source)
        self._preview_source = GLib.timeout_add(PREVIEW_DELAY_MS, self._render_preview)

    def _render_preview(self) -> bool:
        self._preview_source = 0
        draft = self.editor.draft
        if draft is None:
            return False
        scale = self.get_scale_factor()
        theme = self._service.current_theme()
        note = _("Live preview with the folder icon of the current theme (%s).") % theme
        try:
            if draft.ready:
                built = self._service.build(draft.to_settings(), theme)
                render = lambda size: texture_from_svg_text(built.svg, size * scale)
                self.editor.set_auto_color_text(built.color)
            else:
                folder = self._service.folder_file(theme)
                render = lambda size: texture_from_file(folder, size * scale)
                self.editor.set_auto_color_text(None)
            self.editor.set_preview(render(BIG_SIZE), [render(size) for size in SMALL_SIZES], note)
            self.editor.set_blocked(False)
        except CusticonError as error:
            self.editor.set_preview(None, [None] * len(SMALL_SIZES), describe_error(error).split("\n")[0])
            self.editor.set_blocked(True)
        return False

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title=_("Choose a Folder"))
        dialog.select_folder(self, None, self._folder_chosen)

    def _folder_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None or is_portal_path(path):
            self._alert(_("Folder Not Accessible"), _("Custicon needs the real location of the folder. Choose a folder inside your home folder."))
            return
        if not is_text_path(path):
            self._alert(_("Name Not Supported"), _("This name is not valid UTF-8 text, so Custicon cannot use it."))
            return
        self.add_folder(Path(path))

    def add_folder(self, path: Path) -> None:
        try:
            key = str(path.resolve())
        except (OSError, RuntimeError):
            self._alert(_("Folder Not Accessible"), _("Custicon needs the real location of the folder. Choose a folder inside your home folder."))
            return
        if self._entry(key) is None:
            self._pending = Draft(folder=Path(key))
        self.reload(select=key)
        self.split.set_show_content(True)

    def _choose_glyph(self) -> None:
        draft = self.editor.draft
        if draft is None:
            return
        picker = PickerDialog(self._service.icon_theme(), draft.glyph_name, lambda name: self.editor.update_glyph(self.editor.draft.with_glyph_name(name)))
        picker.present(self)

    def _browse_glyph(self) -> None:
        images = Gtk.FileFilter()
        images.set_name(_("SVG and PNG images"))
        images.add_mime_type("image/svg+xml")
        images.add_mime_type("image/png")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog = Gtk.FileDialog(title=_("Choose a Pictogram File"), filters=filters, default_filter=images)
        dialog.open(self, None, self._glyph_file_chosen)

    def _glyph_file_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if not path or self.editor.draft is None:
            return
        if not is_text_path(path):
            self._alert(_("Name Not Supported"), _("This name is not valid UTF-8 text, so Custicon cannot use it."))
            return
        self.editor.update_glyph(self.editor.draft.with_glyph_file(path))

    def _apply(self) -> None:
        draft = self.editor.draft
        if draft is None:
            return
        try:
            entry = self._service.apply(draft.folder, draft.to_settings())
        except CusticonError as error:
            self._alert(_("Could Not Apply the Icon"), describe_error(error))
            return
        if self._pending is not None and str(self._pending.folder) == entry.folder:
            self._pending = None
        self.toast(_("Icon applied to “%s”. Press F5 in Nautilus to see it.") % folder_name(entry.folder), _("Got It"))
        self.reload(select=entry.folder)

    def _confirm_remove(self) -> None:
        draft = self.editor.draft
        if draft is None or self._current_key is None:
            return
        if self._pending is not None and self._current_key == str(self._pending.folder):
            self._pending = None
            self.reload()
            return
        entry = self._entry(self._current_key)
        status = self._service.entry_status(entry) if entry else STATUS_OK
        name = folder_name(draft.folder)
        if status in FORGET_STATUSES:
            heading = _("Forget “%s”?") % name
            body = _("Custicon will stop managing this folder. Its current icon is not changed.")
            label = _("Forget")
        else:
            heading = _("Remove the Icon of “%s”?") % name
            body = _("The folder will get back the icon it had before Custicon. Its content is not modified.")
            label = _("Remove")
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", label)
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, lambda d, result, key=self._current_key, n=name: self._remove_confirmed(d, result, key, n))

    def _remove_confirmed(self, dialog, result, key: str, name: str) -> None:
        if dialog.choose_finish(result) == "remove":
            self.remove(key, name)

    def remove(self, key: str, name: str) -> None:
        try:
            outcome = self._service.reset(key)
        except CusticonError as error:
            self._alert(_("Could Not Remove the Icon"), describe_error(error))
            return
        if outcome == RESET_UNKNOWN:
            self.toast(_("“%s” was no longer in the list.") % name)
        elif outcome == RESET_KEPT_USER_ICON:
            self.toast(_("“%s”: the icon you chose in Nautilus was kept.") % name)
        elif outcome == RESET_FOLDER_MISSING:
            self.toast(_("“%s” was forgotten.") % name)
        else:
            self.toast(_("Icon removed from “%s”. Press F5 in Nautilus to see it.") % name)
        self.reload()

    def _start_regeneration(self) -> None:
        if self._regeneration is not None:
            return
        try:
            entries = list(self._service.store.all().values())
        except CusticonError as error:
            self._report_registry_error(error)
            return
        if not entries:
            self.toast(_("There are no customized folders to regenerate."))
            return
        self._service.refresh_themes()
        self._regeneration = (iter(entries), RegenerationReport())
        self.regenerate_button.set_sensitive(False)
        self._regeneration_source = GLib.idle_add(self._regeneration_step)

    def _regeneration_step(self) -> bool:
        iterator, report = self._regeneration
        try:
            entry = next(iterator)
        except StopIteration:
            self._finish_regeneration()
            self.reload(keep_editor=self.has_unsaved_changes())
            self._show_report(report)
            return False
        try:
            self._service.regenerate_entry(entry, report)
        except Exception as error:
            report.failed[entry.folder] = str(error)
        return True

    def _finish_regeneration(self) -> None:
        self._regeneration = None
        self._regeneration_source = 0
        self.regenerate_button.set_sensitive(True)

    def _on_close_request(self, *_args) -> bool:
        if self._preview_source:
            GLib.source_remove(self._preview_source)
            self._preview_source = 0
        if self._regeneration_source:
            GLib.source_remove(self._regeneration_source)
            self._finish_regeneration()
        return False

    def _show_report(self, report: RegenerationReport) -> None:
        lines = report_lines(report)
        body = "\n".join(lines) if lines else _("Nothing needed to be updated.")
        if report.updated:
            body += "\n\n" + _("Press F5 in Nautilus to see the changes.")
        dialog = Adw.AlertDialog(heading=_("Regeneration Complete"), body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)

    def _alert(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_close_response("close")
        dialog.present(self)

    def toast(self, title: str, button: str | None = None) -> None:
        toast = Adw.Toast(title=title, timeout=TOAST_SECONDS, use_markup=False)
        if button:
            toast.set_button_label(button)
        self.toasts.add_toast(toast)

    def has_unsaved_changes(self) -> bool:
        draft = self.editor.draft
        if draft is None or self._current_key is None:
            return False
        if self._pending is not None and self._current_key == str(self._pending.folder):
            return False
        entry = self._entry(self._current_key)
        return entry is None or Draft.from_entry(entry) != draft

    def _on_active_changed(self, *_args) -> None:
        if self.is_active() and self._rows and not self.has_unsaved_changes():
            self.reload()

    def _on_theme_changed(self, *_args) -> None:
        self._service.refresh_themes()
        if self._rows:
            self._schedule_preview()
