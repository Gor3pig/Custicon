import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from .errors import IconNotFoundError
from .textutil import normalize_text

PHOSPHOR_PREFIX = "phosphor:"
SVG_SUFFIX = ".svg"
INDEX_NAME = "index.json"
RENAMED = {"phone-handset": "phone"}
MIN_TAG_QUERY = 3
WORST_RANK = 4
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
PHOSPHOR_DIRECTORY = PACKAGE_DIRECTORY / "phosphor"
DEFAULT_LIMIT = 200
DEFAULT_LANGUAGE = "en"
DEFINITIONS = (
    ("phosphor", "phosphor:", {"en": "Phosphor", "fr": "Phosphor"}),
    ("emoji", "emoji:", {"en": "Emoji", "fr": "Émoji"}),
    ("brands", "brand:", {"en": "Brand", "fr": "Marque"}),
)


def is_phosphor(name: str | None) -> bool:
    return bool(name) and name.startswith(PHOSPHOR_PREFIX)


def phosphor_name(name: str) -> str:
    return name[len(PHOSPHOR_PREFIX):]


class IconSet:
    def __init__(self, key: str, prefix: str, directory: Path, titles: dict | None = None, renamed: dict | None = None):
        self.key = key
        self.prefix = prefix
        self.directory = directory
        self.titles = titles or {DEFAULT_LANGUAGE: key}
        self._renamed = renamed or {}
        self._loaded = False
        self._icons: dict = {}
        self._folded: dict = {}
        self._words: dict = {}
        self._names: list[str] = []
        self._revision = 1
        self._source = ""

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
            source = index.get("source", "")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            icons, revision, source = {}, 1, ""
        present = {path.name[: -len(SVG_SUFFIX)] for path in self.directory.glob("*" + SVG_SUFFIX)}
        if icons:
            icons = {name: entry for name, entry in icons.items() if name in present and isinstance(entry, dict)}
        else:
            icons = {name: {} for name in present}
        self._icons = icons
        self._revision = revision
        self._source = source if isinstance(source, str) else ""
        self._names = sorted(self.prefix + name for name in icons)
        self._folded = {name: [normalize_text(tag) for tag in entry.get("t", []) if isinstance(tag, str)] for name, entry in icons.items()}
        self._words = {name: [set(tag.split()) for tag in tags] for name, tags in self._folded.items()}

    @property
    def revision(self) -> int:
        self._load()
        return self._revision

    @property
    def source(self) -> str:
        self._load()
        return self._source

    def owns(self, name: str | None) -> bool:
        return bool(name) and name.startswith(self.prefix)

    def short_name(self, name: str) -> str:
        return name[len(self.prefix):] if self.owns(name) else ""

    def all_names(self) -> list[str]:
        self._load()
        return list(self._names)

    def tags(self, name: str) -> list[str]:
        self._load()
        entry = self._icons.get(self.short_name(name), {})
        return [tag for tag in entry.get("t", []) if isinstance(tag, str)]

    def categories(self) -> list[str]:
        self._load()
        found = {category for entry in self._icons.values() for category in entry.get("c", []) if isinstance(category, str)}
        return sorted(found)

    def names_in_category(self, category: str) -> list[str]:
        self._load()
        return sorted(self.prefix + name for name, entry in self._icons.items() if category in entry.get("c", []))

    def label(self, name: str, language: str = DEFAULT_LANGUAGE) -> str:
        self._load()
        short = self.short_name(name)
        names = self._icons.get(short, {}).get("n", {})
        if isinstance(names, dict):
            for code in (language, DEFAULT_LANGUAGE):
                if isinstance(names.get(code), str) and names[code]:
                    return names[code]
        return short.replace("-", " ")

    def title(self, language: str = DEFAULT_LANGUAGE) -> str:
        return self.titles.get(language) or self.titles.get(DEFAULT_LANGUAGE) or self.key

    def symbolic_names(self, query: str = "", limit: int = DEFAULT_LIMIT, max_rank: int = WORST_RANK) -> list[str]:
        self._load()
        needle = normalize_text(query.strip())
        if not needle:
            return list(self._names)[:limit]
        ranked = []
        for name in self._icons:
            if name == needle:
                rank = 0
            elif name.startswith(needle):
                rank = 1
            elif needle in name:
                rank = 2
            elif len(needle) >= MIN_TAG_QUERY and any(needle in words for words in self._words[name]):
                rank = 3
            elif len(needle) >= MIN_TAG_QUERY and any(needle in tag for tag in self._folded[name]):
                rank = 4
            else:
                continue
            if rank <= max_rank:
                ranked.append((rank, name))
        ranked.sort()
        return [self.prefix + name for _rank, name in ranked[:limit]]

    def file(self, name: str) -> Path:
        self._load()
        short = self.short_name(name)
        short = self._renamed.get(short, short)
        if short not in self._icons:
            raise IconNotFoundError(f"{name} not found in the {self.key} set")
        return self.directory / (short + SVG_SUFFIX)

    def glyph_file(self, name: str) -> Path:
        return self.file(name)

    def paintable(self, name: str, size: int, scale: int = 1):
        return Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(str(self.file(name))), size, scale)


class PhosphorSet(IconSet):
    def __init__(self, directory: Path = PHOSPHOR_DIRECTORY):
        super().__init__("phosphor", PHOSPHOR_PREFIX, directory, DEFINITIONS[0][2], RENAMED)


class IconSets:
    def __init__(self, root: Path = PACKAGE_DIRECTORY):
        self._sets = {}
        for key, prefix, titles in DEFINITIONS:
            if key == "phosphor":
                self._sets[key] = PhosphorSet(root / key)
            else:
                self._sets[key] = IconSet(key, prefix, root / key, titles)

    def __iter__(self):
        return iter(self._sets)

    def __getitem__(self, key: str) -> IconSet:
        return self._sets[key]

    def for_name(self, name: str | None) -> IconSet | None:
        for icon_set in self._sets.values():
            if icon_set.owns(name):
                return icon_set
        return None


_default: IconSets | None = None


def default_sets() -> IconSets:
    global _default
    if _default is None:
        _default = IconSets()
    return _default


def current_language() -> str:
    names = GLib.get_language_names()
    return "fr" if names and names[0].startswith("fr") else DEFAULT_LANGUAGE


def display_name(name: str, language: str = DEFAULT_LANGUAGE) -> str:
    icon_set = default_sets().for_name(name)
    if icon_set is None:
        return name
    return f"{icon_set.label(name, language)} ({icon_set.title(language)})"
