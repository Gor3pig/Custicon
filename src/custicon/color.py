import colorsys
import re
from pathlib import Path

from .errors import InvalidIconError
from .layout import FACE_X, FACE_Y, MIN_VISIBLE_ALPHA, SAMPLE_RADIUS, SAMPLE_SIZE
from .render import pixel, surface_from_file

LIGHTNESS_FACTOR = 0.74
SATURATION_FACTOR = 1.12
HEX_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

Rgb = tuple[float, float, float]


def is_hex_color(value: str) -> bool:
    return isinstance(value, str) and bool(HEX_PATTERN.fullmatch(value))


def parse_hex(value: str) -> Rgb:
    if not is_hex_color(value):
        raise ValueError(f"not a #rrggbb color: {value!r}")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))


def format_hex(rgb: Rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def average_visible(surface, x0: int, y0: int, x1: int, y1: int) -> Rgb | None:
    total = [0.0, 0.0, 0.0]
    weight = 0.0
    for y in range(y0, y1):
        for x in range(x0, x1):
            red, green, blue, alpha = pixel(surface, x, y)
            if alpha >= MIN_VISIBLE_ALPHA:
                total = [total[0] + red * alpha, total[1] + green * alpha, total[2] + blue * alpha]
                weight += alpha
    if weight == 0:
        return None
    return tuple(t / weight for t in total)


def face_color(folder_file: Path) -> Rgb:
    surface = surface_from_file(folder_file, SAMPLE_SIZE)
    cx, cy = round(FACE_X * SAMPLE_SIZE), round(FACE_Y * SAMPLE_SIZE)
    found = average_visible(
        surface, cx - SAMPLE_RADIUS, cy - SAMPLE_RADIUS, cx + SAMPLE_RADIUS + 1, cy + SAMPLE_RADIUS + 1
    )
    if found is None:
        found = average_visible(surface, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE)
    if found is None:
        raise InvalidIconError(f"{folder_file.name}: no visible pixel")
    return found


def darker_shade(rgb: Rgb) -> Rgb:
    hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
    return colorsys.hls_to_rgb(
        hue, lightness * LIGHTNESS_FACTOR, min(1.0, saturation * SATURATION_FACTOR)
    )


def auto_glyph_color(folder_file: Path) -> str:
    return format_hex(darker_shade(face_color(folder_file)))
