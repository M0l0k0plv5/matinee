"""Audiobooks from Audiobookshelf: the API key, the backend's proxy and books in the music player, against a fake
Audiobookshelf."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import HAVE_FFMPEG, HAVE_MPV, ROOT, Backend, fake_mpv_dir, temp_dir, wait_for
from mockabs import ABS_KEY, TRACK_SECONDS, MockAudiobookshelf, book
from mockjf import MockJellyfin
from test_music import make_audio


def set_abs_key(b, key):
    return subprocess.run([sys.executable, str(ROOT / "set-password"), "--audiobookshelf", "--stdin"], input=key + "\n",
                          env=b.env, capture_output=True, text=True, timeout=30)


class BookSessionTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.abs = MockAudiobookshelf().start()

    def tearDown(self):
        self.mock.stop()
        self.abs.stop()

    def backend(self, audiobookshelf=True):
        conf = {"audiobookshelf": {"server": self.abs.url}} if audiobookshelf else None
        return Backend(self.mock.url, conf=conf).start()

    def test_without_audiobookshelf(self):
        b = self.backend(audiobookshelf=False)
        try:
            self.assertEqual(b.request("/abs/session")[1], {"configured": False})
            self.assertEqual(b.request("/abs/api/libraries")[0], 503)
            r = set_abs_key(b, ABS_KEY)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("No Audiobookshelf in config.json", r.stderr)
        finally:
            b.stop()

    def test_api_key(self):
        b = self.backend()
        try:
            self.assertEqual(b.request("/abs/session")[1], {"configured": True, "ready": False, "error": "abs_login_required"})
            r = set_abs_key(b, "wrong")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("API key rejected", r.stderr)
            self.assertFalse((b.conf / "audiobookshelf.json").exists())
            r = set_abs_key(b, ABS_KEY)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((b.conf / "audiobookshelf.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(b.request("/abs/session")[1], {"configured": True, "ready": True}, "the running backend picks it up")
        finally:
            b.stop()

    def test_proxy_only_reads(self):
        b = self.backend()
        try:
            set_abs_key(b, ABS_KEY)
            code, r = b.request("/abs/api/libraries/lib1/items?sort=addedAt&desc=1&limit=2")
            self.assertEqual(code, 200)
            self.assertEqual([x["id"] for x in r["results"]], ["bk3", "bk2"], "Audiobookshelf lists what is gone too")
            code, img = b.request("/abs/api/items/bk1/cover?width=300")
            self.assertEqual(code, 200)
            self.assertTrue(img.startswith(b"\x89PNG"))
            self.assertEqual(b.request("/abs/api/items/bk10?expanded=1&include=progress")[1]["media"]["numChapters"], 2)
            self.assertEqual(len(b.request("/abs/api/authors/au1?include=items")[1]["libraryItems"]), 4, "an author's books")
            for path in ("/abs/api/me", "/abs/api/users", "/abs/api/items/bk1/play", "/abs/api/items/bk1/file/bk1-0",
                         "/abs/api/libraries/lib1/scan", "/abs/api/items/../me", "/abs/api/authors/au1/image"):
                self.assertEqual(b.request(path)[0], 403, path)
            self.abs.progress["bk1"] = {"currentTime": 2, "progress": 0.5, "isFinished": False}
            code, p = b.request("/abs/progress")
            self.assertEqual((code, p), (200, {"bk1": {"progress": 0.5, "finished": False, "at": 0}}))
            self.assertNotIn("secret-user-token", json.dumps(p), "the user's own token stays out of it")
            self.assertNotIn(ABS_KEY, b.logtext())
        finally:
            b.stop()

    def test_remove_from_continue_listening(self):
        b = self.backend()
        try:
            set_abs_key(b, ABS_KEY)
            self.assertEqual(b.request("/forget", {"what": "book", "id": "bk1"})[0], 200)
            self.assertEqual(b.request("/hidden")[1]["books"], ["bk1"])
            self.assertEqual(self.abs.hidden, ["bk1"], "Audiobookshelf's own app hides it too")
            self.assertEqual(b.request("/forget", {"what": "book", "id": "../me"})[0], 400)
        finally:
            b.stop()

    def test_mark_as_heard(self):
        b = self.backend()
        try:
            set_abs_key(b, ABS_KEY)
            self.assertEqual(b.request("/abs/finished", {"id": "bk1", "on": True})[0], 200)
            self.assertEqual(self.abs.patches[-1], ("bk1", {"isFinished": True}))
            self.assertEqual(b.request("/abs/finished", {"id": "bk1", "on": False})[0], 200)
            self.assertEqual(self.abs.patches[-1], ("bk1", {"isFinished": False, "currentTime": 0, "progress": 0}), "from the start")
            self.assertEqual(b.request("/abs/finished", {"id": "a/b", "on": True})[0], 400)
        finally:
            b.stop()


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class BookPlayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-books-"))
        cls.audio = make_audio(cls.tmp / "part.ogg", TRACK_SECONDS)
        cls.mpv_dir = fake_mpv_dir(cls.tmp, speed=1)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.abs = MockAudiobookshelf(audio_path=self.audio).start()
        self.b = Backend(self.mock.url, mpv_dir=self.mpv_dir, env={"KINO_ABS_SYNC": "0.5", "KINO_SLEEP_FADE": "1"},
                         conf={"audiobookshelf": {"server": self.abs.url}}).start()
        set_abs_key(self.b, ABS_KEY)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.abs.stop()

    def status(self):
        return self.b.request("/music/status")[1]

    def control(self, cmd, value=None):
        return self.b.request("/music/control", {"cmd": cmd, "value": value})[0]

    def test_plays_a_book_in_two_files_and_tells_audiobookshelf(self):
        self.assertEqual(self.b.request("/abs/play", {"id": "bk10"})[0], 200)
        wait_for(lambda: (self.status().get("pos") or 0) > 0.5, msg="playing")
        s = self.status()
        book = s["queue"][0]
        self.assertEqual((book["id"], book["book"], book["title"], book["artist"]), ("bk10", True, "Folge 10: The Tenth", "The Author"))
        self.assertEqual([c["title"] for c in book["chapters"]], ["Part 1", "Part 2"])
        self.assertEqual(s["duration"], 2 * TRACK_SECONDS, "its files as one")
        self.assertNotIn("url", book, "the address carries the key")
        self.assertNotIn(ABS_KEY, json.dumps(s))
        wait_for(lambda: any(r[0] == "sync" and r[2]["currentTime"] > 0 and r[2]["timeListened"] > 0 for r in self.abs.reports),
                 msg=f"progress reported while it plays: {self.abs.reports}")
        self.assertEqual(self.control("seek", TRACK_SECONDS + 1), 200)  # into the second file
        wait_for(lambda: (self.status().get("pos") or 0) > TRACK_SECONDS + 1, msg="in the second chapter")
        self.assertEqual(self.control("stop"), 200)
        wait_for(lambda: self.abs.reports and self.abs.reports[-1][0] == "close", msg="session closed")
        self.assertGreater(self.abs.reports[-1][2]["currentTime"], TRACK_SECONDS, self.abs.reports)
        self.assertEqual(self.abs.streamed[-1], "bk10-1")
        self.assertNotIn(ABS_KEY, self.b.logtext())

    def test_carries_on_where_it_was_left(self):
        self.abs.progress["bk10"] = {"currentTime": 5.0, "isFinished": False}
        self.b.request("/abs/play", {"id": "bk10"})
        wait_for(lambda: (self.status().get("pos") or 0) >= 5, msg="from 0:05")
        self.control("stop")
        self.b.request("/abs/play", {"id": "bk10", "at": 0})
        wait_for(lambda: self.status().get("pos") is not None, msg="playing")
        self.assertLess(self.status()["pos"], 3, "from the start when asked")

    def test_heard_to_the_end_is_finished(self):
        self.b.request("/abs/play", {"id": "bk1"})
        wait_for(lambda: not self.status().get("active"), timeout=TRACK_SECONDS + 10, msg="played to the end")
        wait_for(lambda: ("bk1", {"isFinished": True}) in self.abs.patches, msg="marked as finished")
        close = [r for r in self.abs.reports if r[0] == "close"]
        self.assertEqual(close[-1][2]["currentTime"], TRACK_SECONDS)
        self.abs.progress["bk1"]["currentTime"] = TRACK_SECONDS
        self.b.request("/abs/play", {"id": "bk1"})
        wait_for(lambda: self.status().get("pos") is not None, msg="playing again")
        self.assertLess(self.status()["pos"], 2, "a book heard to the end starts again")

    def test_skip_and_speed(self):
        self.b.request("/abs/play", {"id": "bk10"})
        wait_for(lambda: self.status().get("pos") is not None, msg="playing")
        self.assertEqual(self.control("skip", 4), 200)
        wait_for(lambda: self.status()["pos"] >= 4, msg="ahead")
        self.assertEqual(self.control("speed", 1.5), 200)
        wait_for(lambda: self.status()["speed"] == 1.5, msg="faster")
        self.assertEqual(self.control("speed", 9), 400)
        self.control("stop")
        self.b.request("/abs/play", {"id": "bk2"})
        wait_for(lambda: self.status().get("speed") == 1.5, msg="the next book as fast")

    def test_sleep_timer_at_the_end_of_the_chapter(self):
        self.b.request("/abs/play", {"id": "bk10"})
        wait_for(lambda: self.status().get("pos") is not None, msg="playing")
        labels = [self.b.request("/sleep", {"cycle": True})[1]["sleep"] for _ in range(4)]
        self.assertEqual(labels[3], {"chapter": True}, f"after 30, 60, 90 minutes: {labels}")
        try:
            wait_for(lambda: not self.status().get("active"), timeout=TRACK_SECONDS + 10, msg="stopped")
        except AssertionError as e:
            # seen once in some hundred runs and not again: what the player and the backend said then, for the next time
            self.b.log.seek(0)
            raise AssertionError(f"{e}\nstatus: {self.status()}\nreports: {self.abs.reports}\nlog:\n{self.b.log.read()[-3000:]}") from None
        # the end's report goes out just after the player stops: waited for, under load it comes a moment later
        close = wait_for(lambda: [r for r in self.abs.reports if r[0] == "close"], msg="the end reported")[-1][2]
        # (a moment late under the load of the whole suite run in parallel: up to half a chapter, still far from the
        # book's end at two chapters)
        self.assertTrue(TRACK_SECONDS - 1.5 < close["currentTime"] < TRACK_SECONDS * 1.5,
                        f"at the end of the first chapter, not of the book: {close}")
        self.assertIsNone(self.b.request("/sleep", {"mode": None})[1]["sleep"])

    def test_played_again_it_comes_back(self):
        self.b.request("/forget", {"what": "book", "id": "bk2"})
        self.assertEqual(self.b.request("/hidden")[1]["books"], ["bk2"])
        self.b.request("/abs/play", {"id": "bk2"})
        self.assertEqual(self.b.request("/hidden")[1]["books"], [])

    def test_unknown_book(self):
        self.assertEqual(self.b.request("/abs/play", {"id": "nope"})[0], 404)
        self.assertEqual(self.b.request("/abs/play", {"id": "a/b"})[0], 400)


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class BookBreakOffTest(unittest.TestCase):
    """A book whose stream breaks off halfway is not heard to the end: Matinee waits for the server and goes on there."""
    SECONDS = 60

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-books-"))
        cls.audio = make_audio(cls.tmp / "long.ogg", cls.SECONDS)
        cls.mpv_dir = fake_mpv_dir(cls.tmp, speed=4)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.abs = MockAudiobookshelf(audio_path=self.audio).start()
        self.abs.books.append(book("bk60", "Folge 60: The Long One", 1, 5, seconds=self.SECONDS))
        self.b = Backend(self.mock.url, mpv_dir=self.mpv_dir, conf={"audiobookshelf": {"server": self.abs.url}}).start()
        set_abs_key(self.b, ABS_KEY)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.abs.stop()

    def status(self):
        return self.b.request("/music/status")[1]

    def test_waits_for_the_server_and_goes_on(self):
        self.abs.stream_cut = self.audio.stat().st_size // 2
        self.assertEqual(self.b.request("/abs/play", {"id": "bk60"})[0], 200)
        wait_for(lambda: self.status().get("lost"), timeout=40, msg="waiting for the server")
        s = self.status()
        cut = s["pos"]
        self.assertLess(cut, self.SECONDS - 10)
        self.assertEqual(s["queue"][0]["id"], "bk60", "still the book")
        self.assertNotIn(("bk60", {"isFinished": True}), self.abs.patches, "not heard to the end")
        self.abs.start()
        wait_for(lambda: not self.status().get("lost") and (self.status().get("pos") or 0) > 0, timeout=20,
                 msg="going on")
        self.assertAlmostEqual(self.status()["pos"], max(0, cut - 3), delta=8)
        # the reports go out behind the playing (book_reports' worker): the close may come a moment after it went on
        wait_for(lambda: any(r[0] == "close" for r in self.abs.reports), timeout=10, msg="the broken-off session closed")
        close = [r for r in self.abs.reports if r[0] == "close"]
        self.assertEqual(len(close), 1, "closed once")
        self.assertAlmostEqual(close[0][2]["currentTime"], cut, delta=5)
        self.assertEqual(self.abs.requests.count(("POST", "/api/items/bk60/play")), 2, "a new listening session")
        wait_for(lambda: ("bk60", {"isFinished": True}) in self.abs.patches, timeout=40, msg="heard to the end now")


if __name__ == "__main__":
    unittest.main()
