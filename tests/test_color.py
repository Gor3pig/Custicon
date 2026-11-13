import colorsys
import unittest

from custicon.color import (
    auto_glyph_color,
    darker_shade,
    face_color,
    format_hex,
    is_hex_color,
    parse_hex,
)
from custicon.errors import InvalidIconError

from .support import SVG_OPEN, WorkspaceTestCase, folder_svg, solid_png


class HexTests(unittest.TestCase):
    def test_round_trip(self):
        for value in ("#000000", "#ffffff", "#438de6", "#0a0b0c"):
            self.assertEqual(format_hex(parse_hex(value)), value)

    def test_uppercase_is_accepted(self):
        self.assertAlmostEqual(parse_hex("#FF0000")[0], 1.0)

    def test_invalid_values_are_rejected(self):
        for value in ("red", "#fff", "#12345g", "123456", "#1234567", ""):
            self.assertFalse(is_hex_color(value))
            with self.assertRaises(ValueError):
                parse_hex(value)

    def test_format_clamps_out_of_range_channels(self):
        self.assertEqual(format_hex((1.5, -0.2, 0.5)), "#ff0080")


class ShadeTests(unittest.TestCase):
    def test_shade_is_darker_and_keeps_the_hue(self):
        face = parse_hex("#a4caee")
        shade = darker_shade(face)
        hue_face, lightness_face, _ = colorsys.rgb_to_hls(*face)
        hue_shade, lightness_shade, _ = colorsys.rgb_to_hls(*shade)
        self.assertLess(lightness_shade, lightness_face)
        self.assertAlmostEqual(hue_shade, hue_face, places=2)

    def test_adwaita_face_gives_the_adwaita_glyph_color(self):
        expected = parse_hex("#438de6")
        for found, wanted in zip(darker_shade(parse_hex("#a4caee")), expected):
            self.assertAlmostEqual(found, wanted, delta=0.05)

    def test_saturation_never_exceeds_one(self):
        shade = darker_shade(parse_hex("#0000ff"))
        self.assertLessEqual(colorsys.rgb_to_hls(*shade)[2], 1.0)

    def test_black_stays_black(self):
        self.assertEqual(format_hex(darker_shade((0.0, 0.0, 0.0))), "#000000")


class FaceColorTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def write(self, name: str, content: str) -> object:
        path = self.root / name
        path.write_text(content)
        return path

    def test_face_color_is_read_at_the_center_of_the_face(self):
        path = self.write("folder.svg", folder_svg("#336699"))
        for found, wanted in zip(face_color(path), parse_hex("#336699")):
            self.assertAlmostEqual(found, wanted, delta=0.02)

    def test_face_color_of_a_png_folder(self):
        path = self.root / "folder.png"
        path.write_bytes(solid_png((0.2, 0.6, 0.3)))
        for found, wanted in zip(face_color(path), (0.2, 0.6, 0.3)):
            self.assertAlmostEqual(found, wanted, delta=0.02)

    def test_transparent_center_falls_back_to_the_visible_pixels(self):
        svg = f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128"><rect width="20" height="20" fill="#00ff00"/></svg>'
        path = self.write("corner.svg", svg)
        found = face_color(path)
        self.assertAlmostEqual(found[1], 1.0, delta=0.02)

    def test_fully_transparent_icon_is_rejected(self):
        svg = f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128"></svg>'
        with self.assertRaises(InvalidIconError):
            face_color(self.write("empty.svg", svg))

    def test_auto_color_matches_the_shade_of_the_face(self):
        path = self.write("folder.svg", folder_svg("#a4caee"))
        found = parse_hex(auto_glyph_color(path))
        for channel, wanted in zip(found, parse_hex("#438de6")):
            self.assertAlmostEqual(channel, wanted, delta=0.06)


if __name__ == "__main__":
    unittest.main()
