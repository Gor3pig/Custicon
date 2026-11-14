import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "build-aux" / "update-icon-sets.py"
spec = importlib.util.spec_from_file_location("update_icon_sets", SCRIPT)
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor"><path d="M0 0h256v256H0z"/></svg>'
ICONS_TS = "\n".join(
    [
        "export const icons = (<const>[",
        "  {",
        '    name: "acorn",',
        '    pascal_name: "Acorn",',
        "    categories: [IconCategory.FINANCE, IconCategory.NATURE],",
        "    figma_category: FigmaCategory.WEATHER,",
        "    tags: [",
        '      "*new*",',
        '      "savings",',
        '      "nut",',
        "    ],",
        "    codepoint: 60314,",
        "  },",
        "  {",
        '    name: "address-book",',
        '    pascal_name: "AddressBook",',
        "    categories: [IconCategory.COMMUNICATION],",
        "    figma_category: FigmaCategory.COMMUNICATION,",
        '    tags: ["contacts", "directory"],',
        "    codepoint: 59128,",
        "  },",
        "  {",
        '    name: "ghost",',
        '    pascal_name: "Ghost",',
        "    categories: [IconCategory.GAMES],",
        "    figma_category: FigmaCategory.GAMES,",
        "    tags: [],",
        "    codepoint: 1,",
        "  },",
        "]);",
    ]
)

PHOSPHOR_FILES = {
    "assets/regular/acorn.svg": SVG,
    "assets/regular/address-book.svg": SVG,
    "assets/regular/ghost.svg": SVG,
    "assets/bold/acorn-bold.svg": b"<svg/>",
    "assets/regular/sub/deep.svg": SVG,
    "src/icons.ts": ICONS_TS.encode(),
    "LICENSE": b"MIT License\n",
    "README.md": b"ignored",
}

FLUENT_SVG = b'<svg width="32" height="32" viewBox="0 0 32 32" fill="none"><path d="M1 1h2v2H1z" fill="#212121"/></svg>'


def meta(cldr, glyph, group, keywords):
    return json.dumps({"cldr": cldr, "glyph": glyph, "group": group, "keywords": keywords}).encode()


EMOJI_FILES = {
    "assets/Grinning face/metadata.json": meta("grinning face", "😀", "Smileys & Emotion", ["face", "grin", "grinning face"]),
    "assets/Grinning face/High Contrast/grinning_face_high_contrast.svg": FLUENT_SVG,
    "assets/Grinning face/Color/grinning_face_color.svg": b"<svg/>",
    "assets/Waving hand/metadata.json": meta("waving hand", "👋", "People & Body", ["hand", "wave"]),
    "assets/Waving hand/Default/High Contrast/waving_hand_high_contrast_default.svg": FLUENT_SVG,
    "assets/Waving hand/Light/High Contrast/waving_hand_high_contrast_light.svg": b"<svg/>",
    "assets/Heart on fire/metadata.json": meta("heart on fire", "❤️‍🔥", "Smileys & Emotion", ["fire"]),
    "assets/Heart on fire/High Contrast/heart_on_fire_high_contrast.svg": FLUENT_SVG,
    "assets/Only color/metadata.json": meta("only color", "🎨", "Objects", ["art"]),
    "assets/Only color/Color/only_color_color.svg": b"<svg/>",
    "LICENSE": b"MIT License Microsoft\n",
}

ANNOTATIONS = "\n".join(
    [
        '<?xml version="1.0" encoding="UTF-8" ?>',
        '<!DOCTYPE ldml SYSTEM "../../common/dtd/ldml.dtd">',
        "<ldml><annotations>",
        '<annotation cp="😀">visage | sourire | content</annotation>',
        '<annotation cp="😀" type="tts">visage rieur</annotation>',
        '<annotation cp="👋">main | salut</annotation>',
        '<annotation cp="👋" type="tts">main qui salue</annotation>',
        '<annotation cp="❤‍🔥">coeur | feu</annotation>',
        '<annotation cp="❤‍🔥" type="tts">cœur en feu</annotation>',
        "</annotations></ldml>",
    ]
).encode()
CLDR_LICENSE = "\n".join(
    [
        "COPYRIGHT AND PERMISSION NOTICE",
        "",
        "All other content in this repo is released under UNICODE LICENSE V3",
        "",
        "Permission is hereby granted, free of charge.",
        "",
        "SPDX-License-Identifier: Unicode-3.0",
    ]
).encode()

