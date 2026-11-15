import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import MetadataError

FILE_NAME = ".directory"
SECTION = "[Desktop Entry]"
KEY = "Icon"
MAX_BYTES = 1024 * 1024
BOM = "\ufeff"
RETRIES = 3


@dataclass(frozen=True)
class Edit:
    text: str
    header_added: bool
    changed: bool


def escape(value: str) -> str:
    result = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    if result.startswith(" "):
        result = "\\s" + result[1:]
    if result.endswith(" "):
        result = result[:-1] + "\\s"
    return result


def unescape(value: str) -> str:
    result = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            following = value[index + 1]
            replacement = {"s": " ", "n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(following)
            if replacement is not None:
                result.append(replacement)
                index += 2
                continue
        result.append(character)
        index += 1
    return "".join(result)


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def split_lines(text: str) -> list[str]:
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def is_header(line: str) -> bool:
    return line.strip(" \t\r\n") == SECTION


def is_other_header(line: str) -> bool:
    stripped = line.strip(" \t\r\n")
    return stripped.startswith("[") and stripped.endswith("]") and stripped != SECTION


def is_icon_line(line: str) -> bool:
    stripped = line.lstrip(" \t")
    if not stripped.startswith(KEY):
        return False
    rest = stripped[len(KEY):].lstrip(" \t")
    return rest.startswith("=")


def section_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = next((index for index, line in enumerate(lines) if is_header(line)), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if is_other_header(lines[index]):
            end = index
            break
    return start, end


def icon_value(text: str) -> str | None:
    lines = split_lines(text.removeprefix(BOM))
    bounds = section_bounds(lines)
    if bounds is None:
        return None
    found = None
    for line in lines[bounds[0] + 1 : bounds[1]]:
        if is_icon_line(line):
            found = unescape(line.split("=", 1)[1].strip(" \t\r\n"))
    return found or None


def with_icon(text: str, value: str) -> Edit:
    prefix = BOM if text.startswith(BOM) else ""
    body = text.removeprefix(BOM)
    newline = newline_of(body)
    lines = split_lines(body)
    wanted = f"{KEY}={escape(value)}"
    bounds = section_bounds(lines)
    if bounds is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        if lines and lines[-1].strip(" \t\r\n"):
            lines.append(newline)
        lines.append(SECTION + newline)
        lines.append(wanted + newline)
        return Edit(prefix + "".join(lines), True, True)
    start, end = bounds
    indexes = [index for index in range(start + 1, end) if is_icon_line(lines[index])]
    if indexes:
        changed = False
        for index in indexes:
            ending = lines[index][len(lines[index].rstrip("\r\n")) :]
            replacement = wanted + (ending or newline)
            if lines[index] != replacement:
                changed = True
            lines[index] = replacement
        return Edit(prefix + "".join(lines), False, changed)
    if not lines[start].endswith(("\n", "\r")):
        lines[start] += newline
    lines.insert(start + 1, wanted + newline)
    return Edit(prefix + "".join(lines), False, True)


def without_icon(text: str) -> Edit:
    prefix = BOM if text.startswith(BOM) else ""
    body = text.removeprefix(BOM)
    lines = split_lines(body)
    bounds = section_bounds(lines)
    if bounds is None:
        return Edit(text, False, False)
    start, end = bounds
    kept = [line for index, line in enumerate(lines) if not (start < index < end and is_icon_line(line))]
    if len(kept) == len(lines):
        return Edit(text, False, False)
    new_bounds = section_bounds(kept)
    if new_bounds is not None and all(not line.strip(" \t\r\n") for line in kept[new_bounds[0] + 1 : new_bounds[1]]):
        first, last = new_bounds
        at_end = last == len(kept)
        del kept[first:last]
        if at_end and first > 0 and not kept[first - 1].strip(" \t\r\n"):
            del kept[first - 1]
    return Edit(prefix + "".join(kept), False, True)


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def encode(text: str) -> bytes:
    return text.encode("utf-8", errors="surrogateescape")


def file_of(folder: Path | str) -> Path:
    return Path(folder) / FILE_NAME


def read_text(path: Path) -> str | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MetadataError(f"cannot read {path.name}: {error.strerror}") from error
    if not stat.S_ISREG(info.st_mode):
        raise MetadataError(f"{path.name} is not a regular file")
    if info.st_size > MAX_BYTES:
        raise MetadataError(f"{path.name} is too large to be edited safely")
    try:
        return decode(path.read_bytes())
    except OSError as error:
        raise MetadataError(f"cannot read {path.name}: {error.strerror}") from error


def to_uri(value: str) -> str:
    if value.startswith("/"):
        return Path(value).as_uri()
    return value


def to_value(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return uri


def read_icon(folder: Path | str) -> str | None:
    text = read_text(file_of(folder))
    if text is None:
        return None
    value = icon_value(text)
    return None if value is None else to_uri(value)


def write_atomic(path: Path, text: str) -> None:
    directory = path.parent
    try:
        existing = path.lstat()
        mode = stat.S_IMODE(existing.st_mode)
    except FileNotFoundError:
        mode = 0o644
    except OSError as error:
        raise MetadataError(f"cannot write {path.name}: {error.strerror}") from error
    temporary = None
    try:
        handle, name = tempfile.mkstemp(prefix=FILE_NAME + ".", suffix=".tmp", dir=directory)
        temporary = Path(name)
        with os.fdopen(handle, "wb") as stream:
            stream.write(encode(text))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise MetadataError(f"cannot write {path.name}: {error.strerror}") from error


@dataclass(frozen=True)
class Written:
    created_file: bool
    removed_file: bool


def write_icon(folder: Path | str, value: str | None, created_by_us: bool = False) -> Written:
    path = file_of(folder)
    for _attempt in range(RETRIES):
        before = read_text(path)
        if value is None:
            if before is None:
                return Written(False, False)
            edit = without_icon(before)
            if not edit.changed:
                return Written(False, False)
            remove_file = created_by_us and not edit.text.strip(" \t\r\n" + BOM)
            if read_text(path) != before:
                continue
            if remove_file:
                try:
                    path.unlink()
                except OSError as error:
                    raise MetadataError(f"cannot remove {path.name}: {error.strerror}") from error
                return Written(False, True)
            write_atomic(path, edit.text)
        else:
            plain = to_value(value)
            edit = with_icon(before or "", plain)
            if not edit.changed and before is not None:
                return Written(False, False)
            if read_text(path) != before:
                continue
            write_atomic(path, edit.text)
        stored = read_icon(folder)
        expected = None if value is None else to_uri(to_value(value))
        if stored != expected:
            raise MetadataError(f"the value was not stored for {folder}")
        return Written(before is None and value is not None, False)
    raise MetadataError(f"{FILE_NAME} changed while it was being edited")
