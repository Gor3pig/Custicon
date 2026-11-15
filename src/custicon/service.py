import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import math
import threading

from .color import auto_glyph_color, is_hex_color
from .compose import (
    BADGE_CORNERS,
    BADGE_DEFAULT_COLOR,
    BADGE_DEFAULT_SIZE,
    BADGE_MAX_SIZE,
    BADGE_MIN_SIZE,
    Badge,
    GlyphStyle,
    compose,
    tinted_folder_svg,
)
from .errors import (
    CusticonError,
    FolderError,
    IconNotFoundError,
    InvalidIconError,
    InvalidSettingsError,
    MetadataError,
    StoreError,
)
from . import backup, dotdirectory
from .environment import BACKEND_DIRECTORY, BACKEND_GVFS
from .metadata import read_custom_icon, write_custom_icon
from .render import is_png, read_icon_bytes
from .store import GLYPH_FILE_PATTERN, MAX_NAME_LENGTH, MAX_STYLE_NAME, MAX_STYLES, Entry, Store
from .textglyph import MAX_CHARACTERS, text_to_svg
from . import customsets, iconsets
from .customsets import AddReport, ImportReport, SetInfo
from .iconsets import IconSets, PhosphorSet
from .themes import DEFAULT_THEME, IconTheme, active_icon_theme, system_accent_color

MIN_SIZE = 0.2
MAX_SIZE = 3.0
MAX_OFFSET = 0.5

BADGE_FIELDS = ("badge_glyph", "badge_color", "badge_corner", "badge_size")
APPEARANCE_FIELDS = ("color", "accent", "size", "offset_x", "offset_y", "tint", "folder_tint", "folder_tint_accent")

STATUS_OK = "ok"
STATUS_FOLDER_MISSING = "folder-missing"
STATUS_ICON_CHANGED = "icon-changed"
STATUS_ICON_FILE_MISSING = "icon-file-missing"
STATUS_UNREADABLE = "unreadable"

RESET_RESTORED = "restored"
RESET_KEPT_USER_ICON = "kept-user-icon"
RESET_FOLDER_MISSING = "folder-missing"
RESET_UNKNOWN = "unknown"
REPAIR_REGENERATE = "regenerate"
REPAIR_TAKE_BACK = "take-back"
REPAIR_FORGET = "forget"
REPAIR_ACTIONS = {
    STATUS_ICON_FILE_MISSING: REPAIR_REGENERATE,
    STATUS_ICON_CHANGED: REPAIR_TAKE_BACK,
    STATUS_FOLDER_MISSING: REPAIR_FORGET,
}


def is_clean_text(text) -> bool:
    if not isinstance(text, str) or "\0" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass(frozen=True)
