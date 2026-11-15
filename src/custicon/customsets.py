import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .compose import GlyphStyle, load_glyph
from .errors import CusticonError, IconSetError
from .render import PNG_MAGIC, read_icon_bytes
from .textutil import normalize_text

KEY_PREFIX = "mine-"
MAX_SETS = 8
MAX_FILES = 2000
MAX_SET_NAME = 40
MAX_ICON_NAME = 50
SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
INDEX_NAME = "index.json"
META_NAME = "meta.json"
SVG_SUFFIX = ".svg"
TEMPORARY_PREFIX = ".tmp-"
CHECK_STYLE = GlyphStyle(color="#ffffff")


@dataclass(frozen=True)
class SetInfo:
    slug: str
    name: str
    count: int

    @property
    def key(self) -> str:
        return KEY_PREFIX + self.slug


@dataclass
class ImportReport:
    slug: str
    name: str
    added: int = 0
    ignored: int = 0
    replaced: bool = False


def slugify(text: str, limit: int = MAX_SET_NAME) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:limit].strip("-")
    return slug


def clean_name(name) -> str:
    if not isinstance(name, str):
        raise IconSetError("the name of the set must be a text")
    cleaned = " ".join(name.split())
    if not 1 <= len(cleaned) <= MAX_SET_NAME or any(unicodedata.category(character).startswith("C") for character in cleaned):
        raise IconSetError(f"the name of the set must have 1 to {MAX_SET_NAME} characters")
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as error:
        raise IconSetError("the name of the set is not valid text") from error
    if not slugify(cleaned):
        raise IconSetError("the name of the set needs at least one letter or digit")
    return cleaned


def read_name(directory: Path, fallback: str) -> str:
    try:
        name = json.loads((directory / META_NAME).read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError, RecursionError):
        return fallback
    try:
        return clean_name(name)
    except IconSetError:
        return fallback


def read_revision(directory: Path) -> int:
    try:
        revision = json.loads((directory / INDEX_NAME).read_text(encoding="utf-8")).get("revision")
    except (OSError, ValueError, AttributeError, RecursionError):
        return 0
    return revision if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0 else 0


def list_sets(root: Path) -> list[SetInfo]:
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    found = []
    for directory in children:
        if directory.name.startswith(".") or SLUG_PATTERN.fullmatch(directory.name) is None or not directory.is_dir():
            continue
        try:
            count = sum(1 for file in directory.glob("*" + SVG_SUFFIX) if file.is_file())
        except OSError:
            continue
        found.append(SetInfo(directory.name, read_name(directory, directory.name), count))
    return sorted(found, key=lambda info: normalize_text(info.name))


def usable_icon(path: Path) -> bytes | None:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        data = read_icon_bytes(path)
        if data.startswith(PNG_MAGIC):
            return None
        load_glyph(data, CHECK_STYLE, set())
    except (CusticonError, OSError, RecursionError, ValueError):
        return None
    return data


@dataclass
class AddReport:
    added: int = 0
    replaced: int = 0
    ignored: int = 0


def set_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or SLUG_PATTERN.fullmatch(slug) is None or not (root / slug).is_dir():
        raise IconSetError("unknown set")
    return root / slug


def icon_names(root: Path, slug: str) -> list[str]:
    directory = set_directory(root, slug)
    try:
        return sorted(file.name[: -len(SVG_SUFFIX)] for file in directory.glob("*" + SVG_SUFFIX) if file.is_file())
    except OSError as error:
        raise IconSetError(f"cannot read the set: {error.strerror}") from error


def write_index(directory: Path, revision: int) -> None:
    temporary = directory / (TEMPORARY_PREFIX + INDEX_NAME)
    temporary.write_text(json.dumps({"revision": revision, "icons": {}}), encoding="utf-8")
    os.replace(temporary, directory / INDEX_NAME)


