"""The same look in every part: each page of each part (in German, its longer words) checked for what slipped
through before: a size off the type scale (web/tokens.css), a heading left in the browser's own bold, a text cut off
without an ellipsis or running off the screen, a broken picture, the last tile focused at the screen's foot. One list of all that was found, page by page."""

import json
import time
import unittest
from pathlib import Path

from helpers import HAVE_CHROMIUM, HAVE_FFMPEG, HAVE_MPV
from test_ui import UiBase

CHECK = (Path(__file__).parent / "consistency.js").read_text()
# the page's last tile focused (and grown): its name and year above the screen's foot, which a TV may cut off
FOOT = """new Promise(async (ok) => {
  const c = [...document.querySelectorAll("#view .card.f")].at(-1);
  if (!c) return ok([]);
  focusEl(c);
  // until whatever slides (the page, its rows) has arrived where it was sent: the background browser draws slowly
  // (by their numbers: written out, a translate3d() is a matrix3d() and what is computed from it a matrix(), never alike)
  const at = (t) => [...new DOMMatrix(t).toFloat64Array()].map((x) => Math.round(x * 100)).join();
  const moving = () => [...document.querySelectorAll("#view [style*=transform]")].some((e) =>
    at(getComputedStyle(e).transform) !== at(e.style.transform || "none"));
  for (let n = 0; n < 50 && moving(); n++) await new Promise((d) => setTimeout(d, 100));
  await new Promise((d) => setTimeout(d, 400)); // the tile grown
  // a start's poster row is as high as the room under its title: shown from its top, as all of them (not only the last)
  const room = c.closest(".rows-track")?.parentElement, row = c.closest(".row");
  if (room && row.offsetHeight > room.clientHeight * 0.9) return ok([]);
  const foot = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--edge-bottom")) / 100 * innerHeight;
  const text = [...c.querySelectorAll(".label, .sub")].at(-1) ?? c, left = innerHeight - text.getBoundingClientRect().bottom;
  ok(left < foot - 1 ? [["at-the-foot", c.dataset.k, text.textContent.slice(0, 20), Math.round(left)]] : []);
})"""
# What a start's top shows the moment the page is there (before the focus has told it anything), and each picture
# drawn behind it and beside it from then on, kept for HERO
WATCH_HERO = """(() => {
  window.firstHero = null;
  window.drawn = { backdrop: [], stage: [] };
  for (const w of window.heroWatch ?? []) w.disconnect();
  const top = new MutationObserver(() => {
    const h = document.querySelector("#hero");
    if (h && firstHero === null) firstHero = h.textContent.trim();
  });
  top.observe(view, { childList: true });
  // the backdrop is outside the view; the stage's place comes with the page, watched once it is there
  const back = new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach(() => drawn.backdrop.push(bgUrl))));
  back.observe(document.querySelector("#backdrop"), { childList: true });
  const stage = new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) =>
    n.parentElement?.id === "stage-art" && drawn.stage.push(n.parentElement.dataset.url))));
  stage.observe(view, { childList: true, subtree: true });
  window.heroWatch = [top, back, stage];
})()"""
# The buttons at the top of a start, in every part alike: each focused, the top shows its name alone (under what it
# is, at most), never a title of what is behind it; and the page comes up with what its focused button shows, not
# with a title that the focus replaces a moment later
HERO = """new Promise(async (ok) => {
  const out = [], hero = document.querySelector(".home #hero"), chips = [...document.querySelectorAll("#view .chips .chip.lib")];
  if (!hero || !chips.length) return ok([]);
  const settle = () => new Promise((d) => setTimeout(d, HERO_WAIT + 150));
  const was = document.activeElement;
  if (was?.classList.contains("lib") && firstHero !== null && firstHero !== hero.textContent.trim())
    out.push(["hero-at-start", was.dataset.k, firstHero.slice(0, 40), hero.textContent.trim().slice(0, 40)]);
  // a picture for a moment, then the focused button's: drawn, not only asked for (the newer one wins while loading)
  for (const [where, urls] of Object.entries(drawn)) if (was?.classList.contains("lib") && new Set(urls).size > 1)
    out.push(["art-at-start", was.dataset.k, where, ...urls.map((u) => String(u).slice(0, 50))]);
  for (const c of chips) {
    focusEl(c);
    await settle();
    const title = hero.querySelector(".title")?.textContent.trim(), extra = hero.querySelector(".logo, .meta, .overview, .poster");
    if (title !== c.textContent.trim() || extra) out.push(["chip-hero", c.dataset.k, c.textContent.trim(), hero.textContent.trim().slice(0, 40)]);
  }
  if (was) focusEl(was);
  await settle();
  ok(out);
})"""
PAGES = [("menu", {}), ("home", {}), ("library", {"id": "lm", "name": "Movies", "type": "movies"}),
         ("library", {"id": "lt", "name": "Shows", "type": "tvshows"}), ("library", {"id": "lc", "name": "Collections", "type": "boxsets"}),
         ("item", {"id": "m1"}), ("item", {"id": "s1"}), ("item", {"id": "e11"}), ("item", {"id": "bx1"}),
         ("person", {"id": "p1", "name": "Actress"}), ("search", {"q": "Movie"}),
         ("ownchannels", {}), ("guide", {}),
         ("music", {}), ("albums", {}), ("artists", {}), ("artist", {"id": "ar1"}), ("album", {"id": "al1"}), ("playlists", {}),
         ("playlist", {"id": "pl1"}), ("favorites", {}), ("radio", {}), ("musicsearch", {"q": "Song"}),
         ("books", {}), ("booklib", {"id": "lib1", "name": "Stories"}), ("book", {"id": "bk1"}),
         ("podcasts", {}), ("podcat", {"cat": ""}), ("podcast", {"id": "p1"}), ("podsearch", {"q": "zeit"}),
         *[("settings", {"part": p}) for p in ("general", "basics", "av", "subs", "movies", "navidrome", "books",
                                              "podcasts", "account", "system", "saver")],
         ("transferin", {}), ("transferout", {}), ("controls", {}), ("pin", {"mode": "on"}), ("allsearch", {"q": "Movie"}),
         ("bluetooth", {}), ("libcheck", {}), ("weatherplace", {}), ("booksearch", {"q": "a"})]


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class ConsistencyTest(UiBase):
    navidrome = audiobookshelf = podfetch = True
    with_player = True
    language = "de"
    section = None
    extra_env = {"KINO_OWN_MIN": "4"}  # one's own channels and their guide

    def test_every_page_alike(self):
        found = {}
        for name, args in self.PAGES:
            self.b.js(WATCH_HERO)
            self.b.js(f"navigate({json.dumps(name)}, {json.dumps(args)})")
            self.b.wait_js(f"current.name === {json.dumps(name)} && !document.body.classList.contains('busy')", msg=name)
            time.sleep(1.2)  # pictures and late rows
            # and under load (the whole suite at once) whatever pictures are still on their way, before anything is
            # measured: a row that grows when its pictures arrive was found off the screen a moment before
            try:
                self.b.wait_js("[...document.images].every((i) => i.complete)", timeout=5)
            except AssertionError:
                pass  # one that never comes (a broken address) is the check's to find, not a reason to stop here
            got = [x for x in self.b.js(CHECK) if not (x[0] == "off-screen" and x[-1] == "LIVE")]  # the guide's endless "live"
            got += self.b.js(HERO)
            got += self.b.js(FOOT)
            if got:
                found[f"{name} {json.dumps(args)}"] = got
        self.assertEqual(found, {}, "\n" + json.dumps(found, indent=1, ensure_ascii=False))


