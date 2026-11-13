from xml.sax.saxutils import quoteattr
import hashlib
import os
import re
import signal
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from unittest import mock

import cairo

from custicon import layout, render
from custicon.color import auto_glyph_color, average_visible, is_hex_color
from custicon.compose import GlyphStyle, box_of, compose, parse_svg, png_size
from custicon.errors import InvalidIconError
from custicon.render import pixel, read_icon_bytes, surface_from_bytes, surface_from_svg

from .support import SVG_OPEN, WorkspaceTestCase, folder_svg, glyph_svg, solid_png

ROOT = Path(__file__).resolve().parent.parent
COLOR = "#112233"


def png_bytes(width: int, height: int, rows: bool = True) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
    body = zlib.compress((b"\x00" + b"\xff" * ((width + 7) // 8)) * height, 9) if rows else b""
    return b"\x89PNG\r\n\x1a\n" + header + chunk(b"IDAT", body) + chunk(b"IEND", b"")


class HostileTestCase(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def write(self, name: str, content) -> Path:
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def compose(self, folder=None, glyph=None, **style):
        folder_path = self.write("folder.svg", folder if folder is not None else folder_svg("#3366cc"))
        glyph_path = self.write("glyph.svg", glyph if glyph is not None else glyph_svg())
        return compose(folder_path, glyph_path, GlyphStyle(color=style.pop("color", COLOR), **style))

    def rgba(self, svg: str, x: int, y: int) -> list[float]:
        return [round(value, 2) for value in pixel(surface_from_svg(svg.encode(), 128), x, y)]


class ColorTests(HostileTestCase):
    def test_a_trailing_newline_is_not_a_color(self):
        for value in ("#aabbcc\n", "#aabbcc\n\n", " #aabbcc", "#aabbcc "):
            self.assertFalse(is_hex_color(value), repr(value))

    def test_a_slightly_translucent_folder_still_gets_an_automatic_color(self):
        svg = f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128"><rect width="128" height="128" fill="#3584e4" opacity="0.98"/></svg>'
        color = auto_glyph_color(self.write("glass.svg", svg))
        self.assertTrue(is_hex_color(color))

    def test_the_automatic_color_weighs_pixels_by_their_opacity(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 2, 1)
        context = cairo.Context(surface)
        context.set_source_rgba(1, 0, 0, 1)
        context.rectangle(0, 0, 1, 1)
        context.fill()
        context.set_source_rgba(0, 0, 1, 0.5)
        context.rectangle(1, 0, 1, 1)
        context.fill()
        red, green, blue = average_visible(surface, 0, 0, 2, 1)
        self.assertAlmostEqual(red, 2 / 3, delta=0.02)
        self.assertAlmostEqual(blue, 1 / 3, delta=0.02)

    def test_a_nearly_invisible_folder_is_still_refused(self):
        svg = f'{SVG_OPEN} width="128" height="128" viewBox="0 0 128 128"><rect width="128" height="128" fill="#3584e4" opacity="0.02"/></svg>'
        with self.assertRaises(InvalidIconError):
            auto_glyph_color(self.write("ghost.svg", svg))


class ReadingTests(HostileTestCase):
    def test_a_named_pipe_is_refused_instead_of_blocking(self):
        fifo = self.root / "pipe.svg"
        os.mkfifo(fifo)

        def give_up(signum, frame):
            raise TimeoutError("reading a named pipe blocked")

        signal.signal(signal.SIGALRM, give_up)
        signal.alarm(3)
        try:
            with self.assertRaises(InvalidIconError):
                read_icon_bytes(fifo)
        finally:
            signal.alarm(0)

    def test_a_file_that_grows_after_the_size_check_is_still_bounded(self):
        path = self.write("grown.svg", folder_svg() + "<!-- " + "x" * 4000 + " -->")
        real = path.stat()
        small = os.stat_result((real.st_mode, real.st_ino, real.st_dev, real.st_nlink, real.st_uid, real.st_gid, 10, 0, 0, 0))
        with mock.patch.object(render, "MAX_FILE_BYTES", 1024), mock.patch.object(Path, "stat", return_value=small):
            with self.assertRaises(InvalidIconError):
                read_icon_bytes(path)

    def test_a_character_device_is_refused(self):
        with self.assertRaises(InvalidIconError):
            read_icon_bytes(Path("/dev/null"))

    def test_a_declared_size_over_the_limit_is_refused_without_reading(self):
        path = self.write("small.svg", folder_svg())
        real = path.stat()
        huge = os.stat_result((real.st_mode, real.st_ino, real.st_dev, real.st_nlink, real.st_uid, real.st_gid, layout.MAX_FILE_BYTES + 1, 0, 0, 0))
        with mock.patch.object(Path, "stat", return_value=huge), mock.patch.object(Path, "open", side_effect=AssertionError("opened")):
            with self.assertRaisesRegex(InvalidIconError, "too large"):
                read_icon_bytes(path)

    def test_the_read_is_bounded_to_one_byte_over_the_limit(self):
        path = self.write("small.svg", folder_svg())
        handle = mock.MagicMock()
        handle.__enter__.return_value = handle
        handle.read.return_value = b"<svg/>"
        with mock.patch.object(Path, "open", return_value=handle):
            read_icon_bytes(path)
        handle.read.assert_called_once_with(layout.MAX_FILE_BYTES + 1)

    def test_a_directory_is_refused(self):
        with self.assertRaises(InvalidIconError):
            read_icon_bytes(self.root)

    def test_the_size_limit_is_two_mebibytes(self):
        self.assertEqual(layout.MAX_FILE_BYTES, 2 * 1024 * 1024)


class PngTests(HostileTestCase):
    def test_a_huge_declared_size_is_refused_before_decoding(self):
        bomb = png_bytes(20000, 20000, rows=False)
        with self.assertRaisesRegex(InvalidIconError, "too large"):
            surface_from_bytes(bomb, 64)

    def test_the_side_limit_is_the_same_for_the_glyph_and_the_folder(self):
        bomb = png_bytes(layout.MAX_IMAGE_SIDE + 1, 8, rows=False)
        with self.assertRaisesRegex(InvalidIconError, "too large"):
            png_size(bomb)

    def test_a_png_at_the_limit_is_accepted(self):
        self.assertEqual(png_size(png_bytes(layout.MAX_IMAGE_SIDE, 1)), (layout.MAX_IMAGE_SIDE, 1))

    def test_a_png_header_without_ihdr_is_refused(self):
        with self.assertRaises(InvalidIconError):
            png_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)

    def test_a_png_whose_first_chunk_is_not_ihdr_is_refused(self):
        data = bytearray(png_bytes(8, 8))
        data[12:16] = b"IDAT"
        with self.assertRaises(InvalidIconError):
            png_size(bytes(data))

    def test_a_truncated_png_header_is_refused(self):
        with self.assertRaises(InvalidIconError):
            png_size(b"\x89PNG\r\n\x1a\n\x00\x00")

    def test_a_bomb_as_a_pictogram_is_refused_by_compose(self):
        bomb = self.write("bomb.png", png_bytes(layout.MAX_IMAGE_SIDE + 1, 4, rows=False))
        folder = self.write("folder.svg", folder_svg())
        with self.assertRaises(InvalidIconError):
            compose(folder, bomb, GlyphStyle(color=COLOR))


class SvgLimitsTests(HostileTestCase):
    def test_a_document_with_too_many_elements_is_refused(self):
        many = f'{SVG_OPEN} viewBox="0 0 16 16">' + "<g/>" * (layout.MAX_SVG_TAGS + 1) + "</svg>"
        with self.assertRaises(InvalidIconError):
            self.compose(glyph=many)
        with self.assertRaises(InvalidIconError):
            surface_from_svg(many.encode(), 16)

    def test_deep_nesting_is_refused_cleanly(self):
        for depth in (layout.MAX_SVG_DEPTH + 1, 1200, 3000):
            nested = f'{SVG_OPEN} viewBox="0 0 16 16">' + "<g>" * depth + '<path d="M0 0h1v1z"/>' + "</g>" * depth + "</svg>"
            with self.assertRaises(InvalidIconError, msg=str(depth)):
                self.compose(glyph=nested)

    def test_reasonable_nesting_is_accepted(self):
        nested = f'{SVG_OPEN} viewBox="0 0 16 16">' + "<g>" * 50 + '<path d="M0 0h16v16H0z"/>' + "</g>" * 50 + "</svg>"
        self.assertTrue(self.compose(glyph=nested))

    def test_unknown_xml_encodings_are_refused_cleanly(self):
        for encoding in ("foo", "xyz-9", "shift_jis", "utf-7"):
            data = f'<?xml version="1.0" encoding="{encoding}"?>'.encode() + glyph_svg().encode()
            with self.assertRaises(InvalidIconError, msg=encoding):
                parse_svg(data)

    def test_entity_bombs_are_refused(self):
        levels = "".join(f'<!ENTITY {name} "{"&" + previous + ";"}{"".join("&" + previous + ";" for _ in range(9))}">' for previous, name in zip("abcdefg", "bcdefgh"))
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa">' + levels + "]>"
            f'<svg {SVG_OPEN.split(" ", 1)[1]} viewBox="0 0 16 16"><text>&h;</text></svg>'
        )
        with self.assertRaises(InvalidIconError):
            parse_svg(bomb.encode())


class BoxTests(HostileTestCase):
    def box(self, attributes: str):
        return box_of(parse_svg(f"<svg {SVG_OPEN.split(' ', 1)[1]} {attributes}/>".encode()))

    def test_non_finite_or_absurd_boxes_are_refused(self):
        for attributes in (
            'viewBox="0 0 nan nan"',
            'viewBox="0 0 inf inf"',
            'viewBox="0 0 1e308 1e308"',
            'viewBox="0 0 1e-320 1e-320"',
            'viewBox="nan 0 16 16"',
            'viewBox="1e30 0 16 16"',
            'width="1e999" height="16"',
            'width="nan" height="16"',
        ):
            with self.assertRaises(InvalidIconError, msg=attributes):
                self.box(attributes)

    def test_exponents_are_read(self):
        self.assertEqual(self.box('width="1e3" height="2.5e1"')[2:], (1000.0, 25.0))

    def test_units_are_converted_or_refused(self):
        self.assertEqual(self.box('width="16px" height="16"')[2:], (16.0, 16.0))
        self.assertAlmostEqual(self.box('width="12pt" height="12pt"')[2], 16.0, places=3)
        self.assertAlmostEqual(self.box('width="1in" height="1in"')[2], 96.0, places=3)
        for attributes in ('width="2em" height="2em"', 'width="100%" height="100%"', 'width="auto" height="16"'):
            with self.assertRaises(InvalidIconError, msg=attributes):
                self.box(attributes)

    def test_commas_in_a_view_box_are_accepted(self):
        self.assertEqual(self.box('viewBox="0,0,16,16"'), (0.0, 0.0, 16.0, 16.0))

    def test_the_output_never_contains_non_finite_numbers(self):
        for style in ({"size": float("nan")}, {"offset_x": float("inf")}, {"offset_y": float("-inf")}, {"size": 1e400}):
            with self.assertRaises(InvalidIconError, msg=str(style)):
                self.compose(**style)

    def test_a_missing_color_is_an_icon_error_not_a_type_error(self):
        folder = self.write("folder.svg", folder_svg())
        glyph = self.write("glyph.svg", glyph_svg())
        with self.assertRaises(InvalidIconError):
            compose(folder, glyph, GlyphStyle(color=None))

    def test_a_zero_or_negative_size_is_refused(self):
        for size in (0.0, -1.0):
            with self.assertRaises(InvalidIconError, msg=str(size)):
                self.compose(size=size)

    def test_sizes_and_offsets_beyond_the_limits_are_refused(self):
        for style in ({"size": layout.MAX_STYLE_SIZE * 2}, {"offset_x": layout.MAX_STYLE_OFFSET * 2}, {"offset_y": -layout.MAX_STYLE_OFFSET * 2}):
            with self.assertRaises(InvalidIconError, msg=str(style)):
                self.compose(**style)

    def test_a_large_origin_keeps_its_precision(self):
        folder = f'{SVG_OPEN} viewBox="123456.5 0 16 16"><rect x="123456.5" width="16" height="16" fill="#3366cc"/></svg>'
        glyph = glyph_svg(COLOR)
        svg = self.compose(folder=folder, glyph=glyph)
        self.assertIn("123456.5", svg)


class IsolationTests(HostileTestCase):
    def test_a_color_equal_to_an_identifier_is_not_rewritten(self):
        folder = (
            f'{SVG_OPEN} viewBox="0 0 128 128"><clipPath id="fff"><rect width="128" height="128"/></clipPath>'
            '<rect width="128" height="128" fill="#fff" clip-path="url(#fff)"/></svg>'
        )
        self.assertEqual(self.rgba(self.compose(folder=folder), 5, 5), [1.0, 1.0, 1.0, 1.0])

    def test_reference_spellings_all_keep_working(self):
        for reference in ("url(#a)", "url( #a )", "url('#a')", 'url("#a")'):
            folder = (
                f'{SVG_OPEN} viewBox="0 0 128 128"><defs><linearGradient id="a"><stop stop-color="#00ff00"/></linearGradient></defs>'
                f"<rect width='128' height='128' fill={quoteattr(reference)}/></svg>"
            )
            self.assertEqual(self.rgba(self.compose(folder=folder), 5, 5)[:3], [0.0, 1.0, 0.0], reference)

    def test_identifiers_that_prefix_each_other_keep_working(self):
        folder = (
            f'{SVG_OPEN} viewBox="0 0 128 128"><defs><linearGradient id="a"><stop stop-color="#00ff00"/></linearGradient>'
            '<linearGradient id="f-a"><stop stop-color="#ff0000"/></linearGradient></defs>'
            '<rect width="128" height="128" fill="url(#a)"/></svg>'
        )
        self.assertEqual(self.rgba(self.compose(folder=folder), 5, 5)[:3], [0.0, 1.0, 0.0])

    def test_the_output_does_not_depend_on_the_hash_seed(self):
        code = (
            "import sys, hashlib\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "from pathlib import Path\n"
            "from custicon.compose import compose, GlyphStyle\n"
            f"folder = Path({str(self.write('seed-folder.svg', self.seed_folder()))!r})\n"
            f"glyph = Path({str(self.write('seed-glyph.svg', glyph_svg()))!r})\n"
            f"print(hashlib.sha256(compose(folder, glyph, GlyphStyle(color={COLOR!r})).encode()).hexdigest())\n"
        )
        digests = set()
        for seed in ("0", "1", "2", "3", "4", "5"):
            environment = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, env=environment, check=True)
            digests.add(result.stdout.strip())
        self.assertEqual(len(digests), 1)

    def seed_folder(self) -> str:
        return (
            f'{SVG_OPEN} viewBox="0 0 128 128"><defs><linearGradient id="a"><stop stop-color="#00ff00"/></linearGradient>'
            '<linearGradient id="f-a"><stop stop-color="#ff0000"/></linearGradient><clipPath id="fff"><rect width="128" height="128"/></clipPath></defs>'
            '<rect width="128" height="128" fill="url(#a)" clip-path="url(#fff)"/></svg>'
        )

    def test_a_folder_style_sheet_cannot_recolor_the_pictogram(self):
        folder = f'{SVG_OPEN} viewBox="0 0 128 128"><style>path{{fill:#ff0000}}</style><rect width="128" height="128" fill="#3366cc"/></svg>'
        self.assertEqual(self.rgba(self.compose(folder=folder), 64, 76)[:3], [0.07, 0.13, 0.2])

    def test_a_pictogram_style_sheet_cannot_alter_the_folder(self):
        glyph = f'{SVG_OPEN} viewBox="0 0 16 16"><style>.st0{{fill:#00ff00}} svg > rect{{display:none}}</style><path class="st0" d="M0 0h16v16H0z"/></svg>'
        folder = f'{SVG_OPEN} viewBox="0 0 128 128"><rect class="st0" width="128" height="128" fill="#3366cc"/></svg>'
        svg = self.compose(folder=folder, glyph=glyph, tint=False)
        self.assertEqual(self.rgba(svg, 10, 10)[:3], [0.2, 0.4, 0.8])
        self.assertEqual(self.rgba(svg, 64, 76)[:3], [0.0, 1.0, 0.0])

    def test_root_attributes_of_the_folder_are_kept(self):
        folder = f'{SVG_OPEN} viewBox="0 0 128 128" fill="#00ff00"><rect width="128" height="128"/></svg>'
        self.assertEqual(self.rgba(self.compose(folder=folder), 5, 5)[:3], [0.0, 1.0, 0.0])

    def test_the_tint_filter_never_collides_with_a_folder_identifier(self):
        folder = f'{SVG_OPEN} viewBox="0 0 128 128"><filter id="custicon-tint"><feFlood flood-color="#00ff00"/></filter><rect width="128" height="128" fill="#3366cc"/></svg>'
        glyph = self.write("glyph.png", solid_png((0.0, 0.0, 0.0)))
        svg = compose(self.write("folder.svg", folder), glyph, GlyphStyle(color=COLOR))
        ids = re.findall(r'\bid="([^"]+)"', svg)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 2)

    def test_the_pictogram_is_embedded_as_a_separate_document(self):
        svg = self.compose()
        self.assertIn("data:image/svg+xml;base64,", svg)

    def test_a_mask_in_a_tinted_pictogram_keeps_its_shape(self):
        glyph = (
            f'{SVG_OPEN} viewBox="0 0 16 16"><mask id="m"><rect width="16" height="16" fill="#fff"/>'
            '<rect x="5" y="5" width="6" height="6" fill="#000"/></mask><path mask="url(#m)" d="M0 0h16v16H0z"/></svg>'
        )
        svg = self.compose(glyph=glyph)
        self.assertEqual(self.rgba(svg, 54, 66)[:3], [0.07, 0.13, 0.2])
        self.assertEqual(self.rgba(svg, 64, 76)[:3], [0.2, 0.4, 0.8])


