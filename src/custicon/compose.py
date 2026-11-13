import base64
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .color import is_hex_color
from .errors import InvalidIconError
from .layout import FACE_X, FACE_Y, GLYPH_SIZE, OUTPUT_SIZE
from .render import is_png, read_icon_bytes

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
NO_PAINT = {"none", "transparent"}
PAINT_PROPERTIES = ("fill", "stroke")
FORBIDDEN_TAGS = {"script", "foreignObject"}
SAFE_REFERENCES = ("#", "data:image/")
LENGTH = re.compile(r"\s*([0-9]*\.?[0-9]+)")
FOLDER_PREFIX = "f-"
GLYPH_PREFIX = "g-"
TINT_FILTER_ID = "g-tint"

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class GlyphStyle:
    color: str
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    tint: bool = True


def svg_tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def number(value: float) -> str:
    return f"{value:.6g}"


def parse_svg(data: bytes) -> ET.Element:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise InvalidIconError(str(error)) from error
    if root.tag != svg_tag("svg"):
        raise InvalidIconError("not an SVG document")
    return root


def parse_length(value: str | None) -> float | None:
    match = LENGTH.match(value or "")
    return float(match.group(1)) if match else None


def box_of(root: ET.Element) -> Box:
    view_box = root.get("viewBox")
    if view_box:
        try:
            x, y, width, height = (float(part) for part in view_box.replace(",", " ").split())
        except ValueError as error:
            raise InvalidIconError("invalid viewBox") from error
    else:
        x, y = 0.0, 0.0
        width, height = parse_length(root.get("width")), parse_length(root.get("height"))
    if not width or not height or width <= 0 or height <= 0:
        raise InvalidIconError("the icon has no usable size")
    return x, y, width, height


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24:
        raise InvalidIconError("truncated PNG")
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        raise InvalidIconError("empty PNG")
    return width, height


def image_element(data: bytes, width: int, height: int) -> ET.Element:
    encoded = base64.b64encode(data).decode()
    return ET.Element(
        svg_tag("image"),
        {
            "width": str(width),
            "height": str(height),
            f"{{{XLINK_NS}}}href": "data:image/png;base64," + encoded,
        },
    )


def raster_root(data: bytes) -> ET.Element:
    width, height = png_size(data)
    root = ET.Element(
        svg_tag("svg"), {"width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}"}
    )
    root.append(image_element(data, width, height))
    return root


def prefix_ids(root: ET.Element, prefix: str) -> None:
    ids = {element.get("id") for element in root.iter() if element.get("id")}
    for element in root.iter():
        for key, value in list(element.attrib.items()):
            if key == "id":
                element.set(key, prefix + value)
                continue
            for old in ids:
                value = value.replace(f"url(#{old})", f"url(#{prefix}{old})")
                if value == f"#{old}":
                    value = f"#{prefix}{old}"
            element.set(key, value)


def sanitize(root: ET.Element) -> None:
    for parent in list(root.iter()):
        for child in list(parent):
            if local_name(child.tag) in FORBIDDEN_TAGS:
                parent.remove(child)
    for element in root.iter():
        for key in list(element.attrib):
            if local_name(key) == "href" and not element.get(key, "").startswith(SAFE_REFERENCES):
                del element.attrib[key]


def recolor_style(style: str, color: str) -> str:
    declarations = []
    for declaration in style.split(";"):
        name, separator, value = declaration.partition(":")
        if separator and name.strip() in PAINT_PROPERTIES and value.strip().lower() not in NO_PAINT:
            value = color
        declarations.append(f"{name.strip()}:{value.strip()}" if separator else declaration)
    return ";".join(part for part in declarations if part.strip())


def recolor(root: ET.Element, color: str) -> None:
    for parent in list(root.iter()):
        for child in list(parent):
            if local_name(child.tag) == "style":
                parent.remove(child)
    for element in root.iter():
        for name in PAINT_PROPERTIES:
            value = element.get(name)
            if value is not None and value.strip().lower() not in NO_PAINT:
                element.set(name, color)
        style = element.get("style")
        if style is not None:
            element.set("style", recolor_style(style, color))


def tint_filter(color: str) -> ET.Element:
    defs = ET.Element(svg_tag("defs"))
    tint = ET.SubElement(
        defs,
        svg_tag("filter"),
        {
            "id": TINT_FILTER_ID,
            "x": "0",
            "y": "0",
            "width": "100%",
            "height": "100%",
            "color-interpolation-filters": "sRGB",
        },
    )
    ET.SubElement(tint, svg_tag("feFlood"), {"flood-color": color})
    ET.SubElement(tint, svg_tag("feComposite"), {"in2": "SourceAlpha", "operator": "in"})
    return defs


def load_folder(path: Path) -> ET.Element:
    data = read_icon_bytes(path)
    root = raster_root(data) if is_png(data) else parse_svg(data)
    sanitize(root)
    prefix_ids(root, FOLDER_PREFIX)
    return root


def load_glyph(path: Path, style: GlyphStyle) -> tuple[list[ET.Element], Box]:
    data = read_icon_bytes(path)
    if is_png(data):
        width, height = png_size(data)
        image = image_element(data, width, height)
        elements = [image]
        if style.tint:
            image.set("filter", f"url(#{TINT_FILTER_ID})")
            elements.insert(0, tint_filter(style.color))
        return elements, (0.0, 0.0, float(width), float(height))
    root = parse_svg(data)
    sanitize(root)
    prefix_ids(root, GLYPH_PREFIX)
    if style.tint:
        recolor(root, style.color)
    return list(root), box_of(root)


def compose(folder_file: Path, glyph_file: Path, style: GlyphStyle) -> str:
    if not is_hex_color(style.color):
        raise InvalidIconError(f"invalid color {style.color!r}")
    folder = load_folder(folder_file)
    glyph_elements, (glyph_x, glyph_y, glyph_width, glyph_height) = load_glyph(glyph_file, style)
    folder_x, folder_y, folder_width, folder_height = box_of(folder)

    result = ET.Element(
        svg_tag("svg"),
        {
            "width": str(OUTPUT_SIZE),
            "height": str(OUTPUT_SIZE),
            "viewBox": " ".join(number(v) for v in (folder_x, folder_y, folder_width, folder_height)),
        },
    )
    result.extend(list(folder))

    scale = GLYPH_SIZE * style.size * folder_width / max(glyph_width, glyph_height)
    center_x = folder_x + (FACE_X + style.offset_x) * folder_width
    center_y = folder_y + (FACE_Y + style.offset_y) * folder_height
    attributes = {
        "transform": (
            f"translate({number(center_x)} {number(center_y)}) scale({number(scale)}) "
            f"translate({number(-(glyph_x + glyph_width / 2))} {number(-(glyph_y + glyph_height / 2))})"
        )
    }
    if style.tint:
        attributes["fill"] = style.color
    group = ET.SubElement(result, svg_tag("g"), attributes)
    group.extend(glyph_elements)
    return XML_HEADER + ET.tostring(result, encoding="unicode") + "\n"
