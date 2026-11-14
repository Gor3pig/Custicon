import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

from .errors import IconNotFoundError

PHOSPHOR_PREFIX = "phosphor:"
SVG_SUFFIX = ".svg"
INDEX_NAME = "index.json"
RENAMED = {"phone-handset": "phone"}
MIN_TAG_QUERY = 3
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
        self._loaded = False
        self._names: list[str] = []
        self._icons: dict = {}
        self._revision = 1

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            index = json.loads((self.directory / INDEX_NAME).read_text(encoding="utf-8"))
            icons = index["icons"]
            revision = index["revision"]
            if not isinstance(icons, dict) or isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            icons, revision = {}, 1
        present = {path.name[: -len(SVG_SUFFIX)] for path in self.directory.glob("*" + SVG_SUFFIX)}
        if icons:
            icons = {name: entry for name, entry in icons.items() if name in present and isinstance(entry, dict)}
        else:
            icons = {name: {} for name in present}
        self._icons = icons
        self._revision = revision
        self._names = sorted(PHOSPHOR_PREFIX + name for name in icons)

    @property
    def revision(self) -> int:
        self._load()
        return self._revision

    def all_names(self) -> list[str]:
        self._load()
        return list(self._names)

    def tags(self, name: str) -> list[str]:
        self._load()
        entry = self._icons.get(phosphor_name(name), {})
        return [tag for tag in entry.get("t", []) if isinstance(tag, str)]

    def categories(self) -> list[str]:
        self._load()
        found = {category for entry in self._icons.values() for category in entry.get("c", []) if isinstance(category, str)}
        return sorted(found)

    def names_in_category(self, category: str) -> list[str]:
        self._load()
        return sorted(PHOSPHOR_PREFIX + name for name, entry in self._icons.items() if category in entry.get("c", []))

    def symbolic_names(self, query: str = "", limit: int = DEFAULT_LIMIT) -> list[str]:
        self._load()
        needle = query.strip().lower()
        if not needle:
            return list(self._names)[:limit]
        ranked = []
        for name, entry in self._icons.items():
            if name == needle:
                rank = 0
            elif name.startswith(needle):
                rank = 1
            elif needle in name:
                rank = 2
            elif len(needle) >= MIN_TAG_QUERY and any(needle in tag for tag in entry.get("t", []) if isinstance(tag, str)):
                rank = 3
            else:
                continue
            ranked.append((rank, name))
        ranked.sort()
        return [PHOSPHOR_PREFIX + name for _rank, name in ranked[:limit]]

    def file(self, name: str) -> Path:
        self._load()
        short = phosphor_name(name) if is_phosphor(name) else ""
        short = RENAMED.get(short, short)
        if short not in self._icons:
            raise IconNotFoundError(f"{name} not found in the Phosphor set")
        return self.directory / (short + SVG_SUFFIX)

    def glyph_file(self, name: str) -> Path:
        return self.file(name)

    def paintable(self, name: str, size: int, scale: int = 1):
        return Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(str(self.file(name))), size, scale)
