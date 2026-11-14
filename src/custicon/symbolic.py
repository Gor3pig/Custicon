from collections import OrderedDict
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Graphene, Gtk

from . import compose
from .errors import CusticonError
from .render import read_icon_bytes, surface_from_bytes
from .ui.textures import texture_from_surface

RENDER_SCALE = 2
CACHE_LIMIT = 4096
LIGHT_FOREGROUND = "#2e3436"
DARK_FOREGROUND = "#eeeeec"

_textures: OrderedDict = OrderedDict()


def hex_of(color: Gdk.RGBA) -> str:
    return "#%02x%02x%02x" % tuple(round(channel * 255) for channel in (color.red, color.green, color.blue))


def default_foreground() -> str:
    return DARK_FOREGROUND if Adw.StyleManager.get_default().get_dark() else LIGHT_FOREGROUND


def colored_svg(data: bytes, color: str) -> bytes:
    root = compose.parse_svg(data)
    compose.recolor(root, color)
    if root.get("fill") is None:
        root.set("fill", color)
    return compose.serialize(root).encode()


def render_texture(path: str, color: str, pixels: int) -> Gdk.Texture:
    key = (path, color, pixels)
    texture = _textures.get(key)
    if texture is not None:
        _textures.move_to_end(key)
        return texture
    data = colored_svg(read_icon_bytes(Path(path)), color)
    texture = texture_from_surface(surface_from_bytes(data, pixels))
    _textures[key] = texture
    if len(_textures) > CACHE_LIMIT:
        _textures.popitem(last=False)
    return texture


def clear_cache() -> None:
    _textures.clear()


def cache_size() -> int:
    return len(_textures)


class SymbolicSvg(GObject.Object, Gdk.Paintable, Gtk.SymbolicPaintable):
    def __init__(self, path: Path | str, size: int):
        super().__init__()
        self._path = str(path)
        self._size = size

    def do_get_intrinsic_width(self):
        return self._size

    def do_get_intrinsic_height(self):
        return self._size

    def do_get_intrinsic_aspect_ratio(self):
        return 1.0

    def do_get_flags(self):
        return Gdk.PaintableFlags.STATIC_SIZE | Gdk.PaintableFlags.STATIC_CONTENTS

    def do_snapshot(self, snapshot, width, height):
        self._draw(snapshot, width, height, default_foreground(), 1.0)

    def do_snapshot_symbolic(self, snapshot, width, height, colors, count=0):
        if colors:
            self._draw(snapshot, width, height, hex_of(colors[0]), colors[0].alpha)
        else:
            self._draw(snapshot, width, height, default_foreground(), 1.0)

    def _draw(self, snapshot, width, height, color, opacity):
        pixels = max(1, round(max(width, height) * RENDER_SCALE))
        try:
            texture = render_texture(self._path, color, pixels)
        except CusticonError:
            return
        rectangle = Graphene.Rect()
        rectangle.init(0, 0, width, height)
        if opacity < 1.0:
            snapshot.push_opacity(opacity)
        snapshot.append_texture(texture, rectangle)
        if opacity < 1.0:
            snapshot.pop()
