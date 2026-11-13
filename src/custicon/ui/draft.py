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
    auto_color: bool = True
    color: str = DEFAULT_COLOR
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    @property
    def ready(self) -> bool:
        return bool(self.glyph_name or self.glyph_file)

    def with_glyph_name(self, name: str) -> "Draft":
        return replace(self, glyph_name=name, glyph_file=None)

    def with_glyph_file(self, path: Path | str) -> "Draft":
        return replace(self, glyph_file=str(path), glyph_name=None)

    def with_default_appearance(self) -> "Draft":
        return replace(self, auto_color=True, size=1.0, offset_x=0.0, offset_y=0.0)

    def has_default_appearance(self) -> bool:
        return self.auto_color and self.size == 1.0 and self.offset_x == 0.0 and self.offset_y == 0.0

    def to_settings(self) -> Settings:
        return Settings(
            glyph_name=self.glyph_name,
            glyph_file=self.glyph_file,
            color=None if self.auto_color else self.color,
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
        return cls(
            folder=Path(entry.folder),
            glyph_name=glyph_name,
            glyph_file=glyph_file,
            tint=settings.tint if isinstance(settings.tint, bool) else True,
            auto_color=color is None,
            color=color or (entry.color if is_hex_color(entry.color) else DEFAULT_COLOR),
            size=clean_number(settings.size, MIN_SIZE, MAX_SIZE, 1.0),
            offset_x=clean_number(settings.offset_x, -MAX_OFFSET, MAX_OFFSET, 0.0),
            offset_y=clean_number(settings.offset_y, -MAX_OFFSET, MAX_OFFSET, 0.0),
        )