class SanitizeTests(HostileTestCase):
    def test_css_imports_and_external_urls_are_removed(self):
        folder = (
            f'{SVG_OPEN} viewBox="0 0 128 128"><style>@import url("http://127.0.0.1:9/a.css"); @import "file:///etc/hostname";'
            ' rect{fill:url(http://127.0.0.1:9/b.svg#x) red}</style>'
            '<rect width="128" height="128" fill="url(http://127.0.0.1:9/c.svg#y)" filter="url(file:///etc/x.svg#f)" '
            'style="fill:url(file:///etc/hostname)"/></svg>'
        )
        svg = self.compose(folder=folder)
        for forbidden in ("127.0.0.1", "/etc/", "@import"):
            self.assertNotIn(forbidden, svg)

    def test_a_base_override_is_removed(self):
        folder = f'{SVG_OPEN} viewBox="0 0 128 128" xml:base="file:///etc/"><rect width="128" height="128" fill="#3366cc"/></svg>'
        self.assertNotIn("xml:base", self.compose(folder=folder))
        self.assertNotIn("file:///etc", self.compose(folder=folder))

    def test_local_references_and_embedded_images_survive(self):
        folder = (
            f'{SVG_OPEN} viewBox="0 0 128 128"><defs><linearGradient id="a"><stop stop-color="#00ff00"/></linearGradient></defs>'
            '<rect width="128" height="128" fill="url(#a)"/></svg>'
        )
        self.assertEqual(self.rgba(self.compose(folder=folder), 5, 5)[:3], [0.0, 1.0, 0.0])


class RegressionDigestTests(HostileTestCase):
    def test_the_same_inputs_give_the_same_digest(self):
        first = hashlib.sha256(self.compose().encode()).hexdigest()
        second = hashlib.sha256(self.compose().encode()).hexdigest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
