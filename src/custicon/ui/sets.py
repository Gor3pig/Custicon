import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..customsets import MAX_FILES, MAX_SET_NAME, MAX_SETS
from ..errors import CusticonError
from ..i18n import _, ngettext
from .messages import describe_error


class SetsDialog(Adw.Dialog):
    def __init__(self, service, on_message, pick_folder, on_changed=None):
        super().__init__(title=_("Custom Icon Sets"), content_width=520)
        self._service = service
        self._on_message = on_message
        self._pick_folder = pick_folder
        self._on_changed = on_changed or (lambda: None)
        self._busy = False
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
        view.set_content(Gtk.ScrolledWindow(child=page, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(view)
        self.refresh()

    def refresh(self) -> None:
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        sets = self._service.custom_sets()
        if not sets:
            placeholder = Adw.ActionRow(title=_("No custom set yet"))
            placeholder.set_sensitive(False)
            self.list.append(placeholder)
        for info in sets:
            row = Adw.ActionRow(title=GLib.markup_escape_text(info.name), subtitle=ngettext("%d icon", "%d icons", info.count) % info.count)
            remove = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect("clicked", self._confirm_remove, info)
            row.add_suffix(remove)
            self.list.append(row)
        self.add_button.set_sensitive(not self._busy)

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
        if self._busy:
            return
        self._busy = True
        self.add_button.set_sensitive(False)
        self.add_button.set_label(_("Adding…"))
        outcome = {}

        def work() -> None:
            try:
                outcome["report"] = self._service.import_set(folder, name)
            except CusticonError as error:
                outcome["error"] = error
            except OSError as error:
                outcome["error"] = error
            GLib.idle_add(self._import_finished, outcome)

        threading.Thread(target=work, daemon=True).start()

    def _import_finished(self, outcome: dict) -> bool:
        self._busy = False
        self.add_button.set_label(_("Add a Set from a Folder…"))
        self.refresh()
        if "error" in outcome:
            error = outcome["error"]
            self._on_message(_("Could Not Add the Set"), describe_error(error) if isinstance(error, CusticonError) else str(error))
            return False
        report = outcome["report"]
        text = ngettext("%(added)d icon added", "%(added)d icons added", report.added) % {"added": report.added}
        if report.ignored:
            text += _(", %(ignored)d ignored (unreadable, too large or already present).") % {"ignored": report.ignored}
        self._on_message(_("“%s” Is Ready") % report.name, text)
        self._on_changed()
        return False
