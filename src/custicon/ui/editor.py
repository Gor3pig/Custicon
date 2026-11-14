from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango

from .. import iconsets
from ..i18n import _, ngettext
from ..service import MAX_OFFSET, MAX_SIZE, MIN_SIZE
from ..textglyph import MAX_CHARACTERS
from .draft import Draft
from .format import display_path, folder_name, from_percent, percent_text, to_percent
from .palette import Palette, hex_of, rgba_of

BIG_SIZE = 250
SMALL_SIZES = (64, 48, 32)
MAX_TEXT_LENGTH = MAX_CHARACTERS
SLIDER_STEP = 0.5
SLIDER_PAGE = 5
SLIDER_DIGITS = 1
OFFSET_STEP = 0.005
SCROLL_FACTOR = 1.06
SUGGESTION_SIZE = 22
NARROW_CONDITION = "max-width: 600sp"
LABEL_WIDTH = 9


def hex_from_rgba(color: Gdk.RGBA) -> str:
    return hex_of(color)


def rgba_from_hex(value: str) -> Gdk.RGBA:
    return rgba_of(value)


def snapped(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, round(value / OFFSET_STEP) * OFFSET_STEP))


class Slider:
    def __init__(self, title: str, lower: float, upper: float, value: float, marks: tuple[float, ...] = ()):
        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.title = Gtk.Label(label=title, xalign=0.0, width_chars=LABEL_WIDTH)
        self.title.add_css_class("heading")
        self.adjustment = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=SLIDER_STEP, page_increment=SLIDER_PAGE)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adjustment)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale.set_hexpand(True)
        self.scale.set_digits(SLIDER_DIGITS)
        self.scale.set_draw_value(False)
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        for mark in marks:
            self.scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        self.label = Gtk.Label(xalign=1.0, width_chars=7)
        self.label.add_css_class("dim-label")
        self.label.add_css_class("numeric")
        self.row.append(self.title)
        self.row.append(self.scale)
        self.row.append(self.label)

    def set_value(self, value: float) -> None:
        self.adjustment.set_value(value)
        self.label.set_text(percent_text(from_percent(value)))

    def connect_changed(self, callback) -> None:
        def handler(adjustment):
            value = adjustment.get_value()
            rounded = round(value / SLIDER_STEP) * SLIDER_STEP
            if rounded != value:
                adjustment.set_value(rounded)
                return
            self.label.set_text(percent_text(from_percent(value)))
            callback(value)

        self.adjustment.connect("value-changed", handler)


def labelled_row(title: str, widget: Gtk.Widget) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
    label = Gtk.Label(label=title, xalign=0.0, width_chars=LABEL_WIDTH, wrap=True, valign=Gtk.Align.CENTER)
    label.add_css_class("heading")
    widget.set_hexpand(True)
    row.append(label)
    row.append(widget)
    return row


def card_of(rows: list[Gtk.Widget]) -> Gtk.Widget:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("card")
    for index, row in enumerate(rows):
        if index:
            card.append(Gtk.Separator())
        card.append(row)
    return card


