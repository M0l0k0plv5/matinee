"""UI in headless Chromium: remote-key navigation, search, libraries, watched state, live updates."""
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

from helpers import HAVE_CHROMIUM, HAVE_FFMPEG, HAVE_MPV, ROOT, Backend, Chromium, fake_mpv_dir, free_port, make_video, temp_dir, USERNAME, wait_for
from mockjf import PASSWORD, USERNAME, Library, MockJellyfin, TICKS


class UiBase(unittest.TestCase):
    language = "en"
    with_player = False
    navidrome = False  # with a fake Navidrome, signed in
    audiobookshelf = False  # with a fake Audiobookshelf and its API key
    podfetch = False  # with a fake Podfetch, signed in
    section = "jellyfin"  # opened from the main menu before each test, None: stay on the menu
    extra_env = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = temp_dir("kino-ui-")
        video = mpv_dir = None
        if cls.with_player and HAVE_MPV and HAVE_FFMPEG:
            video = make_video(Path(cls.tmp) / "t.mkv", 20)
            mpv_dir = fake_mpv_dir(cls.tmp, 8)
        cls.mock = MockJellyfin(video_path=video).start()
        env = {}
        # never the real Bluetooth: a fake bluetoothctl with a speaker nearby and a paired remote
        btbin = Path(cls.tmp) / "btbin"
        btbin.mkdir()
        (btbin / "bluetoothctl").symlink_to(ROOT / "tests" / "fake-bluetoothctl")
        (btbin / "journalctl").symlink_to(ROOT / "tests" / "fake-journalctl")
        (Path(cls.tmp) / "journal.txt").write_text("connect to 34:0E:22:00:00:07: Host is down (112)\n")  # the speaker
        cls.bt_state = Path(cls.tmp) / "bt.json"
        cls.bt_state.write_text(json.dumps({"devices": {
            "50:F5:DA:00:00:02": {"name": "Remote", "known": True, "paired": True, "trusted": True, "connected": True},
            "11:22:33:00:00:03": {"name": "Box", "icon": "audio-card", "nearby": True},
            "34:0E:22:00:00:07": {"name": "Shelf Speaker", "icon": "audio-card", "known": True, "paired": True, "trusted": True,
                                  "away": True}}}))  # paired, but another device has it
        # the PATH given here replaces the one with the fake mpv, so it goes in front of that too
        path = f"{btbin}:{os.environ['PATH']}"
        env.update(PATH=f"{mpv_dir}:{path}" if mpv_dir else path, FAKE_BT_STATE=str(cls.bt_state), FAKE_BT_LOG=str(Path(cls.tmp) / "bt.log"),
                   FAKE_JOURNAL=str(Path(cls.tmp) / "journal.txt"))
        conf = None
        cls.nd = None
        if cls.navidrome:
            from mocknd import ND_USER, MockNavidrome
            from test_music import make_audio
            cls.nd = MockNavidrome(audio_path=make_audio(Path(cls.tmp) / "song.ogg", 30)).start()
            conf = {"navidrome": {"server": cls.nd.url, "username": ND_USER}}
        cls.abs = None
        if cls.audiobookshelf:
            from mockabs import TRACK_SECONDS, MockAudiobookshelf
            from test_music import make_audio
            cls.abs = MockAudiobookshelf(audio_path=make_audio(Path(cls.tmp) / "part.ogg", TRACK_SECONDS)).start()
            conf = {**(conf or {}), "audiobookshelf": {"server": cls.abs.url}}
        cls.pf = None
        if cls.podfetch:
            from mockpf import EPISODE_SECONDS, PF_USER, MockPodfetch
            from test_music import make_audio
            cls.pf = MockPodfetch(audio_path=make_audio(Path(cls.tmp) / "episode.ogg", EPISODE_SECONDS)).start()
            conf = {**(conf or {}), "podfetch": {"server": cls.pf.url, "username": PF_USER}}
        cls.backend = Backend(cls.mock.url, mpv_dir=mpv_dir, language=cls.language, env={**env, **cls.extra_env}, conf=conf).start()
        if cls.pf:
            from mockpf import PF_PASSWORD
            (cls.backend.conf / "podfetch.json").write_text(json.dumps({"password": PF_PASSWORD}))
        if cls.nd:
            from mocknd import ND_PASSWORD
            from test_music import set_nd_password
            set_nd_password(cls.backend, ND_PASSWORD)
        if cls.abs:
            from mockabs import ABS_KEY
            from test_books import set_abs_key
            set_abs_key(cls.backend, ABS_KEY)
        cls.browser = Chromium(cls.backend.url)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.backend.stop()
        cls.mock.stop()
        if cls.nd:
            cls.nd.stop()
        if cls.abs:
            cls.abs.stop()
        if cls.pf:
            cls.pf.stop()

    def setUp(self):
        # every test starts with a fresh library: movie 1 started, episode S1E1 watched
        self.mock.lib = Library(20)
        self.mock.lib.userdata["m1"].update(PlaybackPositionTicks=5 * TICKS, LastPlayedDate="2026-01-01T00:00:00.0000000Z")
        self.mock.lib.set_played("e11", True)
        if not self.browser.alive():  # crashed in a test before: that one failed, the rest get a new one
            print(f"Chromium gone before {self.id()}: started again", file=sys.stderr)
            self.browser.close()
            type(self).browser = Chromium(self.backend.url)
        self.b = self.browser
        self.b.errors.clear()
        self.backend.request("/section", {"id": "jellyfin"})  # the main menu's focus, kept by the backend
        self.backend.request("/unhide", {})  # nothing taken off a row by an earlier test
        self.b.js("try { localStorage.clear(); sessionStorage.clear(); } catch {} location.reload()")  # not yet loaded at first
        time.sleep(0.5)
        # on a tile: Up Next when fresh, else the part used last
        self.b.wait_js("current?.name === 'menu' && document.activeElement?.dataset.k?.startsWith('menu-')", msg="main menu")
        # the logo of the start away at once: the page coming in after it (scaled) would move what the test clicks
        # and measures; UiMenuTest looks at the logo itself
        self.b.js("document.getElementById('intro')?.remove()")
        if self.section == "jellyfin":
            self.b.js("focusEl($('[data-k=menu-jellyfin]'))")
            self.b.key("Enter")
            self.b.wait_js("USER && document.querySelectorAll('.home .card').length > 0", msg="home loaded")
            time.sleep(0.5)

    def tearDown(self):
        self.assertEqual(self.b.errors, [], "JavaScript errors in the UI")

    # ---------- Helpers ----------

    def view(self):
        return self.b.js("current.name")

    def focus(self):
        return self.b.focus_key()

    def focus_text(self):
        return self.b.js("document.activeElement.textContent.trim()")

    def texts(self, selector):
        if selector.startswith("#options"):  # opened by a key: a moment later on a busy machine
            try:
                self.b.wait_js("!options.hidden && $('#options .opt')", timeout=5)
            except AssertionError:
                pass  # closed: what is there (nothing)
        return self.b.js(f"[...document.querySelectorAll('{selector}')].map(e => e.textContent.trim())")

    def pick_option(self, label):
        """Hold OK on the focused tile and choose that line of its options."""
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="the options")
        labels = self.texts("#options .opt")
        self.assertIn(label, labels)
        for _ in range(labels.index(label)):
            self.b.key("ArrowDown")
        self.b.key("Enter")

    def go_to_row(self, prefix):
        """Press down until the focus is in the row whose tiles start with prefix."""
        for _ in range(8):
            if (self.focus() or "").startswith(prefix):
                return
            self.b.key("ArrowDown")
        self.fail(f"row {prefix} not reached, focus: {self.focus()}")

    def open_library(self, name, bar=False):
        """Open the tile `name` in the Libraries row (bar: then up to its sorting, from the first title)."""
        self.go_to_row("libs")
        for _ in range(5):
            if self.focus_text().startswith(name):
                break
            self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'library' && document.querySelector('.grid-page h1')", msg=f"library {name}")
        if bar:
            self.b.key("ArrowUp")
            self.b.key("ArrowUp")  # past the genres, if there are any
            self.b.js("focusEl($('[data-k=\"opt-sort-SortName\"]'))")

    def to_grid(self):
        for _ in range(4):
            if self.b.js("!!document.activeElement.closest('.grid')"):
                return
            self.b.key("ArrowDown")
        self.fail("grid not reached")

    def cards(self):
        return self.texts(".grid .card .label")


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiTest(UiBase):
    # ---------- Home ----------

    def test_home_shows_all_rows(self):
        self.assertEqual(self.texts(".row h2"), ["Continue Watching", "New in Movies", "New in Shows"],
                         "the libraries first, as small buttons without a heading")
        self.assertEqual(self.texts('[data-k^="libs-"]'), ["Movies", "Shows", "Collections"])
        self.assertEqual(self.b.js("[...document.querySelectorAll('.tabbar .tab')].map((x) => x.dataset.k)"),
                         ["tab-search", "tab-jellyfin", "tab-settings"], "its search and settings at the ends of the bar")
        self.assertEqual(self.focus(), "libs-0")
        self.go_to_row("resume")
        self.b.wait_js("document.querySelector('#hero').innerHTML.includes('Movie 1')", msg="what is begun above")
        self.assertEqual(self.b.js("document.querySelector('.card .progress i').style.width"), "25%")

    def test_up_next_from_server(self):
        self.go_to_row("resume")
        self.b.key("ArrowRight")
        self.assertIn("S1 E2", self.focus_text(), "after what is begun, the show's next episode")

    def test_hero_follows_focus(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")
        self.b.wait_js("document.querySelector('#hero').innerHTML.includes('Movie 3')", msg="hero shows movie 3")

    def test_grid_rows_go_on_into_each_other(self):
        self.open_library("Movies")
        self.b.wait_js("document.querySelectorAll('.grid .card').length >= 12", msg="library grid")
        cols = self.b.js("getComputedStyle($('.grid')).gridTemplateColumns.split(' ').length")
        self.b.js(f"focusEl(document.querySelectorAll('.grid .card')[{cols - 1}])")  # the end of the first row
        self.b.key("ArrowRight")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.grid .card')].indexOf(document.activeElement)"), cols,
                         "the first tile of the second row")
        self.b.key("ArrowLeft")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.grid .card')].indexOf(document.activeElement)"), cols - 1,
                         "left at the start of a row: back to the end of the row above (in search it leads to the letters)")
        self.b.js("focusEl(document.querySelector('.grid .card'))")
        self.b.key("ArrowLeft")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.grid .card')].indexOf(document.activeElement)"), 0,
                         "the very first tile has nothing before it")

    def test_no_empty_space_below_the_last_row(self):
        self.go_to_row("latest1")
        time.sleep(0.6)
        gap = self.b.js("$('.home .rows').getBoundingClientRect().bottom - [...document.querySelectorAll('.home .row')].pop().getBoundingClientRect().bottom")
        self.assertLess(gap, 80, "the last row ends at the bottom, it is not moved up to the top")
        self.assertGreaterEqual(gap, 0, "and all of it is on the screen, its titles too")

    def test_hero_in_the_libraries_row(self):
        self.go_to_row("libs")
        self.b.wait_js("$('#hero .title')?.textContent === 'Movies'", msg="above: the library's name")
        self.assertFalse(self.b.js("!!$('#hero .kicker, #hero .logo, #hero .meta, #hero .overview')"),
                         "its name alone, no title of what is in it: as the buttons of every part")
        self.b.js("useEffects('calm')")
        try:
            self.b.js("focusEl($('[data-k=libs-0]').previousElementSibling ?? $('[data-k=resume-0]')); clearBackdrop()")
            self.b.js("focusEl($('[data-k=libs-0]'))")
            self.b.wait_js("$('#backdrop canvas.on')", msg="behind it the blurred colours of one of its newest: with Calm, not black")
        finally:
            self.b.js("useEffects('auto')")

    def test_hero_stays_below_the_bar_with_the_music_bar(self):
        """The music's bar shortens the hero: the line above, a logo, the facts and a long text must not go up behind
        the bar of parts (they did on the TV: the logo under the bar)."""
        self.go_to_row("latest0")
        self.b.wait_js("$('#hero .logo')?.complete && $('#hero .overview')", msg="the newest film, its logo above")
        try:
            self.b.js("document.body.classList.add('with-nowbar');"
                      "$('#hero .overview').textContent = 'A sentence of a long story. '.repeat(30)")
            time.sleep(0.3)
            bar = self.b.js("$('.tabbar').getBoundingClientRect().bottom")
            tops = self.b.js("[...$('#hero').children].map((e) => e.getBoundingClientRect().top)")
            self.assertGreaterEqual(min(tops), bar, "nothing of the hero behind the bar")
            lines = self.b.js("$('#hero .overview').getBoundingClientRect().height"
                              " / parseFloat(getComputedStyle($('#hero .overview')).lineHeight)")
            self.assertAlmostEqual(lines, 2, delta=0.1, msg="the text a line shorter, never cut through a line")
        finally:
            self.b.js("document.body.classList.remove('with-nowbar')")

    def test_the_last_row_above_the_music_bar(self):
        """Music playing: the rows fade out above its bar at the foot, so no tile peeks through its glass; the last
        row, focused, ends above it."""
        self.go_to_row("libs")
        try:
            self.b.js("nowbar.hidden = false; nowbar.innerHTML = '<img alt=\"\"><div><b>Song</b><span>Artist</span></div>';"
                      "document.body.classList.add('with-nowbar')")
            self.b.js("$('.rows-track').insertAdjacentHTML('beforeend', '<div class=\"row\" style=\"height: 4rem\">"
                      "<div class=\"card\" tabindex=\"-1\" data-k=\"low-0\">low</div></div>')")
            gone = self.b.js(r"""(() => { const m = getComputedStyle($('.home .rows')).maskImage, rows = $('.home .rows').getBoundingClientRect();
              const stops = [...m.matchAll(/calc\(100% - ([\d.]+)px\)/g)].map((x) => rows.bottom - parseFloat(x[1]));
              return [stops.length ? Math.max(...stops) : rows.bottom, $('#nowbar').getBoundingClientRect().top]; })()""")
            self.assertLessEqual(gone[0], gone[1], "faded out above the bar")
            self.b.js("focusEl($('[data-k=low-0]'), false)")
            time.sleep(0.5)
            row, bar = self.b.js("[$('.rows-track').lastElementChild, $('#nowbar')].map((e) => e.getBoundingClientRect())"
                                 ".map((r) => [r.top, r.bottom])")
            self.assertLessEqual(row[1], bar[0], "the last row above the bar")
        finally:
            self.b.js("nowbar.hidden = true; document.body.classList.remove('with-nowbar')")

    def test_left_from_a_rows_first_tile_goes_to_the_bar(self):
        self.assertEqual(self.focus(), "libs-0")
        self.b.key("ArrowLeft")
        self.assertEqual(self.focus(), "tab-jellyfin", "the part being shown")
        self.b.key("ArrowLeft")
        self.assertEqual(self.focus(), "tab-search")

    def test_up_into_the_bar_lands_on_the_part_shown(self):
        self.assertEqual(self.focus(), "libs-0")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "tab-jellyfin", "not the magnifier above the first tile")

    def test_down_from_the_bar_to_the_button_used_last(self):
        """Down from the bar: the button left last, else the first; not the one that stands below the part's name."""
        self.b.key("ArrowUp")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "libs-0", "the first")
        self.b.key("ArrowRight")
        self.b.key("ArrowUp")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "libs-1", "the one left last")

    def test_an_empty_library_says_what_fills_it(self):
        """A new Jellyfin with nothing found yet: the start says so, not a black page."""
        for iid in list(self.mock.lib.items):
            if self.mock.lib.items[iid].get("Type") in ("Movie", "Series", "Episode", "Season", "BoxSet"):
                del self.mock.lib.items[iid]
                self.mock.lib.userdata.pop(iid, None)
        self.mock.lib.episodes.clear()
        self.b.js("homeKept = null; navigate('home')")
        self.b.wait_js("current.name === 'home' && $('.row-hint')", msg="the start, empty")
        self.assertIn("Dashboard", self.b.js("$('.row-hint').textContent"))
        self.b.js("shelfCache.clear(); navigate('menu')")
        self.b.wait_js("current.name === 'menu' && document.activeElement?.dataset.k?.startsWith('menu-')", msg="the main menu")
        self.b.js("focusEl($('[data-k=menu-jellyfin]'))")
        self.b.wait_js("$('#shelf .hint')?.textContent.includes('Dashboard')", msg="its shelf says so too")

    def test_up_to_a_pages_buttons_lands_on_play(self):
        """Up from a show's seasons: Play (or the button used last), not the one that stands above the season."""
        self.b.js("navigate('item', { id: 's1' })")
        self.b.wait_js("current.name === 'item' && $('[data-k=season-se2]')", msg="the show")
        self.b.js("focusEl($('[data-k=season-se2]'))")
        self.b.key("ArrowUp")
        self.assertEqual(self.b.js("document.activeElement.dataset.action"), "play")
        self.b.key("ArrowRight")
        used = self.focus()
        self.b.key("ArrowDown")
        self.b.js("focusEl($('[data-k=season-se2]'))")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), used, "the one used last")

    def test_settings_from_the_bar(self):
        self.b.js("focusEl($('[data-k=tab-settings]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings' && current.args.part === 'movies'", msg="Jellyfin's settings")

    def test_row_moves_on_the_gpu(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight", pause=0.1)
        self.assertIn(self.b.js("document.activeElement.closest('.strip').style.transform"), ("", "translate3d(0px, 0px, 0px)"),
                      "a tile in view: the focus goes to it, the row stays")
        for _ in range(9):
            self.b.key("ArrowRight", pause=0.1)
        time.sleep(0.6)
        x = self.b.js("document.activeElement.closest('.strip').style.transform")
        self.assertRegex(x, r"translate3d\(-\d", "one beyond the edge: the row moves")
        self.assertEqual(self.b.js("document.querySelector('.home .rows').scrollTop"), 0, "no native scrolling")

    def test_row_turns_a_page_at_its_edge(self):
        self.go_to_row("latest0")
        strip = "document.activeElement.closest('.strip')"
        shown = f"[...{strip}.children].filter((c) => {{ const r = c.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; }}).length"
        # where the row stops: its focused tile first, or its end (the last tile at the right, as far as it goes)
        first = f"""(() => {{ const s = {strip}, el = document.activeElement, pad = parseFloat(getComputedStyle(s).paddingLeft);
          const last = s.lastElementChild, max = Math.max(0, last.offsetLeft - s.offsetLeft + last.offsetWidth + pad - s.clientWidth);
          return Math.min(max, el.offsetLeft - s.offsetLeft - pad); }})()"""
        n = self.b.js(shown)
        for _ in range(n):  # onto the first tile beyond the edge
            self.b.key("ArrowRight", pause=0.1)
        time.sleep(0.6)
        moved = self.b.js(f"{strip}.slid")
        self.assertGreater(moved, 0, "one beyond the edge: the row moves")
        self.assertAlmostEqual(moved, self.b.js(first), delta=1, msg="a page on: that tile first, or the row's end")
        self.b.key("ArrowRight", pause=0.1)
        self.assertEqual(self.b.js(f"{strip}.slid"), moved, "the next step: the row stands")
        for _ in range(n + 1):  # back until the row moves again
            self.b.key("ArrowLeft", pause=0.1)
            if self.b.js(f"{strip}.slid") < moved:
                break
        time.sleep(0.6)
        self.assertLess(self.b.js(f"{strip}.slid"), moved, "back over the edge: a page back")
        right = self.b.js("document.activeElement.getBoundingClientRect().right")
        self.assertLessEqual(right, self.b.js("innerWidth"), "the tile in view")
        self.assertGreater(right, self.b.js("innerWidth / 2"), "as the last of that page, not the first")

    # ---------- Navigation ----------

    def test_entering_a_row_starts_at_its_first_tile(self):
        self.go_to_row("latest0")
        self.assertEqual(self.focus(), "latest0-0")
        for _ in range(3):
            self.b.key("ArrowRight", pause=0.15)
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "latest1-0", "not the tile that happens to be below")

    def test_coming_back_to_a_row_restores_its_tile(self):
        self.go_to_row("latest0")
        for _ in range(3):
            self.b.key("ArrowRight", pause=0.15)
        self.b.key("ArrowDown")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "latest0-3")

    def test_cast_leads_to_what_else_is_with_them(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && document.querySelector('[data-k^=\"similar-\"]')")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "cast-0", "down from the buttons: the cast")
        self.b.key("ArrowUp")
        self.assertTrue(self.b.js("!!document.activeElement.closest('.actions')"), "up goes back to the buttons")
        self.b.key("ArrowDown")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'person' && document.querySelector('.person .row')", msg="their page")
        self.assertEqual(self.b.js("document.querySelector('.person h1').textContent"), "Actress")
        self.assertIn("Born", self.b.js("document.querySelector('.person .meta').textContent"))
        self.assertEqual(self.texts(".person .row h2"), ["Movies with Actress", "Shows with Actress"])
        self.assertEqual(self.texts('[data-k^="pmovies-"] .label'), ["Movie 2", "Movie 1"], "the newest first")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.args.id === 'm2'", msg="one of them opened")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'person'", msg="back on their page")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'item' && document.activeElement?.dataset.k === 'cast-0'", msg="and on the movie, at the face")

    def test_open_and_back_keeps_focus(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        before = self.focus()
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'", msg="detail page")
        self.assertIn("Play", self.focus_text())
        self.b.key("Escape")
        self.b.wait_js("current.name === 'home'", msg="back on home, one page back")
        self.assertEqual(self.focus(), before, "focus back on the same tile")

    def test_back_shows_the_page_as_it_was(self):
        """The row moved as far as it was, the tile at the same place, and its pictures there at once (not faded in
        again from nothing, as every picture of the page was when it was built anew)."""
        self.go_to_row("latest0")
        for _ in range(9):
            self.b.key("ArrowRight", pause=0.15)
        time.sleep(0.6)  # the row's glide and the tile's lift
        where = "[getComputedStyle(document.activeElement.closest('.strip')).transform, Math.round(document.activeElement.getBoundingClientRect().left)]"
        before = self.b.js(where)
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.b.key("Escape", pause=0)
        self.b.wait_js("current.name === 'home' && document.activeElement.dataset.k === 'latest0-9'", msg="back on the tile")
        faded = self.b.js("""[...view.querySelectorAll('.card .art img')].filter(i => { const r = i.getBoundingClientRect();
            return i.complete && r.width && r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight; })
            .filter(i => parseFloat(getComputedStyle(i).opacity) < 0.99).length""")
        self.assertEqual(faded, 0, "pictures it had already are there at once")
        time.sleep(0.6)
        self.assertEqual(self.b.js(where), before, "the row as far as it was, the tile at the same place")

    def test_backspace_also_goes_back(self):
        self.go_to_row("resume")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.b.key("Backspace")
        self.b.wait_js("current.name === 'home'")
        self.b.key("Backspace")
        self.b.wait_js("!!document.activeElement.closest('.tabbar')")
        self.b.key("Backspace")
        self.b.wait_js("current.name === 'menu'")
        self.assertEqual(self.focus(), "menu-jellyfin")

    def test_home_key(self):
        self.open_library("Movies")
        self.to_grid()
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.b.key("Home")
        self.b.wait_js("current.name === 'home'")
        self.assertEqual(self.b.js("stack.map(e => e.name)"), ["menu"])
        self.b.key("Home")
        self.b.wait_js("current.name === 'menu'")
        self.assertEqual(self.b.js("stack.length"), 0)
        self.b.wait_js("document.activeElement?.dataset.k === 'menu-jellyfin'", msg="on the part used last")

    def test_resume_button_with_time(self):
        self.go_to_row("resume")
        self.b.key("Enter")  # movie 1 from "Continue Watching"
        self.b.wait_js("current.name === 'item'")
        self.assertEqual(self.texts(".actions .btn"), ["Resume from 0:05", "Start Over", "Watched Status", "My List"])

    def open_movie_with_long_overview(self):
        self.mock.lib.items["m1"]["Overview"] = " ".join(f"Sentence number {i} of a long story." for i in range(80)) + " The end."
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && document.querySelector('.detail .hero')")
        self.assertEqual(self.focus(), "resume", "still starts on the play button")

    def test_a_movie_s_fields_from_the_server_cannot_run_code(self):
        bad = '"><img src=x onerror="window.pwned=1">'
        m = self.mock.lib.items["m1"]
        m.update(ProductionYear=bad, ImageTags={"Primary": bad, "Logo": bad}, BackdropImageTags=[])
        self.b.js("navigate('item', { id: 'm1', type: 'Movie' })")
        self.b.wait_js("current.name === 'item' && document.querySelector('.detail .hero')", msg="the movie")
        time.sleep(0.5)
        self.assertFalse(self.b.js("!!window.pwned || [...document.querySelectorAll('.detail img')].some((i) => i.getAttribute('src') === 'x')"),
                         "neither the year nor a picture's tag taken as markup")
        self.assertIn(bad, self.b.js("$('.detail .meta').textContent"), "the year shown as it came")

    def test_a_trailer_button_only_for_a_trailer_of_one_s_own(self):
        self.b.js("navigate('item', { id: 'm4', type: 'Movie' })")
        self.b.wait_js("current.name === 'item' && $('.detail [data-k=favorite]')", msg="a movie with a trailer on the web only")
        time.sleep(0.3)  # the page as it ends
        self.assertFalse(self.b.js("!!$('[data-k=trailer]')"), "nothing to play it with")
        self.mock.lib.items["m5"]["LocalTrailerCount"] = 1
        try:
            self.b.js("navigate('item', { id: 'm5', type: 'Movie' })")
            self.b.wait_js("$('.detail [data-k=trailer]')", msg="one with a trailer in the library: the button")
        finally:
            del self.mock.lib.items["m5"]["LocalTrailerCount"]

    def test_reader_is_really_invisible_when_closed(self):
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('#reader')).display"), "none",
                         "a closed reader must not darken the page")
        self.assertEqual(self.b.js("document.elementFromPoint(innerWidth / 2, innerHeight / 2).closest('#reader')"), None)

    def test_cut_off_overview_shows_whole_lines_only(self):
        self.open_movie_with_long_overview()
        self.b.wait_js("document.querySelector('.hero .overview.f')", msg="focusable")
        lines = self.b.js("(() => { const ov = document.querySelector('.hero .overview'), s = getComputedStyle(ov);"
                          " return ov.clientHeight / parseFloat(s.lineHeight); })()")
        self.assertAlmostEqual(lines, 3, delta=0.05, msg="no part of the next line peeks out below")
        text = self.b.js("document.querySelector('.hero .overview').textContent")
        full = self.b.js("document.querySelector('.hero .overview').dataset.full")
        self.assertTrue(text.endswith("… More"), text)
        shown = text[:-len("… More")]
        self.assertTrue(full.startswith(shown) and full[len(shown)] in " ,.;:", "cut after a whole word")
        self.assertTrue(self.b.js("(() => { const ov = document.querySelector('.hero .overview'); return ov.scrollHeight <= ov.clientHeight + 2; })()"),
                        "More fits on the last line")

    def test_full_overview_with_ok(self):
        self.open_movie_with_long_overview()
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "overview")
        self.b.key("Enter")
        self.b.wait_js("!document.querySelector('#reader').hidden", msg="full text open")
        self.assertTrue(self.b.js("document.querySelector('#reader p').textContent.endsWith('The end.')"))
        self.assertEqual(self.b.js("document.querySelector('#reader h2').textContent"), "Movie 1")
        self.b.key("ArrowDown")  # scrolls the text, does not move the focus behind it
        self.assertEqual(self.focus(), "overview")
        self.b.key("Enter")
        self.assertTrue(self.b.js("document.querySelector('#reader').hidden"), "OK closes it")
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('#reader')).display"), "none")
        self.assertEqual(self.focus(), "overview")
        self.b.key("Enter")
        self.b.wait_js("!document.querySelector('#reader').hidden")
        self.b.key("Escape")
        self.assertTrue(self.b.js("document.querySelector('#reader').hidden"), "Back closes it")
        self.assertEqual(self.view(), "item", "and stays on the page")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "resume")

    def test_short_overview_is_not_focusable(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && document.querySelector('.detail .overview')")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "resume", "nothing to open, so nothing to select")

    def test_director_in_meta_line(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && document.querySelector('.detail .meta')")
        self.assertIn("Directed by Some Director", self.texts(".detail .meta span"))
        self.assertNotIn("Some Director", self.texts(".people .label"), "directors are not listed as cast")

    def test_similar_movies_below_cast(self):
        self.go_to_row("latest0")
        self.b.key("Enter")  # movie 1, genre Action
        self.b.wait_js("current.name === 'item'")
        self.assertEqual(self.texts(".row h2"), ["Cast", "More Like This"])
        self.assertEqual(self.texts('[data-k^="similar-"] .label'), ["Movie 4", "Movie 7", "Movie 10"])
        self.go_to_row("similar")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.args.id === 'm4'", msg="similar movie opened")

    def test_detail_page_does_not_wait_for_similar(self):
        self.mock.similar_delay = 2
        try:
            self.go_to_row("latest0")
            self.b.key("Enter", pause=0)
            start = time.time()
            self.b.wait_js("current.name === 'item' && document.querySelector('.detail .hero')", msg="detail page")
            self.assertLess(time.time() - start, 1.5, "shown before the similar row is there")
            self.assertEqual(self.focus(), "resume")
            self.b.wait_js("document.querySelector('[data-k^=similar-]')", timeout=5, msg="similar row filled in later")
            self.assertEqual(self.focus(), "resume", "the late row does not take the focus")
        finally:
            self.mock.similar_delay = 0

    def test_similar_row_is_loaded_ahead_while_resting_on_a_tile(self):
        self.mock.similar_delay = 1.5
        try:
            self.go_to_row("latest0")
            self.b.key("ArrowRight")
            self.assertEqual(self.focus(), "latest0-1")
            time.sleep(0.6 + 1.5 + 0.5)  # rest on the tile until the row has been loaded ahead
            self.b.key("Enter", pause=0)
            self.b.wait_js("current.name === 'item' && document.querySelector('.detail .hero')", msg="detail page")
            self.assertTrue(self.b.js("!!document.querySelector('[data-k^=similar-]')"), "similar row there right away")
        finally:
            self.mock.similar_delay = 0

    def test_holding_an_arrow_does_not_load_ahead(self):
        self.b.js("window.asked = 0; const f = fetch; fetch = (u, o) => { if (String(u).startsWith('/similar/')) asked++; return f(u, o); }")
        self.go_to_row("latest0")
        for _ in range(5):
            self.b.key("ArrowRight", pause=0.1)
        time.sleep(1)
        self.assertLessEqual(self.b.js("asked"), 1, "only the tile it stopped on")

    def test_a_held_arrow_keeps_its_pace(self):
        """A keyboard repeating a little faster than the pace (a Mac's, every 90 ms): a step every 110 ms all the
        same, not every second repeat dropped (which made it one every 180 ms); none after letting go."""
        self.go_to_row("latest0")
        self.b.js("window.steps = 0; document.addEventListener('focusin', () => steps++)")
        send = lambda **kw: self.b.call("Input.dispatchKeyEvent", key="ArrowRight", code="ArrowRight",
                                        windowsVirtualKeyCode=self.b.VK["ArrowRight"], **kw)
        start = time.time()
        send(type="rawKeyDown")
        sent, end = 1, start + 0.8
        while time.time() < end:
            time.sleep(0.09)
            send(type="rawKeyDown", autoRepeat=True)
            sent += 1
        send(type="keyUp")
        held, took = self.b.js("steps"), time.time() - start
        time.sleep(0.4)
        self.assertEqual(self.b.js("steps"), held, "no step after letting go")
        # as many as the keys came, but no more than one every 110 ms (on a busy machine the keys come slower)
        expected = min(sent, 1 + took / 0.11)
        self.assertGreaterEqual(held, 0.75 * expected, f"about nine a second ({sent} keys in {took:.2f} s)")

    def test_similar_row_at_once_even_when_jellyfin_is_slow(self):
        wait_for(lambda: self.backend.request("/similar/m1")[0] == 200, msg="backend knows the similar titles")
        self.mock.similar_delay = 3
        try:
            self.b.js("navigate('item', { id: 'm1', type: 'Movie' })")
            self.b.wait_js("document.querySelector('[data-k^=similar-]')", timeout=1.5, msg="row without waiting for Jellyfin")
        finally:
            self.mock.similar_delay = 0

    def test_back_from_similar_movie_goes_to_the_movie_before(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("document.querySelector('[data-k^=similar-]')", msg="similar row")
        self.go_to_row("similar")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus(), "similar-1")
        self.b.key("Enter")
        self.b.wait_js("current.args.id === 'm7'", msg="similar movie opened")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'item' && document.activeElement.dataset.k === 'similar-1'",
                       msg="the movie before, on that tile")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'home' && document.activeElement.dataset.k === 'latest0-0'",
                       msg="then home, on the tile it started from")

    def test_refresh_keeps_a_step_taken_while_it_loads(self):
        """A change on the server builds the page again where it stands; a step along the similar row while that
        loads is kept, not undone by the tile focused when it began."""
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && document.querySelector('[data-k^=similar-]')", msg="similar row")
        self.go_to_row("similar")
        self.assertEqual(self.focus(), "similar-0")
        self.b.js("""(() => { const f = window.fetch;
            window.fetch = (u, o) => String(u).startsWith("/jf/Items/m1?") ? new Promise((r) => setTimeout(r, 1000)).then(() => f(u, o)) : f(u, o);
            refresh(); })()""")
        self.b.key("ArrowRight", pause=0)
        time.sleep(2.5)  # the page has been built again by now
        self.assertEqual(self.focus(), "similar-1", "the step taken meanwhile stays")

    def test_slow_refresh_does_not_break_the_opened_page(self):
        """A live refresh still loading must not overwrite a page opened meanwhile or mix up its tiles."""
        self.b.js("""(() => { const f = window.fetch;
            window.fetch = (u, o) => String(u).includes("Resume") ? new Promise((r) => setTimeout(r, 1500)).then(() => f(u, o)) : f(u, o);
            refresh(); navigate("item", { id: "m4" }); })()""")
        self.b.wait_js("document.querySelector('.detail .hero')", msg="detail page")
        time.sleep(2.5)  # the slow refresh has finished by now
        self.assertEqual(self.view(), "item")
        self.assertTrue(self.b.js("!!document.querySelector('.detail')"), "the detail page is still shown")
        self.assertTrue(self.b.js("[...document.querySelectorAll('.f[data-r]')].every((el) => reg[el.dataset.r])"),
                        "every tile still knows its item")
        self.go_to_row("similar")
        expected = self.b.js("reg[document.activeElement.dataset.r].Id")
        self.b.key("Enter")
        self.b.wait_js(f"current.name === 'item' && current.args.id === '{expected}'", msg="similar movie opened")

    def test_episode_page_shows_its_season(self):
        self.go_to_row("resume")
        self.b.key("ArrowRight")  # S1 E2 of the test show, after movie 1
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && document.querySelectorAll('#episodes .card').length === 4", msg="season row")
        self.assertIn("Season 1", self.texts(".row h2"))
        self.assertEqual(self.texts("#episodes .card .label"), ["1. Episode 1", "2. Episode 2", "3. Episode 3", "4. Episode 4"])
        for _ in range(4):
            if self.focus().startswith("ep-"):
                break
            self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "ep-e12", "down lands on this episode, not the first one")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus(), "ep-e13", "the next one is one key away")

    def test_a_new_season_shows_as_its_show(self):
        self.mock.latest_extra = {"lt": ["se2", "e21"]}
        self.addCleanup(setattr, self.mock, "latest_extra", {})
        self.b.js("render(current)")
        self.b.wait_js("document.querySelector('[data-k=\"latest1-1\"]')", msg="new in shows")
        self.assertEqual(self.texts('[data-k^="latest1-"] .label'), ["Test Show", "Test Show"], "the season as its show, once")
        self.go_to_row("latest1")
        self.b.wait_js("$('#hero .overview')?.textContent === 'A show.'", msg="the show's text above")

    def test_library_opens_on_the_first_title(self):
        self.open_library("Movies")
        self.assertTrue(self.b.js("!!document.activeElement.closest('.grid')"), "on the first title, not on the sorting")
        self.assertEqual(self.focus(), self.b.js("document.querySelector('.grid .card').dataset.k"))

    def test_no_similar_row_when_empty(self):
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4")
        self.assertNotIn("More Like This", self.texts(".row h2"))

    # ---------- Shows ----------

    def test_unstarted_show_opens_on_season_1(self):
        for ud in self.mock.lib.userdata.values():
            ud["Played"] = False
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4", msg="episodes loaded")
        self.assertEqual(self.texts(".chip.sel"), ["Season 1✓ 4"], "with its badge")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "season-se1")

    def test_no_spoilers_for_episodes_not_watched(self):
        self.backend.request("/settings", {"spoilers": "hide"})
        self.b.js("SPOILERS = false")  # as read from the settings when Matinee starts
        for i, ud in self.mock.lib.userdata.items():
            ud["Played"] = i == "e11"
        try:
            self.go_to_row("latest1")
            self.b.key("Enter")
            self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4", msg="episodes loaded")
            self.assertEqual(self.texts("#episodes .ep-overview"), ["Plot of S1E1."], "only the one watched has its text")
            pics = self.b.js("[...document.querySelectorAll('#episodes .card')].map((c) => c.querySelector('img')?.src ?? '')")
            self.assertIn("/Items/e11/Images/Primary", pics[0], "the one watched: its still")
            self.assertNotIn("blur=", pics[0], "sharp")
            for i, p in enumerate(pics[1:], 12):
                self.assertIn(f"/Items/e{i}/Images/Primary", p, "the others: their own still")
                self.assertIn("blur=", p, "blurred")
        finally:
            self.backend.request("/settings", {"spoilers": "show"})
            self.b.js("SPOILERS = true")

    def test_search_suggests_before_a_letter(self):
        self.b.js("navigate('search', {q: ''})")
        self.b.wait_js("$('#results .results-title')?.textContent === 'Suggestions'", msg="suggestions")
        self.assertGreater(self.b.js("document.querySelectorAll('#results .grid .card').length"), 0)
        self.b.type("m")
        self.b.wait_js("$('#results .hint')?.textContent === 'One more letter…'", msg="gone with the first letter")

    def test_the_sound_output_rests_between_clicks(self):
        """A running sound output with nothing to play cost an eighth of a core on the thin client all the time."""
        self.b.js("SOUNDS = 0.001; audioCtx = null")
        try:
            self.b.js("tick()")
            self.b.wait_js("audioCtx?.state === 'running'", msg="awake for the click")
            self.b.js("clearTimeout(audioRestTimer); audioCtx.suspend()")  # as after AUDIO_REST without a click
            self.b.wait_js("audioCtx.state === 'suspended'")
            self.b.key("ArrowRight")
            self.b.wait_js("audioCtx.state === 'running'", timeout=2, msg="a key wakes it")
        finally:
            self.b.js("SOUNDS = 0; audioCtx?.close(); audioCtx = null")

    def test_a_click_for_each_step(self):
        self.assertEqual(self.b.js("SOUNDS"), 0, "off, as the tests' settings say, from the start on")
        self.b.js("window.clicks = 0; window.realTick = tick; tick = () => window.clicks++")
        self.go_to_row("resume")
        before = self.b.js("window.clicks")
        self.b.key("ArrowRight")
        self.assertEqual(self.b.js("window.clicks"), before + 1, "one step, one click")
        # the real click, with a silent stand-in for the sound output (never on the developer's speakers)
        self.b.js("tick = window.realTick; audioCtx = null; window.played = 0;"
                  " AudioContext = function () { window.played++; throw new Error('silent'); }; SOUNDS = 0; tick()")
        self.assertEqual(self.b.js("window.played"), 0, "the setting off: no sound")
        self.b.js("SOUNDS = SOUND_LEVELS.low; tick()")
        self.assertEqual(self.b.js("window.played"), 1, "on: the click is played")

    def test_clicks_no_louder_when_the_device_is_turned_up(self):
        # a silent stand-in for the sound output; the level each click is played at, kept
        self.b.js("audioCtx = null; AudioContext = function () { return { state: 'running' }; };"
                  " window.levels = []; playTick = (ctx, kind, level) => window.levels.push(level); SOUNDS = 0.2")
        self.assertIsNone(self.b.js("sinkVolume"), "a desktop: no device volume known (/volume refused)")
        self.b.js("tick(); sinkVolume = 40; tick(); sinkVolume = 60; tick(); showVolume(100); tick()")
        levels = self.b.js("window.levels")
        self.assertEqual(levels[:3], [0.2, 0.2, 0.2], "as before up to 60 %")
        self.assertAlmostEqual(levels[3], 0.2 * 0.6 ** 3, places=6, msg="at 100 % as loud as at 60 %")
        self.b.js("closeVolume(); SOUNDS = 0; audioCtx = null")

    def test_show_with_season_switch(self):
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4", msg="episodes loaded")
        self.assertIn("S1 E2", self.focus_text(), "play button shows the next episode")
        self.assertEqual(self.texts(".chip"), ["Season 1✓ 4", "Season 24/5"], "no specials; each with its badge")
        self.assertIn("2 Seasons", self.b.js("document.querySelector('.hero').textContent"))
        self.b.key("ArrowDown")
        self.assertTrue(self.focus().startswith("season-"))
        self.b.key("ArrowRight")
        self.b.wait_js("document.querySelector('#episodes .card').dataset.k === 'ep-e21'", msg="season 2 loaded")
        self.assertIn("1. Episode 1", self.b.js("document.querySelector('#episodes .card').textContent"))
        self.assertEqual(self.b.js("document.querySelectorAll('#episodes .card').length"), 5,
                         "no specials placed in the season; the missing episode that has aired, not the one to come")
        self.assertTrue(self.b.js("!!document.querySelector('#episodes .watched') === false"), "season 2 unwatched")

    def test_a_season_with_no_episode_in_the_library(self):
        self.mock.lib.add_missing_season()
        self.mock.push("LibraryChanged")  # counted anew
        self.addCleanup(self.mock.push, "LibraryChanged")  # and again for the next test's library
        wait_for(lambda: (self.b.js("loadBadges(true).then(() => !!BADGES.seasons.se3)")), msg="its count known")
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4", msg="episodes loaded")
        self.assertEqual(self.texts(".chip"), ["Season 1✓ 4", "Season 24/5", "Season 30/2"], "shown with what is missing")
        self.assertTrue(self.b.js("document.querySelector('[data-k=season-se3]').classList.contains('missing')"), "dimmed")
        self.assertIn("2 Seasons", self.b.js("document.querySelector('.hero').textContent"), "those in the library")
        self.b.js("loadEpisodes('s1', 'se3')")
        self.b.wait_js("document.querySelectorAll('#episodes .card.missing').length === 2", msg="its episodes, all missing")

    def test_missing_episodes_and_complete_seasons(self):
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4", msg="episodes loaded")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.chip .count-badge')].map((b) => b.className)"),
                         ["count-badge in-chip complete", "count-badge in-chip incomplete"])
        self.b.js("loadEpisodes('s1', 'se2')")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 5", msg="season 2")
        missing = "document.querySelector('#episodes .card.missing')"
        self.assertEqual(self.b.js(f"{missing}.dataset.k"), "ep-e25", "the aired one, dimmed in its place")
        self.assertEqual(self.b.js(f"{missing}.dataset.action"), "missing")
        self.assertIn("Missing", self.b.js(f"{missing}.textContent"))
        self.b.js(f"focusEl({missing})")
        self.b.key("Enter")
        self.b.wait_js("$('#toast')?.textContent.includes('not in your library')", msg="says so instead of playing")
        self.assertEqual(self.mock.reports_for("/Playing"), [], "nothing played")

    def test_badge_on_a_show_poster(self):
        self.b.js("navigate('library', {id: 'lt', name: 'Shows'})")
        self.b.wait_js("$('[data-k] .count-badge')", msg="the show's badge")
        self.assertEqual(self.b.js("$('[data-k] .count-badge').textContent"), "8/9", "specials left out, the one to come too")
        self.assertIn("incomplete", self.b.js("$('[data-k] .count-badge').className"))
        try:
            self.backend.request("/settings", {"badges": "off"})
            self.b.js("BADGE_MODE = 'off'; render(current)")
            self.b.wait_js("$('[data-k]') && !$('.count-badge')", msg="none with the setting off")
        finally:
            self.backend.request("/settings", {"badges": "all"})
            self.b.js("BADGE_MODE = 'all'")

    # ---------- Libraries ----------

    def test_library_grid(self):
        self.open_library("Movies")
        self.assertEqual(len(self.cards()), 12)
        self.assertEqual(self.focus(), self.b.js("$('.grid .card').dataset.k"), "focus starts on the first title")
        first = self.focus()
        self.b.key("ArrowDown")
        self.assertNotEqual(self.focus(), first)
        self.b.wait_js("document.querySelector('.grid-page').style.transform.includes('-')", msg="grid moved on the GPU")
        self.assertEqual(self.b.js("document.querySelector('#view').scrollTop"), 0, "no native scrolling")
        self.b.key("ArrowUp")
        self.b.key("ArrowUp")
        self.b.wait_js("document.querySelector('.grid-page').style.transform === 'translate3d(0px, 0px, 0px)'", msg="bar back at the top")

    def test_grid_leaves_no_row_cut_at_the_top(self):
        self.mock.lib.add_movies(60)
        self.b.js("libCache.clear()")
        self.open_library("Movies")
        self.b.wait_js("document.querySelectorAll('.grid .card').length === 72", msg="the whole library")
        cut = """[...document.querySelectorAll('.grid .card')].filter((c) => {
          const r = c.getBoundingClientRect(); return r.top < 0 && r.bottom > 0; }).length"""
        for step in range(4):
            self.b.key("ArrowDown", pause=0.1)
            time.sleep(0.6)  # the page has slid into place
            self.assertEqual(self.b.js(cut), 0, f"step {step + 1}: no row's names cut in half by the top edge")
        top = self.b.js("document.activeElement.getBoundingClientRect().top")
        self.assertGreater(top, 0, "the focused row in view")
        self.assertLess(top, self.b.js("innerHeight / 2"), "a little above the middle, not far from it")

    def test_sorting(self):
        self.open_library("Movies", bar=True)
        self.assertEqual(self.cards()[:3], ["Movie 1", "Movie 2", "Movie 3"])
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus_text(), "Release Year")
        self.b.key("Enter")
        self.b.wait_js("document.querySelector('.grid .card .label').textContent === 'Movie 12'", msg="newest year first")
        self.assertEqual(self.focus(), "opt-sort-PremiereDate", "focus stays on the chip")
        self.assertTrue(self.b.js("document.activeElement.classList.contains('sel')"))

    def test_unwatched_filter(self):
        for m in ("m1", "m2", "m3"):
            self.mock.lib.set_played(m, True)
        self.open_library("Movies", bar=True)
        for _ in range(6):
            if self.focus_text() == "Unwatched":
                break
            self.b.key("ArrowRight", pause=0.15)
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('.grid .card').length === 9", msg="unwatched only")
        self.assertEqual(self.b.js("document.querySelector('.count').textContent"), "9")
        self.assertNotIn("Movie 1", self.cards())

    def test_genre_filter(self):
        self.open_library("Movies", bar=True)
        self.b.key("ArrowDown")
        self.assertEqual(self.focus_text(), "All Genres")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus_text(), "Action")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('.grid .card').length === 4", msg="action only")
        self.assertEqual(sorted(self.cards()), ["Movie 1", "Movie 10", "Movie 4", "Movie 7"])
        self.assertEqual(self.focus_text(), "Action")

    def test_filters_survive_going_back(self):
        self.open_library("Movies", bar=True)
        self.b.key("ArrowDown")
        self.b.key("ArrowRight")
        self.b.key("Enter")  # Action
        self.b.wait_js("document.querySelectorAll('.grid .card').length === 4")
        self.to_grid()
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'library' && document.querySelectorAll('.grid .card').length === 4", msg="filter still active")

    # ---------- Collections ----------

    def test_collections(self):
        self.open_library("Collections")
        self.assertEqual(self.cards(), ["First Collection", "Second Collection"])
        self.assertFalse(self.b.js("document.querySelector('[data-opt=genre]')"), "no genres for collections")
        self.to_grid()
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('[data-k^=\"col-\"]').length === 3", msg="movies in the collection")
        self.assertEqual(self.texts('[data-k^="col-"] .label'), ["Movie 1", "Movie 2", "Movie 3"], "in release order")
        self.assertIn("3 Movies", self.b.js("document.querySelector('.hero').textContent"))
        self.assertEqual(self.focus_text(), "Resume: Movie 1", "movie 1 is in progress")

    def test_collection_plays_first_unwatched(self):
        self.mock.lib.set_played("m1", True)
        self.b.js("navigate('item', {id: 'bx1'})")
        self.b.wait_js("document.activeElement.textContent.includes('Movie 2')", msg="next unwatched movie")

    # ---------- Search ----------

    def open_search(self):
        self.b.js("focusEl($('[data-k=tab-search]'))")  # the magnifier at the left of the bar
        self.b.key("Enter")
        self.b.wait_js("current.name === 'search'")

    def test_search_with_letter_grid(self):
        self.open_search()
        self.assertEqual(self.focus(), "key-a")
        self.b.key("ArrowDown")
        self.b.key("ArrowDown")                    # M
        self.b.key("Enter")
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")                   # O
        self.b.key("Enter")
        self.assertEqual(self.b.js("document.querySelector('#query').textContent"), "mo")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 12", msg="12 movies")
        self.assertEqual(self.focus(), "key-o", "focus stays on the grid")

    def test_caret_moves_in_the_search_text(self):
        self.open_search()
        self.b.type("mvie")
        self.b.key("ArrowUp")                      # from the letters' top row into the text
        self.assertEqual(self.focus(), "query")
        for _ in range(3):
            self.b.key("ArrowLeft")
        self.b.key("ArrowLeft")                    # at the start: stays there
        self.b.key("ArrowRight")                   # m|vie
        self.b.type("o")
        self.assertEqual(self.b.js("document.querySelector('#query').textContent"), "movie")
        self.b.key("Backspace")
        self.assertEqual(self.b.js("document.querySelector('#query').textContent"), "mvie", "deletes before the caret")
        self.b.key("ArrowDown")
        self.assertTrue(self.focus().startswith("key-"), "down goes back to the letters")

    def test_search_by_typing_from_anywhere(self):
        self.b.type("movie 1")
        self.b.wait_js("current.name === 'search'")
        self.assertEqual(self.b.js("document.querySelector('#query').textContent"), "movie 1", "as typed")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 4", msg="movies 1, 10, 11, 12")
        self.b.key("Backspace")
        self.b.key("Backspace")
        self.assertEqual(self.b.js("document.querySelector('#query').textContent"), "movie")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 12", msg="all 12 movies")

    def test_jellyfin_remote_control_on_the_page(self):
        """Jellyfin's remote control (a phone, its web app's cast menu): keys as the remote's, text into the search,
        a message shown."""
        wait_for(lambda: self.backend.device_id() in self.mock.ws_devices, msg="connected to Jellyfin")
        send = lambda kind, data: self.mock.ws_devices[self.backend.device_id()][0].append({"MessageType": kind, "Data": data})
        before = self.focus()
        send("GeneralCommand", {"Name": "MoveDown"})
        self.b.wait_js(f"document.activeElement?.dataset.k !== {json.dumps(before)}", msg="down moves the focus")
        send("GeneralCommand", {"Name": "SendString", "Arguments": {"String": "movie 1"}})
        self.b.wait_js("current.name === 'search' && $('#query').textContent === 'movie 1'", msg="the search opened with it")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 4", msg="and searched")
        send("GeneralCommand", {"Name": "SendString", "Arguments": {"String": "0\n"}})
        self.b.wait_js("$('#query').textContent === 'movie 10'", msg="typed on at the caret, the line break gone")
        send("GeneralCommand", {"Name": "DisplayMessage", "Arguments": {"Header": "Hello", "Text": "Dinner is ready"}})
        self.b.wait_js("$('#toast')?.textContent === 'Hello – Dinner is ready'", msg="the message shown")
        send("GeneralCommand", {"Name": "Back"})
        self.b.wait_js("current.name === 'home'", msg="back as the remote's")

    def test_open_search_result_and_back(self):
        self.open_search()
        self.b.type("second")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 1")
        for _ in range(8):
            if self.b.js("!!document.activeElement.closest('#results')"):
                break
            self.b.key("ArrowRight", pause=0.15)
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'", msg="collection opened")
        collection = self.b.js("current.args.id")
        self.b.key("Escape")
        self.b.wait_js(f"current.name === 'search' && reg[document.activeElement.dataset.r]?.Id === '{collection}'",
                       msg="back in the search, on that result")
        self.assertEqual(self.b.js("current.args.q"), "second", "the search kept")

    def test_big_library_shows_first_page_at_once(self):
        self.mock.lib.add_movies(100)
        self.mock.items_delay = 2
        try:
            self.b.js("libCache.clear()")
            start = time.time()
            self.open_library("Movies")
            self.assertLess(time.time() - start, 4, "does not wait for the whole library")
            self.assertEqual(self.b.js("document.querySelector('.grid-page .count').textContent"), "112", "total right away")
            self.assertEqual(self.b.js("document.querySelectorAll('.grid .card').length"), 60)
            self.b.wait_js("document.querySelectorAll('.grid .card').length === 112", timeout=6, msg="rest appended")
            labels = self.cards()
            self.assertEqual(len(set(labels)), 112, "no duplicates")
            self.assertEqual(labels[:2], ["Movie 1", "Movie 2"])
            # back and in again: kept, instant
            self.b.key("Escape")
            self.b.wait_js("current.name === 'home'")
            start = time.time()
            self.b.key("Enter")
            self.b.wait_js("current.name === 'library' && document.querySelectorAll('.grid .card').length === 112", msg="kept library")
            self.assertLess(time.time() - start, 1.5)
        finally:
            self.mock.items_delay = 0

    def test_back_from_movie_opened_on_home_goes_home(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")
        before = self.focus()
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.itemType === 'Movie'", msg="movie opened")
        self.b.key("Escape")
        self.b.wait_js(f"current.name === 'home' && document.activeElement.dataset.k === '{before}'",
                       msg="home again, on that movie, not in its library")

    def test_back_from_a_start_goes_up_to_its_bar_first(self):
        self.go_to_row("latest0")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'home' && document.activeElement.matches('.tabbar .tab.sel')",
                       msg="up to the bar")
        self.assertGreaterEqual(self.b.js("document.querySelector('.tabbar').getBoundingClientRect().top"), 0, "the bar in view")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'menu'", msg="then the main menu")

    def test_last_tile_keeps_distance_from_the_screen_edge(self):
        self.go_to_row("latest0")
        for _ in range(12):
            self.b.key("ArrowRight", pause=0.1)
        time.sleep(0.6)  # the row has slid into place
        gap_right = self.b.js("innerWidth - document.activeElement.querySelector('.art').getBoundingClientRect().right")
        pad = self.b.js("parseFloat(getComputedStyle(document.activeElement.closest('.strip')).paddingLeft)")
        self.assertGreater(gap_right, pad * 0.8, "same margin on the right as on the left")

    def test_back_after_several_similar_movies_goes_through_them(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("document.querySelector('[data-k^=similar-]')")
        self.go_to_row("similar")
        self.b.key("Enter")
        self.b.wait_js("current.args.id === 'm4' && document.querySelector('[data-k^=similar-]')")
        self.go_to_row("similar")
        self.b.key("ArrowRight")
        last = self.b.js("reg[document.activeElement.dataset.r].Id")
        self.b.key("Enter")
        self.b.wait_js(f"current.args.id === '{last}'", msg="third movie opened")
        self.b.key("Escape")
        self.b.wait_js("current.args.id === 'm4' && document.activeElement.dataset.k === 'similar-1'", msg="the second movie")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'item' && current.args.id !== 'm4'", msg="the first movie")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'home'", msg="then home")

    def test_back_from_episode_stays_one_page_back(self):
        self.go_to_row("resume")
        self.b.key("ArrowRight")  # S1 E2 of the test show, after movie 1
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.itemType === 'Episode'")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'home'", msg="episodes are not in a library grid")

    def test_back_to_tile_deep_in_a_big_library(self):
        self.mock.lib.add_movies(100)
        self.b.js("libCache.clear()")
        self.open_library("Movies")
        self.b.wait_js("document.querySelectorAll('.grid .card').length === 112", msg="whole library")
        self.b.js("focusEl(document.querySelector('.grid [data-k=m100]'), false)")
        time.sleep(0.6)
        top = "Math.round(document.activeElement.getBoundingClientRect().top)"
        before = self.b.js(top)
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.args.id === 'm100'")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'library' && document.activeElement.dataset.k === 'm100'", msg="back on movie 100")
        time.sleep(0.6)
        self.assertAlmostEqual(self.b.js(top), before, delta=2, msg="at the same height on the screen")

    def test_search_with_a_typo(self):
        self.open_search()
        self.b.type("secnod")  # swapped letters
        self.b.wait_js("document.querySelectorAll('#results .card').length === 1", msg="found despite the typo")
        self.assertEqual(self.texts("#results .card .label"), ["Second Collection"])
        self.assertIn("No exact match", self.b.js("document.querySelector('#results').textContent"))

    def test_a_typo_while_the_titles_still_load(self):
        self.mock.titles_slow = 8
        try:
            self.b.js("titles = null")  # as just started: the index not there yet
            self.open_search()
            start = time.monotonic()
            self.b.type("secnod")
            self.b.wait_js("$('#results').textContent.includes('Nothing found')", timeout=8, msg="an answer meanwhile")
            self.assertLess(time.monotonic() - start, 6, "not waiting for all the titles")
            self.b.wait_js("document.querySelectorAll('#results .card').length === 1", timeout=15, msg="then the typo's match")
            self.assertEqual(self.texts("#results .card .label"), ["Second Collection"])
        finally:
            self.mock.titles_slow = 0

    def test_typo_in_one_of_several_words(self):
        self.open_search()
        self.b.type("tset sh")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 1", msg="Test Show")
        self.assertEqual(self.texts("#results .card .label"), ["Test Show"])

    def test_exact_hits_come_first(self):
        self.open_search()
        self.b.type("first")
        self.b.wait_js("document.querySelectorAll('#results .card').length >= 1")
        self.assertEqual(self.texts("#results .card .label")[0], "First Collection")
        self.assertNotIn("No exact match", self.b.js("document.querySelector('#results').textContent"))

    def test_fuzzy_matching_rules(self):
        js = self.b.js
        self.assertEqual(js("prefixDistance('metoer', 'meteor')"), 1, "swapped letters are one typo")
        self.assertEqual(js("prefixDistance('marsi', 'marsianer')"), 0, "still typing: the start of a word counts")
        self.assertEqual(js("prefixDistance('marsinaer', 'marsianer')"), 1)
        self.assertEqual(js("words('Ein Abend im Café Zoë')"), ["ein", "abend", "im", "cafe", "zoe"])
        self.assertEqual(js("words('Straße')"), ["strasse"])
        index = "[{id: 'a', words: ['the', 'meteor']}, {id: 'b', words: ['mad', 'maximum']}, {id: 'c', words: ['cat']}]"
        self.assertEqual(js(f"fuzzyMatch('metoer', {index})"), ["a"])
        self.assertEqual(js(f"fuzzyMatch('cot', {index})"), [], "no typos in short words, they would match anything")
        self.assertEqual(js(f"fuzzyMatch('mad maxinum', {index})"), ["b"], "every word may have its own typo")

    def open_settings(self, part=None):
        """The settings' first page, as from the main menu, or one of its parts."""
        self.b.js(f"navigate('settings', {{part: {json.dumps(part or 'general')}}})")
        self.b.wait_js(f"current.name === 'settings' && document.querySelector('.settings')?.dataset.part === {json.dumps(part or 'general')}",
                       msg="settings page")

    def open_jellyfin_settings(self):
        self.b.js("focusEl($('[data-k=tab-settings]'))")  # the gear at the right of the bar
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings' && document.querySelector('.settings h1')", msg="settings page")

    def setting_values(self):
        """What each line of the settings is set to: {its key without "set-": value}."""
        return self.b.js("Object.fromEntries([...document.querySelectorAll('.srow')].map((r) => [r.dataset.k.replace('set-', ''), r.querySelector('.sval').textContent]))")

    def choose(self, opt, label):
        """OK on a setting's line, then the choice called label."""
        self.b.js(f"focusEl($('[data-k=set-{opt}]'))")
        self.b.key("Enter")
        self.b.wait_js("!$('#options').hidden", msg="its choices")
        labels = self.texts("#options .opt")
        at = self.b.js("optionAt")
        for _ in range(labels.index(label) - at):
            self.b.key("ArrowDown")
        for _ in range(at - labels.index(label)):
            self.b.key("ArrowUp")
        self.b.key("Enter")

    def test_settings_quality(self):
        self.open_jellyfin_settings()  # from Jellyfin: only those for movies and shows
        self.assertEqual(self.texts(".settings-part"), ["Movies & Shows", "Library", "Server and Sign-In"])
        self.assertEqual(self.b.js("$('[data-k=jf-username] .sval').textContent"), USERNAME, "who is signed in")
        values = self.setting_values()
        self.assertEqual((values["quality"], values["ask"], values["moviesound"]), ("Automatic", "After 3 Episodes", "Leveled"))
        self.assertIn("12.2.0", self.b.js("document.querySelector('.about').textContent"), "Jellyfin version")
        self.assertEqual(self.focus(), "set-quality")
        self.assertIn("connection", self.b.js("$('#set-aside').textContent"), "what it means, beside the list")
        self.b.key("Enter")
        self.assertEqual(self.texts("#options .opt")[:3], ["Automatic", "Original", "20 Mbit/s"])
        self.assertEqual(self.texts("#options .opt.on"), ["Automatic"], "the one set is ticked")
        self.assertEqual(self.b.js("optionAt"), 0, "and chosen")
        for _ in range(4):
            self.b.key("ArrowDown")
        self.b.key("Enter")
        try:
            wait_for(lambda: self.backend.request("/settings")[1]["quality"] == "4", msg="stored")
            self.b.wait_js("document.querySelector('[data-k=set-quality] .sval')?.textContent === '4 Mbit/s'", msg="shown")
            self.assertEqual(self.focus(), "set-quality", "focus stays on the line")
            self.b.key("Escape")
            self.b.wait_js("current.name === 'home'")
        finally:
            self.backend.request("/settings", {"quality": "auto"})

    def test_settings_skip_intros(self):
        self.open_jellyfin_settings()
        self.assertEqual(self.setting_values()["skipintro"], "Offer")
        self.b.js("focusEl($('[data-k=set-skipintro]'))")
        self.assertIn("Automatic right away", self.b.js("$('#set-aside').textContent"))
        self.b.key("Enter")
        self.assertEqual(self.texts("#options .opt"), ["Off", "Offer", "Automatic"])
        self.b.key("ArrowDown")
        self.b.key("Enter")
        try:
            wait_for(lambda: self.backend.request("/settings")[1]["skipintro"] == "auto", msg="stored")
            self.b.wait_js("document.querySelector('[data-k=set-skipintro] .sval')?.textContent === 'Automatic'", msg="shown")
        finally:
            self.backend.request("/settings", {"skipintro": "offer"})

    def test_choose_the_weathers_place(self):
        self.open_settings("saver")
        self.b.js("focusEl($('[data-k=\"set-wxplace\"]'))")
        self.assertEqual(self.b.js("$('[data-k=set-wxplace] .sval').textContent"), "Choose")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'weatherplace' && $('#results .hint')", msg="the place picker")
        self.assertEqual(self.b.js("$('#query').textContent"), "Town or city…")
        try:
            self.b.js("places = [{name: 'Testhausen', region: 'Sachsen, Deutschland', lat: 51.3, lon: 12.4}]; pickPlace(places[0])")
            self.b.wait_js("current.name === 'settings' && $('[data-k=\"set-wxplace\"] .sval')?.textContent === 'Testhausen'", msg="chosen")
            self.assertEqual(self.setting_values()["saverweather"], "Show")
        finally:
            self.backend.request("/settings", {"weather": ""})

    def test_settings_parts_screensaver_and_versions(self):
        self.open_settings()  # from the main menu: its parts
        self.assertEqual(self.texts(".srow .sname"), ["General", "Video and Audio", "Devices", "Using Matinee", "Account", "System", "Movies & Shows",
                                                     "Music", "Audiobooks", "Podcasts"],
                         "what is changed most often first, the account and the system last; the apps as in the main menu")
        self.assertEqual(self.texts(".settings-part"), ["Apps"])
        self.assertEqual(self.b.js("$('[data-k=part-movies] .sval').textContent"), "Connected", "as music's and the audiobooks'")
        self.assertEqual(self.focus(), "part-basics", "on the first")
        self.b.key("Enter")
        self.b.wait_js("document.querySelector('[data-k=set-language]')", msg="General")
        values = self.setting_values()
        self.assertEqual([values[k] for k in ("language", "part-saver")], ["English", "10 min"], "the screensaver: a page of its own")
        self.assertFalse(self.b.js("!!document.querySelector('[data-k=set-ask]')"), "that is Jellyfin's")
        self.b.key("Escape")
        self.b.wait_js("document.querySelector('[data-k=part-basics]') && document.activeElement?.dataset.k === 'part-basics'",
                       msg="back on its line")
        self.open_settings("av")
        values = self.setting_values()
        self.assertEqual([values[k] for k in ("audio", "part-subs")], ["Automatic", "Off"], "subtitles: a page of their own")
        self.open_settings("subs")
        values = self.setting_values()
        self.assertEqual([values[k] for k in ("subtitles", "sublang", "subsize")], ["Off", "Automatic", "Normal"])
        self.open_settings("movies")
        self.assertEqual([self.b.js(f"$('[data-k=jf-{k}] .sval').textContent") for k in ("password", "signin")], ["Signed In", "Connected"])
        self.open_settings("system")
        self.assertIn("Matinee", self.b.js("document.querySelector('.about').textContent"), "Matinee's version")
        self.open_settings("saver")
        try:
            self.choose("screensaver", "Off")
            wait_for(lambda: self.backend.request("/settings")[1]["screensaver"] == "off", msg="stored")
            self.b.wait_js("SAVER_AFTER === null", timeout=3, msg="applies at once")
        finally:
            self.backend.request("/settings", {"screensaver": "10"})

    def test_settings_audio_and_subtitles(self):
        self.open_settings("av")
        try:
            self.assertEqual(self.focus(), "set-audio")
            self.b.key("Enter")
            self.assertEqual(self.texts("#options .opt"), ["Automatic", "Deutsch", "English", "Original"])
            self.b.key("ArrowDown")
            self.b.key("Enter")
            self.b.wait_js("document.querySelector('[data-k=set-audio] .sval')?.textContent === 'Deutsch'", msg="set")
            self.open_settings("subs")
            self.choose("subtitles", "Always")
            self.b.wait_js("document.querySelector('[data-k=set-subtitles] .sval')?.textContent === 'Always'", msg="set")
            s = self.backend.request("/settings")[1]
            self.assertEqual((s["audio"], s["subtitles"], s["sublang"]), ("de", "always", "auto"))
        finally:
            self.backend.request("/settings", {"audio": "auto", "subtitles": "off"})

    def test_check_the_library_and_fix_after_asking(self):
        lib, mock = self.mock.lib.items, self.mock
        saved = {i: dict(lib[i]) for i in ("m3", "m7")}
        lib["m3"]["Path"] = "/media/movies/Right.Film.2013.German.1080p [tmdbid-555].mkv"
        lib["m7"]["PrimaryImageAspectRatio"] = 1.78
        mock.remote_movies = [{"Name": "Right Film", "ProductionYear": 2013, "ProviderIds": {"Tmdb": "555"},
                               "SearchProviderName": "TheMovieDb", "ImageUrl": "/jf/Items/m1/Images/Primary"}]
        mock.remote_images = {"m7": [{"Url": "/jf/Items/m2/Images/Primary", "ProviderName": "TheMovieDb", "Width": 2, "Height": 3}]}
        try:
            self.open_settings("movies")
            self.b.js("focusEl($('[data-k=set-libcheck]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'libcheck' && $('[data-k=lc-all]')", msg="the films found, with what fixes them")
            self.assertEqual(self.b.js("$('#lc-msg').textContent"), "2 movies do not match.")
            self.assertEqual(self.texts(".lc .lc-film b"), ["Movie 3", "Right Film", "Movie 7", "Movie 7"], "as it is, as it would be")
            self.assertEqual(self.texts(".lc .sval"), ["Fix", "Fix"])
            self.b.js("focusEl($('[data-k=lc-m3]'))")
            self.assertIn("another movie (TMDB 555)", self.b.js("$('#set-aside').textContent"), "why, and the file")
            self.b.key("Enter")
            self.b.wait_js("!options.hidden", msg="asks first")
            self.assertEqual(self.b.js("$('#options h2').textContent"), "Identify \u201cMovie 3 (2013)\u201d as \u201cRight Film (2013)\u201d?")
            self.b.key("ArrowDown")
            self.b.key("Enter")  # Cancel
            time.sleep(0.5)
            self.assertEqual(mock.applied, [], "nothing without a yes")
            self.b.js("focusEl($('[data-k=lc-all]'))")
            self.b.key("Enter")
            self.b.wait_js("!options.hidden", msg="asks for all")
            self.assertEqual(self.b.js("$('#options h2').textContent"), "Fix 2 movies as shown?")
            self.b.key("Enter")
            self.b.wait_js("[...document.querySelectorAll('.lc .sval')].every((x) => x.textContent === 'Fixed')", msg="fixed")
            self.assertEqual(sorted(x[0] for x in mock.applied), ["m3", "m7"])
            self.assertEqual(self.texts(".lc .lc-film b"), ["Right Film", "Movie 7"], "as it is now")
        finally:
            for i, it in saved.items():
                lib[i].clear()
                lib[i].update(it)
            mock.applied.clear()

    def test_click_sounds_to_choose_from(self):
        self.open_settings("basics")
        self.assertFalse(self.b.js("!!$('[data-k=set-soundkind]')"), "no sound to choose while the clicks are off")
        self.backend.request("/settings", {"sounds": "medium"})
        try:
            self.open_settings("basics")
            self.b.wait_js("!!$('[data-k=set-soundkind]')", msg="the sound to choose, with the clicks on")
            self.b.js("focusEl($('[data-k=set-soundkind]'))")
            self.b.key("Enter")
            self.b.wait_js("!options.hidden", msg="the sounds")
            self.assertEqual(self.texts("#options .opt"), ["Click", "Tap", "Wood", "Glass", "Soft"])
            self.assertEqual(self.b.js("optionItems.map((x) => x.sound).join()"), "click,tap,wood,glass,soft", "each heard as it is reached")
            self.b.key("ArrowDown")
            self.b.key("ArrowDown")
            self.b.key("Enter")
            wait_for(lambda: self.backend.request("/settings")[1]["soundkind"] == "wood", msg="stored")
            self.b.wait_js("SOUND_KIND === 'wood'", msg="used at once")
            # every one of them makes a sound, short enough not to blur into the next step
            self.b.js("""window.__heard = null; (async () => { const out = {}; for (const k of Object.keys(SOUND_KINDS)) {
              const ctx = new OfflineAudioContext(1, 14400, 48000); playTick(ctx, k, 0.2);
              const d = (await ctx.startRendering()).getChannelData(0); let peak = 0, last = 0;
              d.forEach((v, i) => { if (Math.abs(v) > peak) peak = Math.abs(v); if (Math.abs(v) > 0.002) last = i; });
              out[k] = [peak > 0.05, last / 48 < 150]; } __heard = out; })()""")
            self.b.wait_js("window.__heard", msg="rendered")
            self.assertTrue(all(a and b for a, b in self.b.js("Object.values(__heard)")), self.b.js("JSON.stringify(__heard)"))
        finally:
            self.backend.request("/settings", {"sounds": "off", "soundkind": "click"})
            self.b.js("SOUNDS = 0; SOUND_KIND = 'click'")

    def test_update_date_in_the_language_set(self):
        year = self.b.js("new Date().getFullYear()")
        self.assertEqual(self.b.js(f"calendarDay('{year}-11-02')"), "November 2", "git's date, not as git writes it")
        self.assertEqual(self.b.js("calendarDay('2020-03-01')"), "March 1, 2020", "the year when it is not this one")
        self.assertEqual(self.b.js("new Intl.DateTimeFormat('de', { day: 'numeric', month: 'long' }).format(new Date(2026, 10, 2))"),
                         "2. November", "in German as Germans write it")

    def test_update_tells_what_it_brings(self):
        """What an update check found: when the version here and the newest were made, on the page; what the update
        brings and what the last versions brought (the patch notes) in a line of their own, opened in the reader and
        scrolled there, as many as there are."""
        now = int(time.time())
        changes = [{"at": now - i * 60, "s": f"Change {i}"} for i in range(30)]
        recent = [{"at": now - 86400 - i * 60, "s": f"Earlier {i}"} for i in range(12)]
        update = {"behind": 40, "version": "v1-5", "at": now, "installed": now - 86400 * 3, "checked": now, "changes": changes}
        self.b.js(f"$('#view').insertAdjacentHTML('beforeend', '<div id=\"update-info\"></div>'); "
                  f"$('#update-info').innerHTML = updateInfo({json.dumps(update)})")
        self.assertEqual(self.texts("#update-info dt"), ["Installed", "Newest", "Last Checked"])
        self.assertTrue(self.b.js("$('#update-info dd').textContent").startswith("v1-5 · "), "the version, then when it was made")
        self.assertEqual(self.texts("#update-info li"), [], "no list below the lines, where nothing could scroll it")
        self.b.js("$('#update-info').remove()")
        self.assertEqual(self.b.js(f"updateNotesValue({json.dumps(update)}, [])"), "What's New · 40 changes")
        self.assertEqual(self.b.js("updateLabel({behind: 1})"), "Install Update · 1 change")
        # the account's Matinees: one off for days shows when it was last there, not how far behind it was then
        now = int(time.time())
        devices = [{"name": "TV", "self": True, "behind": 0, "version": "v2", "at": now},
                   {"name": "Laptop", "self": False, "behind": 2, "version": "v1", "at": now - 300},
                   {"name": "Old VM", "self": False, "behind": 39, "version": "v0", "at": now - 3 * 86400}]
        out = self.b.js(f"deviceLines({json.dumps(devices)}, (k, name, state, attrs, hint) => "
                        "JSON.stringify([name, state, hint, attrs.includes('data-behind')]) + '\\n')")
        rows = {r[0]: r[1:] for r in map(json.loads, out.split("\n")[:-1])}
        self.assertEqual(rows["TV (this one)"][0], "Up to date")
        self.assertEqual((rows["Laptop"][0], rows["Laptop"][2]), ("2 versions behind", True), "on now: how far behind")
        self.assertTrue(rows["Old VM"][0].startswith("Last on "), f"off for days: when it was last there, not {rows['Old VM'][0]}")
        self.assertIn("39 versions behind", rows["Old VM"][1], "how far behind it was then: in the hint")
        self.assertFalse(rows["Old VM"][2], "not marked as behind")
        # the settings' System part, with an update found and the last ones installed
        self.b.js(f"""window.realFetch = fetch; window.fetch = (u, o) => String(u) === '/settings'
          ? realFetch(u, o).then((r) => r.json()).then((s) => new Response(JSON.stringify({{ ...s, updates: true,
              update: {json.dumps(update)}, updateRecent: {json.dumps(recent)} }})))
          : realFetch(u, o)""")
        try:
            self.b.js("navigate('settings', { part: 'update' })")
            self.b.wait_js("current.name === 'settings' && $('[data-k=set-notes]')", msg="the patch notes' line")
            self.assertEqual(self.b.js("$('[data-k=set-notes] .sval').textContent"), "What's New · 40 changes")
            for _ in range(6):
                if self.focus() == "set-notes":
                    break
                self.b.key("ArrowDown")
            self.assertEqual(self.focus(), "set-notes", "reached with the arrows")
            self.b.key("Enter")
            self.b.wait_js("!reader.hidden", msg="opened")
            self.assertEqual(self.texts("#reader .update-new"), ["What's New 40 changes", "Recently Added"])
            lines = self.texts("#reader li")
            self.assertEqual(len(lines), 30 + 1 + 12, "all of them, and how many more the update brings")
            self.assertTrue(lines[0].endswith("Change 0"), "newest first")
            self.assertEqual(lines[30], "and 10 more")
            self.assertTrue(self.b.js("$('#reader .reader-box').scrollHeight > $('#reader .reader-box').clientHeight"), "longer than the box")
            self.b.key("ArrowDown")
            self.b.wait_js("$('#reader .reader-box').scrollTop > 0", msg="scrolled down")
            self.b.key("Escape")
            self.b.wait_js("reader.hidden", msg="closed")
            self.assertEqual(self.focus(), "set-notes", "back on its line")
        finally:
            self.b.js("window.fetch = realFetch")
            self.b.js("navigate('home')")

    def test_picture_card_below_the_bar_and_beside_the_title(self):
        """A title's picture card on Jellyfin's start, the title focused: below the bar at the top, right of the title and its
        text, above the rows, inside the window; on a TV and in a Mac's window, and with Reduced effects too (one title's picture is
        what is focused, not decoration)."""
        self.b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        try:
            for w, h, size in ((1920, 1080, "tv"), (1286, 820, "tv"), (1286, 820, "desk"), (2560, 1440, "desk")):
                self.b.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                self.b.js(f"document.documentElement.classList.toggle('desk', {'true' if size == 'desk' else 'false'})")
                self.b.js("document.documentElement.classList.add('light', 'plain'); navigate('home')")
                self.b.wait_js("current.name === 'home' && $('[data-k^=resume-]')", msg="Jellyfin's start")
                self.b.js("focusEl($('[data-k^=resume-]'))")  # the start comes up on its first button: no title's card
                self.b.wait_js("$('#stage-art .cover-art.on')", timeout=15, msg=f"the card at {w}×{h}")
                bar, card, title, rows = self.b.js("""[$('.tabbar'), $('#stage-art .cover-art.on'), $('#hero .title, #hero .logo'),
                  $('.home .rows')].map((e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })""")
                self.assertGreaterEqual(card[1], bar[3] + 6, f"{w}×{h} {size}: below the bar")
                self.assertLessEqual(card[3], rows[1], f"{w}×{h} {size}: above the rows, not behind them")
                self.assertGreaterEqual(card[0], title[2], f"{w}×{h} {size}: right of the title")
                self.assertLessEqual(card[2], w, f"{w}×{h} {size}: inside the window")
        finally:
            self.b.js("document.documentElement.classList.remove('light', 'plain', 'desk')")
            self.b.tv_size()
            self.b.call("Emulation.setEmulatedMedia", features=[])
            self.b.js("navigate('home')")

    def test_desk_size(self):
        """Settings → General → Size: at a desk everything smaller, more tiles to a row, nothing beyond the window's
        edge; Automatic says what it is here (the tests: a television's)."""
        self.go_to_row("latest0")
        in_view = "[...document.activeElement.closest('.strip').querySelectorAll('.card')].filter((c) => c.getBoundingClientRect().right <= innerWidth).length"
        on_tv = self.b.js(in_view)
        self.assertEqual(self.backend.request("/settings", {"size": "desk"})[0], 200)
        try:
            self.b.js("useSize('desk'); navigate('home')")
            self.go_to_row("latest0")
            time.sleep(0.5)
            self.assertGreater(self.b.js(in_view), on_tv, "more tiles to a row")
            self.assertAlmostEqual(self.b.js("parseFloat(getComputedStyle(document.documentElement).fontSize) / innerWidth"), 0.0105 * 0.75,
                                   places=4, msg="the text three quarters as large")
            for page in ("navigate('menu', {})", "navigate('library', {id: 'lm', name: 'Movies', type: 'movies'})",
                         "navigate('item', {id: 'm1'})", "navigate('settings', {})"):
                self.b.js(page)
                time.sleep(1)
                self.assertLessEqual(self.b.js("document.documentElement.scrollWidth"), self.b.js("innerWidth"), f"{page}: nothing beyond the edge")
                if "library" in page:  # a full row: as far from the right edge as from the left
                    left, right = self.b.js("""(() => { const g = $('.grid'), n = getComputedStyle(g).gridTemplateColumns.split(' ').length;
                      while (g.children.length < n) g.append(g.children[0].cloneNode(true));
                      const first = g.children[0], last = g.children[n - 1];  // the layout's, not the focused tile's grown box
                      return [first.offsetLeft - g.offsetLeft, g.offsetLeft + g.offsetWidth - last.offsetLeft - last.offsetWidth]; })()""")
                    self.assertAlmostEqual(left, right, delta=2, msg="the space left over between the tiles, not all on the right")
                    cols = self.b.js("""['grid', 'grid library-grid'].map((cls) => { const g = Object.assign(document.createElement('div'), { className: cls });
                      $('.grid-page').append(g); const n = getComputedStyle(g).gridTemplateColumns.split(' ').length; g.remove(); return n; })""")
                    self.assertEqual(cols[0], cols[1], "albums, books and the like as many to a row as a library")
            self.b.wait_js("current.name === 'settings'", msg="the settings")
            self.b.js("navigate('settings', {part: 'basics'})")
            self.b.wait_js("$('[data-k=\"set-size\"]')", msg="the size's line")
            self.b.js("focusEl($('[data-k=\"set-size\"]'))")  # its hint beside the settings
            self.b.wait_js("document.body.textContent.includes('Automatic here: Television')", msg="Automatic says what it is here")
        finally:  # the next tests at a television's size again, also when this one fails
            self.backend.request("/settings", {"size": "auto"})
            self.b.js("useSize('auto')")
        self.assertFalse(self.b.js("document.documentElement.classList.contains('desk')"), "Automatic: here a television's")

    def test_update_asks_first(self):
        # an update found: OK asks before it installs (fetch stubbed: nothing is installed)
        self.b.js("""window.installs = 0; const real = fetch;
          window.fetch = (u, o) => (String(u) === '/update/install' ? (installs++, Promise.resolve(new Response('{}'))) : real(u, o));
          window.upd = Object.assign(document.createElement('div'), { innerHTML: '<span class="sval"></span>' });
          upd.dataset.ready = '1'; upd.dataset.install = '1'; softwareUpdate(upd)""")
        self.assertFalse(self.b.js("options.hidden"), "a question first")
        self.assertEqual(self.texts("#options .opt"), ["Update Now", "Cancel"])
        self.assertIn("restart", self.b.js("$('#options h2').textContent"))
        self.b.key("Escape")
        self.assertTrue(self.b.js("options.hidden"))
        self.b.js("softwareUpdate(upd)")
        self.b.key("ArrowDown")
        self.b.key("Enter")
        self.assertEqual(self.b.js("installs"), 0, "nothing installed when cancelled")
        self.b.js("softwareUpdate(upd)")
        self.b.key("Enter")
        self.b.wait_js("installs === 1", msg="installed once confirmed")

    def test_restart_and_shut_down_ask_first(self):
        self.b.js("""window.sys = []; const real = fetch;
          window.fetch = (u, o) => (String(u) === '/system' ? (sys.push(JSON.parse(o.body).action), Promise.resolve(new Response('{}'))) : real(u, o));
          window.el = document.createElement('div'); systemAction(el, 'poweroff')""")
        self.assertEqual(self.b.js("$('#options h2').textContent"), "Shut down the computer now?")
        self.assertEqual(self.texts("#options .opt"), ["Shut Down", "Cancel"])
        self.b.key("ArrowDown")
        self.b.key("Enter")
        self.b.js("systemAction(el, 'reboot')")
        self.assertEqual(self.texts("#options .opt"), ["Restart", "Cancel"])
        self.b.key("Enter")
        self.b.wait_js("sys.length === 1", msg="sent once confirmed")
        self.assertEqual(self.b.js("sys"), ["reboot"], "not the one cancelled")

    def test_tailscale_under_system(self):
        rows = "(rows) => rows.map((r) => r.join('=')).join(';')"
        running = self.b.js("tailscaleAbout({ state: 'Running', name: 'tv', dns: 'tv.example.ts.net', ips: ['100.64.0.1'],"
                            f" tailnet: 'example.org', online: 1, peers: 2, exit: null }}, {rows})")
        self.assertIn("Status=Connected;This device=tv.example.ts.net;Address=100.64.0.1;Tailnet=example.org;Devices=1 of 2 online", running)
        self.assertNotIn("Exit", running, "no exit node in use")
        self.assertTrue(self.b.js(f"tailscaleAbout({{ state: 'NeedsLogin' }}, {rows}).endsWith('Status=Not signed in')"), "only the state")
        self.assertEqual(self.b.js(f"tailscaleAbout(null, {rows})"), "", "nothing without Tailscale")

    def test_settings_scroll_with_the_focus(self):
        self.open_settings()
        for _ in range(12):  # down to the last line
            self.b.key("ArrowDown", pause=0.15)
        self.assertEqual(self.focus(), "part-podcasts", "the last one")
        time.sleep(0.6)
        bottom = self.b.js("document.activeElement.getBoundingClientRect().bottom")
        self.assertLess(bottom, self.b.js("innerHeight"), "the last line is on the screen")
        for _ in range(12):
            self.b.key("ArrowUp", pause=0.15)
        time.sleep(0.6)
        self.assertGreaterEqual(self.b.js("document.activeElement.getBoundingClientRect().top"), 0, "and the first one again")

    def test_less_motion(self):
        """Motion Less: nothing springs or glides, but the busy ring turns; the system asking for it does the same."""
        on = lambda: self.b.js("document.documentElement.classList.contains('less-motion')")
        card = "parseFloat(getComputedStyle(document.querySelector('.card') ?? document.body).transitionDuration)"
        ring = "(() => { document.body.classList.add('busy'); const a = getComputedStyle(document.body, '::after'); " \
               "const r = [a.animationIterationCount, a.animationDuration]; document.body.classList.remove('busy'); return r; })()"
        try:
            self.assertFalse(on())
            self.assertGreater(self.b.js(card), 0.1, "the tiles spring as they are focused")
            self.b.js("navigate('settings', {part: 'basics'})")
            self.b.wait_js("$('[data-k=set-motion]')", msg="the line in the settings")
            self.b.js("focusEl($('[data-k=set-motion]'))")
            self.b.key("Enter")
            self.b.wait_js("!$('#options').hidden", msg="its choices")
            self.assertEqual(self.texts("#options .opt"), ["Normal", "Less"])
            self.b.key("ArrowDown")
            self.b.key("Enter")
            wait_for(lambda: self.backend.request("/settings")[1]["motion"] == "less", msg="kept")
            self.assertTrue(on())
            self.assertLess(self.b.js(card), 0.001, "no spring, no glide")
            self.assertEqual(self.b.js(ring), ["infinite", "0.8s"], "the busy ring still turns: something is coming")
            self.b.js("useMotion('full')")
            self.assertFalse(on())
            self.b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
            self.b.js("useMotion('full')")
            self.assertTrue(on(), "the system asking for less motion: the same")
        finally:
            self.b.call("Emulation.setEmulatedMedia", features=[])
            self.backend.request("/settings", {"motion": "full"})
            self.b.js("useMotion('full')")

    def test_reduced_effects(self):
        """Full, Calm (plain alone), Reduced (light and plain together) and Automatic; the choices from before (plain, black)
        are Reduced."""
        dock_blur = "getComputedStyle($('.menu .tiles') ?? document.body).backdropFilter"
        on = lambda c: self.b.js(f"document.documentElement.classList.contains('{c}')")
        try:
            self.backend.request("/settings", {"effects": "full"})
            self.b.js("useEffects('full'); render({name: 'menu'})")
            self.b.wait_js("$('.menu .tiles')", msg="the menu")
            self.assertFalse(on("light") or on("plain"))
            self.assertIn("blur", self.b.js(dock_blur), "frosted glass on the dock")
            self.assertIn("url(", self.b.js(dock_blur), "liquid glass: the picture bent at its rim")
            self.assertEqual(self.backend.request("/settings", {"effects": "calm"})[0], 200)
            self.b.js("useEffects('calm')")
            self.assertTrue(on("plain") and not on("light"), "Calm: the pictures left out, not the glass")
            self.assertIn("url(", self.b.js(dock_blur), "the dock's liquid glass stays")
            self.b.js("$('#stage-art').removeAttribute('data-url'); stageArt('/jf/Items/m1/Images/Backdrop')")
            time.sleep(0.5)
            self.assertFalse(self.b.js("!!$('#stage-art > img')"), "no picture across the top")
            self.backend.request("/settings", {"effects": "light"})
            self.b.js("useEffects('light')")
            self.assertTrue(on("light") and on("plain"), "Reduced: both")
            self.assertEqual(self.b.js(dock_blur), "none", "no glass to blur again for every frame")
            self.assertIn("blur", self.b.js("getComputedStyle($('#reader')).backdropFilter"),
                          "the reader's page behind blurred all the same: nothing moves under it")
            self.assertEqual(self.b.js("getComputedStyle(document.documentElement).getPropertyValue('--dur-focus').trim()"), "0.18s")
            self.assertEqual(self.b.js("getComputedStyle(document.documentElement).getPropertyValue('--glass-blur').trim()"), "none")
            self.b.js("$('#stage-art').removeAttribute('data-url'); stageArt('/jf/Items/m1/Images/Backdrop')")
            time.sleep(0.5)
            self.assertFalse(self.b.js("!!$('#stage-art > img')"), "no picture across the top (a focused title's card may come)")
            self.b.js("stageArt('/jf/Items/m2/Images/Backdrop', null, 'square', true)")
            self.b.wait_js("$('#stage-art > img.on')", msg="but a film's own picture on its page")
            self.assertNotEqual(self.b.js("getComputedStyle($('#stage-art')).display"), "none")
            self.assertEqual(self.b.js("getComputedStyle($('#stage-art > img')).animationName"), "none", "standing still")
            self.b.js("stageArt('/jf/Items/m3/Images/Primary', null, 'cover')")
            self.b.wait_js("$('#stage-art .cover-art.on')", msg="an album's or a book's cover too: what is focused")
            self.b.js("setBackdrop('/jf/Items/m1/Images/Backdrop?a=1')")
            self.b.wait_js("$('#backdrop canvas.on')", msg="the blurred backdrop behind everything stays")
            for old in ("plain", "black"):
                self.b.js(f"useEffects('full'); useEffects('{old}')")
                self.assertTrue(on("light") and on("plain"), f"{old}, from before: Reduced")
            self.b.js("Object.defineProperty(navigator, 'hardwareConcurrency', {value: 4, configurable: true}); useEffects('auto')")
            self.assertTrue(on("light") and on("plain"), "automatic: Reduced on four cores")
            self.b.js("Object.defineProperty(navigator, 'hardwareConcurrency', {value: 10, configurable: true}); useEffects('auto')")
            self.assertEqual(on("light"), self.b.js("(navigator.deviceMemory || 8) <= 4"), "and full on ten, unless memory is short")
            self.b.js("useEffects('full')")
            self.assertFalse(on("light") or on("plain"))
        finally:
            self.backend.request("/settings", {"effects": "auto"})

    def test_effects_preview(self):
        self.open_settings("basics")
        self.b.js("focusEl($('[data-k=set-effects]'))")
        self.b.wait_js("$('.settings-side.fx .fx-preview')", msg="a small main menu in place of the symbol")
        now = self.b.js("fxNow()")
        self.assertEqual(self.b.js("$('.fx-preview').dataset.fx"), now, "as set")
        self.assertEqual(self.b.js("getComputedStyle($('.settings-side .settings-icon')).display"), "none")
        self.b.key("Enter")
        self.b.wait_js("!options.hidden", msg="the choices")
        seen = []
        for _ in range(3):
            self.b.key("ArrowDown")
            seen.append(self.b.js("$('.fx-preview').dataset.fx"))
        self.assertEqual(seen, ["full", "calm", "light"], "each choice shown as it is reached")
        self.assertGreater(self.b.js("+getComputedStyle($('.settings-side')).zIndex"), self.b.js("+getComputedStyle(options).zIndex"),
                           "above the choices' shade")
        self.b.key("Escape")
        self.b.wait_js(f"options.hidden && $('.fx-preview').dataset.fx === {json.dumps(now)}", msg="back to what is set")
        self.assertEqual(self.b.js("fxNow()"), now, "only shown, not set")
        self.b.key("ArrowDown")
        self.b.wait_js("!$('.settings-side.fx')", msg="the symbol again on the next line")

    def test_controls_page(self):
        self.open_settings("general")
        self.b.js("focusEl($('[data-k=set-controls]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'controls'", msg="the controls")
        self.assertEqual(self.b.js("document.querySelectorAll('.controls .srow').length"), 25,
                         "the keys browsing and playing, how to switch off, an Xbox controller's, a keyboard's")
        self.assertEqual(self.b.js("$('[data-k=ctl-pab] .sval').textContent"), "OK / Back", "a controller's A and B")
        self.assertRegex(self.b.js("$('[data-k=ctl-kapps] .sname').textContent"), r"^(⌘|Ctrl) 1 … 9$", "a keyboard's shortcuts with its own key")
        self.assertEqual(self.b.js("$('[data-k=ctl-updown] .sval').textContent"), "Volume", "while playing: up and down, the volume")
        self.assertEqual(self.b.js("$('[data-k=ctl-info] .sname').textContent"), "Menu", "and Menu the info panel")
        self.b.js("focusEl($('[data-k=ctl-hold]'))")
        self.assertIn("My List", self.b.js("$('#set-aside').textContent"), "what a key does, in full beside the list")
        self.assertEqual(self.b.js("$('.settings-icon').dataset.tint"), "blue", "its page's symbol")
        self.b.js("navigate('home')")
        self.b.wait_js("current.name === 'home' && $('.home .card')", msg="Jellyfin's start")
        self.b.js("openOptions($('.home .card'))")
        self.assertIn("Using Matinee", self.b.js("[...document.querySelectorAll('#options .opt')].map((o) => o.textContent)"),
                      "reached from any held OK too")
        self.b.key("Escape")

    def test_simple_settings_with_a_code(self):
        # a remote's digit keys (the test's key() has none of them)
        digits = lambda code: self.b.js(f"[...'{code}'].forEach((d) => document.dispatchEvent(new KeyboardEvent('keydown', {{key: d}})))")
        try:
            self.open_settings("system")
            self.choose("maxage", "Up to 12")  # the age limit, kept by the code from here on
            self.b.wait_js("$('[data-k=set-maxage] .sval')?.textContent === 'Up to 12'", msg="the age limit set")
            self.assertEqual(self.backend.request("/settings")[1]["maxage"], 12)
            self.b.js("focusEl($('[data-k=set-simple]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'pin' && $('[data-k=pin-1]')", msg="choose a code")
            digits("12")
            self.b.key("Backspace")
            self.assertEqual(self.b.js("current.name + ' ' + pinCode"), "pin 1", "Backspace: a digit taken back")
            self.b.key("Backspace")
            self.b.key("Backspace")
            self.b.wait_js("current.name === 'settings'", msg="none left: Back, as everywhere")
            self.b.js("focusEl($('[data-k=set-simple]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'pin' && $('[data-k=pin-1]')", msg="choose a code")
            digits("1234")
            self.b.wait_js("current.args.step === 1", msg="once more")
            digits("5678")
            self.b.wait_js("!current.args.step && $('#pin-msg').textContent.includes('not the same')", msg="not the same: from the start")
            digits("1234")
            self.b.wait_js("current.args.step === 1")
            self.b.js("focusEl($('[data-k=pin-1]'))")
            for d in ("1", "2", "3"):  # on the pad, as with the remote's arrows and OK
                self.b.js(f"focusEl($('[data-k=pin-{d}]'))")
                self.b.key("Enter", pause=0.05)
            digits("4")
            self.b.wait_js("current.name === 'settings' && SIMPLE", msg="on, back on the settings")
            self.open_settings("general")
            keys = self.b.js("[...document.querySelectorAll('.settings .srow')].map((r) => r.dataset.k)")
            self.assertNotIn("part-movies", keys, "no apps' settings")
            self.assertNotIn("part-account", keys, "no account")
            self.assertIn("part-basics", keys)
            self.assertIn("part-system", keys)
            self.b.js("navigate('settings', {part: 'account'})")
            self.b.wait_js("current.name === 'settings' && $('.settings').dataset.part === 'general'", msg="the account's part not reachable")
            self.b.js("render({name: 'menu'})")
            self.b.wait_js("$('.menu .tiles .card')", msg="the main menu")
            self.assertNotIn("Music", self.texts(".menu .tiles .label"), "a service not set up is not in the dock")
            self.open_settings("system")
            self.assertNotIn("Tailscale", self.b.js("document.querySelector('.settings').textContent"))
            self.assertIn("Up to 12", self.b.js("$('[data-k=set-maxage]').textContent"), "the age limit shown")
            self.b.js("focusEl($('[data-k=set-maxage]'))")
            self.b.key("Enter")
            time.sleep(0.3)
            self.assertTrue(self.b.js("$('#options').hidden"), "and not changed without the code")
            self.b.js("focusEl($('[data-k=set-simple]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'pin' && current.args.mode === 'off'", msg="enter the code")
            digits("9999")
            self.b.wait_js("$('#pin-msg').textContent === 'Wrong code.'", timeout=5, msg="a wrong one")
            self.assertTrue(self.b.js("SIMPLE"), "still on")
            digits("1234")
            self.b.wait_js("current.name === 'settings' && !SIMPLE", timeout=5, msg="off again")
            self.open_settings("general")
            self.assertIn("part-account", self.b.js("[...document.querySelectorAll('.settings .srow')].map((r) => r.dataset.k)"))
        finally:
            self.backend.request("/simple", {"on": False, "pin": "1234"})
            self.backend.request("/settings", {"maxage": "off"})
            self.b.js("SIMPLE = false")

    def test_tip_for_a_held_ok(self):
        self.b.js("localStorage.setItem('kino-tip-hold', '0'); tipGen = -1")
        self.go_to_row("latest0")
        self.b.wait_js("document.activeElement.querySelector('.tip.on')", timeout=4, msg="the tip on the tile, after a moment")
        self.assertEqual(self.b.js("document.activeElement.querySelector('.tip').textContent"), "More options: hold OK")
        self.b.key("ArrowRight")
        self.assertFalse(self.b.js("!!document.querySelector('.card .tip.on')"), "gone when the focus moves on")
        self.b.js("document.body.classList.add('pointer'); localStorage.setItem('kino-tip-hold', '0'); tipGen = -1;"
                  "onFocus(document.activeElement)")
        self.b.wait_js("document.activeElement.querySelector('.tip.on')", timeout=4, msg="the tip, with a mouse")
        self.assertEqual(self.b.js("document.activeElement.querySelector('.tip').textContent"), "More options: right click",
                         "what the mouse does for them")
        self.b.js("document.body.classList.remove('pointer'); dropTip()")
        self.b.js("localStorage.setItem('kino-tip-hold', '0'); tipGen = -1")
        self.b.key("ArrowRight")
        self.b.wait_js("document.activeElement.querySelector('.tip.on')", timeout=4, msg="once more")
        self.b.js("openOptions(document.activeElement)")
        self.assertFalse(self.b.js("!!document.querySelector('.card .tip.on')"), "gone once they are open")
        self.assertEqual(self.b.js("localStorage.getItem('kino-tip-hold')"), "-1", "and never again")
        self.b.key("Escape")

    def test_find_subtitles_for_a_movie(self):
        # from a movie's tile: Find Subtitles among its options
        self.b.js("navigate('search', {q: 'movie 1'})")
        self.b.wait_js("$('#results .card[data-id=m1]') || $('#results .card')", msg="the movie found")
        self.b.js("focusEl($('#results .card'))")
        self.b.js("openOptions(document.activeElement)")
        self.b.wait_js("!$('#options').hidden", msg="its options")
        labels = self.texts("#options .opt")
        self.assertIn("Find Subtitles", labels)
        self.b.js("[...document.querySelectorAll('#options .opt')].find((o) => o.textContent === 'Find Subtitles').click()")
        self.b.wait_js("current.name === 'subsearch' && $('#sublist .chip')", msg="the search")
        self.assertEqual(self.texts("#sublist .chip.sel"), ["English"], "in the subtitle language set")
        self.assertIn("No subtitles found in this language", self.b.js("$('#sublist .hint').textContent"))
        self.b.js("$('[data-k=sublang-de]').click()")
        self.b.wait_js("current.args.lang === 'de' && $('#sublist .srow .sname')", msg="the German ones")
        self.assertEqual(self.texts("#sublist .sname"), ["Movie.1.German.srt", "Bad id"])
        self.assertIn("Exactly this file", self.texts("#sublist .sval")[0])
        self.b.js("$('[data-k=sub-0]').click()")
        self.b.wait_js("current.name !== 'subsearch'", msg="back once it is downloaded")
        self.assertEqual(self.mock.subs_downloaded, [("m1", "os-ger-1")])

    def test_service_not_answering_tries_again(self):
        self.b.js("RETRY_AFTER = 600000; ndSession = null; const f = fetch; fetch = (u, o) => String(u).startsWith('/music/session') && !window.healed"
                  " ? Promise.resolve(new Response(JSON.stringify({configured: true, ready: false, error: 'nd_unreachable'}), {headers: {'Content-Type': 'application/json'}}))"
                  " : f(u, o)")
        self.b.js("render({name: 'music'})")
        self.b.wait_js("$('.failed-title')", msg="the failed page, not the sign-in form")
        self.assertEqual(self.b.js("$('.failed-why').textContent"), "Navidrome is not reachable.")
        self.assertFalse(self.b.js("!!$('.jf-signin')"), "no server form")
        self.assertEqual(self.focus(), "retry")
        self.assertTrue(self.b.js("!!$('.failed-icon svg')"), "Navidrome's symbol above")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus(), "failed-settings", "its settings a button away")
        # tried again by itself and failed again: the page built anew, the focus left on the settings, not back on Retry
        self.b.js("$('.failed').dataset.before = '1'; retryRender()")
        self.b.wait_js("$('.failed') && !$('.failed').dataset.before", msg="the failed page built anew")
        self.assertEqual(self.focus(), "failed-settings", "the focus kept where it was")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings' && $('.settings').dataset.part === 'navidrome'", msg="Navidrome's settings")

    def test_starting_shows_what_starts(self):
        self.go_to_row("latest0")
        self.b.js("curtain.classList.add('on'); curtainTitle(document.activeElement)")
        name = self.b.js("reg[document.activeElement.dataset.r].Name")
        self.assertEqual(self.b.js("$('#curtain .curtain-title').textContent").split(" · ")[-1], name.split(" · ")[-1] if name else "")
        self.assertFalse(self.b.js("curtain.classList.contains('slow')"), "a quick start: only black")
        self.b.wait_js("curtain.classList.contains('slow')", timeout=3, msg="after a moment: what starts")
        self.b.js("curtain.classList.remove('on', 'slow')")

    def test_settings_symbols(self):
        for part in ("general", "basics", "av", "system", "account"):
            self.open_settings(part)
            self.assertEqual(self.b.js("[...document.querySelectorAll('.settings .srow')].map((r) => r.dataset.k)"
                                       ".filter((k) => !SET_ICONS[iconKey(k)] && k !== 'set-effects')"), [], f"each line of {part} has its own symbol (effects: a preview)")
        self.open_settings("basics")
        self.b.js("focusEl($('[data-k=set-language]'))")
        self.assertEqual(self.b.js("[$('.settings-icon').dataset.tint, !!$('.settings-icon svg.line')]"), ["blue", True])
        self.b.js("focusEl($('[data-k=set-startwith]'))")
        self.assertEqual(self.b.js("$('.settings-icon').dataset.tint"), "orange", "it changes with the focus")

    def test_settings_of_each_area(self):
        self.open_jellyfin_settings()
        for _ in range(12):  # past the server and sign-in
            if self.focus() == "set-general":
                break
            self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "set-general", "a way to the general ones")
        self.b.key("Enter")
        self.b.wait_js("document.querySelector('[data-k=part-basics]')", msg="all settings")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'settings' && !document.querySelector('[data-k=part-basics]')", msg="back to Jellyfin's")

    def test_way_back_before_the_music_bar(self):
        """Down while the page still glides from a step up finds nothing below yet: back where the step came from, not
        into the bar of the music playing; below the last row without a way back, the bar."""
        self.go_to_row("latest0")
        self.b.js("window.stepFrom = document.activeElement")
        self.b.js("nowbar.hidden = false; nowbar.innerHTML = '<div><b>Song</b><span>Artist</span></div>';"
                  "document.body.classList.add('with-nowbar')")
        try:
            last = "[...view.querySelectorAll('.f')].filter((e) => e.offsetWidth).at(-1)"
            self.b.js(f"wayBack = null; focusEl({last}, false); move('down')")
            self.assertEqual(self.b.js("document.activeElement.id"), "nowbar", "below the last row: the bar")
            self.b.js(f"focusEl({last}, false); wayBack = {{ from: stepFrom, to: document.activeElement, dir: 'up' }}; move('down')")
            self.assertTrue(self.b.js("document.activeElement === stepFrom"), "the way back, not the bar")
        finally:
            self.b.js("nowbar.hidden = true; document.body.classList.remove('with-nowbar'); wayBack = null")

    def test_bluetooth(self):
        self.open_settings()
        self.b.js("activate(document.querySelector('[data-k=set-bluetooth]'))")
        self.b.wait_js("current.name === 'bluetooth' && document.querySelector('[data-k=\"bt-11:22:33:00:00:03\"]')", msg="the speaker nearby")
        self.assertEqual(self.texts("#btlist h2"), ["My Devices", "Nearby"])
        self.assertIn("Speaker · Not Reachable", self.b.js("document.querySelector('[data-k=\"bt-34:0E:22:00:00:07\"]').textContent"))
        self.assertIn("Shelf Speaker does not answer", self.b.js("$('#btlist .bt-down').textContent"),
                      "and what that mostly means")
        self.b.js("focusEl(document.querySelector('[data-k=\"bt-34:0E:22:00:00:07\"]'), false)")
        self.assertIn("Shelf Speaker does not answer", self.b.js("$('#set-aside').textContent"),
                      "beside the list too, while it is focused, as every settings line tells about itself")
        self.assertEqual(self.b.js("document.querySelectorAll('#btlist .srow').length"), 3, "lines, as the other settings")
        headset = {"mac": "00:02:5B:00:00:04", "name": "Desk Speaker", "icon": "audio-headset", "paired": True, "connected": True}
        self.b.js(f"btRefresh({{devices: [{json.dumps(headset)}]}})")
        self.assertEqual(self.b.js("document.querySelector('[data-k=\"bt-00:02:5B:00:00:04\"] .sval').textContent"), "Connected",
                         "a headset's class names no kind: speakers with a microphone tell it too")
        self.b.js("btRefresh()")
        self.b.wait_js("document.querySelector('[data-k=\"bt-11:22:33:00:00:03\"]')", msg="the list as it is again")
        self.b.js("activate(document.querySelector('[data-k=\"bt-50:F5:DA:00:00:02\"]'))")
        self.b.wait_js("$('#toast').textContent.startsWith('Remote stays connected')", msg="the remote is not disconnected")
        self.b.js("activate(document.querySelector('[data-k=\"bt-11:22:33:00:00:03\"]'))")
        self.b.wait_js("$('#toast').textContent === 'Connected: Box'", msg="paired and connected")
        self.b.wait_js("document.querySelector('[data-k=\"bt-11:22:33:00:00:03\"]')?.textContent.includes('Speaker · Connected')", msg="shown as connected")
        self.assertEqual(self.texts("#btlist h2"), ["My Devices", "Nearby"])
        self.b.js("activate(document.querySelector('[data-k=\"bt-34:0E:22:00:00:07\"]'))")
        self.b.wait_js("$('#toast').textContent.includes('not connected to another device')",
                       msg="paired before and not answering: most likely another device has it, not a pairing mode")
        # a controller paired, switched off and on again without coming back by itself: connected, not "stays
        # connected"
        state = json.loads(self.bt_state.read_text())
        pad = "44:16:22:00:00:08"
        state["devices"][pad] = {"name": "Xbox Wireless Controller", "icon": "input-gaming", "known": True, "paired": True,
                                 "trusted": True}
        self.bt_state.write_text(json.dumps(state))
        try:
            self.b.js("btRefresh()")
            self.b.wait_js(f"document.querySelector('[data-k=\"bt-{pad}\"]')", msg="the controller listed")
            self.b.js(f"activate(document.querySelector('[data-k=\"bt-{pad}\"]'))")
            self.b.wait_js("$('#toast').textContent === 'Connected: Xbox Wireless Controller'", msg="connected, not a hint")
        finally:
            state = json.loads(self.bt_state.read_text())
            state["devices"].pop(pad, None)
            self.bt_state.write_text(json.dumps(state))

    def test_restart_refused_on_a_desktop(self):
        self.open_settings()
        self.assertEqual(self.b.js("document.querySelectorAll('[data-action=system]').length"), 0, "only in cage")
        self.b.js("systemAction(document.activeElement, 'reboot')")
        self.assertEqual(self.b.js("$('#options h2').textContent"), "Restart the computer now?")
        self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Could not save'", msg="the backend refuses on a desktop")

    def test_settings_language_switches_the_whole_ui(self):
        self.open_settings("basics")
        self.choose("language", "Deutsch")
        self.b.wait_js("document.documentElement.lang === 'de' && current?.name === 'settings' && document.querySelector('.settings h1')",
                       timeout=15, msg="reloaded in German, back on the settings page")
        self.assertEqual(self.b.js("document.querySelector('.settings h1').textContent"), "Einstellungen")
        self.choose("language", "English")
        self.b.wait_js("document.documentElement.lang === 'en' && document.querySelector('.settings h1')?.textContent === 'Settings'",
                       timeout=15, msg="and back to English")

    def test_search_without_results(self):
        self.open_search()
        self.b.type("xyz")
        self.b.wait_js("document.querySelector('#results').textContent.includes('Nothing found')")

    # ---------- Watched state ----------

    def test_long_press_ok_opens_the_options(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")  # movie 2
        self.b.key("Enter", hold_ms=1000)
        self.assertEqual(self.texts("#options .opt")[:2], ["Add to My List", "Mark as Watched"])
        self.assertEqual(self.view(), "home", "a long press opens nothing else")
        self.b.key("ArrowDown")
        self.b.key("Enter")
        wait_for(lambda: self.mock.lib.userdata["m2"]["Played"], msg="movie 2 watched")
        self.b.wait_js("document.querySelector('[data-k=\"latest0-1\"] .watched')", msg="check mark on the tile")

    def test_menu_key_is_the_volume(self):
        self.go_to_row("latest0")
        start = self.focus()
        self.b.key("ContextMenu")  # not the TV computer: its desktop has the volume
        self.b.wait_js("$('#toast').textContent === 'The volume is set on this computer itself.'", msg="says where")
        self.assertTrue(self.b.js("$('#options').hidden && $('#volume').hidden"))
        self.b.js("volumeBox.hidden = false; showVolume(45)")  # as on the TV computer
        # the browser's own context menu the key brings with it (Chromium on Linux): the volume stays
        self.b.js("document.dispatchEvent(new MouseEvent('contextmenu', {button: 0, bubbles: true}))")
        self.assertFalse(self.b.js("$('#volume').hidden"), "the Menu key's context menu does not close it")
        self.assertEqual(self.view(), "home")
        self.assertEqual(self.b.js("$('#volume span').textContent"), "45 %")
        self.b.key("Escape")
        self.assertTrue(self.b.js("$('#volume').hidden"), "Back closes it")
        self.assertEqual((self.view(), self.focus()), ("home", start), "and nothing else happened")

    def test_volume_keys_anywhere(self):
        """A keyboard's or a speaker's volume keys change the volume on any page, as in the player (fetch stubbed:
        the TV computer's /volume)."""
        self.go_to_row("latest0")
        start = self.focus()
        self.b.js("""window.vol = 40; window.steps = []; window.realFetch = fetch; const real = fetch;
          window.fetch = (u, o) => (String(u) === '/volume' ? (steps.push(JSON.parse(o.body).step), vol += JSON.parse(o.body).step,
            Promise.resolve(new Response(JSON.stringify({ volume: vol })))) : real(u, o))""")
        try:
            self.b.key("AudioVolumeUp")
            self.b.wait_js("!$('#volume').hidden && $('#volume span').textContent === '45 %'", msg="louder, and shown")
            self.b.key("AudioVolumeDown")
            self.b.key("AudioVolumeDown")
            self.b.wait_js("$('#volume span').textContent === '35 %'", msg="quieter, also with the bar open")
            self.assertEqual(self.b.js("steps"), [5, -5, -5])
            self.assertEqual((self.view(), self.focus()), ("home", start), "nothing else happened")
        finally:
            self.b.js("closeVolume(); window.fetch = realFetch")

    def test_short_ok_does_not_mark(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.assertFalse(self.mock.lib.userdata["m1"]["Played"])

    def mouse(self, kind, selector, button="none", **kw):
        """A mouse event at the middle of what the selector finds (CDP: as a real mouse would send it)."""
        x, y = self.b.js(f"(() => {{ const r = $('{selector}').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()")
        self.b.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, **kw)
        return x, y

    def click(self, selector, button="left", hold=0.0):
        # where it stands once the page has stopped gliding (after a key or the wheel; slow on a busy machine)
        rect = f"JSON.stringify(document.querySelector({json.dumps(selector)})?.getBoundingClientRect())"
        end, last = time.time() + 3, None
        while time.time() < end and (now := self.b.js(rect)) != last:
            last = now
            time.sleep(0.1)
        self.mouse("mouseMoved", selector)
        self.mouse("mousePressed", selector, button=button, clickCount=1)
        time.sleep(hold)
        self.mouse("mouseReleased", selector, button=button, clickCount=1)
        time.sleep(0.35)

    def test_a_mouse(self):
        self.go_to_row("resume")
        self.assertEqual(self.focus(), "resume-0")
        pointer = "document.body.classList.contains('pointer')"
        self.assertFalse(self.b.js(pointer), "no pointer before the mouse moves")
        self.b.call("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5, button="none")
        self.mouse("mouseMoved", "[data-k=resume-1]")
        self.assertTrue(self.b.js(pointer), "it shows once the mouse moves")
        self.assertEqual(self.focus(), "resume-1", "what it is over takes the focus")
        long = "A Title Far Too Long To Fit Under Its Tile, Going On And On"
        self.b.js(f"$('[data-k=resume-1] .label').textContent = '{long}'")
        self.mouse("mouseMoved", "[data-k=resume-1] .label")
        self.assertEqual(self.b.js("$('[data-k=resume-1] .label').title"), long, "a cut title shows all of itself")
        self.mouse("mouseMoved", "[data-k=resume-0] .label")
        self.assertEqual(self.b.js("$('[data-k=resume-0] .label').title"), "", "one that fits: nothing besides")
        self.mouse("mouseMoved", "[data-k=resume-1]")
        self.b.key("ArrowLeft")
        self.assertFalse(self.b.js(pointer), "a key hides it")
        self.assertEqual(self.focus(), "resume-0", "the keys go on from there")
        self.click("[data-k=resume-0]", button="right")
        self.assertFalse(self.b.js("$('#options').hidden"), "a right click on it: its options, as OK held")
        self.assertIn("Remove from This Row", self.texts("#options .opt"))
        self.assertEqual(self.b.js("current.name"), "home", "and nothing opened besides")
        self.click("#options .opt", button="right")
        self.assertTrue(self.b.js("$('#options').hidden"), "a right click on them closes them, as Back")
        self.mouse("mouseMoved", "[data-k=resume-0]")
        self.mouse("mouseWheel", "[data-k=resume-0]", deltaX=0, deltaY=120)
        self.b.wait_js("movedY($('.rows-track')) > 0", msg="the wheel moves the page")
        in_view = "(() => { const r = document.activeElement.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()"
        self.b.wait_js(f"!$('.wheeling') && {in_view}", msg="once it stands: the focus on what the pointer is over")
        there = f"[data-k={self.focus()}]"
        self.click(there)
        self.b.wait_js("current.name === 'item'", msg="a click is OK")
        self.click("body", button="right")
        self.b.wait_js("current.name === 'home'", msg="a right click goes back")
        self.click(there)
        self.b.wait_js("current.name === 'item'", msg="in again")
        self.assertEqual(self.b.js("getComputedStyle($('#mouseback')).display"), "flex", "with the pointer: a back button")
        self.click("#mouseback")
        self.b.wait_js("current.name === 'home'", msg="it goes back")
        self.mouse("mouseMoved", "[data-k=resume-0]")
        self.click("#mouseback")
        self.b.wait_js("current.name === 'menu'", msg="on a page with tabs: straight back, not up to the tabs first")
        self.b.js("focusEl($('[data-k=menu-jellyfin]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'home' && $('[data-k=resume-0]')", msg="home again")
        self.b.key("ArrowDown")
        self.assertEqual(self.b.js("getComputedStyle($('#mouseback')).display"), "none", "gone with the pointer")
        self.b.js("navigate('menu', {})")
        self.b.wait_js("current.name === 'menu'")
        self.mouse("mouseMoved", ".menu .tiles .f")
        self.assertEqual(self.b.js("getComputedStyle($('#mouseback')).display"), "none", "none on the main menu")

    def test_the_wheel_moves_as_far_as_the_fingers(self):
        """A trackpad's small steps move the page and, sideways, the row under the pointer as far as they go (no
        further than their ends); a click held is a click (the right click has the options)."""
        self.go_to_row("latest0")  # the movies added lately: more than fit
        self.b.js("document.activeElement.closest('.strip').id = 'longstrip'")
        time.sleep(0.5)
        self.mouse("mouseMoved", "#longstrip > .f")
        for _ in range(5):
            self.mouse("mouseWheel", "#longstrip > .f", deltaX=20, deltaY=0)
        self.b.wait_js("Math.abs($('#longstrip').slid - Math.min(100, stripEnds($('#longstrip')).max)) <= 1", msg="sideways: 5 × 20 px")
        page = self.b.js("movedY($('.rows-track'))")
        for _ in range(3):
            self.mouse("mouseWheel", "#longstrip > .f", deltaX=0, deltaY=10)
        self.b.wait_js(f"Math.abs(movedY($('.rows-track')) - Math.min({page} + 30, moveEnd($('.rows-track')))) <= 1", msg="down: 3 × 10 px")
        self.mouse("mouseWheel", "#longstrip > .f", deltaX=0, deltaY=100000)
        self.b.wait_js("Math.abs(movedY($('.rows-track')) - moveEnd($('.rows-track'))) <= 1", msg="no further than its end")
        self.b.call("Input.dispatchMouseEvent", type="mouseWheel", x=960, y=700, button="none", deltaX=0, deltaY=-100000)
        self.b.wait_js("movedY($('.rows-track')) === 0", msg="nor above its start")
        self.b.wait_js("!$('.wheeling')", msg="it stands")
        self.b.js("navigate('library', {id: 'lm', name: 'Movies', type: 'movies'})")
        self.b.wait_js("$('.grid-page .f')", msg="the library")
        time.sleep(0.5)
        end, was = self.b.js("[moveEnd($('.grid-page')), movedY($('.grid-page'))]")
        self.assertGreater(end, 0, "a library longer than the screen")
        self.b.call("Input.dispatchMouseEvent", type="mouseWheel", x=960, y=600, button="none", deltaX=0, deltaY=40)
        self.b.wait_js(f"Math.abs(movedY($('.grid-page')) - Math.min({was} + 40, {end})) <= 1", msg="a grid moved with the wheel")
        self.b.js("navigate('item', {id: 'm1'})")  # a film's page scrolls by itself
        self.b.wait_js("current.name === 'item' && $('#view .f')", msg="a film")
        time.sleep(0.5)
        end, was = self.b.js("[view.scrollHeight - view.clientHeight, view.scrollTop]")
        if end > 0:
            self.b.call("Input.dispatchMouseEvent", type="mouseWheel", x=960, y=600, button="none", deltaX=0, deltaY=40)
            self.b.wait_js(f"Math.abs(view.scrollTop - Math.min({was} + 40, {end})) <= 1", msg="a film's page scrolled with the wheel")
        self.b.js("window.notches = []; addEventListener('wheel', (e) => notches.push(e.defaultPrevented))")
        self.b.call("Input.dispatchMouseEvent", type="mouseWheel", x=960, y=600, button="none", deltaX=0, deltaY=-120)
        self.b.wait_js("notches.length === 1", msg="the wheel seen")
        self.assertEqual(self.b.js("notches"), [end <= 0], "a page that scrolls by itself: the browser's own scrolling, a mouse's notch not held back")
        time.sleep(0.6)  # the notch's glide over
        top = self.b.js("view.scrollTop")
        self.b.call("Input.dispatchMouseEvent", type="mouseWheel", x=960, y=600, button="none", deltaX=0, deltaY=40, modifiers=2)
        self.b.wait_js("notches.length === 2", msg="a pinch seen (Ctrl with the wheel)")
        time.sleep(0.3)
        self.assertEqual(self.b.js("[notches[1], view.scrollTop]"), [True, top], "a pinch neither zooms the page nor scrolls it")
        self.b.js("navigate('home')")
        self.b.wait_js("current.name === 'home' && $('[data-k=libs-0]')", msg="home")
        time.sleep(0.5)
        self.click("[data-k=libs-0]", hold=1.0)
        self.b.wait_js("current.name === 'library'", msg="a click held a while is a click all the same")
        self.assertTrue(self.b.js("$('#options').hidden"), "no options from holding")

    def test_a_row_turned_with_the_mouse(self):
        """Over a row that goes on beyond the screen: › at its end turns a page of it, ‹ back; the page stays."""
        self.go_to_row("latest0")  # the movies added lately: more than fit
        self.b.js("document.activeElement.closest('.strip').id = 'longstrip'")
        self.assertGreater(self.b.js("stripEnds($('#longstrip')).max"), 0, "longer than the screen")
        time.sleep(0.5)  # the page moved to it
        self.b.call("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5, button="none")
        self.mouse("mouseMoved", "#longstrip > .f")
        on = "(b) => getComputedStyle($(b)).display !== 'none'"
        self.assertTrue(self.b.js(f"({on})('#stripnext')"), "› at its end")
        self.assertFalse(self.b.js(f"({on})('#stripprev')"), "no ‹ at its start")
        page = self.b.js("getComputedStyle($('.rows-track')).transform")
        self.click("#stripnext")
        self.assertGreater(self.b.js("$('#longstrip').slid"), 0, "turned")
        self.assertTrue(self.b.js("$('#longstrip').contains(document.activeElement)"), "the focus on it")
        self.assertEqual(self.b.js("getComputedStyle($('.rows-track')).transform"), page, "the page did not move")
        self.assertTrue(self.b.js(f"({on})('#stripprev')"), "now ‹ too")
        self.click("#stripprev")
        self.assertEqual(self.b.js("$('#longstrip').slid"), 0, "back at its start")
        self.b.key("ArrowDown")
        self.assertFalse(self.b.js(f"({on})('#stripnext')"), "gone with the pointer")

    def test_a_trackpad_s_coasting_is_no_swipe(self):
        """A swipe let go goes on by itself, ever smaller: one step for the swipe, not one for every bit of the coasting."""
        steps = """((deltas) => {
          const real = Date.now; let now = window.fakeNow = (window.fakeNow ?? real()) + 10000, n = 0; // each a while after the last
          const count = (e) => { if (!e.isTrusted && e.key === 'ArrowDown') { n++; e.stopImmediatePropagation(); } };
          document.addEventListener('keydown', count, true);
          Date.now = () => now;
          try { for (const d of deltas) { now += 16; document.dispatchEvent(new WheelEvent('wheel', { deltaY: d, cancelable: true })); } }
          finally { Date.now = real; window.fakeNow = now; document.removeEventListener('keydown', count, true); }
          return n;
        })"""
        swipe = [10, 20, 30, 40, 40, 40, 40]
        coasting = [round(40 * 0.93 ** i, 2) for i in range(1, 80)]  # some 1.3 s of it, as a Mac sends
        self.assertLessEqual(self.b.js(f"{steps}({swipe + coasting})"), 2, "the coasting steps no further")
        self.assertGreaterEqual(self.b.js(f"{steps}({swipe + coasting[:20] + swipe})"), 2, "a new swipe in it does")
        self.assertEqual(self.b.js(f"{steps}([100, 0, 0, 0, 0, 0, 0, 0, 100])"), 2, "a mouse's notches: one step each")

    def test_a_mac_s_shortcuts(self):
        """⌘F the search of the part one is in, ⌘, the settings, ⌘[ and ⌘← back, ⌘1 … ⌘9 the apps, ⌘+ ⌘− ⌘0 the size
        (Ctrl on a PC's keyboard)."""
        def shortcut(key, code, vk):
            for kind in ("rawKeyDown", "keyUp"):
                self.b.call("Input.dispatchKeyEvent", type=kind, key=key, code=code, windowsVirtualKeyCode=vk, modifiers=2)
            time.sleep(0.4)
        self.b.js("navigate('home', {})")
        self.b.wait_js("current.name === 'home'")
        shortcut("f", "KeyF", 70)
        self.b.wait_js("current.name === 'search'", msg="Jellyfin's search")
        shortcut(",", "Comma", 188)
        self.b.wait_js("current.name === 'settings'", msg="the settings")
        shortcut("[", "BracketLeft", 219)
        self.b.wait_js("current.name === 'search'", msg="back")
        shortcut(",", "Comma", 188)
        self.b.wait_js("current.name === 'settings'")
        shortcut("ArrowLeft", "ArrowLeft", 37)
        self.b.wait_js("current.name === 'search'", msg="⌘← back too")
        # the apps in the main menu's order, from any page; Back goes to the menu, on that app's tile
        self.b.js("navigate('item', {id: 'm1'})")
        self.b.wait_js("current.name === 'item'")
        shortcut("1", "Digit1", 49)
        self.b.wait_js("current.name === 'home' && stack.length === 1", msg="the first app, Jellyfin")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'menu' && document.activeElement?.dataset.k === 'menu-jellyfin'", msg="Back: the menu, on its tile")
        # the size: smaller as at a desk, larger as for a television, 0 as by itself; kept as the setting
        size = lambda: (self.b.js("document.documentElement.classList.contains('desk')"), self.backend.request("/settings")[1]["size"])
        shortcut("-", "Minus", 189)
        self.b.wait_js("document.documentElement.classList.contains('desk')", msg="smaller")
        self.assertEqual(size(), (True, "desk"))
        shortcut("+", "Equal", 187)
        self.b.wait_js("!document.documentElement.classList.contains('desk')", msg="larger")
        self.assertEqual(size(), (False, "tv"))
        shortcut("0", "Digit0", 48)
        wait_for(lambda: size() == (False, "auto"), msg="as by itself (the tests: a television)")

    def test_a_message_above_the_music_s_bar(self):
        self.b.js("nowbar.hidden = false; nowbar.innerHTML = '<div><b>Song</b><span>Artist</span></div>'; toast('Message', 20000)")
        toast, bar = self.b.js("[$('#toast'), $('#nowbar')].map((e) => e.getBoundingClientRect()).map((r) => [r.top, r.bottom])")
        self.assertLess(toast[1], bar[0], "not hidden behind it")
        self.b.js("nowbar.hidden = true")

    def test_a_logo_dark_or_with_its_own_background(self):
        from mockjf import png
        self.b.js("navigate('item', { id: 'm1' })")
        self.b.wait_js("$('.detail img.logo, .detail .title') && $('.detail img.logo')?.complete !== false", msg="the film's page")
        self.b.wait_js("$('.detail img.logo')?.classList.contains('dark')", msg="dark letters on nothing: shown white")
        self.mock.image = lambda iid, kind: png(64, 24, (30, 30, 30)) if kind == "Logo" and iid == "m2" else None
        try:
            self.b.js("navigate('item', { id: 'm2' })")
            self.b.wait_js("current.args.id === 'm2' && $('.detail h1.title') && !$('.detail img.logo')",
                           timeout=20, msg="a picture with its own background: the title written out, not a white box")
            self.assertEqual(self.b.js("$('.detail h1.title').textContent"), "Movie 2")
            self.mock.image = lambda iid, kind: b"not a picture" if kind == "Logo" and iid == "m3" else None
            self.b.js("navigate('item', { id: 'm3' })")
            self.b.wait_js("$('.detail h1.title')?.textContent === 'Movie 3' && !$('.detail img.logo')",
                           timeout=20, msg="a logo that does not load: the title written out, not the broken-picture sign")
        finally:
            self.mock.image = None

    def test_an_episode_s_options_lead_to_its_show(self):
        self.go_to_row("resume")
        self.b.js("focusEl([...document.querySelectorAll('[data-k^=resume-]')].find((e) => reg[e.dataset.r]?.Type === 'Episode'))")
        self.b.key("Enter", hold_ms=900)
        labels = self.texts("#options .opt")
        self.assertEqual(labels[0], "Go to Show", "as a podcast's episode: to its show, first")
        self.assertEqual(labels[labels.index("Settings") - 1], "Remove from This Row", "taking it off the row last, not chosen first")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.args.id === 's1'", msg="the show's page")

    def test_a_show_only_looked_into_is_not_continued(self):
        """Jellyfin's Next Up offers episode 1 of a show whose start was only looked into: not on Continue Watching."""
        self.mock.lib.set_played("e11", False)
        self.mock.lib.userdata["e11"].update(LastPlayedDate="2026-01-02T00:00:00.0000000Z", PlayCount=1)
        self.b.js("forgetHome(); render({ name: 'home', args: {} })")
        self.b.wait_js("current.name === 'home' && $('[data-k=resume-0]')", msg="Jellyfin's start")
        self.assertEqual(self.b.js("[...document.querySelectorAll('[data-k^=\"resume-\"]')].map((e) => reg[e.dataset.r]?.Id)"), ["m1"],
                         "only the movie begun")
        self.mock.lib.set_played("e11", True)  # watched now: its show goes on
        self.b.js("forgetHome(); render({ name: 'home', args: {} })")
        self.b.wait_js("current.name === 'home' && $('[data-k=resume-1]')", msg="its next episode")
        self.assertEqual(self.b.js("[...document.querySelectorAll('[data-k^=\"resume-\"]')].map((e) => reg[e.dataset.r]?.Id)"), ["m1", "e12"])

    def test_remove_from_continue_watching_and_up_next(self):
        self.go_to_row("resume")
        self.assertEqual(self.focus(), "resume-0")
        self.assertEqual(self.b.js("document.querySelectorAll('[data-k^=\"resume-\"]').length"), 2,
                         "movie 1 begun, then the show's next episode: one row")
        self.pick_option("Remove from This Row")
        wait_for(lambda: self.mock.lib.userdata["m1"]["PlaybackPositionTicks"] == 0, msg="position forgotten on the server")
        self.b.wait_js("document.querySelectorAll('[data-k^=\"resume-\"]').length === 1", msg="movie 1 off the row")
        self.assertFalse(self.mock.lib.userdata["m1"]["Played"], "not marked watched")
        self.assertIn("S1 E2", self.focus_text(), "on what took its place: the next episode")
        self.pick_option("Remove from This Row")
        self.b.wait_js("!document.querySelector('[data-k^=\"resume-\"]')", msg="the show is off the row too")
        self.assertEqual(self.backend.request("/hidden")[1]["nextup"], ["s1"])
        self.b.js("location.reload()")
        time.sleep(0.5)
        self.b.wait_js("current?.name === 'menu'")
        self.b.js("focusEl($('[data-k=menu-jellyfin]'))")  # the dock starts on Up Next
        self.b.key("Enter")
        self.b.wait_js("current.name === 'home' && document.querySelector('[data-k^=\"latest0-\"]')")
        self.assertFalse(self.b.js("!!document.querySelector('[data-k^=\"resume-\"]')"), "still off after a reload")
        self.go_to_row("latest0")
        self.b.key("Enter", hold_ms=900)
        self.assertNotIn("Remove from This Row", self.texts("#options .opt"), "nothing to remove from other rows")

    def test_a_title_taken_off_its_row_can_be_put_back(self):
        self.go_to_row("resume")
        was = self.mock.lib.userdata["m1"]["PlaybackPositionTicks"]
        self.assertGreater(was, 0)
        self.pick_option("Remove from This Row")
        self.b.wait_js("document.querySelectorAll('[data-k^=\"resume-\"]').length === 1", msg="movie 1 off the row")
        self.assertIn("Undo: hold OK", self.b.js("$('#toast').textContent"), "the message says how")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="the options")
        self.assertEqual(self.texts("#options .opt")[0], "Undo Remove", "first, on whatever tile the focus is now")
        self.b.key("Enter")
        wait_for(lambda: self.mock.lib.userdata["m1"]["PlaybackPositionTicks"] == was, msg="where it was stopped, back on the server")
        self.b.wait_js("document.querySelectorAll('[data-k^=\"resume-\"]').length === 2", msg="movie 1 back on the row")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="the options")
        self.assertNotIn("Undo Remove", self.texts("#options .opt"), "once only")

    def test_pictures_that_did_not_come_are_asked_again(self):
        """Switched on before the network: the tiles' pictures fail at first, and come once the server answers."""
        self.mock.images_down = True
        self.addCleanup(setattr, self.mock, "images_down", False)
        self.b.call("Network.enable")
        self.b.call("Network.setCacheDisabled", cacheDisabled=True)  # pictures had before would come from the cache
        self.addCleanup(self.b.call, "Network.setCacheDisabled", cacheDisabled=False)
        self.b.js("shelfCache.clear(); render({ name: 'menu' }, 'menu-next')")
        self.b.wait_js("document.querySelector('#shelf .card .art .ph')", msg="the names in the pictures' place")
        self.mock.images_down = False
        self.b.wait_js("document.querySelector('#shelf .card .art img.in') && !document.querySelector('#shelf .card .art .ph')",
                       timeout=15, msg="the pictures once the server answers")

    def test_menu_key_puts_it_on_my_list(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        self.b.key("Enter", hold_ms=900)  # the options: My List first
        self.assertEqual(self.texts("#options .opt"), ["Add to My List", "Mark as Watched", "Find Subtitles", "Settings", "Using Matinee",
                                                       "Main Menu"])
        self.assertEqual(self.b.js("$('#options h2').textContent"), "Movie 2")
        self.b.key("Enter")
        wait_for(lambda: "m2" in self.mock.lib.favorites, msg="on My List")
        self.b.wait_js("document.querySelector('[data-k=\"latest0-1\"] .fav')", msg="a heart on the tile")
        self.b.wait_js("[...document.querySelectorAll('.row h2')].some((h) => h.textContent === 'My List')", msg="a row for it")
        self.assertFalse(self.mock.lib.userdata["m2"]["Played"], "not marked watched")
        self.b.js("focusEl(document.querySelector('[data-k=\"latest0-1\"]'))")
        self.b.key("Enter", hold_ms=900)
        self.assertEqual(self.texts("#options .opt")[0], "Remove from My List")
        self.b.key("Enter")
        wait_for(lambda: "m2" not in self.mock.lib.favorites, msg="off it again")

    def test_options_lead_to_the_settings_and_close_with_back(self):
        self.go_to_row("latest0")
        start = self.focus()
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="options open")
        for _ in range(4):  # My List, watched, unwatched (movie 1 is begun), Find Subtitles
            self.b.key("ArrowDown")
        self.assertEqual(self.b.js("$('#options .opt.sel').textContent"), "Settings")
        self.b.key("Escape")
        self.assertTrue(self.b.js("$('#options').hidden"), "Back closes them")
        self.assertEqual((self.view(), self.focus()), ("home", start), "and nothing else happened")
        self.b.key("Enter", hold_ms=900)
        for _ in range(4):
            self.b.key("ArrowDown")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings'", msg="settings")

    def test_my_list_button_on_detail_page(self):
        self.b.js("navigate('item', {id: 'm3'})")
        self.b.wait_js("document.querySelector('[data-k=favorite]')", msg="detail page")
        self.assertEqual(self.b.js("$('[data-k=favorite]').textContent.trim()"), "My List")
        self.b.js("activate($('[data-k=favorite]'))")
        wait_for(lambda: "m3" in self.mock.lib.favorites, msg="on My List")
        self.b.wait_js("$('[data-k=favorite]')?.textContent.trim() === 'In My List'", msg="the button says so")

    def test_watched_button_on_detail_page(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus_text(), "Mark as Watched")
        self.b.key("Enter")
        wait_for(lambda: self.mock.lib.userdata["m2"]["Played"])
        self.b.wait_js("document.activeElement.textContent.trim() === 'Mark as Unwatched'", msg="button toggled, focus kept")

    def test_mark_whole_show_watched(self):
        self.mock.lib.set_played("e11", False)  # nothing seen yet: a plain toggle
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4")
        self.b.js("focusEl($('[data-k=watched]'))")
        self.assertEqual(self.focus_text(), "Mark All as Watched")
        self.b.key("Enter")
        wait_for(lambda: all(self.mock.lib.userdata[e]["Played"] for e in self.mock.lib.episodes))

    def test_mark_begun_movie_unwatched(self):
        self.b.js("navigate('item', {id: 'm1'})")  # begun: 0:05 in
        self.b.wait_js("document.querySelector('[data-k=watched]')", msg="detail page")
        self.b.js("focusEl($('[data-k=watched]'))")
        self.assertEqual(self.focus_text(), "Watched Status")
        self.b.key("Enter")  # a choice of both
        self.assertEqual(self.texts("#options .opt"), ["Mark as Watched", "Mark as Unwatched"])
        self.b.key("ArrowDown")
        self.b.key("Enter")
        wait_for(lambda: self.mock.lib.userdata["m1"]["PlaybackPositionTicks"] == 0, msg="progress taken back")
        self.assertFalse(self.mock.lib.userdata["m1"]["Played"])
        self.b.wait_js("!document.querySelector('[data-k=resume]')", msg="no resume any more")
        self.b.wait_js("document.activeElement.textContent.trim() === 'Mark as Watched'", msg="a plain toggle again, focus kept")

    def test_menu_key_marks_begun_show_unwatched(self):
        self.go_to_row("latest1")  # the show, with S1E1 watched
        self.b.key("Enter", hold_ms=900)
        self.assertEqual(self.texts("#options .opt")[1:3], ["Mark as Watched", "Mark as Unwatched"])
        self.b.key("ArrowDown")
        self.b.key("ArrowDown")
        self.b.key("Enter")
        wait_for(lambda: not self.mock.lib.userdata["e11"]["Played"], msg="S1E1 unwatched again")
        self.b.key("Enter", hold_ms=900)
        self.assertNotIn("Mark as Unwatched", self.texts("#options .opt"), "nothing seen any more")

    def test_mark_begun_show_all_unwatched(self):
        self.go_to_row("latest1")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('#episodes .card').length === 4")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus_text(), "Watched Status")
        self.b.key("Enter")
        self.assertEqual(self.texts("#options .opt"), ["Mark All as Watched", "Mark All as Unwatched"])
        self.b.key("ArrowDown")
        self.b.key("Enter")
        wait_for(lambda: not any(self.mock.lib.userdata[e]["Played"] for e in self.mock.lib.episodes), msg="all unwatched")

    # ---------- Time, errors, screensaver ----------

    def test_detail_page_says_when_it_ends(self):
        self.b.js("navigate('item', { id: 'm1' })")
        self.b.wait_js("current.name === 'item' && document.querySelector('.hero .meta')")
        meta = self.b.js("document.querySelector('.hero .meta').textContent")
        self.assertRegex(meta, r"\d+ min left · Ends at \d{1,2}:\d\d", "started movie: time left and end")
        self.b.js("document.querySelector('.hero').dataset.marker = 'old'; navigate('item', { id: 'm2' })")
        self.b.wait_js("document.querySelector('.hero .meta') && !document.querySelector('.hero').dataset.marker", msg="movie 2 shown")
        meta = self.b.js("document.querySelector('.hero .meta').textContent")
        self.assertIn("Ends at", meta)
        self.assertNotIn("left", meta, "nothing left to say for a movie not started yet")

    def test_play_without_backend_says_so(self):
        self.b.js("const f = fetch; fetch = (u, o) => u === '/play' ? Promise.reject(new TypeError('Failed to fetch')) : f(u, o)")
        self.b.js("play('m1', true)")
        self.b.wait_js("!$('#toast').hidden && $('#toast').textContent.startsWith(\"This can't be played right now\")", msg="error shown")
        self.assertNotIn("fetch", self.b.js("$('#toast').textContent"), "in plain words, not the browser's")

    def test_search_error_replaces_old_results(self):
        self.b.type("movie")
        self.b.wait_js("document.querySelectorAll('#results .card').length > 0", msg="results")
        self.b.js("const f = fetch; fetch = (u, o) => u.includes('searchTerm') ? Promise.reject(new TypeError('Failed to fetch')) : f(u, o)")
        self.b.type("s")
        self.b.wait_js("$('#results').textContent.includes('Jellyfin did not answer')",
                       msg="said in plain words instead of the old results")
        self.assertEqual(self.b.js("document.querySelectorAll('#results .card').length"), 0)
        self.assertNotIn("searchTerm", self.b.js("$('#results').textContent"), "not the request's address")

    def test_kept_libraries_are_limited(self):
        size = self.b.js("for (let i = 0; i < 40; i++) keepLibrary('q' + i, { items: [] }); libCache.size")
        self.assertEqual(size, self.b.js("LIB_KEEP"))
        self.assertTrue(self.b.js("libCache.has('q39') && !libCache.has('q0')"), "the oldest ones go")

    def test_failed_page_tries_again(self):
        self.b.js("RETRY_AFTER = 600; const f = fetch; fetch = (u, o) => (u.includes('/UserViews') && !window.healed) ? "
                  "Promise.resolve(new Response('busy', { status: 500 })) : f(u, o)")
        self.b.js("forgetHome(); render({ name: 'home' })")  # loaded afresh, not the home kept from before
        self.b.wait_js("document.querySelector('[data-k=\"retry\"]') === document.activeElement", msg="error with a focused retry button")
        self.assertNotIn("UserViews", self.b.js("view.textContent"), "no request's address on the TV")
        self.b.js("window.healed = true")
        self.b.wait_js("document.querySelectorAll('.home .card').length > 0", timeout=3, msg="home again by itself")

    def test_retry_with_ok(self):
        self.b.js("RETRY_AFTER = 600000; const f = fetch; fetch = (u, o) => (u.includes('/UserViews') && !window.healed) ? "
                  "Promise.resolve(new Response('busy', { status: 500 })) : f(u, o)")
        self.b.js("forgetHome(); render({ name: 'home' })")  # loaded afresh, not the home kept from before
        self.b.wait_js("document.querySelector('[data-k=\"retry\"]')", msg="error page")
        self.b.js("window.healed = true")
        self.b.key("Enter")
        self.b.wait_js("document.querySelectorAll('.home .card').length > 0", timeout=3, msg="home after OK")

    def test_home_held_goes_to_the_main_menu(self):
        self.open_library("Movies")
        self.b.key("Home", hold_ms=1000)
        self.b.wait_js("current.name === 'menu' && document.activeElement?.dataset.k === 'menu-jellyfin'", msg="main menu")
        self.assertEqual(self.b.js("stack.length"), 0)

    def test_play_held_switches_the_sleep_timer(self):
        self.b.key("MediaPlayPause", hold_ms=1000)
        self.b.wait_js("$('#toast').textContent === 'Sleep timer: 30 min'", msg="sleep timer")
        self.assertEqual(self.view(), "home", "nothing else happened")
        self.backend.request("/sleep", {"mode": None})

    def test_screensaver(self):
        start = self.focus()
        self.b.js("SAVER_AFTER = 400; wake()")
        self.b.wait_js("!$('#saver').hidden", timeout=3, msg="screensaver after a while without a key")
        self.assertRegex(self.b.js("$('#saver .clock').textContent"), r"\d{1,2}:\d\d")
        self.assertFalse(self.b.js("!!$('#saver .weather')"), "no weather without a place")
        self.b.js("saverWeather = {place: 'X', temp: 12, code: 61, day: true, hi: 16, lo: 7}; placeClock()")
        self.assertEqual(self.b.js("$('#saver .weather').textContent"), "12°Rain · 16° / 7°")
        self.assertRegex(self.b.js("$('#saver .date').textContent"), r"^[A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}$", "the date under the time")
        self.b.key("ArrowRight", pause=0.1)
        self.assertTrue(self.b.js("$('#saver').hidden"), "a key wakes it up")
        self.assertEqual(self.focus(), start, "and does nothing else")
        self.b.js("SAVER_AFTER = 600000; wake()")

    def test_no_screensaver_during_playback(self):
        self.b.js("polling = true; SAVER_AFTER = 300; wake()")
        time.sleep(1)
        self.assertTrue(self.b.js("$('#saver').hidden"), "the player is in front")
        self.b.js("polling = false; SAVER_AFTER = 600000; wake()")

    # ---------- Live updates ----------

    def test_change_in_another_app_shows_up_by_itself(self):
        time.sleep(3.2)  # nobody is navigating right now
        ud = self.mock.lib.userdata["m7"]
        ud.update(PlaybackPositionTicks=10 * TICKS, LastPlayedDate="2099-01-01T00:00:00.0000000Z")
        self.mock.push()  # what Jellyfin does when someone keeps watching in another app
        self.b.wait_js("[...document.querySelectorAll('[data-k^=\"resume-\"]')].some(c => c.textContent.includes('Movie 7'))",
                       timeout=8, msg="movie 7 under Continue Watching")

    def test_back_home_at_once_then_what_changed(self):
        """Back to home shows the page kept from before at once, without waiting for the server; what changed on the
        server meanwhile comes in afterwards where the page stands, and nothing moves when nothing changed."""
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        before = self.focus()
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.mock.views_delay = 2
        try:
            self.b.key("Escape", pause=0)
            self.b.wait_js(f"current.name === 'home' && document.activeElement.dataset.k === '{before}'", timeout=1,
                           msg="home at once, on the tile, while the server is slow")
            self.b.js("document.querySelector('.home').dataset.marker = 'kept'")
            time.sleep(5)  # the server's answer and the rest after the last key
            self.assertEqual(self.b.js("document.querySelector('.home').dataset.marker"), "kept", "nothing changed: not built again")
            begun = "[...view.querySelectorAll('[data-k^=\"resume-\"]')].some(e => reg[e.dataset.r]?.Id === 'm1')"
            self.assertTrue(self.b.js(begun), "movie 1 begun")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'item'")
            self.mock.lib.set_played("m1", True)  # watched elsewhere, nobody told
            self.b.key("Escape", pause=0)
            self.b.wait_js(f"current.name === 'home' && document.activeElement.dataset.k === '{before}'", timeout=1, msg="home at once")
            self.b.js("document.querySelector('.home').dataset.marker = 'kept'")
            self.b.wait_js("!document.querySelector('.home').dataset.marker", timeout=10, msg="built again with the change")
            self.assertEqual(self.focus(), before, "focus kept")
            self.assertFalse(self.b.js(begun), "movie 1 is no longer begun")
        finally:
            self.mock.views_delay = 0

    def test_no_refresh_while_navigating(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        self.b.js("document.querySelector('.home').dataset.marker = 'old'")
        self.mock.change_played("m9", True)
        time.sleep(1.5)
        self.assertEqual(self.b.js("document.querySelector('.home').dataset.marker"), "old", "no reload mid-navigation")
        self.b.wait_js("!document.querySelector('.home').dataset.marker", timeout=8, msg="reloaded afterwards")
        self.assertEqual(self.focus(), "latest0-1", "focus kept after the reload")


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class UiPlaybackTest(UiBase):
    with_player = True

    def test_play_key_starts_and_ui_reloads_afterwards(self):
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")  # movie 3
        self.b.js("document.querySelector('.home').dataset.marker = 'old'")
        self.b.key("MediaPlayPause")
        self.assertTrue(self.b.js("$('#curtain').classList.contains('on')"), "the page goes into the black")
        wait_for(lambda: [b for b in self.mock.reports_for("/Playing") if b["ItemId"] == "m3"], msg="movie 3 started")
        time.sleep(1.2)  # past the moment the curtain would say what is starting
        self.assertFalse(self.b.js("$('#curtain').classList.contains('slow')"),
                         "behind the player only black: its end does not say \"Starting…\"")
        wait_for(lambda: self.mock.lib.userdata["m3"]["Played"], timeout=15, msg="watched to the end")
        self.b.wait_js("!document.querySelector('.home').dataset.marker", timeout=8, msg="reloaded after playback")
        self.assertFalse(self.b.js("$('#curtain').classList.contains('on')"), "and comes out of it again")
        self.b.wait_js("document.querySelector('[data-k=\"latest0-2\"] .watched')", msg="check mark on movie 3")


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiMenuTest(UiBase):
    section = None

    def test_logo_of_the_start_gives_way_to_the_menu(self):
        self.b.js("sessionStorage.clear(); location.reload()")  # switched on anew
        time.sleep(0.3)
        # its styles there (the page parsed): asked before, under load, the logo had none yet, which no screen shows
        self.b.wait_js("document.getElementById('intro') && document.readyState !== 'loading'", msg="the boot splash's logo first")
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('#intro svg')).width"), "360px", "as the splash draws it")
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('#intro .fan')).animationName"), "intro-breathe",
                         "its cards breathe should the main menu take long")
        self.b.wait_js("!document.getElementById('intro') && current?.name === 'menu'", timeout=6, msg="then the main menu")
        self.b.js("location.reload()")  # only reloaded (a new backend): not again
        time.sleep(0.5)
        self.b.wait_js("current?.name === 'menu'")
        self.assertFalse(self.b.js("!!document.getElementById('intro')"), "not when the page only reloads")

    def test_opens_what_was_chosen_when_switched_on(self):
        try:
            self.backend.request("/settings", {"startwith": "last"})
            self.backend.request("/section", {"id": "jellyfin"})
            self.b.js("sessionStorage.clear(); location.reload()")  # switched on anew
            time.sleep(0.5)
            self.b.wait_js("current?.name === 'home'", msg="the part used last at once")
            self.b.js("location.reload()")  # only reloaded (a new backend): not again
            time.sleep(0.5)
            self.b.wait_js("current?.name === 'menu'", msg="the main menu")
            time.sleep(1)
            self.assertEqual(self.view(), "menu")
        finally:
            self.backend.request("/settings", {"startwith": "menu"})

    def test_overview_above_the_settings(self):
        state = json.loads(self.bt_state.read_text())
        state["devices"]["50:F5:DA:00:00:02"]["battery"] = 15
        self.bt_state.write_text(json.dumps(state))
        try:
            self.b.js("focusEl($('[data-k=menu-settings]'))")
            self.b.wait_js("$('#shelf .status .panel')", msg="a look at the device")
            self.assertEqual(self.texts("#shelf .status h3"), ["Servers", "Bluetooth", "Matinee"], "the weather by the time, not in a box")
            self.assertTrue(self.b.js("!!$('#stage-title .device-time')"), "the time large at the top")
            self.b.wait_js("getComputedStyle($('#backdrop'), '::before').opacity === '1'", msg="a calm graphite, not the library's picture")
            self.assertIn("Version", self.texts("#shelf .status .panel:nth-child(3) .line")[0], "Matinee's version, no update off the thin client")
            self.assertEqual(self.texts("#shelf .status .panel:first-child .line"), ["JellyfinReachable"])
            self.assertEqual(self.texts("#shelf .status .panel:nth-child(2) .line"), ["RemoteBattery 15 %"])
            self.assertTrue(self.b.js("!!$('#shelf .status .panel:nth-child(2) .dot.down')"), "a battery nearly empty in red")
        finally:
            del state["devices"]["50:F5:DA:00:00:02"]["battery"]
            self.bt_state.write_text(json.dumps(state))

    def test_overview_tailscale_and_no_bluetooth(self):
        o = "{ servers: [], bluetooth: null, weather: null, update: null, version: 'v1', tailscale: %s }"
        html = lambda ts: self.b.js(f"statusHtml({{ title: 'x', o: {o % ts} }})")
        on = html("{ state: 'Running', online: 3, peers: 4, exit: 'nas' }")
        self.assertNotIn("Bluetooth", on, "no Bluetooth box where Matinee cannot pair anything (a Mac)")
        self.assertIn("<h3>Tailscale</h3>", on)
        self.assertIn("Connected<span>via nas</span>", on)
        self.assertIn("3 of 4 online", on)
        off = html("{ state: 'Stopped' }")
        self.assertIn("dot down", off)
        self.assertNotIn("online", off)
        self.assertNotIn("Tailscale", html("null"), "nothing without Tailscale")

    def test_main_menu(self):
        self.assertEqual(self.texts(".menu .tiles .label"), ["Up Next", "Movies & Shows", "Music", "Audiobooks", "Search", "Settings"],
                         "music and audiobooks before they are set up, too")
        self.assertEqual(self.focus(), "menu-next", "switched on: on Up Next")
        for _ in range(5):
            self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings'")
        self.b.key("Escape")
        # the focus comes once the main menu is drawn, a moment after its name (the page is set at its start)
        self.b.wait_js("current.name === 'menu' && document.activeElement?.dataset.k === 'menu-settings'", msg="back on Settings")

    def test_search_everything_ring_stops_with_the_words_deleted(self):
        self.b.js("navigate('allsearch', {})")
        self.b.wait_js("current.name === 'allsearch' && $('[data-k=key-a]')", msg="the letters")
        # Jellyfin takes its time: the words are deleted while it is still asked
        self.b.js("const f = fetch; fetch = (u, o) => String(u).includes('searchTerm=') "
                  "? new Promise((r) => setTimeout(r, 2500)).then(() => f(u, o)) : f(u, o)")
        self.b.js("typeKey('m'); typeKey('o')")
        self.b.wait_js("document.body.classList.contains('busy')", timeout=5, msg="searching")
        self.b.js("typeKey('del')")
        self.b.wait_js("$('#results .hint') && !document.body.classList.contains('busy')", timeout=3, msg="one letter: no ring")
        time.sleep(3)  # the slow answer has come by now
        self.assertFalse(self.b.js("document.body.classList.contains('busy')"), "and it stays off")

    def test_search_everything_from_the_dock(self):
        self.b.js("focusEl($('[data-k=menu-search]'))")
        self.b.wait_js("$('#shelf h2')?.textContent === 'Search'", msg="its shelf says what it looks through")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'allsearch' && $('[data-k=key-a]')", msg="the letters")
        self.b.js("typeKey('m'); typeKey('o'); typeKey('v')")
        self.b.wait_js("$('#all-jellyfin .card')", timeout=6, msg="Jellyfin's titles")
        self.assertEqual(self.b.js("$('#all-jellyfin .results-title').textContent"), "Movies & Shows")
        self.b.wait_js("!document.body.classList.contains('busy')", timeout=10, msg="every part answered")
        self.assertFalse(self.b.js("!!$('#all-music')"), "music not set up: no row for it")
        self.assertEqual(self.b.js("document.querySelectorAll('#all-jellyfin .card').length"), 8, "two lines at most")
        self.b.js("focusEl($('[data-k=all-more-jellyfin]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'search' && current.args.q === 'mov'", msg="more: its own search, the same words")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'allsearch' && $('#all-jellyfin .card')", msg="back")
        self.b.js("focusEl($('#all-jellyfin .card'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'", msg="a title opens as from its own search")
        self.b.js("render({name: 'menu'})")
        self.b.wait_js("current.name === 'menu' && $('.menu .tiles .card')")
        self.b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'q'}))")
        self.b.wait_js("current.name === 'allsearch' && current.args.q === 'q'", msg="typing on the main menu: the search of everything")

    def test_up_next_first(self):
        self.b.js("focusEl($('[data-k=menu-next]'))")
        self.b.wait_js("$('#shelf h2')?.textContent === 'Up Next'", msg="its shelf")
        self.assertEqual(self.texts("#shelf .label")[0], "Movie 1", "what was begun")
        self.b.key("Enter")
        self.assertEqual(self.focus(), "shelf-next-0", "OK goes to its first title")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'", msg="that title opens")

    def test_up_next_without_what_was_marked_unwatched(self):
        """A title on Up Next marked as unwatched there: off the shelf at once, the person still on it."""
        self.b.js("shelfCache.clear(); focusEl($('[data-k=menu-next]'))")
        self.b.wait_js("$('#shelf h2')?.textContent === 'Up Next' && $('#shelf .card')", msg="its shelf")
        self.assertEqual(self.texts("#shelf .label")[0], "Movie 1", "what was begun")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "shelf-next-0")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="its options")
        self.b.js("[...document.querySelectorAll('#options .opt')].find((o) => o.textContent.trim() === 'Mark as Unwatched').click()")
        wait_for(lambda: self.mock.lib.userdata["m1"]["PlaybackPositionTicks"] == 0, msg="progress taken back")
        self.b.wait_js("![...document.querySelectorAll('#shelf .label')].some((l) => l.textContent === 'Movie 1')",
                       msg="off Up Next at once")
        self.assertEqual(self.b.js("current.name"), "menu")

    def test_up_next_with_nothing_begun(self):
        """A fresh start: Up Next says what comes there, rather than leaving black above the dock."""
        for ud in self.mock.lib.userdata.values():
            ud.update(PlaybackPositionTicks=0, Played=False, LastPlayedDate=None)
        self.b.js("shelfCache.clear(); delete $('#shelf').dataset.for; focusEl($('[data-k=menu-settings]'))")
        self.b.js("focusEl($('[data-k=menu-next]'))")
        self.b.wait_js("$('#shelf .hint')", msg="the line")
        self.assertEqual(self.texts("#shelf h2"), ["Up Next"])
        self.assertEqual(self.texts("#shelf .hint"), ["Movies, shows, videos and audiobooks you start show up here."])

    def test_shelf_shown_at_once_from_last_time(self):
        """Started anew: the shelf it showed last is there before the servers answer, then swapped for theirs."""
        self.b.js("focusEl($('[data-k=menu-next]'))")
        self.b.wait_js("$('#shelf h2')?.textContent === 'Up Next' && $('#shelf .card')", msg="its shelf, kept")
        kept = self.b.js("JSON.parse(localStorage.getItem('kino-shelves')).shelves.next.items.length")
        self.assertGreater(kept, 0, "kept in the browser")
        # the servers slow to answer: what it showed last is there at once
        self.b.js("""window.realShelfData = shelfData; shelfData = () => new Promise(() => {});
            shelfCache.clear(); shelfAsked.clear(); render({ name: 'menu' }, 'menu-next')""")
        self.b.wait_js("$('#shelf .card')", timeout=2, msg="shown without the servers")
        self.assertEqual(self.texts("#shelf .label")[0], "Movie 1")
        # what the servers say, swapped in where it is something else
        self.b.js("""const s = JSON.parse(localStorage.getItem('kino-shelves')); s.shelves.next.items[0].it.Name = 'Old Name';
            localStorage.setItem('kino-shelves', JSON.stringify(s)); shelfData = window.realShelfData;
            shelfCache.clear(); shelfAsked.clear(); render({ name: 'menu' }, 'menu-next')""")
        self.b.wait_js("$('#shelf .card')", msg="the kept one")
        self.b.wait_js("document.querySelectorAll('#shelf .label')[0]?.textContent === 'Movie 1'", msg="then the servers' one")
        self.assertEqual(self.b.js("JSON.parse(localStorage.getItem('kino-shelves')).shelves.next.items[0].it.Name"), "Movie 1",
                         "and kept for next time")

    def test_shelves_forgotten_for_another_user(self):
        self.b.js("""localStorage.setItem('kino-shelves', JSON.stringify({ user: 'someone-else', shelves: {
            next: { kind: 'mix', title: 'Up Next', items: [] }, music: { kind: 'nd', title: 'x', items: [] } } }))""")
        self.b.js("shelfCache.clear(); render({ name: 'menu' }, 'menu-next')")
        self.b.wait_js("JSON.parse(localStorage.getItem('kino-shelves')).user === USER", msg="kept for this user now")
        self.assertEqual(self.b.js("Object.keys(JSON.parse(localStorage.getItem('kino-shelves')).shelves)"), ["next"],
                         "the other user's shelves gone")

    def test_shelf_asks_again_after_a_failure(self):
        self.b.js("""shelfCache.clear(); window.offline = true; const f = window.fetch;
            window.fetch = (u, o) => window.offline && String(u).includes('/jf/') ? Promise.reject(new TypeError('offline')) : f(u, o)""")
        self.b.js("render({ name: 'menu' }, 'menu-jellyfin')")
        self.b.wait_js("current.name === 'menu' && document.activeElement?.dataset.k === 'menu-jellyfin'")
        time.sleep(1)
        self.assertEqual(self.b.js("document.querySelectorAll('#shelf .card').length"), 0, "Jellyfin not there yet")
        self.b.js("window.offline = false")
        self.b.key("ArrowRight")
        self.b.key("ArrowLeft")
        self.b.wait_js("document.querySelector('#shelf .card')", msg="asked again, not the failure kept for a minute")

    def test_shelf_shows_what_the_tile_holds(self):
        self.b.js("focusEl($('[data-k=menu-jellyfin]'))")
        self.b.wait_js("$('#shelf h2')?.textContent === 'Continue Watching'", msg="shelf filled")
        self.assertEqual(self.texts("#shelf h2"), ["Continue Watching"])
        self.assertEqual(self.texts("#shelf .label")[0], "Movie 1", "stopped at 0:05")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "shelf-jellyfin-0")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "menu-jellyfin", "down goes back to the tiles")
        self.b.key("ArrowUp")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item' && current.args.id === 'm1'", msg="movie 1 opened")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'menu'", msg="back on the menu, not in the library")
        self.assertEqual(self.focus(), "shelf-jellyfin-0", "on the title it came from")
        self.b.key("ArrowDown")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus(), "menu-music")
        self.b.wait_js("$('#shelf .hint')?.textContent.startsWith('Not set up yet')", msg="music not set up: what OK does")
        self.b.js("focusEl($('[data-k=menu-jellyfin]'))")
        self.b.wait_js("document.querySelector('#shelf .card')", msg="Jellyfin's again")

    def test_set_up_music_on_its_own_page(self):
        from mocknd import ND_PASSWORD, ND_USER, MockNavidrome
        nd = MockNavidrome().start()
        try:
            self.b.js("focusEl($('[data-k=menu-music]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'music' && $('[data-k=svc-server]')", msg="the sign-in on the music's page")
            self.b.js(f"Object.assign(svcDraft.navidrome, {{ server: '{nd.url}', username: '{ND_USER}', password: '{ND_PASSWORD}' }})")
            self.b.js("focusEl($('[data-k=svc-connect]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'music' && $('[data-k=msec-0]')", msg="the music's start in its place")
        finally:
            self.backend.request("/service", {"service": "navidrome", "remove": True})
            self.b.js("ndSession = null")
            nd.stop()

    def paste(self, text):
        """⌘V with text on the clipboard, as the browser hands it to the page."""
        self.b.js(f"""(() => {{ const dt = new DataTransfer(); dt.setData('text/plain', {json.dumps(text)});
            document.activeElement.dispatchEvent(new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true, cancelable: true}})); }})()""")

    def test_paste_into_a_sign_in_field(self):
        self.b.js("navigate('settings', {part: 'podcasts'})")
        self.b.wait_js("$('[data-k=svc-server]')", msg="the podcasts' settings")
        self.b.js("focusEl($('[data-k=svc-server]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'svcentry' && current.args.field === 'server'", msg="typing the server")
        self.paste("  https://pods.example.test:8000 \n")
        self.assertEqual(self.b.js("$('#query').textContent"), "https://pods.example.test:8000", "without the spaces around it")
        self.b.key("Backspace")
        self.b.type("1")
        self.assertEqual(self.b.js("current.args.q"), "https://pods.example.test:8001", "typed on after it")
        self.b.js("focusEl($('[data-k=svc-done]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings' && $('[data-k=svc-password]')", msg="back in the settings")
        self.assertEqual(self.b.js("$('[data-k=svc-server] .sval').textContent"), "https://pods.example.test:8001")
        self.b.js("focusEl($('[data-k=svc-password]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'svcentry' && current.args.field === 'password'", msg="typing the password")
        self.paste(" a b\n")
        self.assertEqual(self.b.js("current.args.q"), " a b", "a password as copied, but for the line break")
        self.assertEqual(self.b.js("$('#query').textContent"), "••••", "and not shown")

    def test_set_up_music_in_the_settings(self):
        from mocknd import ND_PASSWORD, ND_USER, MockNavidrome
        nd = MockNavidrome().start()
        try:
            self.b.js("navigate('settings', {part: 'navidrome'})")
            self.b.wait_js("$('[data-k=svc-server]')", msg="the music's settings")
            for field, text in (("server", nd.url), ("username", ND_USER), ("password", ND_PASSWORD)):
                self.b.js(f"focusEl($('[data-k=svc-{field}]'))")
                self.b.key("Enter")
                self.b.wait_js(f"current.name === 'svcentry' && current.args.field === '{field}'", msg=f"typing the {field}")
                self.b.type(text, pause=0.03)
                if field == "password":
                    self.assertEqual(self.b.js("$('#query').textContent"), "•" * len(text), "not shown while typed")
                self.b.js("focusEl($('[data-k=svc-done]'))")
                self.b.key("Enter")
                self.b.wait_js("current.name === 'settings' && $('[data-k=svc-connect]')", msg="back in the settings")
            self.assertEqual(self.b.js("$('[data-k=svc-server] .sval').textContent"), nd.url)
            self.assertEqual(self.b.js("$('[data-k=svc-password] .sval').textContent"), "•" * len(ND_PASSWORD))
            self.b.js("focusEl($('[data-k=svc-connect]'))")
            self.b.key("Enter")
            self.b.wait_js("$('#toast')?.textContent === 'Music is connected.'", msg="signed in")
            self.b.wait_js("$('[data-k=svc-connect] .sval')?.textContent === 'Connected'", msg="says so")
            self.assertEqual(self.b.js("$('[data-k=svc-password] .sval').textContent"), "Signed In", "as Jellyfin's")
            self.assertEqual(self.b.js("$('[data-k=svc-connect] .sname').textContent"), "Sign In", "the same word as Jellyfin's")
            self.assertEqual(self.backend.request("/music/session")[1], {"configured": True, "ready": True})
            self.assertTrue(self.b.js("!!$('[data-k=set-scrobble]')"), "the music's own settings with it")
            self.assertNotIn(ND_PASSWORD, self.b.js("JSON.stringify(svcDraft)"), "the password not kept in the page")
            self.b.js("render({name: 'menu'}, 'menu-music')")
            self.b.wait_js("document.activeElement?.dataset.k === 'menu-music'", msg="the menu")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'music'", msg="music opens now")
        finally:
            self.backend.request("/service", {"service": "navidrome", "remove": True})
            self.b.js("ndSession = null")
            nd.stop()

    def test_a_service_not_set_up_asks_for_the_sign_in(self):
        self.b.js("focusEl($('[data-k=menu-books]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'books' && $('[data-k=svc-server]')", msg="its sign-in, right there")
        self.assertEqual(self.b.js("$('.jf-signin h1').textContent"), "Audiobooks", "the same page as Jellyfin's")
        self.assertTrue(self.b.js("$('#jf-msg').textContent.startsWith('No Audiobookshelf set up yet.')"))
        self.assertEqual(self.texts(".srow .sname")[:3], ["Server", "API Key", "Sign In"])
        self.assertIn("Not set up", self.b.js("$('[data-k=svc-connect] .sval').textContent"))
        self.assertFalse(self.b.js("!!$('[data-k=svc-remove]')"), "nothing to remove yet")

    def test_tiles_go_round(self):
        self.b.js("focusEl($('[data-k=menu-next]'))")  # the first
        self.b.key("ArrowLeft")
        self.assertEqual(self.focus(), "menu-settings", "from the first to the last")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus(), "menu-next", "and from the last to the first")
        self.b.key("ArrowRight")
        self.assertEqual(self.focus(), "menu-jellyfin")

    def test_letters_on_the_menu_search_everything(self):
        self.b.type("ab")
        self.b.wait_js("current.name === 'allsearch' && current.args.q === 'ab'", msg="no part of its own: the search of all of them")

    def test_up_next_has_the_focus_after_a_restart(self):
        self.b.js("focusEl($('[data-k=menu-jellyfin]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'home'")
        self.b.js("location.reload()")
        time.sleep(0.5)
        self.b.wait_js("current?.name === 'menu' && document.activeElement?.dataset.k === 'menu-next'", msg="Up Next focused")
        self.assertEqual(self.b.js("lastSection"), "jellyfin", "still known, for starting in the part used last")


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class UiBooksTest(UiBase):
    section = None
    with_player = True
    audiobookshelf = True

    def setUp(self):
        self.abs.progress.clear()
        self.abs.progress["bk10"] = {"currentTime": 1.0, "progress": 0.125, "isFinished": False}
        super().setUp()
        self.backend.request("/music/control", {"cmd": "stop"})
        self.b.js("musicPoll()")

    def tearDown(self):
        self.backend.request("/music/control", {"cmd": "stop"})
        super().tearDown()

    def open_books(self):
        self.b.js("focusEl($('[data-k=\"menu-books\"]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'books' && document.querySelector('.music-home .card')", msg="audiobooks")

    def test_up_next_puts_watching_before_listening(self):
        # the book heard a moment ago, the film watched long ago: the film first all the same
        self.b.js("""const f = window.fetch, now = Date.now() / 1000;
            window.fetch = (u, o) => String(u).endsWith('/abs/progress') ? f(u, o).then((r) => r.json()).then((p) => new Response(JSON.stringify(
                  Object.fromEntries(Object.entries(p).map(([k, v]) => [k, { ...v, at: now + 600 }])))))
              : f(u, o); shelfCache.clear()""")
        self.b.js("render({ name: 'menu' }, 'menu-next')")
        self.b.wait_js("[...document.querySelectorAll('#shelf .label')].some((x) => x.textContent === 'Folge 10: The Tenth')"
                       " && [...document.querySelectorAll('#shelf .label')].some((x) => x.textContent === 'Movie 1')", msg="both")
        labels = self.texts("#shelf .label")
        self.assertEqual(labels[-1], "Folge 10: The Tenth", "the book last")
        self.assertLess(labels.index("Movie 1"), labels.index("Folge 10: The Tenth"))

    def test_menu_tile_and_shelf(self):
        self.assertEqual(self.texts(".menu .tiles .label"), ["Up Next", "Movies & Shows", "Music", "Audiobooks", "Search", "Settings"])
        self.b.js("focusEl($('[data-k=menu-books]'))")  # the dock starts on Up Next
        self.assertEqual(self.focus(), "menu-books")
        self.b.wait_js("document.querySelector('#shelf').dataset.for === 'books' && document.querySelector('#shelf .card.sq')",
                       msg="books on the shelf")
        self.assertEqual(self.texts("#shelf h2"), ["Continue Listening"])
        self.assertEqual(self.texts("#shelf .label"), ["Folge 10: The Tenth"])
        # three books are too few for a wall (the same few over and over), and the part is no one book: nothing above
        self.b.wait_js("!$('#stage-art .wall.on') && !$('#stage-art .cover-art.on')", msg="nothing above, not one book's cover")
        self.b.key("ArrowUp")
        self.assertTrue(self.b.js("!!document.activeElement.closest('#shelf')"), "up on the shelf")
        self.b.key("Escape")
        self.assertEqual(self.focus(), "menu-books", "Back: down into the dock, on the shelf's tile")
        self.b.key("ArrowLeft")
        self.b.key("ArrowLeft")  # past the music, not set up here
        self.b.wait_js("$('#stage-art .wall.poster.on')", msg="Jellyfin's wall of posters")

    def test_music_playing_is_not_put_first_among_the_books(self):
        self.b.js("musicPoll = () => {}; musicState = {active: true, index: 0, queue: [{id: 's1', title: 'A Song', artist: 'Someone'}]}")
        self.b.js("navigate('books')")
        self.b.wait_js("current.name === 'books' && document.querySelector('.music-home .card')", msg="audiobooks")
        self.assertNotEqual(self.b.js("$('[data-k=bsec-0]').textContent.trim()"), "Now Playing", "a song: not here")
        self.assertNotIn("A Song", self.b.js("$('#hero').textContent"))
        self.b.js("musicState.queue[0].book = 'bk10'; navigate('books')")
        self.b.wait_js("current.name === 'books' && $('[data-k=bsec-0]')?.textContent.trim() === 'Now Playing'", msg="a book: first")

    def test_books_library_and_a_book(self):
        self.open_books()
        self.assertEqual(self.texts(".music-home .row h2"), ["Continue Listening", "New in Stories"], "the libraries as buttons, without a heading")
        self.assertEqual(self.texts('[data-k^="listen-"] .label'), ["Folge 10: The Tenth"])
        self.assertTrue(self.b.js("!!$('[data-k=\"listen-0\"] .progress')"), "how far it was heard")
        self.assertEqual(self.focus(), "bsec-0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'booklib' && document.querySelector('.grid .card')", msg="the library")
        self.assertEqual(self.cards(), ["Folge 1: The First", "Folge 2: The Second", "Folge 10: The Tenth"], "in their order")
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'book' && document.querySelector('.music-detail h1')", msg="the book")
        self.assertEqual(self.b.js("$('.music-detail h1').textContent"), "Folge 10: The Tenth")
        meta = self.b.js("$('.music-detail .meta').textContent")
        self.assertIn("read by A Reader", meta)
        self.assertIn("1999", meta)
        self.assertIn("left", meta)
        self.assertEqual(self.b.js("$('.music-detail .overview').textContent"), "What it is about.", "the text without its markup")
        self.assertEqual(self.texts(".music-detail .actions .btn"), ["Resume", "Start Over", "Mark as Heard", "Mark as Not Heard"],
                         "begun: either, as a movie")
        self.assertEqual(self.texts(".music-detail .track .t"), ["Part 1", "Part 2"])
        self.assertEqual(self.focus(), "bplay")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && musicState.queue?.[0]?.book", msg="now playing")
        self.assertEqual(self.focus(), "np-pause")
        self.assertEqual(self.b.js("$('.np-title').textContent"), "Folge 10: The Tenth")
        self.assertEqual(self.b.js("$('.np-up').textContent"), "Chapters")
        self.assertEqual(self.texts(".np-queue .track .t"), ["Part 1", "Part 2"])
        self.assertEqual(self.b.js("$('.np-album').textContent"), "Part 1")
        shown = self.b.js("[...document.querySelectorAll('.np-buttons .btn')].filter((b) => !b.hidden).map((b) => b.dataset.k)")
        self.assertEqual(shown, ["np-back", "np-pause", "np-fwd", "np-speed", "np-sleep"])
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("musicState.speed === 1.1", msg="a little faster")
        self.assertEqual(self.b.js("$('[data-k=\"np-speed\"]').textContent"), "1.1×")
        self.b.key("ArrowDown")
        self.b.wait_js("$('.np-album').textContent === 'Part 2'", msg="down: the next chapter")
        self.assertEqual(self.focus(), "np-speed", "the focus stays on the buttons")
        self.b.key("ArrowUp")
        self.b.wait_js("$('.np-album').textContent === 'Part 1'", msg="up at a chapter's start: the one before")
        self.b.key("Enter", hold_ms=900)
        self.assertIn("Choose a Chapter", self.texts("#options .opt"))
        self.b.key("Escape")
        self.b.js("focusEl($('[data-k=\"chapter-1\"]'))")
        self.b.key("Enter")
        self.b.wait_js("$('.np-album').textContent === 'Part 2'", msg="the second chapter")
        self.b.key("MediaRewind")
        self.b.wait_js("musicState.pos < 3", msg="30 seconds back")

    def test_a_year_from_the_server_cannot_run_code(self):
        bk = self.abs.item("bk1")
        bk["media"]["metadata"]["publishedYear"] = '<img src=x onerror="window.pwned=1">'
        self.addCleanup(bk["media"]["metadata"].update, publishedYear="1999")
        self.b.js("navigate('book', {id: 'bk1'})")
        self.b.wait_js("current.name === 'book' && document.querySelector('.music-detail h1')", msg="the book")
        time.sleep(0.5)
        self.assertFalse(self.b.js("!!window.pwned || !!document.querySelector('.music-detail .meta img')"), "not taken as markup")
        self.assertNotIn("img", self.b.js("$('.music-detail .meta').textContent"), "only a year is shown")

    def test_a_book_tile_s_options(self):
        self.open_books()
        self.b.js("focusEl($('[data-k=\"listen-0\"]'))")
        self.b.key("Enter", hold_ms=900)
        self.assertEqual(self.texts("#options .opt")[:3], ["Mark as Heard", "Mark as Not Heard", "Start Over"],
                         "begun: either, as a movie")
        self.b.key("ArrowDown")
        self.b.key("Enter")
        wait_for(lambda: self.abs.progress["bk10"].get("currentTime") == 0, msg="from the start again")
        self.b.wait_js("$('#toast').textContent === 'Marked as not heard'", msg="said so")

    def test_a_begun_book_marked_as_not_heard(self):
        self.b.js("navigate('book', {id: 'bk10'})")
        self.b.wait_js("current.name === 'book' && $('[data-k=bunheard]')", msg="the begun book")
        self.b.js("focusEl($('[data-k=bunheard]'))")
        self.b.key("Enter")
        wait_for(lambda: self.abs.progress["bk10"].get("currentTime") == 0, msg="from the start again")
        self.assertFalse(self.abs.progress["bk10"].get("isFinished"))
        self.b.wait_js("$('.music-detail .actions .btn')?.textContent === 'Play'", msg="no longer begun")
        self.assertEqual(self.texts(".music-detail .actions .btn"), ["Play", "Mark as Heard"])

    def test_remove_from_continue_listening(self):
        self.open_books()
        self.b.js("focusEl($('[data-k=\"listen-0\"]'))")
        self.pick_option("Remove from This Row")
        self.b.wait_js("!document.querySelector('[data-k^=\"listen-\"]')", msg="gone from the row")
        self.assertEqual(self.backend.request("/hidden")[1]["books"], ["bk10"])
        self.assertEqual(self.abs.hidden[-1], "bk10")

    def test_search_in_the_audiobooks(self):
        self.open_books()
        self.b.js("focusEl($('[data-k=tab-search]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'booksearch' && $('#results .suggested')", msg="the search, with suggestions")
        self.b.type("folge")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 3", msg="books found")
        self.assertEqual(self.texts("#results .label"), ["Folge 1: The First", "Folge 2: The Second", "Folge 10: The Tenth"],
                         "in their order, without the one whose files are gone")
        self.b.js("navigate('booksearch', {q: 'the author'})")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 3", msg="the author's books")
        self.assertEqual(self.texts("#results .label"), ["Folge 1: The First", "Folge 2: The Second", "Folge 10: The Tenth"])
        self.b.js("navigate('booksearch', {q: 'bedtime'})")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 1", msg="the series' books")
        self.assertEqual(self.texts("#results .label"), ["Folge 10: The Tenth"])


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiSignInEverywhereTest(UiBase):
    """Its own backend: Jellyfin is signed in as another person here."""
    section = None

    def test_sign_in_everywhere(self):
        self.mock.accounts["robin"] = "Same-Everywhere-7"  # invented
        self.b.js("navigate('settings', {part: 'account'})")
        self.b.wait_js("$('[data-k=part-everywhere]')", msg="the account's settings")
        self.b.js("activate($('[data-k=part-everywhere]'))")
        self.b.wait_js("$('[data-k=ev-username]')", msg="its page")
        self.b.js("activate($('[data-k=ev-username]'))")
        self.b.wait_js("current.name === 'svcentry'", msg="typing the user name")
        self.b.type("robin")
        self.b.js("activate($('[data-k=svc-done]'))")
        self.b.wait_js("current.name === 'settings' && $('[data-k=ev-username] .sval').textContent === 'robin'", msg="the name kept")
        self.b.js("svcDraft.everywhere.password = 'Same-Everywhere-7'; activate($('[data-k=ev-signin]'))")
        # another Jellyfin user: Matinee starts anew, on this page, saying how it went at each service
        self.b.wait_js("current?.name === 'settings' && $('[data-k=ev-jellyfin]')", timeout=20, msg="the result")
        self.assertEqual(self.texts("[data-k^=ev-] .sval")[-4:],
                         ["✓ Signed in"] + ["Not Found"] * 3)
        self.assertEqual(self.backend.request("/settings")[1]["username"], "robin")


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class UiMusicTest(UiBase):
    section = None
    with_player = True
    navidrome = True

    def setUp(self):
        super().setUp()
        self.backend.request("/music/control", {"cmd": "stop"})
        self.b.js("musicPoll()")

    def tearDown(self):
        self.backend.request("/music/control", {"cmd": "stop"})
        super().tearDown()

    def test_search_everything_shows_a_single_once(self):
        single = self.nd.albums[2]["song"][0]
        title = single["title"]
        single["title"] = "Solo"  # the song named as its album
        self.addCleanup(single.update, title=title)
        self.b.js("navigate('allsearch', {q: 'solo'})")
        self.b.wait_js("$('#all-music .card')", timeout=6, msg="the music's part")
        self.b.wait_js("!document.body.classList.contains('busy')", timeout=10, msg="every part answered")
        self.assertEqual(self.texts("#all-music .label"), ["Solo"], "the album stands for its song")
        self.assertFalse(self.b.js("!!$('[data-k=all-more-music]')"), "nothing more to see")

    def test_a_button_with_a_sign_alone_says_what_it_does(self):
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("current.name === 'album' && $('[data-k=mplay]')", msg="the album")
        self.assertNotIn("0 min", self.b.js("$('.meta').textContent"), "an album of seconds is a minute long, not none")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && document.activeElement.dataset.k === 'np-pause'", msg="Now Playing")
        tip = "getComputedStyle(document.activeElement, '::after').content"
        self.assertEqual(self.b.js(tip), "none", "pause says it itself")
        self.b.js("focusEl($('[data-k=np-star]'))")
        self.assertEqual(self.b.js(tip), '"Favorite"')
        self.b.js("focusEl($('[data-k=np-sleep]'))")
        self.assertEqual(self.b.js(tip), '"Sleep Timer"')
        foot = self.b.js("(() => { const b = document.activeElement.getBoundingClientRect(); return b.bottom + parseFloat(getComputedStyle(document.activeElement, '::after').height) + 8; })()")
        self.assertLess(foot, self.b.js("$('.np-up').getBoundingClientRect().top + 8"), "in the gap above the queue")

    def open_music(self):
        self.assertEqual(self.texts(".menu .tiles .label"), ["Up Next", "Movies & Shows", "Music", "Audiobooks", "Search", "Settings"])
        self.b.js("focusEl($('[data-k=menu-music]'))")  # the dock starts on Up Next
        self.assertEqual(self.focus(), "menu-music")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'music' && document.querySelector('.music-home .card')", msg="music home")

    def test_settings_in_the_bar_open_the_part_of_its_service(self):
        self.open_music()
        self.b.js("focusEl($('[data-k=tab-settings]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'settings' && $('.settings-part')?.textContent === 'Music'", msg="Navidrome's settings")

    def test_music_home(self):
        self.open_music()
        self.assertEqual(self.texts('[data-k^="msec-"]'), ["Albums", "Artists", "Favorites", "Playlists", "Radio", "Shuffle All"])
        self.assertEqual(self.texts(".music-home .row h2"), ["Recently Added", "Recently Played", "Most Played", "Random Albums"])
        self.assertIn("/nd/getCoverArt?id=al3", self.b.js("document.querySelector('[data-k=\"new-0\"] img').src"))

    def test_no_row_cut_through_at_the_top(self):
        self.open_music()
        cut = """(() => { const top = $('.home .rows').getBoundingClientRect().top;
            return [...document.querySelectorAll('.home .row')].filter((r) => { const b = r.getBoundingClientRect();
              return b.top < top - 1 && b.bottom > top + 1; }).map((r) => r.querySelector('h2').textContent); })()"""
        for _ in range(6):  # down to the last row, where the rows move only as far as the last one ends at the bottom
            self.b.key("ArrowDown")
            time.sleep(0.6)  # the rows have slid into place
            self.assertEqual(self.b.js(cut), [], "no half titles of a row above under the top")
        self.assertEqual(self.b.js("document.activeElement.closest('.row').querySelector('h2').textContent"), "Random Albums")

    def test_remove_an_album_from_recently_played(self):
        self.open_music()
        self.go_to_row("recent")
        name = self.focus_text()
        before = len(self.texts('[data-k^="recent-"] .label'))
        self.pick_option("Remove from This Row")
        self.b.wait_js(f"document.querySelectorAll('[data-k^=\"recent-\"]').length === {before - 1}", msg="one album less")
        self.assertTrue(self.focus().startswith("recent-"), "the focus stays in the row")
        self.assertEqual(len(self.backend.request("/hidden")[1]["albums"]), 1)
        self.b.js("navigate('settings', {part: 'navidrome'})")
        self.b.wait_js("current.name === 'settings' && document.querySelector('.settings h1')")
        self.assertIn("Share Listening History", self.b.js("$('.settings').textContent"))
        self.b.js("navigate('settings', {part: 'basics'})")
        self.b.wait_js("document.querySelector('[data-k=\"set-unhide\"]')", msg="the button to show it again")
        self.assertEqual(self.b.js("$('[data-k=\"set-unhide\"] .sval').textContent"), "Show 1 Again")
        self.b.js("focusEl($('[data-k=\"set-unhide\"]'))")
        self.b.key("Enter")
        wait_for(lambda: self.backend.request("/hidden")[1]["albums"] == [], msg="shown again")
        self.b.wait_js("!document.querySelector('[data-k=\"set-unhide\"]')", msg="nothing hidden any more")
        self.b.js("navigate('music')")
        self.b.wait_js(f"document.querySelectorAll('[data-k^=\"recent-\"]').length === {before}", msg="the album is back")
        self.assertIn(name.split("\n")[0], self.b.js("$('.music-home').textContent"))

    def test_shelf_of_navidrome(self):
        self.b.js("focusEl($('[data-k=menu-music]'))")  # the dock starts on Up Next
        self.assertEqual(self.focus(), "menu-music")
        self.b.wait_js("document.querySelector('#shelf').dataset.for === 'music' && document.querySelector('#shelf .card.sq')",
                       msg="albums on the shelf")
        self.assertEqual(self.texts("#shelf h2"), ["Recently Played"])
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "shelf-music-0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'album'", msg="album opened")

    def test_last_music_row_fully_on_the_screen(self):
        self.open_music()
        for _ in range(8):
            self.b.key("ArrowDown", pause=0.15)
        time.sleep(0.7)
        bottom = self.b.js("[...document.querySelectorAll('.music-home .row')].pop().getBoundingClientRect().bottom")
        self.assertLessEqual(bottom, self.b.js("innerHeight"), "the titles below the covers are not cut off")
        self.assertGreater(bottom, self.b.js("innerHeight") - 120, "and there is no empty space below")

    def test_play_next_from_the_options(self):
        songs = self.backend.request("/nd/getAlbum?id=al1")[1]["album"]["song"]
        self.backend.request("/music/play", {"songs": songs, "start": 0})
        self.b.js("musicPoll()")
        self.b.wait_js("musicState.active && musicState.queue?.length === 3", msg="music plays")
        self.backend.request("/music/control", {"cmd": "pause"})
        self.b.js("navigate('album', {id: 'al3'})")
        self.b.wait_js("current.name === 'album' && document.querySelector('[data-action=mtrack]')", msg="album page")
        self.b.js("focusEl(document.querySelector('[data-action=mtrack]'))")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="options open")
        labels = self.texts("#options .opt")
        self.assertIn("Play Next", labels)
        self.assertIn("Add to Queue", labels)
        for _ in range(labels.index("Play Next")):
            self.b.key("ArrowDown")
        self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Plays next: Song 7'", msg="says so")
        self.b.wait_js("musicState.queue?.map((x) => x.title).join() === 'Song 1,Song 7,Song 2,Song 3'", msg="in the queue after the playing song")
        self.assertEqual(self.view(), "album", "the page stays")

    def test_to_the_artist_and_the_album_from_the_options(self):
        """As from a podcast's episode to its show: a song to its artist or its album, from a page or the music's bar."""
        self.b.js("navigate('album', {id: 'al3'})")
        self.b.wait_js("current.name === 'album' && document.querySelector('[data-action=mtrack]')", msg="album page")
        self.b.js("focusEl(document.querySelector('[data-action=mtrack]'))")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="options open")
        labels = self.texts("#options .opt")
        self.assertIn("Go to Artist", labels)
        self.assertNotIn("Go to Album", labels, "on its album already")
        for _ in range(labels.index("Go to Artist")):
            self.b.key("ArrowDown")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'artist' && current.args.id === 'ar2'", msg="Beta Singer's page")
        songs = self.backend.request("/nd/getAlbum?id=al1")[1]["album"]["song"]
        self.backend.request("/music/play", {"songs": songs, "start": 0})
        self.b.js("musicPoll()")
        self.b.wait_js("musicState.active && !$('#nowbar').hidden", msg="music plays")
        self.backend.request("/music/control", {"cmd": "pause"})
        self.b.js("focusEl($('#nowbar'))")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!$('#options').hidden", msg="options of the song playing")
        labels = self.texts("#options .opt")
        self.assertEqual(labels[:2], ["Go to Album", "Go to Artist"])
        self.b.key("Enter")
        self.b.wait_js("current.name === 'album' && current.args.id === 'al1'", msg="its album")

    def test_the_bar_of_the_music_takes_the_focus(self):
        """Down from a page's last row: the bar of the music playing; up back, OK opens Now Playing, OK held its star."""
        self.open_music()
        self.b.key("ArrowDown")
        self.b.key("Enter")  # an album
        self.b.wait_js("current.name === 'album' && document.querySelectorAll('.track').length", msg="album page")
        self.b.key("ArrowDown")
        self.b.key("Enter")  # its first song
        self.b.wait_js("current.name === 'nowplaying'", msg="now playing")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'album' && !$('#nowbar').hidden", msg="the bar under the album")
        last = "[...view.querySelectorAll('.f')].at(-1)"
        self.b.js(f"focusEl({last})")
        self.b.key("ArrowDown")
        self.assertEqual(self.b.js("document.activeElement.id"), "nowbar")
        self.b.key("ArrowUp")
        self.assertTrue(self.b.js(f"document.activeElement === {last}"), "back where it came from")
        self.b.key("ArrowDown")
        self.b.key("Enter", hold_ms=900)
        self.b.wait_js("!options.hidden", msg="its options")
        self.assertIn("Add to Favorites", self.texts("#options .opt"))
        self.b.key("Escape")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying'", msg="OK: Now Playing")
        self.b.js("mainMenu()")
        self.b.wait_js("current.name === 'menu'", msg="the main menu")
        self.assertEqual(self.b.js("$('#nowbar').offsetWidth"), 0, "no bar over the dock")
        self.assertIn("getCoverArt", self.b.js("$('[data-k=menu-music] .art img.now')?.src ?? ''"), "the Music tile: the cover playing")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.menu .tiles .ring')].map((r) => r.parentElement.dataset.k)"),
                         ["menu-music"], "a ring round the Music tile alone")
        self.backend.request("/music/control", {"cmd": "pause"})
        self.b.wait_js("$('[data-k=menu-music] .ring.paused')", msg="the ring still while paused")
        self.backend.request("/music/control", {"cmd": "stop"})
        self.b.wait_js("!$('.menu .tiles .ring')", msg="the ring gone with the music")

    def test_now_playing_tab(self):
        """While music plays, the bar at the top of a start has a tab more: Now Playing. It goes when the music stops."""
        self.open_music()
        self.assertFalse(self.b.js("!!$('[data-k=tab-nowplaying]')"), "nothing playing: no tab")
        self.b.key("ArrowDown")
        self.b.key("Enter")  # an album
        self.b.wait_js("current.name === 'album' && document.querySelectorAll('.track').length", msg="album page")
        self.b.key("ArrowDown")
        self.b.key("Enter")  # its first song
        self.b.wait_js("current.name === 'nowplaying'", msg="now playing")
        self.b.key("Escape")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'music' && $('[data-k=tab-nowplaying]')", msg="the tab on the music's start")
        self.assertEqual([x for x in self.texts(".tabbar .tab") if x][-1:], ["Now Playing"], "last of the named tabs, before the gear")
        self.b.js("focusEl($('[data-k=tab-nowplaying]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying'", msg="OK: Now Playing")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'music'")
        self.backend.request("/music/control", {"cmd": "stop"})
        self.b.wait_js("!$('[data-k=tab-nowplaying]')", msg="the tab gone with the music")

    def test_no_jump_while_the_song_loads(self):
        """mpv knows no position for a moment while it loads more of a song: the line stays, not back to the start."""
        self.b.js("""musicState = { active: true, index: 0, pos: 30, duration: 100, queue: [{ id: 's1', title: 'Song 1' }], version: 1 };
          window.realFetch = window.fetch;
          window.fetch = (url, o) => String(url).startsWith('/music/status')
            ? Promise.resolve(new Response(JSON.stringify({ active: true, index: 0, pos: null, duration: null, paused: false, version: 1 })))
            : window.realFetch(url, o)""")
        try:
            self.b.js("musicPoll()")
            self.b.wait_js("musicState.pos === 30 && musicState.duration === 100", msg="the last position kept")
        finally:
            self.b.js("window.fetch = window.realFetch; clearTimeout(musicTimer)")

    def test_play_an_album(self):
        self.open_music()
        self.b.key("ArrowDown")  # recently added: Solo, Second Album, First Album
        self.b.key("ArrowRight")
        self.assertTrue(self.focus_text().startswith("Second Album"), self.focus_text())
        self.b.key("Enter")
        self.b.wait_js("current.name === 'album' && document.querySelectorAll('.track').length === 3", msg="album page")
        self.assertEqual(self.texts(".track .t"), ["Song 4", "Song 5", "Song 6"])
        self.assertEqual(self.focus(), "mplay")
        self.b.key("ArrowDown")
        self.b.key("ArrowDown")  # the second song
        self.assertEqual(self.focus(), "track-1")
        self.b.key("Enter")
        # the music's own mpv starts first: slower on a busy machine
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 5'", timeout=20, msg="now playing")
        self.assertEqual(self.focus(), "np-pause")
        wait_for(lambda: ("s5", False) in self.nd.scrobbles, msg="Navidrome told")
        # the queue's mark follows the player's state a moment after the title
        self.b.wait_js("$('.np-queue .track.on .t')?.textContent === 'Song 5Alpha Band'", msg="marked in the queue")
        self.assertTrue(self.b.js("$('#nowbar').hidden"), "no bar on Now Playing itself")
        self.b.key("Enter")  # pause
        self.b.wait_js("musicState.paused === true", msg="paused")
        self.b.key("Enter")
        self.b.wait_js("musicState.paused === false", msg="playing again")
        self.b.key("ArrowRight")
        self.b.key("Enter")  # next
        self.b.wait_js("$('.np-title').textContent === 'Song 6'", timeout=20, msg="next song")  # mpv loads it: slower when busy
        self.b.key("Escape")
        self.b.wait_js("current.name === 'album'")
        self.b.wait_js("!$('#nowbar').hidden && $('#nowbar b').textContent === 'Song 6'", msg="the bar shows what plays")
        self.b.key("MediaPlayPause")
        self.b.wait_js("musicState.paused === true", msg="the play key pauses the music")

    def test_now_playing_gives_way_when_the_music_ends(self):
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 1'")
        self.assertEqual(self.backend.request("/music/control", {"cmd": "stop"})[0], 200)
        self.b.wait_js("current.name === 'album'", msg="back on the album, not an empty page")

    def test_fast_forward_and_rewind_keys_skip_songs(self):
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 1'")
        self.b.js("window.queueList = $('.np-queue-track')")
        time.sleep(2.5)  # the backend has answered a few times meanwhile
        self.assertTrue(self.b.js("$('.np-queue-track') === window.queueList"), "the same songs: not drawn anew")
        self.b.key("MediaFastForward")
        self.b.wait_js("$('.np-title').textContent === 'Song 2'", msg="next song")
        self.b.wait_js("$('.np-queue .track.on .t')?.textContent.startsWith('Song 2')", msg="the mark moved")
        self.assertTrue(self.b.js("$('.np-queue-track') === window.queueList"), "only the mark moves")
        self.b.key("MediaRewind")  # within its first seconds: the song before
        self.b.wait_js("$('.np-title').textContent === 'Song 1'", msg="previous song")

    def test_the_queues_last_song_stays_clear_of_the_screens_foot(self):
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-queue .track')", msg="now playing with its queue")
        # a long queue: the last song far below the box
        self.b.js("$('.np-queue').innerHTML = `<div class=\"np-queue-track\">${trackRows(Array.from({length: 40}, (_, i) => "
                  "({id: `x${i}`, title: `Made-up Song ${i}`, artist: 'Made-up Artist'})), 'queue', true)}</div>`;"
                  "focusEl($('.np-queue .track:last-child'), false)")
        bottom, keep = self.b.js("[$('.np-queue .track:last-child').getBoundingClientRect().bottom, "
                                 "innerHeight - parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--edge-bottom')) / 100 * innerHeight]")
        self.assertLessEqual(bottom, keep + 1, "above the foot a TV may cut off")
        self.assertGreater(bottom, keep - 120, "and right above it, the queue gone as far as it goes")

    def test_a_bluetooth_speakers_buttons(self):
        """Headphones' and a speaker's buttons (AVRCP, as keys): next, previous, pause and play, stop."""
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 1'")
        self.b.key("MediaTrackNext")
        self.b.wait_js("$('.np-title').textContent === 'Song 2'", msg="next song")
        self.b.key("MediaTrackPrevious")
        self.b.wait_js("$('.np-title').textContent === 'Song 1'", msg="previous song")
        self.b.key("MediaPlayPause")
        wait_for(lambda: self.backend.request("/music/status")[1].get("paused"), msg="paused")
        self.b.key("MediaPlayPause")
        wait_for(lambda: not self.backend.request("/music/status")[1].get("paused"), msg="playing again")
        self.b.key("MediaStop")
        wait_for(lambda: not self.backend.request("/music/status")[1].get("active"), msg="stopped")

    def test_shuffle_all(self):
        self.open_music()
        for _ in range(5):
            self.b.key("ArrowRight")
        self.assertEqual(self.focus_text(), "Shuffle All")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && musicState.queue.length === 9", msg="all songs queued")

    def test_search(self):
        self.open_music()
        self.b.js("focusEl($('[data-k=tab-search]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'musicsearch'")
        self.b.type("solo")
        self.b.wait_js("document.querySelectorAll('#results .card').length === 1", msg="one album")
        self.assertEqual(self.texts("#results .results-title"), ["Albums"])
        self.b.type(" ")
        self.b.key("Backspace")
        for _ in range(4):
            self.b.key("Backspace")
        self.b.type("song 7")
        self.b.wait_js("document.querySelectorAll('#results [data-action=msong]').length === 1", msg="a song")
        self.b.js("focusEl(document.querySelector('#results [data-action=msong]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 7'", msg="plays it")

    def test_favorites(self):
        self.nd.starred.clear()
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "track-0")
        self.b.key("Enter", hold_ms=1000)  # hold OK: the options, with a star
        for _ in range(self.b.js("optionItems.findIndex((x) => x.label === t('starIt'))")):
            self.b.key("ArrowDown")
        self.b.key("Enter")
        wait_for(lambda: "s1" in self.nd.starred, msg="song starred")
        self.b.wait_js("document.activeElement?.dataset.k === 'track-0'", msg="focus back on the song")
        self.b.key("ArrowUp")  # to the buttons
        for _ in range(4):
            if self.focus() == "star":
                break
            self.b.key("ArrowLeft" if self.focus() == "toartist" else "ArrowRight")
        self.assertEqual(self.focus(), "star")
        self.b.key("Enter")
        wait_for(lambda: "al1" in self.nd.starred, msg="album starred")
        self.b.wait_js("document.querySelector('[data-k=star]')?.dataset.on === '1'", msg="the button shows it")
        self.b.js("navigate('favorites', {})")
        self.b.wait_js("current.name === 'favorites' && document.querySelectorAll('.grid .card').length === 2")
        self.assertEqual(self.texts(".results-title"), ["Songs", "Albums"])
        self.b.key("Enter")  # play songs
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 1'", msg="plays them")
        self.b.wait_js("$('[data-k=np-star]').classList.contains('on')", msg="starred in Now Playing too")

    def test_lyrics(self):
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Enter")  # play: Song 1, whose lyrics have times
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 1'")
        self.b.js("focusEl($('[data-k=np-lyrics]'))")
        self.b.key("Enter")
        self.b.wait_js("$('.np-lyrics p.on')?.textContent === 'First line'", msg="the line being sung")
        self.assertTrue(self.b.js("$('.np-queue').hidden"))
        self.b.wait_js("$('.np-lyrics p.on')?.textContent === 'Second line'", timeout=5, msg="follows the song")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'album'", msg="back on the album")
        self.b.js("navigate('nowplaying', {})")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-lyrics p')", msg="opened again: the same song's lyrics are there")
        self.b.js("focusEl($('[data-k=np-lyrics]'))")
        self.b.key("Enter")
        self.b.wait_js("!$('.np-queue').hidden && $('.np-lyrics').hidden", msg="back to the queue")

    def test_sleep_timer_button(self):
        self.b.js("navigate('album', {id: 'al1'})")
        self.b.wait_js("document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Song 1'")
        self.b.js("focusEl($('[data-k=np-sleep]'))")
        self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Sleep timer: 30 min'", msg="said")
        self.b.wait_js("$('[data-k=np-sleep] span').textContent === '30 min'", msg="shown on the button")
        for _ in range(4):  # 60, 90, at the end, off
            self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Sleep timer off' && $('[data-k=np-sleep] span').textContent === ''")

    def test_radio(self):
        self.open_music()
        for _ in range(4):
            self.b.key("ArrowRight")
        self.assertEqual(self.focus_text(), "Radio")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'radio' && document.querySelectorAll('.grid .card').length === 1")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && $('.np-title').textContent === 'Mock FM'", msg="station plays")
        shown = lambda sel: self.b.js(f"$('{sel}').getBoundingClientRect().width > 0")
        self.assertFalse(shown(".np-time"), "a stream has no length")
        self.assertFalse(shown("[data-k=np-star]"), "no star for a station")
        self.assertFalse(shown("[data-k=np-next]"))
        self.assertTrue(shown("[data-k=np-pause]"))

    def test_albums_open_on_the_first_album(self):
        self.b.js("navigate('albums', {})")
        self.b.wait_js("current.name === 'albums' && document.querySelector('.grid .card')", msg="all albums")
        self.assertEqual(self.focus(), "album-al1", "on the first album, not on the sorting")
        self.assertEqual(self.texts(".chip.sel"), ["Artist"], "by artist unless chosen otherwise")
        self.b.key("ArrowUp")
        self.assertTrue(self.focus().startswith("asort-"), "the sorting above it")
        self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.args.sort === 'alphabeticalByName' && document.querySelector('.grid .card')", msg="sorted anew")
        self.assertEqual(self.focus(), "asort-alphabeticalByName", "the focus stays on the chip just chosen")

    def test_back_to_an_album_from_a_later_page(self):
        self.b.js("ALBUMS_FIRST = 1; ALBUMS_PAGE = 1; navigate('albums', {})")
        self.b.wait_js("document.querySelectorAll('.grid .card').length === 3", msg="all albums, one page at a time")
        self.b.js("focusEl(document.querySelector('[data-k=\"album-al3\"]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'album' && document.querySelectorAll('.track').length === 3", msg="album page")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'albums' && document.activeElement?.dataset.k === 'album-al3'",
                       msg="back on that album, though it came with a later page")
        self.b.js("ALBUMS_FIRST = 60; ALBUMS_PAGE = 120")

    def test_artist_and_playlists(self):
        self.open_music()
        self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'artists' && document.querySelectorAll('.grid .card').length === 2")
        self.assertEqual(self.texts(".grid .card .label"), ["Alpha Band", "Beta Singer"])
        self.b.key("Enter")
        self.b.wait_js("current.name === 'artist' && document.querySelectorAll('.grid .card').length === 2", msg="its albums")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'artists'")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'music'")
        self.b.key("ArrowRight")
        self.b.key("ArrowRight")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'playlists' && document.querySelectorAll('.grid .card').length === 1")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'playlist' && document.querySelectorAll('.track').length === 2")
        self.assertEqual(self.texts(".track small"), ["Alpha Band", "Beta Singer"], "a playlist names the artists")


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class UiOwnChannelsTest(UiBase):
    section = None
    extra_env = {"KINO_OWN_MIN": "4"}  # the test library is small: four films make a channel

    def test_the_library_as_channels(self):
        self.b.js("stack = []; render({ name: 'ownchannels', args: {} })")
        self.b.wait_js("current.name === 'ownchannels' && $('[data-k=live-own-g-action]')", msg="a channel of the action films")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.grid .label')].map((x) => x.textContent)"),
                         ["Program Guide", "Action", "Comedy", "Drama", "Movies of the 2010s"], "the guide in front of them")
        self.b.wait_js("/^Movie \\d+$/.test($('[data-k=live-own-g-action] .sub').textContent)", msg="what runs on it now")
        self.b.wait_js("$('[data-k=live-own-g-action] .now-pic')?.getAttribute('src').startsWith('/jf/Items/m')",
                       msg="with the film's picture from Jellyfin")
        self.b.js("focusEl($('[data-k=own-guide]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'guide' && $('.epg-cell.now')", msg="the guide")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.epg-cell')].filter((c) => +c.dataset.end <= +c.dataset.start).length"),
                         0, "no cell for a programme outside the guide's hours (invisible, it took the focus)")
        self.assertEqual(self.b.js("epg.channels.map((c) => c.name)"), ["Action", "Comedy", "Drama", "Movies of the 2010s"])
        self.assertRegex(self.b.js("epg.channels[0].items[0].jf"), r"^m\d+$", "each programme the film it is")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'ownchannels' && document.activeElement?.dataset.k === 'own-guide'", msg="back on it")

    def test_a_button_after_the_libraries(self):
        self.b.js("stack = []; render({ name: 'home', args: {} })")
        self.b.wait_js("current.name === 'home' && $('[data-k=libs-own]')", msg="Jellyfin's start with the channels' button")
        self.assertEqual(self.b.js("$('[data-k=libs-own]').textContent"), "Channels")
        self.assertIsNone(self.b.js("$('[data-k=libs-own]').nextElementSibling"), "after the libraries")
        self.b.js("focusEl($('[data-k=libs-own]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'ownchannels' && document.activeElement?.dataset.k === 'live-own-g-action'", msg="the channels, on the first")
        self.assertEqual(self.b.js("[...document.querySelectorAll('.grid [data-action=tvlive] .label')].map((x) => x.textContent)"),
                         ["Action", "Comedy", "Drama", "Movies of the 2010s"])
        self.b.wait_js("/^Movie \\d+$/.test(document.activeElement.querySelector('.sub').textContent)", msg="what runs on it now")
        self.b.key("Escape")
        self.b.wait_js("current.name === 'home' && document.activeElement?.dataset.k === 'libs-own'", msg="back on the button")

    def test_a_channel_left_out_in_the_settings(self):
        self.b.js("stack = []; render({ name: 'home', args: {} })")
        self.b.wait_js("current.name === 'home' && $('[data-k=libs-own]')", msg="Jellyfin's start")
        self.b.js("navigate('settings', {part: settingsPart()})")  # from Jellyfin: its own settings
        self.b.wait_js("document.querySelector('[data-k=tvch-own-g-drama]')", msg="the channels among Jellyfin's settings")
        try:
            self.b.js("focusEl($('[data-k=tvch-own-g-drama]'))")
            self.b.key("Enter")
            self.b.wait_js("$('[data-k=tvch-own-g-drama] .sval')?.textContent === 'Hidden'", msg="hidden")
            self.assertEqual(self.focus(), "tvch-own-g-drama", "the focus stays on it")
            self.b.js("navigate('ownchannels')")
            self.b.wait_js("current.name === 'ownchannels' && $('.grid [data-action=tvlive]')", msg="the channels")
            self.assertEqual(self.b.js("[...document.querySelectorAll('.grid [data-action=tvlive] .label')].map((x) => x.textContent)"),
                             ["Action", "Comedy", "Movies of the 2010s"])
        finally:
            self.backend.request("/settings", {"tvoff": []})


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class UiGermanTest(UiBase):
    """With language "de" the whole UI is German."""
    language = "de"

    def test_german_labels(self):
        self.assertEqual(self.texts(".row h2"), ["Weiterschauen", "Neu: Filme", "Neu: Serien"])
        self.assertEqual(self.b.js("$('[data-k=tab-search]').getAttribute('aria-label')"), "Suche")
        self.go_to_row("resume")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.assertEqual(self.texts(".actions .btn"), ["Fortsetzen ab 0:05", "Von vorne", "Gesehen-Status", "Meine Liste"])
        self.assertIn("Min.", self.b.js("document.querySelector('.hero').textContent"), "German duration format")

    def test_german_library_bar(self):
        self.open_library("Filme")  # the server's "Movies" in Matinee's language
        self.assertEqual(self.texts('[data-opt="sort"]'), ["A–Z", "Neu hinzugefügt", "Erscheinungsjahr", "Bewertung"])
        self.assertEqual(self.texts('[data-opt="unplayed"]'), ["Alle", "Ungesehen"])

    def test_all_strings_translated(self):
        missing = self.b.js("Object.keys(STRINGS.en).filter(k => !(k in STRINGS.de))")
        extra = self.b.js("Object.keys(STRINGS.de).filter(k => !(k in STRINGS.en))")
        self.assertEqual((missing, extra), ([], []), "both languages must have the same keys")


