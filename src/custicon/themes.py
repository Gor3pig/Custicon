import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from .errors import IconNotFoundError

FLATPAK_INFO = "/.flatpak-info"
SANDBOX_ICON_DIRS = ("/run/host/user-share/icons", "/run/host/share/icons")
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SETTINGS = "org.freedesktop.portal.Settings"
INTERFACE_SCHEMA = "org.gnome.desktop.interface"
ICON_THEME_KEY = "icon-theme"
DEFAULT_THEME = "Adwaita"
SYMBOLIC_SUFFIX = "-symbolic"
LOOKUP_SIZE = 128
GLYPH_LOOKUP_SIZE = 16
SYMBOLIC_LIMIT = 200


def in_sandbox() -> bool:
    return os.path.exists(FLATPAK_INFO)


def host_icon_dirs() -> list[str]:
    if in_sandbox():
        return list(SANDBOX_ICON_DIRS)
    dirs = [os.path.join(GLib.get_user_data_dir(), "icons"), os.path.expanduser("~/.icons")]
    dirs += [os.path.join(data_dir, "icons") for data_dir in GLib.get_system_data_dirs()]
    return [d for d in dirs if os.path.isdir(d)]


def portal_icon_theme() -> str | None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            PORTAL_SETTINGS,
            "Read",
            GLib.Variant("(ss)", (INTERFACE_SCHEMA, ICON_THEME_KEY)),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error:
        return None
    value = reply.get_child_value(0).get_variant()
    if value.get_type_string() == "v":
        value = value.get_variant()
    return value.get_string() if value.get_type_string() == "s" else None


def settings_icon_theme() -> str | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(INTERFACE_SCHEMA, True) is None:
        return None
    return Gio.Settings.new(INTERFACE_SCHEMA).get_string(ICON_THEME_KEY)


def active_icon_theme() -> str:
    return portal_icon_theme() or settings_icon_theme() or DEFAULT_THEME


class IconTheme:
    def __init__(self, name: str, search_dirs: list[str] | None = None):
        Gtk.init_check()
        self.name = name
        self._theme = Gtk.IconTheme()
        self._theme.set_search_path(list(host_icon_dirs() if search_dirs is None else search_dirs))
        self._theme.set_theme_name(name)

    def has_icon(self, icon_name: str) -> bool:
        return self._theme.has_icon(icon_name)

    def find(self, icon_name: str, size: int = LOOKUP_SIZE) -> Path:
        if not self.has_icon(icon_name):
            raise IconNotFoundError(f"{icon_name} not found in theme {self.name}")
        paintable = self._theme.lookup_icon(icon_name, None, size, 1, Gtk.TextDirection.NONE, 0)
        file = paintable.get_file()
        if file is None or file.get_path() is None:
            raise IconNotFoundError(f"{icon_name} has no file in theme {self.name}")
        return Path(file.get_path())

    def folder_file(self) -> Path:
        return self.find("folder")

    def glyph_file(self, icon_name: str) -> Path:
        return self.find(icon_name, GLYPH_LOOKUP_SIZE)

    def symbolic_names(self, query: str = "", limit: int = SYMBOLIC_LIMIT) -> list[str]:
        needle = query.strip().lower()
        candidates = sorted(
            {
                name
                for name in self._theme.get_icon_names()
                if name.endswith(SYMBOLIC_SUFFIX) and needle in name.lower()
            }
        )
        found = []
        for name in candidates:
            if len(found) >= limit:
                break
            try:
                self.glyph_file(name)
            except IconNotFoundError:
                continue
            found.append(name)
        return found