class Editor(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_change = None
        self.on_apply = None
        self.on_remove = None
        self.on_choose_glyph = None
        self.on_browse_glyph = None
        self.on_pick_suggestion = None
        self.on_banner_action = None
        self.on_apply_style = None
        self.on_delete_style = None
        self.on_save_style = None
        self._draft: Draft | None = None
        self._loading = False
        self._existing = False
        self._blocked = False
        self._status = "ok"
        self._drag_origin = None
        self.suggestion_names: list[str] = []

        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", lambda *_: self.on_banner_action and self.on_banner_action())
        self.append(self.banner)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, margin_top=14, margin_bottom=12, margin_start=16, margin_end=16, valign=Gtk.Align.START)
        columns.append(self._build_preview())
        columns.append(self._build_controls())
        self.columns = columns
        clamp = Adw.Clamp(maximum_size=960, tightening_threshold=720, child=columns)
        breakpoint_bin = Adw.BreakpointBin(width_request=300, height_request=200)
        breakpoint_bin.set_child(clamp)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW_CONDITION))
        narrow.add_setter(columns, "orientation", Gtk.Orientation.VERTICAL)
        breakpoint_bin.add_breakpoint(narrow)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scrolled.set_child(breakpoint_bin)
        self.scrolled = scrolled
        self.append(scrolled)
        self.append(self._build_action_bar())

    def _build_preview(self) -> Gtk.Widget:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.START)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        stage = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=14, margin_bottom=4, margin_start=14, margin_end=14)
        self.big = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=False)
        self.big.set_size_request(BIG_SIZE, BIG_SIZE)
        self.big.set_cursor_from_name("grab")
        self.big.set_tooltip_text(_("Drag the pictogram to move it, scroll to resize it"))
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.big.add_controller(drag)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_scroll)
        self.big.add_controller(wheel)
        stage.append(self.big)
        card.append(stage)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=14, margin_end=14)
        self.name_label = Gtk.Label(xalign=0.0, wrap=True, ellipsize=Pango.EllipsizeMode.END)
        self.name_label.add_css_class("title-3")
        self.path_label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.path_label.add_css_class("dim-label")
        self.path_label.add_css_class("caption")
        info.append(self.name_label)
        info.append(self.path_label)
        card.append(info)

        sizes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, margin_top=10, margin_bottom=12, margin_start=14, margin_end=14, valign=Gtk.Align.END)
        self.small_pictures = []
        for size in SMALL_SIZES:
            figure = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.END)
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=False)
            picture.set_size_request(size, size)
            caption = Gtk.Label(label=_("%d px") % size)
            caption.add_css_class("caption")
            caption.add_css_class("dim-label")
            figure.append(picture)
            figure.append(caption)
            sizes.append(figure)
            self.small_pictures.append((size, picture))
        card.append(sizes)
        column.append(card)

        self.preview_note = Gtk.Label(xalign=0.0, wrap=True)
        self.preview_note.add_css_class("caption")
        self.preview_note.add_css_class("dim-label")
        column.append(self.preview_note)
        return column

    def _build_controls(self) -> Gtk.Widget:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True, valign=Gtk.Align.START)

        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.glyph_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.glyph_label.add_css_class("heading")
        choose = Gtk.Button(label=_("Choose…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self.on_choose_glyph and self.on_choose_glyph())
        self.choose_button = choose
        chip.append(self.glyph_label)
        chip.append(choose)

        self.suggestions_box = Adw.WrapBox(child_spacing=6, line_spacing=6, hexpand=True)
        self.suggestions_row = labelled_row(_("Suggestions"), self.suggestions_box)
        self.suggestions_row.set_visible(False)

        self.folder_tint_palette = Palette(_("Folder Tint"), "none", self._on_folder_palette)
        self.color_palette = Palette(_("Pictogram Color"), "auto", self._on_color_palette)
        column.append(
            card_of(
                [
                    labelled_row(_("Pictogram"), chip),
                    self.suggestions_row,
                    labelled_row(_("Folder Tint"), self.folder_tint_palette),
                    labelled_row(_("Pictogram Color"), self.color_palette),
                ]
            )
        )

        self.size_slider = Slider(_("Size"), to_percent(MIN_SIZE), to_percent(MAX_SIZE), 100)
        self.size_slider.connect_changed(self._on_size)
        self.x_slider = Slider(_("Horizontal"), -to_percent(MAX_OFFSET), to_percent(MAX_OFFSET), 0, marks=(0,))
        self.x_slider.connect_changed(self._on_offset_x)
        self.y_slider = Slider(_("Vertical"), -to_percent(MAX_OFFSET), to_percent(MAX_OFFSET), 0, marks=(0,))
        self.y_slider.connect_changed(self._on_offset_y)
        column.append(card_of([self.size_slider.row, self.x_slider.row, self.y_slider.row]))

        sources = Adw.WrapBox(child_spacing=8, line_spacing=8)
        browse = Gtk.Button(label=_("SVG or PNG File…"))
        browse.connect("clicked", lambda *_: self.on_browse_glyph and self.on_browse_glyph())
        self.browse_button = browse
        self.letters_button = Gtk.MenuButton(label=_("Letters or Symbols…"), popover=self._build_letters_popover())
        self.tint_row = Gtk.CheckButton(label=_("Tint the Pictogram"), tooltip_text=_("For a file: applies the chosen color"))
        self.tint_row.connect("notify::active", self._on_tint)
        sources.append(browse)
        sources.append(self.letters_button)
        sources.append(self.tint_row)
        column.append(sources)
        return column

    def _build_letters_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        hint = Gtk.Label(label=_("Type 1 to 3 characters"), xalign=0.0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        self.text_row = Gtk.Entry(max_length=0, width_chars=12, placeholder_text=_("Letters or Symbols"))
        self.text_row.connect("changed", self._on_text_changed)
        box.append(hint)
        box.append(self.text_row)
        return Gtk.Popover(child=box)

    def _build_styles_button(self) -> Gtk.Widget:
        self.styles_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.styles_list.add_css_class("navigation-sidebar")
        placeholder = Gtk.Label(label=_("No saved styles yet"), margin_top=12, margin_bottom=12)
        placeholder.add_css_class("dim-label")
        self.styles_list.set_placeholder(placeholder)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, max_content_height=240, min_content_width=260)
        scrolled.set_child(self.styles_list)
        save = Gtk.Button(label=_("Save Current Appearance…"))
        save.connect("clicked", self._on_save_style_clicked)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(scrolled)
        box.append(save)
        self.styles_popover = Gtk.Popover(child=box)
        self.styles_button = Gtk.MenuButton(label=_("Styles"), popover=self.styles_popover, direction=Gtk.ArrowType.UP)
        return self.styles_button

    def set_styles(self, names: list[str]) -> None:
        child = self.styles_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.styles_list.remove(child)
            child = following
        for name in names:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            choose = Gtk.Button(label=name, hexpand=True, halign=Gtk.Align.FILL)
            choose.add_css_class("flat")
            choose.get_child().set_halign(Gtk.Align.START)
            choose.connect("clicked", self._on_style_chosen, name)
            delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Delete Style"))
            delete.add_css_class("flat")
            delete.update_property([Gtk.AccessibleProperty.LABEL], [_("Delete Style")])
            delete.connect("clicked", self._on_style_deleted, name)
            row.append(choose)
            row.append(delete)
            self.styles_list.append(row)

    def _on_style_chosen(self, _button, name: str) -> None:
        self.styles_popover.popdown()
        if self.on_apply_style:
            self.on_apply_style(name)

    def _on_style_deleted(self, _button, name: str) -> None:
        if self.on_delete_style:
            self.on_delete_style(name)

    def _on_save_style_clicked(self, _button) -> None:
        self.styles_popover.popdown()
        if self.on_save_style:
            self.on_save_style()

    def _build_action_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=10, margin_start=16, margin_end=16)
        bar.add_css_class("toolbar")
        hint = Gtk.Label(
            label=_("Nautilus does not refresh its icons by itself: after applying, press F5 in the Nautilus window."),
            wrap=True,
            xalign=0.0,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        bar.append(hint)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.remove_button = Gtk.Button(label=_("Remove Icon"))
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.add_css_class("flat")
        self.remove_button.connect("clicked", lambda *_: self.on_remove and self.on_remove())
        spacer = Gtk.Box(hexpand=True)
        self.reset_button = Gtk.Button(label=_("Reset to Defaults"))
        self.reset_button.connect("clicked", self._on_reset_appearance)
        self.apply_button = Gtk.Button(label=_("Apply"))
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda *_: self.on_apply and self.on_apply())
        buttons.append(self.remove_button)
        buttons.append(spacer)
        buttons.append(self._build_styles_button())
        buttons.append(self.reset_button)
        buttons.append(self.apply_button)
        bar.append(buttons)
        return bar

    @property
    def draft(self) -> Draft | None:
        return self._draft

    def load(self, draft: Draft, existing: bool, status: str = "ok", banner_text: str | None = None, banner_action: str | None = None, remove_label: str | None = None) -> None:
        self._loading = True
        try:
            self._existing = existing
            self._status = status
            self._draft = draft
            self.name_label.set_text(folder_name(draft.folder))
            self.path_label.set_text(display_path(draft.folder))
            self.path_label.set_tooltip_text(str(draft.folder))
            self._show_widgets(draft)
            self.banner.set_title(banner_text or "")
            self.banner.set_button_label(banner_action)
            self.banner.set_revealed(bool(banner_text))
            self.apply_button.set_label(_("Apply"))
            self.remove_button.set_label(remove_label or _("Remove Icon"))
            self.remove_button.set_visible(existing or remove_label is not None)
            if existing:
                self.remove_button.add_css_class("destructive-action")
            else:
                self.remove_button.remove_css_class("destructive-action")
        finally:
            self._loading = False
        self._refresh_actions()

    def show_batch(self, names: list[str]) -> None:
        count = len(names)
        self.name_label.set_text(ngettext("%d folder", "%d folders", count) % count)
        self.path_label.set_text(", ".join(names))
        self.path_label.set_tooltip_text("\n".join(names))
        self.apply_button.set_label(ngettext("Apply to %d Folder", "Apply to %d Folders", count) % count)
        self.remove_button.set_visible(False)

    def glyph_text(self, draft: Draft) -> str:
        if draft.glyph_name:
            return iconsets.display_name(draft.glyph_name, iconsets.current_language())
        if draft.glyph_file:
            return Path(draft.glyph_file).name
        if draft.glyph_text:
            return "“%s”" % draft.glyph_text
        return _("No pictogram chosen")

    def _show_widgets(self, draft: Draft) -> None:
        self.glyph_label.set_text(self.glyph_text(draft))
        if self.text_row.get_text() != (draft.glyph_text or ""):
            self.text_row.set_text(draft.glyph_text or "")
        self.tint_row.set_active(draft.tint)
        self.tint_row.set_sensitive(bool(draft.glyph_file))
        self.folder_tint_palette.set_state(draft.folder_tint_mode, draft.folder_tint)
        self.color_palette.set_state(draft.color_mode, draft.color)
        self._show_sliders(draft)
        self.reset_button.set_sensitive(not draft.has_default_appearance())

    def _show_sliders(self, draft: Draft) -> None:
        self.size_slider.set_value(to_percent(draft.size))
        self.x_slider.set_value(to_percent(draft.offset_x))
        self.y_slider.set_value(to_percent(draft.offset_y))

    def update_glyph(self, draft: Draft) -> None:
        self._loading = True
        try:
            self._draft = draft
            self._show_widgets(draft)
        finally:
            self._loading = False
        self._refresh_actions()
        self._emit_change()

    def set_accent_color(self, color: str) -> None:
        self.folder_tint_palette.set_accent(color)
        self.color_palette.set_accent(color)

    def set_computed_folder_tint(self, color: str | None) -> None:
        self.folder_tint_palette.set_computed(color)

    def set_computed_color(self, color: str | None) -> None:
        self.color_palette.set_computed(color)

    def set_suggestions(self, items: list) -> None:
        child = self.suggestions_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.suggestions_box.remove(child)
            child = following
        self.suggestion_names = [name for name, _paintable, _label in items]
        for name, paintable, label in items:
            button = Gtk.Button(tooltip_text=label)
            button.add_css_class("flat")
            image = Gtk.Image(pixel_size=SUGGESTION_SIZE)
            image.set_from_paintable(paintable)
            button.set_child(image)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", lambda _button, chosen=name: self.on_pick_suggestion and self.on_pick_suggestion(chosen))
            self.suggestions_box.append(button)
        self.suggestions_row.set_visible(bool(items))

    def set_preview(self, big, smalls: list, note: str) -> None:
        self.big.set_paintable(big)
        for (_size, picture), texture in zip(self.small_pictures, smalls):
            picture.set_paintable(texture)
        self.preview_note.set_text(note)

    def set_blocked(self, blocked: bool) -> None:
        self._blocked = blocked
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        draft = self._draft
        usable = draft is not None and draft.ready and self._status != "folder-missing" and not self._blocked
        self.apply_button.set_sensitive(usable)
        if draft is not None:
            self.reset_button.set_sensitive(not draft.has_default_appearance())

    def _emit_change(self) -> None:
        self._refresh_actions()
        if self.on_change and self._draft is not None:
            self.on_change(self._draft)

    def _update(self, **changes) -> None:
        if self._loading or self._draft is None:
            return
        self._draft = replace(self._draft, **changes)
        self._emit_change()

    def _on_text_changed(self, entry) -> None:
        if self._loading or self._draft is None:
            return
        text = entry.get_text()
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
            entry.set_text(text)
            return
        self._loading = True
        try:
            self._draft = self._draft.with_glyph_text(text)
            self.glyph_label.set_text(self.glyph_text(self._draft))
            self.tint_row.set_sensitive(False)
        finally:
            self._loading = False
        self._emit_change()

    def _on_tint(self, row, _param) -> None:
        self._update(tint=row.get_active())

    def _on_folder_palette(self, mode: str, color: str | None) -> None:
        if color is None:
            self._update(folder_tint_mode=mode)
        else:
            self._update(folder_tint_mode=mode, folder_tint=color)

    def _on_color_palette(self, mode: str, color: str | None) -> None:
        if color is None:
            self._update(color_mode=mode)
        else:
            self._update(color_mode=mode, color=color)

    def _on_size(self, value: float) -> None:
        self._update(size=from_percent(value))

    def _on_offset_x(self, value: float) -> None:
        self._update(offset_x=from_percent(value))

    def _on_offset_y(self, value: float) -> None:
        self._update(offset_y=from_percent(value))

    def _apply_direct(self, **changes) -> None:
        if self._draft is None:
            return
        self._update(**changes)
        self._loading = True
        try:
            self._show_sliders(self._draft)
        finally:
            self._loading = False
        self.reset_button.set_sensitive(not self._draft.has_default_appearance())

    def _on_drag_begin(self, _gesture, _x, _y) -> None:
        draft = self._draft
        self._drag_origin = (draft.offset_x, draft.offset_y) if draft is not None and draft.ready else None
        self.big.set_cursor_from_name("grabbing")

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._drag_origin is None:
            return
        origin_x, origin_y = self._drag_origin
        self._apply_direct(
            offset_x=snapped(origin_x + dx / BIG_SIZE, -MAX_OFFSET, MAX_OFFSET),
            offset_y=snapped(origin_y + dy / BIG_SIZE, -MAX_OFFSET, MAX_OFFSET),
        )

    def _on_drag_end(self, _gesture, _dx, _dy) -> None:
        self._drag_origin = None
        self.big.set_cursor_from_name("grab")

    def _on_scroll(self, _controller, _dx: float, dy: float) -> bool:
        draft = self._draft
        if draft is None or not draft.ready or not dy:
            return False
        self._apply_direct(size=snapped(draft.size * SCROLL_FACTOR ** (-dy), MIN_SIZE, MAX_SIZE))
        return True

    def _on_reset_appearance(self, _button) -> None:
        if self._draft is None:
            return
        self._draft = self._draft.with_default_appearance()
        self._loading = True
        try:
            self._show_widgets(self._draft)
        finally:
            self._loading = False
        self._emit_change()
