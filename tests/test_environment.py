import os
import unittest
from pathlib import Path
from unittest import mock

from custicon import environment, metadata
from custicon.errors import MetadataError

from .support import WorkspaceTestCase

HOME_SHARE = os.path.expanduser("~/.local/share")


def clean_environment(**values):
    cleaned = {key: value for key, value in os.environ.items() if key not in (environment.OWN_DATA_VARIABLE, environment.HOST_DATA_VARIABLE, "XDG_DATA_HOME")}
    cleaned.update(values)
    return mock.patch.dict(os.environ, cleaned, clear=True)


class ShareHostMetadataTests(unittest.TestCase):
    def test_nothing_changes_outside_the_sandbox(self):
        with clean_environment(XDG_DATA_HOME="/data/own"), mock.patch.object(environment, "in_sandbox", return_value=False):
            environment.share_host_metadata()
            self.assertEqual(os.environ["XDG_DATA_HOME"], "/data/own")
            self.assertNotIn(environment.OWN_DATA_VARIABLE, os.environ)
            self.assertIsNone(environment.own_data_home())

    def test_inside_the_sandbox_gvfs_is_pointed_at_the_host_folder(self):
        with clean_environment(XDG_DATA_HOME="/home/someone/.var/app/x/data"), mock.patch.object(environment, "in_sandbox", return_value=True):
            environment.share_host_metadata()
            self.assertEqual(os.environ["XDG_DATA_HOME"], HOME_SHARE)
            self.assertEqual(environment.own_data_home(), "/home/someone/.var/app/x/data")

    def test_a_custom_host_data_folder_is_respected(self):
        with clean_environment(XDG_DATA_HOME="/own", HOST_XDG_DATA_HOME="/custom/share"), mock.patch.object(environment, "in_sandbox", return_value=True):
            environment.share_host_metadata()
            self.assertEqual(os.environ["XDG_DATA_HOME"], "/custom/share")
            self.assertEqual(environment.host_data_home(), "/custom/share")

    def test_calling_twice_keeps_the_original_private_folder(self):
        with clean_environment(XDG_DATA_HOME="/own"), mock.patch.object(environment, "in_sandbox", return_value=True):
            environment.share_host_metadata()
            environment.share_host_metadata()
            self.assertEqual(environment.own_data_home(), "/own")
            self.assertEqual(os.environ["XDG_DATA_HOME"], HOME_SHARE)

    def test_a_missing_private_folder_falls_back_to_the_default(self):
        with clean_environment(), mock.patch.object(environment, "in_sandbox", return_value=True):
            environment.share_host_metadata()
            self.assertEqual(environment.own_data_home(), HOME_SHARE)


class GuardTests(WorkspaceTestCase):
    def test_writing_is_refused_when_gvfs_would_use_the_private_folder(self):
        folder = self.make_workspace() / "target"
        folder.mkdir()
        self.require_metadata(folder)
        with clean_environment(), mock.patch.object(environment, "in_sandbox", return_value=True), mock.patch.object(
            metadata.GLib, "get_user_data_dir", return_value="/home/someone/.var/app/x/data"
        ):
            with self.assertRaises(MetadataError):
                metadata.write_custom_icon(folder, "file:///x.svg")
        self.assertIsNone(metadata.read_custom_icon(folder))

    def test_writing_works_when_gvfs_uses_the_host_folder(self):
        folder = self.make_workspace() / "target"
        folder.mkdir()
        self.require_metadata(folder)
        with clean_environment(), mock.patch.object(environment, "in_sandbox", return_value=True), mock.patch.object(
            metadata.GLib, "get_user_data_dir", return_value=HOME_SHARE
        ):
            metadata.write_custom_icon(folder, "file:///x.svg")
            self.assertEqual(metadata.read_custom_icon(folder), "file:///x.svg")
            metadata.write_custom_icon(folder, None)

    def test_the_guard_accepts_the_host_folder(self):
        with clean_environment(), mock.patch.object(environment, "in_sandbox", return_value=True), mock.patch.object(
            metadata.GLib, "get_user_data_dir", return_value=HOME_SHARE
        ):
            metadata.check_shared_metadata()

    def test_the_guard_does_nothing_outside_the_sandbox(self):
        with clean_environment(), mock.patch.object(environment, "in_sandbox", return_value=False), mock.patch.object(
            metadata.GLib, "get_user_data_dir", return_value="/anything"
        ):
            metadata.check_shared_metadata()


