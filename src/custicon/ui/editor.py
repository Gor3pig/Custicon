from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ..i18n import _
from ..service import MAX_OFFSET, MAX_SIZE, MIN_SIZE
from .draft import Draft
from .format import display_path, folder_name, from_percent, percent_text, to_percent

BIG_SIZE = 144
SMALL_SIZES = (64, 48, 32)
SLIDER_WIDTH = 150


def hex_from_rgba(color: Gdk.RGBA) -> str:
    return "#%02x%02x%02x" % tuple(round(channel * 255) for channel in (color.red, color.green, color.blue))


def rgba_from_hex(value: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(value)
    return color


class Slider:
    def __init__(self, title: str, lower: float, upper: float, value: float, marks: tuple[float, ...] = ()):
        self.row = Adw.ActionRow(title=title, use_markup=False)
        self.adjustment = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=1, page_increment=10)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adjustment)
        self.scale.set_size_request(SLIDER_WIDTH, -1)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale.set_draw_value(False)
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        for mark in marks:
            self.scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        self.label = Gtk.Label(xalign=1.0, width_chars=5)
        self.label.add_css_class("dim-label")
        self.label.add_css_class("numeric")
        self.row.add_suffix(self.scale)
        self.row.add_suffix(self.label)

    def set_value(self, value: float) -> None:
        self.adjustment.set_value(value)
        self.label.set_text(percent_text(from_percent(value)))

    def connect_changed(self, callback) -> None:
        def handler(adjustment):
            self.label.set_text(percent_text(from_percent(adjustment.get_value())))
            callback(adjustment.get_value())

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

        self.tint_row = Adw.SwitchRow(title=_("Tint the Pictogram"), subtitle=_("For a file: applies the chosen color"), use_markup=False)
        self.tint_row.connect("notify::active", self._on_tint)
        group.add(self.tint_row)
        return group

    def _build_appearance_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=_("Appearance"))
        self.reset_button = Gtk.Button(label=_("Reset to Defaults"), valign=Gtk.Align.CENTER)
        self.reset_button.add_css_class("flat")
        self.reset_button.connect("clicked", self._on_reset_appearance)
        group.set_header_suffix(self.reset_button)

        self.auto_row = Adw.SwitchRow(title=_("Automatic Color"), use_markup=False)
        self.auto_row.connect("notify::active", self._on_auto_color)
        self.swatch = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.swatch.connect("notify::rgba", self._on_color)
        self.auto_row.add_prefix(self.swatch)
        group.add(self.auto_row)

        self.size_slider = Slider(_("Size"), to_percent(MIN_SIZE), to_percent(MAX_SIZE), 100)
        self.size_slider.connect_changed(self._on_size)
        self.x_slider = Slider(_("Horizontal Position"), -to_percent(MAX_OFFSET), to_percent(MAX_OFFSET), 0, marks=(0,))
        self.x_slider.connect_changed(self._on_offset_x)
        self.y_slider = Slider(_("Vertical Position"), -to_percent(MAX_OFFSET), to_percent(MAX_OFFSET), 0, marks=(0,))
        self.y_slider.connect_changed(self._on_offset_y)
        for slider in (self.size_slider, self.x_slider, self.y_slider):
            group.add(slider.row)
        return group

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
        self.system_row.set_subtitle(draft.glyph_name or _("No pictogram chosen"))
        self.file_row.set_subtitle(Path(draft.glyph_file).name if draft.glyph_file else _("No file chosen"))
        self.tint_row.set_active(draft.tint)
        self.tint_row.set_sensitive(bool(draft.glyph_file))
        self.auto_row.set_active(draft.auto_color)
        self.swatch.set_rgba(rgba_from_hex(draft.color))
        self.swatch.set_sensitive(not draft.auto_color)
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

    def set_auto_color_text(self, color: str | None) -> None:
        if color:
            self.auto_row.set_subtitle(_("A darker shade of the folder color (%s)") % color)
            if self._draft and self._draft.auto_color:
                self._loading = True
                try:
                    self.swatch.set_rgba(rgba_from_hex(color))
                finally:
                    self._loading = False
        else:
            self.auto_row.set_subtitle(_("A darker shade of the folder color"))

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

    def _on_tint(self, row, _param) -> None:
        self._update(tint=row.get_active())

    def _on_auto_color(self, row, _param) -> None:
        if self._loading or self._draft is None:
            return
        self.swatch.set_sensitive(not row.get_active())
        if row.get_active():
            self._update(auto_color=True)
        else:
            self._update(auto_color=False, color=hex_from_rgba(self.swatch.get_rgba()))

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