class Settings:
    glyph_name: str | None = None
    glyph_file: str | None = None
    glyph_text: str | None = None
    color: str | None = None
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    tint: bool = True
    accent: bool = False
    glyph_copy: str | None = None
    folder_tint: str | None = None
    folder_tint_accent: bool = False
    glyph_revision: int | None = None
    badge_glyph: str | None = None
    badge_color: str | None = None
    badge_corner: str = "br"
    badge_size: float = BADGE_DEFAULT_SIZE

    def validate(self) -> None:
        self._validate_badge()
        for text in (self.glyph_name, self.glyph_file, self.glyph_text, self.color, self.glyph_copy, self.folder_tint):
            if text is not None and not is_clean_text(text):
                raise InvalidSettingsError("invalid text in the settings")
        if not isinstance(self.tint, bool) or not isinstance(self.accent, bool):
            raise InvalidSettingsError("invalid tint or accent setting")
        if self.glyph_revision is not None and (isinstance(self.glyph_revision, bool) or not isinstance(self.glyph_revision, int)):
            raise InvalidSettingsError("invalid pictogram set revision")
        if not isinstance(self.folder_tint_accent, bool):
            raise InvalidSettingsError("invalid folder tint setting")
        if self.folder_tint is not None and not is_hex_color(self.folder_tint):
            raise InvalidSettingsError(f"invalid folder tint {self.folder_tint!r}")
        if self.accent and self.color is not None:
            raise InvalidSettingsError("choose either the system accent or a color")
        for number in (self.size, self.offset_x, self.offset_y):
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise InvalidSettingsError("sizes and offsets must be finite numbers")
        sources = sum(bool(source) for source in (self.glyph_name, self.glyph_file, self.glyph_text))
        if sources > 1 or (sources == 0 and not self.badge_glyph):
            raise InvalidSettingsError("choose either a symbolic icon name, a file or a text")
        if self.glyph_text is not None and len(self.glyph_text) > MAX_CHARACTERS:
            raise InvalidSettingsError(f"use at most {MAX_CHARACTERS} characters")
        if self.glyph_copy is not None and GLYPH_FILE_PATTERN.fullmatch(self.glyph_copy) is None:
            raise InvalidSettingsError("invalid stored pictogram")
        if self.color is not None and not is_hex_color(self.color):
            raise InvalidSettingsError(f"invalid color {self.color!r}")
        if not MIN_SIZE <= self.size <= MAX_SIZE:
            raise InvalidSettingsError(f"size must be between {MIN_SIZE} and {MAX_SIZE}")
        for offset in (self.offset_x, self.offset_y):
            if not -MAX_OFFSET <= offset <= MAX_OFFSET:
                raise InvalidSettingsError(f"offset must be between {-MAX_OFFSET} and {MAX_OFFSET}")

    def _validate_badge(self) -> None:
        for text in (self.badge_glyph, self.badge_color):
            if text is not None and (not is_clean_text(text) or not text):
                raise InvalidSettingsError("invalid text in the badge settings")
        if self.badge_color is not None and not is_hex_color(self.badge_color):
            raise InvalidSettingsError(f"invalid badge color {self.badge_color!r}")
        if self.badge_corner not in BADGE_CORNERS:
            raise InvalidSettingsError(f"invalid badge corner {self.badge_corner!r}")
        size = self.badge_size
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size):
            raise InvalidSettingsError("the badge size must be a finite number")
        if not BADGE_MIN_SIZE <= size <= BADGE_MAX_SIZE:
            raise InvalidSettingsError(f"the badge size must be between {BADGE_MIN_SIZE} and {BADGE_MAX_SIZE}")

    def to_dict(self) -> dict:
        values = asdict(self)
        if self.badge_glyph is None:
            for name in BADGE_FIELDS:
                del values[name]
        return values

    @classmethod
    def from_dict(cls, values: dict) -> "Settings":
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in values.items() if key in known})


@dataclass(frozen=True)
class Style:
    name: str
    appearance: dict


@dataclass(frozen=True)
class BuiltIcon:
    svg: str
    color: str
    theme: str
    folder_file: Path
    folder_tint: str | None = None


@dataclass(frozen=True)
class PickerMemory:
    recent: tuple
    favorites: tuple


@dataclass(frozen=True)
class Customized:
    entry: Entry
    status: str


@dataclass(frozen=True)
class Snapshot:
    entry: Entry
    glyph: bytes | None


@dataclass
class ResetReport:
    restored: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WindowState:
    width: int
    height: int
    maximized: bool


@dataclass
class BackupSummary:
    folders: int
    styles: int
    pictograms: int


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    styles_added: int = 0
    styles_rejected: int = 0


@dataclass
class ApplyReport:
    applied: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Problem:
    folder: str
    status: str
    action: str


