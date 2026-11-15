import io
import json
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from unittest import mock

import cairo

from custicon import compose as compose_module
from custicon.compose import (
    BADGE_CORNERS,
    BADGE_DEFAULT_COLOR,
    Badge,
    GlyphStyle,
    badge_glyph_color,
    compose,
)
from custicon.errors import InvalidIconError, InvalidSettingsError
from custicon.render import surface_from_svg
from custicon.service import BADGE_FIELDS, Service, Settings
from custicon.store import Store

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme

SVG = "{http://www.w3.org/2000/svg}"
STAR = Settings(glyph_name="star-symbolic")


def pixel(surface, x, y):
    data = surface.get_data()
    offset = y * surface.get_stride() + x * 4
    blue, green, red, alpha = data[offset : offset + 4]
    return red, green, blue, alpha


class BadgeComposeTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.folder = self.root / "folder.svg"
        self.folder.write_text(folder_svg("#3366cc"))

    def icon(self, **badge):
        return compose(self.folder, glyph_svg().encode(), GlyphStyle(color="#ffffff", badge=Badge(glyph_svg().encode(), **badge)))

    def badge_document(self, svg):
        import base64

        images = [element for element in ET.fromstring(svg).iter(SVG + "image")]
        href = images[-1].get("{http://www.w3.org/1999/xlink}href")
        return base64.b64decode(href.split(",", 1)[1]).decode()

    def circle(self, svg):
        return next(ET.fromstring(svg).iter(SVG + "circle"))

    def test_no_badge_means_no_circle(self):
        svg = compose(self.folder, glyph_svg().encode(), GlyphStyle(color="#ffffff"))
        self.assertIsNone(next(ET.fromstring(svg).iter(SVG + "circle"), None))

    def test_the_badge_is_a_colored_disc_in_the_chosen_corner(self):
        centers = {}
        for corner in BADGE_CORNERS:
            circle = self.circle(self.icon(corner=corner))
            centers[corner] = (float(circle.get("cx")), float(circle.get("cy")))
            self.assertEqual(circle.get("fill"), BADGE_DEFAULT_COLOR)
        self.assertLess(centers["tl"][0], 64)
        self.assertLess(centers["bl"][0], 64)
        self.assertGreater(centers["tr"][0], 64)
        self.assertGreater(centers["br"][0], 64)
        self.assertLess(centers["tl"][1], 64)
        self.assertLess(centers["tr"][1], 64)
        self.assertGreater(centers["bl"][1], 64)
        self.assertGreater(centers["br"][1], 64)

    def test_the_disc_stays_inside_the_icon(self):
        for corner in BADGE_CORNERS:
            for size in (0.15, 0.3, 0.5):
                circle = self.circle(self.icon(corner=corner, size=size))
                cx, cy, r = (float(circle.get(name)) for name in ("cx", "cy", "r"))
                self.assertGreaterEqual(cx - r, 0, (corner, size))
                self.assertGreaterEqual(cy - r, 0, (corner, size))
                self.assertLessEqual(cx + r, 128, (corner, size))
                self.assertLessEqual(cy + r, 128, (corner, size))

    def test_the_size_is_a_share_of_the_folder_width(self):
        small = float(self.circle(self.icon(size=0.2)).get("r"))
        large = float(self.circle(self.icon(size=0.4)).get("r"))
        self.assertAlmostEqual(large, small * 2)
        self.assertAlmostEqual(large * 2, 0.4 * 112, delta=20)

    def test_the_rendered_corner_has_the_disc_color(self):
        svg = self.icon(corner="br", color="#e62d42")
        surface = surface_from_svg(svg.encode(), 128)
        circle = self.circle(svg)
        cx, cy, r = (float(circle.get(name)) for name in ("cx", "cy", "r"))
        red, green, blue, alpha = pixel(surface, int(cx + r * 0.8), int(cy))
        self.assertGreater(red, 180)
        self.assertLess(green, 90)
        self.assertEqual(alpha, 255)
        self.assertEqual(pixel(surface, 4, 4)[3], 0)

    def test_the_glyph_is_light_on_a_dark_disc_and_dark_on_a_light_one(self):
        self.assertEqual(badge_glyph_color("#e62d42"), "#ffffff")
        self.assertEqual(badge_glyph_color("#000000"), "#ffffff")
        self.assertEqual(badge_glyph_color("#ffffff"), "#241f31")
        self.assertEqual(badge_glyph_color("#f6d32d"), "#241f31")
        self.assertIn("#241f31", self.badge_document(self.icon(color="#f6d32d")))
        self.assertIn("#ffffff", self.badge_document(self.icon(color="#3a944a")))
        self.assertNotIn("#241f31", self.badge_document(self.icon(color="#3a944a")))

    def test_the_badge_glyph_sits_in_the_middle_of_the_disc(self):
        svg = self.icon(corner="bl")
        circle = self.circle(svg)
        groups = [group for group in ET.fromstring(svg).iter(SVG + "g") if "translate" in (group.get("transform") or "")]
        transform = groups[-1].get("transform")
        self.assertTrue(transform.startswith(f"translate({circle.get('cx')} {circle.get('cy')})"))

    def test_invalid_badges_are_refused(self):
        for badge in (
            Badge(glyph_svg().encode(), color="red"),
            Badge(glyph_svg().encode(), corner="middle"),
            Badge(glyph_svg().encode(), size=0.05),
            Badge(glyph_svg().encode(), size=0.9),
            Badge(glyph_svg().encode(), size=float("nan")),
            Badge(glyph_svg().encode(), size=True),
        ):
            with self.subTest(badge=badge), self.assertRaises(InvalidIconError):
                compose(self.folder, glyph_svg().encode(), GlyphStyle(color="#ffffff", badge=badge))

    def test_a_png_badge_glyph_is_accepted(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 16, 16)
        output = io.BytesIO()
        surface.write_to_png(output)
        svg = compose(self.folder, glyph_svg().encode(), GlyphStyle(color="#ffffff", badge=Badge(output.getvalue())))
        self.assertIn("image/png", svg)

    def test_the_badge_does_not_move_the_central_glyph(self):
        plain = compose(self.folder, glyph_svg().encode(), GlyphStyle(color="#ffffff"))
        with_badge = self.icon()
        self.assertTrue(with_badge.startswith(plain[: plain.index("</svg>")].rstrip()[:200]))
        self.assertIn(plain[plain.index("<g"):plain.index("</svg>")], with_badge)


