from dataclasses import dataclass, replace
from pathlib import Path

import math

from ..color import is_hex_color
from ..compose import BADGE_CORNERS, BADGE_DEFAULT_COLOR, BADGE_DEFAULT_SIZE, BADGE_MAX_SIZE, BADGE_MIN_SIZE
from ..service import MAX_OFFSET, MAX_SIZE, MIN_SIZE, Settings
from ..store import Entry

DEFAULT_COLOR = "#3584e4"
DEFAULT_FOLDER_TINT = "#e5a50a"
DEFAULT_BADGE_GLYPH = "phosphor:check"


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
    glyph_text: str | None = None
    tint: bool = True
    color_mode: str = "auto"
    color: str = DEFAULT_COLOR
    size: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    folder_tint_mode: str = "none"
    folder_tint: str = DEFAULT_FOLDER_TINT
    badge_on: bool = False
    badge_glyph: str | None = None
    badge_color: str = BADGE_DEFAULT_COLOR
    badge_corner: str = "br"
    badge_size: float = BADGE_DEFAULT_SIZE

    @property
    def badge_active(self) -> bool:
        return self.badge_on and bool(self.badge_glyph)

    @property
    def auto_color(self) -> bool:
        return self.color_mode == "auto"

    @property
    def ready(self) -> bool:
        return bool(self.glyph_name or self.glyph_file or self.glyph_text)

    def with_glyph_name(self, name: str) -> "Draft":
        return replace(self, glyph_name=name, glyph_file=None, glyph_text=None)

    def with_glyph_file(self, path: Path | str) -> "Draft":
        return replace(self, glyph_file=str(path), glyph_name=None, glyph_text=None)

    def with_glyph_text(self, text: str) -> "Draft":
        return replace(self, glyph_text=text or None, glyph_name=None, glyph_file=None)

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
        if "folder_tint" in values or "folder_tint_accent" in values:
            if values.get("folder_tint_accent") is True:
                changes["folder_tint_mode"] = "accent"
            elif values.get("folder_tint") is not None:
                changes["folder_tint_mode"] = "custom"
                changes["folder_tint"] = values["folder_tint"]
            else:
                changes["folder_tint_mode"] = "none"
        return replace(self, **changes)

    def with_default_appearance(self) -> "Draft":
        return replace(self, color_mode="auto", size=1.0, offset_x=0.0, offset_y=0.0, folder_tint_mode="none")

    def has_default_appearance(self) -> bool:
        return (
            self.auto_color
            and self.size == 1.0
            and self.offset_x == 0.0
            and self.offset_y == 0.0
            and self.folder_tint_mode == "none"
        )

    def appearance(self) -> dict:
        return {
            "color": self.color if self.color_mode == "custom" else None,
            "accent": self.color_mode == "accent",
            "size": self.size,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "tint": self.tint,
            "folder_tint": self.folder_tint if self.folder_tint_mode == "custom" else None,
            "folder_tint_accent": self.folder_tint_mode == "accent",
        }

    def to_settings(self) -> Settings:
        return Settings(
            glyph_name=self.glyph_name,
            glyph_file=self.glyph_file,
            glyph_text=self.glyph_text,
            color=self.color if self.color_mode == "custom" else None,
            accent=self.color_mode == "accent",
            size=self.size,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
            tint=self.tint,
            folder_tint=self.folder_tint if self.folder_tint_mode == "custom" else None,
            folder_tint_accent=self.folder_tint_mode == "accent",
            badge_glyph=self.badge_glyph if self.badge_active else None,
            badge_color=self.badge_color if self.badge_active else None,
            badge_corner=self.badge_corner if self.badge_active else "br",
            badge_size=self.badge_size if self.badge_active else BADGE_DEFAULT_SIZE,
        )

    @classmethod
    def from_entry(cls, entry: Entry) -> "Draft":
        settings = Settings.from_dict(entry.settings)
        glyph_name = clean_text(settings.glyph_name)
        glyph_file = None if glyph_name else clean_text(settings.glyph_file)
        glyph_text = None if glyph_name or glyph_file else clean_text(settings.glyph_text)
        color = settings.color if is_hex_color(settings.color) else None
        accent = settings.accent is True and color is None
        folder_tint = settings.folder_tint if is_hex_color(settings.folder_tint) else None
        folder_accent = settings.folder_tint_accent is True
        badge_glyph = clean_text(settings.badge_glyph)
        badge_color = settings.badge_color if is_hex_color(settings.badge_color) else None
        folder_mode = "accent" if folder_accent else ("none" if folder_tint is None else "custom")
        return cls(
            folder=Path(entry.folder),
            glyph_name=glyph_name,
            glyph_file=glyph_file,
            glyph_text=glyph_text,
            tint=settings.tint if isinstance(settings.tint, bool) else True,
            color_mode="accent" if accent else ("auto" if color is None else "custom"),
            color=color or (entry.color if is_hex_color(entry.color) else DEFAULT_COLOR),
            size=clean_number(settings.size, MIN_SIZE, MAX_SIZE, 1.0),
            offset_x=clean_number(settings.offset_x, -MAX_OFFSET, MAX_OFFSET, 0.0),
            offset_y=clean_number(settings.offset_y, -MAX_OFFSET, MAX_OFFSET, 0.0),
            folder_tint_mode=folder_mode,
            folder_tint=folder_tint or DEFAULT_FOLDER_TINT,
            badge_on=badge_glyph is not None,
            badge_glyph=badge_glyph,
            badge_color=badge_color or BADGE_DEFAULT_COLOR,
            badge_corner=settings.badge_corner if settings.badge_corner in BADGE_CORNERS else "br",
            badge_size=clean_number(settings.badge_size, BADGE_MIN_SIZE, BADGE_MAX_SIZE, BADGE_DEFAULT_SIZE),
        )
