import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..customsets import MAX_FILES, MAX_SET_NAME, MAX_SETS
from ..errors import CusticonError
from ..i18n import _, ngettext
from ..textutil import normalize_text
from .messages import describe_error

MAX_SHOWN = 100
THUMBNAIL = 28


class SetsDialog(Adw.Dialog):
    def __init__(self, service, on_message, pick_folder, on_changed=None, pick_files=None):
        super().__init__(title=_("Custom Icon Sets"), content_width=520)
        self._service = service
        self._on_message = on_message
        self._pick_folder = pick_folder
        self._pick_files = pick_files or (lambda callback: None)
        self._on_changed = on_changed or (lambda: None)
        self._busy = False
        self._open_slugs: set[str] = set()
        self._rows: dict[str, Adw.ExpanderRow] = {}
        self._icon_rows: dict[str, list] = {}
        self._searches: dict[str, str] = {}
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        intro = Gtk.Label(
            label=_("Add a folder of SVG files. Custicon keeps a copy, so you can move or delete the original folder afterwards."),
            wrap=True,
            xalign=0.0,
        )
        intro.add_css_class("dim-label")
        page.append(intro)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        page.append(self.list)
        self.add_button = Gtk.Button(label=_("Add a Set from a Folder…"), halign=Gtk.Align.START)
        self.add_button.add_css_class("suggested-action")
        self.add_button.connect("clicked", lambda *_args: self._pick_folder(self._folder_chosen))
        page.append(self.add_button)
        limits = Gtk.Label(
            label=_("Up to %(sets)d sets and %(files)d SVG files per set. Removing a set does not change the folders already customized: their icons are kept.")
            % {"sets": MAX_SETS, "files": MAX_FILES},
            wrap=True,
            xalign=0.0,
        )
        limits.add_css_class("caption")
        limits.add_css_class("dim-label")
        page.append(limits)
        view.set_content(Gtk.ScrolledWindow(child=page, propagate_natural_height=True, max_content_height=560, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(view)
        self.refresh()

    def refresh(self) -> None:
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        self._rows.clear()
        self._icon_rows.clear()
        sets = self._service.custom_sets()
        if not sets:
            placeholder = Adw.ActionRow(title=_("No custom set yet"))
            placeholder.set_sensitive(False)
            self.list.append(placeholder)
        for info in sets:
            row = Adw.ExpanderRow(title=GLib.markup_escape_text(info.name), subtitle=self._count_text(info.count))
            remove = Gtk.Button(label=_("Remove Set"), valign=Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect("clicked", self._confirm_remove, info)
            row.add_suffix(remove)
            self._rows[info.slug] = row
            self._prepare_tools(row, info)
            row.connect("notify::expanded", self._on_expanded, info)
            self.list.append(row)
            if info.slug in self._open_slugs:
                row.set_expanded(True)
        self.add_button.set_sensitive(not self._busy)

    @staticmethod
    def _count_text(count: int) -> str:
        return ngettext("%d icon", "%d icons", count) % count

    def _prepare_tools(self, row: Adw.ExpanderRow, info) -> None:
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        add = Gtk.Button(label=_("Add Icons…"))
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda *_args: self._pick_files(lambda files: self._start_adding(info, files)))
        search = Gtk.SearchEntry(placeholder_text=_("Search in this set"), hexpand=True)
        search.set_text(self._searches.get(info.slug, ""))
        search.connect("search-changed", self._on_search_changed, info)
        tools.append(add)
        tools.append(search)
        row.add_row(tools)

    def _on_expanded(self, row, _param, info) -> None:
        if row.get_expanded():
            self._open_slugs.add(info.slug)
            self._fill_icons(info)
        else:
            self._open_slugs.discard(info.slug)

    def _on_search_changed(self, entry, info) -> None:
        self._searches[info.slug] = entry.get_text()
        self._fill_icons(info)

    def _fill_icons(self, info) -> None:
        row = self._rows.get(info.slug)
        if row is None:
            return
        for old in self._icon_rows.pop(info.slug, []):
            row.remove(old)
        try:
            names = self._service.set_icons(info.slug)
        except CusticonError as error:
            self._on_message(_("Could Not Read the Set"), describe_error(error))
            return
        needle = normalize_text(self._searches.get(info.slug, "").strip())
        shown = [name for name in names if not needle or needle in normalize_text(name.replace("-", " ")) or needle in name]
        rows = []
        icon_set = self._service.icon_sets()[info.key]
        for name in shown[:MAX_SHOWN]:
            item = Adw.ActionRow(title=GLib.markup_escape_text(name))
            try:
                image = Gtk.Image(paintable=icon_set.paintable(info.key + ":" + name, THUMBNAIL), pixel_size=THUMBNAIL)
                item.add_prefix(image)
            except CusticonError:
                pass
            remove = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.connect("clicked", self._confirm_remove_icon, info, name)
            item.add_suffix(remove)
            row.add_row(item)
            rows.append(item)
        if len(shown) > MAX_SHOWN:
            more = Adw.ActionRow(title=_("Search to see the other icons"))
            more.set_sensitive(False)
            row.add_row(more)
            rows.append(more)
        self._icon_rows[info.slug] = rows
        row.set_subtitle(self._count_text(len(names)))

    def _confirm_remove_icon(self, _button, info, name) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove This Icon?"),
            body=_("“%s” is removed from the set. The folders already customized with it keep their icon.") % name,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self._remove_icon_confirmed, info, name)

    def _remove_icon_confirmed(self, dialog, result, info, name) -> None:
        if dialog.choose_finish(result) != "remove":
            return
        try:
            self._service.remove_icon(info.slug, name)
        except CusticonError as error:
            self._on_message(_("Could Not Remove the Icon"), describe_error(error))
            return
        self._fill_icons(info)
        self._on_changed()

    def _start_adding(self, info, files) -> None:
        if not files:
            return

        def work() -> dict:
            return {"report": self._service.add_icons(info.slug, [Path(file) for file in files])}

        self._run_in_thread(work, lambda outcome: self._adding_finished(info, outcome))

    def _adding_finished(self, info, outcome: dict) -> None:
        if "error" in outcome:
            self._on_message(_("Could Not Add the Icons"), self._error_text(outcome["error"]))
            return
        report = outcome["report"]
        parts = []
        if report.added:
            parts.append(ngettext("%(added)d icon added", "%(added)d icons added", report.added) % {"added": report.added})
        if report.replaced:
            parts.append(ngettext("%(replaced)d replaced", "%(replaced)d replaced", report.replaced) % {"replaced": report.replaced})
        if report.ignored:
            parts.append(ngettext("%(ignored)d ignored", "%(ignored)d ignored", report.ignored) % {"ignored": report.ignored})
        text = ", ".join(parts) + "." if parts else _("Nothing was added.")
        if report.replaced:
            text += "\n\n" + _("The folders that use a replaced icon are marked as outdated: Regenerate updates them.")
        self._on_message(_("Icons Added to “%s”") % info.name, text)
        self._fill_icons(info)
        self._on_changed()

    @staticmethod
    def _error_text(error) -> str:
        return describe_error(error) if isinstance(error, CusticonError) else str(error)

    def _confirm_remove(self, _button, info) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove This Set?"),
            body=_("“%s” is removed. The folders already customized with its pictograms keep their icons.") % info.name,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self._remove_confirmed, info)

    def _remove_confirmed(self, dialog, result, info) -> None:
        if dialog.choose_finish(result) != "remove":
            return
        try:
            self._service.remove_set(info.slug)
        except CusticonError as error:
            self._on_message(_("Could Not Remove the Set"), describe_error(error))
            return
        self._open_slugs.discard(info.slug)
        self._searches.pop(info.slug, None)
        self.refresh()
        self._on_changed()

    def _folder_chosen(self, folder: Path) -> None:
        entry = Gtk.Entry(text=folder.name[:MAX_SET_NAME], max_length=MAX_SET_NAME, activates_default=True)
        dialog = Adw.AlertDialog(
            heading=_("Name the Set"),
            body=_("The icons are copied as they are. Make sure you are allowed to use them."),
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self._name_chosen, folder, entry)

    def _name_chosen(self, dialog, result, folder: Path, entry: Gtk.Entry) -> None:
        if dialog.choose_finish(result) != "add":
            return
        self._start_import(folder, entry.get_text())

    def _start_import(self, folder: Path, name: str) -> None:
        def work() -> dict:
            return {"report": self._service.import_set(folder, name)}

        self._run_in_thread(work, self._import_finished)

    def _run_in_thread(self, work, finished) -> None:
        if self._busy:
            return
        self._busy = True
        self.add_button.set_sensitive(False)
        self.list.set_sensitive(False)
        self.add_button.set_label(_("Adding…"))

        def run() -> None:
            try:
                outcome = work()
            except (CusticonError, OSError) as error:
                outcome = {"error": error}
            GLib.idle_add(self._thread_finished, outcome, finished)

        threading.Thread(target=run, daemon=True).start()

    def _thread_finished(self, outcome: dict, finished) -> bool:
        self._busy = False
        self.add_button.set_label(_("Add a Set from a Folder…"))
        self.list.set_sensitive(True)
        self.refresh()
        finished(outcome)
        return False

    def _import_finished(self, outcome: dict) -> None:
        if "error" in outcome:
            self._on_message(_("Could Not Add the Set"), self._error_text(outcome["error"]))
            return
        report = outcome["report"]
        text = ngettext("%(added)d icon added", "%(added)d icons added", report.added) % {"added": report.added}
        if report.ignored:
            text += _(", %(ignored)d ignored (unreadable, too large or already present).") % {"ignored": report.ignored}
        self._on_message(_("“%s” Is Ready") % report.name, text)
        self._on_changed()
