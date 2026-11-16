import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ..i18n import _

ACTION_NAME = "win.color-scheme"
SCHEMES = {
    "system": Adw.ColorScheme.DEFAULT,
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}
CHIP_CSS = """
.theme-chip {
  padding: 0;
  min-width: 34px;
  min-height: 34px;
  border-radius: 99px;
  border: 1px solid alpha(currentColor, 0.25);
  background-clip: padding-box;
}
.theme-chip-system {
  background-image: linear-gradient(135deg, #f6f5f4 50%, #2e2e32 50%);
}
.theme-chip-light {
  background-image: none;
  background-color: #f6f5f4;
}
.theme-chip-dark {
  background-image: none;
  background-color: #2e2e32;
}
.theme-chip:checked {
  outline: 2px solid @accent_bg_color;
  outline-offset: 2px;
}"""
_css_ready = False


def ensure_css() -> None:
    global _css_ready
    display = Gdk.Display.get_default()
    if _css_ready or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(CHIP_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_ready = True


def apply_color_scheme(name: str) -> None:
    Adw.StyleManager.get_default().set_color_scheme(SCHEMES.get(name, Adw.ColorScheme.DEFAULT))


def chip_titles() -> dict[str, str]:
    return {"system": _("System"), "light": _("Light"), "dark": _("Dark")}


class ThemeChips(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=22, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=6, margin_start=12, margin_end=12)
        ensure_css()
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        for name, title in chip_titles().items():
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
            button = Gtk.ToggleButton(action_name=ACTION_NAME, action_target=GLib.Variant.new_string(name), tooltip_text=title, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            button.add_css_class("theme-chip")
            button.add_css_class(f"theme-chip-{name}")
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            caption = Gtk.Label(label=title)
            caption.add_css_class("caption")
            column.append(button)
            column.append(caption)
            self.append(column)
            self.buttons[name] = button
