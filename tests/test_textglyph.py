import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import cairo

from custicon import textglyph
from custicon.errors import InvalidIconError
from custicon.render import surface_from_svg
from custicon.textglyph import MAX_CHARACTERS, number, path_data, text_to_svg

SVG = "{http://www.w3.org/2000/svg}"


def opaque_pixels(svg: str, size: int = 64) -> int:
    surface = surface_from_svg(svg.encode(), size)
    surface.flush()
    data = bytes(surface.get_data())
    return sum(1 for index in range(3, len(data), 4) if data[index] > 200)


class TextToSvgTests(unittest.TestCase):
    def test_a_letter_becomes_a_single_filled_path(self):
        root = ET.fromstring(text_to_svg("Z"))
        paths = root.findall(f"{SVG}path")
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].get("d").startswith("M"))
        self.assertEqual(len(root.get("viewBox").split()), 4)

    def test_the_view_box_matches_the_ink_so_the_shape_is_centered_by_the_composition(self):
        x, y, width, height = (float(value) for value in ET.fromstring(text_to_svg("I")).get("viewBox").split())
        self.assertGreater(width, 0)
        self.assertGreater(height, width)

    def test_the_output_is_deterministic(self):
        self.assertEqual(text_to_svg("Ab1"), text_to_svg("Ab1"))

    def test_the_shape_is_visible_when_rendered(self):
        self.assertGreater(opaque_pixels(text_to_svg("O")), 200)

    def test_different_texts_give_different_shapes(self):
        self.assertNotEqual(text_to_svg("A"), text_to_svg("B"))

    def test_up_to_the_maximum_length_is_accepted(self):
        self.assertTrue(text_to_svg("é" * MAX_CHARACTERS))

    def test_surrounding_spaces_are_ignored(self):
        self.assertEqual(text_to_svg("  Z "), text_to_svg("Z"))

    def test_unusable_texts_are_refused_with_an_icon_error(self):
        for value in ("", "   ", "\n", "a" * (MAX_CHARACTERS + 1), None, 3, b"A"):
            with self.assertRaises(InvalidIconError, msg=repr(value)):
                text_to_svg(value)

    def test_characters_the_font_cannot_draw_are_refused(self):
        layout = mock.Mock()
        layout.get_unknown_glyphs_count.return_value = 1
        with mock.patch.object(textglyph.PangoCairo, "create_layout", return_value=layout):
            with self.assertRaisesRegex(InvalidIconError, "no shape"):
                text_to_svg("A")

    def test_a_text_without_any_ink_is_refused(self):
        with mock.patch.object(textglyph.PangoCairo, "layout_path"):
            with self.assertRaisesRegex(InvalidIconError, "visible"):
                text_to_svg("A")

    def test_numbers_are_rounded_to_two_decimals(self):
        self.assertEqual(number(1.23456), "1.23")
        self.assertEqual(number(10.0), "10")
        self.assertEqual(number(-0.004), "-0")

    def test_every_path_command_is_written(self):
        path = [
            (cairo.PATH_MOVE_TO, (1.0, 2.0)),
            (cairo.PATH_LINE_TO, (3.0, 4.0)),
            (cairo.PATH_CURVE_TO, (5.0, 6.0, 7.0, 8.0, 9.0, 10.0)),
            (cairo.PATH_CLOSE_PATH, ()),
        ]
        self.assertEqual(path_data(path), "M1 2L3 4C5 6 7 8 9 10Z")


if __name__ == "__main__":
    unittest.main()
