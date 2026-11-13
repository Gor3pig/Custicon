from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import math

from .color import auto_glyph_color, is_hex_color
from .compose import GlyphStyle, compose
from .errors import (
    CusticonError,
    FolderError,
    IconNotFoundError,
    InvalidIconError,
    InvalidSettingsError,
    MetadataError,
)
from .metadata import read_custom_icon, write_custom_icon
from .render import is_png, read_icon_bytes
from .store import GLYPH_FILE_PATTERN, Entry, Store
from .themes import DEFAULT_THEME, IconTheme, active_icon_theme

MIN_SIZE = 0.2
MAX_SIZE = 3.0
MAX_OFFSET = 0.5

APPEARANCE_FIELDS = ("color", "size", "offset_x", "offset_y", "tint")

STATUS_OK = "ok"
STATUS_FOLDER_MISSING = "folder-missing"
STATUS_ICON_CHANGED = "icon-changed"
STATUS_ICON_FILE_MISSING = "icon-file-missing"
STATUS_UNREADABLE = "unreadable"

RESET_RESTORED = "restored"
RESET_KEPT_USER_ICON = "kept-user-icon"
RESET_FOLDER_MISSING = "folder-missing"
RESET_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Settings:
    glyph_name: str | None = None
    glyph_file: str | None = None
    color: str | None = None
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    tint: bool = True
    glyph_copy: str | None = None

    def validate(self) -> None:
        for text in (self.glyph_name, self.glyph_file, self.color, self.glyph_copy):
            if text is not None and (not isinstance(text, str) or "\0" in text):
                raise InvalidSettingsError("invalid text in the settings")
        if not isinstance(self.tint, bool):
            raise InvalidSettingsError("invalid tint setting")
        for number in (self.size, self.offset_x, self.offset_y):
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise InvalidSettingsError("sizes and offsets must be finite numbers")
        if bool(self.glyph_name) == bool(self.glyph_file):
            raise InvalidSettingsError("choose either a symbolic icon name or a file")
        if self.glyph_copy is not None and GLYPH_FILE_PATTERN.fullmatch(self.glyph_copy) is None:
            raise InvalidSettingsError("invalid stored pictogram")
        if self.color is not None and not is_hex_color(self.color):
            raise InvalidSettingsError(f"invalid color {self.color!r}")
        if not MIN_SIZE <= self.size <= MAX_SIZE:
            raise InvalidSettingsError(f"size must be between {MIN_SIZE} and {MAX_SIZE}")
        for offset in (self.offset_x, self.offset_y):
            if not -MAX_OFFSET <= offset <= MAX_OFFSET:
                raise InvalidSettingsError(f"offset must be between {-MAX_OFFSET} and {MAX_OFFSET}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict) -> "Settings":
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in values.items() if key in known})


@dataclass(frozen=True)
class BuiltIcon:
    svg: str
    color: str
    theme: str
    folder_file: Path


@dataclass(frozen=True)
class Customized:
    entry: Entry
    status: str


