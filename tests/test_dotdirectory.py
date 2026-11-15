import os
import random
import re
import stat
import unittest
from pathlib import Path
from unittest import mock

from custicon import dotdirectory as dd
from custicon.errors import MetadataError

from .support import WorkspaceTestCase

DOLPHIN_FILE = (
    "[Dolphin]\n"
    "Timestamp=2026,11,15,1,2,3.456\n"
    "Version=4\n"
    "ViewMode=1\n"
    "\n"
    "[Settings]\n"
    "HiddenFilesShown=true\n"
)
WITH_ICON = (
    "[Desktop Entry]\n"
    "Icon=folder-red\n"
    "\n"
    "[Dolphin]\n"
    "Timestamp=2026,11,15,1,2,3.456\n"
)


class EscapingTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self):
        self.assertEqual(dd.escape("/home/a/icon.svg"), "/home/a/icon.svg")

    def test_special_characters_are_escaped_and_restored(self):
        for value in ("a\\b", "a\nb", "a\rb", "a\tb", " leading", "all \\ \n \r \t mixed"):
            with self.subTest(value=value):
                self.assertEqual(dd.unescape(dd.escape(value)), value)

    def test_a_leading_space_becomes_a_space_escape(self):
        self.assertEqual(dd.escape(" x"), "\\sx")

    def test_an_unknown_escape_is_kept_as_it_is(self):
        self.assertEqual(dd.unescape("a\\qb"), "a\\qb")

    def test_a_trailing_backslash_is_kept(self):
        self.assertEqual(dd.unescape("abc\\"), "abc\\")


class ReadingTests(unittest.TestCase):
    def test_no_icon_in_an_empty_text(self):
        self.assertIsNone(dd.icon_value(""))

    def test_the_icon_of_the_desktop_entry_is_read(self):
        self.assertEqual(dd.icon_value(WITH_ICON), "folder-red")

    def test_another_section_does_not_count(self):
        self.assertIsNone(dd.icon_value("[Other]\nIcon=x\n"))

    def test_a_localized_icon_does_not_count(self):
        self.assertIsNone(dd.icon_value("[Desktop Entry]\nIcon[fr]=x\n"))

    def test_spaces_around_the_equal_sign_are_accepted(self):
        self.assertEqual(dd.icon_value("[Desktop Entry]\nIcon = /a/b.svg\n"), "/a/b.svg")

    def test_an_empty_value_means_no_icon(self):
        self.assertIsNone(dd.icon_value("[Desktop Entry]\nIcon=\n"))

    def test_the_last_duplicate_wins(self):
        self.assertEqual(dd.icon_value("[Desktop Entry]\nIcon=a\nIcon=b\n"), "b")

    def test_crlf_endings_are_understood(self):
        self.assertEqual(dd.icon_value("[Desktop Entry]\r\nIcon=x\r\n"), "x")

    def test_a_byte_order_mark_is_understood(self):
        self.assertEqual(dd.icon_value("﻿[Desktop Entry]\nIcon=x\n"), "x")

    def test_a_header_with_surrounding_spaces_is_understood(self):
        self.assertEqual(dd.icon_value("  [Desktop Entry]  \nIcon=x\n"), "x")

    def test_a_similar_key_is_not_the_icon(self):
        self.assertIsNone(dd.icon_value("[Desktop Entry]\nIconSize=3\n"))

    def test_a_commented_icon_does_not_count(self):
        self.assertIsNone(dd.icon_value("[Desktop Entry]\n#Icon=x\n"))

    def test_the_value_is_unescaped(self):
        self.assertEqual(dd.icon_value("[Desktop Entry]\nIcon=\\sa\\\\b\n"), " a\\b")


