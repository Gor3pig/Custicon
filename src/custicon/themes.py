import configparser
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from .color import format_hex
from .errors import IconNotFoundError

FLATPAK_INFO = "/.flatpak-info"
SANDBOX_ICON_DIRS = ("/run/host/user-share/icons", "/run/host/share/icons")
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SETTINGS = "org.freedesktop.portal.Settings"
INTERFACE_SCHEMA = "org.gnome.desktop.interface"
ICON_THEME_KEY = "icon-theme"
KDE_NAMESPACE_PATTERN = "org.kde.kdeglobals.*"
KDE_ICONS_NAMESPACE = "org.kde.kdeglobals.Icons"
KDE_ICON_THEME_KEY = "Theme"
DEFAULT_THEME = "Adwaita"
KDE_DEFAULT_THEME = "breeze"
APPEARANCE_NAMESPACE = "org.freedesktop.appearance"
ACCENT_KEY = "accent-color"
DEFAULT_ACCENT = "#3584e4"
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


def read_portal_setting(namespace: str, key: str):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            PORTAL_SETTINGS,
            "Read",
            GLib.Variant("(ss)", (namespace, key)),
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
    return value


def portal_has_kde() -> bool:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            PORTAL_SETTINGS,
            "ReadAll",
            GLib.Variant("(as)", ([KDE_NAMESPACE_PATTERN],)),
            GLib.VariantType("(a{sa{sv}})"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error:
        return False
    return bool(reply.get_child_value(0).n_children())


def portal_kde_icon_theme() -> str | None:
    value = read_portal_setting(KDE_ICONS_NAMESPACE, KDE_ICON_THEME_KEY)
    if value is None or value.get_type_string() != "s":
        return None
    return value.get_string() or None


def config_icon_theme(path: str, section: str, key: str) -> str | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    parser.optionxform = str
    try:
        parser.read(os.path.expanduser(path), encoding="utf-8")
        return parser.get(section, key, fallback="").strip() or None
    except (OSError, ValueError, configparser.Error):
        return None


def portal_icon_theme() -> str | None:
    value = read_portal_setting(INTERFACE_SCHEMA, ICON_THEME_KEY)
    if value is None:
        return None
    return value.get_string() if value.get_type_string() == "s" else None


def portal_accent_color() -> str | None:
    value = read_portal_setting(APPEARANCE_NAMESPACE, ACCENT_KEY)
    if value is None or value.get_type_string() != "(ddd)":
        return None
    channels = value.unpack()
    if not all(0.0 <= channel <= 1.0 for channel in channels):
        return None
    return format_hex(channels)


def system_accent_color() -> str:
    return portal_accent_color() or DEFAULT_ACCENT


def settings_icon_theme() -> str | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(INTERFACE_SCHEMA, True) is None:
        return None
    return Gio.Settings.new(INTERFACE_SCHEMA).get_string(ICON_THEME_KEY)


def active_icon_theme() -> str:
    from . import environment

    if environment.is_kde():
        return portal_kde_icon_theme() or config_icon_theme("~/.config/kdeglobals", "Icons", "Theme") or KDE_DEFAULT_THEME
    elif "LXQT" in environment.current_desktops() and environment.detect_backend() == environment.BACKEND_DIRECTORY:
        name = config_icon_theme("~/.config/lxqt/lxqt.conf", "General", "icon_theme")
        if name:
            return name
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

    def paintable(self, icon_name: str, size: int, scale: int = 1):
        if not self.has_icon(icon_name):
            raise IconNotFoundError(f"{icon_name} not found in theme {self.name}")
        return self._theme.lookup_icon(icon_name, None, size, scale, Gtk.TextDirection.NONE, 0)

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