# What is wrong on a page at the TV's 1920×1080, as [kind, element, its box]: an icon without a size of its own
# (it fills the page), text cut off without "…", and something outside the screen that no key brings in
# (rows, grids and long pages move to the focus: outside is fine there while a tile out there can take it; the
# picture behind the top comes slowly closer and may reach past the edges)
LAYOUT_CHECK = r"""(() => {
  const W = innerWidth, H = innerHeight, out = [], seen = new Set();
  const off = (r) => r.right > W + 1 || r.left < -1 || r.bottom > H + 1 || r.top < -1;
  const name = (el) => el.tagName.toLowerCase() + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).join(".") : "")
    + (el.dataset.k ? `[data-k=${el.dataset.k}]` : "") + (el.children.length ? "" : ` "${el.textContent.trim().slice(0, 40)}"`);
  const reachable = (el) => {
    const box = el.closest(".strip, .rows-track, .grid-page, .moving, .np-queue-track, .detail, .epg");
    return box && [...box.querySelectorAll(".f")].some((f) => off(f.getBoundingClientRect()));
  };
  for (const el of document.querySelectorAll("#view *:not(#stage-art, #stage-art *)")) {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    if (s.display === "none" || s.visibility === "hidden" || r.width < 1 || r.height < 1) continue;
    const box = [r.left, r.top, r.right, r.bottom].map(Math.round);
    if (el.tagName === "svg" && (r.width > W / 4 || r.height > H / 4)) out.push(["huge icon", name(el), box]);
    if (!el.children.length && el.textContent.trim() && s.overflow !== "visible" && s.textOverflow !== "ellipsis"
        && s.webkitLineClamp === "none" && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) {
      out.push(["text cut off", name(el), box]);
    }
    // what one sees: text, pictures, icons and tiles (not the boxes around them, whose padding may reach out)
    const seeable = !el.children.length || el.matches("img, svg, .f") || [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (seeable && off(r) && !seen.has(el.parentElement) && !reachable(el)) out.push(["off the screen", name(el), box]);
    if (off(r)) seen.add(el);
  }
  return out;
})()"""


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiLayoutTest(UiBase):
    """Every page at the TV's size, in German (the longer words), with all services: nothing too big, cut off or out of reach."""
    language = "de"
    navidrome = True
    audiobookshelf = True
    section = None
    extra_env = {"KINO_OWN_MIN": "4"}  # one's own channels and their guide too

    PAGES = [("menu", {}), ("home", {}), ("item", {"id": "m1"}), ("item", {"id": "s1"}),
             ("library", {"id": "lm", "name": "Movies", "type": "movies"}), ("settings", {}), ("settings", {"part": "movies"}),
             ("settings", {"part": "navidrome"}), ("weatherplace", {}), ("bluetooth", {}),
             ("search", {}), ("search", {"q": "test"}), ("ownchannels", {}), ("guide", {}), ("music", {}), ("albums", {}),
             ("artists", {}), ("artist", {"id": "ar1"}), ("album", {"id": "al1"}), ("playlists", {}), ("playlist", {"id": "pl1"}),
             ("favorites", {}), ("radio", {}), ("musicsearch", {}),
             ("musicsearch", {"q": "alpha"}), ("nowplaying", {}), ("books", {}), ("booksearch", {}),
             ("booklib", {"id": "lib1", "name": "Stories"}), ("book", {"id": "bk10"})]

    def setUp(self):
        super().setUp()
        self.b.js("connect()")
        self.b.wait_js("USER", msg="signed in to Jellyfin")

    def tearDown(self):
        self.browser.tv_size()
        super().tearDown()

    def test_every_page_fits_the_screen(self):
        for name, args in self.PAGES:
            with self.subTest(page=name, **args):
                self.b.js(f"render({{name: {json.dumps(name)}, args: {json.dumps(args)}}})")
                self.b.wait_js(f"current.name === {json.dumps(name)} && !document.body.classList.contains('busy')", msg=name)
                time.sleep(0.8)  # the parts that come later (search results, the shelf, pictures)
                self.assertEqual(self.b.js(LAYOUT_CHECK), [])


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiSignedOutTest(UiBase):
    def test_signed_out_while_running(self):
        """Signed out by the server on a title's page: the sign-in in its place, then that page again, and Back goes
        where it went before (the page loaded anew for it, and the way back was gone)."""
        self.go_to_row("latest0")
        self.b.key("ArrowRight")
        before = self.focus()
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'", msg="a title's page")
        self.b.js("document.body.dataset.marker = 'same page'")
        self.mock.token = "revoked-and-replaced"
        self.b.js("refresh()")
        self.b.wait_js("$('#jf-msg')?.textContent.startsWith('Signed out by the server')",
                       timeout=10, msg="explains what to do")
        self.backend.set_password(PASSWORD)
        self.b.wait_js("USER && current.name === 'item' && $('.detail [data-action=play]')", timeout=15, msg="the title's page again")
        self.assertEqual(self.b.js("document.body.dataset.marker"), "same page", "not loaded anew")
        self.b.key("Escape")
        self.b.wait_js(f"current.name === 'home' && document.activeElement.dataset.k === '{before}'", msg="back on home, on the tile")


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiLegalTest(UiBase):
    section = None

    def test_licenses_and_privacy(self):
        self.b.js("navigate('settings', {part: 'system'})")
        self.b.wait_js("$('[data-k=set-legal]')", msg="among the system's settings")
        self.b.js("focusEl($('[data-k=set-legal]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'legal' && $('[data-k=legal-nothing]')", msg="the page")
        self.assertEqual(self.texts(".legal .settings-part"), ["Licenses", "What Matinee Connects To"])
        self.assertEqual(self.b.js("$('[data-k=legal-jellyfin] .sval').textContent"), "CC BY-SA 4.0")
        self.assertEqual(self.b.js("$('[data-k=legal-weather] .sval').textContent"), "Off", "no place chosen: no weather asked")
        self.b.js("focusEl($('[data-k=legal-jellyfin]'))")
        self.assertIn("not endorsed", self.b.js("document.activeElement.dataset.hint"), "what each means, beside it")

    def test_status(self):
        self.b.js("navigate('settings', {part: 'system'})")
        self.b.wait_js("$('[data-k=set-diagnose]')", msg="among the system's settings")
        self.b.js("focusEl($('[data-k=set-diagnose]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'diagnose' && $('[data-k=diag-jellyfin]')", timeout=10, msg="the page")
        self.assertEqual(self.b.js("$('[data-k=diag-jellyfin] .sname').textContent"), "Jellyfin")
        self.assertIn("version", self.b.js("$('[data-k=diag-jellyfin] .sval').textContent"), "it answers, with its version")
        self.assertTrue(self.b.js("!!$('[data-k=diag-jellyfin] .diag-dot.ok')"))
        self.assertTrue(self.b.js("document.activeElement.dataset.k?.startsWith('diag-')"), "the focus on a line")
        self.assertFalse(self.b.js("$('#view').textContent.includes('diag_')"), "every line named")


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiSupportTest(UiBase):
    section = None

    @classmethod
    def setUpClass(cls):
        from test_support import Inbox
        cls.inbox = Inbox()
        cls.extra_env = {"KINO_SUPPORT_URL": cls.inbox.url}
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.inbox.stop()

    def test_a_report_sent_from_the_settings(self):
        self.b.js("navigate('settings', {part: 'system'})")
        self.b.wait_js("$('[data-k=set-support]')", msg="among the system's settings")
        self.b.js("focusEl($('[data-k=set-support]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'support' && document.activeElement?.dataset.k === 'sup-send'", msg="what it holds, and Send")
        self.assertIn("no passwords", self.b.js("$('#sup-msg').textContent"))
        self.assertEqual(list((self.inbox.dir / "in").glob("*")) if (self.inbox.dir / "in").exists() else [], [], "nothing before Send")
        self.b.key("Enter")
        self.b.wait_js("$('#sup-code .transfer-code')", timeout=60, msg="sent, with its code")
        code = self.b.js("$('#sup-code .transfer-code').textContent")
        self.assertEqual(len(list((self.inbox.dir / "in").glob(f"*-{code.replace('-', '')}.tar.gz"))), 1, "filed under it")


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiHdmiTest(UiBase):
    """A streaming stick at another input of the TV: set up in the settings, switched to from its tile, and back."""
    section = None

    @classmethod
    def setUpClass(cls):
        from test_hdmi import fake_cec
        tmp = temp_dir("kino-hdmi-ui-")
        cls.cec_state = Path(tmp) / "cec.json"
        cls.extra_env = fake_cec(tmp)
        super().setUpClass()

    def cec(self):
        return json.loads(self.cec_state.read_text())

    def test_a_stick_set_up_and_switched_to(self):
        self.b.js("navigate('settings', {part: 'general'})")
        self.b.wait_js("$('[data-k=set-hdmi]')", msg="other devices among the settings")
        self.b.js("focusEl($('[data-k=set-hdmi]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'hdmi' && $('[data-k=hdmi-add]')", msg="their page")
        self.b.js("focusEl($('[data-k=hdmi-add]'))")
        self.b.key("Enter")
        self.assertEqual(self.texts("#options .opt"), ["HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4"])
        self.b.key("ArrowDown")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'svcentry' && current.args.svc === 'hdmi'", msg="its name asked for")
        self.b.type("Stick")
        self.b.js("focusEl($('[data-k=svc-done]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'hdmi' && $('[data-k=hdmi-0] .sname')?.textContent === 'Stick'", msg="kept")
        self.assertEqual(self.b.js("$('[data-k=hdmi-0] .sval').textContent"), "HDMI 2")
        try:
            self.b.js("navigate('menu', {})")
            self.b.wait_js("$('[data-k=menu-hdmi0]')", msg="its tile on the main menu")
            self.b.js("focusEl($('[data-k=menu-hdmi0]'))")
            self.b.wait_js("$('#shelf').textContent.includes('HDMI 2')", msg="what OK does, above it")
            self.b.key("Enter")
            self.b.wait_js("$('#toast').textContent.startsWith('Switched to Stick')", msg="switched")
            self.assertEqual(self.cec()["shown"], "2.0.0.0")
            self.b.key("ArrowLeft")
            wait_for(lambda: self.cec()["shown"] == "1.0.0.0", msg="a key on Matinee's remote brings the TV back")
            self.assertEqual(self.focus(), "menu-hdmi0", "and does nothing else")
        finally:
            self.backend.request("/hdmi/inputs", {"inputs": []})


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiNetworkTest(UiBase):
    """Wi-Fi from the settings, and the sign-in offering the Jellyfin servers found on the network."""
    section = "jellyfin"  # the sign-in is Jellyfin's

    @classmethod
    def setUpClass(cls):
        from test_network import NETWORKS, Discovery, fake_network
        tmp = temp_dir("kino-net-ui-")
        cls.nm_state = Path(tmp) / "nm.json"
        cls.discovery = Discovery([{"Address": "http://192.168.1.10:8096", "Id": "a1", "Name": "Living Room"}])
        networks = [*NETWORKS, {"ssid": "Office", "signal": 40, "security": "WPA2", "password": "office pass"}]
        cls.extra_env = {**fake_network(tmp, networks=networks, known=["Home"], active="Home"),
                         "KINO_JF_DISCOVER": cls.discovery.addr, "KINO_JF_DISCOVER_WAIT": "0.5"}
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.discovery.stop()

    def setUp(self):
        state = json.loads(self.nm_state.read_text())
        self.nm_state.write_text(json.dumps({**state, "known": ["Home"], "active": "Home"}))  # each test as it began
        super().setUp()

    def test_joining_a_network(self):
        self.b.js("navigate('settings', {part: 'general'})")
        self.b.wait_js("$('[data-k=set-wifi]')", msg="Wi-Fi among the settings")
        self.b.js("focusEl($('[data-k=set-wifi]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'wifi' && $('[data-k=\"wifi-Home\"]')", msg="the networks")
        self.assertIn("Connected", self.b.js("$('[data-k=\"wifi-Home\"] .chip-sub').textContent"))
        self.assertEqual(self.b.js("[...document.querySelectorAll('#wifilist .chip')].map((c) => c.dataset.ssid)"),
                         ["Home", "Café: Guests", "Office"], "the one in use first")
        self.b.js("focusEl($('[data-k=\"wifi-Café: Guests\"]'))")
        self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Connected to Café: Guests'", msg="an open one at once")
        self.b.js("focusEl($('[data-k=\"wifi-Office\"]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'svcentry' && current.args.svc === 'wifi'", msg="its password asked for")
        self.b.type("office pass")
        self.assertEqual(self.b.js("$('#query').textContent"), "•" * 11, "not shown")
        self.b.js("focusEl($('[data-k=svc-done]'))")
        self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Connected to Office'", msg="joined with it")
        self.b.wait_js("current.name === 'wifi' && $('[data-k=\"wifi-Office\"]')?.classList.contains('sel')", msg="in use now")
        self.b.js("focusEl($('[data-k=\"wifi-Office\"]'))")
        self.b.key("Enter")
        self.b.wait_js("!$('#options').hidden", msg="what can be done with the one in use")
        self.assertEqual(self.texts("#options .opt"), ["Forget This Network"])
        self.b.key("Enter")
        self.b.wait_js("$('#toast').textContent === 'Office forgotten'", msg="forgotten")
        self.assertNotIn("Office", json.loads(self.nm_state.read_text())["known"])

    def test_the_sign_in_offers_the_servers_found(self):
        self.go_to_row("latest0")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'", msg="a title's page")
        self.mock.token = "revoked-and-replaced"  # signed out by the server: the sign-in in its place
        try:
            self.b.js("refresh()")
            self.b.wait_js("$('#jf-msg')", timeout=15, msg="the sign-in")
            self.b.wait_js("$('[data-k=jf-found-0]')", timeout=10, msg="a server found on the network")
            self.assertEqual(self.texts("#jf-found .sname"), ["Living Room", "Wi-Fi"])
            self.assertIn("Home", self.b.js("$('[data-k=jf-wifi] .sval').textContent"), "and the network in use")
            self.b.js("focusEl($('[data-k=jf-found-0]'))")
            self.b.key("Enter")
            self.b.wait_js("svcDraft.jellyfin.server === 'http://192.168.1.10:8096'", msg="its address put in")
            self.b.wait_js("$('[data-k=jf-server] .sval')?.textContent === 'http://192.168.1.10:8096'", msg="shown")
        finally:
            self.b.js("svcDraft.jellyfin = {}")
            self.backend.set_password(PASSWORD)
            self.b.wait_js("USER && current.name === 'item' && !$('#jf-msg')", timeout=15, msg="signed in again")


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiAccountTest(UiBase):
    section = None

    @classmethod
    def setUpClass(cls):
        from test_account import fake_tailscale
        cls.port = str(free_port())
        cls.extra_env = {"KINO_TAILSCALE": fake_tailscale(temp_dir("kino-ts-")), "KINO_TRANSFER_PORT": cls.port}
        super().setUpClass()

    def open_account(self):
        self.b.js("navigate('settings', { part: 'account' })")
        self.b.wait_js("current.name === 'settings' && $('[data-k=acc-out]')", msg="the account's settings")

    def test_move_to_another_device(self):
        self.open_account()
        # the devices synced with (the sync may have told of this one already) left out
        self.assertEqual(self.texts(".srow:not([data-k^=acc-dev-]) .sname"), ["Same on Every Device", "Move to Another Device",
                                                      "Take Over from Another Device", "Sign In Everywhere", "Sign Out and Reset"])
        self.assertEqual(self.texts(".settings-part")[:1], [f"Signed In as {USERNAME}"])
        self.b.js("focusEl($('[data-k=acc-out]'))")
        self.b.key("Enter")
        self.b.wait_js("$('#tr-msg')?.textContent.startsWith('No device is waiting')", msg="nobody waiting yet")
        other = Backend("", password=None, env=dict(self.extra_env), conf={"device_name": "Den TV"}).start(wait_ready=False)
        try:
            code = other.request("/transfer/wait", {})[1]["code"]
            self.assertEqual(self.focus(), "tr-again")
            self.b.key("Enter")
            self.b.wait_js("$('[data-k=tr-0]')", msg="found")
            self.assertEqual(self.b.js("$('[data-k=tr-0] .sname').textContent"), "Den TV")
            self.assertEqual(self.b.js("$('#tr-msg').textContent"), "Choose the device that shows the code.")
            self.b.js("focusEl($('[data-k=tr-0]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'svcentry' && current.args.field === 'code' && $('[data-k=svc-done]')", msg="its code")
            self.b.type(code, pause=0.03)
            self.b.js("focusEl($('[data-k=svc-done]'))")
            self.b.key("Enter")
            self.b.wait_js("current.name === 'svcentry' && current.args.field === 'password' && $('[data-k=svc-done]')",
                           msg="then Jellyfin's password")
            self.assertIn("not stored", self.b.js("$('#results .hint').textContent"))
            self.b.js(f"current.args.q = {json.dumps(PASSWORD)}; focusEl($('[data-k=svc-done]'))")
            self.b.key("Enter")
            self.b.wait_js(f"$('#toast').textContent === {json.dumps('Moved to Den TV.')}", msg="sent")
            wait_for(lambda: "Account taken over from" in other.logtext(), msg="taken over there")
        finally:
            other.stop()

    def test_take_over_waits_with_a_code(self):
        self.b.js("navigate('transferin', {})")
        self.b.wait_js("$('.transfer-code')", msg="the code")
        code = self.b.js("$('.transfer-code').textContent").replace(" ", "")
        self.assertRegex(code, r"^\d{6}$")
        self.assertIn("Settings → Account → Move to Another Device", self.b.js("$('.jf-msg').textContent"))
        self.assertTrue(self.backend.request("/transfer/state", {})[1]["waiting"])
        self.b.key("Escape")
        wait_for(lambda: not self.backend.request("/transfer/state", {})[1]["waiting"], msg="Back stops waiting")

    def test_sign_out_asks_first(self):
        self.open_account()
        self.b.js("focusEl($('[data-k=acc-signout]'))")
        self.b.key("Enter")
        self.assertEqual(self.b.js("$('#options h2').textContent"), "Remove everything of yours from this device?")
        self.assertEqual(self.texts("#options .opt"), ["Sign Out and Reset", "Cancel"])
        self.b.key("Escape")
        self.assertTrue((self.backend.conf / "config.json").exists())
        self.assertIn("server", json.loads((self.backend.conf / "config.json").read_text()))


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiStartupTest(unittest.TestCase):
    def test_explains_why_it_cannot_connect(self):
        mock = MockJellyfin().start()
        backend = Backend(mock.url, password="wrong").start(wait_ready=False)
        browser = Chromium(backend.url)
        try:
            browser.wait_js("document.activeElement?.dataset.k === 'menu-next'", msg="main menu, without Jellyfin")
            browser.js("focusEl($('[data-k=menu-jellyfin]'))")
            browser.key("Enter")
            browser.wait_js("$('#jf-msg')?.textContent.startsWith('Sign-in rejected')", msg="hint about the wrong password")
            self.assertEqual(browser.js("document.activeElement?.dataset.k"), "jf-server", "on the sign-in, to put it right")
            browser.key("Escape")
            browser.wait_js("current.name === 'menu'", msg="back to the menu while waiting")
        finally:
            browser.close()
            backend.stop()
            mock.stop()

    def test_fresh_install_signs_in_on_the_tv(self):
        mock = MockJellyfin().start()
        backend = Backend(mock.url)
        (backend.conf / "config.json").write_text('{"welcomed": true}')  # no server: the first start's steps left for later
        backend.start(wait_ready=False)
        browser = Chromium(backend.url)
        try:
            browser.wait_js("document.activeElement?.dataset.k === 'menu-next'", msg="main menu, without Jellyfin")
            browser.js("focusEl($('[data-k=menu-jellyfin]'))")
            browser.wait_js("$('#shelf .hint')?.textContent.startsWith('Not set up yet')", msg="its tile says what OK does")
            browser.key("Enter")
            browser.wait_js("$('#jf-msg')?.textContent.startsWith('Welcome to Matinee')", msg="the sign-in")
            self.assertEqual(browser.js("$('[data-k=jf-transfer] .sname').textContent"), "Take Over from Another Device",
                             "or everything from another device")
            for field, text in (("server", mock.url), ("username", USERNAME), ("password", PASSWORD)):
                browser.js(f"focusEl($('[data-k=jf-{field}]'))")
                browser.key("Enter")
                browser.wait_js(f"current.name === 'svcentry' && current.args.field === '{field}'", msg=f"typing the {field}")
                browser.type(text, pause=0.03)
                browser.js("focusEl($('[data-k=svc-done]'))")
                browser.key("Enter")
                browser.wait_js(f"document.activeElement?.dataset.k === 'jf-{field}'", msg="back on that line")
            browser.key("ArrowDown")
            self.assertEqual(browser.js("document.activeElement?.dataset.k"), "jf-signin")
            browser.key("Enter")
            browser.wait_js("current.name === 'home' && document.querySelector('.rows .card, .row .card')", msg="Jellyfin's start")
            self.assertEqual(backend.request("/session")[1]["userId"], "u1")
        finally:
            browser.close()
            backend.stop()
            mock.stop()


    def test_the_first_start_s_steps(self):
        from test_network import Discovery
        mock = MockJellyfin().start()
        discovery = Discovery([{"Address": mock.url, "Id": "a1", "Name": "Living Room"}])
        backend = Backend(mock.url, env={"KINO_JF_DISCOVER": discovery.addr, "KINO_JF_DISCOVER_WAIT": "0.5",
                                         "KINO_NET_CHECK_HOST": "localhost"})
        (backend.conf / "config.json").unlink()
        backend.start(wait_ready=False)
        browser = Chromium(backend.url)
        try:
            browser.wait_js("current.name === 'welcome' && document.activeElement?.dataset.k === 'welcome-en'", msg="the language first")
            top = browser.js("$('.welcome-steps').getBoundingClientRect().top")
            self.assertGreater(top, 20, "the steps whole on the screen, below its top edge")
            browser.key("ArrowUp")
            browser.key("Enter")  # Deutsch: the page again in it, on the next step
            browser.wait_js("current.name === 'welcome' && current.args.step === 'net' && $('.welcome h1').textContent === 'Netzwerk'",
                            timeout=15, msg="the network, in German")
            self.assertEqual(browser.js("$('#jf-msg').textContent"), "Mit dem Internet verbunden.")
            self.assertEqual(json.loads((backend.conf / "config.json").read_text())["language"], "de")
            browser.js("focusEl($('[data-k=welcome-next]'))")
            browser.key("Enter")
            browser.wait_js("current.args.step === 'jellyfin' && $('[data-k=jf-found-0]')", msg="Jellyfin, with the server found")
            browser.js("focusEl($('[data-k=jf-found-0]'))")
            browser.key("Enter")
            for field, text in (("username", USERNAME), ("password", PASSWORD)):
                browser.wait_js(f"$('[data-k=jf-{field}]')", msg=f"the {field}'s line")
                browser.js(f"focusEl($('[data-k=jf-{field}]'))")
                browser.key("Enter")
                browser.wait_js(f"current.name === 'svcentry' && current.args.field === '{field}'", msg=f"typing the {field}")
                browser.type(text, pause=0.03)
                browser.js("focusEl($('[data-k=svc-done]'))")
                browser.key("Enter")
                browser.wait_js(f"current.name === 'welcome' && document.activeElement?.dataset.k === 'jf-{field}'", msg="back on that line")
            browser.js("focusEl($('[data-k=jf-signin]'))")
            browser.key("Enter")
            browser.wait_js("current.args.step === 'jellyfin' && !$('[data-k=jf-signin]') && $('[data-k=welcome-next]')", timeout=15, msg="signed in")
            self.assertEqual(backend.request("/session")[1]["userId"], "u1")
            browser.js("focusEl($('[data-k=welcome-next]'))")
            browser.key("Enter")
            browser.wait_js("current.args.step === 'done' && document.activeElement?.dataset.k === 'welcome-start'",
                            msg="no Bluetooth here: no remote to pair, ready")
            browser.key("Enter")
            browser.wait_js("current.name === 'menu' && $('[data-k=menu-jellyfin]')", msg="the main menu")
            self.assertFalse(backend.request("/start")[1]["welcome"], "not again")
        finally:
            browser.close()
            backend.stop()
            mock.stop()
            discovery.stop()


    def test_jellyfin_set_up_on_this_device(self):
        mock = MockJellyfin().start()
        mock.fresh = True
        media = Path(temp_dir("kino-media-ui-"))
        backend = Backend("", password=None, env={"KINO_LOCAL_JELLYFIN": mock.url, "KINO_MEDIA": str(media),
                                                  "KINO_NET_CHECK_HOST": "localhost", "KINO_JF_DISCOVER": "127.0.0.1:9"})
        (backend.conf / "share.json").write_text(json.dumps({"name": "Matinee", "user": "anna", "password": "ab12-cd34-ef56"}))
        backend.start(wait_ready=False)
        browser = Chromium(backend.url)
        try:
            browser.wait_js("current.name === 'welcome' && document.activeElement?.dataset.k === 'welcome-en'", msg="the first start")
            browser.key("Enter")
            browser.wait_js("current.args?.step === 'net' && $('[data-k=welcome-next]')", msg="the network")
            browser.js("focusEl($('[data-k=welcome-next]'))")
            browser.key("Enter")
            browser.wait_js("current.args?.step === 'jellyfin' && $('[data-k=local-create]')", msg="Jellyfin on this device offered")
            for field, text in (("username", "anna"), ("password", "geheim1")):
                browser.js(f"focusEl($('[data-k=local-{field}]'))")
                browser.key("Enter")
                browser.wait_js(f"current.name === 'svcentry' && current.args.svc === 'local' && current.args.field === '{field}'", msg=f"its {field}")
                browser.type(text, pause=0.03)
                browser.js("focusEl($('[data-k=svc-done]'))")
                browser.key("Enter")
                browser.wait_js(f"current.name === 'welcome' && $('[data-k=local-create]')", msg="back")
            browser.js("focusEl($('[data-k=local-create]'))")
            browser.key("Enter")
            browser.wait_js("current.args?.step === 'jellyfin' && !$('[data-k=local-create]') && $('[data-k=welcome-next]')", timeout=20, msg="set up and signed in")
            self.assertEqual([f[0] for f in mock.folders], ["Movies", "Shows"])
            self.assertEqual(json.loads((backend.conf / "config.json").read_text())["username"], "anna")
            browser.js("navigate('share')")
            browser.wait_js("current.name === 'share' && $('[data-k=share-password]')", msg="how films get onto it")
            self.assertEqual(browser.js("$('[data-k=share-password] .sval').textContent"), "ab12-cd34-ef56")
            self.assertRegex(browser.js("$('[data-k=share-win] .sval').textContent"), r"^\\\\[^\\]+\\Matinee$")
        finally:
            browser.close()
            backend.stop()
            mock.stop()

