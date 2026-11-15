import base64
import colorsys
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .color import is_hex_color
from .errors import InvalidIconError
from .layout import (
    FACE_X,
    FACE_Y,
    GLYPH_SIZE,
    MAX_COORDINATE,
    MAX_STYLE_OFFSET,
    MAX_STYLE_SIZE,
    MAX_SVG_DEPTH,
    MIN_DIMENSION,
    OUTPUT_SIZE,
)
from .render import check_svg_bytes, is_png, png_size, read_icon_bytes

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
NO_PAINT = {"none", "transparent"}
PAINT_PROPERTIES = ("fill", "stroke")
FORBIDDEN_TAGS = {"script", "foreignObject"}
SAFE_REFERENCES = ("#", "data:image/")
REFERENCE_ATTRIBUTES = ("href", "src")
MASKING_TAGS = {"mask", "clipPath"}
BOX_ATTRIBUTES = ("width", "height", "viewBox")
LENGTH = re.compile(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z%]*)\s*$")
UNIT_FACTORS = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}
EXTERNAL_URL = re.compile(r"url\(\s*(?!['\"]?\s*(?:#|data:image/))[^)]*\)", re.IGNORECASE)
CSS_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)
TINT_FILTER_ID = "custicon-tint"
FOLDER_TINT_FILTER_ID = "custicon-folder-tint"

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class GlyphStyle:
    color: str
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    tint: bool = True
    folder_tint: str | None = None
    flat_tint: bool = False


def svg_tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def number(value: float) -> str:
    return format(value, ".10g")


def check_depth(root: ET.Element) -> None:
    stack = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        if depth > MAX_SVG_DEPTH:
            raise InvalidIconError("SVG is nested too deeply")
        stack.extend((child, depth + 1) for child in element)


def parse_svg(data: bytes) -> ET.Element:
    check_svg_bytes(data)
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, LookupError, ValueError) as error:
        raise InvalidIconError(str(error)) from error
    if root.tag != svg_tag("svg"):
        raise InvalidIconError("not an SVG document")
    check_depth(root)
    return root


def serialize(root: ET.Element) -> str:
    try:
        return ET.tostring(root, encoding="unicode")
    except RecursionError as error:
        raise InvalidIconError("SVG is nested too deeply") from error


def parse_length(value: str | None) -> float | None:
    match = LENGTH.match(value or "")
    if not match:
        return None
    factor = UNIT_FACTORS.get(match.group(2).lower())
    if factor is None:
        return None
    return float(match.group(1)) * factor


def box_of(root: ET.Element) -> Box:
    view_box = root.get("viewBox")
    if view_box:
        parts = view_box.replace(",", " ").split()
        if len(parts) != 4:
            raise InvalidIconError("invalid viewBox")
        try:
            x, y, width, height = (float(part) for part in parts)
        except ValueError as error:
            raise InvalidIconError("invalid viewBox") from error
    else:
        x, y = 0.0, 0.0
        width, height = parse_length(root.get("width")), parse_length(root.get("height"))
    if width is None or height is None:
        raise InvalidIconError("the icon has no usable size")
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        raise InvalidIconError("the icon size is not finite")
    if abs(x) > MAX_COORDINATE or abs(y) > MAX_COORDINATE:
        raise InvalidIconError("the icon origin is out of range")
    if not MIN_DIMENSION <= width <= MAX_COORDINATE or not MIN_DIMENSION <= height <= MAX_COORDINATE:
        raise InvalidIconError("the icon has no usable size")
    return x, y, width, height


def image_element(data: bytes, mime: str, width: float, height: float, x: float = 0.0, y: float = 0.0) -> ET.Element:
    encoded = base64.b64encode(data).decode()
    attributes = {
        "width": number(width),
        "height": number(height),
        f"{{{XLINK_NS}}}href": f"data:{mime};base64,{encoded}",
    }
    if x or y:
        attributes["x"] = number(x)
        attributes["y"] = number(y)
    return ET.Element(svg_tag("image"), attributes)


def raster_root(data: bytes) -> ET.Element:
    width, height = png_size(data)
    root = ET.Element(
        svg_tag("svg"), {"width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}"}
    )
    root.append(image_element(data, "image/png", width, height))
    return root


