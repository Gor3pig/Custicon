from ..errors import (
    CusticonError,
    FolderError,
    IconNotFoundError,
    IconSetError,
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
    BackupSummary,
    RegenerationReport,
    ResetReport,
    RestoreReport,
)


def describe_error(error: Exception) -> str:
    summaries = (
        (IconNotFoundError, _("An icon that is needed was not found in your icon theme.")),
        (InvalidIconError, _("An icon file could not be read.")),
        (InvalidSettingsError, _("These settings are not valid.")),
        (FolderError, _("This is not a usable folder.")),
        (MetadataError, _("The new icon could not be recorded for this folder. Check that Custicon can access it.")),
        (StoreError, _("Custicon could not save its data.")),
        (IconSetError, _("This icon set could not be used.")),
    )
    for kind, summary in summaries:
        if isinstance(error, kind):
            return f"{summary}\n\n{error}"
    return str(error) if isinstance(error, CusticonError) else _("An unexpected error occurred.")


def status_label(status: str) -> str | None:
    return {
        STATUS_FOLDER_MISSING: _("Folder not found"),
        STATUS_ICON_CHANGED: _("Icon changed outside Custicon"),
        STATUS_ICON_FILE_MISSING: _("Icon file missing"),
        STATUS_UNREADABLE: _("Metadata unreadable"),
    }.get(status)


def status_banner(status: str) -> str | None:
    return {
        STATUS_FOLDER_MISSING: _("This folder no longer exists."),
        STATUS_ICON_CHANGED: _("This icon was replaced outside Custicon: Custicon no longer touches it."),
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
        lines.append(ngettext("%d icon kept because you changed it outside Custicon", "%d icons kept because you changed them outside Custicon", count) % count)
    if report.missing:
        count = len(report.missing)
        lines.append(ngettext("%d folder not found and forgotten", "%d folders not found and forgotten", count) % count)
    if report.failed:
        count = len(report.failed)
        lines.append(ngettext("%d icon could not be removed", "%d icons could not be removed", count) % count)
    return lines


def backup_summary_lines(summary: BackupSummary) -> list[str]:
    return [
        ngettext("%d customized folder", "%d customized folders", summary.folders) % summary.folders,
        ngettext("%d named style", "%d named styles", summary.styles) % summary.styles,
        ngettext("%d recent or favorite pictogram", "%d recent or favorite pictograms", summary.pictograms) % summary.pictograms,
    ]


def restore_report_lines(report: RestoreReport) -> list[str]:
    lines = []
    if report.restored:
        count = len(report.restored)
        lines.append(ngettext("%d icon restored", "%d icons restored", count) % count)
    if report.missing:
        count = len(report.missing)
        lines.append(ngettext("%d folder not found, ignored", "%d folders not found, ignored", count) % count)
    if report.kept:
        count = len(report.kept)
        lines.append(ngettext("%d folder whose icon was changed elsewhere, left as it is", "%d folders whose icon was changed elsewhere, left as they are", count) % count)
    if report.failed:
        count = len(report.failed)
        lines.append(ngettext("%d icon could not be restored", "%d icons could not be restored", count) % count)
    if report.styles_added:
        count = report.styles_added
        lines.append(ngettext("%d style added", "%d styles added", count) % count)
    if report.styles_rejected:
        count = report.styles_rejected
        lines.append(ngettext("%d style ignored because it is not valid", "%d styles ignored because they are not valid", count) % count)
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
            ngettext("%d folder skipped: icon changed outside Custicon", "%d folders skipped: icon changed outside Custicon", count)
            % count
        )
    if missing:
        count = len(missing)
        lines.append(ngettext("%d folder not found", "%d folders not found", count) % count)
    if report.failed:
        count = len(report.failed)
        lines.append(ngettext("%d folder could not be updated", "%d folders could not be updated", count) % count)
    return lines
