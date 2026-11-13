import unittest
from unittest import mock

from gi.repository import GLib

from custicon import themes
from custicon.errors import IconNotFoundError
from custicon.themes import IconTheme, active_icon_theme, host_icon_dirs

from .support import WorkspaceTestCase, folder_svg, glyph_svg, make_theme, solid_png


class IconThemeTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()

    def test_finds_the_folder_file_of_the_theme(self):
        theme_dir = make_theme(self.root, "Own", folder=folder_svg())
        found = IconTheme("Own", [str(self.root)]).folder_file()
        self.assertEqual(found, theme_dir / "scalable" / "places" / "folder.svg")

    def test_resolves_the_folder_through_inheritance(self):
        base = make_theme(self.root, "Base", folder=folder_svg("#aa0000"))
        make_theme(self.root, "Child", inherits="Base")
        found = IconTheme("Child", [str(self.root)]).folder_file()
        self.assertEqual(found, base / "scalable" / "places" / "folder.svg")

    def test_own_folder_wins_over_the_parent_one(self):
        make_theme(self.root, "Base", folder=folder_svg("#aa0000"))
        child = make_theme(self.root, "Child", folder=folder_svg("#00aa00"), inherits="Base")
        found = IconTheme("Child", [str(self.root)]).folder_file()
        self.assertEqual(found, child / "scalable" / "places" / "folder.svg")

    def test_raster_only_theme_returns_its_png(self):
        make_theme(self.root, "Base", folder=folder_svg())
        child = make_theme(self.root, "Raster", folder_png=solid_png((0.2, 0.6, 0.3)), inherits="Base")
        found = IconTheme("Raster", [str(self.root)]).folder_file()
        self.assertEqual(found, child / "48x48" / "places" / "folder.png")

    def test_missing_folder_raises(self):
        make_theme(self.root, "Empty")
        with self.assertRaises(IconNotFoundError):
            IconTheme("Empty", [str(self.root)]).folder_file()

    def test_glyph_file_is_found_by_name(self):
        theme_dir = make_theme(self.root, "WithGlyph", glyphs={"star-symbolic": glyph_svg()})
        found = IconTheme("WithGlyph", [str(self.root)]).glyph_file("star-symbolic")
        self.assertEqual(found, theme_dir / "symbolic" / "status" / "star-symbolic.svg")

    def test_unknown_glyph_raises(self):
        make_theme(self.root, "WithGlyph", glyphs={"star-symbolic": glyph_svg()})
        with self.assertRaises(IconNotFoundError):
            IconTheme("WithGlyph", [str(self.root)]).glyph_file("nothing-symbolic")

    def test_symbolic_names_are_filtered_by_the_query(self):
        make_theme(
            self.root,
            "Names",
            glyphs={"star-symbolic": glyph_svg(), "starred-symbolic": glyph_svg(), "music-symbolic": glyph_svg()},
        )
        theme = IconTheme("Names", [str(self.root)])
        self.assertEqual(theme.symbolic_names("star"), ["star-symbolic", "starred-symbolic"])
        self.assertEqual(theme.symbolic_names("MUSIC"), ["music-symbolic"])
        self.assertEqual(theme.symbolic_names("zzz"), [])

    def test_symbolic_names_respect_the_limit(self):
        make_theme(
            self.root,
            "Many",
            glyphs={f"item{index}-symbolic": glyph_svg() for index in range(5)},
        )
        names = IconTheme("Many", [str(self.root)]).symbolic_names("item", limit=3)
        self.assertEqual(names, ["item0-symbolic", "item1-symbolic", "item2-symbolic"])

    def test_symbolic_names_skip_built_in_icons_without_a_file(self):
        make_theme(self.root, "Own", glyphs={"star-symbolic": glyph_svg()})
        names = IconTheme("Own", [str(self.root)]).symbolic_names("start")
        self.assertEqual(names, [])

    def test_symbolic_names_ignore_non_symbolic_icons(self):
        make_theme(self.root, "Mixed", folder=folder_svg(), glyphs={"star-symbolic": glyph_svg()})
        self.assertNotIn("folder", IconTheme("Mixed", [str(self.root)]).symbolic_names(""))


class HostDirectoriesTests(unittest.TestCase):
    def test_sandbox_uses_only_the_host_directories(self):
        with mock.patch.object(themes, "in_sandbox", return_value=True):
            self.assertEqual(host_icon_dirs(), list(themes.SANDBOX_ICON_DIRS))

    def test_outside_sandbox_never_uses_the_host_mount(self):
        with mock.patch.object(themes, "in_sandbox", return_value=False):
            self.assertFalse(set(host_icon_dirs()) & set(themes.SANDBOX_ICON_DIRS))


class ActiveThemeTests(unittest.TestCase):
    def test_the_portal_value_wins(self):
        with mock.patch.object(themes, "portal_icon_theme", return_value="Papirus"), mock.patch.object(
            themes, "settings_icon_theme", return_value="Other"
        ):
            self.assertEqual(active_icon_theme(), "Papirus")

    def test_falls_back_to_settings(self):
        with mock.patch.object(themes, "portal_icon_theme", return_value=None), mock.patch.object(
            themes, "settings_icon_theme", return_value="FromSettings"
        ):
            self.assertEqual(active_icon_theme(), "FromSettings")

    def test_falls_back_to_the_default_theme(self):
        with mock.patch.object(themes, "portal_icon_theme", return_value=None), mock.patch.object(
            themes, "settings_icon_theme", return_value=None
        ):
            self.assertEqual(active_icon_theme(), themes.DEFAULT_THEME)


