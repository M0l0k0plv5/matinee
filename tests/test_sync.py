"""The same on every device: two Matinees signed in to one Jellyfin user keep what they hide and how they like it alike (Sync)."""
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from helpers import Backend, wait_for
from mockjf import MockJellyfin

FAST = {"KINO_SYNC_EVERY": "0.5", "KINO_SYNC_DEBOUNCE": "0.1"}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.a = Backend(self.mock.url, env=FAST).start()
        self.b = Backend(self.mock.url, env=FAST).start()

    def tearDown(self):
        for backend in (self.a, self.b):
            backend.stop()
        self.mock.stop()

    @staticmethod
    def read(backend, name):
        try:
            return json.loads((backend.conf / f"{name}.json").read_text())
        except (OSError, ValueError):
            return {}

    def hidden(self, backend):
        return sorted(self.read(backend, "hidden").get("nextup", []))

    def hide(self, backend, show):  # a show taken off Up Next
        self.assertEqual(backend.request("/forget", {"what": "nextup", "id": show})[0], 200)

    def test_taken_off_a_row_on_one_device_comes_to_the_other(self):
        self.hide(self.a, "s1")
        wait_for(lambda: self.hidden(self.b) == ["s1"], timeout=8, msg="taken off on the other device too")
        prefs = self.mock.display_prefs[("kino", "kino")]
        self.assertIn("s1", prefs["CustomPrefs"]["kino-sync"], "kept with the Jellyfin user")
        self.assertEqual(self.a.request("/forget/undo", {})[0], 200)
        wait_for(lambda: self.hidden(self.b) == [], timeout=8, msg="and back on it there too")

    def test_the_newer_change_wins(self):
        self.hide(self.a, "s1")
        wait_for(lambda: self.hidden(self.b) == ["s1"], timeout=8, msg="on both")
        time.sleep(0.1)
        self.assertEqual(self.b.request("/unhide", {})[0], 200)  # later, on the other one
        self.hide(self.b, "s2")
        wait_for(lambda: self.hidden(self.a) == ["s2"], timeout=8, msg="the removal and the new one on the first")
        time.sleep(1.5)  # a few rounds more: nothing comes back
        self.assertEqual(self.hidden(self.a), ["s2"])
        self.assertEqual(self.hidden(self.b), ["s2"])

    def test_taken_off_a_row_and_shown_again(self):
        self.assertEqual(self.b.request("/forget", {"what": "nextup", "id": "s1"})[0], 200)
        wait_for(lambda: self.read(self.a, "hidden").get("nextup") == ["s1"], timeout=8, msg="hidden on the other device")
        self.assertEqual(self.a.request("/unhide", {})[0], 200)
        wait_for(lambda: self.read(self.b, "hidden").get("nextup") == [], timeout=8, msg="shown again everywhere")

    def test_albums_and_books_taken_off_and_the_channel_watched_last(self):
        self.assertEqual(self.a.request("/forget", {"what": "album", "id": "al1"})[0], 200)
        wait_for(lambda: self.read(self.b, "hidden").get("albums") == ["al1"], timeout=8, msg="the album")
        (self.a.conf / "tv.json").write_text(json.dumps({"lastLive": "own-g-action"}))
        time.sleep(1.5)
        self.assertIsNone(self.read(self.b, "tv").get("lastLive"), "the channel last watched live is the device's")

    def test_audio_and_subtitles_chosen_for_a_show(self):
        (self.a.conf / "tracks.json").write_text(json.dumps({"s1": {"audio": "en", "sub": "de"}}))
        wait_for(lambda: self.read(self.b, "tracks") == {"s1": {"audio": "en", "sub": "de"}}, timeout=8, msg="on the other device")

    def test_settings_of_the_person_not_of_the_device(self):
        self.assertEqual(self.a.request("/settings", {"subtitles": "always", "sublang": "en", "spoilers": "hide",
                                                      "effects": "light", "textsize": "large", "size": "tv"})[0], 200)
        wait_for(lambda: self.read(self.b, "config").get("subtitles") == "always", timeout=8, msg="the subtitles' setting")
        settings = self.b.request("/settings")[1]
        self.assertEqual((settings["subtitles"], settings["sublang"], settings["spoilers"]), ("always", "en", "hide"),
                         "in use at once")
        self.assertNotIn("effects", self.read(self.b, "config"), "the device's own effects stay its own")
        self.assertNotIn("textsize", self.read(self.b, "config"))
        self.assertNotIn("size", self.read(self.b, "config"), "nor how large it is drawn (a TV, a Mac)")
        self.assertIn("server", self.read(self.b, "config"), "and the rest of its file as it was")
        self.assertEqual(self.b.request("/settings", {"subtitles": "off"})[0], 200)
        wait_for(lambda: self.read(self.a, "config").get("subtitles") == "off", timeout=8, msg="changed back from the other")
        # a value this Matinee does not know (from a newer one, say): left out, the rest taken
        prefs = self.mock.display_prefs[("kino", "kino")]
        doc = json.loads(prefs["CustomPrefs"]["kino-sync"])
        doc["lists"]["settings"]["ask"] = [time.time() + 5, {"k": "ask", "v": "never"}]
        doc["lists"]["settings"]["badges"] = [time.time() + 5, {"k": "badges", "v": "off"}]
        prefs["CustomPrefs"]["kino-sync"] = json.dumps(doc)
        wait_for(lambda: self.read(self.a, "config").get("badges") == "off", timeout=8, msg="the one it knows taken")
        self.assertNotEqual(self.read(self.a, "config").get("ask"), "never", "the one it does not know left out")

    def test_the_weather_s_place(self):
        place = {"name": "Sampletown", "lat": 50.5, "lon": 8.25}
        self.assertEqual(self.a.request("/settings", {"weather": place})[0], 200)
        wait_for(lambda: self.read(self.b, "config").get("weather") == place, timeout=8, msg="the screensaver's place")
        # nothing a device cannot take: no place without where it is
        prefs = self.mock.display_prefs[("kino", "kino")]
        doc = json.loads(prefs["CustomPrefs"]["kino-sync"])
        doc["lists"]["settings"]["weather"] = [time.time() + 5, {"k": "weather", "v": {"name": "Nowhere"}}]
        doc["lists"]["settings"]["badges"] = [time.time() + 5, {"k": "badges", "v": "off"}]
        prefs["CustomPrefs"]["kino-sync"] = json.dumps(doc)
        wait_for(lambda: self.read(self.a, "config").get("badges") == "off", timeout=8, msg="the next change taken")
        self.assertEqual(self.read(self.a, "config").get("weather"), place, "nor a place without where it is")

    def test_an_entry_of_another_shape_is_left_out(self):
        """An entry that is no dict in a list of dicts (or no string in a list of ids), from any client of the user:
        not taken, and the sync and the lists go on working."""
        self.hide(self.a, "s1")
        wait_for(lambda: ("kino", "kino") in self.mock.display_prefs, timeout=8, msg="on the server")
        prefs = self.mock.display_prefs[("kino", "kino")]
        doc = json.loads(prefs["CustomPrefs"]["kino-sync"])
        doc["lists"].setdefault("jfEarly", {})["abc"] = [time.time() + 5, "x"]
        doc["lists"].setdefault("hiddenNextup", {})["def"] = [time.time() + 5, {"id": "def"}]
        prefs["CustomPrefs"]["kino-sync"] = json.dumps(doc)
        time.sleep(1.5)  # synced meanwhile
        self.hide(self.a, "s2")
        wait_for(lambda: "s2" in self.hidden(self.b), timeout=8, msg="still syncing")
        for backend in (self.a, self.b):
            self.assertTrue(all(isinstance(x, dict) for x in self.read(backend, "early").get("early", [])))
            self.assertTrue(all(isinstance(x, str) for x in self.read(backend, "hidden").get("nextup", [])))

    def test_the_devices_of_the_account(self):
        self.hide(self.a, "s1")  # something to sync
        def names(backend):
            return [(d["self"], bool(d["name"])) for d in backend.request("/settings")[1]["devices"]]
        wait_for(lambda: names(self.a) == [(True, True), (False, True)], timeout=8, msg="this one first, then the other")
        wait_for(lambda: names(self.b) == [(True, True), (False, True)], timeout=8, msg="the same on the other")
        doc = json.loads(self.mock.display_prefs[("kino", "kino")]["CustomPrefs"]["kino-sync"])
        self.assertEqual(len(doc["devices"]), 2)
        self.assertTrue(all(d["version"] and d["at"] for d in doc["devices"].values()), "each with its version and when")

    def test_what_only_this_device_has_stays_when_it_first_syncs(self):
        self.b.stop()
        self.hide(self.a, "s1")
        wait_for(lambda: ("kino", "kino") in self.mock.display_prefs, timeout=8, msg="on the server")
        later = Backend(self.mock.url, env=FAST)
        (later.conf / "hidden.json").write_text(json.dumps({"nextup": ["s2"], "albums": [], "books": []}))
        self.b = later.start()
        wait_for(lambda: self.hidden(self.b) == ["s1", "s2"], timeout=8, msg="both here")
        wait_for(lambda: self.hidden(self.a) == ["s1", "s2"], timeout=8, msg="and there")

    def test_changes_at_once_lose_nothing(self):
        shows = [f"show{n}" for n in range(20)]
        with ThreadPoolExecutor(len(shows)) as pool:  # taken off all at once, while the sync writes back too
            list(pool.map(lambda show: self.hide(self.a, show), shows))
        self.assertEqual(self.hidden(self.a), sorted(shows), "every one kept")
        wait_for(lambda: self.hidden(self.b) == sorted(shows), timeout=8, msg="and on the other device")

    def test_taken_off_while_the_server_takes_its_time(self):
        self.hide(self.a, "s1")
        wait_for(lambda: self.hidden(self.b) == ["s1"], timeout=8, msg="on both")
        later = "s3"
        self.hide(self.a, later)
        wait_for(lambda: later in json.dumps(self.mock.display_prefs.get(("kino", "kino"))), timeout=8, msg="on the server")
        self.mock.prefs_open.clear()  # both devices' next look waits: it has the new one for the second
        wait_for(lambda: self.mock.prefs_waiting >= 2, timeout=8, msg="both looking")
        here = "s4"
        self.hide(self.b, here)  # meanwhile, on the second device
        self.mock.prefs_open.set()
        every = sorted(["s1", later, here])
        wait_for(lambda: self.hidden(self.b) == every, timeout=8, msg="the one taken off meanwhile kept")
        wait_for(lambda: self.hidden(self.a) == every, timeout=8, msg="and on the first device too")

    def test_off_keeps_this_device_to_itself(self):
        self.assertEqual(self.b.request("/settings", {"sync": "off"})[0], 200)
        self.hide(self.a, "s1")
        wait_for(lambda: ("kino", "kino") in self.mock.display_prefs, timeout=8, msg="on the server")
        time.sleep(1.5)
        self.assertEqual(self.hidden(self.b), [], "not taken")
        settings = self.b.request("/settings")[1]
        self.assertEqual(settings["sync"], "off")
        self.assertEqual(self.b.request("/settings", {"sync": "on"})[0], 200)
        wait_for(lambda: self.hidden(self.b) == ["s1"], timeout=8, msg="on again: taken")
        self.assertIsNotNone(self.b.request("/settings")[1]["syncStatus"]["at"], "when it last synced")


class SyncOnlyWhenNeededTest(unittest.TestCase):
    def test_only_what_is_kept_alike_is_sent(self):
        mock = MockJellyfin().start()
        c = Backend(mock.url, env={"KINO_SYNC_EVERY": "3600", "KINO_SYNC_DEBOUNCE": "0.1"}).start()
        try:
            looks = lambda: sum("/DisplayPreferences/" in r for r in mock.requests)
            wait_for(lambda: c.request("/settings")[1]["syncStatus"]["at"], timeout=8, msg="the first sync")
            before = looks()
            self.assertEqual(c.request("/settings", {"effects": "light"})[0], 200)  # this device's own
            time.sleep(1)
            self.assertEqual(looks(), before, "nothing to send")
            self.assertEqual(c.request("/settings", {"skipintro": "auto"})[0], 200)  # the person's
            wait_for(lambda: looks() > before, timeout=8, msg="sent")
        finally:
            c.stop()
            mock.stop()


if __name__ == "__main__":
    unittest.main()
