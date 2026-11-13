import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .errors import InvalidIconError

FONT = "Sans Bold 64"
MAX_CHARACTERS = 3
DECIMALS = 2
SVG_HEADER = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {width} {height}" width="{width}" height="{height}">'


def number(value: float) -> str:
    return format(round(value, DECIMALS), "g")


def path_data(path) -> str:
    commands = []
    for kind, points in path:
        if kind == cairo.PATH_MOVE_TO:
            commands.append("M" + " ".join(number(value) for value in points))
        elif kind == cairo.PATH_LINE_TO:
            commands.append("L" + " ".join(number(value) for value in points))
        elif kind == cairo.PATH_CURVE_TO:
            commands.append("C" + " ".join(number(value) for value in points))
        else:
            commands.append("Z")
    return "".join(commands)


def text_to_svg(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise InvalidIconError("the text is empty")
    text = text.strip()
    if len(text) > MAX_CHARACTERS:
        raise InvalidIconError(f"use at most {MAX_CHARACTERS} characters")
    context = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(Pango.FontDescription.from_string(FONT))
    layout.set_text(text, -1)
    if layout.get_unknown_glyphs_count() > 0:
        raise InvalidIconError("the font has no shape for these characters")
    PangoCairo.layout_path(context, layout)
    left, top, right, bottom = context.path_extents()
    if right - left <= 0 or bottom - top <= 0:
        raise InvalidIconError("these characters have no visible shape")
    shape = path_data(context.copy_path())
    header = SVG_HEADER.format(x=number(left), y=number(top), width=number(right - left), height=number(bottom - top))
    return f'{header}<path d="{shape}"/></svg>'
