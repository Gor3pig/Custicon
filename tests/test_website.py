import json
import os
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urlparse

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "data" / "website"
PAGES = {"en": SITE / "index.html", "fr": SITE / "fr" / "index.html"}
SCREENS = ("picker", "phosphor", "emoji", "brands", "tint", "choosing", "states")
GENERATED = {"images/custicon-64x64.png", "images/custicon-256x256.png", "images/share.png", "custicon.flatpakref"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()
        self.thumbs = []
        self.hreflangs = {}
        self.scripts = []

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if "id" in values:
            self.ids.add(values["id"])
        if tag in ("a", "link") and values.get("href"):
            self.links.append(values["href"])
            if tag == "link" and values.get("rel") == "alternate":
                self.hreflangs[values.get("hreflang")] = values["href"]
        if tag in ("img", "script") and values.get("src"):
            self.links.append(values["src"])
            if tag == "script":
                self.scripts.append(values["src"])
        if "data-thumb" in values:
            self.thumbs.append(values["data-thumb"])


def parse(code):
    page = Page()
    page.feed(PAGES[code].read_text(encoding="utf-8"))
    return page


class StaticSiteTests(unittest.TestCase):
    def resolve(self, code, link):
        base = PAGES[code].parent
        return (base / link).resolve()

    def local_links(self, code):
        for link in parse(code).links:
            target = urldefrag(link)[0]
            if not target or urlparse(target).scheme:
                continue
            yield link, target

    def test_every_local_file_a_page_uses_exists(self):
        for code in PAGES:
            for link, target in self.local_links(code):
                relative = self.resolve(code, target).relative_to(SITE.resolve()).as_posix()
                if relative in GENERATED:
                    continue
                self.assertTrue(self.resolve(code, target).exists(), (code, link))

    def test_every_thumbnail_exists_and_is_small(self):
        for code in PAGES:
            thumbs = parse(code).thumbs
            self.assertEqual(len(thumbs), len(SCREENS))
            for thumb in thumbs:
                path = self.resolve(code, thumb)
                self.assertTrue(path.exists(), thumb)
                self.assertLess(path.stat().st_size, 40_000, thumb)

    def test_each_page_shows_the_screens_in_its_own_language(self):
        for code in PAGES:
            text = PAGES[code].read_text(encoding="utf-8")
            for screen in SCREENS:
                self.assertIn(f"{code}-{screen}.webp", text)
            other = "fr" if code == "en" else "en"
            self.assertNotIn(f"{other}-main.webp", text)

    def test_the_two_pages_point_to_each_other(self):
        for code in PAGES:
            page = parse(code)
            self.assertEqual(page.hreflangs["en"], "https://gor3pig.github.io/Custicon/")
            self.assertEqual(page.hreflangs["fr"], "https://gor3pig.github.io/Custicon/fr/")
        self.assertIn('data-language="fr"', PAGES["en"].read_text(encoding="utf-8"))
        self.assertIn('data-language="en"', PAGES["fr"].read_text(encoding="utf-8"))

    def test_the_automatic_language_script_only_runs_on_the_english_page(self):
        self.assertIn("language.js", parse("en").scripts)
        self.assertNotIn("../language.js", parse("fr").scripts)

    def test_every_anchor_of_a_page_exists(self):
        for code in PAGES:
            page = parse(code)
            for link in page.links:
                if link.startswith("#"):
                    self.assertIn(link[1:], page.ids, (code, link))

    def test_the_french_anchor_map_targets_the_french_install_section(self):
        script = (SITE / "language.js").read_text(encoding="utf-8")
        match = re.search(r'FRENCH_ANCHORS = (\{.*?\});', script)
        mapping = json.loads(match.group(1).replace("#", "#"))
        for source, target in mapping.items():
            self.assertIn(source[1:], parse("en").ids)
            self.assertIn(target[1:], parse("fr").ids)

    def test_the_images_are_light_enough(self):
        for path in (SITE / "images").glob("*.webp"):
            self.assertLess(path.stat().st_size, 90_000, path.name)

    def test_the_sitemap_lists_both_pages(self):
        text = (SITE / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("<loc>https://gor3pig.github.io/Custicon/</loc>", text)
        self.assertIn("<loc>https://gor3pig.github.io/Custicon/fr/</loc>", text)

    def test_the_stored_language_key_is_the_same_everywhere(self):
        for name in ("language.js", "script.js"):
            self.assertIn('"custicon-language"', (SITE / name).read_text(encoding="utf-8"))


def webkit_available() -> bool:
    if os.environ.get("GDK_BACKEND") != "broadway":
        return False
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gtk, WebKit

        return bool(Gtk.init_check()) and WebKit is not None
    except (ImportError, ValueError):
        return False


@unittest.skipUnless(webkit_available(), "needs WebKit on a Broadway display")
class BrowserTests(unittest.TestCase):
    def setUp(self):
        from gi.repository import GLib, WebKit

        self.glib, self.webkit = GLib, WebKit
        self.views = []
        self.addCleanup(self.close_views)

    def close_views(self):
        self.views.clear()

    def pump(self, condition, timeout=8.0):
        context = self.glib.MainContext.default()
        end = self.glib.get_monotonic_time() + timeout * 1_000_000
        while not condition() and self.glib.get_monotonic_time() < end:
            context.iteration(False)
        return condition()

    def open(self, page, languages, stored=None, fragment=""):
        manager = self.webkit.UserContentManager()
        source = "Object.defineProperty(navigator, 'languages', {get: () => %s});" % json.dumps(languages)
        if stored is not None:
            source += "try { localStorage.setItem('custicon-language', %s); } catch (e) {}" % json.dumps(stored)
        manager.add_script(
            self.webkit.UserScript.new(source, self.webkit.UserContentInjectedFrames.ALL_FRAMES, self.webkit.UserScriptInjectionTime.START, None, None)
        )
        session = self.webkit.NetworkSession.new_ephemeral()
        view = self.webkit.WebView(network_session=session, user_content_manager=manager)
        view.set_size_request(1000, 800)
        self.views.append(view)
        done = []
        view.connect("load-changed", lambda _view, event: done.append(event) if event == self.webkit.LoadEvent.FINISHED else None)
        view.load_uri(page.as_uri() + fragment)
        self.assertTrue(self.pump(lambda: done), "page did not load")
        return view

    def settle_on(self, view, suffix):
        return self.pump(lambda: view.get_uri() and view.get_uri().split("#")[0].endswith(suffix))

    def evaluate(self, view, script):
        result = []
        view.evaluate_javascript(script, -1, None, None, None, lambda source, task: result.append(source.evaluate_javascript_finish(task)))
        self.assertTrue(self.pump(lambda: result))
        text = result[0].to_json(0)
        return json.loads(text) if text else None

    def test_a_french_browser_is_sent_to_the_french_page(self):
        view = self.open(PAGES["en"], ["fr-FR", "en"])
        self.assertTrue(self.settle_on(view, "/fr/index.html") or view.get_uri().endswith("/fr/"), view.get_uri())

    def test_an_english_browser_stays_on_the_english_page(self):
        view = self.open(PAGES["en"], ["en-US"])
        self.assertTrue(view.get_uri().endswith("/data/website/index.html"), view.get_uri())

    def test_an_unsupported_language_falls_back_to_english(self):
        view = self.open(PAGES["en"], ["de-DE"])
        self.assertTrue(view.get_uri().endswith("/data/website/index.html"), view.get_uri())

    def test_the_first_supported_language_wins(self):
        view = self.open(PAGES["en"], ["de", "fr", "en"])
        self.assertTrue(self.pump(lambda: "/fr/" in (view.get_uri() or "")), view.get_uri())

    def test_a_remembered_english_choice_beats_a_french_browser(self):
        view = self.open(PAGES["en"], ["fr-FR"], stored="en")
        self.assertTrue(view.get_uri().endswith("/data/website/index.html"), view.get_uri())

    def test_a_remembered_french_choice_beats_an_english_browser(self):
        view = self.open(PAGES["en"], ["en-US"], stored="fr")
        self.assertTrue(self.pump(lambda: "/fr/" in (view.get_uri() or "")), view.get_uri())

    def test_the_install_anchor_follows_the_redirection(self):
        view = self.open(PAGES["en"], ["fr"], fragment="#install")
        self.assertTrue(self.pump(lambda: (view.get_uri() or "").endswith("/fr/#installer")), view.get_uri())

    def test_choosing_a_language_by_the_link_remembers_it(self):
        view = self.open(PAGES["fr"], ["fr-FR"])
        self.evaluate(view, "document.querySelector('[data-language=en]').addEventListener('click', e => e.preventDefault()); document.querySelector('[data-language=en]').click()")
        self.assertEqual(self.evaluate(view, "localStorage.getItem('custicon-language')"), "en")

    def test_the_carousel_is_enhanced_and_shows_one_slide(self):
        view = self.open(PAGES["fr"], ["fr"])
        state = self.evaluate(view, "JSON.stringify({enhanced: document.querySelector('.carousel').classList.contains('enhanced'), slides: document.querySelectorAll('.slide').length, shown: document.querySelectorAll('.slide.on').length, thumbs: document.querySelectorAll('.thumb').length})")
        self.assertEqual(json.loads(state), {"enhanced": True, "slides": len(SCREENS), "shown": 1, "thumbs": len(SCREENS)})

    def test_the_carousel_arrows_and_dots_change_the_slide(self):
        view = self.open(PAGES["fr"], ["fr"])
        current = "[...document.querySelectorAll('.slide')].findIndex(s => s.classList.contains('on'))"
        self.assertEqual(self.evaluate(view, current), 0)
        self.evaluate(view, "document.querySelector('.arrow.next').click()")
        self.assertEqual(self.evaluate(view, current), 1)
        self.evaluate(view, "document.querySelector('.arrow.prev').click()")
        self.evaluate(view, "document.querySelector('.arrow.prev').click()")
        self.assertEqual(self.evaluate(view, current), len(SCREENS) - 1)
        self.evaluate(view, "document.querySelectorAll('.thumb')[2].click()")
        self.assertEqual(self.evaluate(view, current), 2)

    def test_the_carousel_can_be_paused(self):
        view = self.open(PAGES["fr"], ["fr"])
        self.evaluate(view, "document.querySelector('.toggle').click()")
        self.assertEqual(self.evaluate(view, "document.querySelector('.carousel').dataset.playing"), "false")
        self.evaluate(view, "document.querySelector('.toggle').click()")
        self.assertEqual(self.evaluate(view, "document.querySelector('.carousel').dataset.playing"), "true")

    def test_a_screenshot_opens_in_the_lightbox(self):
        view = self.open(PAGES["fr"], ["fr"])
        self.evaluate(view, "document.querySelector('.showcase a.zoom').click()")
        self.assertTrue(self.evaluate(view, "document.querySelector('dialog.lightbox').open"))
        self.evaluate(view, "document.querySelector('dialog.lightbox').click()")
        self.assertFalse(self.evaluate(view, "document.querySelector('dialog.lightbox').open"))
