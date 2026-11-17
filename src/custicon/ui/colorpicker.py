import colorsys

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ..color import format_hex, is_hex_color, parse_hex
from ..i18n import _

HUE_STEP = 4 / 360
HUE_STEP_FAST = 20 / 360
PLANE_STEP = 0.02
PLANE_STEP_FAST = 0.1
KNOB_RADIUS = 10
HUE_HEIGHT = 26
PLANE_ASPECT = 0.82
PLANE_MIN_WIDTH = 300
EDGE_COLOR = (0, 0, 0, 0.4)

Hsv = tuple[float, float, float]


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def hsv_of(value: str) -> Hsv:
    return colorsys.rgb_to_hsv(*parse_hex(value))


def hex_of_hsv(hsv: Hsv) -> str:
    return format_hex(colorsys.hsv_to_rgb(hsv[0] % 1.0, clamp(hsv[1]), clamp(hsv[2])))


def normalized_hex(text: str) -> str | None:
    candidate = text.strip()
    if candidate and not candidate.startswith("#"):
        candidate = "#" + candidate
    return candidate.lower() if is_hex_color(candidate) else None


def draw_knob(context, x: float, y: float, fill: tuple[float, float, float]) -> None:
    context.arc(x, y, KNOB_RADIUS, 0, 6.2832)
    context.set_source_rgb(*fill)
    context.fill_preserve()
    context.set_line_width(3)
    context.set_source_rgb(1, 1, 1)
    context.stroke_preserve()
    context.set_line_width(1)
    context.set_source_rgba(*EDGE_COLOR)
    context.stroke()


class DragArea(Gtk.DrawingArea):
    def __init__(self, on_position, on_key, **kwargs):
        super().__init__(focusable=True, **kwargs)
        self._on_position = on_position
        self._on_key = on_key
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        self.add_controller(drag)
        self._drag = drag
        self._origin = (0.0, 0.0)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._pressed)
        self.add_controller(keys)

    def _report(self, x: float, y: float) -> None:
        width, height = max(1, self.get_width()), max(1, self.get_height())
        self._on_position(clamp(x / width), clamp(y / height))

    def _begin(self, _gesture, x: float, y: float) -> None:
        self._origin = (x, y)
        self.grab_focus()
        self._report(x, y)

    def _update(self, _gesture, dx: float, dy: float) -> None:
        self._report(self._origin[0] + dx, self._origin[1] + dy)

    def _pressed(self, _controller, keyval, _keycode, state) -> bool:
        fast = bool(state & Gdk.ModifierType.SHIFT_MASK)
        moves = {
            Gdk.KEY_Left: (-1, 0),
            Gdk.KEY_Right: (1, 0),
            Gdk.KEY_Up: (0, -1),
            Gdk.KEY_Down: (0, 1),
        }
        move = moves.get(keyval)
        if move is None:
            return False
        self._on_key(move[0], move[1], fast)
        return True


