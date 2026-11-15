import configparser
import os

FLATPAK_INFO = "/.flatpak-info"
OWN_DATA_VARIABLE = "CUSTICON_DATA_HOME"
HOST_DATA_VARIABLE = "HOST_XDG_DATA_HOME"
DEFAULT_DATA_HOME = "~/.local/share"
HOME_ACCESS = {"home", "host"}
APP_DATA_FOLDER = ".var"
BACKEND_GVFS = "gvfs"
BACKEND_DIRECTORY = "directory"
DESKTOP_VARIABLE = "XDG_CURRENT_DESKTOP"
GVFS_DESKTOPS = frozenset({"GNOME", "UNITY", "BUDGIE", "CINNAMON", "X-CINNAMON", "MATE"})
DIRECTORY_DESKTOPS = frozenset({"KDE", "LXQT"})
UNSUPPORTED_DESKTOPS = frozenset({"XFCE", "LXDE", "COSMIC"})
FILE_MANAGERS = (
    ("KDE", "Dolphin"),
    ("GNOME", "Nautilus"),
    ("BUDGIE", "Nautilus"),
    ("UNITY", "Nautilus"),
    ("X-CINNAMON", "Nemo"),
    ("CINNAMON", "Nemo"),
    ("MATE", "Caja"),
    ("XFCE", "Thunar"),
    ("LXQT", "PCManFM-Qt"),
    ("LXDE", "PCManFM"),
    ("COSMIC", "COSMIC Files"),
    ("PANTHEON", "Files"),
)
MIME_FILE_MANAGERS = {
    "org.kde.dolphin.desktop": ("Dolphin", BACKEND_DIRECTORY),
    "pcmanfm-qt.desktop": ("PCManFM-Qt", BACKEND_DIRECTORY),
    "org.gnome.nautilus.desktop": ("Nautilus", BACKEND_GVFS),
    "nemo.desktop": ("Nemo", BACKEND_GVFS),
    "caja.desktop": ("Caja", BACKEND_GVFS),
    "caja-browser.desktop": ("Caja", BACKEND_GVFS),
}
MIME_FILES = ("~/.config/mimeapps.list", "~/.local/share/applications/mimeapps.list")
DIRECTORY_MIME_TYPE = "inode/directory"
FLATPAK_COMMAND = "flatpak override --user --filesystem=home io.github.gor3pig.Custicon"


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


def grants_home(entry: str, writable: bool = False) -> bool:
    name, _separator, mode = entry.strip().partition(":")
    if writable and mode == "ro":
        return False
    if name in HOME_ACCESS or name == "~":
        return True
    return bool(name) and os.path.normpath(name) == os.path.normpath(os.path.expanduser("~"))


def home_is_visible() -> bool:
    try:
        names = os.listdir(os.path.expanduser("~"))
    except OSError:
        return False
    return any(name.startswith(".") and name != APP_DATA_FOLDER for name in names)


def home_access() -> bool:
    if not in_sandbox():
        return True
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(FLATPAK_INFO, encoding="utf-8")
        entries = parser.get("Context", "filesystems", fallback=None)
    except (OSError, ValueError, configparser.Error):
        return True
    writable = detect_backend() == BACKEND_DIRECTORY
    if entries is not None and any(grants_home(entry, writable) for entry in entries.split(";")):
        return True
    if writable and entries is not None and any(grants_home(entry) for entry in entries.split(";")):
        return False
    return home_is_visible()


def current_desktops(environ=None) -> tuple[str, ...]:
    value = (os.environ if environ is None else environ).get(DESKTOP_VARIABLE, "")
    return tuple(name.strip().upper() for name in value.split(":") if name.strip())


_kde_portal: list = []


def kde_portal_present() -> bool:
    if not _kde_portal:
        from . import themes

        _kde_portal.append(themes.portal_has_kde())
    return _kde_portal[0]


def is_kde(environ=None, probe=None) -> bool:
    desktops = current_desktops(environ)
    if desktops:
        return "KDE" in desktops and not any(name in GVFS_DESKTOPS for name in desktops)
    return (probe or kde_portal_present)()


def default_file_manager() -> tuple[str, str] | None:
    for path in MIME_FILES:
        parser = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
        try:
            parser.read(os.path.expanduser(path), encoding="utf-8")
            value = parser.get("Default Applications", DIRECTORY_MIME_TYPE, fallback="")
        except (OSError, ValueError, configparser.Error):
            continue
        first = next((name.strip().lower() for name in value.split(";") if name.strip()), None)
        if first:
            return MIME_FILE_MANAGERS.get(first)
    return None


def detect_backend(environ=None, probe=None, lookup=None) -> str:
    desktops = current_desktops(environ)
    if any(name in GVFS_DESKTOPS for name in desktops):
        return BACKEND_GVFS
    if any(name in DIRECTORY_DESKTOPS for name in desktops):
        return BACKEND_DIRECTORY
    if not desktops and is_kde(environ, probe):
        return BACKEND_DIRECTORY
    found = (lookup or default_file_manager)()
    return found[1] if found else BACKEND_GVFS


def file_manager_name(environ=None, probe=None, lookup=None) -> str | None:
    desktops = current_desktops(environ)
    if not desktops and is_kde(environ, probe):
        return "Dolphin"
    if not any(name in GVFS_DESKTOPS or name in DIRECTORY_DESKTOPS for name in desktops):
        found = (lookup or default_file_manager)()
        if found:
            return found[0]
    if not desktops:
        return None
    for desktop, manager in FILE_MANAGERS:
        if desktop in desktops:
            return manager
    return None


def unsupported_file_manager(environ=None, lookup=None) -> str | None:
    desktops = current_desktops(environ)
    if any(name in GVFS_DESKTOPS or name in DIRECTORY_DESKTOPS for name in desktops):
        return None
    if (lookup or default_file_manager)():
        return None
    if not any(name in UNSUPPORTED_DESKTOPS for name in desktops):
        return None
    return file_manager_name(environ)
