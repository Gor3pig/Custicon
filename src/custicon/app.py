import atexit
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import environment
from .config import APP_ID, APP_NAME
from .i18n import _
from .service import Service
from .store import Store
from .ui.about import build_about_dialog, open_help
from .ui.window import MainWindow

DEV_ICONS = Path(__file__).resolve().parents[2] / "data" / "icons"
ICON_SIZES = ("16", "24", "32", "48", "64", "96", "128", "256", "512")


def default_service() -> Service:
    return Service(Store(Path(environment.own_data_home() or GLib.get_user_data_dir()) / "custicon"), backend=environment.detect_backend())


def register_development_icons() -> None:
    display = Gdk.Display.get_default()
    if display is None or not DEV_ICONS.is_dir():
        return
    root = Path(tempfile.mkdtemp(prefix="custicon-icons-"))
    atexit.register(shutil.rmtree, root, True)
    for size in ICON_SIZES:
        source = DEV_ICONS / f"custicon-{size}x{size}.png"
        if source.is_file():
            target = root / "hicolor" / f"{size}x{size}" / "apps"
            target.mkdir(parents=True)
            (target / f"{APP_ID}.png").symlink_to(source)
    Gtk.IconTheme.get_for_display(display).add_search_path(str(root))


class StartupErrorWindow(Adw.ApplicationWindow):
    def __init__(self, application, error: BaseException):
        super().__init__(application=application, title=APP_NAME, default_width=520, default_height=420)
        detail = GLib.markup_escape_text(f"{type(error).__name__}: {error}")
        self.description = _("An unexpected error stopped the window from opening. Your folders and icons are not changed.") + "\n\n" + detail
        page = Adw.StatusPage(icon_name="dialog-error-symbolic", title=_("Custicon Could Not Start"), description=self.description)
        quit_button = Gtk.Button(label=_("Quit"), halign=Gtk.Align.CENTER)
        quit_button.add_css_class("pill")
        quit_button.connect("clicked", lambda *_args: application.quit())
        page.set_child(quit_button)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        self.set_content(view)


class Application(Adw.Application):
    def __init__(self, service: Service | None = None, unique: bool = True):
        flags = Gio.ApplicationFlags.HANDLES_OPEN if unique else Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN
        super().__init__(application_id=APP_ID, flags=flags)
        self._service = service

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        register_development_icons()
        self._add_action("quit", self._on_quit, ["<Control>q"])
        self._add_action("about", self._on_about)
        self._add_action("help", self._on_help, ["F1"])
        self.set_accels_for_action("win.add-folder", ["<Control>o"])
        self.set_accels_for_action("win.regenerate", ["<Control>r"])
        self.set_accels_for_action("win.search", ["<Control>f"])

    def do_activate(self) -> None:
        self._window().present()

    def do_open(self, files, _count, _hint) -> None:
        window = self._window()
        window.present()
        if not isinstance(window, StartupErrorWindow):
            window.open_files(list(files))

    def _window(self) -> Adw.ApplicationWindow:
        window = self.props.active_window
        if window is None:
            try:
                window = MainWindow(self, self._service or default_service())
            except Exception as error:
                traceback.print_exc(file=sys.stderr)
                for leftover in list(self.get_windows()):
                    leftover.destroy()
                window = StartupErrorWindow(self, error)
        return window

    def _add_action(self, name: str, callback, accels: list[str] | None = None) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def _on_quit(self, *_args) -> None:
        self.quit()

    def _on_about(self, *_args) -> None:
        build_about_dialog().present(self.props.active_window)

    def _on_help(self, *_args) -> None:
        open_help(self.props.active_window)
