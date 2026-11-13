import re
import unittest
import xml.etree.ElementTree as ET

from custicon.color import parse_hex
from custicon.compose import SVG_NS, GlyphStyle, compose
from custicon.errors import InvalidIconError
from custicon.render import pixel, surface_from_svg

from .support import SVG_OPEN, WorkspaceTestCase, folder_svg, glyph_svg, solid_png

RED = "#ff0000"


class ComposeTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def write(self, name: str, content) -> object:
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def compose(self, folder=None, glyph=None, **style):
        folder_path = self.write("folder.svg", folder if folder is not None else folder_svg("#3366cc"))
        glyph_path = self.write("glyph.svg", glyph if glyph is not None else glyph_svg())
        return compose(folder_path, glyph_path, GlyphStyle(color=style.pop("color", RED), **style))

    def parse(self, svg: str) -> ET.Element:
        return ET.fromstring(svg.encode())

    def glyph_group(self, svg: str) -> ET.Element:
        return self.parse(svg).findall(f"{{{SVG_NS}}}g")[-1]

    def translation(self, svg: str) -> tuple[float, float]:
        match = re.match(r"translate\(([-\d.e]+) ([-\d.e]+)\)", self.glyph_group(svg).get("transform"))
        return float(match.group(1)), float(match.group(2))

    def test_result_is_a_square_vector_icon_with_the_folder_box(self):
        root = self.parse(self.compose())
        self.assertEqual(root.get("viewBox"), "0 0 128 128")
        self.assertEqual((root.get("width"), root.get("height")), ("128", "128"))

    def test_glyph_is_drawn_in_the_requested_color_at_the_center_of_the_face(self):
        surface = surface_from_svg(self.compose(color="#00ff00").encode(), 128)
        red, green, blue, alpha = pixel(surface, 64, 76)
        self.assertAlmostEqual(green, 1.0, delta=0.02)
        self.assertAlmostEqual(red, 0.0, delta=0.02)

    def test_the_folder_stays_visible_around_the_glyph(self):
        surface = surface_from_svg(self.compose().encode(), 128)
        for found, wanted in zip(pixel(surface, 20, 100)[:3], parse_hex("#3366cc")):
            self.assertAlmostEqual(found, wanted, delta=0.02)

    def test_composition_is_deterministic(self):
        self.assertEqual(self.compose(), self.compose())

    def test_ids_of_folder_and_glyph_do_not_collide(self):
        folder = (
            f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128"><defs><linearGradient id="a">'
            '<stop offset="0" stop-color="#fff"/></linearGradient></defs><rect width="128" height="128" fill="url(#a)"/></svg>'
        )
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16"><defs><linearGradient id="a">'
            '<stop offset="0" stop-color="#000"/></linearGradient></defs><rect width="16" height="16" fill="url(#a)"/></svg>'
        )
        svg = self.compose(folder=folder, glyph=glyph, tint=False)
        ids = re.findall(r'\bid="([^"]+)"', svg)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("f-a", ids)
        self.assertIn("g-a", ids)
        self.assertIn("url(#f-a)", svg)
        self.assertIn("url(#g-a)", svg)

    def test_stroke_only_glyph_keeps_its_hollow_shape(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M2 2h12v12H2z" fill="none" stroke="#000000" stroke-width="2"/></svg>'
        )
        svg = self.compose(glyph=glyph)
        path = self.glyph_group(svg).find(f"{{{SVG_NS}}}path")
        self.assertEqual(path.get("fill"), "none")
        self.assertEqual(path.get("stroke"), RED)

    def test_style_attribute_paint_is_recolored_and_the_rest_is_kept(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M0 0h16v16H0z" style="fill:#000000;opacity:0.5;stroke:none"/></svg>'
        )
        style = self.glyph_group(self.compose(glyph=glyph)).find(f"{{{SVG_NS}}}path").get("style")
        self.assertIn(f"fill:{RED}", style)
        self.assertIn("opacity:0.5", style)
        self.assertIn("stroke:none", style)

    def test_embedded_style_sheets_are_removed_when_tinting(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16"><style>.a{{fill:#123456}}</style>'
            '<path class="a" d="M0 0h16v16H0z"/></svg>'
        )
        self.assertNotIn("#123456", self.compose(glyph=glyph))

    def test_without_tint_the_glyph_keeps_its_own_colors(self):
        svg = self.compose(glyph=glyph_svg("#123456"), tint=False)
        group = self.glyph_group(svg)
        self.assertIsNone(group.get("fill"))
        self.assertEqual(group.find(f"{{{SVG_NS}}}path").get("fill"), "#123456")

    def test_external_references_are_removed(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
            '<image xlink:href="file:///etc/passwd" width="16" height="16"/>'
            '<image href="https://example.org/x.png" width="16" height="16"/>'
            '<path id="p" d="M0 0h1v1z"/><use href="#p"/></svg>'
        )
        svg = self.compose(glyph=glyph)
        self.assertNotIn("/etc/passwd", svg)
        self.assertNotIn("example.org", svg)
        self.assertIn('href="#g-p"', svg)

    def test_scripts_and_foreign_objects_are_removed(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16"><script>alert(1)</script>'
            '<foreignObject width="1" height="1"/><path d="M0 0h16v16H0z"/></svg>'
        )
        svg = self.compose(glyph=glyph)
        self.assertNotIn("script", svg)
        self.assertNotIn("foreignObject", svg)

    def test_size_scales_the_glyph(self):
        normal = self.glyph_group(self.compose()).get("transform")
        double = self.glyph_group(self.compose(size=2.0)).get("transform")
        scale = lambda text: float(re.search(r"scale\(([-\d.e]+)\)", text).group(1))
        self.assertAlmostEqual(scale(double), scale(normal) * 2, places=4)

    def test_offsets_move_the_glyph(self):
        base_x, base_y = self.translation(self.compose())
        moved_x, moved_y = self.translation(self.compose(offset_x=0.1, offset_y=-0.1))
        self.assertAlmostEqual(moved_x - base_x, 12.8, places=3)
        self.assertAlmostEqual(moved_y - base_y, -12.8, places=3)

    def test_glyph_without_a_view_box_uses_its_size(self):
        glyph = f'{SVG_OPEN} width="32" height="32"><path d="M0 0h32v32H0z"/></svg>'
        surface = surface_from_svg(self.compose(glyph=glyph, color="#00ff00").encode(), 128)
        self.assertAlmostEqual(pixel(surface, 64, 76)[1], 1.0, delta=0.02)

    def test_folder_with_a_different_grid_is_supported(self):
        folder = f'{SVG_OPEN} width="64" height="64"><rect x="4" y="8" width="56" height="50" fill="#3366cc"/></svg>'
        root = self.parse(self.compose(folder=folder))
        self.assertEqual(root.get("viewBox"), "0 0 64 64")
        surface = surface_from_svg(self.compose(folder=folder, color="#00ff00").encode(), 128)
        self.assertAlmostEqual(pixel(surface, 64, 76)[1], 1.0, delta=0.02)

    def test_png_folder_is_embedded_in_the_vector_wrapper(self):
        folder = self.write("folder.png", solid_png((0.2, 0.6, 0.3), 48))
        glyph = self.write("glyph.svg", glyph_svg())
        svg = compose(folder, glyph, GlyphStyle(color=RED))
        self.assertEqual(self.parse(svg).get("viewBox"), "0 0 48 48")
        self.assertIn("data:image/png;base64,", svg)

    def test_png_glyph_is_tinted_through_a_filter(self):
        glyph = self.write("glyph.png", solid_png((0.0, 0.0, 0.0), 16))
        folder = self.write("folder.svg", folder_svg())
        svg = compose(folder, glyph, GlyphStyle(color="#00ff00"))
        self.assertIn("feFlood", svg)
        surface = surface_from_svg(svg.encode(), 128)
        self.assertAlmostEqual(pixel(surface, 64, 76)[1], 1.0, delta=0.05)

    def test_png_glyph_without_tint_has_no_filter(self):
        glyph = self.write("glyph.png", solid_png((0.0, 0.0, 1.0), 16))
        folder = self.write("folder.svg", folder_svg())
        svg = compose(folder, glyph, GlyphStyle(color=RED, tint=False))
        self.assertNotIn("feFlood", svg)

    def test_invalid_color_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            self.compose(color="red")

    def test_garbage_folder_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            self.compose(folder="this is not xml")

    def test_non_svg_root_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            self.compose(glyph="<html><body/></html>")

    def test_glyph_without_any_size_is_rejected(self):
        with self.assertRaises(InvalidIconError):
            self.compose(glyph=f'{SVG_OPEN}><path d="M0 0h1v1z"/></svg>')

    def test_missing_glyph_file_is_rejected(self):
        folder = self.write("folder.svg", folder_svg())
        with self.assertRaises(InvalidIconError):
            compose(folder, self.root / "missing.svg", GlyphStyle(color=RED))


if __name__ == "__main__":
    unittest.main()