@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class UiPodcastsTest(UiBase):
    """Podcasts from Podfetch: the dock's tile once it is set up, its start, a podcast and an episode playing."""
    section = None
    with_player = True
    podfetch = True

    def setUp(self):
        self.pf.heard.clear()
        self.pf.heard["e-22222222"] = 2  # begun
        super().setUp()
        self.backend.request("/music/control", {"cmd": "stop"})
        self.b.js("musicPoll()")

    def tearDown(self):
        self.backend.request("/music/control", {"cmd": "stop"})
        super().tearDown()

    def test_shelf_says_when_a_sign_in_is_needed(self):
        from mockpf import PF_USER
        self.pf.refused.add(PF_USER)  # the password changed on the server
        try:
            self.b.js("shelfCache.clear(); render({name: 'menu'}, 'menu-podcasts')")
            self.b.wait_js("$('#shelf .hint')?.textContent.includes('sign in again')", msg="the shelf says so")
            self.assertEqual(self.b.js("$('#shelf .hint').textContent"), "Podcasts needs you to sign in again. OK opens the sign-in.")
            self.b.key("Enter")
            self.b.wait_js("$('[data-k=svc-connect]')", msg="the sign-in, with OK on the tile")
        finally:
            self.pf.refused.clear()
            self.b.js("shelfCache.clear(); pfState = null")

    def test_dock_of_seven_on_the_screen(self):
        self.b.js("render({name: 'menu'})")
        self.b.wait_js("$('.menu .tiles .card')", msg="the main menu")
        self.assertEqual(self.b.js("document.querySelectorAll('.menu .tiles .card').length"), 7, "every service and the search")
        box = self.b.js("(() => { const r = $('.menu .tiles').getBoundingClientRect(); return [r.left, r.right, innerWidth]; })()")
        self.assertGreater(box[0], 0, "nothing cut off at the left")
        self.assertLess(box[1], box[2], "nor at the right: the settings in view")

    def test_a_slow_podfetch_does_not_hold_up_the_main_menu(self):
        self.pf.slow = 4
        try:
            self.b.js("sessionStorage.clear(); location.reload()")
            start = time.monotonic()
            self.b.wait_js("document.querySelectorAll('.menu .tiles .card').length === 7", timeout=8, msg="the main menu")
            self.assertLess(time.monotonic() - start, 2.5, "there before Podfetch has answered")
            self.assertIn("Podcasts", self.texts(".menu .tiles .label"), "its tile too: it is set up")
        finally:
            self.pf.slow = 0

    def test_tile_start_and_a_podcast(self):
        self.assertEqual(self.texts(".menu .tiles .label"),
                         ["Up Next", "Movies & Shows", "Music", "Audiobooks", "Podcasts", "Search", "Settings"])
        self.assertEqual(self.b.js("$('.menu .tiles').dataset.n"), "7")
        self.b.js("focusEl($('[data-k=menu-podcasts]'))")
        self.b.wait_js("$('#shelf').dataset.for === 'podcasts' && $('#shelf .card.sq')", msg="podcasts on the shelf")
        self.assertEqual(self.texts("#shelf h2"), ["Continue Listening"])
        self.assertEqual(self.texts("#shelf .label"), ["Episode 1: First"])
        self.assertTrue(self.b.js("!!$('#shelf .card .progress')"), "how far it was heard")
        # on the shelf, an episode shows its podcast's cover large above, as an album or a book does (what the dock's
        # tile put there taken away first: the same cover, it would hide a gap); behind, the menu's picture stays
        self.b.js("$('#stage-art').removeAttribute('data-url'); $('#stage-art').replaceChildren(); bgUrl = 'menu'")
        self.b.key("ArrowUp")
        self.b.wait_js("$('#stage-art .cover-art.on img')?.src.includes('/pf/cover/p1')", msg="the podcast's cover large above")
        self.assertEqual(self.b.js("bgUrl"), "menu", "not blurred behind: the menu's picture stays")
        self.b.key("Escape")
        self.assertEqual(self.focus(), "menu-podcasts")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'podcasts' && $('.music-home .card')", msg="the podcasts' start")
        self.assertEqual(self.texts(".music-home .row h2"), ["Continue Listening", "New Episodes", "Podcasts"])
        self.assertEqual(self.texts('[data-k^="pod-"] .label'), ["Zeitfragen", "Another Show"], "the one heard first")
        self.assertEqual(self.b.js("$('.tabbar .tab.sel').textContent"), "Podcasts")
        self.b.js("focusEl($('[data-k=\"pod-0\"]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'podcast' && $('.podcast-detail h1')", msg="the podcast")
        self.assertEqual(self.b.js("$('.podcast-detail h1').textContent"), "Zeitfragen")
        self.assertIn("2 episodes", self.b.js("$('.podcast-detail .meta').textContent"))
        self.assertEqual(self.texts(".podcast-detail .actions .btn"), ["Continue Listening", "Newest Episode", "Stop Following"])
        self.assertIn("News, Society & Culture", self.b.js("$('.podcast-detail .meta').textContent"), "its categories")
        self.assertEqual(self.texts(".podcast-detail .track .t"), ["Episode 2: Later", "Episode 1: First"], "the newest first")
        self.assertEqual(self.texts(".podcast-detail .track .no"), ["October 20", "October 1"])
        # a day with its year is wider: its column grows, no title runs into it, all start at the same edge
        self.b.js("$('.podcast-detail .track .no').textContent = 'September 16, 2025 (and more)'")
        edges = self.b.js("""[...document.querySelectorAll('.podcast-detail .track')].map((r) =>
            [r.querySelector('.no').getBoundingClientRect().right, r.querySelector('.t').getBoundingClientRect().left,
             r.querySelector('.t').getBoundingClientRect().right, r.getBoundingClientRect().right])""")
        self.assertTrue(all(no < title for no, title, _, _ in edges), edges)
        self.assertEqual(len({round(title) for _, title, _, _ in edges}), 1, edges)
        self.assertTrue(all(right <= row for _, _, right, row in edges), "titles stay inside their rows")
        self.assertEqual(self.focus(), "presume")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'nowplaying' && musicState.queue?.[0]?.podcast === 'p1'", msg="now playing")
        self.assertEqual(self.b.js("$('.np-title').textContent"), "Episode 1: First")
        self.b.wait_js("musicState.pos >= 2", msg="from where it was left")

    def test_the_start_at_once_then_as_it_is_now(self):
        """Opened again, the start shows what it showed last at once, however slow Podfetch is, and puts in what
        changed meanwhile (heard to the end elsewhere) when the answer is there."""
        rows = lambda: self.b.js("[...document.querySelectorAll('.rows h2')].map((h) => h.textContent)")
        self.b.js("render({name: 'podcasts'})")
        self.b.wait_js("current.name === 'podcasts' && document.querySelectorAll('.rows .card').length > 0", msg="the start")
        self.assertIn("Continue Listening", rows())
        self.pf.heard.clear()  # heard to the end on another device meanwhile
        self.pf.slow = 2
        try:
            self.b.js("render({name: 'menu'})")
            self.b.wait_js("current.name === 'menu' && $('.menu .tiles .card')", msg="the main menu")
            took = self.b.js("(async () => { const t = performance.now(); await render({name: 'podcasts'}); return performance.now() - t; })()")
            self.assertLess(took, 1000, "not waiting for Podfetch")
            self.assertIn("Continue Listening", rows(), "as it was shown last")
            self.b.wait_js("current.name === 'podcasts' && ![...document.querySelectorAll('.rows h2')].some((h) => h.textContent === 'Continue Listening')",
                           timeout=10, msg="as it is now, once Podfetch answered")
        finally:
            self.pf.slow = 0

    def test_categories(self):
        self.b.js("navigate('podcasts')")
        self.b.wait_js("current.name === 'podcasts' && $('.music-home .card')", msg="the podcasts' start")
        self.assertEqual(self.texts(".music-home .libs .chip"), ["All Podcasts"], "the categories on its page, as a library's genres")
        self.b.js("focusEl($('[data-k=pcat-all]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'podcat' && $('.grid .card')", msg="all podcasts")
        self.assertEqual(self.texts(".bar .chip"), ["All Podcasts", "News", "Society & Culture", "Technology"],
                         "the one with the most podcasts first")
        self.b.js("focusEl($('[data-k=pc-2]'))")
        self.b.key("Enter")
        self.b.wait_js("$('.grid-page h1').textContent === 'Technology 1'", msg="a category")
        self.assertEqual(self.texts(".grid .card .label"), ["Another Show"])
        self.assertEqual(self.texts(".bar .chip.sel"), ["Technology"])
        self.b.js("focusEl($('[data-k=pc-all]'))")
        self.b.key("Enter")
        self.b.wait_js("$('.grid-page h1').textContent === 'All Podcasts 2'", msg="all of them")
        self.assertEqual(self.focus(), "pc-all", "the focus stays on the chip")
        self.assertEqual(self.b.js("['News', 'Society & Culture', 'Technology', 'True Crime'].map((c) => POD_CATEGORIES.de[c] ?? c)"),
                         ["Nachrichten", "Gesellschaft & Kultur", "Technik", "True Crime"], "in German, where it has a name")

    def test_the_one_heard_last_first(self):
        self.b.js("navigate('podcasts')")
        self.b.wait_js("current.name === 'podcasts' && $('[data-k=\"pod-1\"]')", msg="the podcasts' start")
        self.assertEqual(self.texts('[data-k^="pod-"] .label'), ["Zeitfragen", "Another Show"], "the one heard first, as Recently Played")
        self.pf.heard["e-33333333"] = 5  # now Another Show
        self.b.js("navigate('podcat', { cat: '' })")
        self.b.wait_js("current.name === 'podcat' && $('.grid .card')", msg="all podcasts")
        self.assertEqual(self.texts(".grid .card .label"), ["Another Show", "Zeitfragen"], "by name, to be found")
        self.assertEqual(self.focus(), "pod-p2", "on the one heard last")
        self.b.js("navigate('podcat', { cat: 'Society & Culture' })")
        self.b.wait_js("$('.grid-page h1').textContent.startsWith('Society')", msg="a category without it")
        self.assertEqual(self.focus(), "pod-p1", "there the one heard before")

    def test_follow_from_the_search(self):
        podcasts = list(self.pf.podcasts)
        try:
            self.b.js("navigate('podsearch', { q: 'zeit' })")
            self.b.wait_js("$('[data-k=\"new-222\"]')", msg="new ones to follow")
            self.assertEqual(self.texts("#results .results-title"), ["Podcasts", "New Podcasts to Follow"], "each kind under its heading")
            self.assertEqual(self.texts("#results .label"), ["Zeitfragen", "Zeitgeist Weekly"], "one followed already: not twice")
            self.b.js("focusEl($('[data-k=\"new-222\"]'))")
            self.b.key("Enter")
            self.assertEqual(self.b.js("$('#options h2').textContent"), "Zeitgeist Weekly")
            self.assertEqual(self.texts("#options .opt"), ["Follow", "Cancel"])
            self.b.key("Enter")
            self.b.wait_js("current.name === 'podcast' && $('.podcast-detail h1')?.textContent === 'Zeitgeist Weekly'", msg="its page")
            self.assertEqual(self.pf.followed, [222])
            self.assertIn("fetches them in the background", self.b.js("$('.podcast-detail .hint').textContent"), "no episodes yet")
        finally:
            self.pf.podcasts, self.pf.followed = podcasts, []

    def test_episode_options(self):
        """OK held on an episode: to its show, heard or not, from the start."""
        from mockpf import EPISODE_SECONDS
        self.b.js("navigate('podcasts')")
        self.b.wait_js("current.name === 'podcasts' && $('[data-k=listen-0]')", msg="the podcasts' start")
        self.b.js("focusEl($('[data-k=listen-0]'))")
        self.b.key("Enter", hold_ms=900)
        self.assertEqual(self.b.js("$('#options h2').textContent"), "Episode 1: First")
        self.assertEqual(self.texts("#options .opt")[:4], ["Go to Show", "Mark as Heard", "Mark as Not Heard", "Start Over"],
                         "begun: either, as a movie")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'podcast' && current.args.id === 'p1' && $('[data-k=ep-0]')", msg="its show")
        self.b.js("focusEl($('[data-k=ep-0]'))")
        self.b.key("Enter", hold_ms=900)
        self.assertEqual(self.texts("#options .opt")[0], "Mark as Heard", "on its show's page no way to it")
        self.assertNotIn("Start Over", self.texts("#options .opt"), "not begun")
        self.assertNotIn("Mark as Not Heard", self.texts("#options .opt"), "nothing to undo")
        self.b.key("Enter")
        wait_for(lambda: self.pf.heard.get("e-11111111") == EPISODE_SECONDS, msg="Podfetch told its whole length")
        self.b.wait_js("$('[data-k=ep-0]').classList.contains('heard')", msg="dimmed as heard")
        self.assertEqual(self.focus(), "ep-0", "the focus stays on it")
        self.b.key("Enter", hold_ms=900)
        self.assertEqual(self.texts("#options .opt")[0], "Mark as Not Heard")
        self.b.key("Enter")
        wait_for(lambda: self.pf.heard.get("e-11111111") == 0, msg="from the start again")

    def test_unfollow(self):
        podcasts, episodes = list(self.pf.podcasts), list(self.pf.episodes)
        try:
            self.b.js("navigate('podcasts')")
            self.b.wait_js("current.name === 'podcasts' && $('.music-home .card')", msg="the podcasts' start")
            self.b.js("focusEl($('[data-k=\"pod-1\"]'))")  # Another Show, after the one heard
            self.b.key("Enter")
            self.b.wait_js("current.name === 'podcast' && $('[data-k=punfollow]')", msg="the podcast")
            self.b.js("focusEl($('[data-k=punfollow]'))")
            self.b.key("Enter")
            self.assertEqual(self.b.js("$('#options h2').textContent"),
                             "Stop following “Another Show”? Podfetch forgets the podcast, its episodes and where you were in them.")
            self.assertEqual(self.texts("#options .opt"), ["Stop Following, Keep the Downloads", "Stop Following and Delete the Downloads", "Cancel"])
            self.b.key("ArrowDown")
            self.b.key("ArrowDown")
            self.b.key("Enter")
            self.assertEqual(self.pf.deleted, [], "Cancel: nothing")
            self.b.key("Enter")
            self.b.key("Enter")  # the first: the downloads kept
            self.b.wait_js("current.name === 'podcasts'", msg="back on the start")
            self.assertEqual(self.pf.deleted, [("p2", False)])
            self.assertEqual(self.b.js("$('#toast').textContent"), "No longer following Another Show")
            self.b.wait_js("$('[data-k^=\"pod-\"]')", msg="its podcasts")
            self.assertEqual(self.texts('[data-k^="pod-"] .label'), ["Zeitfragen"], "without it")
        finally:
            self.pf.podcasts, self.pf.episodes, self.pf.deleted = podcasts, episodes, []

    def test_unfollow_from_its_tile(self):
        podcasts, episodes = list(self.pf.podcasts), list(self.pf.episodes)
        try:
            self.b.js("navigate('podcasts')")
            self.b.wait_js("current.name === 'podcasts' && $('[data-k=\"pod-1\"]')", msg="the podcasts' start")
            self.b.js("focusEl($('[data-k=\"pod-1\"]'))")  # Another Show, after the one heard
            self.b.key("Enter", hold_ms=900)
            self.assertEqual(self.texts("#options .opt")[0], "Stop Following", "its first option")
            self.b.key("Enter")
            self.b.wait_js("$('#options h2')?.textContent.startsWith('Stop following “Another Show”?')", msg="asked first")
            self.b.key("Enter")
            wait_for(lambda: self.pf.deleted == [("p2", False)], msg="unfollowed, the downloads kept")
            self.b.wait_js("current.name === 'podcasts' && $('[data-k^=\"pod-\"]')", msg="the start stays")
            self.assertEqual(self.texts('[data-k^="pod-"] .label'), ["Zeitfragen"], "without it")
        finally:
            self.pf.podcasts, self.pf.episodes, self.pf.deleted = podcasts, episodes, []

    def test_search_and_settings(self):
        self.b.js("focusEl($('[data-k=menu-podcasts]'))")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'podcasts' && $('.music-home .card')", msg="the podcasts' start")
        self.b.js("navigate('podsearch', { q: 'zeit' })")
        self.b.wait_js("current.name === 'podsearch' && $('#results .card')", msg="found")
        self.assertEqual(self.texts("#results .label"), ["Zeitfragen"])
        self.b.js("navigate('settings', { part: 'podcasts' })")
        self.b.wait_js("current.name === 'settings' && $('[data-k=svc-password]')", msg="Podfetch's settings")
        self.assertEqual(self.texts(".settings .slist .sname")[:4], ["Server", "User", "Password", "Sign In"])
        self.assertEqual(self.b.js("$('[data-k=svc-connect] .sval').textContent"), "Connected")


