"""Music from Navidrome: sign-in, the backend's Subsonic proxy and the music player, against a fake Navidrome."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import HAVE_FFMPEG, HAVE_MPV, ROOT, Backend, fake_mpv_dir, temp_dir, wait_for
from mockjf import MockJellyfin
from mocknd import ND_PASSWORD, ND_USER, SONG_SECONDS, MockNavidrome


def ogg_encoder():
    """libvorbis where ffmpeg has it; Homebrew's ffmpeg on macOS lacks it, Opus goes in an Ogg file as well."""
    if HAVE_FFMPEG:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        if " libvorbis " not in out and " libopus " in out:
            return "libopus"
    return "libvorbis"


OGG_ENCODER = ogg_encoder()


def make_audio(path, seconds=SONG_SECONDS):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", OGG_ENCODER, str(path)], check=True)
    return path


def set_nd_password(b, password):
    return subprocess.run([sys.executable, str(ROOT / "set-password"), "--navidrome", "--stdin"], input=password + "\n",
                          env=b.env, capture_output=True, text=True, timeout=30)


class MusicSessionTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.nd = MockNavidrome().start()

    def tearDown(self):
        self.mock.stop()
        self.nd.stop()

    def backend(self, navidrome=True):
        conf = {"navidrome": {"server": self.nd.url, "username": ND_USER}} if navidrome else None
        return Backend(self.mock.url, conf=conf).start()

    def test_without_navidrome(self):
        b = self.backend(navidrome=False)
        try:
            self.assertEqual(b.request("/music/session")[1], {"configured": False})
            self.assertEqual(b.request("/nd/getAlbumList2?type=newest")[0], 503)
        finally:
            b.stop()

    def test_sign_in(self):
        b = self.backend()
        try:
            self.assertEqual(b.request("/music/session")[1], {"configured": True, "ready": False,
                                                              "error": "nd_login_required"})
            r = set_nd_password(b, "wrong")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Login rejected", r.stderr)
            r = set_nd_password(b, ND_PASSWORD)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Signed in to Navidrome", r.stdout)
            stored = (b.conf / "navidrome.json").read_text()
            self.assertNotIn(ND_PASSWORD, stored, "only md5(password + salt)")
            self.assertEqual(set(json.loads(stored)), {"salt", "token"})
            self.assertEqual((b.conf / "navidrome.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(b.request("/music/session")[1], {"configured": True, "ready": True},
                             "the running backend picks it up")
        finally:
            b.stop()

    def test_proxy(self):
        b = self.backend()
        try:
            set_nd_password(b, ND_PASSWORD)
            code, r = b.request("/nd/getAlbumList2?type=newest&size=2")
            self.assertEqual(code, 200)
            self.assertEqual(b.request("/nd/getAlbumList2?type=newest&size=2&u=someone&t=x&s=y")[0], 200,
                             "the backend's own sign-in, whatever the UI sends")
            self.assertEqual([a["name"] for a in r["albumList2"]["album"]], ["Solo", "Second Album"])
            code, img = b.request("/nd/getCoverArt?id=al1&size=300")
            self.assertEqual(code, 200)
            self.assertTrue(img.startswith(b"\x89PNG"))
            self.nd.no_picture.add("ar-a1")
            self.assertEqual(b.request("/nd/getCoverArt?id=ar-a1&size=300")[0], 404, "Navidrome's placeholder: no picture")
            for method in ("scrobble", "deletePlaylist", "createUser", "stream"):
                self.assertEqual(b.request(f"/nd/{method}?id=s1")[0], 403, f"{method}: the UI only reads")
            self.assertNotIn(ND_PASSWORD, json.dumps(r))
        finally:
            b.stop()


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class MusicPlayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-music-"))
        cls.audio = make_audio(cls.tmp / "song.ogg")
        cls.mpv_dir = fake_mpv_dir(cls.tmp, speed=1)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.nd = MockNavidrome(audio_path=self.audio).start()
        self.b = Backend(self.mock.url, mpv_dir=self.mpv_dir,
                         conf={"navidrome": {"server": self.nd.url, "username": ND_USER}}).start()
        set_nd_password(self.b, ND_PASSWORD)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.nd.stop()

    def album(self, album_id="al1"):
        return self.b.request(f"/nd/getAlbum?id={album_id}")[1]["album"]["song"]

    def status(self):
        return self.b.request("/music/status")[1]

    def control(self, cmd, value=None):
        return self.b.request("/music/control", {"cmd": cmd, "value": value})[0]

    def test_plays_an_album_and_scrobbles(self):
        songs = self.album()
        self.assertEqual(self.b.request("/music/play", {"songs": songs, "start": 0})[0], 200)
        wait_for(lambda: self.status().get("active") and self.status()["pos"] is not None, msg="playing")
        s = self.status()
        self.assertEqual((s["index"], s["paused"]), (0, False))
        self.assertEqual([x["title"] for x in s["queue"]], ["Song 1", "Song 2", "Song 3"])
        self.assertNotIn("queue", self.b.request(f"/music/status?have={s['version']}")[1], "only when it changed")
        wait_for(lambda: ("s1", False) in self.nd.scrobbles, msg="now playing reported")
        wait_for(lambda: not self.status().get("active"), timeout=SONG_SECONDS * 3 + 10, msg="played to the end")
        self.assertEqual(self.nd.streamed, ["s1", "s2", "s3"])
        wait_for(lambda: [x for x in self.nd.scrobbles if x[1]] == [("s1", True), ("s2", True), ("s3", True)],
                 msg=f"each song counted once it was heard: {self.nd.scrobbles}")
        # its mpv ends with the queue: an idle one left counted as playing (the watchdog, the updates did nothing)
        music_mpv = lambda: subprocess.run(["pgrep", "-f", "--", f"--input-ipc-server={self.b.runtime}/kino-music-"],
                                           capture_output=True, text=True).stdout.strip()
        wait_for(lambda: not music_mpv(), timeout=10, msg="the music's mpv ended with the queue")
        self.assertEqual(self.b.request("/music/play", {"songs": songs[:1], "start": 0})[0], 200, "and the next one starts anew")
        wait_for(lambda: self.status().get("active") and self.status()["pos"] is not None, msg="playing again")

    def handing_off(self, navidrome_there):
        """Songs playing here, then "Continue on" the other Matinee of the account: (the other, where the song was)."""
        conf = {"navidrome": {"server": self.nd.url, "username": ND_USER}} if navidrome_there else None
        other = Backend(self.mock.url, mpv_dir=self.mpv_dir, conf=conf).start()
        self.addCleanup(other.stop)
        self.addCleanup(lambda: other.request("/music/control", {"cmd": "stop"}))
        if navidrome_there:
            set_nd_password(other, ND_PASSWORD)
        wait_for(lambda: len(self.mock.ws_devices) == 2, msg="both connected to Jellyfin")
        self.assertEqual(self.b.request("/music/handoff", {"device": other.device_id()})[0], 409, "nothing plays")
        self.assertEqual(self.b.request("/music/play", {"songs": self.album(), "start": 1})[0], 200)
        wait_for(lambda: (self.status().get("pos") or 0) >= 1, msg="a little way into the second song")
        with self.mock.lock:  # nothing done on either for an hour (Jellyfin's KeepAlive does not count), and one gone
            for x in self.mock.sessions.values():
                x["last"] -= 3600
            self.mock.sessions["gone"] = {"name": "Gone", "client": "Matinee", "last": time.time()}
        self.assertEqual([d["id"] for d in self.b.request("/handoff")[1]], [other.device_id()],
                         "the other, on however long it did nothing; not this one, nor one no longer connected")
        at = self.status()["pos"]
        self.assertEqual(self.b.request("/music/handoff", {"device": other.device_id()})[0], 200)
        return other, at

    def test_going_on_on_another_device(self):
        """"Continue on" in the music's player: the queue goes on on the other Matinee, in the song and where it was."""
        other, at = self.handing_off(navidrome_there=True)
        there = lambda: other.request("/music/status")[1]
        wait_for(lambda: there().get("active") and there().get("pos") is not None, timeout=10, msg="going on there")
        s = there()
        self.assertEqual((s["index"], [x["title"] for x in s["queue"]]), (1, ["Song 1", "Song 2", "Song 3"]))
        self.assertGreaterEqual(s["pos"], at - 0.5, "from where it was, not the start of the song")
        self.assertFalse(self.status().get("active"), "ended here")
        time.sleep(1)
        self.assertFalse(self.status().get("active"), "and not here again")
        other.request("/music/control", {"cmd": "pause"})  # paused: where a song begins stays to be seen
        wait_for(lambda: there()["paused"], msg="paused there")
        other.request("/music/control", {"cmd": "index", "value": 0})
        wait_for(lambda: there().get("index") == 0 and there().get("pos") is not None, msg="the song before")
        other.request("/music/control", {"cmd": "index", "value": 1})
        wait_for(lambda: there().get("index") == 1 and there().get("pos") is not None, msg="the one handed over again")
        self.assertLess(there()["pos"], at - 0.5, "from its beginning now: where it was is only for once")

    def test_going_on_here_again_when_the_other_cannot(self):
        """The other Matinee has no Navidrome: it does not take it, and after a moment the music goes on here."""
        other, at = self.handing_off(navidrome_there=False)
        wait_for(lambda: self.status().get("active") and self.status().get("pos") is not None, timeout=10,
                 msg="here again")
        self.assertEqual(self.status()["index"], 1)
        self.assertGreaterEqual(self.status()["pos"], at - 0.5, "where it was")
        self.assertFalse(other.request("/music/status")[1].get("active"))
        self.assertIn("no Navidrome set up", other.logtext())

    def test_nothing_reported_with_scrobbling_off(self):
        self.assertEqual(self.b.request("/settings", {"scrobble": "off"})[0], 200)
        self.assertEqual(self.b.request("/music/play", {"songs": self.album()[:1], "start": 0})[0], 200)
        wait_for(lambda: not self.status().get("active"), timeout=SONG_SECONDS + 10, msg="played to the end")
        self.assertEqual(self.nd.streamed, ["s1"])
        self.assertEqual(self.nd.scrobbles, [], "neither now playing nor the play count")

    def test_controls(self):
        songs = self.album("al2")
        self.b.request("/music/play", {"songs": songs, "start": 1})
        wait_for(lambda: self.status().get("index") == 1, msg="starts at the chosen song")
        self.assertEqual(self.control("pause"), 200)
        wait_for(lambda: self.status()["paused"], msg="paused")
        self.control("pause")
        wait_for(lambda: not self.status()["paused"], msg="playing again")
        self.control("next")
        wait_for(lambda: self.status().get("index") == 2, msg="next song")
        self.control("prev")  # at the start of a song: the one before
        wait_for(lambda: self.status().get("index") == 1, msg="previous song")
        self.control("index", 0)
        wait_for(lambda: self.status().get("index") == 0, msg="a song from the queue")
        self.assertEqual(self.control("stop"), 200)
        wait_for(lambda: not self.status().get("active"), msg="stopped")
        self.assertEqual(self.control("pause"), 409, "nothing to pause")

    def test_play_next_and_add_to_queue(self):
        self.b.request("/music/play", {"songs": self.album("al1"), "start": 0})
        wait_for(lambda: self.status().get("pos") is not None, msg="playing")
        self.control("pause")
        wait_for(lambda: self.status()["paused"], msg="paused, so the queue stays where it is")
        version = self.status()["version"]
        r = self.b.request("/music/queue", {"songs": self.album("al3")[:2], "next": True})
        self.assertEqual(r, (200, {"joined": True}))
        r = self.b.request("/music/queue", {"songs": self.album("al2")[:1]})
        self.assertEqual(r, (200, {"joined": True}))
        s = self.status()
        self.assertNotEqual(s["version"], version, "the UI fetches the queue again")
        self.assertEqual([x["title"] for x in s["queue"]], ["Song 1", "Song 7", "Song 8", "Song 2", "Song 3", "Song 4"])
        self.assertEqual((s["index"], s["paused"]), (0, True), "the song playing goes on")
        self.control("next")
        wait_for(lambda: self.status().get("index") == 1, msg="next")
        self.control("pause")
        wait_for(lambda: self.nd.streamed[-1:] == ["s7"], msg="mpv plays the songs in the queue's order")

    def test_queue_with_nothing_playing_plays(self):
        r = self.b.request("/music/queue", {"songs": self.album("al2"), "next": True})
        self.assertEqual(r, (200, {"joined": False}))
        wait_for(lambda: self.status().get("pos") is not None, msg="playing")
        self.assertEqual([x["title"] for x in self.status()["queue"]], ["Song 4", "Song 5", "Song 6"])
        self.assertEqual(self.b.request("/music/queue", {"songs": []})[0], 400)

    def test_skipped_songs_are_not_counted(self):
        self.b.request("/music/play", {"songs": self.album(), "start": 0})
        wait_for(lambda: ("s1", False) in self.nd.scrobbles, msg="playing")
        self.control("next")
        wait_for(lambda: ("s2", False) in self.nd.scrobbles, msg="next song")
        time.sleep(0.5)
        self.assertNotIn(("s1", True), self.nd.scrobbles, "skipped right away: not a play")

    def music_mpv(self):
        out = subprocess.run(["pgrep", "-f", "--", f"--input-ipc-server={self.b.runtime}/kino-music-"], capture_output=True, text=True)
        return out.stdout.split()

    def test_players_end_with_the_backend(self):
        self.b.request("/music/play", {"songs": self.album(), "start": 0})
        wait_for(lambda: self.status().get("active"), msg="playing")
        self.assertEqual(len(self.music_mpv()), 1)
        self.b.proc.terminate()
        self.b.proc.wait(5)
        wait_for(lambda: not self.music_mpv(), timeout=5, msg="its mpv ended with it")
        self.b.start()

    def test_players_left_over_by_a_killed_backend_are_stopped(self):
        self.b.request("/music/play", {"songs": self.album(), "start": 0})
        wait_for(lambda: self.status().get("active"), msg="playing")
        self.b.proc.kill()  # no chance to clean up
        self.b.proc.wait(5)
        self.assertEqual(len(self.music_mpv()), 1, "still playing on")
        gone = subprocess.Popen(["true"])
        gone.wait()
        stale = self.b.runtime / f"kino-music-{gone.pid}-0123abcd.sock"  # of a backend killed before
        stale.touch()
        alive = self.b.runtime / f"kino-mpv-{os.getpid()}-0123abcd.sock"  # of a process still there: not ours to take
        alive.touch()
        self.b.start()
        wait_for(lambda: not self.music_mpv(), timeout=5, msg="stopped by the next backend")
        self.assertIn("left over by an earlier Matinee", self.b.logtext())
        self.assertFalse(stale.exists(), "a gone backend's socket cleared away")
        self.assertTrue(alive.exists())
        self.assertEqual([p.name for p in self.b.runtime.glob("kino-music-*.sock") if p != stale], [],
                         "nor is the killed backend's own left")

    def test_rejects_bad_requests(self):
        for body in ({}, {"songs": []}, {"songs": [{"id": "../x"}]}, {"songs": "s1"}):
            self.assertEqual(self.b.request("/music/play", body)[0], 400, body)
        self.assertEqual(self.b.request("/music/control", {"cmd": "rm"})[0], 400)

    def test_stream_address_never_shows_up_in_the_process_list(self):
        self.b.request("/music/play", {"songs": self.album(), "start": 0})
        wait_for(lambda: self.status().get("active"), msg="playing")
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        self.assertNotIn("/rest/stream", ps, "the token goes to mpv over IPC only")


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class MusicExtrasTest(unittest.TestCase):
    """Favorites, internet radio and lyrics."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-music-"))
        cls.audio = make_audio(cls.tmp / "song.ogg", 20)
        cls.mpv_dir = fake_mpv_dir(cls.tmp, speed=1)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.nd = MockNavidrome(audio_path=self.audio).start()
        self.b = Backend(self.mock.url, mpv_dir=self.mpv_dir,
                         conf={"navidrome": {"server": self.nd.url, "username": ND_USER}}).start()
        set_nd_password(self.b, ND_PASSWORD)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.nd.stop()

    def test_favorites(self):
        self.assertEqual(self.b.request("/music/star", {"type": "album", "id": "al2", "on": True})[0], 200)
        self.assertEqual(self.b.request("/music/star", {"type": "song", "id": "s7", "on": True})[0], 200)
        starred = self.b.request("/nd/getStarred2")[1]["starred2"]
        self.assertEqual(([a["id"] for a in starred["album"]], [x["id"] for x in starred["song"]]), (["al2"], ["s7"]))
        self.assertEqual(self.b.request("/music/star", {"type": "album", "id": "al2", "on": False})[0], 200)
        self.assertEqual(self.b.request("/nd/getStarred2")[1]["starred2"]["album"], [])
        self.assertEqual(self.b.request("/music/star", {"type": "playlist", "id": "pl1", "on": True})[0], 400)
        self.assertEqual(self.b.request("/nd/star?id=s1")[0], 403, "only through /music/star")

    def test_radio(self):
        self.assertEqual(self.b.request("/music/play", {"radio": "st1"})[0], 200)
        wait_for(lambda: self.b.request("/music/status")[1].get("pos") is not None, msg="station playing")
        s = self.b.request("/music/status")[1]
        self.assertEqual((s["queue"][0]["title"], s["queue"][0]["radio"], s["duration"]), ("Mock FM", True, None))
        time.sleep(1.5)
        self.assertEqual(self.nd.scrobbles, [], "a station is not a song")
        self.assertEqual(self.b.request("/music/play", {"radio": "nope"})[0], 404)

    def test_lyrics(self):
        r = self.b.request("/nd/getLyricsBySongId?id=s1")[1]["lyricsList"]["structuredLyrics"][0]
        self.assertTrue(r["synced"])
        self.assertEqual([x["value"] for x in r["line"]], ["First line", "Second line"])


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class SleepTimerTest(unittest.TestCase):
    """The sleep timer, with a minute lasting 0.05 seconds."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-sleep-"))
        cls.audio = make_audio(cls.tmp / "song.ogg", 3)
        cls.bin = fake_mpv_dir(cls.tmp, speed=1)
        cls.log = cls.tmp / "systemctl.log"
        (cls.bin / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> {cls.log}\n')
        (cls.bin / "systemctl").chmod(0o755)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.nd = MockNavidrome(audio_path=self.audio).start()
        self.b = Backend(self.mock.url, mpv_dir=self.bin, env={"KINO_SLEEP_UNIT": "0.03", "KINO_SLEEP_FADE": "0.5",
                                                                 "KINO_SESSION": "1"},
                         conf={"navidrome": {"server": self.nd.url, "username": ND_USER}}).start()
        set_nd_password(self.b, ND_PASSWORD)
        self.log.unlink(missing_ok=True)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.nd.stop()

    def sleep(self, **body):
        return self.b.request("/sleep", body)[1]

    def play_album(self):
        songs = self.b.request("/nd/getAlbum?id=al1")[1]["album"]["song"]
        self.b.request("/music/play", {"songs": songs})
        wait_for(lambda: self.b.request("/music/status")[1].get("pos") is not None, msg="playing")

    def test_cycle(self):
        labels = [self.sleep(cycle=True)["label"] for _ in range(5)]
        self.assertEqual(labels, ["Sleep timer: 30 min", "Sleep timer: 60 min", "Sleep timer: 90 min",
                                  "Sleep timer: at the end", "Sleep timer off"])
        self.assertAlmostEqual(self.sleep(mode=60)["sleep"]["left"], 3600, delta=120)
        self.assertEqual(self.b.request("/sleep", {"mode": 7})[0], 400)

    def test_music_fades_out_and_stops(self):
        self.play_album()
        self.sleep(mode=30)  # 0.9 seconds here, then half a second of fading: well inside the first song's 2
        self.assertAlmostEqual(self.b.request("/music/status")[1]["sleep"]["left"], 1800, delta=600)
        playing = set()

        def stopped():
            s = self.b.request("/music/status")[1]
            if s.get("active"):
                playing.add(s.get("index"))
            return not s.get("active")
        wait_for(stopped, timeout=5, msg="stopped before the album ends")
        # what mpv fetched does not tell: mpv 0.40 (Debian 13) opens the next song at once
        self.assertEqual(playing, {0}, "in the first song")
        self.assertIsNone(self.b.request("/status")[1]["sleep"], "the timer is done")
        self.assertFalse(self.log.exists(), "stop only, by default")

    def test_at_the_end_and_then_shut_down(self):
        self.assertEqual(self.b.request("/settings", {"sleepend": "poweroff"})[0], 200)
        self.play_album()
        self.sleep(mode="end")
        wait_for(lambda: not self.b.request("/music/status")[1].get("active"), timeout=20, msg="the album played to its end")
        self.assertEqual(self.nd.streamed, ["s1", "s2", "s3"])
        wait_for(lambda: self.log.exists() and self.log.read_text() == "poweroff\n", msg="shut down afterwards")


@unittest.skipUnless(HAVE_MPV and HAVE_FFMPEG, "needs mpv and ffmpeg")
class MusicBreakOffTest(unittest.TestCase):
    """A song whose stream breaks off halfway: Matinee waits for the server and goes on there, not through the queue."""
    SECONDS = 30  # songs play at normal speed

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(temp_dir("kino-music-"))
        cls.audio = make_audio(cls.tmp / "long.ogg", cls.SECONDS)
        cls.mpv_dir = fake_mpv_dir(cls.tmp, speed=4)

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.nd = MockNavidrome(audio_path=self.audio).start()
        self.b = Backend(self.mock.url, mpv_dir=self.mpv_dir,
                         conf={"navidrome": {"server": self.nd.url, "username": ND_USER}}).start()
        set_nd_password(self.b, ND_PASSWORD)

    def tearDown(self):
        self.b.request("/music/control", {"cmd": "stop"})
        self.b.stop()
        self.mock.stop()
        self.nd.stop()

    def status(self):
        return self.b.request("/music/status")[1]

    def test_waits_for_the_server_and_goes_on(self):
        songs = [{**x, "duration": self.SECONDS} for x in self.b.request("/nd/getAlbum?id=al1")[1]["album"]["song"]]
        self.nd.stream_cut = self.audio.stat().st_size // 2
        self.assertEqual(self.b.request("/music/play", {"songs": songs, "start": 0})[0], 200)
        wait_for(lambda: self.status().get("lost"), timeout=40, msg="waiting for the server")
        s = self.status()
        cut = s["pos"]
        self.assertEqual(s["index"], 0)
        self.assertLess(cut, self.SECONDS - 10)
        self.assertEqual(set(self.nd.streamed), {"s1"}, "not on through the queue (mpv asks for a song more than once)")
        self.nd.start()
        wait_for(lambda: not self.status().get("lost") and (self.status().get("pos") or 0) > 0, timeout=20,
                 msg="going on")
        s = self.status()
        self.assertEqual(s["index"], 0)
        self.assertAlmostEqual(s["pos"], max(0, cut - 3), delta=8)
        self.assertEqual([x["id"] for x in s.get("queue") or songs], ["s1", "s2", "s3"])
        wait_for(lambda: self.status().get("index") == 1, timeout=45, msg="the next song after the real end")
        # told Navidrome beside the playback (a thread of its own): a moment later on a busy machine
        wait_for(lambda: ("s1", True) in self.nd.scrobbles, timeout=5, msg="scrobbled")
        time.sleep(0.5)
        self.assertEqual([x for x in self.nd.scrobbles if x == ("s1", True)], [("s1", True)], "scrobbled once")

    def test_a_station_comes_back(self):
        self.nd.stream_cut = self.audio.stat().st_size // 6
        self.assertEqual(self.b.request("/music/play", {"radio": "st1"})[0], 200)
        wait_for(lambda: self.status().get("lost"), timeout=40, msg="waiting for the station")
        self.assertTrue(self.status()["queue"][0]["radio"])
        self.nd.start()
        wait_for(lambda: not self.status().get("lost") and (self.status().get("pos") or 0) > 0, timeout=20, msg="on again")
        self.assertEqual(self.status()["index"], 0)


# The monitor of the music, run in a process of its own with an empty configuration: a stand-in mpv on the other
# end of a socket pair sends its messages in the order the test says (argv[2]: the steps as JSON).
END_PROBE = r'''
import json, os, socket, sys, threading, time
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import mpv_ipc, music_player

steps = json.loads(sys.argv[2])
ours, theirs = socket.socketpair()
mpv = mpv_ipc.Mpv.__new__(mpv_ipc.Mpv)
mpv.sock, mpv.buf, mpv.rid, mpv.events, mpv.props = ours, b"", 0, [], {}
now = {"time-pos": None}
done = threading.Event()

def answer():  # replies to the monitor's commands, with the time as the steps have set it
    buf = b""
    while True:
        try:
            chunk = theirs.recv(65536)
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            msg = json.loads(line)
            cmd = msg["command"]
            data = now.get(cmd[1]) if cmd[0] == "get_property" else None
            theirs.sendall(json.dumps({"request_id": msg["request_id"], "error": "success", "data": data}).encode() + b"\n")

def play():
    for step in steps:
        time.sleep(step.get("after", 0))
        if "time" in step:
            now["time-pos"] = step["time"]
        if "pos" in step:
            theirs.sendall(json.dumps({"event": "property-change", "name": "playlist-pos", "data": step["pos"]}).encode() + b"\n")
        if "end" in step:
            theirs.sendall(json.dumps({"event": "end-file", "reason": step["end"]}).encode() + b"\n")
    time.sleep(1.5)
    done.set()

class Proc:
    def poll(self):
        return 0 if done.is_set() else None
    def wait(self, timeout=None):
        return 0

closed = []
class Progress:  # stands in for BookProgress: what the monitor would tell Podfetch
    def __init__(self, book):
        self.book = book
    def tick(self, pos, paused):
        pass
    def chapter_ends(self, now, lead):
        return False
    def send(self, what, finished=False):
        if what == "close":
            closed.append([self.book["id"], finished])

music_player.BookProgress = Progress
player = music_player.MusicPlayer()
player.ctl = mpv  # the UI's connection as well: asked whether the queue has ended (queue_done)
player.queue = [{"id": "ep-1", "podcast": 1, "book": True, "duration": 4},
                {"id": "ep-2", "podcast": 1, "book": True, "duration": 4}]
threading.Thread(target=answer, daemon=True).start()
threading.Thread(target=play, daemon=True).start()
player.monitor(Proc(), mpv, type("Sock", (), {"unlink": lambda self, missing_ok=False: None})())
print(json.dumps(closed))
'''


class MusicEndTest(unittest.TestCase):
    """An episode heard to its end is marked as heard, whichever mpv says first: its end or the next position."""

    def closes(self, steps):
        with tempfile.TemporaryDirectory(prefix="kino-end-") as tmp:
            env = dict(XDG_CONFIG_HOME=f"{tmp}/config", XDG_RUNTIME_DIR=tmp, XDG_CACHE_HOME=f"{tmp}/cache",
                       XDG_DATA_HOME=f"{tmp}/data", PATH="/usr/bin:/bin", HOME=tmp)
            server_dir = str(Path(os.environ.get("KINO_SERVER", str(ROOT / "server.py"))).parent)
            out = subprocess.run([sys.executable, "-c", END_PROBE, server_dir, json.dumps(steps)], env=env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_end_told_after_the_next_position(self):
        closes = self.closes([{"pos": 0, "time": 0}, {"after": 0.5, "time": 2}, {"after": 0.5, "time": 3.9},
                              {"after": 0.3, "pos": 1, "time": 0}, {"after": 0.2, "end": "eof"},
                              {"after": 0.5, "time": 1}, {"after": 0.3, "pos": -1}, {"after": 0.05, "end": "stop"}])
        self.assertEqual(closes, [["ep-1", True], ["ep-2", False]],
                         "the first heard to its end; its late end not given to the second, stopped halfway")

    def test_end_told_before_the_next_position(self):
        closes = self.closes([{"pos": 0, "time": 0}, {"after": 0.5, "time": 3.9}, {"after": 0.3, "end": "eof"},
                              {"after": 0.05, "pos": 1, "time": 0}, {"after": 0.5, "pos": -1}, {"after": 0.05, "end": "stop"}])
        self.assertEqual(closes, [["ep-1", True], ["ep-2", False]])

    def test_another_chosen_is_not_heard(self):
        closes = self.closes([{"pos": 0, "time": 0}, {"after": 0.5, "time": 1.5},
                              {"after": 0.3, "pos": 1, "time": 0}, {"after": 0.1, "end": "stop"}])
        self.assertEqual(closes[0], ["ep-1", False], "skipped halfway: not heard")


if __name__ == "__main__":
    unittest.main()
