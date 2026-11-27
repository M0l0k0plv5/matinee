"""The player's audio and subtitles (backend/video_player.py's Player has these as its own): following what plays,
for Jellyfin's dashboard and to remember it per show; subtitle files fetched after the start or found on the
internet, put into mpv; the volume kept for next time."""
import sys
import urllib.request

from .core import changing, RUNTIME, save_state, state, t, TRACKS_FILE, write_private
from .jellyfin_api import reload_token
from .playback import lang_codes, LATE_SUB_TIMEOUT, load_tracks, sub_allowed, tracks_key


class Tracks:
    """Player's part for the tracks: mixed into Player, whose playbacks (p) and mpv it is handed."""

    def follow_tracks(self, mpv, p, session):
        """Keep up with the audio and subtitles playing: for Jellyfin's dashboard and to remember them per show."""
        try:
            if "choice" not in p:  # just started: pick a remembered subtitle file, mpv cannot tell its language
                self.pick_sub_file(mpv, p)
            audio = mpv.get("current-tracks/audio") or {}
            sub = mpv.get("current-tracks/sub")
        except OSError:
            return
        file_sub = p["sub_streams"].get((sub or {}).get("external-filename"), {})
        choice = {"audio": audio.get("lang"), "sub": "no" if sub is None else sub.get("lang") or file_sub.get("lang")}
        p.setdefault("initial", choice)  # what was picked automatically
        p["choice"] = choice
        if session["PlayMethod"] == "DirectPlay":
            session["AudioStreamIndex"] = audio.get("ff-index")
            session["SubtitleStreamIndex"] = -1 if sub is None else file_sub.get("index", sub.get("ff-index"))

    def show_remote_subs(self, mpv, p):
        """What find_remote_subs and get_remote_sub have found, shown: the subtitles found in the info panel, the ones
        downloaded among the subtitles and picked."""
        found = p.pop("subs_found", None)
        if found is not None:
            mpv.command("set_property", "user-data/kino/subfind", found)
        got = p.pop("sub_got", None)
        if got is None:
            return
        if not got:
            return mpv.command("show-text", t("sub_failed"), 4000)
        mpv.command("sub-add", got["url"], "select", "", got["lang"] or "")
        p["sub_streams"][got["url"]] = {"lang": got["lang"], "index": got["index"], "forced": False}
        mpv.command("set_property", "user-data/kino/sublangs", {u: x["lang"] or "" for u, x in p["sub_streams"].items()})
        mpv.command("show-text", t("sub_added"), 3000)

    def add_late_sub(self, mpv, p):
        """The subtitles fetched after the start (fetch_late_sub): shown if forced, else as pick_sub_file decides."""
        late = p.get("late_sub")
        if not late or "file" not in late or late.get("added"):
            return
        late["added"] = True
        path = str(late["file"])
        try:
            mpv.command("sub-add", path, "auto", "", late["lang"] or "")
            p["sub_streams"][path] = {"lang": late["lang"], "index": late["index"], "forced": late["forced"]}
            mpv.command("set_property", "user-data/kino/sublangs", {u: x["lang"] or "" for u, x in p["sub_streams"].items()})
            track = next((t for t in mpv.get("track-list") or [] if t.get("external-filename") == path), None)
            untouched = p.get("choice") == p.get("initial")
            if track and late["forced"] and not mpv.get("current-tracks/sub"):
                mpv.command("set_property", "sid", track["id"])
            else:
                self.pick_sub_file(mpv, p)
            if untouched and "initial" in p:  # what Matinee put in by itself is no choice of someone's to remember
                sub = mpv.get("current-tracks/sub")
                p["initial"] = {**p["initial"], "sub": "no" if sub is None else sub.get("lang") or late["lang"]}
        finally:
            late["file"].unlink(missing_ok=True)  # mpv has read it

    def pick_sub_file(self, mpv, p):
        """mpv does not know the language of subtitle files: pick the one chosen for the show, or the one
        the settings ask for when mpv found no subtitles of its own."""
        prefs = p["prefs"]
        if prefs["sub"] == "no" or prefs["sid"] == "no":
            return
        if prefs["sub"]:
            codes, audio = lang_codes(prefs["sub"]), ""  # chosen: whatever the audio
        else:
            if mpv.get("current-tracks/sub"):
                return
            codes = prefs["slang"].split(",")
            audio = ((mpv.get("current-tracks/audio") or {}).get("lang") or "").lower()
        files = [(url, s) for url, s in p["sub_streams"].items() if s["lang"] and s["lang"].lower() in codes
                 and sub_allowed(s["lang"], s.get("forced"), audio, prefs["matching"])]
        # "auto": in the language of the audio only forced ones; otherwise the full ones first
        for url, s in sorted(files, key=lambda f: (codes.index(f[1]["lang"].lower()), bool(f[1].get("forced")))):
            track = next((t for t in mpv.get("track-list") or [] if t.get("external-filename") == url), None)
            if track:
                mpv.command("set_property", "sid", track["id"])
                return

    def remember_tracks(self, p):
        """Keep what someone changed by hand for the show, so its next episodes start with it; for a film, so it goes
        on with it."""
        key = tracks_key(p["item"])
        if not key or "choice" not in p:
            return
        changed = {k: v for k, v in p["choice"].items() if v and v != p["initial"][k]}
        if p["session"]["PlayMethod"] != "DirectPlay":
            # converted: the stream has the one audio track Matinee asked for, but subtitles turned on or off count (a
            # film that played converted once after they were turned off came back with them, again and again)
            changed = {k: v for k, v in changed.items() if k == "sub"}
        if p["prefs"].get("subs_off"):  # turned on for this playback only (track_prefs), as set when it started
            changed.pop("sub", None)
        if changed:
            try:  # the disk full: not kept, but the end of the playback (its report to Jellyfin) goes on
                with changing(TRACKS_FILE, load_tracks) as tracks:  # under its lock: the sync writes it too
                    tracks[key] = {**tracks.get(key, {}), **changed}
            except OSError as e:
                print(f"Tracks not kept: {e}", file=sys.stderr)

    def remember_volume(self, mpv, volume=None):
        """Keep the volume for next time; `volume` if given (the sleep timer turned it down to nothing)."""
        volume = volume if volume is not None else mpv.props.get("volume") if mpv else None
        if volume is not None and volume != state.get("volume", 100):
            reload_token()  # do not write back an old token if set-password ran meanwhile
            state["volume"] = volume
            try:  # as remember_tracks: never in the way of the end of a playback
                save_state()
            except OSError as e:
                print(f"Volume not kept: {e}", file=sys.stderr)


def fetch_late_sub(late, session_id):
    """Fetch subtitles Jellyfin first takes out of the file, into a file for mpv (late["file"] once it is there)."""
    try:
        with urllib.request.urlopen(late["url"], timeout=LATE_SUB_TIMEOUT) as r:
            data = r.read()
        path = RUNTIME / f"kino-sub-{session_id}.{late['ext']}"
        write_private(path, data.decode("utf-8", "replace"))
        late["file"] = path
        if late.get("ended"):  # come after its playback: nobody takes it (see drop_sub_file)
            path.unlink(missing_ok=True)
    except (OSError, ValueError) as e:
        print(f"Subtitles not loaded: {e}", file=sys.stderr)


def drop_sub_file(sub):
    """At the end of a playback: its subtitle file fetched aside (late_sub, a programme's subs) removed, had mpv not
    taken it yet. One still on its way removes itself once it is there, as it sees "ended" (set before "file" is read
    here, and "file" set there before "ended" is read: one of the two finds the other)."""
    if sub is not None:
        sub["ended"] = True
        if sub.get("file"):
            sub["file"].unlink(missing_ok=True)
