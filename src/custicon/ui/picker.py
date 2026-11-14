import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from .. import iconsets
from ..errors import CusticonError
from ..i18n import _, ngettext
from ..keywords import PHOSPHOR_KEYWORDS, english_fragments
from ..suggestions import suggest
from ..themes import IconTheme

CELL_SIZE = 24
CELL_MARGIN = 10
SEARCH_DELAY_MS = 150
NO_LIMIT = 100_000
SIDEBAR_WIDTH = 200
NARROW_CONDITION = "max-width: 560sp"
CATEGORY_LABELS = {
    "arrows": _("Arrows"),
    "brand": _("Logos"),
    "commerce": _("Commerce"),
    "communication": _("Communication"),
    "design": _("Design"),
    "development": _("Development"),
    "editor": _("Editor"),
    "finance": _("Finance"),
    "games": _("Games"),
    "health": _("Health"),
    "map": _("Maps"),
    "media": _("Media"),
    "nature": _("Nature"),
    "objects": _("Objects"),
    "office": _("Office"),
    "people": _("People"),
    "system": _("System"),
    "weather": _("Weather"),
    "smileys": _("Smileys"),
    "food": _("Food"),
    "travel": _("Travel"),
    "activities": _("Activities"),
    "symbols": _("Symbols"),
    "flags": _("Flags"),
}


