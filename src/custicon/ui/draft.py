from dataclasses import dataclass, replace
from pathlib import Path

from ..service import Settings
from ..store import Entry

DEFAULT_COLOR = "#3584e4"


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
        return cls(
            folder=Path(entry.folder),
            glyph_name=settings.glyph_name,
            glyph_file=settings.glyph_file,
            tint=settings.tint,
            auto_color=settings.color is None,
            color=settings.color or entry.color,
            size=settings.size,
            offset_x=settings.offset_x,
            offset_y=settings.offset_y,
        )
