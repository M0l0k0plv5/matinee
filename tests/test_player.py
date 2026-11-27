"""Playback with a real mpv (no video/audio output): progress, resume, next episode, skipping, keys."""
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from helpers import HAVE_FFMPEG, HAVE_MPV, ROOT, Backend, fake_mpv_dir, make_video, temp_dir, wait_for
from mockjf import TOKEN, USER_ID, MockJellyfin, TICKS

VIDEO_SECONDS = 20


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class PlayerBase(unittest.TestCase):
    speed = 8
    language = "en"
    conf = None
    env = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = temp_dir("kino-video-")
        cls.video = make_video(Path(cls.tmp) / "test.mkv", VIDEO_SECONDS)
        cls.hls = make_video(Path(cls.tmp) / "transcoded.ts", VIDEO_SECONDS)
        cls.mpv_dir = fake_mpv_dir(cls.tmp, cls.speed)

    def setUp(self):
        self.mock = MockJellyfin(video_path=self.video, runtime_s=VIDEO_SECONDS).start()
        self.mock.hls_path = self.hls
        self.backend = Backend(self.mock.url, mpv_dir=self.mpv_dir, language=self.language, conf=self.conf,
                               env=self.env).start()

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def play(self, item, resume=True):
        started = len(self.mock.reports_for("/Playing"))
        self.assertEqual(self.backend.request("/play", {"itemId": item, "resume": resume})[0], 200)
        # the start report only comes once the backend is connected to mpv
        try:
            wait_for(lambda: len(self.mock.reports_for("/Playing")) > started, msg="start reported")
        except AssertionError as e:  # what the backend did meanwhile, to see why
            raise AssertionError(f"{e}\n--- backend log ---\n{self.backend.logtext()[-3000:]}") from None
        wait_for(lambda: self.backend.mpv_socket() and self.backend.mpv("get_property", "time-pos") is not None,
                 msg="mpv playing")

    def status(self):
        return self.backend.request("/status")[1]

    def wait_stopped(self, timeout=20):
        wait_for(lambda: not self.status()["playing"], timeout=timeout, msg="playback finished")

    def key(self, k):
        self.backend.mpv("keypress", k)
        time.sleep(0.4)

    def pos(self):
        return self.backend.mpv("get_property", "time-pos")

    def panel(self):
        """The info panel's tab, "" when it is closed (kino.lua)."""
        return self.backend.mpv("get_property", "user-data/kino/panel") or ""

    def skip(self):
        return self.backend.mpv("get_property", "user-data/kino/skip")

    def audio_lang(self):
        tracks = self.backend.mpv("get_property", "track-list")
        return next(t for t in tracks if t["type"] == "audio" and t.get("selected")).get("lang")


