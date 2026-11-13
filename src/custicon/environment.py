import configparser
import os

FLATPAK_INFO = "/.flatpak-info"
OWN_DATA_VARIABLE = "CUSTICON_DATA_HOME"
HOST_DATA_VARIABLE = "HOST_XDG_DATA_HOME"
DEFAULT_DATA_HOME = "~/.local/share"
HOME_ACCESS = {"home", "host"}
FLATPAK_COMMAND = "flatpak override --user --filesystem=home:ro io.github.gor3pig.Custicon"


def in_sandbox() -> bool:
    return os.path.exists(FLATPAK_INFO)


def host_data_home() -> str:
    value = os.environ.get(HOST_DATA_VARIABLE)
    if value and os.path.isabs(value):
        return value
    return os.path.expanduser(DEFAULT_DATA_HOME)


def share_host_metadata() -> None:
    if not in_sandbox() or OWN_DATA_VARIABLE in os.environ:
        return
    os.environ[OWN_DATA_VARIABLE] = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(DEFAULT_DATA_HOME)
    os.environ["XDG_DATA_HOME"] = host_data_home()


def own_data_home() -> str | None:
    return os.environ.get(OWN_DATA_VARIABLE)


def home_access() -> bool:
    if not in_sandbox():
        return True
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(FLATPAK_INFO, encoding="utf-8")
        entries = parser.get("Context", "filesystems", fallback=None)
    except (OSError, ValueError, configparser.Error):
        return True
    if entries is None:
        return False
    return any(entry.strip().partition(":")[0] in HOME_ACCESS for entry in entries.split(";"))