class ConsistencyLargeTextTest(ConsistencyTest):
    """The same pages with the setting "textsize" at large: nothing runs off or over anything with the larger text."""

    def setUp(self):
        super().setUp()
        self.b.js("useTextSize('large')")

    def tearDown(self):
        self.b.js("useTextSize('normal')")
        super().tearDown()


class ConsistencyDeskTest(ConsistencyTest):
    """The same pages at a desk's size (Settings → General → Size): smaller, more to a row, nothing off or cut."""

    def setUp(self):
        super().setUp()
        self.b.js("useSize('desk')")

    def tearDown(self):
        self.b.js("useSize('auto')")
        super().tearDown()


# Each look in parts, every part a class of its own and so a process of its own beside the others (tests/parallel.py):
# all the pages in one went on for over two minutes, at the end of the run alone. Each part its own backend and browser.
PARTS = 3
for _look in (ConsistencyTest, ConsistencyLargeTextTest, ConsistencyDeskTest):
    for _n in range(PARTS):
        _name = f"{_look.__name__.removesuffix('Test')}Part{_n + 1}Test"
        globals()[_name] = type(_name, (_look,), {"PAGES": PAGES[_n::PARTS], "__module__": __name__,
                                                  "test_every_page_alike": ConsistencyTest.test_every_page_alike})
ConsistencyTest.test_every_page_alike = None  # the looks themselves: only their parts run (None is no test)
del _look, _n, _name


if __name__ == "__main__":
    unittest.main()