class ColorPickerDialog(Adw.Dialog):
    def __init__(self, current: str, on_chosen):
        super().__init__(title=_("Custom Color"), content_width=400)
        self._original = current if is_hex_color(current) else "#e5a50a"
        self._on_chosen = on_chosen
        self._hsv: Hsv = hsv_of(self._original)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_args: self.close())
        header.pack_start(cancel)
        self.choose_button = Gtk.Button(label=_("Choose"))
        self.choose_button.add_css_class("suggested-action")
        self.choose_button.connect("clicked", self._on_choose)
        header.pack_end(self.choose_button)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=8, margin_bottom=16, margin_start=16, margin_end=16)
        self.plane = DragArea(self._on_plane_position, self._on_plane_key, content_width=PLANE_MIN_WIDTH, content_height=round(PLANE_MIN_WIDTH * PLANE_ASPECT), hexpand=True)
        self.plane.set_draw_func(self._draw_plane)
        self.plane.update_property([Gtk.AccessibleProperty.LABEL], [_("Saturation and brightness: use the arrow keys")])
        box.append(self.plane)
        self.hue = DragArea(self._on_hue_position, self._on_hue_key, content_height=HUE_HEIGHT, hexpand=True)
        self.hue.set_draw_func(self._draw_hue)
        self.hue.update_property([Gtk.AccessibleProperty.LABEL], [_("Hue")])
        box.append(self.hue)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.compare = Gtk.DrawingArea(content_width=92, content_height=36, valign=Gtk.Align.CENTER)
        self.compare.set_draw_func(self._draw_compare)
        self.compare.set_tooltip_text(_("Previous color and new color"))
        bottom.append(self.compare)
        self.entry = Gtk.Entry(max_length=7, hexpand=True, valign=Gtk.Align.CENTER)
        self.entry.add_css_class("numeric")
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Hexadecimal code of the color")])
        self.entry.connect("activate", lambda *_args: self.commit_entry())
        leaving = Gtk.EventControllerFocus()
        leaving.connect("leave", lambda *_args: self.commit_entry())
        self.entry.add_controller(leaving)
        bottom.append(self.entry)
        box.append(bottom)
        self.error = Gtk.Label(xalign=0.0, visible=False)
        self.error.add_css_class("caption")
        self.error.add_css_class("error")
        box.append(self.error)
        view.set_content(box)
        self.set_child(view)
        self.set_default_widget(self.choose_button)
        self.set_focus(self.plane)
        self._show_entry()

    @property
    def hex(self) -> str:
        return hex_of_hsv(self._hsv)

    def set_hex(self, value: str) -> bool:
        color = normalized_hex(value)
        if color is None:
            return False
        self._hsv = hsv_of(color)
        self._refresh()
        return True

    def commit_entry(self) -> None:
        if self.set_hex(self.entry.get_text()):
            return
        self.error.set_label(_("Invalid code: expected #RRGGBB."))
        self.error.set_visible(True)
        self.entry.add_css_class("error")

    def _show_entry(self) -> None:
        self.error.set_label("")
        self.error.set_visible(False)
        self.entry.remove_css_class("error")
        if self.entry.get_text() != self.hex:
            self.entry.set_text(self.hex)

    def _refresh(self) -> None:
        self._show_entry()
        for area in (self.plane, self.hue, self.compare):
            area.queue_draw()

    def _on_plane_position(self, x: float, y: float) -> None:
        self._hsv = (self._hsv[0], x, 1 - y)
        self._refresh()

    def _on_plane_key(self, dx: int, dy: int, fast: bool) -> None:
        step = PLANE_STEP_FAST if fast else PLANE_STEP
        hue, saturation, value = self._hsv
        self._hsv = (hue, clamp(saturation + dx * step), clamp(value - dy * step))
        self._refresh()

    def _on_hue_position(self, x: float, _y: float) -> None:
        self._hsv = (min(x, 0.999999), self._hsv[1], self._hsv[2])
        self._refresh()

    def _on_hue_key(self, dx: int, dy: int, fast: bool) -> None:
        step = HUE_STEP_FAST if fast else HUE_STEP
        self._hsv = ((self._hsv[0] + (dx - dy) * step) % 1.0, self._hsv[1], self._hsv[2])
        self._refresh()

    def _draw_plane(self, _area, context, width: int, height: int) -> None:
        base = colorsys.hsv_to_rgb(self._hsv[0], 1, 1)
        across = cairo.LinearGradient(0, 0, width, 0)
        across.add_color_stop_rgb(0, 1, 1, 1)
        across.add_color_stop_rgb(1, *base)
        context.rectangle(0, 0, width, height)
        context.set_source(across)
        context.fill()
        down = cairo.LinearGradient(0, 0, 0, height)
        down.add_color_stop_rgba(0, 0, 0, 0, 0)
        down.add_color_stop_rgba(1, 0, 0, 0, 1)
        context.rectangle(0, 0, width, height)
        context.set_source(down)
        context.fill()
        draw_knob(context, self._hsv[1] * width, (1 - self._hsv[2]) * height, parse_hex(self.hex))

    def _draw_hue(self, _area, context, width: int, height: int) -> None:
        gradient = cairo.LinearGradient(0, 0, width, 0)
        for index in range(7):
            gradient.add_color_stop_rgb(index / 6, *colorsys.hsv_to_rgb(min(index / 6, 0.999999), 1, 1))
        context.rectangle(0, 0, width, height)
        context.set_source(gradient)
        context.fill()
        draw_knob(context, min(max(self._hsv[0] * width, KNOB_RADIUS), width - KNOB_RADIUS), height / 2, colorsys.hsv_to_rgb(self._hsv[0], 1, 1))

    def _draw_compare(self, _area, context, width: int, height: int) -> None:
        half = width / 2
        for index, color in enumerate((self._original, self.hex)):
            context.rectangle(index * half, 0, half, height)
            context.set_source_rgb(*parse_hex(color))
            context.fill()
        context.rectangle(0.5, 0.5, width - 1, height - 1)
        context.set_source_rgba(*EDGE_COLOR)
        context.set_line_width(1)
        context.stroke()

    def _on_choose(self, _button) -> None:
        self.commit_entry()
        if self.error.get_label():
            return
        chosen = self.hex
        self.close()
        self._on_chosen(chosen)