class AddingTests(unittest.TestCase):
    def test_a_new_file_gets_the_section_and_the_icon(self):
        edit = dd.with_icon("", "/i.svg")
        self.assertEqual(edit.text, "[Desktop Entry]\nIcon=/i.svg\n")
        self.assertTrue(edit.header_added)

    def test_other_sections_are_kept_and_ours_is_appended(self):
        edit = dd.with_icon(DOLPHIN_FILE, "/i.svg")
        self.assertTrue(edit.text.startswith(DOLPHIN_FILE))
        self.assertTrue(edit.text.endswith("[Desktop Entry]\nIcon=/i.svg\n"))
        self.assertTrue(edit.header_added)

    def test_an_existing_icon_is_replaced_in_place(self):
        edit = dd.with_icon(WITH_ICON, "/i.svg")
        self.assertEqual(edit.text, WITH_ICON.replace("Icon=folder-red", "Icon=/i.svg"))
        self.assertFalse(edit.header_added)

    def test_a_section_without_icon_gets_a_line_after_the_header(self):
        edit = dd.with_icon("[Desktop Entry]\nName=x\n", "/i.svg")
        self.assertEqual(edit.text, "[Desktop Entry]\nIcon=/i.svg\nName=x\n")

    def test_the_same_value_is_not_a_change(self):
        self.assertFalse(dd.with_icon("[Desktop Entry]\nIcon=/i.svg\n", "/i.svg").changed)

    def test_a_missing_final_newline_is_handled(self):
        edit = dd.with_icon("[Other]\na=b", "/i.svg")
        self.assertEqual(edit.text, "[Other]\na=b\n\n[Desktop Entry]\nIcon=/i.svg\n")

    def test_crlf_files_keep_their_line_endings(self):
        edit = dd.with_icon("[Other]\r\na=b\r\n", "/i.svg")
        self.assertEqual(edit.text, "[Other]\r\na=b\r\n\r\n[Desktop Entry]\r\nIcon=/i.svg\r\n")

    def test_a_replaced_line_keeps_its_ending(self):
        edit = dd.with_icon("[Desktop Entry]\r\nIcon=a\r\nName=x\r\n", "/i.svg")
        self.assertEqual(edit.text, "[Desktop Entry]\r\nIcon=/i.svg\r\nName=x\r\n")

    def test_the_byte_order_mark_is_kept(self):
        edit = dd.with_icon("﻿[Desktop Entry]\nName=x\n", "/i.svg")
        self.assertTrue(edit.text.startswith("﻿[Desktop Entry]"))

    def test_every_duplicate_is_replaced(self):
        edit = dd.with_icon("[Desktop Entry]\nIcon=a\nIcon=b\n", "/i.svg")
        self.assertEqual(edit.text, "[Desktop Entry]\nIcon=/i.svg\nIcon=/i.svg\n")

    def test_other_lines_are_never_touched(self):
        text = "# comment\n[Desktop Entry]\nName=x\nIcon=a\nComment=y\n\n[Dolphin]\nIcon=keep\n"
        edit = dd.with_icon(text, "/i.svg")
        self.assertEqual(edit.text, text.replace("Icon=a", "Icon=/i.svg", 1))

    def test_the_icon_of_another_section_is_not_replaced(self):
        edit = dd.with_icon("[Dolphin]\nIcon=keep\n", "/i.svg")
        self.assertIn("[Dolphin]\nIcon=keep\n", edit.text)
        self.assertEqual(dd.icon_value(edit.text), "/i.svg")

    def test_a_value_with_special_characters_is_stored_escaped(self):
        edit = dd.with_icon("", " /a\\b.svg")
        self.assertEqual(dd.icon_value(edit.text), " /a\\b.svg")


class RemovingTests(unittest.TestCase):
    def test_removing_the_added_line_restores_the_original(self):
        for original in (DOLPHIN_FILE, "[Desktop Entry]\nName=x\n", "", "[Other]\r\na=b\r\n", "[Other]\na=b"):
            with self.subTest(original=original):
                added = dd.with_icon(original, "/i.svg")
                restored = dd.without_icon(added.text)
                self.assertEqual(restored.text.strip(" \t\r\n"), original.strip(" \t\r\n"), (original, added.text, restored.text))

    def test_the_icon_line_is_removed(self):
        edit = dd.without_icon("[Desktop Entry]\nIcon=a\nName=x\n[Dolphin]\nA=1\n")
        self.assertEqual(edit.text, "[Desktop Entry]\nName=x\n[Dolphin]\nA=1\n")

    def test_an_emptied_section_is_dropped(self):
        edit = dd.without_icon(WITH_ICON)
        self.assertEqual(edit.text, "\n[Dolphin]\nTimestamp=2026,11,15,1,2,3.456\n".lstrip("\n"))

    def test_nothing_to_remove_is_not_a_change(self):
        self.assertFalse(dd.without_icon(DOLPHIN_FILE).changed)
        self.assertFalse(dd.without_icon("").changed)

    def test_another_key_of_the_section_keeps_the_section(self):
        edit = dd.without_icon("[Desktop Entry]\nIcon=a\nName=x\n")
        self.assertEqual(edit.text, "[Desktop Entry]\nName=x\n")

    def test_a_localized_icon_is_never_removed(self):
        text = "[Desktop Entry]\nIcon[fr]=x\nIcon=a\n"
        self.assertEqual(dd.without_icon(text).text, "[Desktop Entry]\nIcon[fr]=x\n")

    def test_the_icon_of_another_section_is_never_removed(self):
        text = "[Desktop Entry]\nIcon=a\n[Other]\nIcon=b\n"
        self.assertEqual(dd.without_icon(text).text, "[Other]\nIcon=b\n")

    def test_the_byte_order_mark_is_kept(self):
        self.assertTrue(dd.without_icon("﻿[Desktop Entry]\nIcon=a\nName=x\n").text.startswith("﻿"))


