import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "build-aux" / "update-icon-sets.py"
spec = importlib.util.spec_from_file_location("update_icon_sets", SCRIPT)
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)

ICONS_TS = "\n".join(
    [
        '',
        'export const icons = (<const>[',
        '  {',
        '    name: "acorn",',
        '    pascal_name: "Acorn",',
        '    categories: [IconCategory.FINANCE, IconCategory.NATURE],',
        '    figma_category: FigmaCategory.WEATHER,',
        '    tags: [',
        '      "*new*",',
        '      "savings",',
        '      "nut",',
        '    ],',
        '    codepoint: 60314,',
        '  },',
        '  {',
        '    name: "address-book",',
        '    pascal_name: "AddressBook",',
        '    categories: [IconCategory.COMMUNICATION],',
        '    figma_category: FigmaCategory.COMMUNICATION,',
        '    tags: ["contacts", "directory"],',
        '    codepoint: 59128,',
        '  },',
        '  {',
        '    name: "ghost",',
        '    pascal_name: "Ghost",',
        '    categories: [IconCategory.GAMES],',
        '    figma_category: FigmaCategory.GAMES,',
        '    tags: [],',
        '    codepoint: 1,',
        '  },',
        ']);',
        '',
    ]
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor"><path d="M0 0h256v256H0z"/></svg>'


def archive(names=("acorn", "address-book", "ghost"), extra=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        def add(path, data):
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        for name in names:
            add(f"core-abc/assets/regular/{name}.svg", SVG)
        add("core-abc/assets/bold/acorn-bold.svg", b"<svg/>")
        add("core-abc/src/icons.ts", ICONS_TS.encode())
        add("core-abc/LICENSE", b"MIT License\n")
        for path, data in extra:
            add(path, data)
    return buffer.getvalue()


class ParsingTests(unittest.TestCase):
    def test_names_categories_and_tags_are_read(self):
        entries = update.parse_icons_ts(ICONS_TS)
        self.assertEqual(sorted(entries), ["acorn", "address-book", "ghost"])
        self.assertEqual(entries["acorn"], {"categories": ["finance", "nature"], "tags": ["savings", "nut"]})
        self.assertEqual(entries["address-book"]["tags"], ["contacts", "directory"])
        self.assertEqual(entries["ghost"]["tags"], [])

    def test_release_markers_are_not_tags(self):
        self.assertNotIn("*new*", update.parse_icons_ts(ICONS_TS)["acorn"]["tags"])

    def test_an_empty_text_gives_no_entry(self):
        self.assertEqual(update.parse_icons_ts(""), {})


class ArchiveTests(unittest.TestCase):
    def test_only_the_regular_weight_is_kept(self):
        svgs, icons_ts, license_text = update.read_archive(archive())
        self.assertEqual(sorted(svgs), ["acorn", "address-book", "ghost"])
        self.assertEqual(svgs["acorn"], SVG)
        self.assertIn("export const icons", icons_ts)
        self.assertEqual(license_text, "MIT License\n")

    def test_files_outside_the_expected_folders_are_ignored(self):
        data = archive(extra=[("core-abc/assets/regular/sub/deep.svg", SVG), ("core-abc/assets/regular/readme.txt", b"x")])
        svgs, _icons_ts, _license = update.read_archive(data)
        self.assertEqual(sorted(svgs), ["acorn", "address-book", "ghost"])

    def test_a_missing_part_is_refused(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo("core-abc/LICENSE")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(SystemExit):
            update.read_archive(buffer.getvalue())


class DigestTests(unittest.TestCase):
    def test_the_digest_does_not_depend_on_the_order(self):
        first = update.content_digest({"a": b"1", "b": b"2"})
        second = update.content_digest({"b": b"2", "a": b"1"})
        self.assertEqual(first, second)

    def test_any_change_changes_the_digest(self):
        base = update.content_digest({"a": b"1", "b": b"2"})
        self.assertNotEqual(base, update.content_digest({"a": b"1", "b": b"3"}))
        self.assertNotEqual(base, update.content_digest({"a": b"1", "c": b"2"}))
        self.assertNotEqual(base, update.content_digest({"a": b"1"}))

    def test_a_name_cannot_be_confused_with_content(self):
        self.assertNotEqual(update.content_digest({"ab": b"c"}), update.content_digest({"a": b"bc"}))


class WritingTests(unittest.TestCase):
    def test_the_set_is_written_with_its_index_and_license(self):
        svgs, icons_ts, license_text = update.read_archive(archive())
        index = update.build_index(svgs, update.parse_icons_ts(icons_ts))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "old-symbolic.svg").write_text("<svg/>")
            (target / "index.json").write_text("{}")
            update.write_set(target, svgs, license_text, index)
            self.assertFalse((target / "old-symbolic.svg").exists())
            self.assertEqual(sorted(p.name for p in target.glob("*.svg")), ["acorn.svg", "address-book.svg", "ghost.svg"])
            self.assertEqual((target / "LICENSE").read_text(), "MIT License\n")
            written = json.loads((target / "index.json").read_text())
            self.assertEqual(written, index)

    def test_the_index_lists_every_icon_with_its_tags_and_the_revision(self):
        svgs, icons_ts, _license = update.read_archive(archive())
        index = update.build_index(svgs, update.parse_icons_ts(icons_ts))
        self.assertEqual(index["revision"], update.REVISION)
        self.assertEqual(index["icons"]["acorn"], {"c": ["finance", "nature"], "t": ["savings", "nut"]})
        self.assertEqual(sorted(index["icons"]), ["acorn", "address-book", "ghost"])

    def test_an_icon_without_a_catalog_entry_is_kept_with_no_tags(self):
        svgs = {"mystery": SVG}
        index = update.build_index(svgs, {})
        self.assertEqual(index["icons"]["mystery"], {"c": [], "t": []})

    def test_the_pinned_commit_is_a_full_hash(self):
        self.assertRegex(update.COMMIT, r"^[0-9a-f]{40}$")
        self.assertRegex(update.CONTENT_DIGEST, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