class PickerDialog(Adw.Dialog):
    def __init__(self, theme: IconTheme, current: str | None, on_chosen, icon_sets: iconsets.IconSets | None = None, folder_name: str = "", memory=None, on_favorite=None):
        super().__init__(title=_("Choose a Pictogram"), content_width=780, content_height=640)
        self._theme = theme
        self._sets = icon_sets or iconsets.default_sets()
        current_set = self._sets.for_name(current)
        self._active = current_set.key if current_set else "theme"
        self._on_chosen = on_chosen
        self._on_favorite = on_favorite or (lambda name, favorite: False)
        self._selected = current
        self._timer = 0
        self._names: list[str] = []
        self._category = None
        self._category_ids: list[str] = []
        self._loading = False
        self._folder_name = folder_name

        self.suggestion_names = [name for name in suggest(folder_name, self._sets) if self._exists(name)] if folder_name else []
        self.recent_names = [name for name in (memory.recent if memory else ()) if self._exists(name)]
        self.favorite_names = [name for name in (memory.favorites if memory else ()) if self._exists(name)]

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_args: self.close())
        header.pack_start(cancel)
        self.choose_button = Gtk.Button(label=_("Choose"))
        self.choose_button.add_css_class("suggested-action")
        self.choose_button.set_sensitive(False)
        self.choose_button.connect("clicked", self._on_choose)
        header.pack_end(self.choose_button)
        view.add_top_bar(header)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tools = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6, margin_bottom=8, margin_start=14, margin_end=14)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search by name, in English or French (example: camera, musique)"))
        self.search.connect("search-changed", self._on_search_changed)
        tools.append(self.search)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.set_toggle = Adw.ToggleGroup()
        self.set_toggle.add(Adw.Toggle(name="theme", label=_("Icon theme")))
        for key, label in (("phosphor", "Phosphor"), ("emoji", _("Emoji")), ("brands", _("Brands"))):
            self.set_toggle.add(Adw.Toggle(name=key, label=label))
        self.set_toggle.set_active_name(self._active)
        self.set_toggle.connect("notify::active-name", self._on_set_changed)
        row.append(self.set_toggle)
        self.category_row = Gtk.DropDown(model=Gtk.StringList(), valign=Gtk.Align.CENTER)
        self.category_row.connect("notify::selected", self._on_category_changed)
        row.append(self.category_row)
        self.status = Gtk.Label(xalign=1.0, hexpand=True)
        self.status.add_css_class("caption")
        self.status.add_css_class("dim-label")
        row.append(self.status)
        tools.append(row)
        page.append(tools)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self.sidebar = self._build_sidebar()
        body.append(self.sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.model = Gtk.StringList()
        self.selection = Gtk.SingleSelection(model=self.model, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected", self._on_selection_changed)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_cell)
        factory.connect("bind", self._bind_cell)
        self.grid = Gtk.GridView(model=self.selection, factory=factory, min_columns=4, max_columns=16)
        self.grid.connect("activate", lambda _grid, position: self._on_activated(position))
        scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(self.grid)
        body.append(scrolled)
        page.append(body)
        page.append(Gtk.Separator())
        page.append(self._build_footer())

        view.set_content(page)
        self.set_child(view)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW_CONDITION))
        narrow.add_setter(self.sidebar, "visible", False)
        self.add_breakpoint(narrow)
        self.set_focus(self.search)

        self._refresh_categories()
        self._fill_sidebar()
        self._set_selected(current)
        self._populate("")

    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        for attribute, title in (
            ("suggestions", _("Suggestions for “%s”") % self._folder_name),
            ("recent", _("Recent")),
            ("favorites", _("Favorites")),
        ):
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            heading = Gtk.Label(label=title, xalign=0.0, wrap=True)
            heading.add_css_class("caption-heading")
            heading.add_css_class("dim-label")
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=3, homogeneous=True, column_spacing=4, row_spacing=4)
            section.append(heading)
            section.append(flow)
            box.append(section)
            setattr(self, f"{attribute}_box", section)
            setattr(self, f"{attribute}_flow", flow)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=SIDEBAR_WIDTH)
        scrolled.set_child(box)
        return scrolled

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=8, margin_bottom=8, margin_start=14, margin_end=14)
        self.selection_image = Gtk.Image(pixel_size=28)
        self.selection_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.selection_label.add_css_class("heading")
        self.favorite_button = Gtk.ToggleButton(icon_name="starred-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Add to Favorites"), sensitive=False)
        self.favorite_button.add_css_class("flat")
        self.favorite_button.connect("toggled", self._on_favorite_toggled)
        footer.append(self.selection_image)
        footer.append(self.selection_label)
        footer.append(self.favorite_button)
        return footer

    def _exists(self, name: str) -> bool:
        icon_set = self._sets.for_name(name)
        try:
            if icon_set is not None:
                icon_set.file(name)
            else:
                self._theme.glyph_file(name)
        except CusticonError:
            return False
        return True

    def label_of(self, name: str) -> str:
        icon_set = self._sets.for_name(name)
        return icon_set.label(name, iconsets.current_language()) if icon_set else name

    def _paintable(self, name: str, size: int = CELL_SIZE):
        icon_set = self._sets.for_name(name)
        try:
            return icon_set.paintable(name, size) if icon_set else self._theme.paintable(name, size)
        except CusticonError:
            return None

    def _setup_cell(self, _factory, item) -> None:
        image = Gtk.Image(pixel_size=CELL_SIZE, margin_top=CELL_MARGIN, margin_bottom=CELL_MARGIN, margin_start=CELL_MARGIN, margin_end=CELL_MARGIN)
        item.set_child(image)

    def _bind_cell(self, _factory, item) -> None:
        name = item.get_item().get_string()
        image = item.get_child()
        image.set_from_paintable(self._paintable(name))
        image.set_tooltip_text(self.label_of(name))

    def _fill_flow(self, section: Gtk.Widget, flow: Gtk.FlowBox, names: list[str]) -> None:
        child = flow.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            flow.remove(child)
            child = following
        for name in names:
            button = Gtk.Button(tooltip_text=self.label_of(name))
            button.add_css_class("flat")
            image = Gtk.Image(pixel_size=CELL_SIZE)
            image.set_from_paintable(self._paintable(name))
            button.set_child(image)
            button.connect("clicked", lambda _button, chosen=name: self.pick_name(chosen))
            flow.append(button)
        section.set_visible(bool(names))

    def _fill_sidebar(self) -> None:
        self._fill_flow(self.suggestions_box, self.suggestions_flow, self.suggestion_names)
        self._fill_flow(self.recent_box, self.recent_flow, self.recent_names)
        self._fill_flow(self.favorites_box, self.favorites_flow, self.favorite_names)

    def _source(self):
        return self._sets[self._active] if self._active != "theme" else self._theme

    def _refresh_categories(self) -> None:
        self._loading = True
        try:
            identifiers = self._sets[self._active].categories() if self._active != "theme" else []
            labels = [CATEGORY_LABELS.get(identifier, identifier.capitalize()) for identifier in identifiers]
            model = self.category_row.get_model()
            model.splice(0, model.get_n_items(), [_("All")] + labels)
            self._category_ids = identifiers
            self._category = None
            self.category_row.set_selected(0)
            self.category_row.set_visible(bool(identifiers))
        finally:
            self._loading = False

    def _on_set_changed(self, *_args) -> None:
        self._active = self.set_toggle.get_active_name() or "theme"
        self._refresh_categories()
        self._populate(self.search.get_text())

    def _on_category_changed(self, *_args) -> None:
        if self._loading:
            return
        position = self.category_row.get_selected()
        self._category = self._category_ids[position - 1] if 0 < position <= len(self._category_ids) else None
        self._populate(self.search.get_text())

    def _on_search_changed(self, _entry) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(SEARCH_DELAY_MS, self._search_now)

    def _search_now(self) -> bool:
        self._timer = 0
        self._populate(self.search.get_text())
        return False

    def _fragments(self, query: str) -> list[str]:
        if self._active == "phosphor":
            return english_fragments(query, PHOSPHOR_KEYWORDS)
        if self._active == "theme":
            return english_fragments(query)
        return []

    def _search(self, query: str) -> list[str]:
        source = self._source()
        names = source.symbolic_names(query, limit=NO_LIMIT)
        for fragment in self._fragments(query):
            names.extend(name for name in source.symbolic_names(fragment, limit=NO_LIMIT) if name not in names)
        if self._category and self._active != "theme":
            members = set(source.names_in_category(self._category))
            names = [name for name in names if name in members]
        return names

    def _populate(self, query: str) -> None:
        self._names = self._search(query)
        self._loading = True
        try:
            self.model.splice(0, self.model.get_n_items(), self._names)
            if self._selected in self._names:
                self._show_in_grid(self._selected)
        finally:
            self._loading = False
        count = len(self._names)
        self.status.set_text(ngettext("%d pictogram found", "%d pictograms found", count) % count if count else _("No pictogram found"))

    def _show_in_grid(self, name: str) -> None:
        position = self._names.index(name)
        self.selection.set_selected(position)
        self.grid.scroll_to(position, Gtk.ListScrollFlags.NONE, None)

    def _set_selected(self, name: str | None) -> None:
        self._selected = name
        self.choose_button.set_sensitive(bool(name))
        self.favorite_button.set_sensitive(bool(name))
        self._loading = True
        try:
            self.favorite_button.set_active(bool(name) and name in self.favorite_names)
        finally:
            self._loading = False
        self.favorite_button.set_tooltip_text(_("Remove from Favorites") if self.favorite_button.get_active() else _("Add to Favorites"))
        self.selection_label.set_text(self.label_of(name) if name else "")
        self.selection_image.set_from_paintable(self._paintable(name, 28) if name else None)

    def selected_name(self) -> str | None:
        return self._selected

    def select(self, position: int) -> None:
        self.selection.set_selected(position)

    def pick_name(self, name: str) -> None:
        self._set_selected(name)
        if name in self._names:
            self._loading = True
            try:
                self._show_in_grid(name)
            finally:
                self._loading = False

    def _on_selection_changed(self, *_args) -> None:
        if self._loading:
            return
        position = self.selection.get_selected()
        if 0 <= position < len(self._names):
            self._set_selected(self._names[position])

    def _on_favorite_toggled(self, button) -> None:
        if self._loading or not self._selected:
            return
        name, wanted = self._selected, button.get_active()
        if not self._on_favorite(name, wanted):
            self._loading = True
            try:
                button.set_active(not wanted)
            finally:
                self._loading = False
            return
        others = [item for item in self.favorite_names if item != name]
        self.favorite_names = [name, *others] if wanted else others
        self._fill_flow(self.favorites_box, self.favorites_flow, self.favorite_names)
        button.set_tooltip_text(_("Remove from Favorites") if wanted else _("Add to Favorites"))

    def _on_activated(self, position: int) -> None:
        self.select(position)
        self._on_choose(None)

    def _on_choose(self, _button) -> None:
        if self._selected:
            self._on_chosen(self._selected)
            self.close()
