from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .color import auto_glyph_color, is_hex_color
from .compose import GlyphStyle, compose
from .errors import CusticonError, FolderError, IconNotFoundError, InvalidSettingsError, MetadataError
from .metadata import read_custom_icon, write_custom_icon
from .store import Entry, Store
from .themes import DEFAULT_THEME, IconTheme, active_icon_theme

MIN_SIZE = 0.2
MAX_SIZE = 3.0
MAX_OFFSET = 0.5

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

    def validate(self) -> None:
        if bool(self.glyph_name) == bool(self.glyph_file):
            raise InvalidSettingsError("choose either a symbolic icon name or a file")
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

    def build(self, settings: Settings, theme_name: str | None = None) -> BuiltIcon:
        settings.validate()
        name = theme_name or self._theme_name()
        theme = IconTheme(name, self._search_dirs)
        folder_file = theme.folder_file()
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
        built = self.build(settings)
        digest = self._store.save_icon(built.svg)
        key = str(target)
        existing = self._store.get(key)
        try:
            current = read_custom_icon(target)
            if self._store.owns_uri(current):
                previous = existing.previous if existing else None
            else:
                previous = current
            write_custom_icon(target, self._store.icon_uri(digest))
        except CusticonError:
            self._store.prune()
            raise
        entry = Entry(key, digest, built.theme, built.color, settings.to_dict(), previous)
        try:
            self._store.put(entry)
        except CusticonError:
            write_custom_icon(target, current)
            self._store.prune()
            raise
        self._store.prune()
        return entry

    def reset(self, folder: Path | str) -> str:
        key = str(Path(folder).expanduser().resolve())
        entry = self._store.get(key)
        if entry is None:
            return RESET_UNKNOWN
        target = Path(key)
        outcome = RESET_FOLDER_MISSING
        if target.is_dir():
            outcome = RESET_KEPT_USER_ICON
            if read_custom_icon(target) == self._store.icon_uri(entry.digest):
                write_custom_icon(target, entry.previous)
                outcome = RESET_RESTORED
        self._store.remove(key)
        self._store.prune()
        return outcome

    def forget(self, folder: Path | str) -> None:
        self._store.remove(str(Path(folder).expanduser().resolve()))
        self._store.prune()

    def customized(self) -> list[Customized]:
        return [Customized(entry, self._status(entry)) for entry in self._store.all().values()]

    def regenerate_all(self) -> RegenerationReport:
        report = RegenerationReport()
        for entry in list(self._store.all().values()):
            target = Path(entry.folder)
            status = self._status(entry)
            if status in (STATUS_FOLDER_MISSING, STATUS_ICON_CHANGED):
                report.skipped[entry.folder] = status
                continue
            try:
                updated = self.apply(target, Settings.from_dict(entry.settings))
            except CusticonError as error:
                report.failed[entry.folder] = str(error)
                continue
            (report.unchanged if updated.digest == entry.digest else report.updated).append(entry.folder)
        return report

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
    def _checked_folder(folder: Path | str) -> Path:
        target = Path(folder).expanduser().resolve()
        if not target.is_dir():
            raise FolderError(f"not a folder: {target}")
        return target

    def _find_glyph(self, theme: IconTheme, icon_name: str) -> Path:
        try:
            return theme.glyph_file(icon_name)
        except IconNotFoundError:
            return IconTheme(DEFAULT_THEME, self._search_dirs).glyph_file(icon_name)
