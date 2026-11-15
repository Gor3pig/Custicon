from dataclasses import replace
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .. import environment
from ..config import APP_ID, APP_NAME
from ..store import MAX_STYLE_NAME
from ..errors import CusticonError
from .. import iconsets
from ..suggestions import suggest
from ..i18n import _, ngettext
from ..service import (
    RESET_FOLDER_MISSING,
    RESET_KEPT_USER_ICON,
    RESET_UNKNOWN,
    STATUS_FOLDER_MISSING,
    STATUS_ICON_CHANGED,
    STATUS_ICON_FILE_MISSING,
    STATUS_OK,
    REPAIR_FORGET,
    REPAIR_REGENERATE,
    REPAIR_TAKE_BACK,
    RegenerationReport,
    Service,
)
from .draft import Draft
from .editor import BIG_SIZE, SMALL_SIZES, Editor
from .filemanager import manager_name
from .format import display_path, folder_name, is_portal_path, is_text_path, normalize_text
from .messages import (
    backup_summary_lines,
    banner_action,
    describe_error,
    report_lines,
    reset_report_lines,
    restore_report_lines,
    status_banner,
    status_label,
)
from .picker import PickerDialog
from .textures import texture_from_file, texture_from_svg_text
from .windowstate import initial_state, screen_size

ROW_ICON_SIZE = 32
ROW_TEXTURE_LIMIT = 512
SUGGESTION_LIMIT = 6
PICKER_SUGGESTION_LIMIT = 8
SUGGESTION_ICON_SIZE = 22
PREVIEW_DELAY_MS = 50
TOAST_SECONDS = 8
FORGET_STATUSES = (STATUS_FOLDER_MISSING, STATUS_ICON_CHANGED)


