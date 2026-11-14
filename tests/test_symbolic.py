import os
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gsk, Gtk

from custicon import symbolic
from custicon.errors import CusticonError
from custicon.render import surface_from_bytes
from tests.support import WorkspaceTestCase

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "custicon"
PHOSPHOR = PACKAGE / "phosphor" / "camera.svg"
EMOJI = PACKAGE / "emoji" / "cat.svg"
BRAND = PACKAGE / "brands" / "github.svg"
DISPLAY_AVAILABLE = os.environ.get("GDK_BACKEND") == "broadway" and Gtk.init_check()


def first_opaque(data: bytes, size: int = 48):
    surface = surface_from_bytes(data, size)
    surface.flush()
    pixels = bytes(surface.get_data())
    for offset in range(0, len(pixels), 4):
        if pixels[offset + 3] == 255:
            return pixels[offset + 2], pixels[offset + 1], pixels[offset]
    return None


def rgba(value: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(value)
    return color


class ColoringTests(unittest.TestCase):
    def test_a_current_color_set_is_recolored(self):
        self.assertEqual(first_opaque(symbolic.colored_svg(PHOSPHOR.read_bytes(), "#ff8800")), (255, 136, 0))

    def test_a_set_with_explicit_fills_is_recolored(self):
        self.assertEqual(first_opaque(symbolic.colored_svg(EMOJI.read_bytes(), "#ff8800")), (255, 136, 0))

    def test_a_logo_without_any_fill_is_recolored(self):
        self.assertNotIn(b"fill=", BRAND.read_bytes().split(b"<path", 1)[0])
        self.assertEqual(first_opaque(symbolic.colored_svg(BRAND.read_bytes(), "#ff8800")), (255, 136, 0))

    def test_a_fill_of_none_is_kept(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8" fill="none"><rect width="8" height="8" fill="#000"/></svg>'
        self.assertIn(b'fill="none"', symbolic.colored_svg(svg, "#ffffff").replace(b"'", b'"'))

    def test_an_invalid_document_is_refused(self):
        with self.assertRaises(CusticonError):
            symbolic.colored_svg(b"not an svg", "#ffffff")

    def test_a_color_is_written_as_hexadecimal(self):
        self.assertEqual(symbolic.hex_of(rgba("#0a141e")), "#0a141e")
        self.assertEqual(symbolic.hex_of(rgba("white")), "#ffffff")


class CacheTests(unittest.TestCase):
    def setUp(self):
        symbolic.clear_cache()
        self.addCleanup(symbolic.clear_cache)

    def test_the_same_request_gives_the_same_texture(self):
        first = symbolic.render_texture(str(PHOSPHOR), "#ffffff", 48)
        self.assertIs(symbolic.render_texture(str(PHOSPHOR), "#ffffff", 48), first)
        self.assertEqual(symbolic.cache_size(), 1)

    def test_another_color_or_size_is_another_texture(self):
        symbolic.render_texture(str(PHOSPHOR), "#ffffff", 48)
        symbolic.render_texture(str(PHOSPHOR), "#000000", 48)
        symbolic.render_texture(str(PHOSPHOR), "#ffffff", 56)
        self.assertEqual(symbolic.cache_size(), 3)

    def test_the_cache_is_bounded_and_forgets_the_oldest(self):
        with mock.patch.object(symbolic, "CACHE_LIMIT", 2):
            first = symbolic.render_texture(str(PHOSPHOR), "#111111", 48)
            symbolic.render_texture(str(PHOSPHOR), "#222222", 48)
            symbolic.render_texture(str(PHOSPHOR), "#333333", 48)
            self.assertEqual(symbolic.cache_size(), 2)
            self.assertIsNot(symbolic.render_texture(str(PHOSPHOR), "#111111", 48), first)

    def test_a_recent_use_protects_an_entry(self):
        with mock.patch.object(symbolic, "CACHE_LIMIT", 2):
            first = symbolic.render_texture(str(PHOSPHOR), "#111111", 48)
            symbolic.render_texture(str(PHOSPHOR), "#222222", 48)
            symbolic.render_texture(str(PHOSPHOR), "#111111", 48)
            symbolic.render_texture(str(PHOSPHOR), "#333333", 48)
            self.assertIs(symbolic.render_texture(str(PHOSPHOR), "#111111", 48), first)

    def test_a_missing_file_is_not_cached(self):
        with self.assertRaises(CusticonError):
            symbolic.render_texture(str(PACKAGE / "phosphor" / "nothing-here.svg"), "#ffffff", 48)
        self.assertEqual(symbolic.cache_size(), 0)


class PaintableTests(unittest.TestCase):
    def setUp(self):
        symbolic.clear_cache()
        self.addCleanup(symbolic.clear_cache)
        self.paintable = symbolic.SymbolicSvg(PHOSPHOR, 24)

    def test_the_intrinsic_size_is_the_requested_one(self):
        self.assertEqual((self.paintable.get_intrinsic_width(), self.paintable.get_intrinsic_height()), (24, 24))
        self.assertEqual(self.paintable.get_intrinsic_aspect_ratio(), 1.0)

    def test_the_content_never_changes(self):
        flags = self.paintable.get_flags()
        self.assertTrue(flags & Gdk.PaintableFlags.STATIC_SIZE)
        self.assertTrue(flags & Gdk.PaintableFlags.STATIC_CONTENTS)

    def test_a_symbolic_snapshot_uses_the_given_color(self):
        snapshot = Gtk.Snapshot()
        self.paintable.snapshot_symbolic(snapshot, 24, 24, [rgba("#ffffff")])
        self.assertIsNotNone(snapshot.to_node())
        self.assertEqual(symbolic.cache_size(), 1)
        keys = list(symbolic._textures)
        self.assertEqual(keys[0][1], "#ffffff")

    def test_two_colors_make_two_textures(self):
        self.paintable.snapshot_symbolic(Gtk.Snapshot(), 24, 24, [rgba("#ffffff")])
        self.paintable.snapshot_symbolic(Gtk.Snapshot(), 24, 24, [rgba("#000000")])
        self.assertEqual(symbolic.cache_size(), 2)

    def test_the_rendering_is_done_at_twice_the_size(self):
        self.paintable.snapshot_symbolic(Gtk.Snapshot(), 24, 24, [rgba("#ffffff")])
        self.assertEqual(list(symbolic._textures)[0][2], 48)

    def test_a_translucent_color_is_drawn_with_its_opacity(self):
        snapshot = Gtk.Snapshot()
        color = rgba("rgba(255, 255, 255, 0.5)")
        self.paintable.snapshot_symbolic(snapshot, 24, 24, [color])
        node = snapshot.to_node()
        self.assertEqual(node.get_node_type(), Gsk.RenderNodeType.OPACITY_NODE)
        self.assertAlmostEqual(node.get_opacity(), 0.5, places=2)
        self.assertEqual(list(symbolic._textures)[0][1], "#ffffff")

    def test_an_opaque_color_adds_no_opacity_node(self):
        snapshot = Gtk.Snapshot()
        self.paintable.snapshot_symbolic(snapshot, 24, 24, [rgba("#ffffff")])
        self.assertNotEqual(snapshot.to_node().get_node_type(), Gsk.RenderNodeType.OPACITY_NODE)

    def test_the_sets_hand_out_this_kind_of_paintable(self):
        from custicon import iconsets

        for key in ("phosphor", "emoji", "brands"):
            icon_set = iconsets.default_sets()[key]
            self.assertIsInstance(icon_set.paintable(icon_set.all_names()[0], 24), symbolic.SymbolicSvg)

    def test_a_broken_file_draws_nothing_and_does_not_fail(self):
        broken = symbolic.SymbolicSvg(PACKAGE / "phosphor" / "nothing-here.svg", 24)
        snapshot = Gtk.Snapshot()
        broken.snapshot_symbolic(snapshot, 24, 24, [rgba("#ffffff")])
        self.assertEqual(symbolic.cache_size(), 0)

    def test_without_colors_the_default_foreground_is_used(self):
        with mock.patch.object(symbolic, "default_foreground", return_value="#123456"):
            self.paintable.do_snapshot_symbolic(Gtk.Snapshot(), 24, 24, [])
        self.assertEqual(list(symbolic._textures)[0][1], "#123456")


@unittest.skipUnless(DISPLAY_AVAILABLE, "needs a Broadway display")
class DisplayedTests(WorkspaceTestCase):
    def setUp(self):
        from gi.repository import Adw

        Adw.init()
        self.adw = Adw
        symbolic.clear_cache()
        self.addCleanup(symbolic.clear_cache)
        self.addCleanup(self.adw.StyleManager.get_default().set_color_scheme, self.adw.ColorScheme.DEFAULT)

    def color_when(self, scheme) -> str:
        from tests.test_ui_smoke import settle

        symbolic.clear_cache()
        self.adw.StyleManager.get_default().set_color_scheme(scheme)
        image = Gtk.Image(pixel_size=48)
        image.set_from_paintable(symbolic.SymbolicSvg(PHOSPHOR, 48))
        window = Gtk.Window(default_width=120, default_height=120, child=image)
        self.addCleanup(window.destroy)
        window.present()
        self.assertTrue(settle(lambda: symbolic.cache_size() > 0), "nothing was drawn")
        return list(symbolic._textures)[0][1]

    @staticmethod
    def brightness(color: str) -> float:
        return sum(int(color[index : index + 2], 16) for index in (1, 3, 5)) / 3

    def test_the_pictogram_is_dark_on_a_light_style_and_light_on_a_dark_style(self):
        light = self.color_when(self.adw.ColorScheme.FORCE_LIGHT)
        dark = self.color_when(self.adw.ColorScheme.FORCE_DARK)
        self.assertLess(self.brightness(light), 100, light)
        self.assertGreater(self.brightness(dark), 150, dark)

    def test_the_default_foreground_follows_the_style(self):
        manager = self.adw.StyleManager.get_default()
        manager.set_color_scheme(self.adw.ColorScheme.FORCE_DARK)
        self.assertEqual(symbolic.default_foreground(), symbolic.DARK_FOREGROUND)
        manager.set_color_scheme(self.adw.ColorScheme.FORCE_LIGHT)
        self.assertEqual(symbolic.default_foreground(), symbolic.LIGHT_FOREGROUND)

    def test_a_pictogram_already_shown_follows_a_change_of_style(self):
        from tests.test_ui_smoke import settle

        manager = self.adw.StyleManager.get_default()
        manager.set_color_scheme(self.adw.ColorScheme.FORCE_LIGHT)
        symbolic.clear_cache()
        image = Gtk.Image(pixel_size=48)
        image.set_from_paintable(symbolic.SymbolicSvg(PHOSPHOR, 48))
        window = Gtk.Window(default_width=120, default_height=120, child=image)
        self.addCleanup(window.destroy)
        window.present()
        self.assertTrue(settle(lambda: symbolic.cache_size() > 0))
        manager.set_color_scheme(self.adw.ColorScheme.FORCE_DARK)
        self.assertTrue(settle(lambda: any(self.brightness(key[1]) > 150 for key in list(symbolic._textures))))