@dataclass
class RegenerationReport:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(
        self,
        store: Store,
        theme_name: Callable[[], str] = active_icon_theme,
        search_dirs: list[str] | None = None,
    ):
        self._store = store
        self._theme_name = theme_name
        self._search_dirs = search_dirs
        self._themes: dict[str, IconTheme] = {}

    @property
    def store(self) -> Store:
        return self._store

    def refresh_themes(self) -> None:
        self._themes.clear()

    def _theme(self, name: str) -> IconTheme:
        if name not in self._themes:
            self._themes[name] = IconTheme(name, self._search_dirs)
        return self._themes[name]

    def build(self, settings: Settings, theme_name: str | None = None, glyph_file: Path | None = None) -> BuiltIcon:
        settings.validate()
        name = theme_name or self._theme_name()
        theme = self._theme(name)
        folder_file = theme.folder_file()
        if glyph_file is None:
            glyph_file = Path(settings.glyph_file) if settings.glyph_file else self._find_glyph(theme, settings.glyph_name)
        color = settings.color or auto_glyph_color(folder_file)
        style = GlyphStyle(
            color=color,
            size=settings.size,
            offset_x=settings.offset_x,
            offset_y=settings.offset_y,
            tint=settings.tint,
        )
        return BuiltIcon(compose(folder_file, glyph_file, style), color, name, folder_file)

    def apply(self, folder: Path | str, settings: Settings) -> Entry:
        target = self._checked_folder(folder)
        key = str(target)
        existing = self._store.get(key)
        settings.validate()
        glyph_file, glyph_copy = self._keep_glyph(settings, existing)
        built = self.build(settings, glyph_file=glyph_file)
        digest = self._store.save_icon(built.svg)
        try:
            current = read_custom_icon(target)
            if self._store.owns_uri(current):
                previous = existing.previous if existing else None
            else:
                previous = current
            stored = replace(settings, glyph_copy=glyph_copy).to_dict()
            entry = Entry(key, digest, built.theme, built.color, stored, previous)
            self._store.put(entry)
        except CusticonError:
            self._discard(digest, glyph_copy, existing)
            raise
        try:
            write_custom_icon(target, self._store.icon_uri(digest))
        except CusticonError:
            self._undo(target, key, current, existing)
            self._discard(digest, glyph_copy, existing)
            raise
        if existing is not None:
            if existing.digest != digest:
                self._store.release(existing.digest)
            old_copy = existing.settings.get("glyph_copy")
            if old_copy != glyph_copy:
                self._store.release_glyph(old_copy)
        return entry

    def reset(self, folder: Path | str) -> str:
        key, entry = self._locate(folder)
        if entry is None:
            return RESET_UNKNOWN
        target = self._resolved(key)
        outcome = RESET_FOLDER_MISSING
        if target.is_dir():
            outcome = RESET_KEPT_USER_ICON
            if read_custom_icon(target) == self._store.icon_uri(entry.digest):
                write_custom_icon(target, entry.previous)
                outcome = RESET_RESTORED
        self._store.remove(key)
        self._release(entry)
        return outcome

    def forget(self, folder: Path | str) -> None:
        key, entry = self._locate(folder)
        if entry is not None:
            self._store.remove(key)
            self._release(entry)

    def remembered_appearance(self) -> dict:
        stored = self._store.load_defaults() or {}
        values = {name: stored[name] for name in APPEARANCE_FIELDS if name in stored}
        try:
            Settings(glyph_name="x", **values).validate()
        except InvalidSettingsError:
            return {}
        return values

    def remember_appearance(self, settings: Settings) -> None:
        try:
            self._store.save_defaults({name: getattr(settings, name) for name in APPEARANCE_FIELDS})
        except CusticonError:
            pass

    def customized(self) -> list[Customized]:
        return [Customized(entry, self._status(entry)) for entry in self._store.all().values()]

    def regenerate_all(self) -> RegenerationReport:
        self.refresh_themes()
        report = RegenerationReport()
        for entry in list(self._store.all().values()):
            self.regenerate_entry(entry, report)
        return report

    def regenerate_entry(self, entry: Entry, report: RegenerationReport) -> None:
        status = self._status(entry)
        if status in (STATUS_FOLDER_MISSING, STATUS_ICON_CHANGED):
            report.skipped[entry.folder] = status
            return
        try:
            updated = self.apply(Path(entry.folder), Settings.from_dict(entry.settings))
        except (CusticonError, OSError) as error:
            report.failed[entry.folder] = str(error)
            return
        (report.unchanged if updated.digest == entry.digest else report.updated).append(entry.folder)

    def entry_status(self, entry: Entry) -> str:
        return self._status(entry)

    def folder_file(self, theme_name: str | None = None) -> Path:
        return self._theme(theme_name or self._theme_name()).folder_file()

    def current_theme(self) -> str:
        return self._theme_name()

    def icon_theme(self, theme_name: str | None = None) -> IconTheme:
        return self._theme(theme_name or self._theme_name())

    def _status(self, entry: Entry) -> str:
        target = Path(entry.folder)
        if not target.is_dir():
            return STATUS_FOLDER_MISSING
        try:
            current = read_custom_icon(target)
        except MetadataError:
            return STATUS_UNREADABLE
        if current != self._store.icon_uri(entry.digest):
            return STATUS_ICON_CHANGED
        if not self._store.icon_path(entry.digest).exists():
            return STATUS_ICON_FILE_MISSING
        return STATUS_OK

    @staticmethod
    def _resolved(folder: Path | str) -> Path:
        try:
            return Path(folder).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as error:
            raise FolderError(f"unusable folder path: {error}") from error

    def _checked_folder(self, folder: Path | str) -> Path:
        target = self._resolved(folder)
        try:
            str(target).encode("utf-8")
        except UnicodeEncodeError as error:
            raise FolderError("the folder name is not valid UTF-8") from error
        if not target.is_dir():
            raise FolderError(f"not a folder: {target}")
        return target

    def _locate(self, folder: Path | str) -> tuple[str, Entry | None]:
        try:
            given = str(Path(folder).expanduser())
        except (RuntimeError, ValueError):
            given = str(folder)
        try:
            resolved = str(self._resolved(folder))
        except FolderError:
            resolved = given
        for key in dict.fromkeys((given, resolved)):
            entry = self._store.get(key)
            if entry is not None:
                return key, entry
        return resolved, None

    def _keep_glyph(self, settings: Settings, existing: Entry | None) -> tuple[Path | None, str | None]:
        if not settings.glyph_file:
            return None, None
        stored = self._store.glyph_path(settings.glyph_copy)
        if stored is not None and stored.is_file():
            return stored, settings.glyph_copy
        try:
            data = read_icon_bytes(Path(settings.glyph_file))
        except InvalidIconError:
            old = existing.settings if existing else {}
            old_path = self._store.glyph_path(old.get("glyph_copy"))
            if old.get("glyph_file") == settings.glyph_file and old_path is not None and old_path.is_file():
                return old_path, old["glyph_copy"]
            raise
        name = self._store.save_glyph(data, ".png" if is_png(data) else ".svg")
        return self._store.glyph_path(name), name

    def _undo(self, target: Path, key: str, current: str | None, existing: Entry | None) -> None:
        try:
            if existing is None:
                self._store.remove(key)
            else:
                self._store.put(existing)
        except CusticonError:
            pass
        try:
            write_custom_icon(target, current)
        except CusticonError:
            pass

    def _discard(self, digest: str, glyph_copy: str | None, existing: Entry | None) -> None:
        self._store.release(digest)
        self._store.release_glyph(glyph_copy)

    def _release(self, entry: Entry) -> None:
        self._store.release(entry.digest)
        self._store.release_glyph(entry.settings.get("glyph_copy"))

    def _find_glyph(self, theme: IconTheme, icon_name: str) -> Path:
        try:
            return theme.glyph_file(icon_name)
        except IconNotFoundError:
            return self._theme(DEFAULT_THEME).glyph_file(icon_name)