class RandomContentTests(unittest.TestCase):
    def random_text(self, rng: random.Random) -> str:
        newline = rng.choice(["\n", "\r\n"])
        lines = []
        for _ in range(rng.randint(0, 12)):
            kind = rng.choice(["header", "key", "comment", "blank", "icon", "other-icon", "localized"])
            if kind == "header":
                header = rng.choice(["[Desktop Entry]", "[Dolphin]", "[Settings]", "[X-KDE]"])
                if header not in lines:
                    lines.append(header)
            elif kind == "key":
                lines.append(f"Key{rng.randint(0, 9)}={rng.choice(['a', '1,2,3', 'é', ''])}")
            elif kind == "comment":
                lines.append("# comment " + str(rng.randint(0, 99)))
            elif kind == "blank":
                lines.append("")
            elif kind == "icon":
                lines.append("Icon=" + rng.choice(["folder-red", "/x/y.svg", ""]))
            elif kind == "other-icon":
                lines.append("IconSize=" + str(rng.randint(1, 5)))
            else:
                lines.append("Icon[fr]=zz")
        text = newline.join(lines)
        if lines and rng.random() < 0.8:
            text += newline
        return text

    def reference_without_entry_icon(self, text: str) -> list[str]:
        section = None
        kept = []
        for line in text.split("\n"):
            stripped = line.strip(" \t\r")
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped
            if section == "[Desktop Entry]" and re.match(r"^\s*Icon\s*=", line):
                continue
            kept.append(line.rstrip("\r"))
        return [line for line in kept if line.strip()]

    def test_adding_then_removing_restores_everything_else(self):
        rng = random.Random(20261115)
        for _ in range(600):
            original = self.random_text(rng)
            added = dd.with_icon(original, "/i.svg")
            self.assertEqual(dd.icon_value(added.text), "/i.svg", original)
            removed = dd.without_icon(added.text)
            got = [line.rstrip("\r") for line in removed.text.split("\n") if line.strip()]
            wanted = [line for line in self.reference_without_entry_icon(original) if line.strip() != dd.SECTION or self.has_other_keys(original)]
            got = [line for line in got if line.strip() != dd.SECTION or self.has_other_keys(original)]
            self.assertEqual(got, wanted, (original, added.text, removed.text))

    def has_other_keys(self, text: str) -> bool:
        section = None
        for line in text.split("\n"):
            stripped = line.strip(" \t\r")
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped
                continue
            if section == "[Desktop Entry]" and stripped and not re.match(r"^Icon\s*=", stripped):
                return True
        return False

    def test_other_lines_are_byte_identical_after_adding(self):
        rng = random.Random(7)
        for _ in range(600):
            original = self.random_text(rng)
            added = dd.with_icon(original, "/i.svg")
            before = [line.rstrip("\r\n") + "\n" for line in dd.split_lines(original) if not dd.is_icon_line(line)]
            after = [line.rstrip("\r\n") + "\n" for line in dd.split_lines(added.text) if not dd.is_icon_line(line) and line.strip(" \t\r\n") not in ("", dd.SECTION)]
            kept = [line for line in before if line.strip(" \t\r\n") not in ("", dd.SECTION)]
            self.assertEqual(after, kept, (original, added.text))

    def test_adding_twice_is_the_same_as_once(self):
        rng = random.Random(11)
        for _ in range(300):
            once = dd.with_icon(self.random_text(rng), "/i.svg").text
            self.assertEqual(dd.with_icon(once, "/i.svg").text, once)
            self.assertFalse(dd.with_icon(once, "/i.svg").changed)

    def test_the_value_read_is_the_value_written(self):
        rng = random.Random(3)
        alphabet = "abc /\\é.-_\t"
        for _ in range(300):
            value = "/" + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 20)))
            self.assertEqual(dd.icon_value(dd.with_icon(self.random_text(rng), value).text), value)


