from pathlib import Path

from gi.repository import Gio, GLib

from .errors import MetadataError

CUSTOM_ICON_KEY = "metadata::custom-icon"
NO_FLAGS = Gio.FileQueryInfoFlags.NONE


def read_custom_icon(folder: Path) -> str | None:
    try:
        info = Gio.File.new_for_path(str(folder)).query_info(CUSTOM_ICON_KEY, NO_FLAGS, None)
    except GLib.Error as error:
        raise MetadataError(error.message) from error
    return info.get_attribute_string(CUSTOM_ICON_KEY) if info.has_attribute(CUSTOM_ICON_KEY) else None


def write_custom_icon(folder: Path, uri: str | None) -> None:
    target = Gio.File.new_for_path(str(folder))
    try:
        if uri is None:
            target.set_attribute(CUSTOM_ICON_KEY, Gio.FileAttributeType.INVALID, None, NO_FLAGS, None)
        else:
            target.set_attribute_string(CUSTOM_ICON_KEY, uri, NO_FLAGS, None)
    except GLib.Error as error:
        raise MetadataError(error.message) from error
    stored = read_custom_icon(folder)
    if stored != uri:
        raise MetadataError(f"the value was not stored for {folder}")
