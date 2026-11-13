import gettext
import os

DOMAIN = "custicon"
FLATPAK_INFO = "/.flatpak-info"


def locale_dir() -> str:
    explicit = os.environ.get("CUSTICON_LOCALEDIR")
    if explicit:
        return explicit
    return "/app/share/locale" if os.path.exists(FLATPAK_INFO) else "/usr/share/locale"


_translation = gettext.translation(DOMAIN, locale_dir(), fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
