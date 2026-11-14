import argparse
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "custicon"
CLDR = "unicode-org/cldr"
CLDR_COMMIT = "c0b65ae35609ff36b5fd593fdb0176ba092054f8"
CLDR_ANNOTATIONS = "common/annotations/fr.xml"
CLDR_LICENSE = "LICENSE"
CLDR_LICENSE_MARKER = "All other content in this repo is released under UNICODE LICENSE V3"
CLDR_COPYRIGHT = re.compile(r"Copyright © (\d{4}-\d{4}) Unicode, Inc\.")
VARIATION_SELECTOR = "️"
WORKERS = 12
EXCLUDED_BRANDS = frozenset(
    {
        "b5cf43ae07a7364e0c0ca9e838f01f278fc6a71c207a6f8c3de8d908608b2db1",
        "c70eca6b0f88f44d81a41311647e50fda1ac454ec04ffd442b0eb4743a993131",
        "c857d09db23e6822e3600bc06ad8d58f92ed62bc8efd81c753f77048662cb97d",
    }
)

ENTRY = re.compile(r'name: "([^"]+)",.*?categories: \[(.*?)\],.*?tags: \[(.*?)\],', re.S)
CATEGORY = re.compile(r"IconCategory\.([A-Z_]+)")
TAG = re.compile(r'"([^"]*)"')
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
GROUPS = {
    "Smileys & Emotion": "smileys",
    "People & Body": "people",
    "Animals & Nature": "nature",
    "Food & Drink": "food",
    "Travel & Places": "travel",
    "Activities": "activities",
    "Objects": "objects",
    "Symbols": "symbols",
    "Flags": "flags",
}


@dataclass(frozen=True)
class Built:
    svgs: dict
    licenses: dict
    index: dict


@dataclass(frozen=True)
class Source:
    name: str
    repository: str
    commit: str
    digest: str
    directory: str
    revision: int
    wanted: Callable
    extra: tuple = field(default=())


def pattern(expression: str) -> Callable:
    compiled = re.compile(expression)
    return lambda path: compiled.search(path) is not None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def plain_glyph(glyph: str) -> str:
    return glyph.replace(VARIATION_SELECTOR, "")


def is_excluded(slug: str, excluded: frozenset) -> bool:
    return hashlib.sha256(slug.encode()).hexdigest() in excluded


def unique(values: list) -> list:
    seen, result = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_icons_ts(text: str) -> dict:
    entries = {}
    for name, categories, tags in ENTRY.findall(text):
        entries[name] = {
            "categories": [value.lower() for value in CATEGORY.findall(categories)],
            "tags": [tag for tag in TAG.findall(tags) if not tag.startswith("*")],
        }
    return entries


def parse_annotations(data: bytes) -> dict:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        sys.exit(f"The French annotations cannot be read: {error}")
    found = {}
    for annotation in root.iter("annotation"):
        glyph = plain_glyph(annotation.get("cp", ""))
        text = (annotation.text or "").strip()
        if not glyph or not text:
            continue
        entry = found.setdefault(glyph, {"keywords": [], "name": ""})
        if annotation.get("type") == "tts":
            entry["name"] = text
        else:
            entry["keywords"] = [word.strip() for word in text.split("|") if word.strip()]
    return found


def content_digest(files: dict) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + hashlib.sha256(files[name]).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def need(files: dict, path: str) -> str:
    if path not in files:
        sys.exit(f"{path} is missing from the downloaded files")
    return files[path].decode("utf-8") if isinstance(files[path], bytes) else files[path]


def header(source: Source) -> str:
    parts = [f"{source.repository}@{source.commit}"] + [f"{repository}@{commit}" for repository, commit, _paths in source.extra]
    return "; ".join(parts)


def build_phosphor(files: dict) -> Built:
    source = SOURCES["phosphor"]
    svgs = {path[len("assets/regular/") : -len(".svg")]: data for path, data in files.items() if path.startswith("assets/regular/") and path.endswith(".svg")}
    if not svgs:
        sys.exit("The archive does not contain the regular icons")
    entries = parse_icons_ts(need(files, "src/icons.ts"))
    icons = {}
    for name in sorted(svgs):
        entry = entries.get(name, {"categories": [], "tags": []})
        icons[name] = {"c": entry["categories"], "t": entry["tags"]}
    return Built(svgs, {"LICENSE": need(files, "LICENSE")}, {"revision": source.revision, "source": header(source), "icons": icons})


