import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import StoreError

REGISTRY_NAME = "registry.json"
DEFAULTS_NAME = "defaults.json"
BACKUP_SUFFIX = ".bak"
ICONS_DIR = "icons"
GLYPHS_DIR = "glyphs"
REGISTRY_VERSION = 1
DIGEST_LENGTH = 32
DIGEST_PATTERN = re.compile(r"[0-9a-f]{32}")
ICON_FILE_PATTERN = re.compile(r"[0-9a-f]{32}\.svg")
GLYPH_FILE_PATTERN = re.compile(r"[0-9a-f]{32}\.(?:svg|png)")
SCALAR_TYPES = (str, int, float, bool, type(None))
LOAD_ERRORS = (OSError, ValueError, KeyError, TypeError, RecursionError)


@dataclass(frozen=True)
class Entry:
    folder: str
    digest: str
    theme: str
    color: str
    settings: dict
    previous: str | None


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.icons_dir = self.root / ICONS_DIR
        self.glyphs_dir = self.root / GLYPHS_DIR
        self.registry_path = self.root / REGISTRY_NAME
        self.defaults_path = self.root / DEFAULTS_NAME

    def icon_path(self, digest: str) -> Path:
        return self.icons_dir / f"{digest}.svg"

    def icon_uri(self, digest: str) -> str:
        return self.icon_path(digest).as_uri()

    def owns_uri(self, uri: str | None) -> bool:
        prefix = self.icons_dir.as_uri() + "/"
        return bool(uri) and uri.startswith(prefix) and ICON_FILE_PATTERN.fullmatch(uri[len(prefix) :]) is not None

    def glyph_path(self, name: str | None) -> Path | None:
        if not isinstance(name, str) or GLYPH_FILE_PATTERN.fullmatch(name) is None:
            return None
        return self.glyphs_dir / name

    def save_glyph(self, data: bytes, suffix: str) -> str:
        name = hashlib.sha256(data).hexdigest()[:DIGEST_LENGTH] + suffix
        target = self.glyphs_dir / name
        if not target.exists():
            self._write_atomic(target, data)
        return name

    def save_icon(self, svg: str) -> str:
        digest = hashlib.sha256(svg.encode()).hexdigest()[:DIGEST_LENGTH]
        target = self.icon_path(digest)
        if not target.exists():
            self._write_atomic(target, svg.encode())
        return digest

    def all(self) -> dict[str, Entry]:
        return self._load()

    def get(self, folder: str) -> Entry | None:
        return self._load().get(folder)

    def put(self, entry: Entry) -> None:
        entries = self._load()
        entries[entry.folder] = entry
        self._save(entries)

    def remove(self, folder: str) -> Entry | None:
        entries = self._load()
        removed = entries.pop(folder, None)
        if removed is not None:
            self._save(entries)
        return removed

    def release(self, digest: str) -> None:
        self._release(self.icon_path(digest), lambda entry: entry.digest == digest)

    def release_glyph(self, name: str | None) -> None:
        path = self.glyph_path(name)
        if path is not None:
            self._release(path, lambda entry: entry.settings.get("glyph_copy") == name)

    def load_defaults(self) -> dict | None:
        try:
            values = json.loads(self.defaults_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return None
        if not Store._is_flat_mapping(values):
            return None
        return values

    def save_defaults(self, values: dict) -> None:
        self._write_atomic(self.defaults_path, json.dumps(values, indent=2).encode())

    def prune(self) -> list[str]:
        backup = self.registry_path.with_name(REGISTRY_NAME + BACKUP_SUFFIX)
        if not self.registry_path.exists() and not backup.exists():
            return []
        entries = self._load().values()
        digests = {entry.digest for entry in entries}
        copies = {entry.settings.get("glyph_copy") for entry in entries}
        removed = []
        for directory, pattern, referenced in (
            (self.icons_dir, "*.svg", digests),
            (self.glyphs_dir, "*", copies),
        ):
            if not directory.is_dir():
                continue
            for file in sorted(directory.glob(pattern)):
                name = file.stem if directory == self.icons_dir else file.name
                if name not in referenced and self._unlink(file):
                    removed.append(name)
        return removed

    def _release(self, path: Path, uses) -> None:
        try:
            if any(uses(entry) for entry in self._load().values()):
                return
        except StoreError:
            return
        self._unlink(path)

    @staticmethod
    def _unlink(path: Path) -> bool:
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def _load(self) -> dict[str, Entry]:
        backup = self.registry_path.with_name(REGISTRY_NAME + BACKUP_SUFFIX)
        if not self.registry_path.exists() and not backup.exists():
            return {}
        problems = []
        for candidate in (self.registry_path, backup):
            if not candidate.exists():
                continue
            try:
                return self._parse(candidate.read_text(encoding="utf-8"))
            except LOAD_ERRORS as error:
                problems.append(f"{candidate.name}: {error}")
        raise StoreError("unreadable registry: " + "; ".join(problems))

    def _is_readable(self, candidate: Path) -> bool:
        try:
            self._parse(candidate.read_text(encoding="utf-8"))
        except LOAD_ERRORS:
            return False
        return True

    @staticmethod
    def _parse(text: str) -> dict[str, Entry]:
        document = json.loads(text)
        if not isinstance(document, dict) or document["version"] != REGISTRY_VERSION:
            raise ValueError("unsupported registry")
        if not isinstance(document["entries"], list):
            raise TypeError("entries must be a list")
        entries = {}
        for item in document["entries"]:
            entry = Entry(
                folder=item["folder"],
                digest=item["digest"],
                theme=item["theme"],
                color=item["color"],
                settings=item["settings"],
                previous=item["previous"],
            )
            Store._check_entry(entry)
            entries[entry.folder] = entry
        return entries

    @staticmethod
    def _check_entry(entry: Entry) -> None:
        for text in (entry.folder, entry.digest, entry.theme, entry.color):
            if not isinstance(text, str):
                raise TypeError("registry fields must be strings")
        if entry.previous is not None and not isinstance(entry.previous, str):
            raise TypeError("previous must be a string or null")
        if not os.path.isabs(entry.folder) or "\0" in entry.folder:
            raise ValueError("the folder must be an absolute path")
        if DIGEST_PATTERN.fullmatch(entry.digest) is None:
            raise ValueError("invalid digest")
        if not Store._is_flat_mapping(entry.settings):
            raise TypeError("settings must be a flat mapping")

    @staticmethod
    def _is_flat_mapping(values) -> bool:
        return isinstance(values, dict) and all(
            isinstance(key, str) and isinstance(value, SCALAR_TYPES) for key, value in values.items()
        )

    def _save(self, entries: dict[str, Entry]) -> None:
        document = {
            "version": REGISTRY_VERSION,
            "entries": [asdict(entries[folder]) for folder in sorted(entries)],
        }
        try:
            data = json.dumps(document, indent=2, ensure_ascii=False).encode()
            if self._is_readable(self.registry_path):
                shutil.copyfile(self.registry_path, self.registry_path.with_name(REGISTRY_NAME + BACKUP_SUFFIX))
        except UnicodeError as error:
            raise StoreError("a folder name cannot be stored as UTF-8") from error
        except OSError as error:
            raise StoreError(f"cannot back up the registry: {error.strerror}") from error
        self._write_atomic(self.registry_path, data)

    @staticmethod
    def _write_atomic(target: Path, data: bytes) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            with open(temporary, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as error:
            raise StoreError(f"cannot write {target.name}: {error.strerror}") from error
