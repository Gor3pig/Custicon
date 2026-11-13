import sys
import unittest
from pathlib import Path
from unittest import mock

from custicon import __main__ as entry
from custicon import capture


class CaptureArgumentTests(unittest.TestCase):
    def test_a_screen_and_an_output_file_are_read(self):
        arguments = capture.parse_arguments(["custicon", "--capture", "main", "--out", "/tmp/x.png"])
        self.assertEqual((arguments.screen, arguments.out), ("main", Path("/tmp/x.png")))

    def test_an_unknown_screen_is_refused(self):
        with mock.patch.object(sys, "stderr"), self.assertRaises(SystemExit) as raised:
            capture.parse_arguments(["custicon", "--capture", "nowhere", "--out", "/tmp/x.png"])
        self.assertEqual(raised.exception.code, 2)

    def test_a_missing_output_file_is_refused_instead_of_an_index_error(self):
        with mock.patch.object(sys, "stderr"), self.assertRaises(SystemExit) as raised:
            capture.parse_arguments(["custicon", "--capture", "main"])
        self.assertEqual(raised.exception.code, 2)

    def test_a_missing_screen_value_is_refused(self):
        with mock.patch.object(sys, "stderr"), self.assertRaises(SystemExit):
            capture.parse_arguments(["custicon", "--capture"])

    def test_the_demo_data_is_cleaned_up_even_when_the_setup_fails(self):
        with mock.patch.object(capture.Demo, "create", side_effect=RuntimeError("boom")), mock.patch.object(capture.Demo, "cleanup") as cleanup:
            with self.assertRaises(RuntimeError):
                capture.main(["custicon", "--capture", "main", "--out", "/tmp/x.png"])
        cleanup.assert_called_once()


class EntryPointTests(unittest.TestCase):
    def test_the_capture_mode_is_used_when_it_is_available(self):
        with mock.patch.object(sys, "argv", ["custicon", "--capture", "main", "--out", "x.png"]):
            with mock.patch.object(capture, "main", return_value=7) as run:
                self.assertEqual(entry.main(), 7)
        run.assert_called_once()

    def test_without_the_capture_module_the_normal_application_starts(self):
        with mock.patch.object(sys, "argv", ["custicon", "--capture", "main"]):
            with mock.patch.object(entry.importlib.util, "find_spec", return_value=None):
                with mock.patch("custicon.app.Application") as application:
                    application.return_value.run.return_value = 0
                    self.assertEqual(entry.main(), 0)
        application.return_value.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
