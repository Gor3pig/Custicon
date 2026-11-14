from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

from .errors import IconNotFoundError

PHOSPHOR_PREFIX = "phosphor:"
SYMBOLIC_SUFFIX = "-symbolic.svg"
PHOSPHOR_DIRECTORY = Path(__file__).resolve().parent / "phosphor"
DEFAULT_LIMIT = 200


def is_phosphor(name: str | None) -> bool:
    return bool(name) and name.startswith(PHOSPHOR_PREFIX)


def phosphor_name(name: str) -> str:
    return name[len(PHOSPHOR_PREFIX):]


def display_name(name: str) -> str:
    if is_phosphor(name):
        return f"{phosphor_name(name)} (Phosphor)"
    return name


class PhosphorSet:
    def __init__(self, directory: Path = PHOSPHOR_DIRECTORY):
        self.directory = directory
        self._names: list[str] | None = None

    def all_names(self) -> list[str]:
        if self._names is None:
            self._names = sorted(
                PHOSPHOR_PREFIX + path.name[: -len(SYMBOLIC_SUFFIX)] for path in self.directory.glob("*" + SYMBOLIC_SUFFIX)
            )
        return list(self._names)

    def symbolic_names(self, query: str = "", limit: int = DEFAULT_LIMIT) -> list[str]:
        needle = query.strip().lower()
        found = [name for name in self.all_names() if needle in phosphor_name(name)]
        return found[:limit]

    def file(self, name: str) -> Path:
        if name not in self.all_names():
            raise IconNotFoundError(f"{name} not found in the Phosphor set")
        return self.directory / (phosphor_name(name) + SYMBOLIC_SUFFIX)

    def glyph_file(self, name: str) -> Path:
        return self.file(name)

    def paintable(self, name: str, size: int, scale: int = 1):
        return Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(str(self.file(name))), size, scale)
