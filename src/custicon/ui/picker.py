import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..i18n import _, ngettext
from .. import iconsets
from ..keywords import PHOSPHOR_KEYWORDS, english_fragments
from ..themes import IconTheme

COLUMNS = 8
CELL_SIZE = 24
SEARCH_DELAY_MS = 150
RESULT_LIMIT = 200


class PickerDialog(Adw.Dialog):
    def __init__(self, theme: IconTheme, current: str | None, on_chosen, icon_sets: iconsets.IconSets | None = None):
        super().__init__(title=_("Choose a Pictogram"), content_width=640, content_height=560)
        self._theme = theme
        self._sets = icon_sets or iconsets.default_sets()
        current_set = self._sets.for_name(current)
        self._active = current_set.key if current_set else "theme"
        self._on_chosen = on_chosen
        self._selected = current
        self._timer = 0
        self._names: list[str] = []

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6, margin_bottom=12, margin_start=16, margin_end=16)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search by name, in English or French (example: camera, musique)"))
        self.search.connect("search-changed", self._on_search_changed)
        box.append(self.search)

        self.set_toggle = Adw.ToggleGroup(halign=Gtk.Align.CENTER)
        self.set_toggle.add(Adw.Toggle(name="theme", label=_("Icon theme")))
        for key, label in (("phosphor", "Phosphor"), ("emoji", _("Emoji")), ("brands", _("Brands"))):
            self.set_toggle.add(Adw.Toggle(name=key, label=label))
        self.set_toggle.set_active_name(self._active)
        self.set_toggle.connect("notify::active-name", self._on_set_changed)
        box.append(self.set_toggle)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            max_children_per_line=COLUMNS,
            min_children_per_line=3,
            homogeneous=True,
            column_spacing=6,
            row_spacing=6,
            valign=Gtk.Align.START,
        )
        self.flow.connect("selected-children-changed", self._on_selection_changed)
        self.flow.connect("child-activated", self._on_activated)
        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(self.flow)
        box.append(scrolled)

        self.status = Gtk.Label(xalign=0.0)
        self.status.add_css_class("caption")
        self.status.add_css_class("dim-label")
        box.append(self.status)

        self.choose_button = Gtk.Button(label=_("Choose"), halign=Gtk.Align.END)
        self.choose_button.add_css_class("suggested-action")
        self.choose_button.connect("clicked", self._on_choose)
        box.append(self.choose_button)

        view.set_content(box)
        self.set_child(view)
        self.set_focus(self.search)
        self._populate("")

    def _on_set_changed(self, *_args) -> None:
        self._active = self.set_toggle.get_active_name() or "theme"
        self._populate(self.search.get_text())

    def _label(self, name: str) -> str:
        icon_set = self._sets.for_name(name)
        return icon_set.label(name, iconsets.current_language()) if icon_set and icon_set.key != "phosphor" else name

    def _source(self):
        return self._sets[self._active] if self._active in list(self._sets) else self._theme

    def _on_search_changed(self, _entry) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(SEARCH_DELAY_MS, self._search_now)

    def _search_now(self) -> bool:
        self._timer = 0
        self._populate(self.search.get_text())
        return False

    def _populate(self, query: str) -> None:
        child = self.flow.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.flow.remove(child)
            child = following
        self._names = self._search(query)
        for name in self._names:
            image = Gtk.Image.new_from_paintable(self._source().paintable(name, CELL_SIZE))
            image.set_pixel_size(CELL_SIZE)
            image.set_margin_top(14)
            image.set_margin_bottom(14)
            cell = Gtk.FlowBoxChild()
            cell.set_child(image)
            cell.set_tooltip_text(self._label(name))
            cell.icon_name = name
            self.flow.append(cell)
            if name == self._selected:
                self.flow.select_child(cell)
        if self._names:
            count = len(self._names)
            self.status.set_text(self._label(self._selected) if self._selected else ngettext("%d pictogram found", "%d pictograms found", count) % count)
        else:
            self.status.set_text(_("No pictogram found"))
        self.choose_button.set_sensitive(bool(self._selected))

    def _fragments(self, query: str) -> list[str]:
        if self._active == "phosphor":
            return english_fragments(query, PHOSPHOR_KEYWORDS)
        if self._active == "theme":
            return english_fragments(query)
        return []

    def _search(self, query: str) -> list[str]:
        source = self._source()
        names = source.symbolic_names(query, limit=RESULT_LIMIT)
        for fragment in self._fragments(query):
            if len(names) >= RESULT_LIMIT:
                break
            names.extend(name for name in source.symbolic_names(fragment, limit=RESULT_LIMIT) if name not in names)
        return names[:RESULT_LIMIT]

    def _on_selection_changed(self, _flow) -> None:
        children = self.flow.get_selected_children()
        if children:
            self._selected = children[0].icon_name
            self.status.set_text(self._label(self._selected))
        self.choose_button.set_sensitive(bool(self._selected))

    def _on_activated(self, _flow, child) -> None:
        self._selected = child.icon_name
        self._on_choose(None)

    def _on_choose(self, _button) -> None:
        if self._selected:
            self._on_chosen(self._selected)
            self.close()
