from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ..i18n import _
from ..iconsets import display_name
from ..service import MAX_OFFSET, MAX_SIZE, MIN_SIZE
from ..textglyph import MAX_CHARACTERS
from .draft import Draft
from .format import display_path, folder_name, from_percent, percent_text, to_percent

BIG_SIZE = 144
SMALL_SIZES = (64, 48, 32)
COLOR_MODES = ("auto", "accent", "custom")
MAX_TEXT_LENGTH = MAX_CHARACTERS
SLIDER_WIDTH = 150
SLIDER_STEP = 0.5
SLIDER_PAGE = 5
SLIDER_DIGITS = 1


def hex_from_rgba(color: Gdk.RGBA) -> str:
    return "#%02x%02x%02x" % tuple(round(channel * 255) for channel in (color.red, color.green, color.blue))


def rgba_from_hex(value: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(value)
    return color


class Slider:
    def __init__(self, title: str, lower: float, upper: float, value: float, marks: tuple[float, ...] = ()):
        self.row = Adw.ActionRow(title=title, use_markup=False)
        self.adjustment = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=SLIDER_STEP, page_increment=SLIDER_PAGE)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adjustment)
        self.scale.set_size_request(SLIDER_WIDTH, -1)
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
        self.row.add_suffix(self.scale)
        self.row.add_suffix(self.label)

    def set_value(self, value: float) -> None:
        self.adjustment.set_value(value)
        self.label.set_text(percent_text(from_percent(value)))

    def connect_changed(self, callback) -> None:
        def handler(adjustment):
            value = adjustment.get_value()
            snapped = round(value / SLIDER_STEP) * SLIDER_STEP
            if snapped != value:
                adjustment.set_value(snapped)
                return
            self.label.set_text(percent_text(from_percent(value)))
            callback(value)

        self.adjustment.connect("value-changed", handler)