def portal_reply(inner: GLib.Variant) -> GLib.Variant:
    return GLib.Variant("(v)", (inner,))


class PortalThemeTests(unittest.TestCase):
    def ask(self, reply=None, error=None):
        bus = mock.Mock()
        if error is not None:
            bus.call_sync.side_effect = error
        else:
            bus.call_sync.return_value = reply
        with mock.patch.object(themes.Gio, "bus_get_sync", return_value=bus):
            return themes.portal_icon_theme(), bus

    def test_the_theme_name_is_read_from_the_portal_reply(self):
        name, bus = self.ask(portal_reply(GLib.Variant("s", "Papirus")))
        self.assertEqual(name, "Papirus")
        arguments = bus.call_sync.call_args.args
        self.assertEqual(arguments[0:4], (themes.PORTAL_NAME, themes.PORTAL_PATH, themes.PORTAL_SETTINGS, "Read"))
        self.assertEqual(arguments[4].unpack(), (themes.INTERFACE_SCHEMA, themes.ICON_THEME_KEY))

    def test_a_doubly_boxed_reply_is_unwrapped(self):
        name, _bus = self.ask(portal_reply(GLib.Variant("v", GLib.Variant("s", "Adwaita"))))
        self.assertEqual(name, "Adwaita")

    def test_a_reply_that_is_not_a_string_is_ignored(self):
        name, _bus = self.ask(portal_reply(GLib.Variant("i", 3)))
        self.assertIsNone(name)

    def test_a_portal_failure_gives_no_answer(self):
        name, _bus = self.ask(error=GLib.Error("no portal"))
        self.assertIsNone(name)


class PortalAccentTests(unittest.TestCase):
    def ask(self, reply=None, error=None):
        bus = mock.Mock()
        if error is not None:
            bus.call_sync.side_effect = error
        else:
            bus.call_sync.return_value = reply
        with mock.patch.object(themes.Gio, "bus_get_sync", return_value=bus):
            return themes.portal_accent_color(), bus

    def test_the_accent_is_read_as_a_hex_color(self):
        color, bus = self.ask(portal_reply(GLib.Variant("(ddd)", (1.0, 0.0, 0.5))))
        self.assertEqual(color, "#ff0080")
        self.assertEqual(bus.call_sync.call_args.args[4].unpack(), ("org.freedesktop.appearance", "accent-color"))

    def test_a_doubly_boxed_accent_is_unwrapped(self):
        color, _bus = self.ask(portal_reply(GLib.Variant("v", GLib.Variant("(ddd)", (0.0, 1.0, 0.0)))))
        self.assertEqual(color, "#00ff00")

    def test_an_out_of_range_accent_means_no_accent(self):
        for channels in ((-1.0, 0.0, 0.0), (0.0, 1.5, 0.0), (0.0, 0.0, float("nan"))):
            color, _bus = self.ask(portal_reply(GLib.Variant("(ddd)", channels)))
            self.assertIsNone(color, channels)

    def test_a_reply_of_another_type_is_ignored(self):
        color, _bus = self.ask(portal_reply(GLib.Variant("s", "blue")))
        self.assertIsNone(color)

    def test_a_portal_failure_gives_no_accent(self):
        color, _bus = self.ask(error=GLib.Error("no portal"))
        self.assertIsNone(color)

    def test_the_system_accent_falls_back_to_the_default_blue(self):
        with mock.patch.object(themes, "portal_accent_color", return_value=None):
            self.assertEqual(themes.system_accent_color(), themes.DEFAULT_ACCENT)
        with mock.patch.object(themes, "portal_accent_color", return_value="#112233"):
            self.assertEqual(themes.system_accent_color(), "#112233")


class SettingsThemeTests(unittest.TestCase):
    def test_without_the_schema_there_is_no_answer(self):
        source = mock.Mock()
        source.lookup.return_value = None
        with mock.patch.object(themes.Gio.SettingsSchemaSource, "get_default", return_value=source):
            self.assertIsNone(themes.settings_icon_theme())

    def test_without_any_schema_source_there_is_no_answer(self):
        with mock.patch.object(themes.Gio.SettingsSchemaSource, "get_default", return_value=None):
            self.assertIsNone(themes.settings_icon_theme())

    def test_the_theme_is_read_from_the_settings(self):
        source = mock.Mock()
        source.lookup.return_value = object()
        settings = mock.Mock()
        settings.get_string.return_value = "Papirus"
        with mock.patch.object(themes.Gio.SettingsSchemaSource, "get_default", return_value=source):
            with mock.patch.object(themes.Gio.Settings, "new", return_value=settings) as new:
                self.assertEqual(themes.settings_icon_theme(), "Papirus")
        new.assert_called_once_with(themes.INTERFACE_SCHEMA)
        settings.get_string.assert_called_once_with(themes.ICON_THEME_KEY)

    def test_the_default_theme_is_the_last_resort(self):
        with mock.patch.object(themes, "portal_icon_theme", return_value=None):
            with mock.patch.object(themes, "settings_icon_theme", return_value=None):
                self.assertEqual(active_icon_theme(), themes.DEFAULT_THEME)



if __name__ == "__main__":
    unittest.main()
