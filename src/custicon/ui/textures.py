from pathlib import Path

import cairo
from gi.repository import Gdk, GLib

from ..render import surface_from_bytes, surface_from_file


def texture_from_surface(surface: cairo.ImageSurface) -> Gdk.Texture:
    surface.flush()
    return Gdk.MemoryTexture.new(
        surface.get_width(),
        surface.get_height(),
        Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
        GLib.Bytes.new(bytes(surface.get_data())),
        surface.get_stride(),
    )


def texture_from_svg_text(svg: str, size: int) -> Gdk.Texture:
    return texture_from_surface(surface_from_bytes(svg.encode(), size))


def texture_from_file(path: Path, size: int) -> Gdk.Texture:
    return texture_from_surface(surface_from_file(path, size))
