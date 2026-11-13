from ..errors import (
    CusticonError,
    FolderError,
    IconNotFoundError,
    InvalidIconError,
    InvalidSettingsError,
    MetadataError,
    StoreError,
)
from ..i18n import _, ngettext
from ..service import (
    STATUS_FOLDER_MISSING,
    STATUS_ICON_CHANGED,
    STATUS_ICON_FILE_MISSING,
    STATUS_UNREADABLE,
    RegenerationReport,
    ResetReport,
)


def describe_error(error: Exception) -> str:
    summaries = (
        (IconNotFoundError, _("An icon that is needed was not found in your icon theme.")),
        (InvalidIconError, _("An icon file could not be read.")),
        (InvalidSettingsError, _("These settings are not valid.")),
        (FolderError, _("This is not a usable folder.")),
        (MetadataError, _("Nautilus could not be told about the new icon. Check that Custicon can access this folder.")),
        (StoreError, _("Custicon could not save its data.")),
    )
    for kind, summary in summaries:
        if isinstance(error, kind):
            return f"{summary}\n\n{error}"
    return str(error) if isinstance(error, CusticonError) else _("An unexpected error occurred.")


def status_label(status: str) -> str | None:
    return {
        STATUS_FOLDER_MISSING: _("Folder not found"),
        STATUS_ICON_CHANGED: _("Icon changed from Nautilus"),
        STATUS_ICON_FILE_MISSING: _("Icon file missing"),
        STATUS_UNREADABLE: _("Metadata unreadable"),
    }.get(status)


def status_banner(status: str) -> str | None:
    return {
        STATUS_FOLDER_MISSING: _("This folder no longer exists."),
        STATUS_ICON_CHANGED: _("This icon was replaced from Nautilus: Custicon no longer touches it."),
        STATUS_ICON_FILE_MISSING: _("The icon file of this folder is missing."),
        STATUS_UNREADABLE: _("The metadata of this folder cannot be read."),
    }.get(status)


def banner_action(status: str) -> str | None:
    return {
        STATUS_ICON_CHANGED: _("Take Back the Custicon Icon"),
        STATUS_ICON_FILE_MISSING: _("Regenerate the Icon"),
        STATUS_FOLDER_MISSING: _("Find the Folder…"),
    }.get(status)


def reset_report_lines(report: ResetReport) -> list[str]:
    lines = []
    if report.restored:
        count = len(report.restored)
        lines.append(ngettext("%d icon removed", "%d icons removed", count) % count)
    if report.kept:
        count = len(report.kept)
        lines.append(ngettext("%d icon kept because you changed it from Nautilus", "%d icons kept because you changed them from Nautilus", count) % count)
    if report.missing:
        count = len(report.missing)
        lines.append(ngettext("%d folder not found and forgotten", "%d folders not found and forgotten", count) % count)
    if report.failed:
        count = len(report.failed)
        lines.append(ngettext("%d icon could not be removed", "%d icons could not be removed", count) % count)
    return lines


def report_lines(report: RegenerationReport) -> list[str]:
    changed = [
        status for status in report.skipped.values() if status == STATUS_ICON_CHANGED
    ]
    missing = [status for status in report.skipped.values() if status == STATUS_FOLDER_MISSING]
    lines = []
    if report.updated:
        count = len(report.updated)
        lines.append(ngettext("%d icon updated", "%d icons updated", count) % count)
    if report.unchanged:
        count = len(report.unchanged)
        lines.append(ngettext("%d icon already up to date", "%d icons already up to date", count) % count)
    if changed:
        count = len(changed)
        lines.append(
            ngettext("%d folder skipped: icon changed from Nautilus", "%d folders skipped: icon changed from Nautilus", count)
            % count
        )
    if missing:
        count = len(missing)
        lines.append(ngettext("%d folder not found", "%d folders not found", count) % count)
    if report.failed:
        count = len(report.failed)
        lines.append(ngettext("%d folder could not be updated", "%d folders could not be updated", count) % count)
    return lines