def add_icons(root: Path, slug: str, files: list) -> AddReport:
    directory = set_directory(root, slug)
    report = AddReport()
    existing = set(icon_names(root, slug))
    try:
        staging = Path(tempfile.mkdtemp(prefix=TEMPORARY_PREFIX, dir=root))
    except OSError as error:
        raise IconSetError(f"cannot prepare the icons: {error.strerror}") from error
    try:
        staged: dict[str, bytes] = {}
        for path in files:
            path = Path(path)
            icon = slugify(path.name[: -len(SVG_SUFFIX)], MAX_ICON_NAME) if path.name.lower().endswith(SVG_SUFFIX) else ""
            data = usable_icon(path) if icon else None
            if data is None or icon in staged:
                report.ignored += 1
                continue
            if icon not in existing and len(existing) + len([name for name in staged if name not in existing]) >= MAX_FILES:
                report.ignored += 1
                continue
            staged[icon] = data
        changed = False
        for icon, data in staged.items():
            target = directory / (icon + SVG_SUFFIX)
            if icon in existing:
                if target.read_bytes() == data:
                    report.ignored += 1
                    continue
                report.replaced += 1
                changed = True
            else:
                report.added += 1
            (staging / (icon + SVG_SUFFIX)).write_bytes(data)
            os.replace(staging / (icon + SVG_SUFFIX), target)
        if changed:
            write_index(directory, read_revision(directory) + 1)
    except OSError as error:
        raise IconSetError(f"cannot store the icons: {error.strerror}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def remove_icon(root: Path, slug: str, icon: str) -> None:
    names = icon_names(root, slug)
    if not isinstance(icon, str) or icon not in names:
        raise IconSetError("unknown icon")
    if len(names) == 1:
        raise IconSetError("a set keeps at least one icon: remove the set instead")
    try:
        (root / slug / (icon + SVG_SUFFIX)).unlink()
    except OSError as error:
        raise IconSetError(f"cannot remove the icon: {error.strerror}") from error


def import_set(root: Path, source: Path, name: str) -> ImportReport:
    name = clean_name(name)
    slug = slugify(name)
    source = Path(source)
    if not source.is_dir():
        raise IconSetError("choose a folder that contains SVG files")
    final = root / slug
    others = [info for info in list_sets(root) if info.slug != slug]
    if len(others) >= MAX_SETS:
        raise IconSetError(f"at most {MAX_SETS} sets can be kept: remove one first")
    try:
        candidates = sorted(path for path in source.iterdir() if path.name.lower().endswith(SVG_SUFFIX))
    except OSError as error:
        raise IconSetError(f"cannot read the folder: {error.strerror}") from error
    report = ImportReport(slug, name, replaced=final.is_dir())
    try:
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=TEMPORARY_PREFIX, dir=root))
    except OSError as error:
        raise IconSetError(f"cannot prepare the set: {error.strerror}") from error
    try:
        seen = set()
        for path in candidates[:MAX_FILES]:
            icon = slugify(path.name[: -len(SVG_SUFFIX)], MAX_ICON_NAME)
            data = usable_icon(path) if icon and icon not in seen else None
            if data is None:
                report.ignored += 1
                continue
            seen.add(icon)
            (staging / (icon + SVG_SUFFIX)).write_bytes(data)
            report.added += 1
        report.ignored += max(0, len(candidates) - MAX_FILES)
        if report.added == 0:
            raise IconSetError("no usable SVG file was found in this folder")
        revision = read_revision(final) + 1
        (staging / INDEX_NAME).write_text(json.dumps({"revision": revision, "icons": {}}), encoding="utf-8")
        (staging / META_NAME).write_text(json.dumps({"name": name}, ensure_ascii=False), encoding="utf-8")
        swap(final, staging)
    except OSError as error:
        raise IconSetError(f"cannot store the set: {error.strerror}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def swap(final: Path, staging: Path) -> None:
    if not final.is_dir():
        os.replace(staging, final)
        return
    previous = Path(tempfile.mkdtemp(prefix=TEMPORARY_PREFIX, dir=final.parent))
    try:
        os.replace(final, previous / final.name)
        try:
            os.replace(staging, final)
        except OSError:
            os.replace(previous / final.name, final)
            raise
    finally:
        shutil.rmtree(previous, ignore_errors=True)


def remove_set(root: Path, slug: str) -> None:
    if not isinstance(slug, str) or SLUG_PATTERN.fullmatch(slug) is None:
        raise IconSetError("unknown set")
    target = root / slug
    if not target.is_dir():
        raise IconSetError("unknown set")
    try:
        shutil.rmtree(target)
    except OSError as error:
        raise IconSetError(f"cannot remove the set: {error.strerror}") from error
