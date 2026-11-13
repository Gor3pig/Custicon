import importlib
import os
import unittest
from pathlib import Path
from unittest import mock

import custicon
from custicon import app, environment


class DefaultServiceTests(unittest.TestCase):
    def test_the_store_lives_in_the_private_data_folder_inside_the_sandbox(self):
        with mock.patch.dict(os.environ, {environment.OWN_DATA_VARIABLE: "/home/someone/.var/app/x/data"}):
            service = app.default_service()
        self.assertEqual(service.store.root, Path("/home/someone/.var/app/x/data/custicon"))

    def test_the_store_lives_in_the_user_data_folder_outside_the_sandbox(self):
        environment_without = {key: value for key, value in os.environ.items() if key != environment.OWN_DATA_VARIABLE}
        with mock.patch.dict(os.environ, environment_without, clear=True):
            with mock.patch.object(app.GLib, "get_user_data_dir", return_value="/home/someone/.local/share"):
                service = app.default_service()
        self.assertEqual(service.store.root, Path("/home/someone/.local/share/custicon"))


class PackageImportTests(unittest.TestCase):
    def test_importing_the_package_redirects_gvfs_inside_the_sandbox(self):
        values = {"XDG_DATA_HOME": "/home/someone/.var/app/x/data", "HOST_XDG_DATA_HOME": "/home/someone/.local/share"}
        with mock.patch.dict(os.environ, values):
            os.environ.pop(environment.OWN_DATA_VARIABLE, None)
            with mock.patch.object(environment, "in_sandbox", return_value=True):
                importlib.reload(custicon)
                self.assertEqual(os.environ["XDG_DATA_HOME"], "/home/someone/.local/share")
                self.assertEqual(os.environ[environment.OWN_DATA_VARIABLE], "/home/someone/.var/app/x/data")
        importlib.reload(custicon)

    def test_importing_the_package_changes_nothing_outside_the_sandbox(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/somewhere"}):
            os.environ.pop(environment.OWN_DATA_VARIABLE, None)
            with mock.patch.object(environment, "in_sandbox", return_value=False):
                importlib.reload(custicon)
                self.assertEqual(os.environ["XDG_DATA_HOME"], "/somewhere")
                self.assertNotIn(environment.OWN_DATA_VARIABLE, os.environ)
        importlib.reload(custicon)


class DesktopEntryTests(unittest.TestCase):
    def entry(self) -> dict:
        path = Path(__file__).resolve().parent.parent / "data" / "io.github.gor3pig.Custicon.desktop.in"
        return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)

    def test_the_launcher_receives_the_selected_folders(self):
        self.assertTrue(self.entry()["Exec"].endswith(" %F"))

    def test_the_application_is_offered_for_folders(self):
        self.assertEqual(self.entry()["MimeType"], "inode/directory;")


if __name__ == "__main__":
    unittest.main()