def strip_external_urls(text: str) -> str:
    return EXTERNAL_URL.sub("none", text)


def strip_css(text: str) -> str:
    return strip_external_urls(CSS_IMPORT.sub("", text))


def sanitize(root: ET.Element) -> None:
    for parent in list(root.iter()):
        for child in list(parent):
            if local_name(child.tag) in FORBIDDEN_TAGS:
                parent.remove(child)
    for element in root.iter():
        for key in list(element.attrib):
            value = element.get(key, "")
            if key == XML_BASE or (local_name(key) in REFERENCE_ATTRIBUTES and not value.startswith(SAFE_REFERENCES)):
                del element.attrib[key]
            elif "url(" in value.lower():
                element.set(key, strip_external_urls(value))
        if local_name(element.tag) == "style" and element.text:
            element.text = strip_css(element.text)


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
    protected = set()
    for element in root.iter():
        if local_name(element.tag) in MASKING_TAGS:
            protected.update(element.iter())
    for element in root.iter():
        if element in protected:
            continue
        for name in PAINT_PROPERTIES:
            value = element.get(name)
            if value is not None and value.strip().lower() not in NO_PAINT:
                element.set(name, color)
        style = element.get("style")
        if style is not None:
            element.set("style", recolor_style(style, color))


def unique_id(wanted: str, taken: set[str]) -> str:
    candidate = wanted
    counter = 1
    while candidate in taken:
        counter += 1
        candidate = f"{wanted}-{counter}"
    return candidate


def tint_filter(color: str, filter_id: str) -> ET.Element:
    defs = ET.Element(svg_tag("defs"))
    tint = ET.SubElement(
        defs,
        svg_tag("filter"),
        {
            "id": filter_id,
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


def folder_tint_filter(color: str, filter_id: str) -> ET.Element:
    defs = ET.Element(svg_tag("defs"))
    tint = ET.SubElement(
        defs,
        svg_tag("filter"),
        {
            "id": filter_id,
            "x": "0",
            "y": "0",
            "width": "100%",
            "height": "100%",
            "color-interpolation-filters": "sRGB",
        },
    )
    ET.SubElement(tint, svg_tag("feFlood"), {"flood-color": color, "result": "tint"})
    ET.SubElement(tint, svg_tag("feBlend"), {"in": "tint", "in2": "SourceGraphic", "mode": "color", "result": "blended"})
    ET.SubElement(tint, svg_tag("feComposite"), {"in": "blended", "in2": "SourceAlpha", "operator": "in"})
    return defs


def tint_folder(folder: ET.Element, color: str, taken_ids: set[str]) -> None:
    filter_id = unique_id(FOLDER_TINT_FILTER_ID, taken_ids)
    group = ET.Element(svg_tag("g"), {"filter": f"url(#{filter_id})"})
    group.extend(list(folder))
    for child in list(folder):
        folder.remove(child)
    folder.append(folder_tint_filter(color, filter_id))
    folder.append(group)


HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z])")
FLAT_TINT_ATTRIBUTES = ("fill", "stroke", "stop-color", "flood-color", "color")


def hue_of(color: str) -> tuple[float, float]:
    red, green, blue = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    hue, _lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    return hue, saturation


def retint(match: re.Match, hue: float, saturation: float) -> str:
    text = match.group(0)
    if len(text) == 4:
        text = "#" + "".join(character * 2 for character in text[1:])
    red, green, blue = (int(text[index : index + 2], 16) / 255 for index in (1, 3, 5))
    _hue, lightness, _saturation = colorsys.rgb_to_hls(red, green, blue)
    channels = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#%02x%02x%02x" % tuple(round(channel * 255) for channel in channels)


def flat_tint_folder(folder: ET.Element, color: str) -> bool:
    if any(local_name(element.tag) in ("image", "filter") for element in folder.iter()):
        return False
    hue, saturation = hue_of(color)
    changed = False

    def convert(value: str) -> str:
        nonlocal changed
        result = HEX_COLOR.sub(lambda match: retint(match, hue, saturation), value)
        changed = changed or result != value
        return result

    for element in folder.iter():
        for name in FLAT_TINT_ATTRIBUTES:
            value = element.get(name)
            if value is not None:
                element.set(name, convert(value))
        style = element.get("style")
        if style is not None:
            element.set("style", convert(style))
        if local_name(element.tag) == "style" and element.text:
            element.text = convert(element.text)
    return changed


