import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import StoreError

REGISTRY_NAME = "registry.json"
BACKUP_SUFFIX = ".bak"
ICONS_DIR = "icons"
REGISTRY_VERSION = 1
DIGEST_LENGTH = 32


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
        self.registry_path = self.root / REGISTRY_NAME

    def icon_path(self, digest: str) -> Path:
        return self.icons_dir / f"{digest}.svg"

    def icon_uri(self, digest: str) -> str:
        return self.icon_path(digest).as_uri()

    def owns_uri(self, uri: str | None) -> bool:
        return bool(uri) and uri.startswith(self.icons_dir.as_uri() + "/")

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

    def prune(self) -> list[str]:
        if not self.icons_dir.is_dir():
            return []
        referenced = {entry.digest for entry in self._load().values()}
        removed = []
        for file in sorted(self.icons_dir.glob("*.svg")):
            if file.stem not in referenced:
                file.unlink()
                removed.append(file.stem)
        return removed

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
            except (OSError, ValueError, KeyError, TypeError) as error:
                problems.append(f"{candidate.name}: {error}")
        raise StoreError("unreadable registry: " + "; ".join(problems))

    def _is_readable(self, candidate: Path) -> bool:
        try:
            self._parse(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    @staticmethod
    def _parse(text: str) -> dict[str, Entry]:
        document = json.loads(text)
        if document["version"] != REGISTRY_VERSION:
            raise ValueError(f"unsupported version {document['version']}")
        entries = {}
        for item in document["entries"]:
            entry = Entry(
                folder=str(item["folder"]),
                digest=str(item["digest"]),
                theme=str(item["theme"]),
                color=str(item["color"]),
                settings=dict(item["settings"]),
                previous=None if item["previous"] is None else str(item["previous"]),
            )
            entries[entry.folder] = entry
        return entries

    def _save(self, entries: dict[str, Entry]) -> None:
        document = {
            "version": REGISTRY_VERSION,
            "entries": [asdict(entries[folder]) for folder in sorted(entries)],
        }
        if self._is_readable(self.registry_path):
            shutil.copyfile(self.registry_path, self.registry_path.with_name(REGISTRY_NAME + BACKUP_SUFFIX))
        self._write_atomic(self.registry_path, json.dumps(document, indent=2, ensure_ascii=False).encode())

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