@dataclass
class RepairReport:
    regenerated: list[str] = field(default_factory=list)
    taken_back: list[str] = field(default_factory=list)
    forgotten: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


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
        accent_color: Callable[[], str] = system_accent_color,
        backend: str = BACKEND_GVFS,
    ):
        self._backend = backend
        self._store = store
        self._theme_name = theme_name
        self._search_dirs = search_dirs
        self._accent_color = accent_color
        self._themes: dict[str, IconTheme] = {}
        self._sets = IconSets(user_root=store.sets_dir)
        self._sets_lock = threading.RLock()
        iconsets.default_sets().set_user_root(store.sets_dir)

    @property
    def store(self) -> Store:
        return self._store

    def refresh_themes(self) -> None:
        self._themes.clear()

    def _theme(self, name: str) -> IconTheme:
        if name not in self._themes:
            self._themes[name] = IconTheme(name, self._search_dirs)
        return self._themes[name]

    def build(self, settings: Settings, theme_name: str | None = None, glyph_file: Path | None = None, flat_tint: bool = False) -> BuiltIcon:
        settings.validate()
        name = theme_name or self._theme_name()
        theme = self._theme(name)
        folder_file = theme.folder_file()
        if glyph_file is None:
            glyph_file = self._glyph_source(theme, settings)
        folder_tint = self._accent_color() if settings.folder_tint_accent else settings.folder_tint
        folder_data = None if folder_tint is None else tinted_folder_svg(folder_file, folder_tint, flat_tint)
        color = settings.color or (self._accent_color() if settings.accent else auto_glyph_color(folder_file, folder_data))
        style = GlyphStyle(
            color=color,
            size=settings.size,
            offset_x=settings.offset_x,
            offset_y=settings.offset_y,
            tint=settings.tint if settings.glyph_file else True,
            folder_tint=folder_tint,
            flat_tint=flat_tint,
            badge=self._badge(theme, settings),
        )
        return BuiltIcon(compose(folder_file, glyph_file, style), color, name, folder_file, folder_tint)

    def _badge(self, theme: IconTheme, settings: Settings) -> Badge | None:
        if not settings.badge_glyph:
            return None
        return Badge(
            self._find_glyph(theme, settings.badge_glyph),
            settings.badge_color or BADGE_DEFAULT_COLOR,
            settings.badge_corner,
            settings.badge_size,
        )

    def _glyph_revision(self, settings: Settings) -> int | None:
        icon_set = self._sets.for_name(settings.glyph_name)
        return icon_set.revision if icon_set else None

    def _set_is_stale(self, entry: Entry) -> bool:
        icon_set = self._sets.for_name(entry.settings.get("glyph_name"))
        return icon_set is not None and entry.settings.get("glyph_revision") != icon_set.revision

    def accent_color(self) -> str:
        return self._accent_color()

    def folder_preview(self, settings: Settings, theme_name: str | None = None) -> tuple[str | None, str | None]:
        folder_tint = self._accent_color() if settings.folder_tint_accent else settings.folder_tint
        if folder_tint is None:
            return None, None
        folder_file = self._theme(theme_name or self._theme_name()).folder_file()
        return tinted_folder_svg(folder_file, folder_tint).decode(), folder_tint

    def apply(self, folder: Path | str, settings: Settings, backend: str | None = None) -> Entry:
        target = self._checked_folder(folder, self._keeps_links(backend or self._backend))
        key = str(target)
        existing = self._store.get(key)
        migrated = existing is not None and backend is None and existing.backend != self._backend
        base = None if migrated else existing
        backend = existing.backend if base is not None else (backend or self._backend)
        settings.validate()
        glyph_file, glyph_copy = self._keep_glyph(settings, existing)
        if migrated:
            self._clear_mark(target, existing)
        built = self.build(settings, glyph_file=glyph_file, flat_tint=backend == BACKEND_DIRECTORY)
        digest = self._store.save_icon(built.svg)
        try:
            current = self._read_mark(target, backend)
            directory_created = self._directory_created(target, backend, base)
            if self._store.owns_uri(current):
                previous = base.previous if base else None
            else:
                previous = current
            stored = replace(
                settings,
                glyph_copy=glyph_copy,
                folder_tint=built.folder_tint if settings.folder_tint_accent else settings.folder_tint,
                glyph_revision=self._glyph_revision(settings),
            ).to_dict()
            entry = Entry(key, digest, built.theme, built.color, stored, previous, backend, directory_created)
            self._store.put(entry)
        except CusticonError:
            self._discard(digest, glyph_copy, existing)
            raise
        try:
            self._write_mark(target, self._store.icon_uri(digest), backend, directory_created)
        except CusticonError:
            self._undo(target, key, current, base, backend, directory_created)
            self._discard(digest, glyph_copy, existing)
            raise
        if existing is not None:
            if existing.digest != digest:
                self._store.release(existing.digest)
            old_copy = existing.settings.get("glyph_copy")
            if old_copy != glyph_copy:
                self._store.release_glyph(old_copy)
        return entry

    def _clear_mark(self, target: Path, entry: Entry) -> None:
        try:
            if self._store.owns_uri(self._read_mark(target, entry.backend)):
                self._write_mark(target, entry.previous, entry.backend, entry.directory_created)
        except CusticonError:
            pass

    def apply_many(self, folders: list[Path | str], settings: Settings) -> ApplyReport:
        report = ApplyReport()
        for folder in folders:
            try:
                report.applied.append(self.apply(folder, settings).folder)
            except CusticonError as error:
                report.failed[str(folder)] = str(error)
        return report

    def reset(self, folder: Path | str) -> str:
        key, entry = self._locate(folder)
        if entry is None:
            return RESET_UNKNOWN
        target = self._resolved(key, self._keeps_links(entry.backend))
        outcome = RESET_FOLDER_MISSING
        if target.is_dir():
            outcome = RESET_KEPT_USER_ICON
            if self._read_mark(target, entry.backend) == self._store.icon_uri(entry.digest):
                self._write_mark(target, entry.previous, entry.backend, entry.directory_created)
                outcome = RESET_RESTORED
        self._store.remove(key)
        self._release(entry)
        return outcome

    def reset_all(self) -> ResetReport:
        report = ResetReport()
        for entry in list(self._store.all().values()):
            try:
                outcome = self.reset(entry.folder)
            except (CusticonError, OSError) as error:
                report.failed[entry.folder] = str(error)
                continue
            if outcome == RESET_RESTORED:
                report.restored.append(entry.folder)
            elif outcome == RESET_KEPT_USER_ICON:
                report.kept.append(entry.folder)
            else:
                report.missing.append(entry.folder)
        return report

    def reset_many(self, folders: list[Path | str]) -> ResetReport:
        report = ResetReport()
        for folder in folders:
            try:
                key = str(self.folder_key(folder))
                outcome = self.reset(folder)
            except (CusticonError, OSError) as error:
                report.failed[str(folder)] = str(error)
                continue
            if outcome == RESET_RESTORED:
                report.restored.append(key)
            elif outcome == RESET_KEPT_USER_ICON:
                report.kept.append(key)
            elif outcome == RESET_FOLDER_MISSING:
                report.missing.append(key)
        return report

    def snapshot(self, key: str) -> Snapshot | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        glyph = None
        copy = self._store.glyph_path(entry.settings.get("glyph_copy"))
        if copy is not None and copy.is_file():
            try:
                glyph = read_icon_bytes(copy)
            except InvalidIconError:
                glyph = None
        return Snapshot(entry, glyph)

    def restore(self, snapshot: Snapshot) -> Entry:
        entry = snapshot.entry
        copy = entry.settings.get("glyph_copy")
        if snapshot.glyph is not None and isinstance(copy, str):
            self._store.save_glyph(snapshot.glyph, Path(copy).suffix)
        return self.apply(Path(entry.folder), Settings.from_dict(entry.settings), entry.backend)

    def relocate(self, old: str, folder: Path | str) -> Entry:
        entry = self._store.get(old)
        if entry is None:
            raise FolderError("this folder is not in the list")
        if Path(old).is_dir():
            raise FolderError("this folder still exists")
        target = self._checked_folder(folder, self._keeps_links(entry.backend))
        key = str(target)
        if self._store.get(key) is not None:
            raise FolderError("this folder is already customized")
        current = self._read_mark(target, entry.backend)
        uri = self._store.icon_uri(entry.digest)
        previous = entry.previous if self._store.owns_uri(current) else current
        directory_created = self._directory_created(target, entry.backend, None)
        moved = replace(entry, folder=key, previous=previous, directory_created=directory_created)
        self._store.put(moved)
        try:
            if current != uri:
                self._write_mark(target, uri, entry.backend, directory_created)
        except CusticonError:
            self._store.remove(key)
            raise
        self._store.remove(old)
        return moved

    def cleanup(self) -> list[str]:
        try:
            removed = self._store.prune()
        except CusticonError:
            removed = []
        self._store.remove_temporary_files()
        return removed

    def forget(self, folder: Path | str) -> None:
        key, entry = self._locate(folder)
        if entry is not None:
            self._store.remove(key)
            self._release(entry)

    @staticmethod
    def clean_appearance(stored: dict) -> dict | None:
        values = {name: stored[name] for name in APPEARANCE_FIELDS if name in stored}
        try:
            Settings(glyph_name="x", **values).validate()
        except InvalidSettingsError:
            return None
        return values

    def remembered_appearance(self) -> dict:
        return self.clean_appearance(self._store.load_defaults() or {}) or {}

    def window_state(self) -> WindowState | None:
        loaded = self._store.load_window()
        return WindowState(loaded["width"], loaded["height"], loaded["maximized"]) if loaded else None

    def remember_window(self, width: int, height: int, maximized: bool) -> bool:
        try:
            self._store.save_window(width, height, maximized)
        except CusticonError:
            return False
        return True

    def picker_memory(self) -> PickerMemory:
        loaded = self._store.load_picker()
        return PickerMemory(tuple(loaded["recent"]), tuple(loaded["favorites"]))

    @staticmethod
    def _usable_name(name) -> bool:
        return isinstance(name, str) and 1 <= len(name) <= MAX_NAME_LENGTH and "\0" not in name

    def remember_glyph(self, name) -> bool:
        if not self._usable_name(name):
            return False
        memory = self.picker_memory()
        try:
            self._store.save_picker([name, *memory.recent], list(memory.favorites))
        except CusticonError:
            return False
        return True

    def set_favorite(self, name, favorite: bool) -> bool:
        if not self._usable_name(name):
            return False
        memory = self.picker_memory()
        favorites = [name, *memory.favorites] if favorite else [item for item in memory.favorites if item != name]
        try:
            self._store.save_picker(list(memory.recent), favorites)
        except CusticonError:
            return False
        return True

    def styles(self) -> list[Style]:
        styles = []
        for item in self._store.load_styles():
            appearance = self.clean_appearance(item["appearance"])
            if appearance is not None:
                styles.append(Style(item["name"], appearance))
        return styles

    def save_style(self, name: str, appearance: dict) -> None:
        name = name.strip() if isinstance(name, str) else ""
        if not 1 <= len(name) <= MAX_STYLE_NAME:
            raise InvalidSettingsError(f"a style name has 1 to {MAX_STYLE_NAME} characters")
        cleaned = self.clean_appearance(appearance)
        if cleaned is None:
            raise InvalidSettingsError("invalid appearance")
        kept = [style for style in self.styles() if style.name.casefold() != name.casefold()]
        if len(kept) >= MAX_STYLES:
            raise InvalidSettingsError(f"at most {MAX_STYLES} styles can be saved")
        self._store.save_styles([{"name": style.name, "appearance": style.appearance} for style in kept] + [{"name": name, "appearance": cleaned}])

    def delete_style(self, name: str) -> None:
        kept = [style for style in self.styles() if style.name != name]
        self._store.save_styles([{"name": style.name, "appearance": style.appearance} for style in kept])

    def export_styles(self, path: Path | str) -> int:
        styles = [{"name": style.name, "appearance": style.appearance} for style in self.styles()]
        try:
            with open(path, "wb") as handle:
                handle.write(self._store.dump_styles(styles))
        except OSError as error:
            raise StoreError(f"cannot write {Path(path).name}: {error.strerror}") from error
        return len(styles)

    def import_styles(self, path: Path | str) -> tuple[int, int]:
        try:
            data = read_icon_bytes(Path(path))
            incoming = json.loads(data.decode("utf-8"))
        except (InvalidIconError, UnicodeDecodeError, ValueError, RecursionError) as error:
            raise StoreError("this is not a Custicon styles file") from error
        if not isinstance(incoming, dict) or not isinstance(incoming.get("styles"), list):
            raise StoreError("this is not a Custicon styles file")
        return self._merge_styles(incoming["styles"])

    def _merge_styles(self, items: list) -> tuple[int, int]:
        merged = {style.name.casefold(): style for style in self.styles()}
        imported = rejected = 0
        for item in items:
            style = self._valid_style(item)
            if style is None or (style.name.casefold() not in merged and len(merged) >= MAX_STYLES):
                rejected += 1
                continue
            merged[style.name.casefold()] = style
            imported += 1
        if imported:
            self._store.save_styles([{"name": style.name, "appearance": style.appearance} for style in merged.values()])
        return imported, rejected

    def export_backup(self, path: Path | str) -> BackupSummary:
        from .config import VERSION

        entries = list(self._store.all().values())
        styles = self.styles()
        memory = self.picker_memory()
        document = backup.build_document(VERSION, entries, styles, self._store.load_defaults(), memory)
        try:
            with open(path, "wb") as handle:
                handle.write(backup.dump(document))
        except OSError as error:
            raise StoreError(f"cannot write {Path(path).name}: {error.strerror}") from error
        return BackupSummary(len(entries), len(styles), len(set(memory.recent) | set(memory.favorites)))

    def inspect_backup(self, path: Path | str) -> int:
        return len(backup.folder_items(self._read_backup(path)))

    def _read_backup(self, path: Path | str) -> dict:
        try:
            with open(path, "rb") as handle:
                data = handle.read(backup.MAX_BYTES + 1)
        except OSError as error:
            raise StoreError(f"cannot read {Path(path).name}: {error.strerror}") from error
        return backup.parse(data)

    def import_backup(self, path: Path | str) -> RestoreReport:
        document = self._read_backup(path)
        report = RestoreReport()
        for folder, values in backup.folder_items(document):
            self._restore_folder(folder, values, report)
        styles = document.get("styles")
        if isinstance(styles, list):
            before = len(self.styles())
            _imported, report.styles_rejected = self._merge_styles(styles)
            report.styles_added = len(self.styles()) - before
        self._restore_defaults(document.get("defaults"))
        self._restore_picker(document.get("picker"))
        return report

    def _restore_folder(self, folder: str, values: dict, report: RestoreReport) -> None:
        try:
            target = self._resolved(folder, self._keeps_links(self._backend))
            key = str(target)
            if not target.is_dir():
                report.missing.append(key)
                return
            existing = self._store.get(key)
            if existing is not None and self._status(existing) == STATUS_ICON_CHANGED:
                report.kept.append(key)
                return
            self.apply(target, Settings.from_dict(values))
        except (CusticonError, OSError, TypeError, ValueError) as error:
            report.failed[folder] = str(error)
            return
        report.restored.append(key)

    def _restore_defaults(self, values) -> None:
        if not isinstance(values, dict) or self._store.load_defaults() is not None:
            return
        cleaned = self.clean_appearance(values)
        if cleaned:
            try:
                self._store.save_defaults(cleaned)
            except CusticonError:
                pass

    def _restore_picker(self, picker) -> None:
        if not isinstance(picker, dict):
            return
        memory = self.picker_memory()
        recent = [*memory.recent, *[name for name in Store._clean_names(picker.get("recent"), 1000) if name not in memory.recent]]
        favorites = [*memory.favorites, *[name for name in Store._clean_names(picker.get("favorites"), 1000) if name not in memory.favorites]]
        try:
            self._store.save_picker(recent, favorites)
        except CusticonError:
            pass

    def _valid_style(self, item) -> Style | None:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("appearance"), dict):
            return None
        name = item["name"].strip()
        if not 1 <= len(name) <= MAX_STYLE_NAME:
            return None
        appearance = self.clean_appearance(item["appearance"])
        return None if appearance is None else Style(name, appearance)

    def remember_appearance(self, settings: Settings) -> None:
        try:
            self._store.save_defaults({name: getattr(settings, name) for name in APPEARANCE_FIELDS})
        except CusticonError:
            pass

    def outdated(self, known: list[Customized] | None = None) -> list[str]:
        theme = self._theme_name()
        accent = None
        stale = []
        if known is None:
            known = self.customized()
        for item in known:
            entry = item.entry
            if item.status != STATUS_OK:
                continue
            if entry.theme != theme or self._set_is_stale(entry):
                stale.append(entry.folder)
            else:
                follows_glyph = entry.settings.get("accent") is True
                follows_folder = entry.settings.get("folder_tint_accent") is True
                if not (follows_glyph or follows_folder):
                    continue
                accent = accent or self._accent_color()
                if (follows_glyph and entry.color != accent) or (
                    follows_folder and entry.settings.get("folder_tint") != accent
                ):
                    stale.append(entry.folder)
        return stale

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

    def problems(self, known: list[Customized] | None = None) -> list[Problem]:
        if known is None:
            known = self.customized()
        return [
            Problem(item.entry.folder, item.status, REPAIR_ACTIONS[item.status])
            for item in known
            if item.status in REPAIR_ACTIONS
        ]

    def repair(self, folders: list[str]) -> RepairReport:
        report = RepairReport()
        for key in dict.fromkeys(folders):
            entry = self._store.get(key)
            if entry is None:
                report.skipped.append(key)
                continue
            action = REPAIR_ACTIONS.get(self._status(entry))
            if action is None:
                report.skipped.append(key)
                continue
            try:
                if action == REPAIR_FORGET:
                    self.forget(key)
                    report.forgotten.append(key)
                else:
                    self.apply(Path(key), Settings.from_dict(entry.settings), entry.backend)
                    (report.regenerated if action == REPAIR_REGENERATE else report.taken_back).append(key)
            except (CusticonError, OSError) as error:
                report.failed[key] = str(error)
        return report

    def folder_file(self, theme_name: str | None = None) -> Path:
        return self._theme(theme_name or self._theme_name()).folder_file()

    def current_theme(self) -> str:
        return self._theme_name()

    def phosphor_set(self) -> PhosphorSet:
        return self._sets["phosphor"]

    def icon_sets(self) -> IconSets:
        return self._sets

    def custom_sets(self) -> list[SetInfo]:
        return customsets.list_sets(self._store.sets_dir)

    def import_set(self, folder: Path | str, name: str) -> ImportReport:
        with self._sets_lock:
            report = customsets.import_set(self._store.sets_dir, Path(folder), name)
            self._refresh_sets()
        return report

    def set_icons(self, slug: str) -> list[str]:
        return customsets.icon_names(self._store.sets_dir, slug)

    def add_icons(self, slug: str, files: list) -> AddReport:
        with self._sets_lock:
            report = customsets.add_icons(self._store.sets_dir, slug, files)
            self._refresh_sets()
        return report

    def remove_icon(self, slug: str, icon: str) -> None:
        with self._sets_lock:
            customsets.remove_icon(self._store.sets_dir, slug, icon)
            self._refresh_sets()

    def remove_set(self, slug: str) -> None:
        with self._sets_lock:
            customsets.remove_set(self._store.sets_dir, slug)
            self._refresh_sets()

    def _refresh_sets(self) -> None:
        self._sets.refresh()
        iconsets.default_sets().refresh()

    def icon_theme(self, theme_name: str | None = None) -> IconTheme:
        return self._theme(theme_name or self._theme_name())

    def _status(self, entry: Entry) -> str:
        target = Path(entry.folder)
        if not target.is_dir():
            return STATUS_FOLDER_MISSING
        try:
            current = self._read_mark(target, entry.backend)
        except MetadataError:
            return STATUS_UNREADABLE
        if current != self._store.icon_uri(entry.digest):
            return STATUS_ICON_CHANGED
        if not self._store.icon_path(entry.digest).exists():
            return STATUS_ICON_FILE_MISSING
        return STATUS_OK

    @staticmethod
    def _keeps_links(backend: str) -> bool:
        return backend != BACKEND_DIRECTORY

    @staticmethod
    def _resolved(folder: Path | str, keep_link: bool = False) -> Path:
        try:
            path = Path(folder).expanduser()
            if keep_link and path.is_symlink():
                return path.parent.resolve() / path.name
            return path.resolve()
        except (OSError, RuntimeError, ValueError) as error:
            raise FolderError(f"unusable folder path: {error}") from error

    def folder_key(self, folder: Path | str) -> Path:
        return self._resolved(folder, self._keeps_links(self._backend))

    def _checked_folder(self, folder: Path | str, keep_link: bool = False) -> Path:
        target = self._resolved(folder, keep_link)
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
            link = str(self._resolved(folder, True))
            real = str(self._resolved(folder, False))
        except FolderError:
            link = real = given
        for key in dict.fromkeys((given, link, real)):
            entry = self._store.get(key)
            if entry is None:
                continue
            if key == real and key != link and self._keeps_links(entry.backend):
                continue
            return key, entry
        return real, None

    def _glyph_source(self, theme: IconTheme, settings: Settings) -> Path | bytes | None:
        if not (settings.glyph_name or settings.glyph_file or settings.glyph_text):
            return None
        if settings.glyph_file:
            return Path(settings.glyph_file)
        if settings.glyph_text:
            return text_to_svg(settings.glyph_text).encode()
        if settings.glyph_name.startswith(customsets.KEY_PREFIX):
            return self._locate_set_glyph(settings)[0]
        return self._find_glyph(theme, settings.glyph_name)

    def _locate_set_glyph(self, settings: Settings) -> tuple[Path, bool]:
        name = settings.glyph_name
        icon_set = self._sets.for_name(name)
        try:
            if icon_set is None:
                raise IconNotFoundError(f"{name}: this icon set is not installed")
            return icon_set.file(name), True
        except IconNotFoundError:
            stored = self._store.glyph_path(settings.glyph_copy)
            if stored is not None and stored.is_file():
                return stored, False
            raise

    def _keep_set_glyph(self, settings: Settings) -> tuple[Path | bytes | None, str | None] | None:
        name = settings.glyph_name
        if not name or not name.startswith(customsets.KEY_PREFIX):
            return None
        path, from_set = self._locate_set_glyph(settings)
        if from_set:
            try:
                data = read_icon_bytes(path)
            except InvalidIconError:
                stored = self._store.glyph_path(settings.glyph_copy)
                if stored is not None and stored.is_file():
                    return stored, settings.glyph_copy
                raise
            copy = self._store.save_glyph(data, ".svg")
            return self._store.glyph_path(copy), copy
        return path, settings.glyph_copy

    def _keep_glyph(self, settings: Settings, existing: Entry | None) -> tuple[Path | bytes | None, str | None]:
        kept = self._keep_set_glyph(settings)
        if kept is not None:
            return kept
        if not settings.glyph_file and not settings.glyph_text:
            return None, None
        stored = self._store.glyph_path(settings.glyph_copy)
        if stored is not None and stored.is_file():
            return stored, settings.glyph_copy
        if settings.glyph_text:
            name = self._store.save_glyph(text_to_svg(settings.glyph_text).encode(), ".svg")
            return self._store.glyph_path(name), name
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

    def _undo(self, target: Path, key: str, current: str | None, existing: Entry | None, backend: str = BACKEND_GVFS, directory_created: bool = False) -> None:
        try:
            if existing is None:
                self._store.remove(key)
            else:
                self._store.put(existing)
        except CusticonError:
            pass
        try:
            self._write_mark(target, current, backend, directory_created)
        except CusticonError:
            pass

    @staticmethod
    def _read_mark(target: Path, backend: str) -> str | None:
        if backend == BACKEND_DIRECTORY:
            return dotdirectory.read_icon(target)
        return read_custom_icon(target)

    @staticmethod
    def _write_mark(target: Path, uri: str | None, backend: str, directory_created: bool = False) -> None:
        if backend == BACKEND_DIRECTORY:
            dotdirectory.write_icon(target, uri, created_by_us=directory_created)
            dotdirectory.notify(target)
        else:
            write_custom_icon(target, uri)

    @staticmethod
    def _directory_created(target: Path, backend: str, existing: Entry | None) -> bool:
        if backend != BACKEND_DIRECTORY:
            return False
        absent = not dotdirectory.file_of(target).exists()
        if existing is not None:
            return existing.directory_created or absent
        return absent

    def _discard(self, digest: str, glyph_copy: str | None, existing: Entry | None) -> None:
        self._store.release(digest)
        self._store.release_glyph(glyph_copy)

    def _release(self, entry: Entry) -> None:
        self._store.release(entry.digest)
        self._store.release_glyph(entry.settings.get("glyph_copy"))

    def _find_glyph(self, theme: IconTheme, icon_name: str) -> Path:
        icon_set = self._sets.for_name(icon_name)
        if icon_set is not None:
            return icon_set.file(icon_name)
        try:
            return theme.glyph_file(icon_name)
        except IconNotFoundError:
            return self._theme(DEFAULT_THEME).glyph_file(icon_name)
