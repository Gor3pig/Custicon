from .. import environment
from ..i18n import _


def manager_name() -> str:
    return environment.file_manager_name() or _("your file manager")
