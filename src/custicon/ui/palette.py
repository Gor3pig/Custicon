import colorsys
import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ..color import is_hex_color
from ..i18n import _
from .colorpicker import ColorPickerDialog

SWATCH_SIZE = 24
DEFAULT_ACCENT = "#3584e4"
DEFAULT_CUSTOM = "#e5a50a"
PALETTE_COLORS = (
    ("#3584e4", _("Blue")),
    ("#2190a4", _("Teal")),
    ("#3a944a", _("Green")),
    ("#c88800", _("Yellow")),
    ("#ed5b00", _("Orange")),
    ("#e62d42", _("Red")),
    ("#d56199", _("Pink")),
    ("#9141ac", _("Purple")),
    ("#6f8396", _("Slate")),
)
SELECTED_CSS = """
.selected-swatch {
  outline: 2px solid @accent_bg_color;
  outline-offset: 2px;
  border-radius: 99px;
}
.palette-swatch {
  padding: 0;
  min-width: 24px;
  min-height: 24px;
  border-radius: 99px;
}"""
_css_ready = False


def ensure_css() -> None:
    global _css_ready
    display = Gdk.Display.get_default()
    if _css_ready or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(SELECTED_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_ready = True


def rgba_of(value: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(value)
    return color


def hex_of(color: Gdk.RGBA) -> str:
    return "#%02x%02x%02x" % tuple(round(channel * 255) for channel in (color.red, color.green, color.blue))


def draw_swatch(context, width: int, height: int, color: str | None, symbol: str | None) -> None:
    radius = min(width, height) / 2 - 1
    center_x, center_y = width / 2, height / 2
    context.arc(center_x, center_y, radius, 0, 2 * math.pi)
    if color is not None:
        red, green, blue = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
        context.set_source_rgb(red, green, blue)
        context.fill_preserve()
    context.set_source_rgba(0, 0, 0, 0.28)
    context.set_line_width(1)
    context.stroke()
    if symbol == "none":
        context.set_source_rgba(0.45, 0.45, 0.45, 1)
        context.set_line_width(2)
        context.move_to(center_x - radius * 0.55, center_y + radius * 0.55)
        context.line_to(center_x + radius * 0.55, center_y - radius * 0.55)
        context.stroke()
    elif symbol == "auto":
        context.set_source_rgba(0.35, 0.35, 0.35, 1)
        context.set_line_width(1.8)
        context.move_to(center_x - radius * 0.45, center_y + radius * 0.5)
        context.line_to(center_x, center_y - radius * 0.55)
        context.line_to(center_x + radius * 0.45, center_y + radius * 0.5)
        context.move_to(center_x - radius * 0.25, center_y + radius * 0.15)
        context.line_to(center_x + radius * 0.25, center_y + radius * 0.15)
        context.stroke()


RAINBOW_WEDGES = 72
WEDGE_OVERLAP = 0.08
CENTER_GAP = 0.55
CENTER_DOT = 0.42


def draw_rainbow(context, width: int, height: int, chosen: str | None) -> None:
    radius = min(width, height) / 2 - 1
    center_x, center_y = width / 2, height / 2
    step = 2 * math.pi / RAINBOW_WEDGES
    for index in range(RAINBOW_WEDGES):
        red, green, blue = colorsys.hsv_to_rgb(index / RAINBOW_WEDGES, 0.85, 0.95)
        context.set_source_rgb(red, green, blue)
        context.move_to(center_x, center_y)
        context.arc(center_x, center_y, radius, index * step, (index + 1) * step + WEDGE_OVERLAP)
        context.close_path()
        context.fill()
    context.arc(center_x, center_y, radius, 0, 2 * math.pi)
    context.set_source_rgba(0, 0, 0, 0.28)
    context.set_line_width(1)
    context.stroke()
    if chosen is not None:
        context.arc(center_x, center_y, radius * CENTER_GAP, 0, 2 * math.pi)
        context.set_source_rgb(1, 1, 1)
        context.fill()
        red, green, blue = (int(chosen[index : index + 2], 16) / 255 for index in (1, 3, 5))
        context.arc(center_x, center_y, radius * CENTER_DOT, 0, 2 * math.pi)
        context.set_source_rgb(red, green, blue)
        context.fill_preserve()
        context.set_source_rgba(0, 0, 0, 0.28)
        context.stroke()


class CustomSwatch(Gtk.Button):
    __gtype_name__ = "CusticonCustomSwatch"
    rgba = GObject.Property(type=Gdk.RGBA)

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.add_css_class("flat")
        self.add_css_class("palette-swatch")
        self._chosen = False
        self._area = Gtk.DrawingArea(content_width=SWATCH_SIZE, content_height=SWATCH_SIZE)
        self._area.set_draw_func(self._draw)
        self.set_child(self._area)
        self.connect("clicked", self._on_clicked)
        self.connect("notify::rgba", lambda *_args: self._area.queue_draw())

    def _draw(self, _area, context, width, height) -> None:
        draw_rainbow(context, width, height, hex_of(self.rgba) if self._chosen and self.rgba is not None else None)

    def set_rgba(self, color: Gdk.RGBA) -> None:
        self.rgba = color

    def get_rgba(self) -> Gdk.RGBA:
        return self.rgba

    def set_chosen(self, chosen: bool) -> None:
        if chosen != self._chosen:
            self._chosen = chosen
            self._area.queue_draw()

    def is_chosen(self) -> bool:
        return self._chosen

    def _on_clicked(self, _button) -> None:
        dialog = ColorPickerDialog(hex_of(self.rgba) if self.rgba is not None else DEFAULT_CUSTOM, lambda value: self.set_rgba(rgba_of(value)))
        root = self.get_root()
        dialog.present(root if isinstance(root, Gtk.Widget) else None)


class CustomChoice:
    def __init__(self, palette: "Palette"):
        self._palette = palette

    def get_active(self) -> bool:
        return self._palette.custom_is_active()

    def get_tooltip_text(self) -> str:
        return self._palette.custom_button.get_tooltip_text() or ""


class Palette(Adw.WrapBox):
    def __init__(self, label: str, base_mode: str, on_changed=None, plain: bool = False):
        super().__init__(child_spacing=6, line_spacing=6, accessible_role=Gtk.AccessibleRole.GROUP)
        ensure_css()
        self._base_mode = base_mode
        self._on_changed = on_changed
        self._quiet = False
        self._mode = base_mode
        self._color = None
        self._computed = None
        self._accent = DEFAULT_ACCENT
        self._base_label = _("None") if base_mode == "none" else _("Automatic")
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self._buttons: dict = {}
        self._areas: dict = {}
        self._plain = plain
        if not plain:
            self._add("base", None, base_mode, self._base_label)
            self._add("accent", self._accent, "accent", _("System Accent"))
        for color, name in PALETTE_COLORS:
            self._add(color, color, None, name)
        self.custom_button = CustomSwatch()
        self.custom_button.set_rgba(rgba_of(DEFAULT_CUSTOM))
        self.custom_button.set_tooltip_text(_("Custom"))
        self.custom_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Custom")])
        self.custom_button.connect("notify::rgba", self._on_custom_color)
        self.append(self.custom_button)
        self._custom_choice = CustomChoice(self)
        self._refresh()

    def _add(self, key: str, color: str | None, symbol: str | None, tooltip: str) -> None:
        area = Gtk.DrawingArea(content_width=SWATCH_SIZE, content_height=SWATCH_SIZE)
        button = Gtk.ToggleButton(valign=Gtk.Align.CENTER, tooltip_text=tooltip)
        button.add_css_class("flat")
        button.add_css_class("palette-swatch")
        button.set_child(area)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        area.set_draw_func(lambda _area, context, width, height, key=key: self._draw(key, context, width, height))
        button.connect("toggled", self._on_toggled, key)
        self._buttons[key] = button
        self._areas[key] = area
        self.append(button)

    def _swatch_color(self, key: str) -> str | None:
        if key == "base":
            return None
        if key == "accent":
            return self._accent
        return key

    def _draw(self, key: str, context, width: int, height: int) -> None:
        symbol = (self._base_mode if self._base_mode in ("none", "auto") else None) if key == "base" else None
        draw_swatch(context, width, height, self._swatch_color(key), symbol)

    def buttons(self) -> dict:
        return {**self._buttons, "custom": self._custom_choice}

    def state(self) -> tuple[str, str | None]:
        return self._mode, self._color

    def custom_hex(self) -> str:
        return hex_of(self.custom_button.get_rgba())

    def custom_is_active(self) -> bool:
        return self._mode == "custom" and self._color is not None and self._color not in self._buttons

    def accent_color(self) -> str:
        return self._accent

    def set_accent(self, color: str) -> None:
        if is_hex_color(color):
            self._accent = color.lower()
            self._buttons["accent"].set_tooltip_text(f"{_('System Accent')} ({self._accent})")
            self._areas["accent"].queue_draw()

    def set_computed(self, color: str | None) -> None:
        self._computed = color
        self._buttons["base"].set_tooltip_text(f"{self._base_label} ({color})" if color else self._base_label)

    def set_state(self, mode: str, color: str | None) -> None:
        self._quiet = True
        try:
            if mode == "custom":
                normalized = color.lower() if isinstance(color, str) and is_hex_color(color) else None
                if normalized is None:
                    normalized = self.custom_hex() if self._color is None or self._color in self._buttons else self._color
                if normalized not in self._buttons:
                    self.custom_button.set_rgba(rgba_of(normalized))
                self._mode, self._color = "custom", normalized
            elif self._plain:
                self._mode, self._color = "custom", self.custom_hex()
            elif mode == "accent":
                self._mode, self._color = "accent", None
            else:
                self._mode, self._color = self._base_mode, None
        finally:
            self._quiet = False
        self._refresh()

    def _refresh(self) -> None:
        self._quiet = True
        try:
            for key, button in self._buttons.items():
                button.set_active(self._is_selected(key))
                button.remove_css_class("selected-swatch")
                if button.get_active():
                    button.add_css_class("selected-swatch")
                self._areas[key].queue_draw()
            self.custom_button.set_chosen(self.custom_is_active())
            if self.custom_is_active():
                self.custom_button.add_css_class("selected-swatch")
            else:
                self.custom_button.remove_css_class("selected-swatch")
        finally:
            self._quiet = False

    def _is_selected(self, key: str) -> bool:
        if key == "base":
            return self._mode == self._base_mode
        if key == "accent":
            return self._mode == "accent"
        return self._mode == "custom" and self._color == key

    def _emit(self) -> None:
        if self._on_changed is not None:
            self._on_changed(self._mode, self._color)

    def _on_toggled(self, button, key: str) -> None:
        if self._quiet:
            return
        if not button.get_active():
            self._refresh()
            return
        if key == "base":
            self._mode, self._color = self._base_mode, None
        elif key == "accent":
            self._mode, self._color = "accent", None
        else:
            self._mode, self._color = "custom", key
        self._refresh()
        self._emit()

    def _on_custom_color(self, *_args) -> None:
        if self._quiet:
            return
        self._mode, self._color = "custom", self.custom_hex()
        self._refresh()
        self._emit()

    def choose_base(self) -> None:
        self._buttons["base"].set_active(True)

    def choose_accent(self) -> None:
        self._buttons["accent"].set_active(True)

    def choose_color(self, color: str) -> None:
        self._buttons[color].set_active(True)