class PlayerFastTest(PlayerBase):
    """Eight times faster: a video ends after 2.5 seconds."""

    def test_movie_played_to_the_end_is_reported_watched(self):
        self.play("m1", resume=False)
        self.wait_stopped()
        kinds = [p.rsplit("/", 1)[1] for p, b in self.mock.reports if b["ItemId"] == "m1"]
        self.assertEqual(kinds[0], "Playing")
        self.assertIn("Progress", kinds)
        self.assertEqual(kinds[-1], "Stopped")
        stop = self.mock.reports_for("Stopped")[-1]
        self.assertGreater(stop["PositionTicks"], 0.95 * VIDEO_SECONDS * TICKS)
        self.assertTrue(self.mock.lib.userdata["m1"]["Played"])
        wait_for(lambda: self.backend.mpv_socket() is None, msg="IPC socket cleaned up")

    def test_a_player_that_crashes_is_told_to_the_ui(self):
        self.play("m1", resume=False)
        self.backend.mpv("quit")
        self.wait_stopped()
        self.assertIsNone(self.status()["error"], "stopped as usual: nothing to say")
        self.play("m1", resume=False)
        subprocess.run(["pkill", "-KILL", "-f", "--", f"--input-ipc-server={self.backend.runtime}"], check=False)
        self.wait_stopped()
        self.assertEqual(self.status()["error"], "crashed", "the UI says it stopped unexpectedly")
        self.assertEqual(self.mock.reports_for("Stopped")[-1]["ItemId"], "m1", "and where it was is kept")

    def test_next_episode_starts_automatically(self):
        self.play("e11", resume=False)
        wait_for(lambda: self.status()["itemId"] == "e12", timeout=15, msg="episode 2 loaded")
        self.assertTrue(self.status()["playing"], "in the same mpv, without stopping")
        self.assertTrue(self.mock.lib.userdata["e11"]["Played"], "episode 1 reported as watched")
        # the start is reported once episode 2 plays
        wait_for(lambda: [b["ItemId"] for b in self.mock.reports_for("/Playing")][:2] == ["e11", "e12"], timeout=3,
                 msg="start of episode 2 reported")
        self.assertEqual(self.backend.mpv("get_property", "media-title"), "Test Show – S01E02 – Episode 2")

    def test_across_season_boundary(self):
        self.play("e14", resume=False)
        wait_for(lambda: self.status()["itemId"] == "e21", timeout=15, msg="first episode of the next season")

    def test_last_episode_closes_the_player(self):
        self.play("e24", resume=False)
        self.wait_stopped()
        self.assertEqual([b["ItemId"] for b in self.mock.reports_for("/Playing")], ["e24"])
        self.assertTrue(self.mock.lib.userdata["e24"]["Played"])

    def test_server_outage_during_playback(self):
        """If the server is gone at the end, the final position is stored and sent later."""
        self.play("m4", resume=False)
        self.mock.stop()
        self.wait_stopped()
        pending = self.backend.conf / "pending.json"
        wait_for(lambda: '"m4"' in pending.read_text(), msg="final position queued")
        self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.backend.conf.glob("*.tmp")), [], "written in one piece")
        self.assertFalse(self.mock.lib.userdata["m4"]["Played"])
        self.mock.start()
        wait_for(lambda: self.mock.lib.userdata["m4"]["Played"], timeout=15, msg="sent later and marked watched")

    def test_stream_breaking_off_waits_for_the_server(self):
        """A stream cut halfway is not the end of the episode: Matinee waits for the server and goes on there."""
        self.mock.stream_cut = self.video.stat().st_size // 2
        self.play("e11", resume=False)
        wait_for(lambda: self.mock.httpd.socket.fileno() == -1, msg="server gone")
        wait_for(lambda: self.backend.mpv("get_property", "eof-reached"), timeout=30, msg="mpv at the cut")
        cut = self.pos()
        self.assertLess(cut, VIDEO_SECONDS - 5)
        time.sleep(4)
        self.assertTrue(self.status()["playing"], "still waiting in the player")
        self.assertEqual(self.status()["itemId"], "e11", "not on to the next episode")
        self.mock.start()
        wait_for(lambda: len(self.mock.reports_for("/Playing")) >= 2, timeout=20, msg="started again")
        again = self.mock.reports_for("/Playing")[-1]
        self.assertEqual(again["ItemId"], "e11")
        self.assertAlmostEqual(again["PositionTicks"] / TICKS, max(0, cut - 3), delta=1)
        stopped = self.mock.reports_for("/Stopped")
        self.assertEqual([b["ItemId"] for b in stopped], ["e11"], "the broken-off session ended")
        self.assertAlmostEqual(stopped[0]["PositionTicks"] / TICKS, cut, delta=2)
        wait_for(lambda: self.status()["itemId"] == "e12", timeout=20, msg="the next episode after the real end")
        self.assertTrue(self.mock.lib.userdata["e11"]["Played"])

    def test_token_not_in_the_process_list(self):
        self.play("m1", resume=False)
        pids = subprocess.run(["pgrep", "-f", "--", f"--input-ipc-server={self.backend.runtime}"], capture_output=True, text=True).stdout.split()
        self.assertTrue(pids, "mpv found")
        for pid in pids:
            # ps shows what anyone on the machine sees, on Linux as well as on macOS
            cmdline = subprocess.run(["ps", "-ww", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
            self.assertNotIn(TOKEN, cmdline, "other users on the machine could read it with ps")
        self.assertIn(TOKEN, self.backend.mpv("get_property", "path"), "mpv still has it for the stream")

    def test_fast_line_plays_the_file_directly(self):
        self.play("m1", resume=False)
        self.assertEqual(self.mock.reports_for("/Playing")[-1]["PlayMethod"], "DirectPlay")
        self.assertEqual(self.mock.transcodes, [])
        self.assertIn("/stream?static=true", self.backend.mpv("get_property", "path"))

    def test_movie_does_not_start_a_next_episode(self):
        self.play("m1", resume=False)
        self.wait_stopped()
        self.assertEqual([b["ItemId"] for b in self.mock.reports_for("/Playing")], ["m1"])


class PlayerEarlyStopTest(PlayerBase):
    """Stopped in a film's first minutes, where Jellyfin keeps no position (5 %, here half of it): Matinee keeps it."""
    speed = 1
    env = {"KINO_EARLY_FROM": "1", "KINO_EARLY_SHARE": "0.5"}

    def shown(self, item):
        """The position the page is given for an item, through the backend."""
        return self.backend.request(f"/jf/Items/{item}?userId={USER_ID}")[1]["UserData"]["PlaybackPositionTicks"] / TICKS

    def test_kept_here_and_gone_on_from(self):
        self.mock.min_resume = 0.5
        self.play("m2", resume=False)
        wait_for(lambda: (self.pos() or 0) >= 3, timeout=10, msg="a little way in")
        self.key("GO_BACK")
        self.wait_stopped()
        self.assertEqual(self.mock.lib.userdata["m2"]["PlaybackPositionTicks"], 0, "Jellyfin keeps nothing so early")
        at = self.shown("m2")
        self.assertGreaterEqual(at, 3, "the page offers to go on")
        self.assertEqual(self.shown("m3"), 0, "only for that film")
        resume = self.backend.request(f"/jf/UserItems/Resume?userId={USER_ID}&Limit=12&MediaTypes=Video")[1]["Items"]
        self.assertEqual([i["Id"] for i in resume], ["m2"], "in Continue Watching too, though Jellyfin has it not begun")
        self.assertGreaterEqual(resume[0]["UserData"]["PlaybackPositionTicks"] / TICKS, 3, "with where it was")
        self.play("m2", resume=True)
        self.assertGreaterEqual(self.pos(), at - 0.5, "goes on from there")
        wait_for(lambda: (self.pos() or 0) >= 11, timeout=15, msg="past where Jellyfin keeps it")
        self.key("GO_BACK")
        self.wait_stopped()
        self.assertGreaterEqual(self.mock.lib.userdata["m2"]["PlaybackPositionTicks"], 11 * TICKS)
        self.assertEqual(json.loads((self.backend.tmp / "config" / "kino" / "early.json").read_text())["early"], [],
                         "Jellyfin's again")

    def test_marked_unwatched_it_is_gone(self):
        """Marked unwatched (or watched) after an early stop: off Continue Watching, the page offers no position."""
        self.mock.min_resume = 0.5
        self.play("m2", resume=False)
        wait_for(lambda: (self.pos() or 0) >= 2, timeout=10, msg="a little way in")
        self.key("GO_BACK")
        self.wait_stopped()
        resume = lambda: [i["Id"] for i in self.backend.request(f"/jf/UserItems/Resume?userId={USER_ID}")[1]["Items"]]
        self.assertEqual(resume(), ["m2"])
        self.assertEqual(self.backend.request("/played", {"itemId": "m2", "played": False})[0], 200)
        self.assertEqual(resume(), [], "gone from Continue Watching")
        self.assertEqual(self.shown("m2"), 0, "and from its page")

    def test_not_for_a_film_watched_meanwhile(self):
        self.mock.min_resume = 0.5
        self.play("m4", resume=False)
        wait_for(lambda: (self.pos() or 0) >= 2, timeout=10, msg="a little way in")
        self.key("GO_BACK")
        self.wait_stopped()
        self.assertGreaterEqual(self.shown("m4"), 2)
        self.mock.lib.userdata["m4"]["Played"] = True  # watched to the end elsewhere
        self.assertEqual(self.shown("m4"), 0)
        self.play("m4", resume=True)
        self.assertLess(self.pos(), 1.5, "from the start")


class PlayerServerStaysAwayTest(PlayerBase):
    env = {"KINO_RECONNECT_FOR": "3"}

    def test_gives_up_and_says_so(self):
        self.mock.stream_cut = self.video.stat().st_size // 2
        self.play("m1", resume=False)
        self.wait_stopped(timeout=40)
        self.assertEqual(self.status()["error"], "connection", "the UI says what happened")
        pending = self.backend.conf / "pending.json"
        wait_for(lambda: '"m1"' in pending.read_text(), msg="the position kept for when the server is back")


class PlayerRemoteKeysTest(PlayerBase):
    """Keys of a Fire TV remote: its play/pause key arrives in mpv as PLAY and must pause as well."""

    def test_play_key_pauses_and_plays(self):
        self.play("m1", resume=False)
        self.key("PLAY")
        self.assertTrue(self.backend.mpv("get_property", "pause"), "paused")
        self.key("PLAY")
        self.assertFalse(self.backend.mpv("get_property", "pause"), "playing again")

    def test_play_key_held_switches_the_sleep_timer(self):
        self.play("m1", resume=False)
        self.backend.mpv("keydown", "PLAY")
        time.sleep(1)
        self.backend.mpv("keyup", "PLAY")
        wait_for(lambda: self.status()["sleep"] is not None, timeout=3, msg="sleep timer on")
        self.assertFalse(self.backend.mpv("get_property", "pause"), "and not paused")
        self.backend.request("/sleep", {"mode": None})


class PlayerGameControllerTest(PlayerBase):
    """An Xbox controller while a video plays: its keys go to mpv, pressed and let go apart (a held key repeats)."""

    def setUp(self):
        def bits(*codes):  # as the kernel lists them: words of a long, the highest first, without their zeros
            width = struct.calcsize("l") * 2
            h = format(sum(1 << c for c in codes), "x")
            h = h.rjust(-(-len(h) // width) * width, "0")
            return " ".join(h[i:i + width].lstrip("0") or "0" for i in range(0, len(h), width))
        inp = Path(self.tmp) / f"input-{self._testMethodName}"
        inp.mkdir()
        devices = inp / "devices"
        devices.write_text(f'N: Name="Made-up Controller"\nH: Handlers=event9 js0\nB: KEY={bits(304, 305, 315)}\n'
                           f"B: ABS={bits(0, 1, 16, 17)}\n\n")
        os.mkfifo(inp / "event9")
        self.pad = os.open(inp / "event9", os.O_RDWR)
        self.addCleanup(os.close, self.pad)
        self.env = {"KINO_SESSION": "1", "KINO_ALIVE_FIRST": "600", "KINO_INPUT_DEVICES": str(devices),
                    "KINO_INPUT_DIR": str(inp)}
        super().setUp()

    def press(self, code, value):
        os.write(self.pad, struct.pack("llHHi", 0, 0, 1, code, value) + struct.pack("llHHi", 0, 0, 0, 0, 0))
        time.sleep(0.4)

    def test_controller_in_the_player(self):
        self.play("m1", resume=False)
        self.press(304, 1)
        self.press(304, 0)
        self.assertTrue(self.backend.mpv("get_property", "pause"), "A is OK: pauses (and the video waits for the rest)")
        self.press(315, 1)
        self.press(315, 0)
        self.assertEqual(self.panel(), "info", "the Menu button opens the info panel")
        self.press(305, 1)
        self.press(305, 0)
        self.assertEqual(self.panel(), "", "B closes it")
        before = self.pos()
        os.write(self.pad, struct.pack("llHHi", 0, 0, 3, 16, 1) + struct.pack("llHHi", 0, 0, 0, 0, 0))  # D-pad right
        time.sleep(0.3)
        os.write(self.pad, struct.pack("llHHi", 0, 0, 3, 16, 0) + struct.pack("llHHi", 0, 0, 0, 0, 0))
        time.sleep(0.4)
        self.assertLess(abs(self.pos() - before), 1, "paused: the mark moves, playback waits for OK")
        self.press(304, 1)
        self.press(304, 0)
        wait_for(lambda: self.pos() > before + 8, timeout=3, msg="A: goes to the mark, 10 s on")
        self.press(305, 1)
        self.press(305, 0)
        self.wait_stopped(5)


class PlayerStillWatchingTest(PlayerBase):
    """After three episodes in a row without a key press Matinee asks before playing on."""
    # how long the question stays: long enough for the tests' look every tenth of a second to see it under the load of
    # the whole suite (3 s: once missed, and "asked" waited for in vain)
    env = {"KINO_ASK_TIMEOUT": "6"}

    def asking(self):
        skip = self.skip()
        return isinstance(skip, dict) and skip["label"].startswith("Still watching?")

    def test_asks_after_three_episodes_and_ok_plays_on(self):
        self.play("e11", resume=False)
        wait_for(self.asking, timeout=40, msg="asked at the end of the third episode")
        self.assertEqual(self.status()["itemId"], "e13")
        time.sleep(1.5)
        self.assertEqual(self.status()["itemId"], "e13", "does not play on by itself")
        self.key("ENTER")
        wait_for(lambda: self.status()["itemId"] == "e14", timeout=3, msg="OK starts the next episode at once")
        self.assertFalse(self.asking())

    def test_no_answer_stops_playback(self):
        self.play("e11", resume=False)
        wait_for(self.asking, timeout=40, msg="asked")
        self.wait_stopped(10)
        self.assertTrue(self.mock.lib.userdata["e13"]["Played"])
        self.assertNotIn("e14", [b["ItemId"] for b in self.mock.reports_for("/Playing")])

    def test_any_key_counts_as_watching(self):
        self.play("e11", resume=False)
        wait_for(lambda: self.status()["itemId"] == "e12", timeout=15, msg="second episode")
        self.key("m")  # mute: someone is there
        self.key("m")
        wait_for(self.asking, timeout=40, msg="asked")
        self.assertEqual(self.status()["itemId"], "e14", "three episodes after the key press, not after the start")


class PlayerAskSettingTest(PlayerBase):
    """ "ask" in config.json: after how many episodes, or never."""
    env = {"KINO_ASK_TIMEOUT": "30"}
    conf = {"ask": 2}

    def test_asks_after_two_episodes(self):
        self.play("e11", resume=False)
        wait_for(lambda: isinstance(self.skip(), dict) and self.skip()["label"].startswith("Still"), timeout=30, msg="asked")
        self.assertEqual(self.status()["itemId"], "e12")

    def test_asks_when_the_next_episode_was_not_found_at_its_start(self):
        # the server away while the second episode started: its next one looked up at its end, and asked for all the same
        self.play("e11", resume=False)
        wait_for(lambda: self.status().get("itemId") == "e11", timeout=15, msg="first episode")
        self.mock.busy_next["/Shows/s1/Episodes"] = 1  # the second episode's lookup of its next one
        wait_for(lambda: self.status()["itemId"] == "e12", timeout=30, msg="second episode")
        self.assertEqual(self.mock.busy_next["/Shows/s1/Episodes"], 0, "looked up as the second episode started")
        wait_for(lambda: isinstance(self.skip(), dict) and self.skip()["label"].startswith("Still"), timeout=30, msg="asked")
        self.assertEqual(self.status()["itemId"], "e12")


class PlayerAskOffTest(PlayerBase):
    conf = {"ask": "off"}

    def test_never_asks(self):
        self.play("e11", resume=False)
        wait_for(lambda: self.status()["itemId"] == "e14", timeout=45, msg="fourth episode without asking")


class PlayerLongPauseTest(PlayerBase):
    """A movie paused for a long time stops, so its picture does not stand still on the TV for hours."""
    speed = 1
    env = {"KINO_PAUSE_LIMIT": "2"}

    def test_long_pause_stops_playback(self):
        self.play("m1", resume=False)
        time.sleep(1.5)
        self.key("ENTER")
        self.assertTrue(self.backend.mpv("get_property", "pause"))
        self.wait_stopped(8)
        stop = self.mock.reports_for("Stopped")[-1]
        self.assertGreater(stop["PositionTicks"], TICKS, "the position is kept in Jellyfin")


class PlayerLongPauseSaverOffTest(PlayerBase):
    speed = 1
    env = {"KINO_PAUSE_LIMIT": "2"}
    conf = {"screensaver": "off"}

    def test_long_pause_keeps_playing_without_screensaver(self):
        self.play("m1", resume=False)
        self.key("ENTER")
        time.sleep(4)
        self.assertTrue(self.status()["playing"])
        self.key("GO_BACK")
        self.wait_stopped(5)


class PlayerRealtimeTest(PlayerBase):
    """Normal speed for positions and keys."""
    speed = 1
    conf = {"subtitles": "auto"}

    def test_resume_at_stored_position(self):
        self.mock.lib.userdata["m2"]["PlaybackPositionTicks"] = 10 * TICKS
        self.play("m2", resume=True)
        self.assertGreaterEqual(self.pos(), 9.5)
        self.key("GO_BACK")
        self.wait_stopped()
        self.assertTrue(self.mock.reports_for("Stopped"), "final position reported before the status says finished")
        stop = self.mock.reports_for("Stopped")[-1]
        self.assertTrue(10 * TICKS <= stop["PositionTicks"] < 14 * TICKS, stop["PositionTicks"])
        self.assertFalse(self.mock.lib.userdata["m2"]["Played"], "stopped halfway is not watched")

    def test_stopped_told_when_kino_ends_while_it_plays(self):
        """Matinee ended while a film plays (Cmd+Q, an update, switching off): Jellyfin is told it stopped, and where."""
        self.mock.lib.userdata["m2"]["PlaybackPositionTicks"] = 10 * TICKS
        self.play("m2", resume=True)
        self.assertGreaterEqual(self.pos(), 9.5)
        time.sleep(1.2)  # the backend looks where it is about once a second
        self.backend.proc.terminate()
        self.backend.proc.wait(10)
        stops = [b for b in self.mock.reports_for("Stopped") if b["ItemId"] == "m2"]
        self.assertEqual(len(stops), 1, "told once")
        self.assertTrue(10 * TICKS <= stops[0]["PositionTicks"] < 14 * TICKS, stops[0]["PositionTicks"])

    def test_stop_kept_when_jellyfin_is_too_slow_as_kino_ends(self):
        """Matinee ended while a film plays and Jellyfin slow to take the stop report: kept, sent at the next start."""
        self.mock.lib.userdata["m2"]["PlaybackPositionTicks"] = 10 * TICKS
        self.play("m2", resume=True)
        time.sleep(1.2)
        self.mock.stopped_delay = 10  # longer than Matinee waits (KEEP_NOW_WAIT, 3 s)
        self.backend.proc.terminate()
        self.backend.proc.wait(10)
        pending = json.loads((self.backend.conf / "pending.json").read_text())
        self.assertEqual([(r["path"], r["body"]["ItemId"]) for r in pending], [("/Sessions/Playing/Stopped", "m2")])
        self.assertGreaterEqual(pending[0]["body"]["PositionTicks"], 10 * TICKS, "where it was")

    def test_start_over(self):
        self.mock.lib.userdata["m2"]["PlaybackPositionTicks"] = 10 * TICKS
        self.play("m2", resume=False)
        self.assertLess(self.pos(), 3)

    def test_a_show_taken_off_up_next_comes_back_when_played(self):
        self.assertEqual(self.backend.request("/forget", {"what": "nextup", "id": "s1"})[0], 200)
        self.play("e12", resume=False)
        self.assertEqual(self.backend.request("/hidden")[1]["nextup"], [])

    def test_english_audio_is_preferred(self):
        self.play("m1", resume=False)
        self.assertEqual(self.audio_lang(), "eng")

    def test_external_subtitles_are_loaded(self):
        self.play("m3", resume=False)
        subs = [t for t in self.backend.mpv("get_property", "track-list") if t["type"] == "sub"]
        self.assertTrue(any(t.get("external") for t in subs), subs)
        self.assertIn("ApiKey=", subs[0].get("external-filename", ""), "subtitles use ApiKey, not api_key")

    # ---------- Tracks and volume remembered ----------

    def track(self, kind, lang=None):
        return next(t for t in self.backend.mpv("get_property", "track-list")
                    if t["type"] == kind and (lang is None or t.get("lang") == lang))

    def sub_file(self):
        return (self.backend.mpv("get_property", "current-tracks/sub") or {}).get("external-filename", "")

    def stop(self):
        self.key("GO_BACK")
        self.wait_stopped(5)

    def test_audio_chosen_for_a_show_carries_over_and_is_remembered(self):
        self.play("e11", resume=False)
        self.assertEqual(self.audio_lang(), "eng")
        self.backend.mpv("set_property", "aid", self.track("audio", "deu")["id"])
        time.sleep(1.5)
        self.key("NEXT")
        wait_for(lambda: self.status()["itemId"] == "e12" and self.pos() is not None, msg="next episode")
        wait_for(lambda: self.audio_lang() == "deu", timeout=3, msg="German audio carried over")
        stop = [b for b in self.mock.reports_for("Stopped") if b["ItemId"] == "e11"][-1]
        self.assertEqual(stop["AudioStreamIndex"], 2, "Jellyfin is told which track played")
        self.stop()
        self.play("e21", resume=False)
        self.assertEqual(self.audio_lang(), "deu", "remembered for the show")
        self.stop()
        self.play("m1", resume=False)
        self.assertEqual(self.audio_lang(), "eng", "other titles keep the default")

    def test_audio_chosen_for_a_film_is_kept_for_it(self):
        self.play("m1", resume=False)
        self.assertEqual(self.audio_lang(), "eng")
        self.backend.mpv("set_property", "aid", self.track("audio", "deu")["id"])
        time.sleep(1.5)
        self.stop()
        self.play("m1", resume=True)
        self.assertEqual(self.audio_lang(), "deu", "goes on in the language chosen")
        self.stop()
        self.play("m2", resume=False)
        self.assertEqual(self.audio_lang(), "eng", "another film keeps the default")

    def test_subtitle_file_chosen_for_a_show_carries_over(self):
        self.play("e21", resume=False)
        self.assertEqual(self.sub_file(), "", "none at first")
        self.backend.mpv("set_property", "sid", self.track("sub")["id"])
        time.sleep(1.5)
        self.key("NEXT")
        wait_for(lambda: self.status()["itemId"] == "e22" and self.pos() is not None, msg="next episode")
        wait_for(lambda: "/Videos/e22/" in self.sub_file(), timeout=3, msg="its subtitle file picked")

    def test_subtitles_turned_off_stay_off(self):
        self.play("e21", resume=False)
        self.backend.mpv("set_property", "sid", self.track("sub")["id"])
        time.sleep(1.5)
        self.stop()
        self.play("e21", resume=False)
        wait_for(lambda: "/Videos/e21/" in self.sub_file(), timeout=3, msg="remembered across playbacks")
        self.backend.mpv("set_property", "sid", "no")
        time.sleep(1.5)
        self.stop()
        self.play("e22", resume=False)
        time.sleep(1.5)
        self.assertEqual(self.sub_file(), "", "off for the show now")

    def test_volume_is_kept_for_the_next_playback(self):
        self.play("m1", resume=False)
        self.key("VOLUME_DOWN")
        self.key("VOLUME_DOWN")
        self.assertEqual(self.backend.mpv("get_property", "volume"), 90)
        self.stop()
        self.play("m2", resume=False)
        self.assertEqual(self.backend.mpv("get_property", "volume"), 90)

    @unittest.skipUnless(sys.platform == "darwin", "an option of mpv on macOS only")
    def test_full_screen_without_a_space_of_its_own_on_a_mac(self):
        """macOS: the player fills the screen where it is, not in a Space of its own sliding in and out."""
        self.play("m1")
        self.assertIs(self.backend.mpv("get_property", "native-fs"), False)

    def test_remote_keys(self):
        self.play("m1", resume=False)
        self.key("ENTER")
        self.assertTrue(self.backend.mpv("get_property", "pause"), "OK pauses")
        self.key("PLAYPAUSE")
        self.assertFalse(self.backend.mpv("get_property", "pause"), "play/pause resumes")
        before = self.pos()
        self.key("RIGHT")
        wait_for(lambda: self.pos() > before + 8, timeout=3, msg="right skips 10 s forward, a moment after")
        self.key("LEFT")
        wait_for(lambda: self.pos() < before + 5, timeout=3, msg="left skips 10 s back")
        volume = self.backend.mpv("get_property", "volume")
        self.key("UP")
        wait_for(lambda: self.backend.mpv("get_property", "volume") == volume + 5, timeout=3, msg="up: louder at once")
        self.key("DOWN")
        wait_for(lambda: self.backend.mpv("get_property", "volume") == volume, timeout=3, msg="down: quieter")
        self.backend.mpv("keydown", "DOWN")  # held: on and on, a step every 0.2 s, not at the key's repeat rate
        time.sleep(1.2)
        self.backend.mpv("keyup", "DOWN")
        time.sleep(0.5)
        self.assertTrue(volume - 35 <= self.backend.mpv("get_property", "volume") <= volume - 15,
                        f"held a second: a few steps quieter, not {volume - self.backend.mpv('get_property', 'volume')}")
        self.backend.mpv("set_property", "volume", volume)
        self.assertEqual(self.panel(), "", "up and down open no panel")
        self.key("MENU")
        self.assertEqual(self.panel(), "info", "Menu opens the info panel")
        self.key("GO_BACK")
        self.assertEqual(self.panel(), "", "back only closes the panel")
        self.key("TAB")
        self.assertEqual(self.panel(), "info", "Tab too, for a keyboard without a menu key")
        self.key("TAB")
        self.assertEqual(self.panel(), "", "and closes it again")
        self.assertTrue(self.status()["playing"])
        self.key("GO_BACK")
        self.wait_stopped(5)

    def test_a_mouse_or_trackpad(self):
        """A click pauses and plays, a right click opens and closes the panel, the wheel is the volume, sideways it
        seeks, and a trackpad's many small steps make few."""
        self.play("m1", resume=False)
        self.key("MBTN_LEFT")
        self.assertTrue(self.backend.mpv("get_property", "pause"), "a click pauses")
        self.key("MBTN_LEFT")
        self.assertFalse(self.backend.mpv("get_property", "pause"), "and plays")
        volume = self.backend.mpv("get_property", "volume")
        self.key("WHEEL_UP")
        wait_for(lambda: self.backend.mpv("get_property", "volume") == volume + 5, timeout=3, msg="the wheel up: louder")
        self.key("WHEEL_DOWN")
        wait_for(lambda: self.backend.mpv("get_property", "volume") == volume, timeout=3, msg="down: quieter")
        start = time.monotonic()
        for _ in range(20):  # a trackpad's swipe: many at once
            self.backend.mpv("keypress", "WHEEL_DOWN")
        took = time.monotonic() - start
        time.sleep(0.5)
        # a step every 0.12 s at most (WHEEL_STEP in kino.lua): sent on a busy machine, the 20 take longer
        steps = 2 + int(took / 0.12)
        self.assertGreaterEqual(self.backend.mpv("get_property", "volume"), volume - 5 * min(steps, 19),
                                f"a step or two, not twenty ({took:.2f} s for the 20)")
        self.backend.mpv("set_property", "volume", volume)
        before = self.pos()
        self.key("WHEEL_RIGHT")
        wait_for(lambda: self.pos() > before + 8, timeout=3, msg="sideways: 10 s on, a moment after")
        self.key("MBTN_RIGHT")
        self.assertEqual(self.panel(), "info", "a right click: the info panel")
        self.key("WHEEL_UP")
        self.assertEqual(self.panel(), "info", "the wheel does not close it by the way")
        self.key("MBTN_RIGHT")
        self.assertEqual(self.panel(), "", "a right click again closes it")
        self.assertTrue(self.status()["playing"], "playing on")

    def test_going_on_on_another_device(self):
        """The info panel's "Continue on": the other Matinee of the account that is on; chosen, it ends here and goes on
        there from where it was, told through Jellyfin."""
        other = Backend(self.mock.url, mpv_dir=self.mpv_dir, language=self.language).start()
        try:
            wait_for(lambda: len(self.mock.ws_devices) == 2, msg="both connected to Jellyfin")
            self.play("m1", resume=False)
            wait_for(lambda: self.backend.mpv("get_property", "user-data/kino/handoff"), timeout=5, msg="the other offered")
            self.assertEqual(len(self.backend.mpv("get_property", "user-data/kino/handoff")), 1, "not this one itself")
            time.sleep(1)  # a little way in: so early Jellyfin keeps no position
            self.mock.min_resume = 0.5
            self.key("MENU")
            for _ in range(6):
                if self.panel() == "handoff":
                    break
                self.key("RIGHT")
            self.assertEqual(self.panel(), "handoff", "its own tab, the last")
            self.key("DOWN")
            at = self.pos()
            self.key("ENTER")
            self.wait_stopped(10)
            wait_for(lambda: other.request("/status")[1]["playing"], timeout=10, msg="going on on the other")
            wait_for(lambda: other.mpv("get_property", "time-pos") is not None, timeout=5, msg="playing there")
            self.assertGreaterEqual(other.mpv("get_property", "time-pos"), at - 0.5, "from where it was, not the start")
            wait_for(lambda: len(self.mock.messages) == 2, timeout=5, msg="the other answers")
            self.assertEqual([h for _s, h, _t in self.mock.messages], ["kino-handoff", "kino-handoff-ack"],
                             "told through Jellyfin, and it says it goes on")
            time.sleep(1)
            self.assertFalse(self.status()["playing"], "not here again")
            self.assertIn("Going on on", self.backend.logtext())
        finally:
            other.stop()

    def test_going_on_only_for_a_kino_of_the_account(self):
        """Jellyfin names no sender (Jellyfin 12: all zeros): what is asked is done only for one of this user's Matinees."""
        wait_for(lambda: self.backend.device_id() in self.mock.ws_devices, msg="connected to Jellyfin")
        ask = lambda what, user="0" * 32: self.mock.ws_devices[self.backend.device_id()][0].append(
            {"MessageType": "GeneralCommand", "Data": {"Name": "DisplayMessage", "ControllingUserId": user,
                                                        "Arguments": {"Header": "kino-handoff", "Text": json.dumps(what)}}})
        ask({"kind": "jf", "id": "m1", "from": "stranger", "ack": "a1"})
        ask({"kind": "jf", "id": "m1"})  # no Matinee's
        ask({"kind": "jf", "id": "m1", "from": self.backend.device_id(), "ack": "a2"}, user="f" * 32)  # another user's
        wait_for(lambda: "stranger is not a Matinee of this account" in self.backend.logtext(), timeout=5, msg="looked at")
        time.sleep(1)
        self.assertFalse(self.status()["playing"], "nothing played")
        self.assertEqual(self.mock.messages, [], "and nobody answered")

    # ---------- Jellyfin's remote control ----------

    def remote(self, kind, data):
        """A message of Jellyfin's remote control (its web app's cast menu, a phone app) to this Matinee's session."""
        wait_for(lambda: self.backend.device_id() in self.mock.ws_devices, msg="connected to Jellyfin")
        self.mock.ws_devices[self.backend.device_id()][0].append({"MessageType": kind, "Data": data})

    def test_jellyfin_remote_control_in_the_player(self):
        """Its keys as the remote's, seeking, the audio and subtitles by Jellyfin's index, stop."""
        self.play("e21", resume=False)
        self.remote("GeneralCommand", {"Name": "Select"})
        wait_for(lambda: self.backend.mpv("get_property", "pause") is True, timeout=3, msg="OK pauses")
        self.remote("Playstate", {"Command": "Unpause"})
        wait_for(lambda: self.backend.mpv("get_property", "pause") is False, timeout=3, msg="plays on")
        self.remote("Playstate", {"Command": "Seek", "SeekPositionTicks": 12 * TICKS})
        wait_for(lambda: self.pos() >= 11.5, timeout=3, msg="gone to where it says")
        self.remote("GeneralCommand", {"Name": "SetAudioStreamIndex", "Arguments": {"Index": "2"}})
        wait_for(lambda: self.audio_lang() == "deu", timeout=3, msg="the German audio, stream 2")
        self.remote("GeneralCommand", {"Name": "SetSubtitleStreamIndex", "Arguments": {"Index": "3"}})
        wait_for(lambda: "/Videos/e21/" in self.sub_file(), timeout=3, msg="its subtitle file, stream 3")
        self.remote("GeneralCommand", {"Name": "SetSubtitleStreamIndex", "Arguments": {"Index": "-1"}})
        wait_for(lambda: self.sub_file() == "", timeout=3, msg="subtitles off")
        self.remote("GeneralCommand", {"Name": "ToggleContextMenu"})
        wait_for(lambda: self.panel() != "", timeout=3, msg="the info panel opened")
        self.remote("GeneralCommand", {"Name": "Back"})
        wait_for(lambda: self.panel() == "", timeout=3, msg="and closed")
        self.remote("Playstate", {"Command": "Stop"})
        self.wait_stopped(5)
        self.assertNotIn("Remote control:", self.backend.logtext(), "every command done")

    def test_jellyfin_play_on_this_kino(self):
        """"Play on": a film from where it says; a show itself is nothing the player plays."""
        self.remote("Play", {"ItemIds": ["s1"], "PlayCommand": "PlayNow"})
        wait_for(lambda: "a Series is not played here" in self.backend.logtext(), timeout=5, msg="looked at")
        self.assertFalse(self.status()["playing"])
        self.remote("Play", {"ItemIds": ["m1"], "StartPositionTicks": 6 * TICKS, "PlayCommand": "PlayNow"})
        wait_for(lambda: self.status()["playing"] and self.backend.mpv_socket()
                 and (self.pos() or 0) >= 5.5, msg="m1 plays from 6 s")
        self.assertEqual(self.status()["itemId"], "m1")

    def test_a_handoff_come_too_late_is_left(self):
        """A device whose Matinee stalled takes the message only later, once the other has gone on by itself: it leaves
        it, rather than both playing."""
        other = Backend(self.mock.url, mpv_dir=self.mpv_dir, language=self.language).start()
        try:
            wait_for(lambda: len(self.mock.ws_devices) == 2, msg="both connected to Jellyfin")
            self.mock.ws_devices[self.backend.device_id()][0].append(
                {"MessageType": "GeneralCommand", "Data": {"Name": "DisplayMessage", "ControllingUserId": "0" * 32,
                 "Arguments": {"Header": "kino-handoff", "Text": json.dumps(
                     {"kind": "jf", "id": "m1", "pos": 3, "from": other.device_id(), "ack": "late1",
                      "until": time.time() - 10})}}})
            wait_for(lambda: "gone on there by now" in self.backend.logtext(), timeout=5, msg="seen and left")
            time.sleep(1)
            self.assertFalse(self.status()["playing"], "not played here")
            self.assertEqual(self.mock.messages, [], "nor answered")
        finally:
            other.stop()

    def test_going_on_here_when_the_other_does_not_answer(self):
        """A device still among Jellyfin's sessions that is no longer there (a laptop closed): Jellyfin takes the
        message all the same, nobody answers, and it goes on here from where it was."""
        other = Backend(self.mock.url, mpv_dir=self.mpv_dir, language=self.language).start()
        try:
            wait_for(lambda: len(self.mock.ws_devices) == 2, msg="both connected to Jellyfin")
            self.play("m1", resume=False)
            wait_for(lambda: self.backend.mpv("get_property", "user-data/kino/handoff"), timeout=5, msg="the other offered")
            self.mock.deaf.update(did for did in self.mock.ws_devices if did != self.backend.device_id())
            time.sleep(1)
            self.mock.min_resume = 0.5  # so early Jellyfin keeps no position
            self.key("MENU")
            for _ in range(6):
                if self.panel() == "handoff":
                    break
                self.key("RIGHT")
            self.key("DOWN")
            self.key("ENTER")
            self.wait_stopped(10)
            # KINO_HANDOFF_ACK_WAIT: 2 s here
            wait_for(lambda: self.status()["playing"], timeout=15, msg="going on here again")
            wait_for(lambda: self.backend.mpv("get_property", "time-pos") is not None, timeout=5, msg="playing")
            self.assertGreater(self.backend.mpv("get_property", "time-pos"), 0.8, "from where it was, not the start")
            self.assertIn("no answer in 2 s", self.backend.logtext())
            self.assertFalse(other.request("/status")[1]["playing"])
        finally:
            other.stop()

    def test_info_panel(self):
        """Menu: the info panel on Info; left and right through its tabs, down into a list, OK picks, up back out."""
        self.play("e21", resume=False)
        self.key("MENU")
        self.assertEqual(self.panel(), "info")
        self.assertEqual(self.backend.mpv("get_property", "user-data/kino/ui")["subtitles"], "Subtitles")
        self.assertTrue(self.backend.mpv("get_property", "user-data/kino/about")["title"], "the episode's name")
        for _ in range(3):  # to the subtitles (chapters only when the file has them)
            if self.panel() != "subtitles":
                self.key("RIGHT")
        self.assertEqual(self.panel(), "subtitles")
        self.key("DOWN")  # "Off"
        self.key("DOWN")  # the first subtitles
        self.key("ENTER")
        wait_for(lambda: self.backend.mpv("get_property", "current-tracks/sub"), timeout=3, msg="subtitles on")
        self.assertEqual(self.panel(), "subtitles", "still open, to see the choice")
        self.key("UP")
        self.key("ENTER")  # on "Off"
        wait_for(lambda: not self.backend.mpv("get_property", "current-tracks/sub"), timeout=3, msg="subtitles off")
        self.key("UP")
        self.key("RIGHT")  # the last tab: the speed
        self.assertEqual(self.panel(), "speed")
        for _ in range(5):  # 0.5×, 0.75×, Normal, 1.25×, 1.5×
            self.key("DOWN")
        self.key("ENTER")
        wait_for(lambda: self.backend.mpv("get_property", "speed") == 1.5, timeout=3, msg="at 1.5×")
        for _ in range(5):
            self.key("UP")
        self.key("UP")  # from the tabs: closed
        self.assertEqual(self.panel(), "")
        self.assertTrue(self.status()["playing"])

    def test_subtitles_found_while_watching(self):
        """The subtitles tab's last row: subtitles from the internet, the language set first; OK downloads and picks."""
        self.play("m2", resume=False)
        self.key("MENU")
        for _ in range(3):
            if self.panel() != "subtitles":
                self.key("RIGHT")
        self.assertEqual(self.panel(), "subtitles")
        self.key("DOWN")  # "Off"
        self.key("DOWN")  # "Find Subtitles…": the film has none of its own
        self.key("ENTER")
        wait_for(lambda: (self.backend.mpv("get_property", "user-data/kino/subfind") or {}).get("state") == "done",
                 timeout=5, msg="subtitles found")
        found = self.backend.mpv("get_property", "user-data/kino/subfind")["items"]
        self.assertEqual([x["name"] for x in found], ["Movie.1.German.srt", "Bad id"], "none in English")
        self.assertEqual(found[0]["extra"], "Deutsch · Open Subtitles")
        self.key("ENTER")  # on the first found
        self.assertEqual(self.panel(), "", "closed")
        wait_for(lambda: (self.backend.mpv("get_property", "current-tracks/sub") or {}).get("lang") == "ger",
                 timeout=8, msg="downloaded and picked")
        self.assertEqual(self.mock.subs_downloaded, [("m2", "os-ger-1")])
        self.assertTrue(self.status()["playing"])

    def test_pictures_while_seeking(self):
        """With Jellyfin's trickplay pictures: the one where the mark is, cut out of its tile, shown over it."""
        tile = Path(self.backend.tmp) / "tile.jpg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(self.video), "-vf", "fps=1/10,scale=320:180,tile=2x1",
                        "-frames:v", "1", str(tile)], check=True)
        self.mock.trickplay_tile = tile.read_bytes()
        self.mock.lib.trickplay = True
        self.play("m1", resume=False)
        tp = self.backend.mpv("get_property", "user-data/kino/trickplay")
        self.assertEqual((tp["interval"], tp["count"]), (10000, 2))
        self.key("ENTER")
        self.key("RIGHT")  # paused: the mark at 10 s, the second picture
        wait_for(lambda: self.backend.mpv("get_property", "user-data/kino/preview") == 1, timeout=5, msg="its picture shown")
        self.assertEqual(len(self.mock.trickplay_requests), 1)
        self.assertEqual(Path(tp["dir"], "216-1.bgra").stat().st_size, 384 * 216 * 4, "raw BGRA, a fifth of 1080 high")
        self.key("GO_BACK")
        self.assertEqual(self.backend.mpv("get_property", "user-data/kino/preview"), "", "gone with the mark")
        self.key("GO_BACK")
        self.wait_stopped(5)
        wait_for(lambda: not Path(tp["dir"]).exists(), timeout=3, msg="the pictures removed after playback")

    def test_a_playback_held_up_leaves_the_next_ones_pictures(self):
        """The playback before, held up by a slow server (a progress report on its way) for longer than the next start
        waits for it (10 s): when it ends at last, it clears its own pictures for seeking, not the new playback's."""
        tile = Path(self.backend.tmp) / "tile.jpg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(self.video), "-vf", "fps=1/10,scale=320:180,tile=2x1",
                        "-frames:v", "1", str(tile)], check=True)
        self.mock.trickplay_tile = tile.read_bytes()
        self.mock.lib.trickplay = True
        self.play("m1", resume=False)
        self.mock.progress_delay = 14
        wait_for(self.mock.progress_held.is_set, timeout=15, msg="a progress report held up")
        self.mock.progress_delay = 0  # the new playback's go through at once
        held = lambda: [b for b in self.mock.reports_for("Progress") if b["ItemId"] == "m1"]  # noqa: E731
        before, started = len(held()), len(self.mock.reports_for("/Playing"))

        def play_next():  # the answer comes after the start's wait for the playback before: longer than request's
            try:
                self.backend.request("/play", {"itemId": "m2", "resume": False})
            except OSError:
                pass
        threading.Thread(target=play_next, daemon=True).start()
        wait_for(lambda: len(self.mock.reports_for("/Playing")) > started, timeout=20, msg="the next playback started")
        wait_for(lambda: (self.backend.mpv("get_property", "user-data/kino/trickplay") or {}).get("dir"), msg="its pictures")
        tp = self.backend.mpv("get_property", "user-data/kino/trickplay")
        wait_for(lambda: len(held()) > before, timeout=15, msg="the held report taken at last")
        time.sleep(1.5)  # the playback before ending meanwhile
        self.assertTrue(self.status()["playing"])
        self.assertTrue(Path(tp["dir"]).is_dir(), "the new playback's pictures still there")

    def test_seeking_while_paused(self):
        """Paused, the arrows move a mark only; OK goes there and plays, Back leaves it where it was."""
        self.play("m1", resume=False)
        self.key("ENTER")
        start = self.pos()
        self.key("RIGHT")
        self.key("RIGHT")
        time.sleep(1)
        self.assertAlmostEqual(self.pos(), start, delta=0.5, msg="not yet")
        self.key("ENTER")
        wait_for(lambda: self.pos() > start + 18, timeout=3, msg="20 s further with OK")
        self.assertFalse(self.backend.mpv("get_property", "pause"), "playing there")
        self.key("ENTER")
        start = self.pos()
        self.key("LEFT")
        self.key("GO_BACK")
        time.sleep(1)
        self.assertAlmostEqual(self.pos(), start, delta=0.5, msg="Back: where it was")
        self.assertTrue(self.status()["playing"], "Back only ended the seek")

    def test_skip_intro(self):
        # intro of the test episodes: 2 to 8 seconds
        self.play("e11", resume=False)
        wait_for(lambda: isinstance(self.skip(), dict), timeout=6, msg="offer during the intro")
        self.assertEqual(self.skip()["label"], "Skip Intro")
        self.key("ENTER")
        self.assertGreaterEqual(self.pos(), 7.8, "jumped to the end of the intro")
        self.assertFalse(self.backend.mpv("get_property", "pause"), "OK did not pause")
        wait_for(lambda: not self.skip(), timeout=3, msg="offer gone after the intro")
        self.key("ENTER")
        self.assertTrue(self.backend.mpv("get_property", "pause"), "outside the intro OK pauses again")

    def test_credits_go_to_next_episode(self):
        # credits of the test episodes: 15 to 19.6 seconds
        self.play("e12", resume=False)
        self.backend.mpv("seek", 15.5, "absolute")
        wait_for(lambda: isinstance(self.skip(), dict) and self.skip()["label"] == "Next Episode", timeout=4,
                 msg="offer during the credits")
        self.key("ENTER")
        wait_for(lambda: self.status()["itemId"] == "e13", timeout=4, msg="next episode right away")

    def test_stopped_in_the_credits_is_watched(self):
        self.play("e12", resume=False)
        self.backend.mpv("seek", 15.5, "absolute")  # in the credits, at 77 %: Jellyfin itself only counts 90 %
        wait_for(lambda: isinstance(self.skip(), dict), timeout=4, msg="in the credits")
        self.key("ESC")
        self.wait_stopped(5)
        wait_for(lambda: self.mock.lib.userdata["e12"]["Played"], timeout=5, msg="watched, off Continue Watching")

    def test_last_episode_offers_no_next_episode(self):
        self.play("e24", resume=False)
        self.backend.mpv("seek", 15.5, "absolute")
        time.sleep(2)
        self.assertFalse(self.skip(), "there is no next episode to go to")
        self.key("ENTER")
        self.assertTrue(self.backend.mpv("get_property", "pause"), "OK pauses as usual")

    def test_movies_have_no_skip_offers(self):
        self.play("m1", resume=False)
        time.sleep(3)
        self.assertFalse(self.skip())

    def test_next_key_skips_to_next_episode(self):
        self.play("e21", resume=False)
        self.key("NEXT")
        wait_for(lambda: self.status()["itemId"] == "e22", timeout=4, msg="next episode right away, no countdown")
        stop = [b for b in self.mock.reports_for("Stopped") if b["ItemId"] == "e21"][0]
        self.assertLess(stop["PositionTicks"], 5 * TICKS)
        self.assertFalse(self.mock.lib.userdata["e21"]["Played"], "skipped is not watched")
        self.assertLess(wait_for(lambda: self.pos() is not None and self.pos() + 0.001), 3, "new episode starts at the beginning")

    def test_new_playback_replaces_running_one(self):
        self.play("m1", resume=False)
        self.play("m2", resume=False)
        self.assertEqual(self.status()["itemId"], "m2")
        self.assertIn("m1", [b["ItemId"] for b in self.mock.reports_for("Stopped")])
        self.assertEqual(len(list(self.backend.runtime.glob("kino-mpv-*.sock"))), 1)


