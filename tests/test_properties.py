import unittest
from pathlib import Path

from custicon import themes
from custicon.compose import GlyphStyle, compose
from custicon.render import read_icon_bytes, surface_from_svg

from .support import SVG_OPEN, WorkspaceTestCase

EMPTY_GLYPH = f'{SVG_OPEN} viewBox="0 0 1 1"/>'
SIZE = 128
TOLERANCE = 3


def installed_folder_icons() -> list[Path]:
    found = []
    for base in themes.host_icon_dirs():
        for theme in sorted(Path(base).glob("*")):
            found.extend(sorted(theme.glob("scalable/places/folder.svg")))
    return found


class CompositionPreservesTheFolderTests(WorkspaceTestCase):
    def test_the_folder_looks_the_same_with_an_empty_pictogram(self):
        icons = installed_folder_icons()
        if not icons:
            self.skipTest("no installed folder icon")
        root = self.make_workspace()
        glyph = root / "empty.svg"
        glyph.write_text(EMPTY_GLYPH)
        for icon in icons:
            with self.subTest(icon=str(icon)):
                raw = surface_from_svg(read_icon_bytes(icon), SIZE)
                composed = surface_from_svg(compose(icon, glyph, GlyphStyle(color="#112233")).encode(), SIZE)
                raw.flush()
                composed.flush()
                worst = max(abs(a - b) for a, b in zip(bytes(raw.get_data()), bytes(composed.get_data())))
                self.assertLessEqual(worst, TOLERANCE)


if __name__ == "__main__":
    unittest.main()
