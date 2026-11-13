from pathlib import Path

from ..i18n import _

PERCENT_STEP = 100


def display_path(path: Path | str, home: Path | None = None) -> str:
    target = Path(path)
    base = Path.home() if home is None else home
    try:
        relative = target.relative_to(base)
    except ValueError:
        return str(target)
    return "~" if str(relative) == "." else f"~/{relative}"


def folder_name(path: Path | str) -> str:
    target = Path(path)
    return target.name or str(target)


def percent_text(value: float) -> str:
    return _("%d%%") % round(value * PERCENT_STEP)


def to_percent(value: float) -> float:
    return value * PERCENT_STEP


def from_percent(value: float) -> float:
    return value / PERCENT_STEP


def is_portal_path(path: Path | str) -> bool:
    text = str(path)
    return text.startswith("/run/user/") and "/doc/" in text
