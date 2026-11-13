import shutil
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk

from .app import Application
from .metadata import write_custom_icon
from .service import STATUS_FOLDER_MISSING, STATUS_ICON_CHANGED, RegenerationReport, Service, Settings
from .store import Store

SCREENS = ("main", "empty", "picker", "states", "report", "dark", "narrow", "confirm", "new", "toast")
SETTLE_MS = 1500
RETRIES = 6
DIALOG_DELAY_MS = 600
DIALOG_SETTLE_MS = 2600
DIALOG_SCREENS = ("picker", "confirm", "report")
WIDE = (1000, 720)
NARROW = (400, 720)
FOLDERS = (
    ("Projets", "applications-engineering-symbolic"),
    ("Musique", "applications-multimedia-symbolic"),
    ("Photos", "camera-photo-symbolic"),
    ("Archives", "user-bookmarks-symbolic"),
)
USER_ICON = "file:///usr/share/icons/Adwaita/scalable/places/folder-documents.svg"


class Demo:
    def __init__(self):
        base = Path(GLib.get_user_cache_dir()) / "custicon-capture"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        self.root = base / "demo"
        self.root.mkdir()
        self.service = Service(Store(self.root / "data"))
        self.folders: dict[str, Path] = {}

    def create(self, populate: bool = True) -> None:
        for name, glyph in FOLDERS:
            folder = self.root / name
            folder.mkdir()
            self.folders[name] = folder
            if populate:
                self.service.apply(folder, Settings(glyph_name=glyph))

    def break_some(self) -> None:
        self.folders["Musique"].rmdir()
        write_custom_icon(self.folders["Archives"], USER_ICON)

    def cleanup(self) -> None:
        for entry in list(self.service.store.all().values()):
            if Path(entry.folder).is_dir():
                try:
                    write_custom_icon(Path(entry.folder), None)
                except Exception:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)


def arrange(screen: str, window, demo: Demo) -> None:
    size = NARROW if screen == "narrow" else WIDE
    window.set_default_size(*size)
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    scheme = Adw.ColorScheme.FORCE_DARK if screen == "dark" else Adw.ColorScheme.FORCE_LIGHT
    Adw.StyleManager.get_default().set_color_scheme(scheme)
    if screen in ("states", "report"):
        window.reload(select=str(demo.folders["Archives"]))
    if screen == "main" or screen == "dark" or screen == "toast" or screen == "picker" or screen == "confirm":
        window.reload(select=str(demo.folders["Projets"]))
    if screen == "narrow":
        window.reload(select=str(demo.folders["Projets"]))
        window.split.set_show_content(True)
    if screen == "new":
        new_folder = demo.root / "Vidéos"
        new_folder.mkdir()
        window.add_folder(new_folder)
    if screen == "picker":
        GLib.timeout_add(DIALOG_DELAY_MS, window._choose_glyph)
    if screen == "confirm":
        GLib.timeout_add(DIALOG_DELAY_MS, window._confirm_remove)
    if screen == "toast":
        window.toast("Icon applied to “Projets”. Press F5 in Nautilus to see it.", "Got It")
    if screen == "report":
        report = RegenerationReport(
            updated=[str(demo.folders["Projets"]), str(demo.folders["Photos"])],
            skipped={
                str(demo.folders["Archives"]): STATUS_ICON_CHANGED,
                str(demo.folders["Musique"]): STATUS_FOLDER_MISSING,
            },
        )
        GLib.timeout_add(DIALOG_DELAY_MS, window._show_report, report)


def snapshot(window, out: Path) -> bool:
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    recorder = Gtk.Snapshot()
    paintable.snapshot(recorder, width, height)
    node = recorder.to_node()
    if node is None:
        return False
    renderer = Gsk.CairoRenderer.new()
    renderer.realize_for_display(Gdk.Display.get_default())
    viewport = Graphene.Rect()
    viewport.init(0, 0, width, height)
    texture = renderer.render_texture(node, viewport)
    renderer.unrealize()
    texture.save_to_png(str(out))
    return True


def main(argv: list[str]) -> int:
    screen = argv[argv.index("--capture") + 1]
    out = Path(argv[argv.index("--out") + 1])
    if screen not in SCREENS:
        print(f"unknown screen {screen!r}; choose from {', '.join(SCREENS)}", file=sys.stderr)
        return 2
    demo = Demo()
    demo.create(populate=screen != "empty")
    if screen in ("states", "report"):
        demo.break_some()
    app = Application(demo.service, unique=False)

    def on_activate(application) -> None:
        window = application.props.active_window
        arrange(screen, window, demo)

        attempts = [0]

        def finish() -> bool:
            attempts[0] += 1
            if not snapshot(window, out) and attempts[0] < RETRIES:
                return True
            application.quit()
            return False

        GLib.timeout_add(DIALOG_SETTLE_MS if screen in DIALOG_SCREENS else SETTLE_MS, finish)

    app.connect_after("activate", on_activate)
    try:
        return app.run([argv[0]])
    finally:
        demo.cleanup()
