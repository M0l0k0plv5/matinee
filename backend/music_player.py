"""Music, audiobooks and podcasts in an mpv of their own, without a window."""
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .core import CUT_OFF_MARGIN, END_FILE_WAIT, RECONNECT_EVERY, RECONNECT_FOR, RUNTIME, save_state, state
from .desktop import mpris_script
from .preferences import setting
from .handoff import music_songs
from .mpv_ipc import Mpv
from .sleeping import sleep_timer
from .video_player import player
from .services import abs_book, BookProgress, NavidromeError, nd, nd_stream_url, pf_play


class MusicPlayer:
    """Music from Navidrome: an mpv of its own, without a window, playing a queue (an album, a playlist,
    a random mix). It tells Navidrome what is playing, and scrobbles a song once half of it (or four
    minutes) has been heard. A movie or an episode stops it. A book from Audiobookshelf plays here too,
    as a queue of its own, faster or slower if wished, with its progress going back to Audiobookshelf."""

    def __init__(self):
        self.proc = None
        self.ctl = None        # connection for commands from the UI, used under the lock
        self.lock = threading.Lock()
        self.queue = []
        self.version = 0       # changes with the queue, so the UI only fetches it then
        self.stopped_at = None
        self.lost = None       # {"index", "pos", "duration"} while waiting for the server after a stream broke off

    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        sock = RUNTIME / f"kino-music-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
        self.proc = proc = subprocess.Popen([
            "mpv", "--no-config", "--no-terminal", "--idle=yes", "--no-video", "--audio-display=no",
            "--force-window=no", "--gapless-audio=weak", "--replaygain=album", f"--input-ipc-server={sock}",
            f"--volume={state.get('volume', 100)}", *mpris_script()])
        conns = []
        for _ in range(2):  # one for commands, one for the events the monitor waits for
            for _ in range(200):
                try:
                    conns.append(Mpv(sock))
                    break
                except OSError:
                    if proc.poll() is not None:
                        raise OSError("mpv did not start")
                    time.sleep(0.05)
        if len(conns) < 2:
            proc.kill()
            raise OSError("mpv did not start")
        self.ctl = conns[0]
        threading.Thread(target=self.monitor, args=(proc, conns[1], sock), daemon=True).start()

    def play(self, songs, start=0, at=None):
        """The songs as the queue, from the one at start; at: so far into that one (only this once: played again
        later it starts at its beginning, unlike a book's chapter carried on with its "start")."""
        player.stop()
        with self.lock:
            if not self.active:
                self.start()
            self.ctl.command("stop")  # also empties the playlist
            for s in songs:
                url = s.get("url") or nd_stream_url(s["id"])
                if s.get("start"):  # a book carries on where it was left
                    self.ctl.command("loadfile", url, "append", -1, f"start={s['start']}")
                else:
                    self.ctl.command("loadfile", url, "append")
            self.queue = songs
            self.version += 1
            self.ctl.command("set_property", "speed", float(state.get("bookspeed", 1)) if songs[0].get("book") else 1.0)
            # so far into the first one from its start, not begun at 0 and moved there after (heard, and a moment
            # where it was not yet); for no other song after it (once_loaded)
            self.ctl.command("set_property", "start", f"{float(at):.3f}" if at else "none")
            self.ctl.command("playlist-play-index", start)
            self.ctl.command("set_property", "pause", False)
            version = self.version
        if at:
            threading.Thread(target=self.once_loaded, args=(version, start), daemon=True).start()

    def once_loaded(self, version, index):
        """Where the song begun by play starts from is for it alone: once mpv has it loaded, the next ones and that
        one played again start at their beginning (another queue sets its own)."""
        until = time.monotonic() + 10
        while time.monotonic() < until:
            with self.lock:  # the connection is the UI's too
                if self.version != version or not self.active:
                    return
                try:
                    pos = self.ctl.get("playlist-pos")
                    if pos != index and pos not in (None, -1) or pos == index and self.ctl.get("time-pos") is not None:
                        break  # loaded, or another song came first (that one failed)
                except OSError:
                    return
            time.sleep(0.1)
        with self.lock:
            try:
                if self.version == version and self.active:
                    self.ctl.command("set_property", "start", "none")
            except OSError:
                pass

    def enqueue(self, songs, next_up=False):
        """Songs after the one playing (next_up) or at the end of the queue, without stopping it; with nothing
        (or a station) playing they are played instead. True if they joined a queue, False if they started one."""
        with self.lock:
            index = None
            if self.active:
                try:
                    index = self.ctl.get("playlist-pos")
                except OSError:
                    index = None
            playing = index is not None and 0 <= index < len(self.queue) and not self.queue[index].get("radio")
            if playing:
                try:
                    # insert-next puts each right after the playing one: in reverse they end up in order
                    for x in (reversed(songs) if next_up else songs):
                        self.ctl.command("loadfile", nd_stream_url(x["id"]), "insert-next" if next_up else "append")
                except OSError:
                    playing = False
                else:
                    at = index + 1 if next_up else len(self.queue)
                    self.queue = [*self.queue[:at], *songs, *self.queue[at:]]
                    self.version += 1
                    return True
        self.play(songs)
        return False

    def control(self, cmd, value=None):
        """False if there is nothing playing to control."""
        with self.lock:
            if not self.active:
                return False
            try:
                self._control(cmd, value)
            except OSError:  # mpv has just gone
                return False
            return True

    def _control(self, cmd, value):
        c = self.ctl
        if cmd in ("next", "prev", "index"):  # a handed-over song's position is for it alone (once_loaded)
            c.command("set_property", "start", "none")
        if cmd == "pause":
            c.command("cycle", "pause")
        elif cmd == "setpause":  # paused or not, whichever it was (Jellyfin's remote control)
            c.command("set_property", "pause", bool(value))
        elif cmd == "next":
            c.command("playlist-next")
        elif cmd == "prev":  # like any player: back to the start of the song, and only then to the one before
            if (c.get("time-pos") or 0) > 3 or not c.get("playlist-pos"):
                c.command("seek", 0, "absolute")
            else:
                c.command("playlist-prev")
        elif cmd == "seek":
            c.command("seek", float(value), "absolute")
        elif cmd == "index":
            c.command("playlist-play-index", int(value))
        elif cmd == "skip":  # a book: back or ahead some seconds
            c.command("seek", max(-600.0, min(600.0, float(value))), "relative")
        elif cmd == "speed":
            speed = float(value)
            c.command("set_property", "speed", speed)
            state["bookspeed"] = speed
            save_state()
        elif cmd == "stop":
            try:
                self.stopped_at = c.get("time-pos")  # for a book's last report: the monitor looks only every second
                c.command("quit")
            except OSError:
                pass  # mpv may close the connection before it answers
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:  # it hangs: the film that stops the music starts all the same
                self.proc.kill()

    def playing_book(self):
        """Whether a book is what plays (its sleep timer can stop at the end of a chapter)."""
        with self.lock:
            if not self.active:
                return False
            try:
                index = self.ctl.get("playlist-pos")
            except OSError:
                return False
            return index is not None and 0 <= index < len(self.queue) and bool(self.queue[index].get("book"))

    def stop(self):
        try:
            self.control("stop")
        except OSError:
            pass

    def handing_off(self):
        """What plays, for another Matinee to go on with (the queue, the song playing and where in it), ended here; None
        if it is no music of Navidrome's (a book, a station) or nothing plays."""
        with self.lock:
            if not self.active or self.lost:
                return None
            try:
                index, pos = self.ctl.get("playlist-pos"), self.ctl.get("time-pos")
            except OSError:
                return None
            queue = self.queue
            if index is None or not 0 <= index < len(queue) or any(x.get("book") or x.get("radio") for x in queue):
                return None
        self.stop()
        return {"kind": "music", "songs": music_songs(queue), "index": index, "pos": round(pos or 0.0, 1)}

    def status(self, have=None):
        with self.lock:
            if not self.active:
                return {"active": False}
            try:
                c = self.ctl
                index = c.get("playlist-pos")
                if self.lost:
                    s = {"active": True, **self.lost, "paused": True, "lost": True, "version": self.version,
                         "sleep": sleep_timer.state()}
                elif index is None or index < 0:  # the queue has played to its end
                    return {"active": False}
                else:
                    s = {"active": True, "index": index, "pos": c.get("time-pos"), "duration": c.get("duration"),
                         "paused": bool(c.get("pause")), "version": self.version, "sleep": sleep_timer.state()}
                if not self.lost and 0 <= index < len(self.queue) and self.queue[index].get("book"):
                    s["speed"] = c.get("speed")
                if not self.lost and 0 <= index < len(self.queue) and self.queue[index].get("radio"):
                    s["duration"] = None  # a stream has no end
                    try:  # what the station plays right now, if it says so
                        s["icy"] = c.get("metadata/by-key/icy-title")
                    except OSError:
                        pass
            except OSError:
                return {"active": False}
        if have != str(self.version):  # a book's address carries the token: it stays here
            s["queue"] = [{k: v for k, v in x.items() if k not in ("url", "session")} for x in self.queue]
        return s

    def monitor(self, proc, mpv, sock):
        """Follow the queue: tell Navidrome what plays and scrobble what has been heard."""
        try:
            mpv.observe("playlist-pos")
            mpv.command("enable_event", "end-file")
        except OSError:
            pass
        # the song playing, kept here: a new queue may replace self.queue before its first song starts
        # (entry: the queue's own entry, so a new queue starting at the same place counts as a change)
        index, entry, song, started, heard = -1, None, None, 0, 0.0
        played = False  # an entry of a queue has played: the queue's end (no position then) ends this mpv
        book, ended = None, False  # the book playing (BookProgress), and whether its end was reached
        file_ended = False  # mpv has said the entry playing is over (its end-file, for whatever reason)
        entry_id = None  # mpv's id of the entry playing: an end-file of another one (come late) is not its
        slept = False
        last = None  # where the entry playing was at the last look
        while proc.poll() is None:
            try:
                mpv.wait(1)
                ends = [e for e in mpv.events if e.get("event") == "end-file"
                        and (entry_id is None or e.get("playlist_entry_id") in (None, entry_id))]
                eof = any(e.get("reason") == "eof" for e in ends)
                file_ended = file_ended or bool(ends)
                mpv.events.clear()
                if eof and self.cut_off(entry, last):
                    if self.wait_for_server(proc, mpv, index, entry, last):
                        continue
                    eof = False  # given up, or something else chosen meanwhile: not heard to the end
                # a song played to its end counts as heard, however rarely its position was looked at
                if song and eof:
                    heard = max(heard, song.get("duration") or 1)
                ended = ended or (book is not None and eof)
                pos = mpv.props.get("playlist-pos")
                pos = -1 if pos is None else pos  # -1: nothing plays (the queue has ended)
                t_pos = mpv.get("time-pos")
            except (OSError, ValueError):
                break
            queue = self.queue
            changed = pos != index or (queue[pos] if 0 <= pos < len(queue) else None) is not entry
            if t_pos is not None and not changed:
                heard = max(heard, t_pos)
            was_at = last
            last = None if changed else t_pos if t_pos is not None else last
            if book and not changed:
                try:
                    # "at the end of the chapter": the fading starts so that it is silent where the chapter ends
                    if sleep_timer.at_chapter and book.chapter_ends(t_pos, sleep_timer.FADE * (mpv.get("speed") or 1)):
                        sleep_timer.fade(mpv)
                        book.tick(mpv.get("time-pos"), False)
                        slept = True
                        break
                    book.tick(t_pos, mpv.get("pause"))
                except (OSError, ValueError):
                    break
            if sleep_timer.due() and index >= 0:
                try:
                    sleep_timer.fade(mpv)
                except OSError:
                    break
                slept = True
                break
            if pos != index and pos == -1 and index >= 0 and sleep_timer.at_end:
                slept = True  # the queue has played to its end; its last song is counted below
                break
            if changed:
                # mpv tells its new playlist position before the end of the file that moved it on (property
                # changes go first): that end, waited for briefly here, belongs to the entry that was playing, not
                # to the next one; without it an episode heard to its end stayed unheard
                # (not for an entry replaced where it is, as when going on after the stream broke off)
                if entry is not None and not file_ended and pos != index:
                    try:
                        late = mpv.wait_event("end-file", END_FILE_WAIT)
                    except (OSError, ValueError):
                        late = None
                    if late and entry_id is not None and late.get("playlist_entry_id") not in (None, entry_id):
                        late = None  # another entry's
                    if late and late.get("reason") == "eof" and not self.cut_off(entry, was_at):
                        if song:
                            heard = max(heard, song.get("duration") or 1)
                        ended = ended or book is not None
                self.scrobble(song, started, heard)
                if book:
                    book.send("close", finished=ended)
                index, started, heard, file_ended = pos, time.time(), 0.0, False
                try:
                    entry_id = mpv.get(f"playlist/{index}/id") if index >= 0 else None
                except (OSError, ValueError):
                    entry_id = None
                song = entry = queue[index] if 0 <= index < len(queue) else None
                book, ended = (BookProgress(song), False) if song and song.get("book") else (None, False)
                if song and (song.get("radio") or song.get("book")):
                    song = None  # a station or a book is not a song: nothing to tell Navidrome
                if song:
                    self.report(song["id"], submission=False)
            if index >= 0:
                played = True
            elif played and self.queue_done(proc):
                break
        self.scrobble(song, started, heard)
        if book:
            if self.stopped_at is not None and not ended:
                book.tick(self.stopped_at, False)
            book.send("close", finished=ended)
        self.stopped_at = None
        if slept:  # before mpv quits: once the music says it has stopped, the timer is done too
            sleep_timer.done()
        try:
            mpv.command("quit")
        except OSError:
            pass
        proc.wait()
        mpv.close()
        sock.unlink(missing_ok=True)

    def queue_done(self, proc):
        """The queue has played to its end: mpv ends, so nothing counts as playing any more (the watchdog, the updates,
        the nightly update's look for an mpv); the next music starts one anew. Not a queue emptied for a new one: play
        does that under the lock, and has started the new one by the time it lets go of it."""
        with self.lock:
            try:
                if self.ctl.get("playlist-pos") not in (None, -1):
                    return False
                self.ctl.command("quit")
            except OSError:
                pass  # gone already
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return True

    def cut_off(self, entry, pos):
        """A song or book at its end well before its end: the stream broke off (see CUT_OFF_MARGIN), unless it
        stopped at the same place after going on there already. The margin is wider than a film's: a book may play
        faster, and the position is only looked at every second."""
        if entry and entry.get("radio"):
            return pos is not None  # a station has no end: once it played, an end is a stream broken off
        duration = (entry or {}).get("duration")
        if not duration or pos is None or duration - pos < 2 * CUT_OFF_MARGIN:
            return False
        return abs(pos - entry.get("cut_at", -4 * CUT_OFF_MARGIN)) > 2 * CUT_OFF_MARGIN

    def wait_for_server(self, proc, mpv, index, entry, pos):
        """Hold the queue where the stream broke off, wait for the server and go on a few seconds before; a book
        with a new listening session. False when the server stayed away or something else was chosen meanwhile."""
        print(f"Stream broke off at {pos:.0f} s, waiting for the server", file=sys.stderr)
        with self.lock:
            self.lost = {"index": index, "pos": pos, "duration": entry.get("duration")}
            try:
                mpv.command("playlist-play-index", "none")  # not on through the rest of the queue
            except OSError:
                pass
        end = time.monotonic() + RECONNECT_FOR
        try:
            while proc.poll() is None and time.monotonic() < end:
                time.sleep(RECONNECT_EVERY)
                at = mpv.get("playlist-pos")
                if at is not None and at >= 0:
                    return False  # another song or book was chosen
                try:
                    if entry.get("podcast") is not None:
                        again = {**pf_play(entry["id"], at=max(0, pos - 3)), "cut_at": pos}
                    elif entry.get("book"):
                        again = {**abs_book(entry["id"], at=max(0, pos - 3)), "cut_at": pos}
                    elif entry.get("radio"):  # the station's own server: does it send again?
                        with urllib.request.urlopen(entry["url"], timeout=10) as r:
                            r.read(1)
                        again = entry
                    else:
                        nd("ping")
                        again = entry
                        entry["cut_at"] = pos
                except Exception:
                    continue  # still away
                with self.lock:
                    if index >= len(self.queue) or self.queue[index] is not entry:
                        return False  # a new queue meanwhile
                    url = again.get("url") or nd_stream_url(again["id"])
                    if entry.get("radio"):
                        mpv.command("loadfile", url, "insert-at", index)  # live: where it is now
                    else:
                        mpv.command("loadfile", url, "insert-at", index, f"start={max(0, pos - 3)}")
                    mpv.command("playlist-remove", index + 1)
                    if again is not entry:
                        self.queue[index] = again
                        self.version += 1
                    mpv.command("playlist-play-index", index)
                    mpv.command("set_property", "pause", False)
                print(f"Server back, going on at {pos:.0f} s", file=sys.stderr)
                return True
            if proc.poll() is None:
                print("The server stayed away, stopping", file=sys.stderr)
            return False
        except (OSError, ValueError):
            return False
        finally:
            with self.lock:
                self.lost = None
            mpv.events.clear()

    def scrobble(self, song, started, heard):
        if song and heard > 0 and heard >= min((song.get("duration") or 0) / 2, 240):
            self.report(song["id"], submission=True, at=started)

    def report(self, song_id, submission, at=None):
        """Navidrome's "now playing" and its play counts; failing quietly, the music goes on regardless.
        Off in the settings ("scrobble"): Navidrome hears nothing of what plays, and keeps no history of it."""
        if setting("scrobble") == "off":
            return
        params = {"id": song_id, "submission": "true" if submission else "false"}
        if at:
            params["time"] = int(at * 1000)
        threading.Thread(target=self._report, args=(params,), daemon=True).start()

    def _report(self, params):
        try:
            nd("scrobble", **params)
        except NavidromeError as e:
            print(f"Navidrome scrobble failed: {e}", file=sys.stderr)


music = MusicPlayer()


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import system  # noqa: E402,F401