# Every page walked with the four arrows from where its focus starts, as a remote does: what cannot be reached, where
# the focus is lost, and where Left or Right leaves its row for another (a row's end stays put; only Left from a row's
# first one goes up to the page's bar)
FOCUS_CRAWL = r"""
(() => {
  const key = (el) => el?.dataset?.k;
  const row = (x) => x?.closest(".strip, .chips, .actions, .grid, .tabbar, .tiles, .slist, .keys, .search-left");
  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const start = document.activeElement;
  const seen = new Set(), queue = [start], lost = [], jumps = [], oneway = [];
  while (queue.length && seen.size < 300) {
    const el = queue.shift(), k = key(el);
    if (!k || seen.has(k) || !el.isConnected) continue;
    seen.add(k);
    for (const d of ["up", "down", "left", "right"]) {
      if (!el.isConnected) break;
      focusEl(el, false);
      move(d);
      const to = document.activeElement;
      if (!to?.classList?.contains("f")) { lost.push(`${k} ${d}`); continue; }
      // (between the letters and the results beside them is a way of its own: a search's two halves)
      const halves = (a, b) => a.closest(".search-left") && b.closest("#results");
      if ((d === "left" || d === "right") && row(el) && row(to) !== row(el) && !(d === "left" && to.closest(".tabbar"))
          && !halves(el, to) && !halves(to, el))
        jumps.push(`${k} -${d}-> ${key(to)}`);
      // and back the way it came: the opposite arrow returns to where it was (a page's own map, not a guess anew)
      // (left from a row's start up into the bar is a way of its own: on from there, right goes along the bar)
      if (to !== el && key(to) && !(d === "left" && to.closest(".tabbar") && !el.closest(".tabbar"))) {
        move({ up: "down", down: "up", left: "right", right: "left" }[d]);
        if (document.activeElement !== el) oneway.push(`${k} -${d}-> ${key(to)} -back-> ${key(document.activeElement)}`);
      }
      if (key(to) && !seen.has(key(to))) queue.push(to);
    }
  }
  const all = [...view.querySelectorAll(".f")].filter((e) => key(e) && visible(e)).map(key);
  focusEl(start, false);
  return { reached: seen.size, unreached: all.filter((k) => !seen.has(k)), lost, jumps, oneway };
})()
"""