class PlayerSkipIntroAutoTest(PlayerBase):
    """ "skipintro": "auto": an intro skipped by itself, once; going back into it offers it on OK."""
    speed = 1
    conf = {"skipintro": "auto"}

    def test_intro_skipped_by_itself(self):
        # intro of the test episodes: 2 to 8 seconds
        self.play("e11", resume=False)
        wait_for(lambda: self.pos() >= 7.8, timeout=6, msg="past the intro without a key")
        self.assertLess(self.pos(), 9.5, "jumped, not played through")
        self.backend.mpv("seek", 3, "absolute")
        wait_for(lambda: isinstance(self.skip(), dict), timeout=3, msg="offered on OK when going back into it")
        self.assertLess(self.pos(), 7, "not skipped a second time")


class PlayerSkipIntroOffTest(PlayerBase):
    speed = 1
    conf = {"skipintro": "off"}

    def test_no_intro_offer(self):
        self.play("e11", resume=False)
        wait_for(lambda: self.pos() >= 4, timeout=6, msg="in the intro")
        self.assertFalse(self.skip(), "nothing offered")
        self.backend.mpv("seek", 15.5, "absolute")
        wait_for(lambda: isinstance(self.skip(), dict), timeout=4, msg="the next episode still offered in the credits")


class PlayerAudioSyncTest(PlayerBase):
    """ "audiosync": the sound earlier on the Bluetooth speaker it is set for, not on another output."""

    def setUp(self):
        bin_dir = Path(temp_dir("kino-wpctl-"))
        self.sink = bin_dir / "sink"
        self.sink.write_text("34:0E:22:00:00:09")
        # a stand-in for PipeWire's wpctl: the sound output in use is the Bluetooth speaker in the file, or HDMI
        (bin_dir / "wpctl").write_text(f"""#!/bin/sh
[ "$1" = inspect ] || exit 1
mac=$(cat {self.sink})
if [ -n "$mac" ]; then
  echo '    api.bluez5.address = "'"$mac"'"'; echo '  * node.description = "Living Room Speaker"'
else
  echo '  * node.description = "Built-in Audio Digital Stereo (HDMI)"'
fi
""")
        (bin_dir / "wpctl").chmod(0o755)
        self.env = {"PATH": f"{bin_dir}:{self.mpv_dir}:{os.environ['PATH']}"}
        super().setUp()

    def test_sound_earlier_on_the_speaker_set(self):
        self.assertEqual(self.backend.request("/settings")[1]["audiosync"],
                         {"id": "34:0E:22:00:00:09", "name": "Living Room Speaker", "ms": 0})
        self.assertEqual(self.backend.request("/settings", {"audiosync": {"device": "34:0E:22:00:00:09", "ms": "150"}})[0], 200)
        self.assertEqual(self.backend.request("/settings")[1]["audiosync"]["ms"], 150)
        self.assertEqual(self.backend.request("/settings", {"audiosync": {"device": "34:0E:22:00:00:09", "ms": "120"}})[0],
                         400, "only the steps offered")
        self.assertEqual(self.backend.request("/settings", {"audiosync": {"device": "x; rm", "ms": "50"}})[0], 400)
        self.play("m1", resume=False)
        self.assertAlmostEqual(self.backend.mpv("get_property", "audio-delay"), -0.15)
        self.key("GO_BACK")
        self.wait_stopped(5)
        self.sink.write_text("")  # the TV's own sound
        self.assertEqual(self.backend.request("/settings")[1]["audiosync"]["id"], "default")
        self.play("m1", resume=False)
        self.assertEqual(self.backend.mpv("get_property", "audio-delay"), 0)


