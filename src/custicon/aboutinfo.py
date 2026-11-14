import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from .config import APP_ID

METAINFO_NAME = f"{APP_ID}.metainfo.xml"
DEVELOPMENT_METAINFO = Path(__file__).resolve().parents[2] / "data" / f"{METAINFO_NAME}.in"
HELP_BASE = "https://github.com/Gor3pig/Custicon/blob/main/help"
SOURCE_LANGUAGE = "en"
TRANSLATED_GUIDES = ("fr",)
FRENCH = "fr"
LANGUAGE_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}lang"
NOTE_TAGS = ("p", "ul", "ol")


def is_french(languages: list[str]) -> bool:
    return bool(languages) and languages[0].startswith(FRENCH)


def language_code(language: str) -> str:
    for separator in "_.@":
        language = language.partition(separator)[0]
    return language


def help_url(languages: list[str]) -> str:
    guide = SOURCE_LANGUAGE
    for code in map(language_code, languages):
        if code in TRANSLATED_GUIDES:
            guide = code
            break
        if code == SOURCE_LANGUAGE or code == "C":
            break
    return f"{HELP_BASE}/{guide}/README.md"


def chosen_text(element: ET.Element, translation: ET.Element | None, french: bool) -> str:
    source = translation if french and translation is not None else element
    return escape("".join(source.itertext()).strip())


def translations_of(parent: ET.Element) -> list[tuple[ET.Element, ET.Element | None]]:
    pairs = []
    children = list(parent)
    for index, child in enumerate(children):
        if child.get(LANGUAGE_ATTRIBUTE) is not None:
            continue
        following = children[index + 1] if index + 1 < len(children) else None
        translation = following if following is not None and following.get(LANGUAGE_ATTRIBUTE) == FRENCH else None
        pairs.append((child, translation))
    return pairs


def release_notes(metainfo: str, version: str, french: bool) -> str | None:
    try:
        root = ET.fromstring(metainfo)
    except ET.ParseError:
        return None
    for release in root.iter("release"):
        if release.get("version") == version:
            description = release.find("description")
            break
    else:
        return None
    if description is None:
        return None
    notes = []
    for element, translation in translations_of(description):
        if element.tag not in NOTE_TAGS:
            continue
        if element.tag == "p":
            notes.append(f"<p>{chosen_text(element, translation, french)}</p>")
            continue
        items = "".join(
            f"<li>{chosen_text(item, item_translation, french)}</li>" for item, item_translation in translations_of(element)
        )
        notes.append(f"<{element.tag}>{items}</{element.tag}>")
    return "".join(notes) or None


def find_metainfo(data_dirs: list[str]) -> Path:
    for directory in data_dirs:
        candidate = Path(directory) / "metainfo" / METAINFO_NAME
        if candidate.is_file():
            return candidate
    return DEVELOPMENT_METAINFO


def current_release_notes(version: str, languages: list[str], data_dirs: list[str]) -> str | None:
    try:
        text = find_metainfo(data_dirs).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return release_notes(text, version, is_french(languages))