@unittest.skipUnless(HAVE_CHROMIUM, "needs chromium")
class UiFocusTest(UiBase):
    """The remote's arrows on every page: each thing on it reachable, the focus never lost, no row left sideways, and
    the opposite arrow back where it came from."""
    section = None
    navidrome = True
    audiobookshelf = True
    podfetch = True
    extra_env = {"KINO_OWN_MIN": "4"}  # one's own channels

    PAGES = [
        ("main menu", "mainMenu()", "current.name === 'menu' && $('#shelf .f')"),
        ("Jellyfin start", "navigate('home')", "current.name === 'home' && $('.home .card')"),
        ("library", "navigate('library', { id: 'lm', name: 'Movies' })", "current.name === 'library' && $('.grid .card')"),
        ("movie", "navigate('item', { id: 'm2' })", "current.name === 'item' && $('.actions .f')"),
        ("show", "navigate('item', { id: 's1' })", "current.name === 'item' && $('[data-k^=ep-]')"),
        ("settings", "navigate('settings', {})", "current.name === 'settings' && $('.srow')"),
        ("settings, video and audio", "navigate('settings', { part: 'av' })", "current.name === 'settings' && $('.srow')"),
        ("search", "navigate('allsearch', { q: '' })", "current.name === 'allsearch' && $('[data-k=key-a]')"),
        ("music start", "navigate('music')", "current.name === 'music' && $('.music-home .card')"),
        ("audiobooks start", "navigate('books')", "current.name === 'books' && $('.music-home .card')"),
        ("one's own channels", "navigate('ownchannels')", "current.name === 'ownchannels' && $('.grid .card')"),
        ("podcasts", "navigate('podcasts')", "current.name === 'podcasts' && $('#view .f')"),
        ("controls", "navigate('controls')", "current.name === 'controls' && $('#view .f')"),
        ("Bluetooth", "navigate('bluetooth', {})", "current.name === 'bluetooth' && $('#view .f')"),
        *((f"settings, {part}", f"navigate('settings', {{ part: '{part}' }})", "current.name === 'settings' && $('.srow')")
          for part in ("basics", "subs", "saver", "movies", "navidrome", "books", "podcasts", "account", "system", "update")),
        ("album", "navigate('album', { id: 'al1' })", "current.name === 'album' && $('#view .f')"),
        ("artist", "navigate('artist', { id: 'ar1' })", "current.name === 'artist' && $('#view .f')"),
        ("playlist", "navigate('playlist', { id: 'pl1' })", "current.name === 'playlist' && $('#view .f')"),
        ("audiobooks' library", "navigate('booklib', { id: 'lib1', name: 'Stories' })", "current.name === 'booklib' && $('#view .card')"),
        ("audiobook", "navigate('book', { id: 'bk1' })", "current.name === 'book' && $('#view .f')"),
        ("podcast", "navigate('podcast', { id: 'p1' })", "current.name === 'podcast' && $('[data-k^=ep-]')"),
        ("podcasts of a kind", "navigate('podcat', { cat: '' })", "current.name === 'podcat' && $('#view .f')"),
        ("legal", "navigate('legal')", "current.name === 'legal' && $('#view .f')"),
        ("status", "navigate('diagnose')", "current.name === 'diagnose' && $('#view .f')"),
        ("support", "navigate('support')", "current.name === 'support' && $('#view .f')"),
        ("check library", "navigate('libcheck', {})", "current.name === 'libcheck' && $('#view .f')"),
    ]

    def test_the_way_back(self):
        """Down and up again, by the keys: back on the key it came from, not on the one nearest above the wide key
        below; and from a tab not shown into the page and up again, on that tab, not on the one shown."""
        self.b.js("navigate('allsearch', { q: '' })")
        self.b.wait_js("current.name === 'allsearch' && $('[data-k=key-mark1]')", msg="the letters")
        self.b.js("focusEl($('[data-k=key-mark1]'), false)")
        self.b.key("ArrowDown")
        self.assertEqual(self.focus(), "key-shift", "the wide key below")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "key-mark1", "back where it came from")
        self.b.js("navigate('home')")
        self.b.wait_js("current.name === 'home' && $('[data-k=libs-0]')", msg="Jellyfin's start")
        self.b.js("focusEl($('[data-k=tab-music]'), false)")
        self.b.key("ArrowDown")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "tab-music")
        self.b.key("ArrowDown")
        self.b.key("ArrowRight")
        self.assertTrue(self.focus().startswith("libs-"), "along the row")
        self.b.key("ArrowUp")
        self.assertEqual(self.focus(), "tab-jellyfin", "not right after the step down: the tab shown")
        self.b.js("document.activeElement.blur()")  # nothing focused (a click beside everything)
        self.b.key("ArrowDown")
        self.b.key("ArrowUp")
        self.assertTrue(self.b.js("document.activeElement.classList.contains('f')"), "not back onto nothing")

    def test_every_page_by_the_arrows(self):
        self.b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        for name, open_js, ready in self.PAGES:
            with self.subTest(page=name):
                self.b.js(open_js)
                self.b.wait_js(ready, timeout=15, msg=name)
                self.b.wait_js("document.activeElement?.classList.contains('f')", msg=f"{name}: something focused")
                time.sleep(1)  # rows filled after the page (the shelf, pictures) settle first
                r = self.b.js(FOCUS_CRAWL)
                self.assertGreaterEqual(r["reached"], 1, name)  # some pages have one button only
                self.assertEqual(r["unreached"], [], f"{name}: not reachable by the arrows")
                self.assertEqual(r["lost"], [], f"{name}: the focus lost")
                self.assertEqual(r["jumps"], [], f"{name}: out of a row sideways")
                self.assertEqual(r["oneway"], [], f"{name}: the opposite arrow not back where it came from")

    # what OK does there that only shows something (a page, a list to choose from, a text to read) and changes nothing:
    # tried on every page; playing, signing out, deleting, connecting and the like are tried by their own tests
    OPENS = {None, "open", "section", "library", "tab", "partsearch", "settingstile", "settingspart", "controls", "legal", "diagnose",
             "support", "share", "person", "allmore", "ownchannels", "ownguide", "svcfield", "choose", "libcheck",
             "bluetooth", "wifi", "hdmi", "goto", "season", "watchedmenu", "libopt", "sublangopt", "simple", "overview",
             "volumeopen", "malbum", "martist", "mplaylist", "msection", "albumsort", "toartist", "bbook", "blib",
             "ppodcast", "pcat", "wxplace"}

    def test_ok_and_back_on_every_page(self):
        """OK on each thing of every page (two of a kind: one tile of a row stands for the rest), then Back: back on the
        page it was pressed on, on that same thing, and the focus on something at every step, never on nothing."""
        self.b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        # the page built to its end (render, which marks it in shown), not only begun: the one before is still on the
        # screen meanwhile, its things there to be pressed
        settled = "!document.body.classList.contains('busy') && shown?.gen === renderGen"
        on_something = "document.activeElement?.classList.contains('f')"
        for name, open_js, ready in self.PAGES:
            with self.subTest(page=name):
                def open_page(again=False):
                    self.b.js("stack = []; $('#options').hidden || closeOptions()")
                    self.b.js(open_js)
                    # again: built to its end is enough (the main menu's focus kept on a tile whose shelf has nothing
                    # to choose, say)
                    self.b.wait_js(f"current.name === '{home}' && {settled}" if again else f"({ready}) && {settled}",
                                   timeout=15, msg=name)
                    self.b.wait_js(on_something, msg=f"{name}: something focused")
                home = None
                open_page()
                home = self.b.js("current.name")
                time.sleep(1)  # rows filled after the page settle first
                found = self.b.js("""[...view.querySelectorAll('.f')].filter((e) => e.dataset.k
                    && e.getBoundingClientRect().width > 0).map((e) => [e.dataset.k, e.dataset.action ?? null])""")
                kinds, tried = {}, []
                for k, action in found:
                    kind = re.sub(r"\d+", "#", k)
                    if action in self.OPENS and kinds.get(kind, 0) < 2:
                        kinds[kind] = kinds.get(kind, 0) + 1
                        tried.append(k)
                for k in tried:
                    if self.b.js(f"current.name !== '{home}'") or not self.b.js(f"!!view.querySelector('[data-k=\"{k}\"]')"):
                        open_page(again=True)
                    if not self.b.js(f"!!view.querySelector('[data-k=\"{k}\"]')"):
                        continue  # only there while something else was focused (a row's own buttons)
                    self.b.js(f"focusEl(view.querySelector('[data-k=\"{k}\"]'), false)")
                    page, depth = self.b.js("current.name"), self.b.js("stack.length")
                    self.b.key("Enter")
                    time.sleep(0.3)
                    self.b.wait_js(settled, timeout=15, msg=f"{name}: OK on {k} done")
                    # a page that is still finding out what to show (checking the library) has the focus once it does
                    self.b.wait_js(on_something, timeout=5, msg=f"{name}: OK on {k}, then the focus on nothing")
                    went = self.b.js("[stack.length, current.name, !$('#options').hidden, !$('#reader').hidden]")
                    if went[2] or went[3]:  # a list to choose from, a text to read: Back closes it
                        self.b.key("Escape")
                        self.b.wait_js("$('#options').hidden && $('#reader').hidden",
                                       msg=f"{name}: Back closes what OK on {k} opened")
                    elif went[0] > depth:  # a page: Back to this one (a page with a bar goes up to it first)
                        for _ in range(3):
                            self.b.key("Escape")
                            self.b.wait_js(settled, timeout=15)
                            if self.b.js("stack.length") <= depth:
                                break
                        self.b.wait_js(f"current.name === '{page}' && document.activeElement?.dataset.k === '{k}'",
                                       timeout=5, msg=f"{name}: Back from {went[1]} (OK on {k}) onto {k}")
                    self.assertTrue(self.b.js(on_something), f"{name}: Back after OK on {k}, then the focus on nothing")


# The two walks over every page in parts, every part a class of its own and so a process of its own beside the others
# (tests/parallel.py): over all the pages in one they took five minutes, at the end of the run alone
FOCUS_PARTS = 4
for _n in range(FOCUS_PARTS):
    _name = f"UiFocusPart{_n + 1}Test"
    globals()[_name] = type(_name, (UiFocusTest,), {
        "PAGES": UiFocusTest.PAGES[_n::FOCUS_PARTS], "__module__": __name__, "test_the_way_back": None,
        "test_every_page_by_the_arrows": UiFocusTest.test_every_page_by_the_arrows,
        "test_ok_and_back_on_every_page": UiFocusTest.test_ok_and_back_on_every_page})
UiFocusTest.test_every_page_by_the_arrows = UiFocusTest.test_ok_and_back_on_every_page = None  # in the parts
del _n, _name


if __name__ == "__main__":
    unittest.main()