class PlayerSubtitleBoxTest(PlayerBase):
    """ "substyle": "box": the subtitles on a dark box, without their outline."""
    conf = {"substyle": "box"}

    def test_subtitles_on_a_box(self):
        self.play("m1", resume=False)
        self.assertEqual(self.backend.mpv("get_property", "sub-border-style"), "background-box")
        self.assertEqual(self.backend.mpv("get_property", "sub-outline-color"), "#00000000")
        self.key("GO_BACK")
        self.wait_stopped(5)
        self.assertEqual(self.backend.request("/settings", {"substyle": "outline"})[0], 200)
        self.play("m1", resume=False)
        self.assertEqual(self.backend.mpv("get_property", "sub-border-style"), "outline-and-shadow", "as before")


class PlayerSlowLineTest(PlayerBase):
    """A line too slow for the file (8 Mbit/s): Jellyfin transcodes to what the line can carry."""
    speed = 1
    conf = {"subtitles": "auto"}

    def setUp(self):
        super().setUp()
        self.mock.stream_rate = 40_000  # 0.32 Mbit/s

    def test_slow_line_gets_a_transcoded_stream(self):
        start = time.time()
        self.play("m1", resume=False)
        self.assertLess(time.time() - start, 8, "measuring the line takes a moment, not long")
        tc = self.mock.transcodes[-1]
        self.assertEqual(tc["VideoCodec"], "h264")
        self.assertEqual(tc["AudioStreamIndex"], "1", "English audio, the only track in the transcoded stream")
        self.assertLessEqual(int(tc["VideoBitrate"]) + int(tc["AudioBitrate"]), 410_000, "fits the line, at least 0.4 Mbit/s")
        self.assertIn("master.m3u8", self.backend.mpv("get_property", "path"))
        self.assertIn("ApiKey=", self.backend.mpv("get_property", "path"))
        playing = self.mock.reports_for("/Playing")[-1]
        self.assertEqual(playing["PlayMethod"], "Transcode")
        self.assertEqual(playing["PlaySessionId"], tc["PlaySessionId"], "Jellyfin can match reports and transcode")
        wait_for(lambda: self.pos() > 1, timeout=10, msg="transcoded stream plays")
        self.key("GO_BACK")
        self.wait_stopped()
        self.assertEqual(self.mock.stopped_encodings, [tc["PlaySessionId"]], "transcode on the server stopped")

    def test_pressing_play_twice_starts_once(self):
        threads = [threading.Thread(target=self.backend.request, args=("/play", {"itemId": "m1", "resume": False}))
                   for _ in range(2)]
        for th in threads:
            th.start()
            time.sleep(0.3)  # the second press arrives while the line is still being measured
        for th in threads:
            th.join()
        wait_for(lambda: self.mock.reports_for("/Playing"), msg="start reported")
        time.sleep(1)
        self.assertEqual(len(self.mock.reports_for("/Playing")), 1)
        self.assertEqual(len({tc["PlaySessionId"] for tc in self.mock.transcodes}), 1, "one transcode on the server")

    def test_subtitles_turned_off_while_converted_stay_off(self):
        sub_file = lambda: (self.backend.mpv("get_property", "current-tracks/sub") or {}).get("external-filename", "")
        self.mock.stream_rate = 0  # chosen once on a fast line
        self.play("m3", resume=False)
        track = next(t for t in self.backend.mpv("get_property", "track-list") if t["type"] == "sub")
        self.backend.mpv("set_property", "sid", track["id"])
        time.sleep(1.5)
        self.key("GO_BACK")
        self.wait_stopped()
        self.mock.stream_rate = 40_000
        self.play("m3", resume=False)
        self.assertIn("master.m3u8", self.backend.mpv("get_property", "path"))
        wait_for(lambda: "/Videos/m3/" in sub_file(), timeout=3, msg="the chosen subtitles, also when converted")
        self.backend.mpv("set_property", "sid", "no")
        time.sleep(1.5)
        self.key("GO_BACK")
        self.wait_stopped()
        self.play("m3", resume=False)
        time.sleep(1.5)
        self.assertEqual(sub_file(), "", "turned off while converted: off next time")

    def test_resume_works_with_transcoding(self):
        self.mock.lib.userdata["m2"]["PlaybackPositionTicks"] = 10 * TICKS
        self.play("m2", resume=True)
        self.assertGreaterEqual(self.pos(), 9.5)