BRAND_FILES = {
    "icons/github.svg": b'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>GitHub</title><path d="M0 0h24v24H0z"/></svg>',
    "icons/dotenv.svg": b'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>.ENV</title><path d="M0 0h24v24H0z"/></svg>',
    "icons/nested/other.svg": b"<svg/>",
    "data/simple-icons.json": json.dumps([{"title": ".ENV", "hex": "ECD53F", "aliases": {"aka": ["Dotenv"], "loc": {"fr-FR": "Dot env"}}}, {"title": "GitHub", "hex": "181717"}]).encode(),
    "LICENSE.md": b"CC0 1.0\n",
    "DISCLAIMER.md": b"Trademarks belong to their owners\n",
    "README.md": b"ignored",
}


class SlugTests(unittest.TestCase):
    def test_a_name_becomes_a_file_name(self):
        self.assertEqual(update.slugify("1st place medal"), "1st-place-medal")
        self.assertEqual(update.slugify("Man's shoe & sock!"), "man-s-shoe-sock")
        self.assertEqual(update.slugify("  --Already-slug-- "), "already-slug")

    def test_nothing_usable_gives_an_empty_slug(self):
        self.assertEqual(update.slugify("!!!"), "")


class PhosphorParsingTests(unittest.TestCase):
    def test_names_categories_and_tags_are_read(self):
        entries = update.parse_icons_ts(ICONS_TS)
        self.assertEqual(sorted(entries), ["acorn", "address-book", "ghost"])
        self.assertEqual(entries["acorn"], {"categories": ["finance", "nature"], "tags": ["savings", "nut"]})
        self.assertEqual(entries["ghost"]["tags"], [])

    def test_an_empty_text_gives_no_entry(self):
        self.assertEqual(update.parse_icons_ts(""), {})


class SelectionTests(unittest.TestCase):
    def test_phosphor_keeps_only_the_regular_icons_the_catalog_and_the_license(self):
        wanted = update.SOURCES["phosphor"].wanted
        self.assertTrue(wanted("assets/regular/house.svg"))
        self.assertTrue(wanted("src/icons.ts"))
        self.assertTrue(wanted("LICENSE"))
        for path in ("assets/bold/house-bold.svg", "assets/regular/sub/x.svg", "README.md", "assets/regular/readme.txt"):
            self.assertFalse(wanted(path), path)

    def test_emoji_keeps_the_monochrome_icons_and_their_metadata(self):
        wanted = update.SOURCES["emoji"].wanted
        self.assertTrue(wanted("assets/Grinning face/High Contrast/grinning_face_high_contrast.svg"))
        self.assertTrue(wanted("assets/Waving hand/Default/High Contrast/waving_hand_high_contrast_default.svg"))
        self.assertTrue(wanted("assets/Waving hand/metadata.json"))
        self.assertTrue(wanted("LICENSE"))
        for path in ("assets/Grinning face/Color/grinning_face_color.svg", "assets/Waving hand/Light/High Contrast/x.svg", "assets/Grinning face/3D/x.png"):
            self.assertFalse(wanted(path), path)

    def test_brands_keep_the_icons_the_data_and_the_texts(self):
        wanted = update.SOURCES["brands"].wanted
        for path in ("icons/github.svg", "data/simple-icons.json", "LICENSE.md", "DISCLAIMER.md"):
            self.assertTrue(wanted(path), path)
        for path in ("icons/nested/other.svg", "README.md", "icons/readme.txt"):
            self.assertFalse(wanted(path), path)


class PhosphorBuildTests(unittest.TestCase):
    def test_the_regular_icons_are_kept_with_the_catalog(self):
        files = {path: data for path, data in PHOSPHOR_FILES.items() if update.SOURCES["phosphor"].wanted(path)}
        built = update.build_phosphor(files)
        self.assertEqual(sorted(built.svgs), ["acorn", "address-book", "ghost"])
        self.assertEqual(built.licenses, {"LICENSE": "MIT License\n"})
        self.assertEqual(built.index["revision"], update.SOURCES["phosphor"].revision)
        self.assertEqual(built.index["icons"]["acorn"], {"c": ["finance", "nature"], "t": ["savings", "nut"]})

    def test_an_icon_without_a_catalog_entry_is_kept_with_no_tags(self):
        files = {"assets/regular/mystery.svg": SVG, "src/icons.ts": b"", "LICENSE": b"x"}
        self.assertEqual(update.build_phosphor(files).index["icons"]["mystery"], {"c": [], "t": []})

    def test_a_missing_part_is_refused(self):
        with self.assertRaises(SystemExit):
            update.build_phosphor({"LICENSE": b"x"})


