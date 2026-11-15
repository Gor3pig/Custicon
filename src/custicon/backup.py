import json

from .errors import StoreError

FORMAT = "custicon-backup"
VERSION = 1
MAX_BYTES = 8 * 1024 * 1024
MAX_FOLDERS = 5000
EXCLUDED_SETTINGS = ("glyph_copy",)


def build_document(app_version: str, entries, styles, defaults, picker) -> dict:
    folders = []
    for entry in entries:
        settings = {name: value for name, value in entry.settings.items() if name not in EXCLUDED_SETTINGS}
        folders.append({"folder": entry.folder, "settings": settings})
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_by": app_version,
        "folders": folders,
        "styles": [{"name": style.name, "appearance": style.appearance} for style in styles],
        "defaults": defaults,
        "picker": {"recent": list(picker.recent), "favorites": list(picker.favorites)},
    }


def dump(document: dict) -> bytes:
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")


def parse(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise StoreError("this backup file is too large")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise StoreError("this is not a Custicon backup file") from error
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise StoreError("this is not a Custicon backup file")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise StoreError("this is not a Custicon backup file")
    if version > VERSION:
        raise StoreError("this backup was made by a newer version of Custicon")
    folders = document.get("folders", [])
    if not isinstance(folders, list) or len(folders) > MAX_FOLDERS:
        raise StoreError("this is not a Custicon backup file")
    return document


def folder_items(document: dict) -> list[tuple[str, dict]]:
    items = []
    for item in document.get("folders", []):
        if not isinstance(item, dict):
            continue
        folder, settings = item.get("folder"), item.get("settings")
        if isinstance(folder, str) and folder and "\0" not in folder and isinstance(settings, dict):
            items.append((folder, settings))
    return items