class UriTests(unittest.TestCase):
    def test_a_path_becomes_a_file_uri(self):
        self.assertEqual(dd.to_uri("/a b/é.svg"), "file:///a%20b/%C3%A9.svg")

    def test_a_theme_name_stays_a_name(self):
        self.assertEqual(dd.to_uri("folder-red"), "folder-red")

    def test_a_file_uri_becomes_a_path(self):
        self.assertEqual(dd.to_value("file:///a%20b/%C3%A9.svg"), "/a b/é.svg")

    def test_a_name_stays_a_name_the_other_way(self):
        self.assertEqual(dd.to_value("folder-red"), "folder-red")


class FileTests(WorkspaceTestCase):
    def setUp(self):
        self.folder = self.make_workspace() / "folder"
        self.folder.mkdir()
        self.file = self.folder / ".directory"

    def uri(self, name="i.svg"):
        return (self.folder / name).as_uri()

    def test_a_folder_without_file_has_no_icon(self):
        self.assertIsNone(dd.read_icon(self.folder))

    def test_writing_creates_the_file(self):
        written = dd.write_icon(self.folder, self.uri())
        self.assertTrue(written.created_file)
        self.assertEqual(self.file.read_text(), f"[Desktop Entry]\nIcon={self.folder}/i.svg\n")
        self.assertEqual(dd.read_icon(self.folder), self.uri())

    def test_the_created_file_is_readable_by_others(self):
        dd.write_icon(self.folder, self.uri())
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o644)

    def test_writing_keeps_the_settings_of_the_file_manager(self):
        self.file.write_text(DOLPHIN_FILE)
        written = dd.write_icon(self.folder, self.uri())
        self.assertFalse(written.created_file)
        self.assertTrue(self.file.read_text().startswith(DOLPHIN_FILE))

    def test_writing_keeps_the_permissions_of_an_existing_file(self):
        self.file.write_text(DOLPHIN_FILE)
        os.chmod(self.file, 0o600)
        dd.write_icon(self.folder, self.uri())
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_removing_restores_the_original_bytes(self):
        self.file.write_bytes(DOLPHIN_FILE.encode())
        dd.write_icon(self.folder, self.uri())
        dd.write_icon(self.folder, None)
        self.assertEqual(self.file.read_bytes(), DOLPHIN_FILE.encode())

    def test_removing_deletes_a_file_that_was_created_by_us(self):
        dd.write_icon(self.folder, self.uri())
        written = dd.write_icon(self.folder, None, created_by_us=True)
        self.assertTrue(written.removed_file)
        self.assertFalse(self.file.exists())

    def test_removing_keeps_a_file_that_was_not_created_by_us(self):
        self.file.write_text("[Desktop Entry]\nIcon=a\n")
        written = dd.write_icon(self.folder, None)
        self.assertFalse(written.removed_file)
        self.assertTrue(self.file.exists())

    def test_removing_keeps_the_other_settings_of_a_file_created_by_us(self):
        dd.write_icon(self.folder, self.uri())
        self.file.write_text(self.file.read_text() + "\n[Dolphin]\nViewMode=2\n")
        written = dd.write_icon(self.folder, None, created_by_us=True)
        self.assertFalse(written.removed_file)
        self.assertIn("ViewMode=2", self.file.read_text())
        self.assertNotIn("Icon=", self.file.read_text())

    def test_removing_when_there_is_no_file_is_fine(self):
        written = dd.write_icon(self.folder, None)
        self.assertEqual((written.created_file, written.removed_file), (False, False))

    def test_the_same_icon_twice_does_not_rewrite_the_file(self):
        dd.write_icon(self.folder, self.uri())
        before = self.file.stat().st_mtime_ns
        dd.write_icon(self.folder, self.uri())
        self.assertEqual(self.file.stat().st_mtime_ns, before)

    def test_a_previous_theme_name_is_restored_as_a_name(self):
        self.file.write_text("[Desktop Entry]\nIcon=folder-red\n")
        dd.write_icon(self.folder, self.uri())
        dd.write_icon(self.folder, "folder-red")
        self.assertEqual(self.file.read_text(), "[Desktop Entry]\nIcon=folder-red\n")

    def test_no_temporary_file_is_left(self):
        dd.write_icon(self.folder, self.uri())
        dd.write_icon(self.folder, None, created_by_us=True)
        self.assertEqual([entry.name for entry in self.folder.iterdir()], [])

    def test_an_unreadable_file_is_refused(self):
        self.file.write_bytes(b"\xff\xfe[Desktop Entry]\n\x80\nIcon=a\n")
        written = dd.write_icon(self.folder, self.uri())
        self.assertFalse(written.created_file)
        self.assertIn(b"\x80", self.file.read_bytes())

    def test_invalid_bytes_survive_an_edit(self):
        original = b"[Dolphin]\nX=\xe9\xff\n"
        self.file.write_bytes(original)
        dd.write_icon(self.folder, self.uri())
        dd.write_icon(self.folder, None)
        self.assertTrue(self.file.read_bytes().startswith(original))

    def test_a_directory_named_like_the_file_is_refused(self):
        self.file.mkdir()
        with self.assertRaises(MetadataError):
            dd.write_icon(self.folder, self.uri())

    def test_a_symbolic_link_is_refused_and_left_alone(self):
        target = self.folder / "elsewhere"
        target.write_text("[Dolphin]\nX=1\n")
        self.file.symlink_to(target)
        with self.assertRaises(MetadataError):
            dd.write_icon(self.folder, self.uri())
        self.assertTrue(self.file.is_symlink())
        self.assertEqual(target.read_text(), "[Dolphin]\nX=1\n")

    def test_a_huge_file_is_refused(self):
        self.file.write_bytes(b"#" * (dd.MAX_BYTES + 1))
        with self.assertRaises(MetadataError):
            dd.write_icon(self.folder, self.uri())

    @unittest.skipIf(os.geteuid() == 0, "root can write anywhere")
    def test_a_read_only_folder_gives_a_clear_error_and_changes_nothing(self):
        os.chmod(self.folder, 0o555)
        self.addCleanup(os.chmod, self.folder, 0o755)
        with self.assertRaises(MetadataError):
            dd.write_icon(self.folder, self.uri())
        self.assertFalse(self.file.exists())

    @unittest.skipIf(os.geteuid() == 0, "root can write anywhere")
    def test_a_read_only_file_in_a_read_only_folder_is_not_modified(self):
        self.file.write_text(DOLPHIN_FILE)
        os.chmod(self.folder, 0o555)
        self.addCleanup(os.chmod, self.folder, 0o755)
        with self.assertRaises(MetadataError):
            dd.write_icon(self.folder, self.uri())
        self.assertEqual(self.file.read_text(), DOLPHIN_FILE)

    def test_a_missing_folder_gives_an_error(self):
        with self.assertRaises(MetadataError):
            dd.write_icon(self.folder / "missing", self.uri())

    def test_a_failure_while_writing_leaves_the_file_untouched(self):
        self.file.write_text(DOLPHIN_FILE)
        with mock.patch.object(dd.os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(MetadataError):
                dd.write_icon(self.folder, self.uri())
        self.assertEqual(self.file.read_text(), DOLPHIN_FILE)
        self.assertEqual([entry.name for entry in self.folder.iterdir()], [".directory"])

    def test_a_change_during_the_edit_is_noticed_and_retried(self):
        self.file.write_text(DOLPHIN_FILE)
        real = dd.read_text
        calls = []

        def racing(path):
            calls.append(path)
            if len(calls) == 2:
                path.write_text(DOLPHIN_FILE + "[Added]\nx=1\n")
            return real(path)

        with mock.patch.object(dd, "read_text", side_effect=racing):
            dd.write_icon(self.folder, self.uri())
        self.assertIn("[Added]", self.file.read_text())
        self.assertEqual(dd.read_icon(self.folder), self.uri())

    def test_endless_changes_give_up_with_an_error(self):
        self.file.write_text(DOLPHIN_FILE)
        counter = iter(range(1000))
        real = dd.read_text

        def always_changing(path):
            path.write_text(DOLPHIN_FILE + f"#{next(counter)}\n")
            return real(path)

        with mock.patch.object(dd, "read_text", side_effect=always_changing):
            with self.assertRaises(MetadataError):
                dd.write_icon(self.folder, self.uri())

    def test_the_value_is_checked_after_writing(self):
        with mock.patch.object(dd, "read_icon", return_value=None):
            with self.assertRaises(MetadataError):
                dd.write_icon(self.folder, self.uri())

    def test_a_name_with_spaces_and_accents_round_trips(self):
        uri = (self.folder / "mon icône 1.svg").as_uri()
        dd.write_icon(self.folder, uri)
        self.assertEqual(dd.read_icon(self.folder), uri)
        self.assertIn("mon icône 1.svg", self.file.read_text())

    def test_crlf_files_stay_crlf(self):
        self.file.write_bytes(b"[Dolphin]\r\nViewMode=1\r\n")
        dd.write_icon(self.folder, self.uri())
        content = self.file.read_bytes()
        self.assertIn(b"\r\n", content)
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        self.assertNotIn(b"\r", content.replace(b"\r\n", b""))

    def test_a_theme_name_in_the_file_is_read_as_a_name(self):
        self.file.write_text("[Desktop Entry]\nIcon=folder-blue\n")
        self.assertEqual(dd.read_icon(self.folder), "folder-blue")


if __name__ == "__main__":
    unittest.main()


class MutationGapTests(WorkspaceTestCase):
    def setUp(self):
        self.folder = self.make_workspace() / "folder"
        self.folder.mkdir()
        self.file = self.folder / ".directory"

    def uri(self):
        return (self.folder / "i.svg").as_uri()

    def test_headers_must_open_and_close_with_brackets(self):
        for line in ("[x\n", "x]\n", "[x]\n"):
            with self.subTest(line=line):
                self.assertEqual(dd.is_other_header(line), line == "[x]\n")
        self.assertFalse(dd.is_other_header("[Desktop Entry]\n"))

    def test_a_key_after_two_other_sections_is_not_ours(self):
        text = "[Desktop Entry]\n[A]\nIcon=a\n[B]\nName=b\n"
        self.assertIsNone(dd.icon_value(text))
        self.assertEqual(dd.with_icon(text, "/i.svg").text, "[Desktop Entry]\nIcon=/i.svg\n[A]\nIcon=a\n[B]\nName=b\n")
        self.assertEqual(dd.without_icon(text), dd.Edit(text, False, False))

    def test_the_flags_of_every_edit_are_exact(self):
        self.assertEqual(dd.with_icon("[Desktop Entry]\nName=x\n", "/i.svg"), dd.Edit("[Desktop Entry]\nIcon=/i.svg\nName=x\n", False, True))
        self.assertEqual(dd.with_icon("", "/i.svg"), dd.Edit("[Desktop Entry]\nIcon=/i.svg\n", True, True))
        self.assertEqual(dd.with_icon("[Desktop Entry]\nIcon=a\n", "/i.svg"), dd.Edit("[Desktop Entry]\nIcon=/i.svg\n", False, True))
        self.assertEqual(dd.with_icon("[Desktop Entry]\nIcon=/i.svg\n", "/i.svg"), dd.Edit("[Desktop Entry]\nIcon=/i.svg\n", False, False))
        self.assertEqual(dd.without_icon("[Desktop Entry]\nName=x\n"), dd.Edit("[Desktop Entry]\nName=x\n", False, False))
        self.assertEqual(dd.without_icon("[Other]\nA=1\n"), dd.Edit("[Other]\nA=1\n", False, False))
        self.assertEqual(dd.without_icon("[Desktop Entry]\nIcon=a\nName=x\n"), dd.Edit("[Desktop Entry]\nName=x\n", False, True))

    def test_a_byte_order_mark_is_not_duplicated(self):
        for original in ("﻿[Desktop Entry]\nName=x\n", "﻿[Other]\nA=1\n", "﻿[Desktop Entry]\nIcon=a\n"):
            with self.subTest(original=original):
                text = dd.with_icon(original, "/i.svg").text
                self.assertEqual(text.count("﻿"), 1)
                self.assertTrue(text.startswith("﻿"))

    def test_a_last_line_without_newline_gets_one_when_replaced(self):
        self.assertEqual(dd.with_icon("[Desktop Entry]\nIcon=a", "/i.svg").text, "[Desktop Entry]\nIcon=/i.svg\n")

    def test_a_whitespace_only_line_is_not_content(self):
        self.assertEqual(dd.with_icon("[Other]\nA=1\n  \n", "/i.svg").text, "[Other]\nA=1\n  \n[Desktop Entry]\nIcon=/i.svg\n")
        self.assertEqual(dd.without_icon("[Desktop Entry]\nIcon=a\n \t\n").text, "")
        self.assertEqual(dd.without_icon("[Other]\nA=1\n\n[Desktop Entry]\nIcon=a\n").text, "[Other]\nA=1\n")
        self.assertEqual(dd.without_icon("[Other]\nA=1\n \n[Desktop Entry]\nIcon=a\n").text, "[Other]\nA=1\n")

    def test_the_icon_key_may_be_indented_and_spaced(self):
        self.assertTrue(dd.is_icon_line("  Icon = x\n"))
        self.assertTrue(dd.is_icon_line("\tIcon=x\n"))
        self.assertFalse(dd.is_icon_line("Icon\n"))

    def test_a_file_of_exactly_the_limit_is_accepted(self):
        self.file.write_bytes(b"#" * dd.MAX_BYTES)
        self.assertEqual(len(dd.read_text(self.file)), dd.MAX_BYTES)

    def test_the_results_of_writing_are_exact(self):
        self.assertEqual(dd.write_icon(self.folder, self.uri()), dd.Written(True, False))
        self.assertEqual(dd.write_icon(self.folder, self.uri()), dd.Written(False, False))
        self.assertEqual(dd.write_icon(self.folder, None, created_by_us=True), dd.Written(False, True))
        self.assertFalse(self.file.exists())
        self.assertEqual(dd.write_icon(self.folder, None), dd.Written(False, False))
        self.file.write_text(DOLPHIN_FILE)
        self.assertEqual(dd.write_icon(self.folder, self.uri()), dd.Written(False, False))
        self.assertEqual(dd.write_icon(self.folder, None, created_by_us=True), dd.Written(False, False))
        self.assertEqual(self.file.read_text(), DOLPHIN_FILE)

    def test_removing_when_nothing_is_ours_does_not_touch_the_file(self):
        self.file.write_text(DOLPHIN_FILE)
        before = self.file.stat()
        with mock.patch.object(dd, "write_atomic") as write:
            dd.write_icon(self.folder, None)
        write.assert_not_called()
        self.assertEqual(self.file.stat().st_ino, before.st_ino)

    def test_a_change_during_a_removal_is_noticed_and_retried(self):
        self.file.write_text("[Desktop Entry]\nIcon=/a.svg\n")
        real = dd.read_text
        calls = []

        def racing(path):
            calls.append(path)
            if len(calls) == 2:
                path.write_text("[Desktop Entry]\nIcon=/a.svg\n[Added]\nx=1\n")
            return real(path)

        with mock.patch.object(dd, "read_text", side_effect=racing):
            dd.write_icon(self.folder, None, created_by_us=True)
        self.assertEqual(self.file.read_text(), "[Added]\nx=1\n")

    def test_a_change_before_deleting_a_created_file_keeps_the_new_content(self):
        self.file.write_text("[Desktop Entry]\nIcon=/a.svg\n")
        real = dd.read_text
        calls = []

        def racing(path):
            calls.append(path)
            if len(calls) == 2:
                path.write_text("[Desktop Entry]\nIcon=/a.svg\n[Dolphin]\nViewMode=1\n")
            return real(path)

        with mock.patch.object(dd, "read_text", side_effect=racing):
            dd.write_icon(self.folder, None, created_by_us=True)
        self.assertEqual(self.file.read_text(), "[Dolphin]\nViewMode=1\n")

    def test_errors_name_the_cause(self):
        self.file.mkdir()
        with self.assertRaisesRegex(MetadataError, "not a regular file"):
            dd.read_icon(self.folder)

    def test_a_removal_with_nothing_to_remove_reports_nothing(self):
        self.file.write_text(DOLPHIN_FILE)
        self.assertEqual(dd.write_icon(self.folder, None), dd.Written(False, False))

    def test_the_byte_order_mark_does_not_hide_the_icon_to_remove(self):
        self.assertEqual(dd.without_icon("﻿[Desktop Entry]\nIcon=a\nName=x\n").text, "﻿[Desktop Entry]\nName=x\n")

    def test_os_errors_are_reported_with_their_cause(self):
        self.file.write_text(DOLPHIN_FILE)
        denied = PermissionError(13, "Permission denied")
        with mock.patch.object(Path, "lstat", side_effect=denied):
            with self.assertRaisesRegex(MetadataError, "cannot read .directory: Permission denied"):
                dd.read_text(self.file)
            with self.assertRaisesRegex(MetadataError, "cannot write .directory: Permission denied"):
                dd.write_atomic(self.file, "x")
        with mock.patch.object(Path, "read_bytes", side_effect=denied):
            with self.assertRaisesRegex(MetadataError, "cannot read .directory: Permission denied"):
                dd.read_text(self.file)
        with mock.patch.object(dd.tempfile, "mkstemp", side_effect=denied):
            with self.assertRaisesRegex(MetadataError, "cannot write .directory: Permission denied"):
                dd.write_atomic(self.file, "x")
        self.file.write_text("[Desktop Entry]\nIcon=/a.svg\n")
        with mock.patch.object(Path, "unlink", side_effect=denied):
            with self.assertRaisesRegex(MetadataError, "cannot remove .directory: Permission denied"):
                dd.write_icon(self.folder, None, created_by_us=True)

    def test_a_missing_value_after_writing_is_an_error(self):
        with mock.patch.object(dd, "read_icon", return_value=None):
            with self.assertRaisesRegex(MetadataError, "was not stored"):
                dd.write_icon(self.folder, self.uri())


class NotifyTests(WorkspaceTestCase):
    def setUp(self):
        self.folder = self.make_workspace() / "folder"
        self.folder.mkdir()

    def test_the_folder_and_its_file_are_announced_to_the_file_manager(self):
        bus = mock.Mock()
        with mock.patch.object(dd.Gio, "bus_get_sync", return_value=bus):
            self.assertTrue(dd.notify(self.folder))
        args = bus.emit_signal.call_args.args
        self.assertEqual(args[1:4], ("/", "org.kde.KDirNotify", "FilesChanged"))
        self.assertEqual(args[4].unpack(), ([self.folder.as_uri(), (self.folder / ".directory").as_uri()],))
        bus.flush_sync.assert_called_once()

    def test_a_missing_session_bus_is_not_an_error(self):
        with mock.patch.object(dd.Gio, "bus_get_sync", side_effect=dd.GLib.Error("no bus")):
            self.assertFalse(dd.notify(self.folder))
        bus = mock.Mock()
        bus.emit_signal.side_effect = dd.GLib.Error("refused")
        with mock.patch.object(dd.Gio, "bus_get_sync", return_value=bus):
            self.assertFalse(dd.notify(self.folder))

    def test_writing_and_removing_through_the_service_notify_only_for_this_engine(self):
        from custicon.service import Service
        from custicon.store import Store
        from .support import folder_svg, glyph_svg, make_theme

        themes_dir = self.folder.parent / "themes"
        themes_dir.mkdir()
        make_theme(themes_dir, "Blue", folder=folder_svg("#3366cc"), glyphs={"star-symbolic": glyph_svg()})
        from custicon.service import Settings

        for backend, expected in (("directory", 2), ("gvfs", 0)):
            target = self.folder.parent / backend
            target.mkdir()
            service = Service(Store(self.folder.parent / f"data-{backend}"), theme_name=lambda: "Blue", search_dirs=[str(themes_dir)], backend=backend)
            with mock.patch.object(dd, "notify") as notify:
                try:
                    service.apply(target, Settings(glyph_name="star-symbolic"))
                    service.reset(target)
                except MetadataError:
                    pass
            self.assertEqual(notify.call_count, expected, backend)
