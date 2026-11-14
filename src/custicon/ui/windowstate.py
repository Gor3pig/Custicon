import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 680
MINIMUM_WIDTH = 360
MINIMUM_HEIGHT = 360
SCREEN_SHARE = 0.92


def screen_size() -> tuple[int, int] | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitors = display.get_monitors()
    if monitors is None or monitors.get_n_items() == 0:
        return None
    geometry = monitors.get_item(0).get_geometry()
    if geometry.width <= 0 or geometry.height <= 0:
        return None
    return geometry.width, geometry.height


def fitted(width: int, height: int, screen: tuple[int, int] | None) -> tuple[int, int]:
    if screen is not None:
        width = min(width, int(screen[0] * SCREEN_SHARE))
        height = min(height, int(screen[1] * SCREEN_SHARE))
    return max(width, MINIMUM_WIDTH), max(height, MINIMUM_HEIGHT)


def initial_state(remembered, screen: tuple[int, int] | None) -> tuple[int, int, bool]:
    if remembered is None:
        width, height = fitted(DEFAULT_WIDTH, DEFAULT_HEIGHT, screen)
        return width, height, False
    width, height = fitted(remembered.width, remembered.height, screen)
    return width, height, remembered.maximized