def taken_ids_of(root: ET.Element) -> set[str]:
    return {element.get("id") for element in root.iter() if element.get("id")}


def tinted_folder_svg(folder_file: Path, color: str, flat: bool = False) -> bytes:
    if not is_hex_color(color):
        raise InvalidIconError(f"invalid color {color!r}")
    folder = load_folder(folder_file)
    if not (flat and flat_tint_folder(folder, color)):
        tint_folder(folder, color, taken_ids_of(folder))
    return (XML_HEADER + serialize(folder)).encode()


def load_folder(path: Path) -> ET.Element:
    data = read_icon_bytes(path)
    root = raster_root(data) if is_png(data) else parse_svg(data)
    sanitize(root)
    return root


def load_glyph(source: Path | bytes, style: GlyphStyle, taken_ids: set[str]) -> tuple[list[ET.Element], Box]:
    data = source if isinstance(source, bytes) else read_icon_bytes(source)
    if is_png(data):
        width, height = png_size(data)
        image = image_element(data, "image/png", width, height)
        elements = [image]
        if style.tint:
            filter_id = unique_id(TINT_FILTER_ID, taken_ids)
            image.set("filter", f"url(#{filter_id})")
            elements.insert(0, tint_filter(style.color, filter_id))
        return elements, (0.0, 0.0, float(width), float(height))
    root = parse_svg(data)
    sanitize(root)
    if style.tint:
        recolor(root, style.color)
        root.set("fill", style.color)
    x, y, width, height = box_of(root)
    document = (XML_HEADER + serialize(root)).encode()
    return [image_element(document, "image/svg+xml", width, height, x, y)], (x, y, width, height)


def check_style(style: GlyphStyle) -> None:
    if not is_hex_color(style.color):
        raise InvalidIconError(f"invalid color {style.color!r}")
    if style.folder_tint is not None and not is_hex_color(style.folder_tint):
        raise InvalidIconError(f"invalid folder tint {style.folder_tint!r}")
    for value, limit in ((style.size, MAX_STYLE_SIZE), (style.offset_x, MAX_STYLE_OFFSET), (style.offset_y, MAX_STYLE_OFFSET)):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
            raise InvalidIconError("the pictogram size or position is out of range")
    if style.size <= 0:
        raise InvalidIconError("the pictogram size must be positive")


def compose(folder_file: Path, glyph: Path | bytes, style: GlyphStyle) -> str:
    check_style(style)
    folder = load_folder(folder_file)
    taken_ids = taken_ids_of(folder)
    if style.folder_tint is not None and not (style.flat_tint and flat_tint_folder(folder, style.folder_tint)):
        tint_folder(folder, style.folder_tint, taken_ids)
    glyph_elements, (glyph_x, glyph_y, glyph_width, glyph_height) = load_glyph(glyph, style, taken_ids)
    folder_x, folder_y, folder_width, folder_height = box_of(folder)

    result = ET.Element(
        svg_tag("svg"),
        {
            "width": str(OUTPUT_SIZE),
            "height": str(OUTPUT_SIZE),
            "viewBox": " ".join(number(v) for v in (folder_x, folder_y, folder_width, folder_height)),
        },
    )
    for key, value in folder.attrib.items():
        if key not in BOX_ATTRIBUTES:
            result.set(key, value)
    result.extend(list(folder))

    scale = GLYPH_SIZE * style.size * folder_width / max(glyph_width, glyph_height)
    center_x = folder_x + (FACE_X + style.offset_x) * folder_width
    center_y = folder_y + (FACE_Y + style.offset_y) * folder_height
    group = ET.SubElement(
        result,
        svg_tag("g"),
        {
            "transform": (
                f"translate({number(center_x)} {number(center_y)}) scale({number(scale)}) "
                f"translate({number(-(glyph_x + glyph_width / 2))} {number(-(glyph_y + glyph_height / 2))})"
            )
        },
    )
    group.extend(glyph_elements)
    return XML_HEADER + serialize(result) + "\n"