class PlayerQualityCapTest(PlayerBase):
    """"quality": 2 caps the bitrate at 2 Mbit/s without measuring."""
    speed = 1
    conf = {"quality": 2}

    def test_cap_transcodes_without_measuring(self):
        self.play("m1", resume=False)
        tc = self.mock.transcodes[-1]
        self.assertEqual(int(tc["VideoBitrate"]) + int(tc["AudioBitrate"]), 2_000_000)
        self.assertEqual(self.mock.stream_requests, 0, "no measuring, no direct stream")


class PlayerOriginalQualityTest(PlayerBase):
    speed = 1
    conf = {"quality": "original"}

    def test_original_never_transcodes(self):
        self.mock.stream_rate = 40_000
        self.play("m1", resume=False)
        self.assertEqual(self.mock.transcodes, [])


class PlayerAudioSettingTest(PlayerBase):
    """ "audio" and "subtitles" in the settings: German audio and no subtitles in an English Matinee."""
    conf = {"audio": "de", "subtitles": "off", "sublang": "de"}

    def test_german_audio_without_subtitles(self):
        self.play("e21", resume=False)
        self.assertEqual(self.audio_lang(), "deu")
        time.sleep(1)
        self.assertIsNone(self.backend.mpv("get_property", "current-tracks/sub"), "subtitles off")


