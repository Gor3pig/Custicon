import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..aboutinfo import current_release_notes, help_url, website_url
from ..config import APP_ID, APP_NAME, VERSION
from ..iconsets import PACKAGE_DIRECTORY
from ..i18n import _

REPOSITORY = "https://github.com/Gor3pig/Custicon"
ISSUES = f"{REPOSITORY}/issues"
COPYRIGHT = "© 2026 Gor3pig"
PHOSPHOR_COPYRIGHT = "© 2023 Phosphor Icons"
FLUENT_COPYRIGHT = "© Microsoft Corporation"
CLDR_LICENSE = PACKAGE_DIRECTORY / "emoji" / "LICENSE-CLDR"
CLDR_FALLBACK_COPYRIGHT = "© Unicode, Inc."


def cldr_section() -> tuple[str, str, Gtk.License, str]:
    try:
        text = CLDR_LICENSE.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    lines = text.splitlines()
    copyright_line = lines[0].replace("Copyright ", "") if lines and lines[0].startswith("Copyright") else CLDR_FALLBACK_COPYRIGHT
    body = text or "UNICODE LICENSE V3\n\nhttps://www.unicode.org/license.txt"
    return "Unicode CLDR", copyright_line, Gtk.License.CUSTOM, body


def legal_sections() -> list[tuple[str, str, Gtk.License, str | None]]:
    brands_text = _(
        "The logos are released under CC0 1.0 Universal (public domain). The brands and logos remain the trademarks of their respective owners, and their use is subject to the guidelines of those owners."
    )
    return [
        ("Phosphor Icons", PHOSPHOR_COPYRIGHT, Gtk.License.MIT_X11, None),
        ("Fluent Emoji", FLUENT_COPYRIGHT, Gtk.License.MIT_X11, None),
        ("Simple Icons", "CC0 1.0", Gtk.License.CUSTOM, brands_text),
        cldr_section(),
    ]


def build_about_dialog(languages: list[str] | None = None, data_dirs: list[str] | None = None) -> Adw.AboutDialog:
    languages = GLib.get_language_names() if languages is None else languages
    data_dirs = GLib.get_system_data_dirs() if data_dirs is None else data_dirs
    dialog = Adw.AboutDialog(
        application_name=APP_NAME,
        application_icon=APP_ID,
        developer_name="Gor3pig",
        version=VERSION,
        comments=_("Customize the icon of a folder in Nautilus."),
        website=website_url(languages),
        issue_url=ISSUES,
        support_url=help_url(languages),
        license_type=Gtk.License.GPL_3_0,
        copyright=COPYRIGHT,
        translator_credits=_("translator-credits"),
    )
    for title, copyright_line, kind, text in legal_sections():
        dialog.add_legal_section(title, copyright_line, kind, text)
    notes = current_release_notes(VERSION, languages, data_dirs)
    if notes:
        dialog.set_release_notes(notes)
        dialog.set_release_notes_version(VERSION)
    return dialog


def open_help(parent: Gtk.Window | None, languages: list[str] | None = None) -> None:
    languages = GLib.get_language_names() if languages is None else languages
    Gtk.UriLauncher.new(help_url(languages)).launch(parent, None, lambda *_args: None)
