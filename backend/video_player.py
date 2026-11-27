"""Movies, episodes and one's own channels in mpv: the player (its mpv client in mpv_ipc, the sleep timer in sleeping,
going on on another Matinee in handoff, the pictures for seeking in trickplay, the reports to Jellyfin in jf_reports)."""
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import core, display
from .core import (ASK_TIMEOUT, CUT_OFF_MARGIN, PAUSE_LIMIT,
    RECONNECT_EVERY, RECONNECT_FOR, ROOT, RUNTIME, state, t, TICKS, TRACK_LANGS,
    WEB, write_private)
from .jellyfin_api import jf, log_failure
from .desktop import focus_player, focus_ui, mpris_script, natural_scroll
from .sound import audio_delay, audio_output, volume_keys
from .playback import about_item, panel_texts, prepare, remember_early
from .preferences import movie_sound, setting, SUB_BOX, SUB_SCALE
from .display import match_rate, switch_mode
from .mpv_ipc import Mpv, osd_text
from .sleeping import sleep_timer
from .jf_reports import load_pending, pending_lock, report, save_pending
from .trickplay import Previews
from .handoff import handoff_targets, send_handoff
from .player_tracks import drop_sub_file, fetch_late_sub, Tracks


def end_session(session):
    """Report the end of a playback and stop Jellyfin's transcoding for it."""
    report("/Sessions/Playing/Stopped", session, critical=True)
    stop_transcoding(session)


def stop_transcoding(session):
    if session.get("PlayMethod") == "Transcode":
        try:
            jf(f"/Videos/ActiveEncodings?deviceId={state['device_id']}&playSessionId={session['PlaySessionId']}", "DELETE")
        except Exception as e:
            log_failure("Could not stop the transcoding", e)  # Jellyfin also stops it by itself after a while


SKIPPABLE = ("Intro", "Recap", "Preview")


def segments(item_id):
    """Intro, recap and credits (e.g. from the Intro Skipper plugin) in seconds; empty if the server has none."""
    try:
        items = jf(f"/MediaSegments/{item_id}").get("Items", [])
    except urllib.error.HTTPError as e:
        if e.code != 404:  # a server without segments says so; anything else is worth a line
            log_failure("Could not load intro and credits", e)
        return []
    except Exception as e:
        log_failure("Could not load intro and credits", e)
        return []
    return [{"type": s["Type"], "start": s["StartTicks"] / TICKS, "end": s["EndTicks"] / TICKS}
            for s in items if s.get("Type") in (*SKIPPABLE, "Outro")]


def skip_offer(segs, pos, has_next):
    """What OK would skip right now: {"label", "to"} with a target time or "next" for the next episode."""
    for s in segs:
        if s["start"] <= pos < s["end"] - 1:
            if s["type"] in SKIPPABLE and setting("skipintro") != "off":
                return {"label": t(s["type"]), "to": s["end"], "since": pos - s["start"], "type": s["type"],
                        "start": s["start"]}
            if s["type"] == "Outro" and has_next:
                return {"label": t("next"), "to": "next", "since": pos - s["start"]}
    return None


def next_episode(item):
    if item.get("Type") != "Episode" or not item.get("SeriesId"):
        return None
    eps = jf(f"/Shows/{item['SeriesId']}/Episodes?userId={state['user_id']}&startItemId={item['Id']}&isMissing=false&Limit=10")["Items"]
    # specials are skipped, unless one special follows another
    return next((e for e in eps[1:] if e.get("ParentIndexNumber") != 0 or item.get("ParentIndexNumber") == 0), None)


KEEP_NOW_WAIT = 3  # seconds Matinee ending waits for Jellyfin to take the end of what plays (Player.keep_now)
MPV_CONNECT_SECONDS = float(os.environ.get("KINO_MPV_CONNECT", "20"))  # how long a started mpv may take to answer
OWN_FAILED_MAX = 3  # programmes of one's own channel failing one after another before it ends


def in_credits(segs, pos):
    """Whether a film or episode was stopped in its credits (Jellyfin's "Outro" segment, e.g. from Intro Skipper)."""
    return any(s["type"] == "Outro" and pos >= s["start"] - 1 for s in segs or [])


def mark_watched(item, pos_ticks=0):
    """A film or episode stopped in its credits: watched, off Continue Watching. Jellyfin only counts it so from 90 %
    on, and some credits begin before that. Also when it was watched before: the position just reported keeps a film
    seen again on Continue Watching until it is marked anew (that clears it). Not from 90 % on: Jellyfin has counted
    it itself then, and marked again it would count the one viewing twice."""
    run = item.get("RunTimeTicks") or 0
    if run and pos_ticks >= run * 0.9:
        return
    try:
        jf(f"/UserPlayedItems/{item['Id']}?userId={state['user_id']}", "POST")
    except Exception as e:
        log_failure("Could not mark it watched", e)


