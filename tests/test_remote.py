"""Matinee Remote (remote/): the phone's page against a fake Jellyfin that serves it and passes its remote control on to
a real Matinee backend, and the lists Matinee puts with the Jellyfin account for the parts a phone is not signed in to."""
import json
import time
import unittest

from helpers import HAVE_FFMPEG, HAVE_MPV, wait_for
from mockabs import ABS_KEY
from mockjf import PASSWORD, USERNAME
from mocknd import ND_PASSWORD
from mockpf import PF_PASSWORD
from test_ui import HAVE_CHROMIUM, UiBase

SHELF = ("kino-remote", "kino-remote")


@unittest.skipUnless(HAVE_CHROMIUM and HAVE_MPV and HAVE_FFMPEG, "needs chromium, mpv and ffmpeg")
class RemoteTest(UiBase):
    navidrome = audiobookshelf = podfetch = True
    with_player = True
    section = None
    extra_env = {"KINO_REMOTE_SHELF_AFTER": "0", "KINO_REMOTE_SHELF_EVERY": "1"}

    def setUp(self):
        super().setUp()
        wait_for(lambda: self.backend.device_id() in self.mock.ws_devices, msg="Matinee connected to Jellyfin")
        self.mock.remote_sent.clear()

    def tearDown(self):
        self.backend.request("/music/control", {"cmd": "stop"})
        if self.backend.request("/status")[1].get("playing"):
            self.backend.mpv("keypress", "ESC")
            wait_for(lambda: not self.backend.request("/status")[1].get("playing"), msg="stopped")
        # Matinee's own page again, at the TV's size, for the next test
        self.b.errors.clear()
        self.b.js(f"if (location.origin !== {json.dumps(self.backend.url)}) location.href = {json.dumps(self.backend.url + '/')}")
        self.browser.tv_size()
        time.sleep(0.5)
        super().tearDown()

    # ---------- Helpers ----------

    def shelves(self):
        wait_for(lambda: SHELF in self.mock.display_prefs, timeout=20, msg="the lists put with the account")
        return {s["id"]: s["items"] for s in json.loads(self.mock.display_prefs[SHELF]["CustomPrefs"]["kino-remote"])["shelves"]}

    def music(self):
        return self.backend.request("/music/status")[1]

    def ask(self, what):
        """Matinee Remote's "play this", as Jellyfin passes it on."""
        self.mock.ws_devices[self.backend.device_id()][0].append(
            {"MessageType": "GeneralCommand", "Data": {"Name": "DisplayMessage", "ControllingUserId": "0" * 32,
                                                        "Arguments": {"Header": "kino-remote-play", "Text": json.dumps(what)}}})

    def phone(self, signed_in=True):
        """The remote's page on a phone, signed in and on this Matinee's library."""
        self.shelves()
        self.b.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        self.b.js(f"location.href = {json.dumps(self.mock.url + '/remote/index.html')}")
        self.b.wait_js("location.pathname === '/remote/index.html' && document.getElementById('loginForm') && typeof show === 'function'",
                       msg="the remote's page")
        self.b.js("localStorage.clear(); location.reload()")
        time.sleep(0.5)
        self.b.wait_js("typeof show === 'function' && !document.getElementById('login').hidden", msg="its sign-in")
        if not signed_in:
            return
        self.sign_in(PASSWORD)
        self.b.wait_js("!document.getElementById('devices').hidden && document.querySelectorAll('#deviceList .row').length === 1",
                       msg="the Matinee listed")
        self.b.js("document.querySelector('#deviceList .row').click()")
        self.b.wait_js("!document.getElementById('library').hidden && document.querySelectorAll('#shelves .shelf').length >= 8",
                       msg="its library")

    def sign_in(self, password):
        self.b.js(f"""(() => {{ server.value = location.origin; user.value = {json.dumps(USERNAME)}; password.value = {json.dumps(password)};
            document.getElementById('loginForm').requestSubmit(); }})()""")

    def titles(self):
        return self.b.js("[...document.querySelectorAll('#shelves .shelf')].sort((a, b) => a.style.order - b.style.order).map((s) => s.querySelector('h2').textContent)")

    def tap_card(self, shelf, name):
        """A tile of a row tapped; the choices it offers."""
        found = self.b.js(f"""(() => {{
          const s = [...document.querySelectorAll('#shelves .shelf')].find((s) => s.querySelector('h2').textContent === {json.dumps(shelf)});
          const c = s && [...s.querySelectorAll('.card')].find((c) => c.querySelector('.name').textContent === {json.dumps(name)});
          c?.click();
          return !!c; }})()""")
        self.assertTrue(found, f"{name} in {shelf}")
        self.b.wait_js("!document.getElementById('sheetBg').hidden && document.querySelectorAll('#sheetList .row').length > 0", msg="its choices")
        return self.b.js("[...document.querySelectorAll('#sheetList .row')].map((r) => r.textContent)")

    def choose(self, label):
        self.b.js(f"[...document.querySelectorAll('#sheetList .row')].find((r) => r.textContent === {json.dumps(label)}).click()")

    def sent(self, kind):
        return [data for _, k, data in self.mock.remote_sent if k == kind]

    # ---------- The lists Matinee puts there ----------

    def test_the_lists_for_the_phone(self):
        shelves = self.shelves()
        order = ["musicRecent", "musicNew", "booksBegun", "booksNew", "podcastsBegun", "podcastsNew"]
        self.assertEqual(list(shelves), [k for k in order if k in shelves], "part by part; a row with nothing in it left out")
        self.assertLessEqual({"musicNew", "booksNew", "podcastsNew"}, set(shelves))
        album = shelves["musicNew"][0]
        self.assertEqual((album["kind"], album["id"][:2], bool(album["title"]), bool(album["sub"])), ("album", "al", True, True))
        self.assertTrue(album["pic"].startswith("data:image/png;base64,"), "its cover in the list: the phone cannot ask Navidrome")
        book = next(x for x in shelves["booksNew"] if x["id"] == "bk1")
        self.assertEqual((book["kind"], book["title"]), ("book", "Folge 1: The First"))
        self.assertTrue(book["pic"].startswith("data:image/"))
        self.assertIsNone(next(x for x in shelves["booksNew"] if x["id"] == "bk2")["pic"], "a cover gone missing: none")
        self.assertEqual({x["kind"] for x in shelves["podcastsNew"]}, {"episode"})
        self.assertTrue(all(x["sub"] for x in shelves["podcastsNew"]), "each with its show")
        text = json.dumps(self.mock.display_prefs[SHELF])
        for secret in (ND_PASSWORD, ABS_KEY, PF_PASSWORD, self.nd.url, self.abs.url, self.pf.url):
            self.assertNotIn(secret, text, "no address, key or password of the other servers with Jellyfin")
        self.assertLess(len(text), 400_000)

    def test_not_with_the_sync_off(self):
        self.shelves()
        self.assertEqual(self.backend.request("/settings", {"sync": "off"})[0], 200)
        there = lambda: "kino-remote" in (self.mock.display_prefs.get(SHELF, {}).get("CustomPrefs") or {})
        try:
            wait_for(lambda: not there(), timeout=10, msg="what it put there before taken off the account")
            self.backend.request("/music/play", {"songs": [{"id": "s1", "title": "x"}]})  # something that would change them
            time.sleep(3)
            self.assertFalse(there(), "nothing of this Matinee's kept with the account")
        finally:
            self.backend.request("/settings", {"sync": "on"})
        wait_for(there, timeout=20, msg="put there again once it is on")

    def test_plays_what_the_phone_asks_for(self):
        shelves = self.shelves()
        self.ask({"kind": "album", "id": "../../etc"})
        self.ask({"kind": "song", "id": "s1"})
        time.sleep(1.5)
        self.assertFalse(self.music().get("active"), "not a good id, not a kind it plays")
        album = shelves["musicNew"][0]
        self.ask({"kind": "album", "id": album["id"]})
        wait_for(lambda: self.music().get("active"), msg="the album plays")
        queue = self.music()["queue"]
        self.assertGreater(len(queue), 0)
        self.assertEqual({s["albumId"] for s in queue}, {album["id"]}, "its songs, asked of Navidrome here")
        self.ask({"kind": "book", "id": "bk1"})
        wait_for(lambda: (self.music().get("queue") or [{}])[0].get("id") == "bk1", msg="the book plays")
        episode = shelves["podcastsNew"][0]
        self.ask({"kind": "episode", "id": episode["id"], "pos": 0})
        wait_for(lambda: (self.music().get("queue") or [{}])[0].get("id") == episode["id"], msg="the episode plays")
        self.assertTrue(self.music()["queue"][0].get("book"), "like a book")

    # ---------- The page ----------

    def test_signing_in(self):
        self.phone(signed_in=False)
        self.sign_in("wrong")
        self.b.wait_js("document.getElementById('loginError').textContent !== ''", msg="says so")
        self.assertEqual(self.b.js("document.getElementById('loginError').textContent"), "User name or password is wrong.")
        self.sign_in(PASSWORD)
        self.b.wait_js("!document.getElementById('devices').hidden && document.querySelectorAll('#deviceList .row').length === 1",
                       msg="the Matinee listed")
        self.assertEqual(self.b.js("document.getElementById('password').value"), "", "the password is not kept in the page")
        self.assertNotIn(PASSWORD, self.b.js("JSON.stringify(localStorage)"))
        self.b.js("document.getElementById('signOut').click()")
        self.b.wait_js("!document.getElementById('login').hidden", msg="signed out")
        self.assertNotIn("token", self.b.js("JSON.stringify(localStorage)"))

    def test_the_library_plays_on_the_kino(self):
        self.phone()
        titles = self.titles()
        self.assertEqual(titles[:2], ["Continue Watching", "Up Next"])
        self.assertEqual([t for t in titles if "·" in t],
                         [t for t in ["Music · Recently Played", "Music · New", "Audiobooks · Continue Listening", "Audiobooks · New",
                                      "Podcasts · Continue Listening", "Podcasts · New Episodes"]
                          if t in titles], "the other parts after Jellyfin's, in their order")
        self.assertLessEqual({"Movies", "Shows", "Music · New", "Audiobooks · New", "Podcasts · New Episodes"}, set(titles))
        shapes = self.b.js("""Object.fromEntries([...document.querySelectorAll('#shelves .shelf')].map((s) => {
          const a = s.querySelector('.card .art').getBoundingClientRect(); return [s.querySelector('h2').textContent, Math.round(100 * a.width / a.height)]; }))""")
        self.assertEqual((shapes["Movies"], shapes["Music · New"], shapes["Audiobooks · New"]), (67, 100, 100),
                         "posters and square covers")
        self.assertEqual(self.b.js("document.documentElement.scrollWidth <= innerWidth"), True, "nothing wider than the phone")
        # an album: played by Matinee from its Navidrome
        self.assertEqual(self.tap_card("Music · New", "First Album"), ["Play"])
        self.choose("Play")
        wait_for(lambda: self.music().get("active"), msg="the album plays on the Matinee")
        self.b.wait_js("!document.getElementById('remote').hidden", msg="on to the remote")
        self.assertEqual(self.mock.messages[-1][1], "kino-remote-play")
        self.backend.request("/music/control", {"cmd": "stop"})
        # a film: Jellyfin's own "Play on"
        self.b.js("document.getElementById('libraryButton').click()")
        self.b.wait_js("!document.getElementById('library').hidden")
        self.assertEqual(self.tap_card("Continue Watching", "Movie 1"), ["Resume at 0:05", "Start Over"])
        self.choose("Start Over")
        wait_for(lambda: self.backend.request("/status")[1].get("playing"), msg="the film plays on the Matinee")
        self.assertEqual(self.sent("Play")[-1], {"ItemIds": ["m1"], "PlayCommand": "PlayNow", "StartPositionTicks": 0})
        self.b.wait_js("!document.getElementById('remote').hidden")
        self.b.js("document.querySelector('[data-play=Stop]').click()")
        wait_for(lambda: not self.backend.request("/status")[1].get("playing"), msg="stopped from the phone")

    def test_the_keys(self):
        self.phone()
        self.b.js("document.getElementById('libraryBack').click()")
        self.b.wait_js("!document.getElementById('remote').hidden", msg="the remote")
        self.assertEqual(self.b.js("document.getElementById('status').textContent"), "Connected")
        for name in ("Back", "GoHome"):
            self.b.js(f"document.querySelector('[data-cmd={name}]')?.click()")
        wait_for(lambda: len(self.sent("GeneralCommand")) >= 2, msg="the keys sent")
        self.assertEqual(sorted(c["Name"] for c in self.sent("GeneralCommand")), ["Back", "GoHome"])
        # the surface: a tap in its middle selects, a swipe moves, a step for each stretch of it
        self.mock.remote_sent.clear()
        self.b.js("""(() => { const pad = document.getElementById('pad'), r = pad.getBoundingClientRect();
          const at = (type, x, y) => pad.dispatchEvent(new PointerEvent(type, { pointerId: 1, clientX: r.left + x, clientY: r.top + y, bubbles: true, button: 0 }));
          pad.setPointerCapture = () => {};
          const cx = r.width / 2, cy = r.height / 2;
          at('pointerdown', cx, cy); at('pointerup', cx, cy);
          at('pointerdown', cx - 60, cy); at('pointermove', cx + 40, cy); at('pointerup', cx + 40, cy); })()""")
        wait_for(lambda: len(self.sent("GeneralCommand")) >= 3, msg="the surface's commands")
        self.assertEqual(sorted(c["Name"] for c in self.sent("GeneralCommand")), ["MoveRight", "MoveRight", "Select"])
        # a text typed on the phone goes into Matinee's search
        self.mock.remote_sent.clear()
        self.b.js("document.getElementById('text').value = 'tatort'; document.getElementById('searchForm').requestSubmit()")
        wait_for(lambda: self.sent("GeneralCommand"), msg="the text sent")
        self.assertEqual(self.sent("GeneralCommand")[0], {"Name": "SendString", "Arguments": {"String": "tatort"}, "ControllingUserId": self.sent("GeneralCommand")[0]["ControllingUserId"]})


if __name__ == "__main__":
    unittest.main()
