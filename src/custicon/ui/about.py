import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..aboutinfo import current_release_notes, help_url
from ..config import APP_ID, APP_NAME, VERSION
from ..i18n import _

WEBSITE = "https://github.com/Gor3pig/Custicon"
ISSUES = "https://github.com/Gor3pig/Custicon/issues"
COPYRIGHT = "© 2026 Gor3pig"
PHOSPHOR_COPYRIGHT = "© 2023 Phosphor Icons"


def build_about_dialog(languages: list[str] | None = None, data_dirs: list[str] | None = None) -> Adw.AboutDialog:
    languages = GLib.get_language_names() if languages is None else languages
    data_dirs = GLib.get_system_data_dirs() if data_dirs is None else data_dirs
    dialog = Adw.AboutDialog(
        application_name=APP_NAME,
        application_icon=APP_ID,
        developer_name="Gor3pig",
        version=VERSION,
        comments=_("Customize the icon of a folder in Nautilus."),
        website=WEBSITE,
        issue_url=ISSUES,
        support_url=help_url(languages),
        license_type=Gtk.License.GPL_3_0,
        copyright=COPYRIGHT,
        translator_credits=_("translator-credits"),
    )
    dialog.add_legal_section("Phosphor Icons", PHOSPHOR_COPYRIGHT, Gtk.License.MIT_X11, None)
    notes = current_release_notes(VERSION, languages, data_dirs)
    if notes:
        dialog.set_release_notes(notes)
        dialog.set_release_notes_version(VERSION)
    return dialog


def open_help(parent: Gtk.Window | None, languages: list[str] | None = None) -> None:
    languages = GLib.get_language_names() if languages is None else languages
    Gtk.UriLauncher.new(help_url(languages)).launch(parent, None, lambda *_args: None)