class Editor(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_change = None
        self.on_apply = None
        self.on_remove = None
        self.on_choose_glyph = None
        self.on_browse_glyph = None
        self.on_banner_action = None
        self.on_apply_style = None
        self.on_delete_style = None
        self.on_save_style = None
        self._draft: Draft | None = None
        self._loading = False
        self._existing = False
        self._blocked = False
        self._status = "ok"

        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", lambda *_: self.on_banner_action and self.on_banner_action())
        self.append(self.banner)

        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=640, tightening_threshold=480, margin_top=18, margin_bottom=18, margin_start=12, margin_end=12)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp.set_child(column)
        scrolled.set_child(clamp)
        self.append(scrolled)

        column.append(self._build_preview())
        column.append(self._build_glyph_group())
        column.append(self._build_appearance_group())
        self.append(self._build_action_bar())

    def _build_preview(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        card.add_css_class("card")
        inner = Adw.WrapBox(child_spacing=24, line_spacing=12, margin_top=16, margin_bottom=16, margin_start=20, margin_end=20)
        self.big = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=False)
        self.big.set_size_request(BIG_SIZE, BIG_SIZE)
        self.big.set_valign(Gtk.Align.CENTER)
        inner.append(self.big)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        self.name_label = Gtk.Label(xalign=0.0, wrap=True, ellipsize=3)
        self.name_label.add_css_class("title-2")
        self.path_label = Gtk.Label(xalign=0.0, ellipsize=2)
        self.path_label.add_css_class("dim-label")
        info.append(self.name_label)
        info.append(self.path_label)

        sizes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, margin_top=12, valign=Gtk.Align.END)
        self.small_pictures = []
        for size in SMALL_SIZES:
            figure = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.END)
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=False)
            picture.set_size_request(size, size)
            caption = Gtk.Label(label=_("%d px") % size)
            caption.add_css_class("caption")
            caption.add_css_class("dim-label")
            figure.append(picture)
            figure.append(caption)
            sizes.append(figure)
            self.small_pictures.append((size, picture))
        info.append(sizes)

        self.preview_note = Gtk.Label(xalign=0.0, wrap=True, margin_top=8)
        self.preview_note.add_css_class("caption")
        self.preview_note.add_css_class("dim-label")
        info.append(self.preview_note)
        inner.append(info)
        card.append(inner)
        return card

    def _build_glyph_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=_("Pictogram"))
        self.system_row = Adw.ActionRow(title=_("System Icon"), use_markup=False)
        choose = Gtk.Button(label=_("Choose…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self.on_choose_glyph and self.on_choose_glyph())
        self.system_row.add_suffix(choose)
        self.system_row.set_activatable_widget(choose)
        group.add(self.system_row)

        self.file_row = Adw.ActionRow(title=_("SVG or PNG File"), use_markup=False)
        browse = Gtk.Button(label=_("Browse…"), valign=Gtk.Align.CENTER)
        browse.connect("clicked", lambda *_: self.on_browse_glyph and self.on_browse_glyph())
        self.file_row.add_suffix(browse)
        self.file_row.set_activatable_widget(browse)
        group.add(self.file_row)

        self.text_row = Adw.EntryRow(title=_("Letters or Symbols"))
        self.text_row.connect("changed", self._on_text_changed)
        group.add(self.text_row)

        self.tint_row = Adw.SwitchRow(title=_("Tint the Pictogram"), subtitle=_("For a file: applies the chosen color"), use_markup=False)
        self.tint_row.connect("notify::active", self._on_tint)
        group.add(self.tint_row)
        return group

    def _build_appearance_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=_("Appearance"))
        self.reset_button = Gtk.Button(label=_("Reset to Defaults"), valign=Gtk.Align.CENTER)
        self.reset_button.add_css_class("flat")
        self.reset_button.connect("clicked", self._on_reset_appearance)
        suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        suffix.append(self._build_styles_button())
        suffix.append(self.reset_button)
        group.set_header_suffix(suffix)

        self.color_row = Adw.ComboRow(
            title=_("Color"),
            model=Gtk.StringList.new([_("Automatic"), _("System Accent"), _("Custom")]),
            use_markup=False,
        )
        self.color_row.connect("notify::selected", self._on_color_mode)
        self.swatch = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.swatch.connect("notify::rgba", self._on_color)
        self.color_row.add_prefix(self.swatch)
        group.add(self.color_row)

        self.size_slider = Slider(_("Size"), to_percent(MIN_SIZE), to_percent(MAX_SIZE), 100)
        self.size_slider.connect_changed(self._on_size)
        self.x_slider = Slider(_("Horizontal Position"), -to_percent(MAX_OFFSET), to_percent(MAX_OFFSET), 0, marks=(0,))
        self.x_slider.connect_changed(self._on_offset_x)
        self.y_slider = Slider(_("Vertical Position"), -to_percent(MAX_OFFSET), to_percent(MAX_OFFSET), 0, marks=(0,))
        self.y_slider.connect_changed(self._on_offset_y)
        for slider in (self.size_slider, self.x_slider, self.y_slider):
            group.add(slider.row)
        return group

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
        self.styles_button = Gtk.MenuButton(label=_("Styles"), popover=self.styles_popover, valign=Gtk.Align.CENTER)
        self.styles_button.add_css_class("flat")
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
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_bottom=12, margin_start=12, margin_end=12)
        bar.add_css_class("toolbar")
        clamp = Adw.Clamp(maximum_size=640, tightening_threshold=480)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hint = Gtk.Label(
            label=_("Nautilus does not refresh its icons by itself: after applying, press F5 in the Nautilus window."),
            wrap=True,
            xalign=0.0,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        inner.append(hint)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.remove_button = Gtk.Button(label=_("Remove Icon"))
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.add_css_class("flat")
        self.remove_button.connect("clicked", lambda *_: self.on_remove and self.on_remove())
        self.apply_button = Gtk.Button(label=_("Apply"), hexpand=True, halign=Gtk.Align.END)
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda *_: self.on_apply and self.on_apply())
        buttons.append(self.remove_button)
        buttons.append(self.apply_button)
        inner.append(buttons)
        clamp.set_child(inner)
        bar.append(clamp)
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
            self.remove_button.set_label(remove_label or _("Remove Icon"))
            self.remove_button.set_visible(existing or remove_label is not None)
            if existing:
                self.remove_button.add_css_class("destructive-action")
            else:
                self.remove_button.remove_css_class("destructive-action")
        finally:
            self._loading = False
        self._refresh_actions()

    def _show_widgets(self, draft: Draft) -> None:
        self.system_row.set_subtitle(display_name(draft.glyph_name) if draft.glyph_name else _("No pictogram chosen"))
        self.file_row.set_subtitle(Path(draft.glyph_file).name if draft.glyph_file else _("No file chosen"))
        if self.text_row.get_text() != (draft.glyph_text or ""):
            self.text_row.set_text(draft.glyph_text or "")
        self.tint_row.set_active(draft.tint)
        self.tint_row.set_sensitive(bool(draft.glyph_file))
        self.color_row.set_selected(COLOR_MODES.index(draft.color_mode))
        self.swatch.set_rgba(rgba_from_hex(draft.color))
        self.swatch.set_sensitive(draft.color_mode == "custom")
        self.size_slider.set_value(to_percent(draft.size))
        self.x_slider.set_value(to_percent(draft.offset_x))
        self.y_slider.set_value(to_percent(draft.offset_y))
        self.reset_button.set_sensitive(not draft.has_default_appearance())

    def update_glyph(self, draft: Draft) -> None:
        self._loading = True
        try:
            self._draft = draft
            self._show_widgets(draft)
        finally:
            self._loading = False
        self._refresh_actions()
        self._emit_change()

    def set_computed_color(self, color: str | None) -> None:
        mode = self._draft.color_mode if self._draft else "auto"
        self.color_row.set_subtitle(self._color_subtitle(mode, color))
        if color and mode != "custom":
            self._loading = True
            try:
                self.swatch.set_rgba(rgba_from_hex(color))
            finally:
                self._loading = False

    @staticmethod
    def _color_subtitle(mode: str, color: str | None) -> str:
        if mode == "accent":
            return _("The accent color of your system (%s)") % color if color else _("The accent color of your system")
        if mode == "auto":
            return _("A darker shade of the folder color (%s)") % color if color else _("A darker shade of the folder color")
        return ""

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

    def _on_text_changed(self, row) -> None:
        if self._loading or self._draft is None:
            return
        text = row.get_text()
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
            row.set_text(text)
            return
        self._loading = True
        try:
            self._draft = self._draft.with_glyph_text(text)
            self.system_row.set_subtitle(_("No pictogram chosen"))
            self.file_row.set_subtitle(_("No file chosen"))
            self.tint_row.set_sensitive(False)
        finally:
            self._loading = False
        self._emit_change()

    def _on_tint(self, row, _param) -> None:
        self._update(tint=row.get_active())

    def _on_color_mode(self, row, _param) -> None:
        if self._loading or self._draft is None:
            return
        mode = COLOR_MODES[row.get_selected()]
        self.swatch.set_sensitive(mode == "custom")
        if mode == "custom":
            self._update(color_mode=mode, color=hex_from_rgba(self.swatch.get_rgba()))
        else:
            self._update(color_mode=mode)

    def _on_color(self, button, _param) -> None:
        self._update(color=hex_from_rgba(button.get_rgba()))

    def _on_size(self, value: float) -> None:
        self._update(size=from_percent(value))

    def _on_offset_x(self, value: float) -> None:
        self._update(offset_x=from_percent(value))

    def _on_offset_y(self, value: float) -> None:
        self._update(offset_y=from_percent(value))

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