class FolderRow(Gtk.ListBoxRow):
    def __init__(self, key: str, name: str, subtitle: str, texture, warning: str | None, pending: bool, choosing: bool = False):
        super().__init__()
        self.key = key
        self.search_text = normalize_text(f"{name} {subtitle}")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, visible=choosing)
        self.check.update_property([Gtk.AccessibleProperty.LABEL], [name])
        box.append(self.check)
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
        width, height, maximized = initial_state(service.window_state(), screen_size())
        super().__init__(application=application, title=APP_NAME, default_width=width, default_height=height)
        self._service = service
        self._pending: Draft | None = None
        self._batch: list[Path] | None = None
        self._choosing = False
        self._current_key: str | None = None
        self._reloading = False
        self._preview_source = 0
        self._regeneration: tuple | None = None
        self._regeneration_source = 0
        self._shown_error: str | None = None
        self._rows: dict[str, FolderRow] = {}
        self._row_textures: dict = {}
        self._plain_textures: dict = {}
        self._suggested: dict = {}
        self._suggested_sets = None
        self._shown = None
        self._rows_shown = None

        self._build_actions()
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(vexpand=True)
        self.permission_banner = Adw.Banner(
            title=_("Custicon cannot see your home folder"),
            button_label=_("How to Fix"),
            revealed=not environment.home_access(),
        )
        self.permission_banner.connect("button-clicked", lambda *_args: self._show_permission_help())
        self.outdated_banner = Adw.Banner(
            title=_("Some icons no longer match your icon theme or accent color"),
            button_label=_("Regenerate"),
            revealed=False,
        )
        self.outdated_banner.connect("button-clicked", lambda *_args: self._start_regeneration())
        self.problems_banner = Adw.Banner(title="", button_label=_("Repair…"), revealed=False)
        self.problems_banner.connect("button-clicked", lambda *_args: self._open_repair())
        unsupported = environment.unsupported_file_manager()
        self.unsupported_banner = Adw.Banner(
            title=_("%s cannot show custom folder icons, so the icons made with Custicon will not appear in it.") % (unsupported or ""),
            revealed=unsupported is not None,
        )
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.permission_banner)
        page.append(self.unsupported_banner)
        page.append(self.outdated_banner)
        page.append(self.problems_banner)
        page.append(self.stack)
        self.toasts.set_child(page)
        self.set_content(self.toasts)
        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_main_page(), "main")

        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_files_dropped)
        self.add_controller(drop)
        if maximized:
            self.maximize()
        self.connect("close-request", self._on_close_request)
        self.connect("notify::is-active", self._on_active_changed)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.connect("notify::gtk-icon-theme-name", self._on_theme_changed)
        self._service.cleanup()
        Adw.StyleManager.get_default().connect("notify::accent-color", self._on_theme_changed)
        self.reload()

    def _build_actions(self) -> None:
        actions = (
            ("add-folder", self._choose_folder),
            ("regenerate", self._start_regeneration),
            ("export-styles", self._export_styles),
            ("import-styles", self._import_styles),
            ("backup", self._backup_settings),
            ("restore", self._restore_settings),
            ("reset-all", self._confirm_reset_all),
            ("search", self._start_search),
        )
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_args, cb=callback: cb())
            self.add_action(action)

    def _primary_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        menu.append(_("Regenerate All Icons"), "win.regenerate")
        menu.append(_("Export Styles…"), "win.export-styles")
        menu.append(_("Import Styles…"), "win.import-styles")
        menu.append(_("Save Settings…"), "win.backup")
        menu.append(_("Restore Settings…"), "win.restore")
        menu.append(_("Remove All Icons…"), "win.reset-all")
        menu.append(_("Help"), "app.help")
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
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search folders"))
        self.search_entry.connect("search-changed", lambda *_args: self.list.invalidate_filter())
        self.search_bar = Gtk.SearchBar(child=self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode_changed)
        box.append(self.search_bar)
        toggle = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=_("Search Folders (Ctrl+F)"))
        toggle.update_property([Gtk.AccessibleProperty.LABEL], [_("Search Folders")])
        toggle.bind_property("active", self.search_bar, "search-mode-enabled", GObject.BindingFlags.BIDIRECTIONAL)
        sidebar_header.pack_end(toggle)
        self.choose_toggle = Gtk.ToggleButton(icon_name="selection-mode-symbolic", tooltip_text=_("Select Several Folders"))
        self.choose_toggle.update_property([Gtk.AccessibleProperty.LABEL], [_("Select Several Folders")])
        self.choose_toggle.connect("toggled", self._on_choosing_toggled)
        sidebar_header.pack_end(self.choose_toggle)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_row_selected)
        self.list.connect("row-activated", self._on_row_activated)
        self.list.set_filter_func(self._row_matches)
        no_match = Gtk.Label(label=_("No folder matches your search"), margin_top=18, margin_bottom=18)
        no_match.add_css_class("dim-label")
        self.list.set_placeholder(no_match)
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
        self.editor.on_choose_badge_glyph = self._choose_badge_glyph
        self.editor.on_browse_glyph = self._browse_glyph
        self.editor.on_pick_suggestion = self._on_suggestion_picked
        self.editor.on_banner_action = self._on_banner_action
        self.editor.on_apply_style = self._apply_style
        self.editor.on_delete_style = self._delete_style
        self.editor.on_save_style = self._save_style
        view.set_content(self.editor)
        self.content_page = Adw.NavigationPage.new(view, APP_NAME)
        return self.content_page

    def reload(self, select: str | None = None, keep_editor: bool = False) -> None:
        self._reloading = True
        try:
            self._rebuild_rows()
            if not self._rows:
                self._current_key = None
                self.stack.set_visible_child_name("main" if self._batch else "empty")
                return
            self.stack.set_visible_child_name("main")
            key = select if select in self._rows else (self._current_key if self._current_key in self._rows else next(iter(self._rows)))
            self.list.select_row(self._rows[key])
            self._current_key = key
        finally:
            self._reloading = False
        if not keep_editor:
            self._show_key(key)

    def _rows_signature(self, items: list, scale: int):
        pending = (self._pending.folder, self._pending.glyph_name) if self._pending is not None else None
        return (tuple((item.entry, item.status) for item in items), pending, scale, self._service.current_theme())

    def _rebuild_rows(self) -> None:
        scale = self.get_scale_factor()
        items = self._customized()
        signature = self._rows_signature(items, scale)
        if self._rows and signature == self._rows_shown:
            self._update_outdated_banner(items)
            return
        self._rows_shown = signature
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        self._rows.clear()
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
            texture = self._row_texture(entry.digest, scale)
            self._add_row(entry.folder, folder_name(entry.folder), display_path(entry.folder), texture, status_label(item.status), False)
        self._update_outdated_banner(items)

    def _row_texture(self, digest: str, scale: int):
        key = (digest, scale)
        texture = self._row_textures.get(key)
        if texture is None:
            try:
                texture = texture_from_file(self._service.store.icon_path(digest), ROW_ICON_SIZE * scale)
            except CusticonError:
                return self._plain_folder_texture(scale)
            self._row_textures[key] = texture
            if len(self._row_textures) > ROW_TEXTURE_LIMIT:
                self._row_textures.pop(next(iter(self._row_textures)))
        return texture

    def _update_outdated_banner(self, known: list | None = None) -> None:
        try:
            stale = self._service.outdated(known)
        except CusticonError:
            stale = []
        self.outdated_banner.set_revealed(bool(stale))
        self._update_problems_banner(known)

    def _update_problems_banner(self, known: list | None = None) -> None:
        try:
            count = len(self._service.problems(known))
        except CusticonError:
            count = 0
        if count:
            self.problems_banner.set_title(ngettext("%d folder to check", "%d folders to check", count) % count)
        self.problems_banner.set_revealed(count > 0)

    def _open_repair(self) -> None:
        try:
            problems = self._service.problems()
        except CusticonError as error:
            self._alert(_("Could Not Read the Saved Data"), describe_error(error))
            return
        if not problems:
            self.toast(_("Nothing needs to be repaired."))
            self._update_problems_banner()
            return
        descriptions = {
            REPAIR_REGENERATE: _("Regenerate the icon"),
            REPAIR_TAKE_BACK: _("Take back the Custicon icon (replaces the one you chose)"),
            REPAIR_FORGET: _("Forget this folder (there is no icon to restore)"),
        }
        dialog = Adw.AlertDialog(heading=_("Repair Folders"), body=_("Choose what Custicon should do. Nothing is changed until you click Repair."))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        checks = []
        for action in (REPAIR_REGENERATE, REPAIR_TAKE_BACK, REPAIR_FORGET):
            group = [problem for problem in problems if problem.action == action]
            if not group:
                continue
            title = Gtk.Label(label=status_label(group[0].status), xalign=0.0, margin_top=8)
            title.add_css_class("heading")
            content.append(title)
            for problem in group:
                check = Gtk.CheckButton(active=action == REPAIR_REGENERATE)
                text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                name = Gtk.Label(label=folder_name(problem.folder), xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE)
                detail = Gtk.Label(label=descriptions[action], xalign=0.0, wrap=True)
                detail.add_css_class("dim-label")
                detail.add_css_class("caption")
                text.append(name)
                text.append(detail)
                check.set_child(text)
                content.append(check)
                checks.append((problem.folder, check))
        scrolled = Gtk.ScrolledWindow(child=content, propagate_natural_height=True, max_content_height=320, hscrollbar_policy=Gtk.PolicyType.NEVER)
        dialog.set_extra_child(scrolled)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("repair", _("Repair"))
        dialog.set_response_appearance("repair", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def refresh(*_args) -> None:
            count = sum(1 for _key, check in checks if check.get_active())
            dialog.set_response_enabled("repair", count > 0)
            dialog.set_response_label("repair", ngettext("Repair %d Folder", "Repair %d Folders", count) % count if count else _("Repair"))

        for _key, check in checks:
            check.connect("toggled", refresh)
        refresh()
        dialog.choose(self, None, self._repair_chosen, checks)

    def _repair_chosen(self, dialog, result, checks) -> None:
        if dialog.choose_finish(result) != "repair":
            return
        keys = [key for key, check in checks if check.get_active()]
        try:
            report = self._service.repair(keys)
        except CusticonError as error:
            self._alert(_("Could Not Repair the Folders"), describe_error(error))
            return
        repaired = len(report.regenerated) + len(report.taken_back)
        self.toast(
            _("%(repaired)d repaired, %(forgotten)d forgotten, %(failed)d failed. Press F5 in %(manager)s to see the changes.")
            % {"repaired": repaired, "forgotten": len(report.forgotten), "failed": len(report.failed), "manager": manager_name()}
        )
        self.reload(select=self._current_key)
        if report.failed:
            lines = [f"{folder_name(key)}: {message}" for key, message in report.failed.items()]
            self._alert(_("Some Folders Could Not Be Repaired"), "\n".join(lines))

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
        row = FolderRow(key, name, subtitle, texture, warning, pending, self._choosing)
        row.check.connect("toggled", self._on_row_checked)
        self._rows[key] = row
        self.list.append(row)

    def _plain_folder_texture(self, scale: int):
        try:
            path = self._service.folder_file()
        except CusticonError:
            return None
        key = (str(path), scale)
        texture = self._plain_textures.get(key)
        if texture is None:
            try:
                texture = texture_from_file(path, ROW_ICON_SIZE * scale)
            except CusticonError:
                return None
            self._plain_textures[key] = texture
        return texture

    def _on_choosing_toggled(self, button) -> None:
        self._choosing = button.get_active()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE if self._choosing else Gtk.SelectionMode.SINGLE)
        for row in self._rows.values():
            row.check.set_visible(self._choosing)
            self._set_checked(row, self._choosing and row.key == self._current_key and not self._batch)
        if not self._choosing:
            current = self._current_key
            self._batch = None
            self.reload(select=current)

    def _set_checked(self, row, active: bool) -> None:
        self._reloading = True
        try:
            row.check.set_active(active)
        finally:
            self._reloading = False

    def _checked_keys(self) -> list[str]:
        return [key for key, row in self._rows.items() if row.check.get_active()]

    def _on_row_checked(self, _check) -> None:
        if self._reloading or not self._choosing:
            return
        keys = self._checked_keys()
        if len(keys) >= 2:
            self._show_batch([Path(key) for key in keys])
        elif len(keys) == 1:
            self._show_key(keys[0])
            self.split.set_show_content(True)

    def _on_row_selected(self, _list, row) -> None:
        if self._reloading or row is None or self._choosing:
            return
        self._show_key(row.key)
        self.split.set_show_content(True)

    def _on_row_activated(self, _list, row) -> None:
        if self._reloading or row is None:
            return
        if self._choosing:
            row.check.set_active(not row.check.get_active())
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

    def _current_state(self):
        key = self._current_key
        if key is None:
            return None
        if self._pending is not None and key == str(self._pending.folder):
            return (key, self._pending, STATUS_OK)
        entry = self._entry(key)
        if entry is None:
            return None
        return (key, entry, self._service.entry_status(entry))

    def _show_key(self, key: str) -> None:
        self._batch = None
        self._current_key = key
        self._shown = self._current_state()
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
        self._refresh_styles()
        self._refresh_suggestions(draft)
        self._schedule_preview()

    def _suggestions_for(self, name: str, limit: int) -> list[str]:
        sets = self._service.icon_sets()
        if sets is not self._suggested_sets:
            self._suggested_sets = sets
            self._suggested = {}
        found = self._suggested.get((name, limit))
        if found is None:
            found = self._suggested[(name, limit)] = suggest(name, sets, limit)
        return list(found)

    def _refresh_suggestions(self, draft: Draft) -> None:
        sets = self._service.icon_sets()
        language = iconsets.current_language()
        items = []
        for name in self._suggestions_for(folder_name(draft.folder), SUGGESTION_LIMIT):
            icon_set = sets.for_name(name)
            if icon_set is not None:
                items.append((name, icon_set.paintable(name, SUGGESTION_ICON_SIZE), icon_set.label(name, language)))
        self.editor.set_suggestions(items)

    def _on_suggestion_picked(self, name: str) -> None:
        if self.editor.draft is not None:
            self._on_glyph_chosen(name)

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
                self.editor.set_computed_color(built.color)
                self.editor.set_computed_folder_tint(built.folder_tint)
            else:
                folder_svg, folder_tint = self._service.folder_preview(draft.to_settings(), theme)
                if folder_svg is None:
                    folder = self._service.folder_file(theme)
                    render = lambda size: texture_from_file(folder, size * scale)
                else:
                    render = lambda size: texture_from_svg_text(folder_svg, size * scale)
                self.editor.set_computed_color(None)
                self.editor.set_computed_folder_tint(folder_tint)
            self.editor.set_accent_color(self._service.accent_color())
            self.editor.set_preview(render(BIG_SIZE), [render(size) for size in SMALL_SIZES], note)
            self.editor.set_blocked(False)
        except CusticonError as error:
            self.editor.set_preview(None, [None] * len(SMALL_SIZES), describe_error(error).split("\n")[0])
            self.editor.set_blocked(True)
        return False

    def _start_search(self) -> None:
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    def _on_search_mode_changed(self, *_args) -> None:
        if not self.search_bar.get_search_mode():
            self.search_entry.set_text("")
            self.list.invalidate_filter()

    def _row_matches(self, row) -> bool:
        query = normalize_text(self.search_entry.get_text().strip())
        return not query or query in row.search_text

    def _on_banner_action(self) -> None:
        entry = self._entry(self._current_key) if self._current_key else None
        if entry is not None and self._service.entry_status(entry) == STATUS_FOLDER_MISSING:
            self._choose_new_location()
        else:
            self._apply()

    def _choose_new_location(self) -> None:
        dialog = Gtk.FileDialog(title=_("Find the Folder"))
        old = self._current_key
        dialog.select_folder(self, None, lambda d, result: self._new_location_chosen(d, result, old))

    def _new_location_chosen(self, dialog, result, old: str) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = self._real_path(gfile)
        if path is None:
            return
        try:
            entry = self._service.relocate(old, path)
        except CusticonError as error:
            self._alert(_("Could Not Find the Folder"), describe_error(error))
            return
        self.toast(_("“%(folder)s” is back in the list. Press F5 in %(manager)s to see its icon.") % {"folder": folder_name(entry.folder), "manager": manager_name()})
        self.reload(select=entry.folder)

    def _confirm_reset_all(self) -> None:
        if not self._rows:
            self.toast(_("There are no customized folders."))
            return
        dialog = Adw.AlertDialog(
            heading=_("Remove All Icons?"),
            body=_("Every customized folder gets back the icon it had before Custicon. The content of the folders is not modified."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove All"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self._reset_all_confirmed)

    def _reset_all_confirmed(self, dialog, result) -> None:
        if dialog.choose_finish(result) != "remove":
            return
        try:
            report = self._service.reset_all()
        except CusticonError as error:
            self._alert(_("Could Not Remove the Icons"), describe_error(error))
            return
        self._pending = None
        self.reload()
        body = "\n".join(reset_report_lines(report))
        if report.restored:
            body += "\n\n" + _("Press F5 in %s to see the changes.") % manager_name()
        self._alert(_("Icons Removed"), body)

    def _real_path(self, gfile) -> str | None:
        path = gfile.get_path()
        if path is None or is_portal_path(path):
            if not environment.home_access():
                self._show_permission_help()
                return None
            self._alert(_("Folder Not Accessible"), _("Custicon needs the real location of the folder. Choose a folder inside your home folder."))
            return None
        if not is_text_path(path):
            self._alert(_("Name Not Supported"), _("This name is not valid UTF-8 text, so Custicon cannot use it."))
            return None
        return path

    def open_files(self, files: list) -> None:
        if not files:
            return
        path = self._real_path(files[0])
        if path is None:
            return
        folder = Path(path)
        if not folder.is_dir():
            self._alert(_("Not a Folder"), _("Custicon customizes folders, not files."))
            return
        if len(files) > 1:
            self.open_many(files)
            return
        self.add_folder(folder)

    def open_many(self, files: list) -> None:
        folders = []
        ignored = 0
        for gfile in files:
            path = gfile.get_path()
            if path is None or is_portal_path(path) or not is_text_path(path) or not Path(path).is_dir():
                ignored += 1
            elif Path(path) not in folders:
                folders.append(Path(path))
        if not folders and not environment.home_access():
            self._show_permission_help()
            return
        if ignored:
            self.toast(ngettext("%d item ignored: not a usable folder", "%d items ignored: not usable folders", ignored) % ignored)
        self.add_folders(folders)

    def add_folders(self, folders: list[Path]) -> None:
        resolved = []
        for folder in folders:
            try:
                real = self._service.folder_key(folder)
            except (CusticonError, OSError, RuntimeError):
                continue
            if real not in resolved:
                resolved.append(real)
        if len(resolved) <= 1:
            if resolved:
                self.add_folder(resolved[0])
            return
        self._choosing_off()
        self._batch = resolved
        self._pending = None
        self._current_key = None
        self.reload(keep_editor=True)
        self._reloading = True
        try:
            self.list.unselect_all()
        finally:
            self._reloading = False
        self._show_batch(resolved)
        self.editor.choose_button.grab_focus()

    def _choosing_off(self) -> None:
        if self._choosing:
            self._batch = None
            self.choose_toggle.set_active(False)

    def _customized_in(self, folders: list[Path]) -> list[str]:
        try:
            known = self._service.store.all()
        except CusticonError as error:
            self._report_registry_error(error)
            return []
        return [known[key].folder for key in map(str, folders) if key in known]

    def _show_batch(self, folders: list[Path]) -> None:
        self._batch = folders
        self._current_key = None
        names = [folder_name(folder) for folder in folders]
        draft = Draft(folder=folders[0]).with_appearance(self._service.remembered_appearance())
        self.content_page.set_title(ngettext("%d folder", "%d folders", len(folders)) % len(folders))
        self.editor.load(draft, False, STATUS_OK)
        self.editor.show_batch(names, len(self._customized_in(folders)))
        self.editor.set_suggestions([])
        self._refresh_styles()
        self._schedule_preview()
        self.split.set_show_content(True)

    def _on_files_dropped(self, _target, value, _x, _y) -> bool:
        self.open_files(value.get_files())
        return True

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title=_("Choose Folders"))
        dialog.select_multiple_folders(self, None, self._folders_chosen)

    def _folders_chosen(self, dialog, result) -> None:
        try:
            model = dialog.select_multiple_folders_finish(result)
        except GLib.Error:
            return
        self.open_files([model.get_item(index) for index in range(model.get_n_items())])

    def add_folder(self, path: Path) -> None:
        self._choosing_off()
        try:
            key = str(self._service.folder_key(path))
        except (CusticonError, OSError, RuntimeError):
            self._alert(_("Folder Not Accessible"), _("Custicon needs the real location of the folder. Choose a folder inside your home folder."))
            return
        if self._entry(key) is None:
            self._pending = Draft(folder=Path(key)).with_appearance(self._service.remembered_appearance())
        self.reload(select=key)
        self.split.set_show_content(True)

    def _choose_glyph(self) -> None:
        draft = self.editor.draft
        if draft is None:
            return
        picker = PickerDialog(
            self._service.icon_theme(),
            draft.glyph_name,
            self._on_glyph_chosen,
            self._service.icon_sets(),
            folder_name(draft.folder),
            self._service.picker_memory(),
            self._service.set_favorite,
            self._suggestions_for(folder_name(draft.folder), PICKER_SUGGESTION_LIMIT),
        )
        picker.present(self)

    def _choose_badge_glyph(self) -> None:
        draft = self.editor.draft
        if draft is None:
            return
        picker = PickerDialog(
            self._service.icon_theme(),
            draft.badge_glyph,
            self._on_badge_glyph_chosen,
            self._service.icon_sets(),
            folder_name(draft.folder),
            self._service.picker_memory(),
            self._service.set_favorite,
            self._suggestions_for(folder_name(draft.folder), PICKER_SUGGESTION_LIMIT),
        )
        picker.present(self)

    def _on_badge_glyph_chosen(self, name: str) -> None:
        self._service.remember_glyph(name)
        self.editor.update_glyph(replace(self.editor.draft, badge_glyph=name, badge_on=True))

    def _on_glyph_chosen(self, name: str) -> None:
        self._service.remember_glyph(name)
        self.editor.update_glyph(self.editor.draft.with_glyph_name(name))

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

    def _apply_batch(self, draft: Draft) -> None:
        folders = self._batch
        report = self._service.apply_many(folders, draft.to_settings())
        if report.applied:
            self._service.remember_appearance(draft.to_settings())
        self._batch = None
        self._choosing_off()
        self.reload(select=report.applied[0] if report.applied else None)
        if report.failed:
            lines = [ngettext("%d folder customized", "%d folders customized", len(report.applied)) % len(report.applied)]
            lines.append(ngettext("%d folder could not be customized", "%d folders could not be customized", len(report.failed)) % len(report.failed))
            lines.extend("%s: %s" % (folder_name(folder), message.split("\n")[0]) for folder, message in report.failed.items())
            self._alert(_("Some Icons Could Not Be Applied"), "\n".join(lines))
        else:
            self.toast(ngettext("Icon applied to %(count)d folder. Press F5 in %(manager)s to see it.", "Icon applied to %(count)d folders. Press F5 in %(manager)s to see it.", len(report.applied)) % {"count": len(report.applied), "manager": manager_name()}, _("Got It"))

    def _apply(self) -> None:
        draft = self.editor.draft
        if draft is None:
            return
        if self._batch:
            self._apply_batch(draft)
            return
        try:
            entry = self._service.apply(draft.folder, draft.to_settings())
        except CusticonError as error:
            self._alert(_("Could Not Apply the Icon"), describe_error(error))
            return
        self._service.remember_appearance(draft.to_settings())
        if self._pending is not None and str(self._pending.folder) == entry.folder:
            self._pending = None
        self.toast(_("Icon applied to “%(folder)s”. Press F5 in %(manager)s to see it.") % {"folder": folder_name(entry.folder), "manager": manager_name()}, _("Got It"))
        self.reload(select=entry.folder)

    def _confirm_remove_batch(self) -> None:
        keys = self._customized_in(self._batch)
        if not keys:
            return
        ignored = len(self._batch) - len(keys)
        body = _("The folders will get back the icon they had before Custicon. Their content is not modified.")
        if ignored:
            body += "\n\n" + ngettext("%d folder without a Custicon icon is left alone.", "%d folders without a Custicon icon are left alone.", ignored) % ignored
        dialog = Adw.AlertDialog(heading=ngettext("Remove the Icon of %d Folder?", "Remove the Icons of %d Folders?", len(keys)) % len(keys), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, lambda d, result, chosen=keys: self._remove_batch_confirmed(d, result, chosen))

    def _remove_batch_confirmed(self, dialog, result, keys: list[str]) -> None:
        if dialog.choose_finish(result) == "remove":
            self.remove_many(keys)

    def remove_many(self, keys: list[str]) -> None:
        snapshots = []
        for key in keys:
            try:
                snapshot = self._service.snapshot(key)
            except CusticonError:
                snapshot = None
            if snapshot is not None:
                snapshots.append(snapshot)
        report = self._service.reset_many(keys)
        self._batch = None
        self._choosing_off()
        self.reload()
        restored = [snapshot for snapshot in snapshots if snapshot.entry.folder in report.restored]
        if report.failed or report.kept or report.missing:
            body = "\n".join(reset_report_lines(report))
            if report.restored:
                body += "\n\n" + _("Press F5 in %s to see the changes.") % manager_name()
            self._alert(_("Icons Removed"), body)
            if restored:
                count = len(restored)
                self.toast(ngettext("Icon removed from %(count)d folder. Press F5 in %(manager)s to see it.", "Icons removed from %(count)d folders. Press F5 in %(manager)s to see it.", count) % {"count": count, "manager": manager_name()}, _("Undo"), lambda: self._undo_removals(restored))
            return
        count = len(report.restored)
        message = ngettext("Icon removed from %(count)d folder. Press F5 in %(manager)s to see it.", "Icons removed from %(count)d folders. Press F5 in %(manager)s to see it.", count) % {"count": count, "manager": manager_name()}
        self.toast(message, _("Undo") if restored else None, (lambda: self._undo_removals(restored)) if restored else None)

    def _undo_removals(self, snapshots: list) -> None:
        restored = []
        failed = {}
        for snapshot in snapshots:
            try:
                restored.append(self._service.restore(snapshot).folder)
            except CusticonError as error:
                failed[snapshot.entry.folder] = describe_error(error)
        self.reload(select=restored[0] if restored else None)
        if failed:
            self._alert(_("Could Not Restore the Icon"), "\n".join(f"{folder_name(folder)}: {message}" for folder, message in failed.items()))
            return
        count = len(restored)
        self.toast(ngettext("Icon restored on %(count)d folder. Press F5 in %(manager)s to see it.", "Icon restored on %(count)d folders. Press F5 in %(manager)s to see it.", count) % {"count": count, "manager": manager_name()})

    def _confirm_remove(self) -> None:
        if self._batch:
            self._confirm_remove_batch()
            return
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
            snapshot = self._service.snapshot(key)
        except CusticonError:
            snapshot = None
        try:
            outcome = self._service.reset(key)
        except CusticonError as error:
            self._alert(_("Could Not Remove the Icon"), describe_error(error))
            return
        if outcome == RESET_UNKNOWN:
            self.toast(_("“%s” was no longer in the list.") % name)
        elif outcome == RESET_KEPT_USER_ICON:
            self.toast(_("“%s”: the icon you chose outside Custicon was kept.") % name)
        elif outcome == RESET_FOLDER_MISSING:
            self.toast(_("“%s” was forgotten.") % name)
        else:
            undo = (lambda: self._undo_removal(snapshot)) if snapshot is not None else None
            self.toast(_("Icon removed from “%(folder)s”. Press F5 in %(manager)s to see it.") % {"folder": name, "manager": manager_name()}, _("Undo") if undo else None, undo)
        self.reload()

    def _undo_removal(self, snapshot) -> None:
        try:
            entry = self._service.restore(snapshot)
        except CusticonError as error:
            self._alert(_("Could Not Restore the Icon"), describe_error(error))
            return
        self.toast(_("Icon restored on “%(folder)s”. Press F5 in %(manager)s to see it.") % {"folder": folder_name(entry.folder), "manager": manager_name()})
        self.reload(select=entry.folder)

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
        self._remember_window()
        return False

    def _remember_window(self) -> None:
        if self.is_fullscreen():
            return
        width, height = self.get_default_size()
        self._service.remember_window(width, height, self.is_maximized())

    def _show_report(self, report: RegenerationReport) -> None:
        lines = report_lines(report)
        body = "\n".join(lines) if lines else _("Nothing needed to be updated.")
        if report.updated:
            body += "\n\n" + _("Press F5 in %s to see the changes.") % manager_name()
        dialog = Adw.AlertDialog(heading=_("Regeneration Complete"), body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)

    def _styles(self) -> list:
        try:
            return self._service.styles()
        except CusticonError:
            return []

    def _refresh_styles(self) -> None:
        self.editor.set_styles([style.name for style in self._styles()])

    def _apply_style(self, name: str) -> None:
        draft = self.editor.draft
        style = next((style for style in self._styles() if style.name == name), None)
        if draft is not None and style is not None:
            self.editor.update_glyph(draft.with_appearance(style.appearance))

    def _delete_style(self, name: str) -> None:
        try:
            self._service.delete_style(name)
        except CusticonError as error:
            self._alert(_("Could Not Delete the Style"), describe_error(error))
            return
        self._refresh_styles()
        self.toast(_("Style “%s” deleted.") % name)

    def _save_style(self) -> None:
        draft = self.editor.draft
        if draft is None:
            return
        entry = Gtk.Entry(max_length=MAX_STYLE_NAME, activates_default=True, placeholder_text=_("Style name"))
        dialog = Adw.AlertDialog(heading=_("Save Style"), body=_("Give a name to the current appearance to reuse it for other folders."))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        appearance = draft.appearance()
        dialog.choose(self, None, lambda d, result: self._style_named(d, result, entry.get_text(), appearance))

    def _style_named(self, dialog, result, name: str, appearance: dict) -> None:
        if dialog.choose_finish(result) != "save":
            return
        try:
            self._service.save_style(name, appearance)
        except CusticonError as error:
            self._alert(_("Could Not Save the Style"), describe_error(error))
            return
        self._refresh_styles()
        self.toast(_("Style “%s” saved.") % name.strip())

    def _export_styles(self) -> None:
        if not self._styles():
            self.toast(_("There are no styles to export."))
            return
        dialog = Gtk.FileDialog(title=_("Export Styles"), initial_name="custicon-styles.json")
        dialog.save(self, None, self._styles_export_chosen)

    def _styles_export_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            self._alert(_("Could Not Export the Styles"), _("Choose a location on this computer."))
            return
        try:
            count = self._service.export_styles(path)
        except CusticonError as error:
            self._alert(_("Could Not Export the Styles"), describe_error(error))
            return
        self.toast(ngettext("%d style exported.", "%d styles exported.", count) % count)

    def _import_styles(self) -> None:
        dialog = Gtk.FileDialog(title=_("Import Styles"))
        dialog.open(self, None, self._styles_import_chosen)

    def _styles_import_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            self._alert(_("Could Not Import the Styles"), _("Choose a file on this computer."))
            return
        try:
            imported, rejected = self._service.import_styles(path)
        except CusticonError as error:
            self._alert(_("Could Not Import the Styles"), describe_error(error))
            return
        self._refresh_styles()
        if rejected:
            self.toast(_("%(imported)d imported, %(ignored)d ignored because they are not valid") % {"imported": imported, "ignored": rejected})
        else:
            self.toast(ngettext("%d style imported.", "%d styles imported.", imported) % imported)

    def _backup_settings(self) -> None:
        dialog = Gtk.FileDialog(title=_("Save Settings"), initial_name=f"custicon-{date.today().isoformat()}.json")
        dialog.save(self, None, self._backup_save_chosen)

    def _backup_save_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            self._alert(_("Could Not Save the Settings"), _("Choose a location on this computer."))
            return
        try:
            summary = self._service.export_backup(path)
        except CusticonError as error:
            self._alert(_("Could Not Save the Settings"), describe_error(error))
            return
        lines = "\n".join(backup_summary_lines(summary))
        self._alert(
            _("Settings Saved"),
            _("The file “%s” was saved.") % Path(path).name + "\n\n" + lines + "\n\n" + _("The file only holds paths and settings, never images."),
        )

    def _restore_settings(self) -> None:
        dialog = Gtk.FileDialog(title=_("Restore Settings"))
        dialog.open(self, None, self._backup_open_chosen)

    def _backup_open_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            self._alert(_("Could Not Restore the Settings"), _("Choose a file on this computer."))
            return
        try:
            count = self._service.inspect_backup(path)
        except CusticonError as error:
            self._alert(_("Could Not Restore the Settings"), describe_error(error))
            return
        dialog = Adw.AlertDialog(
            heading=_("Restore the Settings?"),
            body=ngettext(
                "The icon of %d folder will be regenerated with the settings of the file. Folders that no longer exist are ignored. Your current styles are kept and completed.",
                "The icons of %d folders will be regenerated with the settings of the file. Folders that no longer exist are ignored. Your current styles are kept and completed.",
                count,
            )
            % count,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, lambda d, answer, chosen=path: self._restore_confirmed(d, answer, chosen))

    def _restore_confirmed(self, dialog, result, path: str) -> None:
        if dialog.choose_finish(result) != "restore":
            return
        self.restore_from(path)

    def restore_from(self, path: str) -> None:
        try:
            report = self._service.import_backup(path)
        except CusticonError as error:
            self._alert(_("Could Not Restore the Settings"), describe_error(error))
            return
        self._choosing_off()
        self.reload()
        self._refresh_styles()
        self._show_restore_report(report)

    def _show_restore_report(self, report) -> None:
        body = "\n".join(restore_report_lines(report))
        if report.failed:
            body += "\n\n" + "\n".join(f"{folder_name(folder)}: {message.splitlines()[0]}" for folder, message in report.failed.items())
        if report.restored:
            body += "\n\n" + _("Press F5 in %s to see the changes.") % manager_name()
        self._alert(_("Restoration Finished"), body.strip() or _("The file contained nothing to restore."))

    def _show_permission_help(self) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Allow Access to Your Home Folder"),
            body=_(
                "Custicon needs to see the real location of your folders. Custicon never modifies the content of a folder.\n\n"
                "Allow access to the home folder in the Settings (Applications, then Custicon) or in Flatseal, or run this command in a terminal, then restart Custicon. It works for an installation for all users too:"
            ),
        )
        command = Gtk.Label(label=environment.FLATPAK_COMMAND, selectable=True, wrap=True, xalign=0.0, margin_top=6)
        command.add_css_class("monospace")
        dialog.set_extra_child(command)
        dialog.add_response("close", _("Close"))
        dialog.set_close_response("close")
        dialog.present(self)

    def _alert(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_close_response("close")
        dialog.present(self)

    def toast(self, title: str, button: str | None = None, on_button=None) -> None:
        toast = Adw.Toast(title=title, timeout=TOAST_SECONDS, use_markup=False)
        if button:
            toast.set_button_label(button)
            if on_button is not None:
                toast.connect("button-clicked", lambda *_args: on_button())
        self.toasts.add_toast(toast)

    def has_unsaved_changes(self) -> bool:
        draft = self.editor.draft
        if self._batch:
            return True
        if draft is None or self._current_key is None:
            return False
        if self._pending is not None and self._current_key == str(self._pending.folder):
            return False
        entry = self._entry(self._current_key)
        return entry is None or Draft.from_entry(entry) != draft

    def _on_active_changed(self, *_args) -> None:
        if self.is_active() and self._rows and not self.has_unsaved_changes():
            shown = self._shown
            self.reload(keep_editor=True)
            if shown is None or self._current_state() != shown:
                self._show_key(self._current_key)

    def _on_theme_changed(self, *_args) -> None:
        self._service.refresh_themes()
        self._update_outdated_banner()
        if self._rows:
            self._schedule_preview()
