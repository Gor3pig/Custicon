import unittest
from unittest import mock

from custicon import metadata
from custicon.errors import MetadataError
from custicon.metadata import read_custom_icon, write_custom_icon

from .support import WorkspaceTestCase, tree_fingerprint

URI = "file:///custicon-test/icon.svg"


class MetadataTests(WorkspaceTestCase):
    def setUp(self):
        self.root = self.make_workspace()
        self.folder = self.root / "target"
        self.folder.mkdir()
        (self.folder / "file.txt").write_text("content")
        self.require_metadata(self.folder)
        self.addCleanup(self.clear)

    def clear(self):
        try:
            write_custom_icon(self.folder, None)
        except MetadataError:
            pass

    def test_a_new_folder_has_no_custom_icon(self):
        self.assertIsNone(read_custom_icon(self.folder))

    def test_write_then_read(self):
        write_custom_icon(self.folder, URI)
        self.assertEqual(read_custom_icon(self.folder), URI)

    def test_overwrite_replaces_the_value(self):
        write_custom_icon(self.folder, URI)
        write_custom_icon(self.folder, URI + "2")
        self.assertEqual(read_custom_icon(self.folder), URI + "2")

    def test_none_removes_the_attribute(self):
        write_custom_icon(self.folder, URI)
        write_custom_icon(self.folder, None)
        self.assertIsNone(read_custom_icon(self.folder))

    def test_removing_an_absent_value_is_harmless(self):
        write_custom_icon(self.folder, None)
        self.assertIsNone(read_custom_icon(self.folder))

    def test_the_folder_content_is_never_modified(self):
        before = tree_fingerprint(self.folder)
        write_custom_icon(self.folder, URI)
        write_custom_icon(self.folder, None)
        self.assertEqual(tree_fingerprint(self.folder), before)

    def test_a_value_that_is_not_stored_is_reported(self):
        with mock.patch.object(metadata, "read_custom_icon", return_value="file:///other"):
            with self.assertRaises(MetadataError):
                write_custom_icon(self.folder, URI)

    def test_reading_a_missing_path_fails_cleanly(self):
        with self.assertRaises(MetadataError):
            read_custom_icon(self.root / "missing")

    def test_writing_to_a_missing_path_fails_cleanly(self):
        with self.assertRaises(MetadataError):
            write_custom_icon(self.root / "missing", URI)


if __name__ == "__main__":
    unittest.main()
