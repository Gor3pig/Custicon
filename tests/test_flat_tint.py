import colorsys
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from custicon import compose
from custicon.compose import GlyphStyle, compose as compose_icon, flat_tint_folder, tinted_folder_svg

from .support import SVG_OPEN, WorkspaceTestCase, glyph_svg


def hls_of(color):
    return colorsys.rgb_to_hls(*(int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)))


def folder(body, extra=""):
    return f'{SVG_OPEN} width="64" height="64" viewBox="0 0 64 64">{extra}{body}</svg>'


class FlatTintTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path

    def parsed(self, text):
        return ET.fromstring(text)

    def test_the_hue_and_saturation_of_the_tint_replace_those_of_every_color(self):
        root = self.parsed(folder('<rect fill="#3daee9"/><rect fill="#2980b9"/>'))
        self.assertTrue(flat_tint_folder(root, "#e01b24"))
        fills = [element.get("fill") for element in root]
        for fill, original in zip(fills, ("#3daee9", "#2980b9")):
            hue, lightness, saturation = hls_of(fill)
            self.assertAlmostEqual(hue, hls_of("#e01b24")[0], delta=0.01)
            self.assertAlmostEqual(lightness, hls_of(original)[1], delta=0.01)
        self.assertNotEqual(fills[0], fills[1])

    def test_white_and_black_keep_their_lightness(self):
        root = self.parsed(folder('<rect fill="#ffffff"/><rect fill="#000"/>'))
        flat_tint_folder(root, "#e01b24")
        self.assertEqual([element.get("fill") for element in root], ["#ffffff", "#000000"])

    def test_styles_and_style_elements_are_recolored(self):
        root = self.parsed(folder('<rect style="fill:#3daee9;opacity:.5"/><rect class="a"/>', '<style>.a{fill:#3daee9;color:#2980b9}</style>'))
        self.assertTrue(flat_tint_folder(root, "#e01b24"))
        text = ET.tostring(root, encoding="unicode")
        self.assertNotIn("#3daee9", text)
        self.assertNotIn("#2980b9", text)
        self.assertIn("opacity:.5", text)

    def test_short_hex_colors_are_expanded(self):
        root = self.parsed(folder('<rect fill="#08f"/>'))
        self.assertTrue(flat_tint_folder(root, "#e01b24"))
        self.assertRegex(root[0].get("fill"), r"^#[0-9a-f]{6}$")

    def test_a_folder_without_hex_colors_cannot_be_flattened(self):
        root = self.parsed(folder('<rect fill="currentColor"/><rect fill="red"/>'))
        self.assertFalse(flat_tint_folder(root, "#e01b24"))

    def test_a_raster_or_filtered_folder_falls_back_to_the_filter(self):
        for body in ('<image href="data:image/png;base64,AAAA" width="64" height="64"/><rect fill="#3daee9"/>', '<filter id="f"/><rect fill="#3daee9"/>'):
            with self.subTest(body=body):
                root = self.parsed(folder(body))
                self.assertFalse(flat_tint_folder(root, "#e01b24"))
                self.assertIn("#3daee9", ET.tostring(root, encoding="unicode"))

    def test_the_flat_icon_has_no_filter_and_the_default_one_still_has(self):
        path = self.write("folder.svg", folder('<rect fill="#3daee9"/>'))
        flat = compose_icon(path, glyph_svg().encode(), GlyphStyle(color="#ffffff", folder_tint="#e01b24", flat_tint=True))
        self.assertNotIn("feBlend", flat)
        self.assertNotIn("#3daee9", flat)
        classic = compose_icon(path, glyph_svg().encode(), GlyphStyle(color="#ffffff", folder_tint="#e01b24"))
        self.assertIn("feBlend", classic)
        self.assertIn("#3daee9", classic)

    def test_a_flat_request_on_an_unflattenable_folder_keeps_the_filter(self):
        path = self.write("folder.svg", folder('<rect fill="red"/>'))
        icon = compose_icon(path, glyph_svg().encode(), GlyphStyle(color="#ffffff", folder_tint="#e01b24", flat_tint=True))
        self.assertIn("feBlend", icon)

    def test_no_tint_means_no_change(self):
        path = self.write("folder.svg", folder('<rect fill="#3daee9"/>'))
        icon = compose_icon(path, glyph_svg().encode(), GlyphStyle(color="#ffffff", flat_tint=True))
        self.assertIn("#3daee9", icon)
        self.assertNotIn("feBlend", icon)

    def test_the_preview_svg_can_be_flat(self):
        path = self.write("folder.svg", folder('<rect fill="#3daee9"/>'))
        self.assertIn(b"feBlend", tinted_folder_svg(path, "#e01b24"))
        self.assertNotIn(b"feBlend", tinted_folder_svg(path, "#e01b24", True))

    def test_the_result_is_valid_for_a_renderer(self):
        path = self.write("folder.svg", folder('<rect fill="#3daee9"/>'))
        icon = compose_icon(path, glyph_svg().encode(), GlyphStyle(color="#ffffff", folder_tint="#e01b24", flat_tint=True))
        self.assertTrue(re.search(r"<svg", icon))
        ET.fromstring(icon)
