import argparse
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
from .ui.editor import rgba_from_hex

SCREENS = ("main", "empty", "picker", "phosphor", "emoji", "brands", "tint", "about", "states", "report", "dark", "narrow", "confirm", "new", "toast", "batch", "choosing", "darkpicker", "custom")
SETTLE_MS = 1500
RETRIES = 6
DIALOG_DELAY_MS = 600
DIALOG_SETTLE_MS = 3200
DIALOG_SCREENS = ("darkpicker", "picker", "phosphor", "emoji", "brands", "about", "confirm", "report")
SET_SCREENS = {"darkpicker": ("emoji", "chat"), "phosphor": ("phosphor", ""), "emoji": ("emoji", "chat"), "brands": ("brands", "git")}
WIDE = (1000, 720)
NARROW = (400, 720)
FOLDERS = (
    ("Projets", "applications-engineering-symbolic", "#33d17a"),
    ("Musique", "phosphor:music-notes", "#9141ac"),
    ("Photos", "phosphor:camera", "#e66100"),
    ("Archives", "user-bookmarks-symbolic", None),
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
        for name, glyph, tint in FOLDERS:
            folder = self.root / name
            folder.mkdir()
            self.folders[name] = folder
            if populate:
                self.service.apply(folder, Settings(glyph_name=glyph, folder_tint=tint))

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


def show_set(window, key: str, query: str) -> bool:
    window._choose_glyph()
    GLib.timeout_add(DIALOG_DELAY_MS, switch_to_set, window, key, query)
    return False


def switch_to_set(window, key: str, query: str) -> bool:
    dialog = window.get_visible_dialog()
    if dialog is not None:
        dialog.set_toggle.set_active_name(key)
        dialog.search.set_text(query)
    return False


def show_tint(window) -> bool:
    editor = window.editor
    editor.folder_tint_palette.choose_color("#ed5b00")
    return False


def show_batch(window, demo) -> bool:
    batch = [demo.root / name for name in ("Documents", "Téléchargements", "Vidéos")]
    for folder in batch:
        folder.mkdir()
    window.add_folders(batch)
    window.editor.update_glyph(window.editor.draft.with_glyph_name("phosphor:star"))
    return False


def show_choosing(window, demo) -> bool:
    window.choose_toggle.set_active(True)
    for name in ("Musique", "Photos"):
        window._rows[str(demo.folders[name])].check.set_active(True)
    window.editor.update_glyph(window.editor.draft.with_glyph_name("phosphor:star"))
    return False


def show_custom(window) -> bool:
    editor = window.editor
    editor.folder_tint_palette.custom_button.set_rgba(rgba_from_hex("#8e2de2"))
    editor.color_palette.custom_button.set_rgba(rgba_from_hex("#00a58e"))
    return False


def show_about(window) -> bool:
    window.get_application().activate_action("about", None)
    return False


def arrange(screen: str, window, demo: Demo) -> None:
    size = NARROW if screen == "narrow" else WIDE
    window.set_default_size(*size)
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    scheme = Adw.ColorScheme.FORCE_DARK if screen in ("dark", "darkpicker") else Adw.ColorScheme.FORCE_LIGHT
    Adw.StyleManager.get_default().set_color_scheme(scheme)
    if screen in ("states", "report"):
        window.reload(select=str(demo.folders["Archives"]))
    if screen == "main" or screen == "dark" or screen == "toast" or screen == "picker" or screen in SET_SCREENS or screen == "confirm" or screen == "tint" or screen == "about":
        window.reload(select=str(demo.folders["Projets"]))
    if screen == "narrow":
        window.reload(select=str(demo.folders["Projets"]))
        window.split.set_show_content(True)
    if screen == "new":
        new_folder = demo.root / "Vidéos"
        new_folder.mkdir()
        window.add_folder(new_folder)
    if screen == "batch":
        GLib.timeout_add(DIALOG_DELAY_MS, show_batch, window, demo)
    if screen == "choosing":
        window.reload(select=str(demo.folders["Projets"]))
        GLib.timeout_add(DIALOG_DELAY_MS, show_choosing, window, demo)
    if screen == "picker":
        GLib.timeout_add(DIALOG_DELAY_MS, window._choose_glyph)
    if screen in SET_SCREENS:
        key, query = SET_SCREENS[screen]
        GLib.timeout_add(DIALOG_DELAY_MS, show_set, window, key, query)
    if screen == "tint":
        GLib.timeout_add(DIALOG_DELAY_MS, show_tint, window)
    if screen == "custom":
        window.reload(select=str(demo.folders["Projets"]))
        GLib.timeout_add(DIALOG_DELAY_MS, show_custom, window)
    if screen == "about":
        GLib.timeout_add(DIALOG_DELAY_MS, show_about, window)
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


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="custicon --capture", description="Save a screenshot of one screen of the interface.")
    parser.add_argument("--capture", dest="screen", required=True, choices=SCREENS)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    screen, out = arguments.screen, arguments.out
    demo = Demo()
    try:
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
        return app.run([argv[0]])
    finally:
        demo.cleanup()