class BadgeSettingsTests(unittest.TestCase):
    def test_a_badge_free_setting_keeps_the_old_format(self):
        values = STAR.to_dict()
        for name in BADGE_FIELDS:
            self.assertNotIn(name, values)

    def test_a_badge_is_stored_with_all_its_settings(self):
        settings = replace(STAR, badge_glyph="heart-symbolic", badge_color="#3584e4", badge_corner="tl", badge_size=0.25)
        values = settings.to_dict()
        self.assertEqual({name: values[name] for name in BADGE_FIELDS}, {"badge_glyph": "heart-symbolic", "badge_color": "#3584e4", "badge_corner": "tl", "badge_size": 0.25})
        self.assertEqual(Settings.from_dict(values), settings)

    def test_an_old_dictionary_loads_without_a_badge(self):
        loaded = Settings.from_dict({"glyph_name": "star-symbolic", "size": 1.0})
        self.assertIsNone(loaded.badge_glyph)
        self.assertEqual((loaded.badge_corner, loaded.badge_size), ("br", 0.3))

    def test_invalid_badge_settings_are_refused(self):
        for changes in (
            {"badge_color": "red"},
            {"badge_color": ""},
            {"badge_glyph": ""},
            {"badge_glyph": "a\0b"},
            {"badge_corner": "top"},
            {"badge_size": 0.0},
            {"badge_size": 0.6},
            {"badge_size": float("inf")},
            {"badge_size": "big"},
            {"badge_size": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(InvalidSettingsError):
                replace(STAR, **{"badge_glyph": "heart-symbolic", **changes}).validate()

    def test_the_bounds_of_the_size_are_accepted(self):
        for size in (0.15, 0.5):
            replace(STAR, badge_glyph="heart-symbolic", badge_size=size).validate()


class BadgeServiceBase(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.themes = self.root / "themes"
        self.themes.mkdir()
        glyphs = {"star-symbolic": glyph_svg(), "heart-symbolic": glyph_svg("#ff0000")}
        make_theme(self.themes, "Blue", folder=folder_svg("#3366cc"), glyphs=glyphs)
        self.store = Store(self.root / "data")
        self.service = Service(self.store, theme_name=lambda: "Blue", search_dirs=[str(self.themes)], backend="directory")
        self.workspace = self.root / "folders"
        self.workspace.mkdir()
        self.folder = self.workspace / "docs"
        self.folder.mkdir()

    def badge_settings(self, **changes):
        return replace(STAR, badge_glyph="heart-symbolic", **changes)


class BadgeServiceTests(BadgeServiceBase):
    def test_build_draws_the_badge_only_when_asked(self):
        self.assertNotIn("<circle", self.service.build(STAR).svg)
        built = self.service.build(self.badge_settings(badge_color="#2190a4"))
        self.assertIn("<circle", built.svg)
        self.assertIn("#2190a4", built.svg)

    def test_the_default_color_is_used_without_a_choice(self):
        self.assertIn(BADGE_DEFAULT_COLOR, self.service.build(self.badge_settings()).svg)

    def test_applying_stores_and_regenerates_the_badge(self):
        entry = self.service.apply(self.folder, self.badge_settings(badge_corner="tr"))
        self.assertEqual(entry.settings["badge_corner"], "tr")
        stored = self.store.get(str(self.folder.resolve()))
        self.assertEqual(Settings.from_dict(stored.settings).badge_glyph, "heart-symbolic")
        self.assertIn("<circle", self.store.icon_path(entry.digest).read_text())
        regenerated = self.service.regenerate_all() if hasattr(self.service, "regenerate_all") else None
        self.assertIn("<circle", self.store.icon_path(self.store.get(str(self.folder.resolve())).digest).read_text())

    def test_a_folder_without_badge_has_the_same_registry_as_before(self):
        self.service.apply(self.folder, STAR)
        document = json.loads(self.store.registry_path.read_text())
        text = json.dumps(document)
        self.assertNotIn("badge", text)

    def test_the_same_icon_without_a_badge_keeps_its_digest(self):
        first = self.service.apply(self.folder, STAR)
        other = self.workspace / "other"
        other.mkdir()
        second = self.service.apply(other, STAR)
        self.assertEqual(first.digest, second.digest)

    def test_an_unknown_badge_glyph_is_an_error(self):
        with self.assertRaises(Exception):
            self.service.build(replace(STAR, badge_glyph="missing-symbolic"))

    def test_the_badge_does_not_change_the_central_glyph_color_choice(self):
        plain = self.service.build(STAR)
        with_badge = self.service.build(self.badge_settings())
        self.assertEqual(plain.color, with_badge.color)


class BadgeOnlyTests(BadgeServiceBase):

    def only(self, **changes):
        return Settings(badge_glyph="heart-symbolic", **changes)

    def test_a_badge_alone_is_a_valid_setting(self):
        self.only().validate()

    def test_without_pictogram_and_badge_there_is_nothing_to_draw(self):
        with self.assertRaises(InvalidSettingsError):
            Settings().validate()
        folder = self.root / "plain-folder.svg"
        folder.write_text(folder_svg("#3366cc"))
        with self.assertRaises(InvalidIconError):
            compose(folder, None, GlyphStyle(color="#ffffff"))

    def test_two_pictogram_sources_are_still_refused(self):
        with self.assertRaises(InvalidSettingsError):
            Settings(glyph_name="a-symbolic", glyph_text="x", badge_glyph="heart-symbolic").validate()

    def test_the_icon_has_the_disc_and_no_central_pictogram(self):
        alone = self.service.build(self.only()).svg
        both = self.service.build(self.badge_settings()).svg
        self.assertEqual(alone.count("<circle"), 1)
        self.assertEqual(alone.count("<image"), both.count("<image") - 1)

    def test_a_badge_alone_is_applied_and_regenerated(self):
        entry = self.service.apply(self.folder, self.only(badge_corner="tl"))
        stored = Settings.from_dict(self.store.get(str(self.folder.resolve())).settings)
        self.assertEqual((stored.glyph_name, stored.badge_glyph), (None, "heart-symbolic"))
        self.assertIn("<circle", self.store.icon_path(entry.digest).read_text())
