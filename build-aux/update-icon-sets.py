import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

REPOSITORY = "phosphor-icons/core"
COMMIT = "2b75f3ad12b420c9504ef05df8d2564a28f8500e"
CONTENT_DIGEST = "541e06d1d0a4713596dd99fa297e6ba2d7638e825ffe1e0958080d2a90693ec7"
REVISION = 2
TARGET = Path(__file__).resolve().parent.parent / "src" / "custicon" / "phosphor"

ENTRY = re.compile(r'name: "([^"]+)",.*?categories: \[(.*?)\],.*?tags: \[(.*?)\],', re.S)
CATEGORY = re.compile(r"IconCategory\.([A-Z_]+)")
TAG = re.compile(r'"([^"]*)"')
SVG_PATH = re.compile(r"^[^/]+/assets/regular/([^/]+)\.svg$")
CATALOG_PATH = re.compile(r"^[^/]+/src/icons\.ts$")
LICENSE_PATH = re.compile(r"^[^/]+/LICENSE$")


def parse_icons_ts(text: str) -> dict:
    entries = {}
    for name, categories, tags in ENTRY.findall(text):
        entries[name] = {
            "categories": [value.lower() for value in CATEGORY.findall(categories)],
            "tags": [tag for tag in TAG.findall(tags) if not tag.startswith("*")],
        }
    return entries


def content_digest(files: dict) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + hashlib.sha256(files[name]).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def read_archive(data: bytes) -> tuple[dict, str, str]:
    svgs, catalog, license_text = {}, None, None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            svg = SVG_PATH.match(member.name)
            if svg:
                svgs[svg.group(1)] = tar.extractfile(member).read()
            elif CATALOG_PATH.match(member.name):
                catalog = tar.extractfile(member).read().decode("utf-8")
            elif LICENSE_PATH.match(member.name):
                license_text = tar.extractfile(member).read().decode("utf-8")
    if not svgs or catalog is None or license_text is None:
        sys.exit("The archive does not contain the regular icons, the catalog and the license")
    return svgs, catalog, license_text


def build_index(svgs: dict, entries: dict) -> dict:
    icons = {}
    for name in sorted(svgs):
        entry = entries.get(name, {"categories": [], "tags": []})
        icons[name] = {"c": entry["categories"], "t": entry["tags"]}
    return {"revision": REVISION, "source": f"{REPOSITORY}@{COMMIT}", "icons": icons}


def write_set(target: Path, svgs: dict, license_text: str, index: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.svg"):
        old.unlink()
    for name, data in svgs.items():
        (target / f"{name}.svg").write_bytes(data)
    (target / "LICENSE").write_text(license_text, encoding="utf-8")
    (target / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def download(commit: str) -> bytes:
    with urllib.request.urlopen(f"https://codeload.github.com/{REPOSITORY}/tar.gz/{commit}", timeout=120) as response:
        return response.read()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Prepare the bundled Phosphor icons")
    parser.add_argument("--archive", type=Path, help="use this tar.gz of the pinned commit instead of downloading it")
    parser.add_argument("--print-digest", action="store_true", help="print the digest of the content and stop")
    arguments = parser.parse_args(argv)
    data = arguments.archive.read_bytes() if arguments.archive else download(COMMIT)
    svgs, catalog, license_text = read_archive(data)
    digest = content_digest({**{f"{name}.svg": content for name, content in svgs.items()}, "icons.ts": catalog.encode()})
    if arguments.print_digest:
        print(digest)
        return 0
    if digest != CONTENT_DIGEST:
        sys.exit(f"The content digest is {digest}, not the pinned {CONTENT_DIGEST}")
    write_set(TARGET, svgs, license_text, build_index(svgs, parse_icons_ts(catalog)))
    print(f"{len(svgs)} icons written to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