class PlayerOriginalAudioTest(PlayerBase):
    """ "original": the file's own default track, here the English first one, even in a German Matinee."""
    language = "de"
    conf = {"audio": "original"}

    def test_original_audio(self):
        self.play("m1", resume=False)
        self.assertEqual(self.audio_lang(), "eng")


class PlayerSubtitlesOffTest(PlayerBase):
    """Subtitles off, as without a setting: every start without them, whatever was chosen for the title before."""
    conf = {"audio": "de", "sublang": "de"}

    def sub_file(self):
        return (self.backend.mpv("get_property", "current-tracks/sub") or {}).get("external-filename", "")

    def test_turned_on_for_one_playback_only(self):
        self.play("e21", resume=False)
        self.assertEqual(self.sub_file(), "", "none at first")
        track = next(t for t in self.backend.mpv("get_property", "track-list") if t["type"] == "sub")
        self.backend.mpv("set_property", "sid", track["id"])
        time.sleep(1.5)
        self.key("GO_BACK")
        self.wait_stopped()
        self.play("e21", resume=False)
        time.sleep(1.5)
        self.assertEqual(self.sub_file(), "", "not kept for the next start")

    def test_a_choice_kept_from_before_does_not_count(self):
        tracks = self.backend.conf / "tracks.json"
        tracks.write_text(json.dumps({"s1": {"sub": "ger"}}))  # turned on while the setting was "auto"
        self.play("e21", resume=False)
        time.sleep(1.5)
        self.assertEqual(self.sub_file(), "")

    def test_no_forced_ones_either(self):
        self.mock.lib.forced_subs.add("e21")
        self.play("e21", resume=False)
        time.sleep(1.5)
        self.assertEqual(self.sub_file(), "")


