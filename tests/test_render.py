import unittest
from unittest import mock

from custicon import render
from custicon.errors import InvalidIconError
from custicon.render import pixel, surface_from_bytes, surface_from_file, surface_from_svg

from .support import SVG_OPEN, WorkspaceTestCase, folder_svg, solid_png


class RenderTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def test_svg_is_rendered_at_the_requested_size(self):
        surface = surface_from_svg(folder_svg("#ff0000").encode(), 64)
        self.assertEqual((surface.get_width(), surface.get_height()), (64, 64))
        red, green, blue, alpha = pixel(surface, 32, 38)
        self.assertAlmostEqual(red, 1.0, places=2)
        self.assertAlmostEqual(green, 0.0, places=2)
        self.assertEqual(alpha, 1.0)

    def test_transparent_pixel_has_no_alpha(self):
        surface = surface_from_svg(folder_svg().encode(), 64)
        self.assertEqual(pixel(surface, 0, 0), (0.0, 0.0, 0.0, 0.0))

    def test_semi_transparent_pixel_is_not_premultiplied(self):
        svg = f'{SVG_OPEN} width="8" height="8" viewBox="0 0 8 8"><rect width="8" height="8" fill="#ff0000" fill-opacity="0.5"/></svg>'
        red, green, blue, alpha = pixel(surface_from_svg(svg.encode(), 8), 4, 4)
        self.assertAlmostEqual(red, 1.0, places=1)
        self.assertAlmostEqual(alpha, 0.5, places=1)

    def test_png_is_scaled_to_the_requested_size(self):
        surface = surface_from_bytes(solid_png((0.0, 1.0, 0.0), 16), 64)
        red, green, blue, alpha = pixel(surface, 32, 32)
        self.assertAlmostEqual(green, 1.0, places=2)
        self.assertEqual((surface.get_width(), surface.get_height()), (64, 64))

    def test_garbage_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            surface_from_bytes(b"this is not an image", 32)

    def test_truncated_png_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            surface_from_bytes(solid_png((1.0, 0.0, 0.0))[:30], 32)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            surface_from_file(self.root / "missing.svg", 32)

    def test_oversized_file_is_rejected(self):
        big = self.root / "big.svg"
        big.write_text(folder_svg() + "\n<!-- " + "x" * 2048 + " -->\n")
        self.assertIsNotNone(surface_from_file(big, 32))
        with mock.patch.object(render, "MAX_FILE_BYTES", 1024):
            with self.assertRaises(InvalidIconError):
                surface_from_file(big, 32)


if __name__ == "__main__":
    unittest.main()
