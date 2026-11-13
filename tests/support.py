import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import cairo
from gi.repository import Gio, GLib

SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def folder_svg(color: str = "#3366cc") -> str:
    return (
        f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128">'
        f'<rect x="8" y="16" width="112" height="100" fill="{color}"/></svg>'
    )


def glyph_svg(color: str = "#2e3436") -> str:
    return (
        f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
        f'<path d="M0 0h16v16H0z" fill="{color}"/></svg>'
    )


def solid_png(color: tuple[float, float, float], size: int = 48) -> bytes:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    context = cairo.Context(surface)
    context.set_source_rgb(*color)
    context.paint()
    path = Path(tempfile.mkstemp(suffix=".png")[1])
    try:
        surface.write_to_png(str(path))
        return path.read_bytes()
    finally:
        path.unlink()


def make_theme(
    root: Path,
    name: str,
    folder: str | None = None,
    folder_png: bytes | None = None,
    inherits: str | None = None,
    glyphs: dict[str, str] | None = None,
) -> Path:
    theme = root / name
    sections = []
    if folder is not None:
        (theme / "scalable" / "places").mkdir(parents=True, exist_ok=True)
        (theme / "scalable" / "places" / "folder.svg").write_text(folder)
        sections.append("[scalable/places]\nContext=Places\nSize=48\nMinSize=8\nMaxSize=512\nType=Scalable")
    if folder_png is not None:
        (theme / "48x48" / "places").mkdir(parents=True, exist_ok=True)
        (theme / "48x48" / "places" / "folder.png").write_bytes(folder_png)
        sections.append("[48x48/places]\nContext=Places\nSize=48\nType=Fixed")
    if glyphs:
        (theme / "symbolic" / "status").mkdir(parents=True, exist_ok=True)
        for glyph_name, content in glyphs.items():
            (theme / "symbolic" / "status" / f"{glyph_name}.svg").write_text(content)
        sections.append("[symbolic/status]\nContext=Status\nSize=16\nMinSize=8\nMaxSize=512\nType=Scalable")
    if not sections:
        (theme / "scalable" / "apps").mkdir(parents=True, exist_ok=True)
        sections.append("[scalable/apps]\nContext=Applications\nSize=48\nMinSize=8\nMaxSize=512\nType=Scalable")
    names = [section.split("]")[0][1:] for section in sections]
    header = f"[Icon Theme]\nName={name}\nComment=test theme\nDirectories={','.join(names)}\n"
    if inherits:
        header += f"Inherits={inherits}\n"
    (theme / "index.theme").write_text(header + "\n" + "\n\n".join(sections) + "\n")
    return theme


def gvfs_metadata_works(folder: Path) -> bool:
    key = "metadata::custom-icon"
    flags = Gio.FileQueryInfoFlags.NONE
    target = Gio.File.new_for_path(str(folder))
    try:
        target.set_attribute_string(key, "probe", flags, None)
        stored = target.query_info(key, flags, None).get_attribute_string(key)
        target.set_attribute(key, Gio.FileAttributeType.INVALID, None, flags, None)
    except GLib.Error:
        return False
    return stored == "probe"


def tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class WorkspaceTestCase(unittest.TestCase):
    def make_workspace(self) -> Path:
        base = Path(GLib.get_user_cache_dir()) / "custicon-tests"
        base.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(dir=base))
        self.addCleanup(shutil.rmtree, workspace, True)
        return workspace

    def require_metadata(self, probe: Path) -> None:
        if not gvfs_metadata_works(probe):
            self.skipTest("gvfs metadata unavailable in this environment")
