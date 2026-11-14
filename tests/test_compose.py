import base64
import re
import unittest
import xml.etree.ElementTree as ET

from custicon import layout
from custicon.color import parse_hex
from custicon.compose import SVG_NS, GlyphStyle, compose, tinted_folder_svg
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

    def glyph_text(self, svg: str) -> str:
        match = re.search(r"base64,([A-Za-z0-9+/=]+)", svg)
        return base64.b64decode(match.group(1)).decode()

    def glyph_root(self, svg: str) -> ET.Element:
        return ET.fromstring(self.glyph_text(svg).encode())

    def everything(self, svg: str) -> str:
        return svg + self.glyph_text(svg)

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

    def test_the_folder_tint_recolors_the_folder_and_keeps_its_shading(self):
        surface = surface_from_svg(self.compose(folder_tint="#e01b24").encode(), 128)
        red, green, blue, alpha = pixel(surface, 20, 100)
        self.assertGreater(red, blue + 0.2)
        self.assertGreater(red, green + 0.2)
        self.assertEqual(alpha, 1.0)

    def test_the_folder_tint_keeps_the_light_and_dark_areas_of_the_folder(self):
        folder = (
            f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128">'
            '<rect width="64" height="128" fill="#203060"/><rect x="64" width="64" height="128" fill="#a0c0f0"/></svg>'
        )
        surface = surface_from_svg(self.compose(folder=folder, folder_tint="#e01b24").encode(), 128)
        dark, light = pixel(surface, 10, 120), pixel(surface, 120, 120)
        self.assertGreater(sum(light[:3]), sum(dark[:3]) + 0.5)
        self.assertGreater(dark[0], dark[2])
        self.assertGreater(light[0], light[2])

    def test_a_tinted_folder_alone_is_available_for_sampling(self):
        path = self.write("folder.svg", folder_svg("#3366cc"))
        surface = surface_from_svg(tinted_folder_svg(path, "#e01b24"), 128)
        red, green, blue, alpha = pixel(surface, 20, 100)
        self.assertGreater(red, blue)
        with self.assertRaises(InvalidIconError):
            tinted_folder_svg(path, "red")

    def test_the_folder_tint_does_not_fill_the_gaps_inside_the_folder_box(self):
        folder = (
            f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128">'
            '<rect x="8" y="16" width="40" height="40" fill="#3366cc"/>'
            '<rect x="80" y="80" width="40" height="40" fill="#3366cc"/></svg>'
        )
        surface = surface_from_svg(self.compose(folder=folder, folder_tint="#e01b24").encode(), 128)
        self.assertEqual(pixel(surface, 100, 30)[3], 0.0)
        self.assertEqual(pixel(surface, 30, 100)[3], 0.0)
        self.assertEqual(pixel(surface, 100, 100)[3], 1.0)

    def test_the_folder_tint_leaves_the_glyph_color_alone(self):
        surface = surface_from_svg(self.compose(color="#00ff00", folder_tint="#e01b24").encode(), 128)
        red, green, blue, alpha = pixel(surface, 64, 76)
        self.assertAlmostEqual(green, 1.0, delta=0.02)
        self.assertAlmostEqual(red, 0.0, delta=0.02)

    def test_the_folder_tint_keeps_transparent_areas_transparent(self):
        surface = surface_from_svg(self.compose(folder_tint="#e01b24").encode(), 128)
        self.assertEqual(pixel(surface, 2, 2)[3], 0.0)

    def test_without_a_folder_tint_the_folder_is_not_filtered(self):
        svg = self.compose()
        self.assertNotIn("feBlend", svg)
        self.assertNotIn("custicon-folder-tint", svg)

    def test_the_folder_tint_does_not_collide_with_existing_ids(self):
        folder = (
            f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128"><defs><filter id="custicon-folder-tint"/></defs>'
            '<rect width="128" height="128" fill="#3366cc"/></svg>'
        )
        svg = self.compose(folder=folder, folder_tint="#e01b24")
        ids = re.findall(r'\bid="([^"]+)"', svg)
        self.assertEqual(sorted(ids), ["custicon-folder-tint", "custicon-folder-tint-2"])
        self.assertIn('filter="url(#custicon-folder-tint-2)"', svg)

    def test_the_folder_and_png_glyph_tints_use_distinct_filters(self):
        glyph = solid_png((0.0, 0.0, 0.0))
        svg = self.compose(glyph=glyph, folder_tint="#e01b24")
        self.assertEqual(sorted(re.findall(r'\bid="([^"]+)"', svg)), ["custicon-folder-tint", "custicon-tint"])

    def test_an_invalid_folder_tint_is_refused(self):
        for value in ("red", "#12345", "url(#x)", 5):
            with self.subTest(value=value), self.assertRaises(InvalidIconError):
                self.compose(folder_tint=value)

    def test_the_theme_folder_file_is_never_modified(self):
        path = self.write("folder.svg", folder_svg("#3366cc"))
        before = path.read_bytes()
        compose(path, self.write("glyph.svg", glyph_svg()), GlyphStyle(color=RED, folder_tint="#e01b24"))
        self.assertEqual(path.read_bytes(), before)

    def test_a_png_folder_can_be_tinted(self):
        svg = self.compose(folder=solid_png((0.2, 0.4, 0.8)), folder_tint="#e01b24")
        surface = surface_from_svg(svg.encode(), 128)
        red, green, blue, alpha = pixel(surface, 10, 10)
        self.assertGreater(red, blue)

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
        self.assertEqual(re.findall(r'\bid="([^"]+)"', svg), ["a"])
        self.assertIn("url(#a)", svg)
        inner = self.glyph_text(svg)
        self.assertEqual(re.findall(r'\bid="([^"]+)"', inner), ["a"])
        self.assertIn("url(#a)", inner)

    def test_a_wide_glyph_on_an_offset_non_square_folder_is_centered_and_scaled_by_its_longest_side(self):
        folder = f'{SVG_OPEN} viewBox="10 20 256 128"><rect x="10" y="20" width="256" height="128" fill="#3366cc"/></svg>'
        glyph = f'{SVG_OPEN} viewBox="4 6 32 8"><path d="M4 6h32v8H4z"/></svg>'
        svg = self.compose(folder=folder, glyph=glyph)
        transform = self.glyph_group(svg).get("transform")
        numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", transform)]
        center_x, center_y, scale, back_x, back_y = numbers
        self.assertAlmostEqual(center_x, 10 + layout.FACE_X * 256, places=6)
        self.assertAlmostEqual(center_y, 20 + layout.FACE_Y * 128, places=6)
        self.assertAlmostEqual(scale, layout.GLYPH_SIZE * 256 / 32, places=6)
        self.assertAlmostEqual(back_x, -(4 + 16), places=6)
        self.assertAlmostEqual(back_y, -(6 + 4), places=6)
        self.assertEqual(self.parse(svg).get("viewBox"), "10 20 256 128")

    def test_a_tall_glyph_is_scaled_by_its_height(self):
        folder = f'{SVG_OPEN} viewBox="0 0 128 128"><rect width="128" height="128" fill="#3366cc"/></svg>'
        glyph = f'{SVG_OPEN} viewBox="0 0 8 32"><path d="M0 0h8v32H0z"/></svg>'
        transform = self.glyph_group(self.compose(folder=folder, glyph=glyph)).get("transform")
        scale = float(re.search(r"scale\(([-\d.e]+)\)", transform).group(1))
        self.assertAlmostEqual(scale, layout.GLYPH_SIZE * 128 / 32, places=6)

    def test_stroke_only_glyph_keeps_its_hollow_shape(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M2 2h12v12H2z" fill="none" stroke="#000000" stroke-width="2"/></svg>'
        )
        svg = self.compose(glyph=glyph)
        path = self.glyph_root(svg).find(f"{{{SVG_NS}}}path")
        self.assertEqual(path.get("fill"), "none")
        self.assertEqual(path.get("stroke"), RED)

    def test_style_attribute_paint_is_recolored_and_the_rest_is_kept(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M0 0h16v16H0z" style="fill:#000000;opacity:0.5;stroke:none"/></svg>'
        )
        style = self.glyph_root(self.compose(glyph=glyph)).find(f"{{{SVG_NS}}}path").get("style")
        self.assertIn(f"fill:{RED}", style)
        self.assertIn("opacity:0.5", style)
        self.assertIn("stroke:none", style)

    def test_embedded_style_sheets_are_removed_when_tinting(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16"><style>.a{{fill:#123456}}</style>'
            '<path class="a" d="M0 0h16v16H0z"/></svg>'
        )
        self.assertNotIn("#123456", self.everything(self.compose(glyph=glyph)))

    def test_without_tint_the_glyph_keeps_its_own_colors(self):
        svg = self.compose(glyph=glyph_svg("#123456"), tint=False)
        group = self.glyph_root(svg)
        self.assertIsNone(group.get("fill"))
        self.assertEqual(group.find(f"{{{SVG_NS}}}path").get("fill"), "#123456")

    def test_external_references_are_removed(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16">'
            '<image xlink:href="file:///etc/passwd" width="16" height="16"/>'
            '<image href="https://example.org/x.png" width="16" height="16"/>'
            '<path id="p" d="M0 0h1v1z"/><use href="#p"/></svg>'
        )
        svg = self.everything(self.compose(glyph=glyph))
        self.assertNotIn("/etc/passwd", svg)
        self.assertNotIn("example.org", svg)
        self.assertIn('href="#p"', svg)

    def test_scripts_and_foreign_objects_are_removed(self):
        glyph = (
            f'{SVG_OPEN} width="16" height="16" viewBox="0 0 16 16"><script>alert(1)</script>'
            '<foreignObject width="1" height="1"/><path d="M0 0h16v16H0z"/></svg>'
        )
        svg = self.everything(self.compose(glyph=glyph))
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