class PlayerSubtitleSettingTest(PlayerBase):
    """German subtitles (a file, whose language only Jellyfin knows) for English audio, and "always"."""
    conf = {"subtitles": "auto", "sublang": "de"}

    def sub_file(self):
        return (self.backend.mpv("get_property", "current-tracks/sub") or {}).get("external-filename", "")

    def test_subtitles_in_another_language_than_the_audio(self):
        self.play("e21", resume=False)
        self.assertEqual(self.audio_lang(), "eng")
        wait_for(lambda: "/Videos/e21/" in self.sub_file(), timeout=3, msg="German subtitle file picked")

    def test_no_subtitles_in_the_language_of_the_audio_unless_always(self):
        self.assertEqual(self.backend.request("/settings", {"audio": "de"})[0], 200)
        self.play("e21", resume=False)
        self.assertEqual(self.audio_lang(), "deu")
        time.sleep(1.5)
        self.assertEqual(self.sub_file(), "", "German audio: no German subtitles")
        self.key("GO_BACK")
        self.wait_stopped(5)
        self.assertEqual(self.backend.request("/settings", {"subtitles": "always"})[0], 200)
        self.play("e21", resume=False)
        wait_for(lambda: "/Videos/e21/" in self.sub_file(), timeout=3, msg="always: also for German audio")

    def test_converted_after_a_break_saves_no_choice(self):
        """Subtitles picked by themselves, then the stream breaks and comes back converted on a slow line: what the
        converted stream lacks is no one's choice of "no" for the show (as picture subtitles burned in)."""
        self.mock.stream_cut = self.video.stat().st_size // 2
        self.play("e21", resume=False)
        wait_for(lambda: "/Videos/e21/" in self.sub_file(), timeout=3, msg="German subtitle file picked")
        wait_for(lambda: self.mock.httpd.socket.fileno() == -1, timeout=30, msg="server gone")
        self.mock.stream_rate = 40_000
        self.mock.lib.subtitled.discard("e21")  # the converted stream comes without them
        self.mock.start()
        wait_for(lambda: self.mock.transcodes, timeout=30, msg="converted after the break")
        wait_for(lambda: self.status()["itemId"] != "e21", timeout=40, msg="on after the end")
        tracks = self.backend.conf / "tracks.json"
        saved = json.loads(tracks.read_text()) if tracks.exists() else {}
        self.assertNotEqual(saved.get("s1", {}).get("sub"), "no")

    def test_setting_changed_during_playback_counts_from_the_start(self):
        """Started with subtitles "off": turned on in the player, they are for this playback only, even when the setting
        is "auto" by the time it ends (changed on another device)."""
        self.assertEqual(self.backend.request("/settings", {"subtitles": "off"})[0], 200)
        self.play("e21", resume=False)
        time.sleep(1.5)
        track = next(t for t in self.backend.mpv("get_property", "track-list") if t["type"] == "sub")
        self.backend.mpv("set_property", "sid", track["id"])
        time.sleep(1.5)
        self.assertEqual(self.backend.request("/settings", {"subtitles": "auto"})[0], 200)
        self.key("GO_BACK")
        self.wait_stopped(5)
        tracks = self.backend.conf / "tracks.json"
        saved = json.loads(tracks.read_text()) if tracks.exists() else {}
        self.assertNotIn("sub", saved.get("s1", {}), "not kept for the show")