class AnnotationTests(unittest.TestCase):
    def test_french_keywords_and_names_are_read_by_glyph(self):
        found = update.parse_annotations(ANNOTATIONS)
        self.assertEqual(found["😀"], {"keywords": ["visage", "sourire", "content"], "name": "visage rieur"})
        self.assertEqual(found["👋"]["name"], "main qui salue")

    def test_the_variation_selector_does_not_matter(self):
        found = update.parse_annotations(ANNOTATIONS)
        self.assertIn(update.plain_glyph("❤️‍🔥"), found)
        self.assertEqual(update.plain_glyph("❤️"), update.plain_glyph("❤"))

    def test_a_broken_document_is_refused(self):
        with self.assertRaises(SystemExit):
            update.parse_annotations(b"<ldml")


class EmojiBuildTests(unittest.TestCase):
    def build(self, files=None):
        selected = {path: data for path, data in (files or EMOJI_FILES).items() if update.SOURCES["emoji"].wanted(path)}
        return update.build_emoji(selected, ANNOTATIONS, CLDR_LICENSE.decode())

    def test_each_emoji_with_a_monochrome_icon_is_kept(self):
        built = self.build()
        self.assertEqual(sorted(built.svgs), ["grinning-face", "heart-on-fire", "waving-hand"])

    def test_an_emoji_without_a_monochrome_icon_is_left_out(self):
        self.assertNotIn("only-color", self.build().svgs)

    def test_the_default_skin_tone_is_used_for_people(self):
        self.assertEqual(self.build().svgs["waving-hand"], FLUENT_SVG)

    def test_tags_mix_english_and_french_keywords(self):
        entry = self.build().index["icons"]["grinning-face"]
        for word in ("face", "grin", "visage", "sourire", "content"):
            self.assertIn(word, entry["t"])
        self.assertEqual(len(entry["t"]), len(set(entry["t"])))

    def test_names_are_given_in_both_languages(self):
        entry = self.build().index["icons"]["grinning-face"]
        self.assertEqual(entry["n"], {"en": "grinning face", "fr": "visage rieur"})

    def test_an_emoji_with_a_variation_selector_finds_its_french_text(self):
        self.assertEqual(self.build().index["icons"]["heart-on-fire"]["n"]["fr"], "cœur en feu")

    def test_the_group_is_the_category(self):
        icons = self.build().index["icons"]
        self.assertEqual(icons["grinning-face"]["c"], ["smileys"])
        self.assertEqual(icons["waving-hand"]["c"], ["people"])

    def test_an_unknown_group_becomes_a_category_of_its_own(self):
        files = dict(EMOJI_FILES)
        files["assets/Only color/metadata.json"] = meta("only color", "🎨", "Some New Group", ["art"])
        files["assets/Only color/High Contrast/only_color_high_contrast.svg"] = FLUENT_SVG
        self.assertEqual(self.build(files).index["icons"]["only-color"]["c"], ["some-new-group"])

    def test_an_emoji_without_a_group_has_no_category(self):
        files = dict(EMOJI_FILES)
        files["assets/Only color/metadata.json"] = meta("only color", "🎨", "", ["art"])
        files["assets/Only color/High Contrast/only_color_high_contrast.svg"] = FLUENT_SVG
        self.assertEqual(self.build(files).index["icons"]["only-color"]["c"], [])

    def test_an_emoji_without_french_text_keeps_english_only(self):
        annotations = update.parse_annotations(ANNOTATIONS)
        annotations.pop("😀")
        files = {path: data for path, data in EMOJI_FILES.items() if update.SOURCES["emoji"].wanted(path)}
        built = update.build_emoji(files, annotations, CLDR_LICENSE.decode())
        self.assertEqual(built.index["icons"]["grinning-face"]["n"], {"en": "grinning face"})

    def test_the_licenses_of_the_icons_and_of_the_french_text_are_kept(self):
        licenses = self.build().licenses
        self.assertEqual(licenses["LICENSE"], "MIT License Microsoft\n")
        self.assertIn("UNICODE LICENSE V3", licenses["LICENSE-CLDR"])
        self.assertTrue(licenses["LICENSE-CLDR"].startswith("Copyright"))

    def test_two_emoji_with_the_same_slug_get_distinct_names(self):
        files = dict(EMOJI_FILES)
        files["assets/Grinning Face/metadata.json"] = meta("Grinning face", "😃", "Smileys & Emotion", [])
        files["assets/Grinning Face/High Contrast/grinning_face_high_contrast.svg"] = FLUENT_SVG
        built = self.build(files)
        self.assertEqual(len([name for name in built.svgs if name.startswith("grinning-face")]), 2)

    def test_a_missing_license_is_refused(self):
        files = {path: data for path, data in EMOJI_FILES.items() if update.SOURCES["emoji"].wanted(path) and path != "LICENSE"}
        with self.assertRaises(SystemExit):
            update.build_emoji(files, ANNOTATIONS, CLDR_LICENSE.decode())

    def test_a_license_text_without_the_unicode_part_is_refused(self):
        files = {path: data for path, data in EMOJI_FILES.items() if update.SOURCES["emoji"].wanted(path)}
        with self.assertRaises(SystemExit):
            update.build_emoji(files, ANNOTATIONS, "nothing useful here")