class HomeAccessTests(WorkspaceTestCase):
    def access(self, filesystems, names=(".var",)):
        workspace = self.make_workspace()
        home = workspace / "home"
        home.mkdir(exist_ok=True)
        for name in names:
            (home / name).mkdir(exist_ok=True)
        info = workspace / "flatpak-info"
        info.write_text("[Application]\nname=x\n\n[Context]\n" + (f"filesystems={filesystems}\n" if filesystems is not None else ""))
        return self.run_access(info, home)

    backend = environment.BACKEND_GVFS

    def run_access(self, info, home):
        with mock.patch.object(environment, "FLATPAK_INFO", str(info)), mock.patch.object(
            environment.os.path, "expanduser", side_effect=lambda value: str(home) if value == "~" else value
        ), mock.patch.object(environment, "detect_backend", return_value=self.backend):
            return environment.home_access()

    def test_outside_the_sandbox_the_home_folder_is_always_visible(self):
        with mock.patch.object(environment, "in_sandbox", return_value=False):
            self.assertTrue(environment.home_access())

    def test_home_and_host_grant_access_in_either_mode(self):
        for value in ("home;", "home:ro;", "home:rw;", "host;", "xdg-download;home:ro;", "!home;home:ro;"):
            self.assertTrue(self.access(value), value)

    def test_the_directory_engine_needs_write_access_to_the_home_folder(self):
        self.backend = environment.BACKEND_DIRECTORY
        for value in ("home:ro;", "~:ro;", "!home;home:ro;"):
            self.assertFalse(self.access(value), value)
        for value in ("home;", "home:rw;", "host;"):
            self.assertTrue(self.access(value), value)

    def test_a_visible_read_only_home_is_not_enough_for_the_directory_engine(self):
        self.backend = environment.BACKEND_DIRECTORY
        self.assertFalse(self.access("home:ro;", names=(".var", ".config")))
        self.assertFalse(self.access("xdg-download;home:ro;", names=(".config",)))
        self.assertTrue(self.access(None, names=(".config",)))
        self.backend = environment.BACKEND_GVFS
        self.assertTrue(self.access("home:ro;", names=(".config",)))

    def test_other_folders_do_not_grant_access_to_the_home_folder(self):
        for value in ("xdg-download;", "xdg-documents:ro;", "!home;", "/srv;", "host-os;", "", None):
            self.assertFalse(self.access(value), repr(value))

    def test_the_home_folder_written_as_a_path_grants_access(self):
        workspace = self.make_workspace()
        home = workspace / "home"
        home.mkdir()
        (home / ".var").mkdir()
        info = workspace / "flatpak-info"
        for value in ("~;", "~:ro;", f"{home};", f"{home}:ro;", f"{home}/;", "xdg-download;~:ro;"):
            info.write_text(f"[Context]\nfilesystems={value}\n")
            self.assertTrue(self.run_access(info, home), value)

    def test_another_folder_written_as_a_path_does_not_grant_access(self):
        workspace = self.make_workspace()
        home = workspace / "home"
        home.mkdir()
        (home / ".var").mkdir()
        info = workspace / "flatpak-info"
        for value in (f"{home}/Documents;", "/srv:ro;", "/;x"):
            info.write_text(f"[Context]\nfilesystems={value}\n")
            self.assertFalse(self.run_access(info, home), value)

    def test_a_real_home_folder_is_seen_even_when_the_permission_is_not_listed(self):
        self.assertTrue(self.access("xdg-download;", [".var", ".bashrc", "Downloads"]))
        self.assertTrue(self.access(None, [".var", ".config"]))

    def test_a_sandbox_home_with_only_the_application_data_is_not_the_real_home(self):
        for names in ([".var"], [".var", "Downloads", "Documents"], []):
            self.assertFalse(self.access("xdg-download;", names), names)

    def test_an_empty_permission_list_with_a_bare_home_is_missing_access(self):
        self.assertFalse(self.access("", [".var"]))

    def test_an_unreadable_home_is_not_the_real_home(self):
        workspace = self.make_workspace()
        info = workspace / "flatpak-info"
        info.write_text("[Context]\nfilesystems=xdg-download;\n")
        self.assertFalse(self.run_access(info, workspace / "missing"))

    def test_an_unreadable_description_does_not_raise_an_alarm(self):
        info = self.make_workspace() / "flatpak-info"
        info.write_bytes(b"\xff\xfe[Context\n")
        with mock.patch.object(environment, "FLATPAK_INFO", str(info)):
            self.assertTrue(environment.home_access())


if __name__ == "__main__":
    unittest.main()