def find_emoji_icon(files: dict, folder: str) -> str | None:
    for prefix in (f"assets/{folder}/High Contrast/", f"assets/{folder}/Default/High Contrast/"):
        candidates = sorted(path for path in files if path.startswith(prefix) and path.endswith(".svg"))
        if candidates:
            return candidates[0]
    return None


def unicode_license(text: str, copyright_line: str) -> str:
    position = text.find(CLDR_LICENSE_MARKER)
    if position < 0:
        sys.exit("The Unicode license cannot be found in the CLDR license file")
    return f"{copyright_line}\n\n{text[position:].strip()}\n"


def build_emoji(files: dict, annotations, cldr_license: str, copyright_line: str = "Copyright © Unicode, Inc.") -> Built:
    source = SOURCES["emoji"]
    if isinstance(annotations, bytes):
        annotations = parse_annotations(annotations)
    licenses = {"LICENSE": need(files, "LICENSE"), "LICENSE-CLDR": unicode_license(cldr_license, copyright_line)}
    svgs, icons, used = {}, {}, set()
    for folder in sorted({path.split("/")[1] for path in files if path.startswith("assets/") and path.count("/") >= 2}):
        icon, metadata = find_emoji_icon(files, folder), files.get(f"assets/{folder}/metadata.json")
        if icon is None or metadata is None:
            continue
        try:
            meta = json.loads(metadata)
        except ValueError:
            sys.exit(f"assets/{folder}/metadata.json cannot be read")
        glyph = meta.get("glyph", "")
        english = str(meta.get("cldr") or folder)
        name = slugify(english) or slugify(folder)
        if name in used:
            name = f"{name}-{'-'.join(f'{ord(character):x}' for character in plain_glyph(glyph)) or 'x'}"
        used.add(name)
        french = annotations.get(plain_glyph(glyph), {"keywords": [], "name": ""})
        tags = unique([word.lower() for word in meta.get("keywords", [])] + [english.lower()] + [word.lower() for word in french["keywords"]] + [french["name"].lower()])
        names = {"en": english}
        if french["name"]:
            names["fr"] = french["name"]
        group = meta.get("group", "")
        svgs[name] = files[icon]
        icons[name] = {"c": [GROUPS.get(group, slugify(group))] if group else [], "t": tags, "n": names}
    return Built(svgs, licenses, {"revision": source.revision, "source": header(source), "icons": dict(sorted(icons.items()))})


def build_brands(files: dict, excluded: frozenset = EXCLUDED_BRANDS) -> Built:
    source = SOURCES["brands"]
    try:
        data = json.loads(need(files, "data/simple-icons.json"))
    except ValueError as error:
        sys.exit(f"data/simple-icons.json cannot be read: {error}")
    aliases = {}
    for entry in data if isinstance(data, list) else []:
        found = entry.get("aliases", {}) if isinstance(entry, dict) else {}
        words = list(found.get("aka", [])) + list(found.get("loc", {}).values()) + [item.get("title", "") for item in found.get("dup", []) if isinstance(item, dict)]
        aliases[entry.get("title", "")] = [word.lower() for word in words if isinstance(word, str)]
    svgs, icons = {}, {}
    for path, content in files.items():
        if not (path.startswith("icons/") and path.endswith(".svg")):
            continue
        slug = path[len("icons/") : -len(".svg")]
        if is_excluded(slug, excluded):
            continue
        found = TITLE.search(content.decode("utf-8", "replace"))
        title = html.unescape(found.group(1)).strip() if found else slug
        svgs[slug] = content
        icons[slug] = {"c": [], "t": unique([title.lower(), slug] + aliases.get(title, [])), "n": {"en": title}}
    if not svgs:
        sys.exit("The archive does not contain any logo")
    licenses = {"LICENSE": need(files, "LICENSE.md"), "DISCLAIMER": need(files, "DISCLAIMER.md")}
    return Built(svgs, licenses, {"revision": source.revision, "source": header(source), "icons": dict(sorted(icons.items()))})