class Player(Tracks):
    def __init__(self):
        self.proc = None
        self.thread = None
        self.item_id = None
        self.error = None  # why the last playback could not start, for the UI
        self.lock = threading.Lock()
        self.starting = None  # item being prepared right now (measuring the line takes a moment)
        self.starting_lock = threading.Lock()
        self.sock = None  # the socket of the mpv playing now
        self.handoff_to = None  # the device to go on on, chosen in the info panel (hand_off)
        self.jf_session = None  # a film or episode playing: its session as reported to Jellyfin, for keep_now
        self.jf_item = None  # and the film or episode itself

    def keep_now(self):
        """Matinee ends while something plays (Cmd+Q, an update, switching off): where it is kept first, as Back would
        have: a film or episode told to Jellyfin as stopped (its session shown as playing otherwise), waited for a few
        seconds at most: still on its way then (a slow server), it is kept among the reports to send (PENDING_FILE) and
        goes at the next start, as one that failed would. A channel's programme keeps nothing."""
        session, item = self.jf_session, self.jf_item
        if session:
            remember_early(item or {}, session.get("PositionTicks", 0) / TICKS)
            told = threading.Thread(target=end_session, args=(session,), daemon=True)
            told.start()
            told.join(KEEP_NOW_WAIT)
            if told.is_alive() and pending_lock.acquire(timeout=1):
                try:
                    q = load_pending()
                    q.append({"path": "/Sessions/Playing/Stopped", "body": dict(session), "at": time.time()})
                    save_pending(q)
                    print("Jellyfin did not answer in time: its stop report kept for the next start", file=sys.stderr)
                finally:
                    pending_lock.release()

    def remote(self, *command):
        """A command of Jellyfin's remote control (remote_command in live_events) to the mpv playing, on its own
        connection beside the one watching it; False if none plays or it could not be reached."""
        if not self.playing or not self.sock:
            return False
        try:
            m = Mpv(self.sock)
            m.command(*command)
            m.sock.close()
        except OSError:
            return False
        return True

    def remote_track(self, mpv, p, kind, index):
        """Jellyfin's remote control chose an audio track or subtitles ("audio" or "sub") by its index among the item's
        streams: the same one in mpv, by the index Matinee reports (follow_tracks). Only while the file itself plays: a
        converted stream has other tracks."""
        try:
            index = int(index)
        except ValueError:
            return
        if p["session"]["PlayMethod"] != "DirectPlay":
            return
        if kind == "sub" and index < 0:
            return mpv.command("set_property", "sid", "no")
        files = {s.get("index"): url for url, s in p["sub_streams"].items()}
        for track in mpv.get("track-list") or []:
            if track.get("type") != kind:
                continue
            if (files.get(index) == track.get("external-filename") if track.get("external")
                    else track.get("ff-index") == index):
                return mpv.command("set_property", "aid" if kind == "audio" else "sid", track["id"])

    def say(self, text, seconds):
        """A message in the player while it plays (on its own connection, beside the one watching it)."""
        if not self.playing or not self.sock:
            return
        try:
            m = Mpv(self.sock)
            m.command("show-text", osd_text(text), int(seconds * 1000))
            m.sock.close()
        except OSError:
            pass

    @property
    def playing(self):
        # counts as playing until the final position has been reported to Jellyfin,
        # otherwise the UI would reload and still show the old progress
        return self.thread is not None and self.thread.is_alive()

    def play(self, item_id, resume, at=None):
        # mpv starts empty and gets the file over IPC (see load), like every next episode: stream URLs
        # carry the access token and must not show up in the process list
        self.run(item_id, lambda: prepare(item_id, resume, at), self.monitor, ["--idle=once", "--ytdl=no", *movie_sound()])

    def play_tv(self, item):
        """One of one's own channels live: what runs there now, where it is by now (own_tune); with --idle a programme
        that cannot be played leaves mpv idle, so the UI can say so."""
        p = {"kind": "tv", "url": "", "title": item["title"], "id": item["id"], "item": item, "start": 0}

        def prepare():
            p.update(self.own_tune(item["live"]))
            return p
        self.run(f"tv:{item['id']}", prepare, self.monitor_channel, ["--idle=yes", "--ytdl=no"])

    # Going on on another device (the info panel's "Continue on"): the other Matinees of the account that Jellyfin has a
    # session of are offered (user-data/kino/handoff); one chosen, the player here ends as with Back (the position
    # kept: with Jellyfin, or in the files kept alike) and the other device is told through Jellyfin what to play and
    # where (a message to its session, HANDOFF_HEADER), which it starts at once (take_handoff).
    def offer_handoff(self, sock_path):
        def offer():
            targets = handoff_targets()
            if not targets:
                return
            try:
                m = Mpv(sock_path)
                m.command("set_property", "user-data/kino/handoff", [{"id": d["id"], "name": d["name"]} for d in targets])
                m.sock.close()
            except OSError:
                pass
        self.handoff_to = None
        threading.Thread(target=offer, daemon=True).start()

    def note_handoff(self, events):
        for e in events:
            args = e.get("args") or []
            if e.get("event") == "client-message" and len(args) == 2 and args[0] == "kino-handoff":
                self.handoff_to = args[1]

    def hand_off(self, what):
        target, self.handoff_to = self.handoff_to, None
        if target:
            threading.Thread(target=send_handoff, args=(target, what), daemon=True).start()

    def run(self, key, prepare, monitor, options, env=None):
        # OK pressed twice on a remote: the second press must not restart the same item
        with self.starting_lock:
            if self.starting == key:
                return
            self.starting = key
        try:
            self.launch(key, prepare, monitor, options, env)
        finally:
            with self.starting_lock:
                if self.starting == key:
                    self.starting = None

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()

    def launch(self, key, prepare, monitor, options, env=None):
        music_player.music.stop()
        self.t0 = time.monotonic()  # for the log: how long it took to the first picture
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait()
            if self.thread:
                self.thread.join(10)
            p = prepare()
            t_prepared = time.monotonic()
            restore = match_rate(p.get("fps"))  # a channel live has none
            print(f"Starting {key}: prepared in {t_prepared - self.t0:.1f} s, display switched in "
                  f"{time.monotonic() - t_prepared:.1f} s", file=sys.stderr)
            # one socket per playback, otherwise the old thread would remove the new one
            sock = RUNTIME / f"kino-mpv-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
            args = [
                "mpv", f"--config-dir={ROOT / 'mpv'}", "--no-terminal",
                f"--input-ipc-server={sock}", "--osd-playing-msg-duration=4000",
                f"--alang={TRACK_LANGS[core.LANG][0]}", f"--slang={TRACK_LANGS[core.LANG][1]}",
                f"--volume={state.get('volume', 100)}",  # as it was left last time
                f"--sub-scale={SUB_SCALE[setting('subsize')]}",
                *(SUB_BOX if setting("substyle") == "box" else []),
                *audio_delay(audio_output()),  # a Bluetooth speaker's lag, as set for it
                "--force-window=immediate",  # a window at once, with the turning ring of kino.lua until the picture
                f"--osd-fonts-dir={WEB / 'fonts'}",  # Allo, the UI's font, for the player's texts too
                f"--script-opts-append=kino-ends={t('ends', time='%H:%M')}",  # "Ends at 23:41" in the bar (kino.lua)
                f"--script-opts-append=kino-natural={'yes' if natural_scroll() else 'no'}",  # the wheel's volume turned round
                # on a desktop, how to reach the info panel (a keyboard there has no menu key): under the bar
                f"--script-opts-append=kino-menu={'' if display.IN_CAGE else t('playerMenu')}",
                *mpris_script(),  # a desktop's media keys reach the player too
                # macOS: full screen over the window it is on, without a Space of its own sliding in at each start
                # and out at the end (a second each); "native-fs" is an option of mpv on macOS only
                *(["--native-fs=no"] if sys.platform == "darwin" else []),
                # in cage only Wayland: no X11 to fall back to when Vulkan and OpenGL fail there (mpv falls over
                # on that, an assertion in its x11 code), but gpu's own fallback (mpv.conf) instead
                *(["--gpu-context=waylandvk,wayland"] if display.IN_CAGE and sys.platform == "linux" else []),
                *options,  # last, so they win
            ]
            self.item_id = key
            self.error = None
            self.sock = sock
            self.proc = subprocess.Popen(args, env={**os.environ, **(env or {})})
            self.thread = threading.Thread(target=self.watch, args=(monitor, self.proc, sock, p, restore), daemon=True)
            self.thread.start()
            threading.Thread(target=focus_player, args=(self.proc,), daemon=True).start()

    def watch(self, monitor, proc, sock, p, restore):
        try:
            monitor(proc, sock, p)
            # mpv killed (it crashed, or ran out of memory) rather than ended or stopped by Matinee (SIGTERM): the UI says
            # so, it came back to the page without a word
            code = proc.poll()
            if code is not None and code < 0 and code != -signal.SIGTERM and not self.error:
                print(f"mpv ended by signal {-code}", file=sys.stderr)
                self.error = "crashed"
        finally:
            for output, mode in restore:  # the menus get their refresh rate back
                switch_mode(output, mode)

    def connect(self, proc, sock_path):
        for _ in range(int(MPV_CONNECT_SECONDS / 0.05)):
            if proc.poll() is not None:
                return None
            try:
                mpv = Mpv(sock_path)
            except OSError:
                time.sleep(0.05)
                continue
            return mpv
        # running, but not to be reached (its socket could not be made: a path too long, a full disk): nothing could
        # be controlled or reported, so it is stopped and the UI says so instead of "playing" for ever
        print(f"mpv not reachable at {sock_path} after {MPV_CONNECT_SECONDS:.0f} s: stopped", file=sys.stderr)
        self.error = "unreachable"
        proc.terminate()
        return None

    def monitor(self, proc, sock_path, p):
        session = p["session"]
        self.jf_session, self.jf_item = session, p["item"]  # told to Jellyfin should Matinee end meanwhile (keep_now)
        mpv = self.connect(proc, sock_path)  # connect first so an early key press is not lost
        try:
            if mpv:
                mpv.observe("pause", "eof-reached", "volume")
                mpv.command("set_property", "user-data/kino/ui", panel_texts())
                mpv.command("set_property", "user-data/kino/subsearch", True)  # "Find Subtitles…" in the info panel
                self.load(mpv, p)
                self.started(mpv, p, session)
                self.offer_handoff(sock_path)
        except OSError:
            mpv = None
        report("/Sessions/Playing", session)
        last_report = 0
        offer = None
        skipped = set()      # intros skipped by themselves (skipintro "auto"): once, going back into one shows it
        slept = False        # stopped by the sleep timer
        kept_volume = None   # the volume before it faded out
        auto = 0             # episodes started automatically since the last key press
        asked = None         # when "Still watching?" was asked
        paused_since = None
        while proc.poll() is None and mpv:
            try:
                mpv.wait(1)
                pos = mpv.get("time-pos")
            except (OSError, ValueError):
                break
            if pos is not None:
                session["PositionTicks"] = int(pos * TICKS)
                session["IsPaused"] = bool(mpv.props.get("pause"))
                self.add_late_sub(mpv, p)
                self.follow_tracks(mpv, p, session)
                offer = self.update_offer(mpv, p["segments"], pos, bool(p["next"]), offer, skipped)
            ended = mpv.props.get("eof-reached")
            try:
                volume_keys(mpv)
            except OSError:
                break
            messages = [(e.get("args") or [None])[0] for e in mpv.events if e.get("event") == "client-message"]
            previews = [e["args"][1:3] for e in mpv.events if e.get("event") == "client-message"
                        and e["args"][0] == "kino-preview" and len(e["args"]) == 3]
            subs_wanted = [e["args"][1:] for e in mpv.events if e.get("event") == "client-message"
                           and e["args"][0] in ("kino-find-subs", "kino-get-sub")]
            tracks_wanted = [e["args"][1:3] for e in mpv.events if e.get("event") == "client-message"
                             and e["args"][0] == "kino-remote-track" and len(e["args"]) == 3]
            self.note_handoff(mpv.events)
            mpv.events.clear()
            try:
                for kind, index in tracks_wanted:  # chosen with Jellyfin's remote control (remote_command)
                    self.remote_track(mpv, p, kind, index)
                for args in subs_wanted:  # the info panel's "Find Subtitles…" (kino.lua): the server asked aside
                    if args:
                        mpv.command("show-text", t("sub_getting"), 30000)
                    threading.Thread(target=library.get_remote_sub if args else library.find_remote_subs, args=(p, *args[:1]),
                                     daemon=True).start()
                self.show_remote_subs(mpv, p)
            except OSError:
                break
            for index, height in previews:  # seeking: the picture where the mark is (kino.lua)
                if mpv.previews and index.isdigit() and height.isdigit():
                    mpv.previews.want(int(index), int(height))
            skip = "kino-next" in messages
            if skip or "kino-active" in messages:  # every key in the player sends kino-active (input.conf)
                auto = 0
            if "kino-sleep" in messages:
                sleep_timer.cycle()
                try:
                    mpv.command("show-text", sleep_timer.label(), 2500)
                except OSError:
                    break
            if sleep_timer.due():
                try:
                    kept_volume = sleep_timer.fade(mpv)  # kept as it was, not as faded
                    mpv.command("quit")
                except OSError:
                    pass
                slept = True
                break

            if ended and not skip and self.cut_off(mpv, p, pos):
                loaded = self.reconnect(mpv, proc, p, session)
                if not loaded:
                    break
                drop_sub_file(p.get("late_sub"))
                p, session = loaded, loaded["session"]
                self.jf_session, self.jf_item = session, p["item"]
                offer = None
                last_report = time.monotonic()
                continue

            paused_since, too_long = self.long_pause(mpv, paused_since)
            if too_long:
                try:
                    mpv.command("quit")
                except OSError:
                    pass
                break

            if ended and not skip and not p["next"] and p["item"].get("Type") == "Episode" and not p.get("next_looked"):
                p["next_looked"] = True  # once: a show at its last episode is not asked for again and again
                try:  # the lookup at the start may have failed while the server was briefly away (or slow)
                    p["next"] = next_episode(p["item"])
                except Exception as e:
                    print(f"Next episode not found: {e}", file=sys.stderr)

            ask = setting("ask")
            if ended and not skip and p["next"] and ask != "off" and auto + 1 >= int(ask):
                # nobody has touched the remote for a few episodes, maybe everyone fell asleep:
                # ask before playing on, and stop if nobody answers
                try:
                    if asked is None:
                        asked = time.monotonic()
                        mpv.command("set_property", "user-data/kino/skip", {"label": t("still"), "to": "next"})
                    if time.monotonic() - asked > ASK_TIMEOUT:
                        mpv.command("quit")
                        break
                    mpv.command("show-text", t("still"), 1500)
                except OSError:
                    break
                continue

            if ended and not skip and sleep_timer.at_end:
                try:
                    mpv.command("quit")
                except OSError:
                    pass
                slept = True
                break

            if ended or skip:
                nxt = p["next"]
                if not nxt:
                    if ended:
                        try:
                            mpv.command("quit")
                        except OSError:
                            pass
                        break
                    continue
                self.remember_tracks(p)
                end_session(session)
                remember_early(p["item"], session["PositionTicks"] / TICKS)
                countdown = bool(ended) and not skip
                loaded = self.load_next(mpv, proc, nxt, countdown=countdown)
                if not loaded:
                    break
                drop_sub_file(p.get("late_sub"))
                p, session = loaded, loaded["session"]
                self.jf_session, self.jf_item = session, p["item"]
                self.started(mpv, p, session)
                offer, asked = None, None
                auto = auto + 1 if countdown else 0
                report("/Sessions/Playing", session)
                last_report = time.monotonic()
                continue

            if time.monotonic() - last_report > 10:
                report("/Sessions/Playing/Progress", session)
                last_report = time.monotonic()

        proc.wait()
        # this playback's own: the next one may have begun already (its launch waits only so long for this thread),
        # with pictures and the screen of its own
        if self.proc is proc:
            focus_ui()
        if mpv and mpv.previews:
            mpv.previews.close()
            mpv.previews = None
        drop_sub_file(p.get("late_sub"))
        self.remember_tracks(p)
        self.remember_volume(mpv, kept_volume)
        if self.jf_session is session:
            self.jf_session = None
        end_session(session)  # where it was, with Jellyfin: the other device goes on from there
        remember_early(p["item"], session["PositionTicks"] / TICKS)
        if in_credits(p.get("segments"), session["PositionTicks"] / TICKS) and not self.handoff_to:
            mark_watched(p["item"], session["PositionTicks"])
        self.hand_off({"kind": "jf", "id": p["item"]["Id"], "pos": session.get("PositionTicks", 0) / TICKS})
        if mpv:
            mpv.close()
        sock_path.unlink(missing_ok=True)
        if slept:
            sleep_timer.done()

    def monitor_channel(self, proc, sock_path, p):
        """One of one's own channels live: nothing to report, its programmes play one after another until Back."""
        mpv = self.connect(proc, sock_path)
        try:
            if mpv:
                mpv.observe("pause", "eof-reached", "volume")
                mpv.command("enable_event", "end-file")
                mpv.command("set_property", "force-media-title", p["title"])
                mpv.command("set_property", "user-data/kino/ui", panel_texts())
                mpv.command("set_property", "user-data/kino/about", {"title": p["title"], "line": p["item"]["channel"], "text": ""})
                self.own_show(mpv, p)
                self.offer_handoff(sock_path)
        except OSError:
            mpv = None
        paused_since = None
        slept = False
        kept_volume = None
        watched = {"pos": None, "duration": None}  # where the programme is
        # what runs, from the channel's programme (looked at every half minute, beside the loop), shown when it starts
        # and whenever OK is pressed (kino.lua)
        live = p["item"]["live"]
        guide = {"now": None, "at": 0.0, "title": None, "info": None}
        if mpv:
            try:
                mpv.command("set_property", "user-data/kino/live", True)  # up and down change the channel (kino.lua)
            except OSError:
                pass
        played = False  # since the last load: a stream that never played is broken, not broken off
        own_failed = 0  # programmes of one's own channel that could not be played, one after another
        while proc.poll() is None and mpv:
            try:
                mpv.wait(1)
            except (OSError, ValueError):
                break
            failed = any(e.get("event") == "end-file" and e.get("reason") == "error" for e in mpv.events)
            try:
                volume_keys(mpv)
            except OSError:
                break
            messages = [(e.get("args") or [None])[0] for e in mpv.events if e.get("event") == "client-message"]
            self.note_handoff(mpv.events)
            mpv.events.clear()
            try:
                pos = mpv.get("time-pos")
                if pos is not None:  # at the end too: where it ended is where it was
                    played = played or not mpv.props.get("eof-reached")
                    watched.update(pos=pos, duration=mpv.get("duration") or watched["duration"])
                self.live_info(mpv, p, live, guide)
                if pos is not None and p.get("own"):
                    self.own_subtitles(mpv, p)
                if "kino-sleep" in messages:
                    sleep_timer.cycle()
                    mpv.command("show-text", sleep_timer.label(), 2500)
                if "kino-zap-next" in messages or "kino-zap-prev" in messages:
                    live = self.zap(mpv, p, live, 1 if "kino-zap-next" in messages else -1, guide)
                    played, failed, own_failed = False, False, 0  # another channel: its own count
                if sleep_timer.due() or (sleep_timer.at_end and mpv.props.get("eof-reached")):
                    if sleep_timer.due():
                        kept_volume = sleep_timer.fade(mpv)
                    mpv.command("quit")
                    slept = True
                    break
            except OSError:
                break
            paused_since, too_long = self.long_pause(mpv, paused_since)
            ended = failed or mpv.props.get("eof-reached")
            if ended and played and self.broke_off(p, watched):
                # the network or the server away: wait for it and go on where it was
                p["cut_at"] = watched["pos"]
                if self.stream_back(mpv, proc, p, max(0, watched["pos"] - 3)):
                    continue
                try:
                    mpv.command("quit")
                except OSError:
                    pass
                break
            # the programme is over, or its file could not be played (gone, or not to be transcoded): the channel's
            # next one, as a channel goes on; a few that fail one after another end it
            if p.get("own") and not too_long and (failed and own_failed < OWN_FAILED_MAX or not failed and mpv.props.get("eof-reached")):
                own_failed = own_failed + 1 if failed else 0
                if failed:
                    print(f"{p['title']}: a programme could not be played, the next one instead", file=sys.stderr)
                try:
                    self.own_show(mpv, p, self.own_tune(live, after=p["own"]["jf"]))
                    watched.update(pos=None, duration=None)
                    played = False
                    guide["at"] = 0.0  # what runs now: looked up at once
                    continue
                except Exception as e:  # the library away: it ends, as a film would
                    print(f"{p['title']}: the next programme could not be played ({e!r})", file=sys.stderr)
            if failed:  # the programme gone from the library meanwhile, or a stream Jellyfin could not make
                print(f"{p['title']} could not be played", file=sys.stderr)
                self.error = "tv"
            if failed or too_long or mpv.props.get("eof-reached"):
                try:
                    mpv.command("quit")
                except OSError:
                    pass
                break
        proc.wait()
        if self.proc is proc:  # not when the next playback has begun already (as above)
            focus_ui()
        self.own_leave(p)
        self.remember_volume(mpv, kept_volume)
        self.hand_off({"kind": "tv", "live": live})
        if mpv:
            mpv.close()
        sock_path.unlink(missing_ok=True)
        if slept:
            sleep_timer.done()

    def broke_off(self, p, watched):
        """Whether a stream that played came to an end it should not have: a programme ended well before its end (see
        CUT_OFF_MARGIN), unless it stopped at the same place after going on there already."""
        pos, duration = watched["pos"], watched["duration"]
        if pos is None or not duration or duration - pos < CUT_OFF_MARGIN:
            return False
        return abs(pos - p.get("cut_at", -CUT_OFF_MARGIN * 2)) > CUT_OFF_MARGIN

    def stream_back(self, mpv, proc, p, start):
        """The message on the picture, and every few seconds the same address again (from where it was) until it
        plays. False when Back ended it or the stream stayed away for RECONNECT_FOR seconds
        (then the UI says so)."""
        print(f"{p['title']}: stream broke off, waiting for it", file=sys.stderr)
        end = time.monotonic() + RECONNECT_FOR
        lost = lambda: mpv.command("show-text", osd_text(t("lost")), 2000)
        try:
            while proc.poll() is None and time.monotonic() < end:
                mpv.command("set_property", "start", f"{start:.1f}" if start else "none")
                mpv.command("loadfile", p["url"], "replace")
                # paused at the end the broken stream reached (keep-open), mpv stays paused for the next file: it
                # came back standing still
                mpv.command("set_property", "pause", False)
                mpv.props["eof-reached"] = False
                tried = time.monotonic()
                while proc.poll() is None and time.monotonic() - tried < 20:  # one try: until it plays or fails
                    lost()
                    mpv.wait(1)
                    failed = any(e.get("event") == "end-file" and e.get("reason") == "error" for e in mpv.events)
                    mpv.events.clear()
                    if mpv.get("time-pos") is not None and not mpv.props.get("eof-reached"):
                        print(f"{p['title']}: stream back", file=sys.stderr)
                        mpv.command("show-text", "", 1)
                        return True
                    if failed:
                        break
                waited = time.monotonic()
                while proc.poll() is None and time.monotonic() - waited < RECONNECT_EVERY:
                    lost()
                    mpv.wait(1)
                    mpv.events.clear()
        except (OSError, ValueError):
            return False
        if proc.poll() is None:
            print(f"{p['title']}: the stream stayed away", file=sys.stderr)
            self.error = "connection"
        return False

    def zap(self, mpv, p, channel_id, step, guide):
        """The channel before or after, in the same window; its programme shows at once."""
        try:
            ids = [c["id"] for c in tv.tv_own()]
            new_id, name, url = tv.tv_live_channel(ids[(ids.index(channel_id) + step) % len(ids) if channel_id in ids else 0])
            tuned = self.own_tune(new_id)
        except (OSError, ValueError, KeyError, TypeError, LookupError) as e:
            print(f"Zapping from {channel_id}: {e}", file=sys.stderr)
            return channel_id
        tv.tv_remember_live(new_id)  # before it shows as playing: whoever sees the new channel finds it remembered
        p["item"] = {"id": f"live-{new_id}", "channel": name, "url": url, "live": new_id}
        p["title"] = name
        self.item_id = f"tv:live-{new_id}"
        guide.update(now=None, at=0.0, title=None, info=None)
        mpv.command("set_property", "user-data/kino/info", "")
        mpv.command("set_property", "force-media-title", name)
        self.own_show(mpv, p, tuned)
        mpv.command("set_property", "pause", False)  # another channel plays, also when the one before was paused
        mpv.command("show-text", osd_text(name), 2000)
        return new_id

    # One's own channels (tv.own_on): a film or episode of the library as a channel's programme, joined where it is by
    # now, the next one after it. Played like a channel live, so nothing of it is told to Jellyfin; only a stream
    # Jellyfin converts for it is stopped there when the programme is left.
    def own_tune(self, channel_id, after=None):
        """What one of one's own channels plays now (after: once that film or episode ended), ready for mpv."""
        programme, into = tv.own_on(channel_id, after)
        got = prepare(programme["jf"], False, at=into, aside=True)
        return {"url": got["url"], "start": got["start"] / TICKS, "prefs": got["prefs"], "sub_streams": got["sub_streams"],
                "own": {"jf": programme["jf"], "session": got["session"], "subs": got["subs"]}}

    def own_leave(self, p, mpv=None):
        """The programme left: a stream converted for it stopped; mpv (zapped to a channel of another kind): the
        languages as they were before the first of them."""
        own = p.pop("own", None)
        if own:
            threading.Thread(target=stop_transcoding, args=(own["session"],), daemon=True).start()
        for prop, value in (p.pop("own_before", {}) if mpv else {}).items():
            mpv.command("set_property", prop, value)

    OWN_PROPS = {"alang": "alang", "slang": "slang", "sid": "sid", "subs-with-matching-audio": "matching"}

    def own_show(self, mpv, p, tuned=None):
        """A channel's programme in mpv (tuned: the next one, in place of the one playing): with the languages chosen
        for its series or in the settings, as when it is played by itself."""
        if tuned:
            self.own_leave(p)
            p.update(tuned)
        p.pop("cut_at", None)
        if "own_before" not in p:
            p["own_before"] = {prop: mpv.get(prop) for prop in self.OWN_PROPS}
        for prop, pref in self.OWN_PROPS.items():
            mpv.command("set_property", prop, p["prefs"][pref])
        mpv.props["eof-reached"] = False  # the end of the programme before is handled
        mpv.command("set_property", "start", f"{p['start']:.1f}" if p["start"] else "none")
        mpv.command("loadfile", p["url"], "replace")
        mpv.command("set_property", "pause", False)

    def own_subtitles(self, mpv, p):
        """The programme's subtitle files, once it plays: there to choose, and picked as for a film (pick_sub_file)."""
        files = p["own"].pop("subs", None)
        if not files:
            return
        for url in files:
            mpv.command("sub-add", url, "auto", "", p["sub_streams"].get(url, {}).get("lang") or "")
        self.pick_sub_file(mpv, p)

    def live_info(self, mpv, p, channel_id, guide):
        if time.monotonic() - guide["at"] > 30:
            guide["at"] = time.monotonic()

            def look():  # an answer for a channel zapped away from meanwhile is dropped
                now = tv.tv_now().get(channel_id)
                if p["item"].get("live") == channel_id:
                    guide["now"] = now
            threading.Thread(target=look, daemon=True).start()
        now = guide["now"]
        if not now:
            return
        info = tv.tv_info(p["item"]["channel"], now)
        if info != guide["info"]:  # the minutes left, in what OK shows
            guide["info"] = info
            mpv.command("set_property", "user-data/kino/info", info)
        if now["title"] != guide["title"]:  # tuned in, or the next programme began
            guide["title"] = now["title"]
            mpv.command("set_property", "force-media-title", f"{p['item']['channel']} – {now['title']}")
            mpv.command("show-text", osd_text(info), 6000)

    def long_pause(self, mpv, since):
        """(paused since, too long): a picture standing still for PAUSE_LIMIT seconds stops playback,
        unless the screensaver is off."""
        if mpv.props.get("pause") and not mpv.props.get("eof-reached") and setting("screensaver") != "off":
            since = since or time.monotonic()
            if time.monotonic() - since > PAUSE_LIMIT:
                print("Paused for a long time, stopping playback", file=sys.stderr)
                return since, True
            return since, False
        return None, False

    def started(self, mpv, p, session):
        """Wait until the file plays, then note the tracks mpv picked by itself, before anyone can change them."""
        try:
            for _ in range(600):
                if mpv.get("time-pos") is not None:
                    print(f"First picture {time.monotonic() - self.t0:.1f} s after OK ({session['PlayMethod']})", file=sys.stderr)
                    break
                time.sleep(0.05)
        except OSError:
            return  # mpv has gone, the loop notices
        self.follow_tracks(mpv, p, session)

    def update_offer(self, mpv, segs, pos, has_next, current, skipped):
        """Arm the OK key in mpv (kino.lua) for skipping and show a short hint; with skipintro "auto" an intro,
        recap or preview is skipped right away instead."""
        offer = skip_offer(segs, pos, has_next)
        try:
            if offer and offer["to"] != "next" and setting("skipintro") == "auto" and offer["start"] not in skipped:
                skipped.add(offer["start"])
                print(f"Skipping {offer['type']} {offer['start']:.0f}-{offer['to']:.0f} s", file=sys.stderr)
                mpv.command("seek", offer["to"], "absolute")
                mpv.command("show-text", t(f"skipped_{offer['type']}"), 2000)
                if current:
                    mpv.command("set_property", "user-data/kino/skip", "")
                return None
            if offer and (not current or offer["to"] != current["to"]):
                mpv.command("set_property", "user-data/kino/skip", {"label": offer["label"], "to": offer["to"]})
            elif not offer and current:
                mpv.command("set_property", "user-data/kino/skip", "")
            if offer and offer["since"] < 10:  # hint only for the first seconds, OK works for the whole segment
                mpv.command("show-text", f"OK  ▸  {offer['label']}", 1300)
        except OSError:
            pass
        return offer

    def cut_off(self, mpv, p, pos):
        """At the end of the file well before its end: the stream broke off. Unless it stopped at the same place
        after going on there already: then the file itself is that short."""
        try:
            duration = mpv.get("duration") or p["item"].get("RunTimeTicks", 0) / TICKS
        except OSError:
            return False
        if pos is None or not duration or duration - pos < CUT_OFF_MARGIN:
            return False
        return abs(pos - p.get("cut_at", -CUT_OFF_MARGIN * 2)) > CUT_OFF_MARGIN

    def reconnect(self, mpv, proc, p, session):
        """Wait for the server with a message on the picture, then go on a few seconds before where it stopped, in
        the same window. None when Back ended it meanwhile or the server stayed away (the UI says so)."""
        pos = session["PositionTicks"] / TICKS
        print(f"Stream broke off at {pos:.0f} s, waiting for the server", file=sys.stderr)
        try:
            tracks = {"aid": mpv.get("aid"), "sid": mpv.get("sid")}
        except OSError:
            return None
        end = time.monotonic() + RECONNECT_FOR
        next_try = time.monotonic() + RECONNECT_EVERY
        while proc.poll() is None:
            if time.monotonic() > end:
                print("The server stayed away, stopping playback", file=sys.stderr)
                self.error = "connection"
                try:
                    mpv.command("quit")
                except OSError:
                    pass
                return None
            try:
                mpv.command("show-text", osd_text(t("lost")), 2000)
                mpv.wait(1)
                mpv.events.clear()
            except (OSError, ValueError):
                return None
            if time.monotonic() < next_try:
                continue
            next_try = time.monotonic() + RECONNECT_EVERY
            try:
                again = prepare(p["item"]["Id"], resume=False)
            except Exception:
                continue  # still away
            again.update(start=int(max(0, pos - 3) * TICKS), cut_at=pos, note=None)
            again["session"]["PositionTicks"] = again["start"]
            if "initial" in p and again["session"]["PlayMethod"] == p["session"]["PlayMethod"]:
                # what to remember for the show stays measured from the first start, but only against the same kind of
                # stream: a converted one may not carry the subtitles the file had, which is no one's choice of "no"
                again["initial"] = p["initial"]
            try:
                self.load(mpv, again)
                self.started(mpv, again, again["session"])
                if again["session"]["PlayMethod"] == p["session"]["PlayMethod"] == "DirectPlay":
                    for prop, value in tracks.items():  # the same file: the tracks someone picked by hand
                        if value is not None:
                            mpv.command("set_property", prop, "no" if value is False else value)
            except OSError:
                return None
            self.remember_tracks(p)
            end_session(session)
            print(f"Server back, going on at {pos:.0f} s", file=sys.stderr)
            report("/Sessions/Playing", again["session"])
            return again
        return None

    def load_next(self, mpv, proc, nxt, countdown):
        """Load the next episode in the same mpv window, with a short countdown at the end of an episode."""
        label = f"S{nxt.get('ParentIndexNumber', 0):02}E{nxt.get('IndexNumber', 0):02} – {nxt.get('Name', '')}"
        try:
            if countdown:
                for left in range(5, 0, -1):
                    mpv.command("show-text", osd_text(t("countdown", label=label, left=left)), 1100)
                    time.sleep(1)
                    if proc.poll() is not None:
                        return None
            p = prepare(nxt["Id"], resume=False)
            self.load(mpv, p)
            self.item_id = nxt["Id"]
            return p
        except Exception as e:
            print(f"Could not load next episode: {e}", file=sys.stderr)
            return None

    def load(self, mpv, p):
        """Play p in the running mpv: the first file as well as every next episode."""
        mpv.props["eof-reached"] = False  # the end of the previous episode is handled
        prefs = p["prefs"]
        for prop, value in (("alang", prefs["alang"]), ("slang", prefs["slang"]), ("sid", prefs["sid"]),
                            ("subs-with-matching-audio", prefs["matching"])):
            mpv.command("set_property", prop, value)
        mpv.command("set_property", "start", f"{p['start'] / TICKS:.2f}" if p["start"] else "none")
        mpv.command("set_property", "force-media-title", p["title"])
        mpv.command("set_property", "user-data/kino/about", about_item(p["item"]))
        if mpv.previews:  # the episode before's
            mpv.previews.close()
        mpv.previews = Previews.of(p["item"])
        mpv.command("set_property", "user-data/kino/trickplay", mpv.previews.for_mpv() if mpv.previews else "")
        # the subtitle files' languages, which mpv does not know: for the info panel's list
        mpv.command("set_property", "user-data/kino/sublangs", {u: x["lang"] or "" for u, x in p["sub_streams"].items()})
        mpv.command("set_property", "osd-playing-msg", osd_text(p["note"] or ""))
        mpv.command("change-list", "sub-files", "clr", "")
        for u in p["subs"]:
            mpv.command("change-list", "sub-files", "append", u)
        mpv.command("loadfile", p["url"], "replace")
        mpv.command("set_property", "pause", False)
        if p.get("late_sub"):
            threading.Thread(target=fetch_late_sub, args=(p["late_sub"], p["session"]["PlaySessionId"]), daemon=True).start()




player = Player()


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import (  # noqa: E402,F401
    library, tv, syncing, music_player,
)