class PlayerUnreachableTest(PlayerBase):
    """An mpv that runs but cannot be reached (its socket could not be made) is stopped, and the UI told."""
    env = {"KINO_MPV_CONNECT": "2"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        real = shutil.which("mpv")
        (cls.mpv_dir / "mpv").write_text(  # mpv without its IPC socket
            "#!/bin/sh\nfor a; do shift; case \"$a\" in --input-ipc-server=*) ;; *) set -- \"$@\" \"$a\" ;; esac; done\n"
            f'exec {real} --vo=null --ao=null "$@"\n')

    def test_stopped_and_told(self):
        self.assertEqual(self.backend.request("/play", {"itemId": "m1", "resume": False})[0], 200)
        self.wait_stopped(timeout=10)
        self.assertEqual(self.status()["error"], "unreachable")
        self.assertIn("not reachable", self.backend.logtext())


class PlayerForcedSubtitleTest(PlayerBase):
    """Forced subtitles (a sign, a letter, a line in another language) come even with audio in their language."""
    conf = {"audio": "de", "subtitles": "auto", "sublang": "de"}

    def sub_file(self):
        return (self.backend.mpv("get_property", "current-tracks/sub") or {}).get("external-filename", "")

    def test_german_audio_shows_the_forced_ones_only(self):
        self.mock.lib.forced_subs.add("e21")
        self.play("e21", resume=False)
        self.assertEqual(self.audio_lang(), "deu")
        wait_for(lambda: "/Subtitles/6/" in self.sub_file(), timeout=3, msg="the forced German file, not the full one")

    def test_english_audio_takes_the_full_ones(self):
        self.mock.lib.forced_subs.add("e21")
        self.assertEqual(self.backend.request("/settings", {"audio": "en"})[0], 200)
        self.play("e21", resume=False)
        self.assertEqual(self.audio_lang(), "eng")
        wait_for(lambda: "/Subtitles/3/" in self.sub_file(), timeout=3, msg="the full German file")

    def test_converted_stream_sends_no_full_subtitles_in_the_language_of_the_audio(self):
        self.mock.stream_rate = 40_000  # a slow line: Jellyfin converts
        self.play("m3", resume=False)
        tc = self.mock.transcodes[-1]
        self.assertEqual(tc["AudioStreamIndex"], "2")
        time.sleep(1)
        self.assertEqual(self.sub_file(), "", "German audio: not the full German subtitles")
        self.key("GO_BACK")
        self.wait_stopped(5)
        self.mock.lib.forced_subs.add("m3")
        self.play("m3", resume=False)
        wait_for(lambda: "/Subtitles/6/" in self.sub_file(), timeout=3, msg="but the forced ones")


class PlayerFrameRateTest(PlayerBase):
    """In cage: a 25 fps movie plays at 50 Hz, the menus get their 120 Hz back (fake wlr-randr)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.mpv_dir / "wlr-randr").symlink_to(ROOT / "tests" / "fake-wlr-randr")
        cls.randr_log = Path(cls.tmp) / "randr.log"
        cls.env = {"KINO_SESSION": "1", "FAKE_RANDR_STATE": str(Path(cls.tmp) / "screen.json"),
                   "FAKE_RANDR_LOG": str(cls.randr_log)}

    def modes_set(self):
        calls = [json.loads(line) for line in self.randr_log.read_text().splitlines()] if self.randr_log.exists() else []
        return [c[c.index("--mode") + 1] for c in calls if "--mode" in c]

    def test_refresh_rate_follows_the_movie(self):
        self.assertEqual(self.backend.request("/settings", {"display": "1920x1080@120"})[0], 200)
        self.play("m1", resume=False)
        self.assertEqual(self.modes_set()[-1], "1920x1080@50.000Hz", "switched before mpv opened")
        self.key("GO_BACK")
        self.wait_stopped(5)
        wait_for(lambda: self.modes_set()[-1] == "1920x1080@120.000Hz", timeout=5, msg="menus at 120 Hz again")

    def test_off(self):
        self.assertEqual(self.backend.request("/settings", {"display": "1920x1080@120", "framerate": "off"})[0], 200)
        before = len(self.modes_set())
        self.play("m1", resume=False)
        self.key("GO_BACK")
        self.wait_stopped(5)
        self.assertEqual(len(self.modes_set()), before, "left alone")


class PlayerNaturalScrollTest(PlayerBase):
    """A desktop that scrolls naturally (a trackpad swiped up sends the wheel down): up is louder all the same."""
    env = {"KINO_NATURAL_SCROLL": "1"}

    def test_swiped_up_is_louder(self):
        self.play("m1", resume=False)
        volume = self.backend.mpv("get_property", "volume")
        self.key("WHEEL_DOWN")
        wait_for(lambda: self.backend.mpv("get_property", "volume") == volume + 5, timeout=3, msg="swiped up: louder")
        self.key("WHEEL_UP")
        wait_for(lambda: self.backend.mpv("get_property", "volume") == volume, timeout=3, msg="swiped down: quieter")


class PlayerSleepTest(PlayerBase):
    """The sleep timer in the player: S switches it, "at the end" stops after this episode."""
    env = {"KINO_SLEEP_UNIT": "0.05", "KINO_SLEEP_FADE": "0.5"}

    def test_at_the_end_of_the_episode(self):
        self.play("e11", resume=False)
        self.key("s")
        self.assertIn("left", self.backend.request("/status")[1]["sleep"], "30 minutes")
        for _ in range(3):  # 60, 90, at the end
            self.key("s")
        self.assertEqual(self.backend.request("/status")[1]["sleep"], {"end": True})
        self.wait_stopped(timeout=30)
        self.assertEqual(self.status()["itemId"], "e11", "no next episode")
        self.assertIsNone(self.status()["sleep"], "done")

    def test_fades_out_and_keeps_the_volume(self):
        self.play("m1", resume=False)
        self.backend.mpv("set_property", "volume", 80)
        time.sleep(0.5)
        self.backend.request("/sleep", {"mode": 30})
        self.wait_stopped(timeout=10)
        self.play("m2", resume=False)
        self.assertEqual(self.backend.mpv("get_property", "volume"), 80, "not the faded one")


class PlayerConvertTest(PlayerBase):
    """A video the graphics chip cannot decode (vainfo says so) is converted by Jellyfin to 1080p H.264."""
    conf = {"subtitles": "auto"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.mpv_dir / "vainfo").symlink_to(ROOT / "tests" / "fake-vainfo")

    def test_4k_hevc_10_bit_is_converted(self):
        self.play("m6", resume=False)
        tc = self.mock.transcodes[-1]
        self.assertEqual((tc["VideoCodec"], tc["MaxHeight"], tc["MaxWidth"]), ("h264", "1080", "1920"))
        self.assertLessEqual(int(tc["VideoBitrate"]), 12_000_000)
        self.assertEqual(self.backend.mpv("get_property", "osd-playing-msg"), "4K HDR HEVC 10 bit: converted by Jellyfin for this device")

    def test_subtitles_inside_a_converted_file_follow_the_picture(self):
        self.mock.subtitle_delay = 4  # Jellyfin reading the whole film for them
        t0 = time.monotonic()
        self.play("m6", resume=False)
        self.assertLess(time.monotonic() - t0, 4, "the film does not wait for them")
        self.backend.mpv("set_property", "pause", True)  # the short test video would end before they come
        subs = lambda: [t for t in self.backend.mpv("get_property", "track-list") if t["type"] == "sub"]
        self.assertEqual(subs(), [])
        wait_for(lambda: subs(), timeout=10, msg="subtitles added later")
        wait_for(lambda: self.backend.mpv("get_property", "current-tracks/sub"), msg="forced ones shown")
        self.assertEqual(subs()[0].get("lang"), "eng")
        self.assertEqual(list(Path(self.backend.runtime).glob("kino-sub-*")), [], "the file is gone once mpv has it")

    def test_subtitles_come_after_the_film_ended_are_not_kept(self):
        self.mock.subtitle_delay = 3
        self.play("m6", resume=False)
        self.backend.mpv("keypress", "ESC")  # stopped before they come
        self.wait_stopped(5)
        time.sleep(4)  # they come meanwhile
        self.assertEqual(list(Path(self.backend.runtime).glob("kino-sub-*")), [], "no file left in the runtime folder")

    def test_what_it_decodes_plays_as_it_is(self):
        before = len(self.mock.transcodes)
        self.play("m1", resume=False)
        self.assertEqual(len(self.mock.transcodes), before, "H.264: straight from the file")


class PlayerGermanTest(PlayerBase):
    """With language "de": German audio track and German overlays."""
    speed = 1
    language = "de"

    def test_german_audio_is_preferred(self):
        self.play("m1", resume=False)
        self.assertEqual(self.audio_lang(), "deu")

    def test_transcode_picks_german_audio_and_burns_in_picture_subtitles(self):
        self.mock.stream_rate = 40_000
        # German audio: full German subtitles only when always wanted (otherwise only forced ones)
        self.assertEqual(self.backend.request("/settings", {"subtitles": "always"})[0], 200)
        self.play("m5", resume=False)
        tc = self.mock.transcodes[-1]
        self.assertEqual(tc["AudioStreamIndex"], "2")
        self.assertEqual((tc.get("SubtitleStreamIndex"), tc.get("SubtitleMethod")), ("4", "Encode"))
        self.assertEqual(self.backend.mpv("get_property", "osd-playing-msg"),
                         "Langsame Verbindung: geringere Qualität (0.4 Mbit/s)")

    def test_german_skip_label(self):
        self.play("e11", resume=False)
        wait_for(lambda: isinstance(self.skip(), dict), timeout=6, msg="offer during the intro")
        self.assertEqual(self.skip()["label"], "Intro überspringen")


if __name__ == "__main__":
    unittest.main()
