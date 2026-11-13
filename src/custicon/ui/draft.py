from dataclasses import dataclass, replace
from pathlib import Path

import math

from ..color import is_hex_color
from ..service import MAX_OFFSET, MAX_SIZE, MIN_SIZE, Settings
from ..store import Entry

DEFAULT_COLOR = "#3584e4"


def clean_number(value, lower: float, upper: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return min(upper, max(lower, float(value)))


def clean_text(value) -> str | None:
    return value if isinstance(value, str) and value and "\0" not in value else None


@dataclass(frozen=True)
class Draft:
    folder: Path
    glyph_name: str | None = None
    glyph_file: str | None = None
    tint: bool = True
    color_mode: str = "auto"
    color: str = DEFAULT_COLOR
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    @property
    def auto_color(self) -> bool:
        return self.color_mode == "auto"

    @property
    def ready(self) -> bool:
        return bool(self.glyph_name or self.glyph_file)

    def with_glyph_name(self, name: str) -> "Draft":
        return replace(self, glyph_name=name, glyph_file=None)

    def with_glyph_file(self, path: Path | str) -> "Draft":
        return replace(self, glyph_file=str(path), glyph_name=None)

    def with_appearance(self, values: dict) -> "Draft":
        changes = {name: values[name] for name in ("size", "offset_x", "offset_y", "tint") if name in values}
        if "color" in values or "accent" in values:
            if values.get("accent") is True:
                changes["color_mode"] = "accent"
            elif values.get("color") is not None:
                changes["color_mode"] = "custom"
                changes["color"] = values["color"]
            else:
                changes["color_mode"] = "auto"
        return replace(self, **changes)

    def with_default_appearance(self) -> "Draft":
        return replace(self, color_mode="auto", size=1.0, offset_x=0.0, offset_y=0.0)

    def has_default_appearance(self) -> bool:
        return self.auto_color and self.size == 1.0 and self.offset_x == 0.0 and self.offset_y == 0.0

    def to_settings(self) -> Settings:
        return Settings(
            glyph_name=self.glyph_name,
            glyph_file=self.glyph_file,
            color=self.color if self.color_mode == "custom" else None,
            accent=self.color_mode == "accent",
            size=self.size,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
            tint=self.tint,
        )

    @classmethod
    def from_entry(cls, entry: Entry) -> "Draft":
        settings = Settings.from_dict(entry.settings)
        glyph_name = clean_text(settings.glyph_name)
        glyph_file = None if glyph_name else clean_text(settings.glyph_file)
        color = settings.color if is_hex_color(settings.color) else None
        accent = settings.accent is True and color is None
        return cls(
            folder=Path(entry.folder),
            glyph_name=glyph_name,
            glyph_file=glyph_file,
            tint=settings.tint if isinstance(settings.tint, bool) else True,
            color_mode="accent" if accent else ("auto" if color is None else "custom"),
            color=color or (entry.color if is_hex_color(entry.color) else DEFAULT_COLOR),
            size=clean_number(settings.size, MIN_SIZE, MAX_SIZE, 1.0),
            offset_x=clean_number(settings.offset_x, -MAX_OFFSET, MAX_OFFSET, 0.0),
            offset_y=clean_number(settings.offset_y, -MAX_OFFSET, MAX_OFFSET, 0.0),
        )
