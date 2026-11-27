"""Podcasts from Podfetch: signing in from the settings (with a user name and password, or none), what the backend
gives the UI, and episodes in the music player with their progress going back to Podfetch, against a fake Podfetch."""
import json
import unittest
from pathlib import Path

from helpers import HAVE_FFMPEG, HAVE_MPV, Backend, fake_mpv_dir, temp_dir, wait_for
from mockjf import MockJellyfin
from mockpf import EPISODE_SECONDS, PF_API_KEY, PF_PASSWORD, PF_USER, MockPodfetch
from test_music import make_audio


def sign_in(b, pf, user=PF_USER, password=PF_PASSWORD):
    return b.request("/service", {"service": "podfetch", "server": pf.url, "username": user, "password": password})


class PodfetchSessionTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.pf = MockPodfetch().start()
        self.b = Backend(self.mock.url).start()

    def tearDown(self):
        self.b.stop()
        self.mock.stop()
        self.pf.stop()

    def conf(self):
        return json.loads((self.b.conf / "config.json").read_text())

    def test_not_set_up(self):
        self.assertEqual(self.b.request("/pf/session")[1], {"configured": False})
        self.assertEqual(self.b.request("/pf/podcasts")[0], 503)
        self.assertEqual(self.b.request("/settings")[1]["podfetch"], None)

    def test_sign_in_from_the_settings(self):
        self.assertEqual(sign_in(self.b, self.pf, password="wrong"), (400, {"error": "service_rejected"}))
        self.assertEqual(sign_in(self.b, self.pf, user="", password=""), (400, {"error": "service_rejected"}),
                         "this Podfetch asks for a sign-in")
        self.assertEqual(sign_in(self.b, self.pf, password=""), (400, {"error": "service_missing_secret"}))
        self.assertNotIn("podfetch", self.conf(), "nothing kept that does not work")
        self.assertEqual(sign_in(self.b, self.pf)[0], 200)
        self.assertEqual(self.conf()["podfetch"], {"server": self.pf.url, "username": PF_USER})
        self.assertEqual(json.loads((self.b.conf / "podfetch.json").read_text()), {"password": PF_PASSWORD})
        self.assertEqual((self.b.conf / "podfetch.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(PF_PASSWORD, json.dumps(self.conf()))
        self.assertEqual(self.b.request("/pf/session")[1], {"configured": True, "ready": True})
        self.assertEqual(self.b.request("/settings")[1]["podfetch"], {"server": self.pf.url, "username": PF_USER})
        self.assertIn({"name": "Podfetch", "ok": True}, self.b.request("/overview")[1]["servers"])
        self.assertEqual(self.b.request("/service", {"service": "podfetch", "remove": True})[0], 200)
        self.assertNotIn("podfetch", self.conf())
        self.assertFalse((self.b.conf / "podfetch.json").exists())
        self.assertEqual(self.b.request("/pf/session")[1], {"configured": False})

    def test_a_podfetch_without_sign_in(self):
        self.pf.auth = False
        self.assertEqual(sign_in(self.b, self.pf, user="", password="")[0], 200)
        self.assertEqual(self.conf()["podfetch"], {"server": self.pf.url})
        self.assertFalse((self.b.conf / "podfetch.json").exists())
        self.assertEqual(self.b.request("/pf/session")[1], {"configured": True, "ready": True})
        self.pf.auth = True  # it asks for one now
        self.assertEqual(self.b.request("/pf/session")[1], {"configured": True, "ready": False, "error": "pf_login_required"})

    def test_what_the_ui_gets(self):
        sign_in(self.b, self.pf)
        code, r = self.b.request("/pf/podcasts")
        self.assertEqual(code, 200)
        self.assertEqual([(p["id"], p["name"], p["cover"]) for p in r["podcasts"]],
                         [("p2", "Another Show", True), ("p1", "Zeitfragen", True)], "by their name")
        code, img = self.b.request("/pf/cover/p1")
        self.assertEqual(code, 200)
        self.assertTrue(img.startswith(b"\x89PNG"), "fetched with the API key Podfetch put on its address")
        self.assertEqual(self.b.request("/pf/cover/nope")[0], 404)
        self.pf.heard["e-22222222"] = 2
        code, page = self.b.request("/pf/podcast/p1")
        self.assertEqual(code, 200)
        self.assertEqual(page["podcast"]["author"], "Radio One")
        self.assertEqual([(e["id"], e["date"], e["duration"], e["pos"]) for e in page["episodes"]],
                         [("e-11111111", "2026-10-20", EPISODE_SECONDS, 0), ("e-22222222", "2026-10-01", EPISODE_SECONDS, 2)],
                         "the newest first, with where it was left")
        code, recent = self.b.request("/pf/recent")
        self.assertEqual(code, 200)
        self.assertEqual([(e["id"], e["show"]) for e in recent["begun"]], [("e-22222222", "Zeitfragen")])
        self.assertEqual([e["id"] for e in recent["latest"]], ["e-11111111", "e-33333333", "e-22222222"])
        everything = json.dumps([r, page, recent])
        self.assertNotIn(PF_API_KEY, everything, "Podfetch's addresses with its key stay in the backend")
        self.assertNotIn("127.0.0.1", everything)
        self.assertEqual(self.b.request("/pf/podcast/../podcasts")[0], 404)

    def test_categories(self):
        sign_in(self.b, self.pf)
        pods = {p["id"]: p for p in self.b.request("/pf/podcasts")[1]["podcasts"]}
        self.assertEqual(pods["p1"]["categories"], ["News", "Society & Culture"], "as the feed gives them")
        self.assertEqual(pods["p2"]["categories"], ["Technology", "News"], "each once, without the spaces")
        self.assertEqual(self.b.request("/pf/podcast/p1")[1]["podcast"]["categories"], ["News", "Society & Culture"])

    def test_find_and_follow(self):
        sign_in(self.b, self.pf)
        code, r = self.b.request("/pf/find?q=zeit")
        self.assertEqual(code, 200)
        self.assertEqual([(p["id"], p["name"], p["following"]) for p in r["podcasts"]],
                         [(111, "Zeitfragen", True), (222, "Zeitgeist Weekly", False)], "the one followed known by its feed")
        self.assertEqual(r["podcasts"][0]["art"], "https://art.example/111.jpg")
        self.assertEqual(r["podcasts"][1]["art"], "", "only a picture over https")
        self.assertEqual(self.b.request("/pf/find?q=z")[1], {"podcasts": []}, "two letters at least")
        code, added = self.b.request("/pf/follow", {"id": 222})
        self.assertEqual(code, 200)
        self.assertEqual(self.pf.followed, [222])
        self.assertIn(added["id"], [p["id"] for p in self.b.request("/pf/podcasts")[1]["podcasts"]], "its id in Podfetch")
        self.assertTrue(self.b.request("/pf/find?q=weekly")[1]["podcasts"][0]["following"], "followed now")
        for bad in ("222", True, -1, 2 ** 31, None):
            self.assertEqual(self.b.request("/pf/follow", {"id": bad})[0], 400, bad)
        self.assertEqual(self.b.request("/pf/follow", {"id": 999})[0], 503, "Podfetch could not add it")
        self.assertEqual(self.pf.followed, [222])

    def test_unfollow(self):
        sign_in(self.b, self.pf)
        self.assertEqual(self.b.request("/pf/unfollow", {"id": "p2"})[0], 200)
        self.assertEqual(self.pf.deleted, [("p2", False)], "its downloads kept unless asked")
        self.assertEqual([p["id"] for p in self.b.request("/pf/podcasts")[1]["podcasts"]], ["p1"])
        self.assertNotIn("e-33333333", [e["id"] for e in self.b.request("/pf/recent")[1]["latest"]], "its episodes gone too")
        self.assertEqual(self.b.request("/pf/unfollow", {"id": "p1", "files": True})[0], 200)
        self.assertEqual(self.pf.deleted[-1], ("p1", True))
        self.assertEqual(self.b.request("/pf/unfollow", {"id": "p1"})[0], 404, "not there any more")
        self.assertEqual(self.b.request("/pf/unfollow", {"id": "../x"})[0], 400)

    def test_marked_as_heard(self):
        sign_in(self.b, self.pf)
        self.assertEqual(self.b.request("/pf/finished", {"id": "e-11111111", "on": True, "duration": EPISODE_SECONDS})[0], 200)
        self.assertEqual(self.pf.heard["e-11111111"], EPISODE_SECONDS)
        self.assertTrue(self.b.request("/pf/podcast/p1")[1]["episodes"][0]["finished"])
        self.assertEqual(self.b.request("/pf/finished", {"id": "e-11111111", "on": False})[0], 200)
        self.assertEqual(self.pf.heard["e-11111111"], 0, "from the start")
        self.assertEqual(self.b.request("/pf/finished", {"id": "a/b", "on": True})[0], 400)


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class PodcastPlayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-podcasts-"))
        cls.audio = make_audio(cls.tmp / "episode.ogg", EPISODE_SECONDS)
        cls.mpv_dir = fake_mpv_dir(cls.tmp, speed=1)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.pf = MockPodfetch(audio_path=self.audio).start()
        self.b = Backend(self.mock.url, mpv_dir=self.mpv_dir, env={"KINO_ABS_SYNC": "0.5"}).start()
        sign_in(self.b, self.pf)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.pf.stop()

    def status(self):
        return self.b.request("/music/status")[1]

    def test_plays_an_episode_and_tells_podfetch(self):
        self.assertEqual(self.b.request("/pf/play", {"id": "e-11111111"})[0], 200)
        wait_for(lambda: (self.status().get("pos") or 0) > 0.5, msg="playing")
        s = self.status()
        ep = s["queue"][0]
        self.assertEqual((ep["id"], ep["book"], ep["podcast"], ep["title"], ep["artist"]),
                         ("e-11111111", True, "p1", "Episode 2: Later", "Zeitfragen"))
        self.assertEqual([c["title"] for c in ep["chapters"]], ["Intro", "Main"])
        self.assertNotIn("url", ep)
        self.assertNotIn(PF_API_KEY, json.dumps(s))
        self.assertEqual(self.pf.streamed, ["e-11111111"], "its file, with the key, at the address Matinee knows")
        wait_for(lambda: any(e == "e-11111111" and t > 0 for e, t in self.pf.reports), msg=f"where it is: {self.pf.reports}")
        self.assertEqual(self.b.request("/music/control", {"cmd": "speed", "value": 1.5})[0], 200, "as fast as a book")
        wait_for(lambda: not self.status().get("active"), timeout=EPISODE_SECONDS + 10, msg="played to the end")
        wait_for(lambda: self.pf.heard.get("e-11111111") == EPISODE_SECONDS, msg=f"heard to the end: {self.pf.reports}")
        self.assertNotIn(PF_API_KEY, self.b.logtext())
        self.assertNotIn(PF_PASSWORD, self.b.logtext())

    def test_carries_on_where_it_was_left(self):
        self.pf.heard["e-33333333"] = 2
        self.b.request("/pf/play", {"id": "e-33333333"})
        wait_for(lambda: (self.status().get("pos") or 0) >= 2, msg="from 0:02")
        self.b.request("/music/control", {"cmd": "stop"})
        wait_for(lambda: not self.status().get("active"), msg="stopped")
        self.pf.heard["e-33333333"] = EPISODE_SECONDS  # heard to the end: from the start again
        self.b.request("/pf/play", {"id": "e-33333333"})
        wait_for(lambda: self.status().get("pos") is not None, msg="playing")
        self.assertLess(self.status()["pos"], 1.5)

    def test_unknown_episode(self):
        self.assertEqual(self.b.request("/pf/play", {"id": "nope"})[0], 404)
        self.assertEqual(self.b.request("/pf/play", {"id": "a/b"})[0], 400)


if __name__ == "__main__":
    unittest.main()
