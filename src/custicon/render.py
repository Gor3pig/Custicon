import io
from pathlib import Path

import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import GLib, Rsvg

from .errors import InvalidIconError
from .layout import MAX_FILE_BYTES

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_icon_bytes(path: Path) -> bytes:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise InvalidIconError(f"{path.name}: file too large")
        return path.read_bytes()
    except OSError as error:
        raise InvalidIconError(f"{path.name}: {error.strerror}") from error


def is_png(data: bytes) -> bool:
    return data.startswith(PNG_MAGIC)


def surface_from_svg(data: bytes, size: int) -> cairo.ImageSurface:
    try:
        handle = Rsvg.Handle.new_from_data(data)
    except GLib.Error as error:
        raise InvalidIconError(error.message) from error
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    viewport = Rsvg.Rectangle()
    viewport.x, viewport.y, viewport.width, viewport.height = 0, 0, size, size
    try:
        handle.render_document(cairo.Context(surface), viewport)
    except GLib.Error as error:
        raise InvalidIconError(error.message) from error
    return surface


def surface_from_png(data: bytes, size: int) -> cairo.ImageSurface:
    try:
        source = cairo.ImageSurface.create_from_png(io.BytesIO(data))
    except (cairo.Error, OSError) as error:
        raise InvalidIconError(str(error)) from error
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    context = cairo.Context(surface)
    context.scale(size / source.get_width(), size / source.get_height())
    context.set_source_surface(source, 0, 0)
    context.get_source().set_filter(cairo.FILTER_BEST)
    context.paint()
    return surface


def surface_from_bytes(data: bytes, size: int) -> cairo.ImageSurface:
    if is_png(data):
        return surface_from_png(data, size)
    return surface_from_svg(data, size)


def surface_from_file(path: Path, size: int) -> cairo.ImageSurface:
    return surface_from_bytes(read_icon_bytes(path), size)


def pixel(surface: cairo.ImageSurface, x: int, y: int) -> tuple[float, float, float, float]:
    surface.flush()
    data = surface.get_data()
    offset = y * surface.get_stride() + x * 4
    alpha = data[offset + 3] / 255
    if alpha == 0:
        return 0.0, 0.0, 0.0, 0.0
    blue, green, red = (min(1.0, data[offset + i] / 255 / alpha) for i in range(3))
    return red, green, blue, alpha