class BrandBuildTests(unittest.TestCase):
    def build(self):
        selected = {path: data for path, data in BRAND_FILES.items() if update.SOURCES["brands"].wanted(path)}
        return update.build_brands(selected)

    def test_each_logo_is_kept_under_its_file_name(self):
        self.assertEqual(sorted(self.build().svgs), ["dotenv", "github"])

    def test_the_title_comes_from_the_icon_itself(self):
        icons = self.build().index["icons"]
        self.assertEqual(icons["github"]["n"], {"en": "GitHub"})
        self.assertEqual(icons["dotenv"]["n"], {"en": ".ENV"})

    def test_tags_hold_the_title_words_and_the_aliases(self):
        icons = self.build().index["icons"]
        self.assertIn("github", icons["github"]["t"])
        self.assertIn("dotenv", icons["dotenv"]["t"])
        self.assertIn("dot env", icons["dotenv"]["t"])

    def test_the_license_and_the_trademark_notice_are_kept(self):
        licenses = self.build().licenses
        self.assertEqual(licenses["LICENSE"], "CC0 1.0\n")
        self.assertEqual(licenses["DISCLAIMER"], "Trademarks belong to their owners\n")

    def test_an_xml_entity_in_the_title_is_decoded(self):
        files = {"icons/att.svg": b"<svg><title>AT&amp;T</title></svg>", "LICENSE.md": b"x", "DISCLAIMER.md": b"y", "data/simple-icons.json": b"[]"}
        icons = update.build_brands(files).index["icons"]
        self.assertEqual(icons["att"]["n"], {"en": "AT&T"})
        self.assertIn("at&t", icons["att"]["t"])

    def test_a_logo_without_a_title_uses_its_file_name(self):
        files = {"icons/plain.svg": b"<svg/>", "LICENSE.md": b"x", "DISCLAIMER.md": b"y", "data/simple-icons.json": b"[]"}
        self.assertEqual(update.build_brands(files).index["icons"]["plain"]["n"], {"en": "plain"})

    def test_a_broken_data_file_is_refused(self):
        files = dict(BRAND_FILES)
        files["data/simple-icons.json"] = b"{broken"
        with self.assertRaises(SystemExit):
            update.build_brands({path: data for path, data in files.items() if update.SOURCES["brands"].wanted(path)})


class ExclusionTests(unittest.TestCase):
    def test_an_excluded_logo_is_left_out(self):
        import hashlib

        excluded = frozenset({hashlib.sha256(b"github").hexdigest()})
        selected = {path: data for path, data in BRAND_FILES.items() if update.SOURCES["brands"].wanted(path)}
        built = update.build_brands(selected, excluded)
        self.assertEqual(sorted(built.svgs), ["dotenv"])
        self.assertNotIn("github", built.index["icons"])

    def test_without_an_exclusion_every_logo_is_kept(self):
        selected = {path: data for path, data in BRAND_FILES.items() if update.SOURCES["brands"].wanted(path)}
        self.assertEqual(sorted(update.build_brands(selected, frozenset()).svgs), ["dotenv", "github"])

    def test_the_default_exclusions_are_hashes_not_names(self):
        self.assertEqual(len(update.EXCLUDED_BRANDS), 3)
        for digest in update.EXCLUDED_BRANDS:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_the_default_list_keeps_ordinary_logos(self):
        selected = {path: data for path, data in BRAND_FILES.items() if update.SOURCES["brands"].wanted(path)}
        self.assertEqual(sorted(update.build_brands(selected).svgs), ["dotenv", "github"])

    def test_the_exclusions_apply_by_default(self):
        import inspect

        self.assertIs(inspect.signature(update.build_brands).parameters["excluded"].default, update.EXCLUDED_BRANDS)

    def test_a_name_is_recognised_by_its_hash(self):
        import hashlib

        digests = frozenset({hashlib.sha256(b"github").hexdigest()})
        self.assertTrue(update.is_excluded("github", digests))
        self.assertFalse(update.is_excluded("githubx", digests))
        self.assertFalse(update.is_excluded("github", frozenset()))


