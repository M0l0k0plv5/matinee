"""One's own channels in the backend: the Jellyfin library as television, its guide and playing it."""
import time
import unittest
from pathlib import Path

from helpers import HAVE_FFMPEG, HAVE_MPV, Backend, fake_mpv_dir, make_video, temp_dir, wait_for
from mockjf import MockJellyfin

VIDEO_SECONDS = 20


class OwnChannelsTest(unittest.TestCase):
    """The library as channels (tv.py, "One's own channels"): which there are, what runs on them, and playing them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-own-"))
        cls.video = make_video(cls.tmp / "test.mkv", VIDEO_SECONDS) if HAVE_MPV and HAVE_FFMPEG else None
        cls.bin = fake_mpv_dir(cls.tmp, speed=8) if cls.video else None

    def setUp(self):
        self.mock = MockJellyfin(video_path=self.video).start()
        self.mock.lib.items["s1"]["Genres"] = ["Comedy"]  # the show's episodes run on the comedies' channel
        self.mock.lib.items["m2"]["Genres"] = ["Kom\u00f6die"]  # the same genre under its German name: no channel of its own
        self.backend = Backend(self.mock.url, mpv_dir=self.bin, env={"KINO_OWN_MIN": "4"}).start()

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def day(self, channel):  # its programmes in the guide, from an hour ago on
        return next(c for c in self.backend.request("/tv/guide")[1]["channels"] if c["id"] == channel)["items"]

    def own(self):
        return self.backend.request("/tv/own")[1]["own"]

    def test_a_channel_for_each_genre_and_decade(self):
        own = self.own()
        self.assertEqual([c["name"] for c in own], ["Comedy", "Action", "Drama", "Movies of the 2010s"],
                         "the genre with the most first; too few films of the 2020s for a channel")
        self.assertEqual(own[0]["id"], "own-g-comedy")
        ids = [c["id"] for c in self.backend.request("/tv/guide")[1]["channels"]]
        self.assertEqual(ids, [c["id"] for c in own], "the guide has a row for each")

    def test_left_out_in_the_settings(self):
        self.assertEqual(self.backend.request("/settings", {"tvoff": ["own-g-drama"]})[0], 200)
        self.assertEqual([c["name"] for c in self.own()], ["Comedy", "Action", "Movies of the 2010s"])
        self.assertNotIn("own-g-drama", [c["id"] for c in self.backend.request("/tv/guide")[1]["channels"]])
        self.assertEqual([c["name"] for c in self.backend.request("/settings")[1]["ownchannels"]],
                         ["Comedy", "Action", "Drama", "Movies of the 2010s"], "the settings list it still, to show it again")
        self.assertEqual(self.backend.request("/settings", {"tvoff": ["own-g drama"]})[0], 400)
        self.assertEqual(self.backend.request("/settings", {"tvoff": ["nonsense"]})[0], 400)

    def test_nothing_above_the_age_limit_on_them(self):
        self.mock.lib.items["m1"]["OfficialRating"] = "FSK-16"
        self.mock.lib.items["s1"]["OfficialRating"] = "FSK-16"  # its episodes: none of their own
        everything = {x["jf"] for c in self.backend.request("/tv/guide")[1]["channels"] for x in c["items"]}
        self.assertIn("m1", everything)
        self.assertTrue(any(i.startswith("e") for i in everything), "the show's episodes run too")
        self.assertEqual(self.backend.request("/settings", {"maxage": "12"})[0], 200)
        kept = {x["jf"] for c in self.backend.request("/tv/guide")[1]["channels"] for x in c["items"]}
        self.assertNotIn("m1", kept, "made again without it at once")
        self.assertFalse(any(i.startswith("e") for i in kept), "nor the show rated 16")
        self.assertIn("m2", kept)

    def test_a_programme_without_end(self):
        now = self.backend.request("/tv/now")[1]["own-g-action"]
        self.assertIn(now["jf"], ("m1", "m4", "m7", "m10"))
        self.assertLessEqual(now["start"], time.time())
        self.assertGreater(now["end"], time.time() - 1)
        self.assertEqual(now["picture"], f"/jf/Items/{now['jf']}/Images/Backdrop?maxWidth=960&quality=90")
        self.assertEqual(now["sub"], str(2010 + int(now["jf"][1:])), "a film's year below its title")
        items = self.day("own-g-action")
        self.assertTrue(all(abs(a["end"] - b["start"]) < 0.01 for a, b in zip(items, items[1:])), "one after the other")
        self.assertEqual({x["end"] - x["start"] for x in items[:8]} - {20.0}, set(), "each as long as it is")
        order = [x["jf"] for x in items[:8]]
        self.assertEqual(sorted(order[:4]), ["m1", "m10", "m4", "m7"], "all of them once")
        self.assertEqual(order[:4], order[4:], "then anew, in the same order")
        again = [x["jf"] for x in self.day("own-g-action")]
        self.assertIn(",".join(order[:4]), ",".join(again + again), "the same order whenever it is asked for")

    def test_a_series_runs_in_its_order(self):
        items = self.day("own-g-comedy")[:24]
        episodes = [x["jf"] for x in items if x["jf"].startswith("e")]
        self.assertEqual(set(episodes), {"e11", "e12", "e13", "e14", "e21", "e22", "e23", "e24"}, "no specials, none missing")
        order = ["e11", "e12", "e13", "e14", "e21", "e22", "e23", "e24"]
        at = order.index(episodes[0])
        self.assertEqual(episodes[:8], order[at:] + order[:at], "one episode after the other, round and round")
        self.assertEqual({x["jf"] for x in items[:12]} - set(order), {"m2", "m5", "m8", "m11"}, "its films among them")
        first = next(x for x in items if x["jf"] == "e12")
        self.assertEqual((first["title"], first["sub"]), ("Test Show", "S01E02 · Episode 2"))

    def test_none_of_a_small_library(self):
        self.backend.stop()
        del self.mock.lib.items["s1"]["Genres"]
        self.backend = Backend(self.mock.url).start()
        self.assertEqual(self.own(), [], "a channel needs a dozen films or episodes")

    @unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
    def test_tuning_in_and_what_comes_after(self):
        # mpv started, zapped and moved on under the load of the whole suite (seven at once): waited for longer than
        # wait_for's 10 s, which a green run never uses
        playing = lambda: (self.backend.mpv_socket() and self.backend.mpv("get_property", "path") or "").split("/Videos/")[-1].split("/")[0]
        order = [x["jf"] for x in self.day("own-g-action")]
        self.assertEqual(self.backend.request("/tv/play", {"live": "own-g-action"})[0], 200)
        wait_for(lambda: self.backend.mpv_socket() and self.backend.mpv("get_property", "time-pos") is not None, timeout=30, msg="mpv playing")
        first = playing()
        self.assertIn(first, order[:5], "what runs now")
        self.assertEqual(self.backend.request("/status")[1]["itemId"], "tv:live-own-g-action")
        self.assertTrue(self.backend.mpv("get_property", "user-data/kino/live"), "a channel: up and down zap")
        wait_for(lambda: (self.backend.mpv("get_property", "media-title") or "").startswith("Action – Movie "), timeout=30, msg="what runs as its title")
        after = order[order.index(first) + 1]
        wait_for(lambda: playing() == after, timeout=30, msg="the next programme once the film is over")
        self.assertEqual(self.backend.mpv("get_property", "start"), "none", "from its start")
        self.backend.mpv("keypress", "DOWN")  # the channel before, among one's own
        wait_for(lambda: self.backend.request("/status")[1]["itemId"] == "tv:live-own-g-comedy", timeout=30, msg="zapped to the comedies")
        wait_for(lambda: playing() in [x["jf"] for x in self.day("own-g-comedy")[:12]], timeout=30, msg="one of its films or episodes")
        self.backend.mpv("keypress", "DOWN")  # round to the last one
        wait_for(lambda: self.backend.request("/status")[1]["itemId"] == "tv:live-own-d-2010", timeout=30, msg="zapped to the films of the 2010s")
        wait_for(lambda: playing() in [f"m{n}" for n in range(1, 10)], timeout=30, msg="one of its films")
        wait_for(lambda: any(x.get("external") and "/Videos/m3/" in x.get("external-filename", "")
                             for x in self.backend.mpv("get_property", "track-list") or []), timeout=60,
                 msg="a film's subtitle file with it (Movie 3 has one)")
        self.backend.mpv("keypress", "ESC")
        wait_for(lambda: not self.backend.request("/status")[1]["playing"], timeout=30, msg="stopped")
        self.assertEqual(self.mock.reports, [], "nothing of it told to Jellyfin")
        self.assertEqual(self.backend.request("/tv/own")[1]["lastLive"], "own-d-2010")
