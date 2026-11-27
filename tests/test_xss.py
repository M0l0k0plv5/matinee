"""Names, titles and texts from the services (Jellyfin, Navidrome, Audiobookshelf, Podfetch) that carry
HTML: shown as text on every page, never made into elements of the page (a title on someone's server is no code)."""
import json
import time
import unittest

from test_ui import HAVE_CHROMIUM, UiBase, UiLayoutTest

# out of a text and out of an attribute in double quotes alike; an element of its own if either lets it through
MARK = "\"'><img src=x data-xss=1>"
# the fields people see, by their names in the services' answers (ids and paths left as they are: they are asked for)
TEXT_KEYS = {"Name", "Overview", "SeriesName", "Album", "AlbumArtist", "Taglines", "OriginalTitle", "SortName",
             "name", "title", "artist", "album", "description", "topic", "author", "narrator", "subtitle", "summary",
             "collectionName", "artistName", "subline", "synopsis"}
LIST_KEYS = {"Artists", "Genres", "Tags"}


def poison(x, seen):
    if id(x) in seen:
        return
    seen.add(id(x))
    if isinstance(x, dict):
        for k, v in list(x.items()):
            if isinstance(v, str) and k in TEXT_KEYS and v:
                x[k] = v + MARK
            elif isinstance(v, list) and k in LIST_KEYS and all(isinstance(i, str) for i in v):
                x[k] = [i + MARK for i in v]
            else:
                poison(v, seen)
    elif isinstance(x, list):
        for v in x:
            poison(v, seen)


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiXssTest(UiBase):
    navidrome = True
    audiobookshelf = True
    podfetch = True
    section = None

    PAGES = UiLayoutTest.PAGES + [("podcasts", {}), ("podcast", {"id": "p1"}), ("podsearch", {})]

    def setUp(self):
        super().setUp()  # Jellyfin's library made anew for each test: given the names after it
        seen = set()
        for mock in (self.mock.lib, self.nd, self.abs, self.pf):
            for v in vars(mock).values() if mock else ():
                if isinstance(v, (dict, list)):
                    poison(v, seen)
        self.b.js("connect()")
        self.b.wait_js("USER", msg="signed in to Jellyfin")

    def test_no_text_of_a_service_becomes_an_element(self):
        found, plain = {}, []
        for name, args in self.PAGES:
            self.b.js(f"render({{name: {json.dumps(name)}, args: {json.dumps(args)}}})")
            try:
                self.b.wait_js(f"current.name === {json.dumps(name)} && !document.body.classList.contains('busy')",
                               timeout=15, msg=name)
            except AssertionError:
                pass  # a page that does not come up with such names is another test's business: what it shows counts
            time.sleep(0.8)  # the parts that come later (search results, the shelf, pictures)
            if self.b.js("view.textContent.includes('<img src=x data-xss=1>')"):
                plain.append(name)  # there as the text it is
            n = self.b.js("document.querySelectorAll('[data-xss]').length")
            if n:
                where = self.b.js("[...document.querySelectorAll('[data-xss]')].map((e) =>"
                                  " (e.parentElement?.className || e.parentElement?.tagName) + ': '"
                                  " + (e.parentElement?.outerHTML || '').slice(0, 160)).slice(0, 3)")
                found[f"{name} {json.dumps(args)}"] = where
        self.assertEqual(found, {}, "HTML from a service made into elements")
        # the names reached these pages as text: the test looked at something (the settings'
        # come from elsewhere)
        self.assertLessEqual({"home", "item", "library", "search", "music", "album", "artist", "playlist", "books", "book",
                              "podcasts", "podcast"}, set(plain))


if __name__ == "__main__":
    unittest.main()