class DigestTests(unittest.TestCase):
    def test_the_digest_does_not_depend_on_the_order(self):
        self.assertEqual(update.content_digest({"a": b"1", "b": b"2"}), update.content_digest({"b": b"2", "a": b"1"}))

    def test_any_change_changes_the_digest(self):
        base = update.content_digest({"a": b"1", "b": b"2"})
        self.assertNotEqual(base, update.content_digest({"a": b"1", "b": b"3"}))
        self.assertNotEqual(base, update.content_digest({"a": b"1", "c": b"2"}))
        self.assertNotEqual(base, update.content_digest({"a": b"1"}))

    def test_a_name_cannot_be_confused_with_content(self):
        self.assertNotEqual(update.content_digest({"ab": b"c"}), update.content_digest({"a": b"bc"}))


class WritingTests(unittest.TestCase):
    def test_the_set_is_written_with_its_index_and_licenses(self):
        files = {path: data for path, data in PHOSPHOR_FILES.items() if update.SOURCES["phosphor"].wanted(path)}
        built = update.build_phosphor(files)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "old-symbolic.svg").write_text("<svg/>")
            (target / "index.json").write_text("{}")
            (target / "LICENSE-OLD").write_text("old")
            update.write_set(target, built)
            self.assertFalse((target / "old-symbolic.svg").exists())
            self.assertFalse((target / "LICENSE-OLD").exists())
            self.assertEqual(sorted(p.name for p in target.glob("*.svg")), ["acorn.svg", "address-book.svg", "ghost.svg"])
            self.assertEqual((target / "LICENSE").read_text(), "MIT License\n")
            self.assertEqual(json.loads((target / "index.json").read_text()), built.index)

    def test_the_output_is_deterministic(self):
        files = {path: data for path, data in PHOSPHOR_FILES.items() if update.SOURCES["phosphor"].wanted(path)}
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            update.write_set(Path(first), update.build_phosphor(files))
            update.write_set(Path(second), update.build_phosphor(dict(reversed(list(files.items())))))
            for name in ("index.json", "acorn.svg", "LICENSE"):
                self.assertEqual((Path(first) / name).read_bytes(), (Path(second) / name).read_bytes(), name)


class PinningTests(unittest.TestCase):
    def test_every_source_is_pinned_to_a_full_commit_and_a_digest(self):
        for name, source in update.SOURCES.items():
            self.assertRegex(source.commit, r"^[0-9a-f]{40}$", name)
            self.assertRegex(source.digest, r"^[0-9a-f]{64}$", name)
            for repository, commit, paths in source.extra:
                self.assertRegex(commit, r"^[0-9a-f]{40}$", name)

    def test_every_source_has_its_own_folder_and_revision(self):
        folders = [source.directory for source in update.SOURCES.values()]
        self.assertEqual(len(set(folders)), len(folders))
        for source in update.SOURCES.values():
            self.assertIsInstance(source.revision, int)
            self.assertGreaterEqual(source.revision, 1)


class CacheTests(unittest.TestCase):
    def test_a_cached_file_is_not_downloaded_again(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return b"data"

        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            first = update.fetch_file("owner/repo", "a" * 40, "dir/file.svg", cache, fetch)
            second = update.fetch_file("owner/repo", "a" * 40, "dir/file.svg", cache, fetch)
            self.assertEqual((first, second), (b"data", b"data"))
            self.assertEqual(len(calls), 1)
            self.assertIn("owner/repo/" + "a" * 40 + "/dir/file.svg", calls[0])

    def test_a_path_cannot_leave_the_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SystemExit):
                update.fetch_file("owner/repo", "a" * 40, "../../escape", Path(folder), lambda url: b"x")

    def test_without_a_cache_the_file_is_always_downloaded(self):
        calls = []
        update.fetch_file("o/r", "b" * 40, "f", None, lambda url: calls.append(url) or b"x")
        update.fetch_file("o/r", "b" * 40, "f", None, lambda url: calls.append(url) or b"x")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