SOURCES = {
    "phosphor": Source(
        "phosphor",
        "phosphor-icons/core",
        "2b75f3ad12b420c9504ef05df8d2564a28f8500e",
        "26a6c9952736b5a18acc3d2ee46f6878f78affa6256e3c9c54be8d093255a4a2",
        "phosphor",
        2,
        pattern(r"^(assets/regular/[^/]+\.svg|src/icons\.ts|LICENSE)$"),
    ),
    "emoji": Source(
        "emoji",
        "microsoft/fluentui-emoji",
        "1ffb34c752ecf5d402f04cfb4b392c77f57c54bc",
        "63ff4a0e1720c07e9c874e5e8ed8eeb3c12719c90900938603249bcf58fe9599",
        "emoji",
        1,
        pattern(r"^(assets/[^/]+/(High Contrast/[^/]+\.svg|Default/High Contrast/[^/]+\.svg|metadata\.json)|LICENSE)$"),
        ((CLDR, CLDR_COMMIT, (CLDR_ANNOTATIONS, CLDR_LICENSE)),),
    ),
    "brands": Source(
        "brands",
        "simple-icons/simple-icons",
        "9e2437ce2d46aa8054d8a00ac63370a44cf5e71a",
        "7bca1b153166857a450bf4e08206f1e3e212cf1d9203cff719d233333751f2d5",
        "brands",
        1,
        pattern(r"^(icons/[^/]+\.svg|data/simple-icons\.json|LICENSE\.md|DISCLAIMER\.md)$"),
    ),
}


def write_set(target: Path, built: Built) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for old in list(target.glob("*.svg")) + list(target.glob("LICENSE*")) + list(target.glob("DISCLAIMER*")) + [target / "index.json"]:
        if old.exists():
            old.unlink()
    for name, data in built.svgs.items():
        (target / f"{name}.svg").write_bytes(data)
    for name, text in built.licenses.items():
        (target / name).write_text(text, encoding="utf-8")
    (target / "index.json").write_text(json.dumps(built.index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def http_get(url: str) -> bytes:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    return b""


def fetch_file(repository: str, commit: str, path: str, cache: Path | None, fetch: Callable = http_get) -> bytes:
    if cache is not None:
        stored = (cache / repository / commit / path).resolve()
        if not stored.is_relative_to((cache / repository / commit).resolve()):
            sys.exit(f"{path} leads outside the cache")
        if stored.is_file():
            return stored.read_bytes()
    data = fetch(f"https://raw.githubusercontent.com/{repository}/{commit}/{urllib.parse.quote(path)}")
    if cache is not None:
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
    return data


def list_files(repository: str, commit: str) -> list:
    tree = json.loads(http_get(f"https://api.github.com/repos/{repository}/git/trees/{commit}?recursive=1"))
    if tree.get("truncated"):
        sys.exit(f"The file list of {repository} is too long to be read at once")
    return [item["path"] for item in tree["tree"] if item["type"] == "blob"]


def download(source: Source, cache: Path | None) -> tuple[dict, dict]:
    paths = [path for path in list_files(source.repository, source.commit) if source.wanted(path)]
    jobs = [(source.repository, source.commit, path, False) for path in paths]
    for repository, commit, extra in source.extra:
        jobs += [(repository, commit, path, True) for path in extra]
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(lambda job: fetch_file(job[0], job[1], job[2], cache), jobs))
    own = {path: data for (_repository, _commit, path, is_extra), data in zip(jobs, results) if not is_extra}
    extras = {path: data for (_repository, _commit, path, is_extra), data in zip(jobs, results) if is_extra}
    return own, extras


def build(source: Source, own: dict, extras: dict) -> Built:
    if source.name == "phosphor":
        return build_phosphor(own)
    if source.name == "brands":
        return build_brands(own)
    annotations = extras[CLDR_ANNOTATIONS]
    found = CLDR_COPYRIGHT.search(annotations.decode("utf-8", "replace"))
    line = f"Copyright © {found.group(1)} Unicode, Inc." if found else "Copyright © Unicode, Inc."
    return build_emoji(own, annotations, extras[CLDR_LICENSE].decode("utf-8"), line)


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Prepare the icon sets bundled with Custicon")
    parser.add_argument("sets", nargs="*", choices=[*SOURCES, "all"], default=["all"])
    parser.add_argument("--cache", type=Path, help="keep the downloaded files in this folder and reuse them")
    parser.add_argument("--print-digest", action="store_true", help="print the digest of the downloaded content and stop")
    arguments = parser.parse_args(argv)
    names = list(SOURCES) if "all" in arguments.sets else arguments.sets
    for name in names:
        source = SOURCES[name]
        own, extras = download(source, arguments.cache)
        digest = content_digest({**own, **{f"extra:{path}": data for path, data in extras.items()}})
        if arguments.print_digest:
            print(f"{name} {digest}")
            continue
        if digest != source.digest:
            sys.exit(f"The content digest of {name} is {digest}, not the pinned {source.digest}")
        built = build(source, own, extras)
        write_set(PACKAGE / source.directory, built)
        print(f"{name}: {len(built.svgs)} icons written to {PACKAGE / source.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
