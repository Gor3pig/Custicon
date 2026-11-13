import atexit
import shutil
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import environment
from .config import APP_ID, APP_NAME, VERSION
from .i18n import _
from .service import Service
from .store import Store
from .ui.window import MainWindow

DEV_ICONS = Path(__file__).resolve().parents[2] / "data" / "icons"
ICON_SIZES = ("16", "24", "32", "48", "64", "96", "128", "256", "512")


def default_service() -> Service:
    return Service(Store(Path(environment.own_data_home() or GLib.get_user_data_dir()) / "custicon"))


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
        self.set_accels_for_action("win.add-folder", ["<Control>o"])
        self.set_accels_for_action("win.regenerate", ["<Control>r"])
        self.set_accels_for_action("win.search", ["<Control>f"])

    def do_activate(self) -> None:
        self._window().present()

    def do_open(self, files, _count, _hint) -> None:
        window = self._window()
        window.present()
        window.open_files(list(files))

    def _window(self) -> MainWindow:
        window = self.props.active_window
        if window is None:
            window = MainWindow(self, self._service or default_service())
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
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            comments=_("Customize the icon of a folder in Nautilus."),
        )
        dialog.present(self.props.active_window)
