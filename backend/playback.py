"""What mpv needs to play an item from Jellyfin: audio and subtitle choices, positions kept ahead of the server, the
connection's speed, what the graphics chip decodes."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import age_limit, core
from .core import changing, conf, EARLY_FILE, read_json, state, STRINGS, t, TICKS, TRACK_LANGS, TRACKS_FILE
from .jellyfin_api import jf, log_failure


# "auto": measure the connection before each playback and let Jellyfin transcode if it is too slow for the file;
# a number: never use more than that many Mbit/s; "original": always play the file as it is
QUALITY = str(conf.get("quality", "auto"))
LATE_SUB_TIMEOUT = 300  # Jellyfin takes a minute or two to take subtitles out of a big film
TEXT_SUBS = {"subrip": "srt", "srt": "srt", "ass": "ass", "ssa": "ass", "webvtt": "vtt", "vtt": "vtt"}


def measure_bandwidth(url, size=0, seconds=2.0, enough=None, settle=0.5):
    """Bits per second, from downloading part of the file for a moment (None if that fails).
    The first `settle` seconds are not counted: buffers along the way and the provider's shaper let a burst of a
    megabyte or more through at once, which made a 10 Mbit/s line look like 30. Stops early once the line has
    been faster than `enough` for half a second after that, so at home playback starts after about a second."""
    req = urllib.request.Request(url, headers={"Range": f"bytes={size // 10}-"})
    got, first, base = 0, None, None  # base: (seconds, bytes) when the settling time was over
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            while time.monotonic() - start < seconds + settle:
                chunk = r.read1(65536)
                if not chunk:
                    break
                now = time.monotonic()
                if first is None:  # the first bytes include connection setup and latency
                    first = now
                    continue
                got += len(chunk)
                if base is None and now - first >= settle:
                    base = (now, got)
                elif base and enough and now - base[0] > 0.5 and (got - base[1]) * 8 / (now - base[0]) > enough:
                    break
    except Exception as e:
        print(f"Could not measure the connection: {e}", file=sys.stderr)
        return None
    end = time.monotonic()
    if base and end - base[0] > 0.2 and got > base[1]:
        return (got - base[1]) * 8 / (end - base[0])
    elapsed = end - (first or start)  # a file too short to get past the settling time
    return got * 8 / elapsed if got and elapsed > 0.2 else None


# What the graphics chip decodes itself (vainfo, from libva-utils): a video it cannot (4K HEVC with 10 bits on a
# Skylake thin client) would only stutter in software, so Jellyfin converts it to 1080p H.264, HDR mapped to SDR.
# Without vainfo nothing is known and every file plays as it is.
VA_PROFILES = {("h264", 8): "VAProfileH264High", ("hevc", 8): "VAProfileHEVCMain", ("hevc", 10): "VAProfileHEVCMain10",
               ("av1", 8): "VAProfileAV1Profile0", ("av1", 10): "VAProfileAV1Profile0", ("vp9", 8): "VAProfileVP9Profile0",
               ("vp9", 10): "VAProfileVP9Profile2", ("mpeg2video", 8): "VAProfileMPEG2Main", ("vc1", 8): "VAProfileVC1Advanced"}
CONVERT_BITRATE = 12_000_000  # 1080p H.264 from a 4K file: plenty, and light for the server's encoder
_va_profiles = None


def va_profiles():
    """The profiles the graphics chip decodes (VAEntrypointVLD), once; None if that cannot be found out."""
    global _va_profiles
    if _va_profiles is None:
        try:
            out = subprocess.run(["vainfo"], capture_output=True, text=True, timeout=10).stdout
            _va_profiles = {line.split(":")[0].strip() for line in out.splitlines() if "VAEntrypointVLD" in line} or set()
        except (OSError, subprocess.SubprocessError):
            _va_profiles = set()
    return _va_profiles or None


def cannot_decode(video):
    """A short description ("4K HDR HEVC 10 bit") if the graphics chip cannot decode this video stream, else None."""
    profiles = va_profiles()
    codec = (video.get("Codec") or "").lower()
    depth = 10 if (video.get("BitDepth") or 8) > 8 else 8
    need = VA_PROFILES.get((codec, depth), "" if (codec, 8) not in VA_PROFILES else "unknown")
    if not profiles or not need or need in profiles:
        return None
    hdr = (video.get("VideoRange") or "").upper() == "HDR"
    return " ".join(filter(None, ["4K" if (video.get("Height") or 0) > 1100 else None, "HDR" if hdr else None,
                                  codec.upper(), f"{depth} bit" if depth > 8 else None]))


def pick_bitrate(measured, source_bitrate):
    """None to play the file as it is, otherwise the total bitrate Jellyfin should transcode to."""
    if QUALITY == "original" or not source_bitrate:
        return None
    if QUALITY == "auto":
        if measured is None or measured >= source_bitrate * 1.3:  # some headroom for the line varying
            return None
        target = measured * 0.7
    else:
        target = float(QUALITY) * 1e6
        if source_bitrate <= target:
            return None
    return max(int(target), 400_000)  # below that there is no watchable picture anyway


# The same language under its different codes (ISO 639-1, 639-2/B and /T): a choice made on a file tagged
# one way also finds tracks tagged another way
LANG_ALIASES = [("de", "deu", "ger"), ("en", "eng"), ("fr", "fra", "fre"), ("es", "spa"), ("it", "ita"),
                ("nl", "nld", "dut"), ("pt", "por"), ("ru", "rus"), ("ja", "jpn"), ("ko", "kor"),
                ("zh", "zho", "chi"), ("cs", "ces", "cze"), ("pl", "pol"), ("tr", "tur"), ("sv", "swe"),
                ("da", "dan"), ("no", "nor", "nob"), ("fi", "fin"), ("el", "ell", "gre"), ("hu", "hun")]


# The languages' names in the info panel's lists (kino.lua), in the order of LANG_ALIASES
LANG_NAMES = {
    "en": ["German", "English", "French", "Spanish", "Italian", "Dutch", "Portuguese", "Russian", "Japanese", "Korean",
           "Chinese", "Czech", "Polish", "Turkish", "Swedish", "Danish", "Norwegian", "Finnish", "Greek", "Hungarian"],
    "de": ["Deutsch", "Englisch", "Französisch", "Spanisch", "Italienisch", "Niederländisch", "Portugiesisch", "Russisch",
           "Japanisch", "Koreanisch", "Chinesisch", "Tschechisch", "Polnisch", "Türkisch", "Schwedisch", "Dänisch",
           "Norwegisch", "Finnisch", "Griechisch", "Ungarisch"],
    "fr": ["Allemand", "Anglais", "Français", "Espagnol", "Italien", "Néerlandais", "Portugais", "Russe", "Japonais",
           "Coréen", "Chinois", "Tchèque", "Polonais", "Turc", "Suédois", "Danois", "Norvégien", "Finnois", "Grec", "Hongrois"],
    "es": ["Alemán", "Inglés", "Francés", "Español", "Italiano", "Neerlandés", "Portugués", "Ruso", "Japonés", "Coreano",
           "Chino", "Checo", "Polaco", "Turco", "Sueco", "Danés", "Noruego", "Finés", "Griego", "Húngaro"],
    "nl": ["Duits", "Engels", "Frans", "Spaans", "Italiaans", "Nederlands", "Portugees", "Russisch", "Japans", "Koreaans",
           "Chinees", "Tsjechisch", "Pools", "Turks", "Zweeds", "Deens", "Noors", "Fins", "Grieks", "Hongaars"],
}


def panel_texts():
    """The info panel's words (kino.lua, user-data/kino/ui): its tabs ("Chapter {n}" filled in there), and the
    languages under each of their codes."""
    texts = {k: STRINGS[core.LANG][f"p_{k}"] for k in ("info", "chapters", "audio", "subtitles", "off", "chapter", "forced",
                                                    "speed", "normal", "decimal", "find", "looking", "none", "failed",
                                                    "handoff", "handing")}
    texts["langs"] = {code: name for codes, name in zip(LANG_ALIASES, LANG_NAMES[core.LANG]) for code in codes}
    return texts


def about_item(item):
    """What the info panel tells about a Jellyfin item: its name, a line (the show and episode, or the year, genres
    and rating) and its description, left out for an episode not watched yet with spoilers hidden."""
    if item.get("Type") == "Episode":
        line = [item.get("SeriesName", ""), f"S{item.get('ParentIndexNumber', 0):02}E{item.get('IndexNumber', 0):02}"]
    else:
        line = [str(item.get("ProductionYear") or ""), ", ".join(item.get("Genres", [])[:2]), item.get("OfficialRating") or ""]
    hidden = preferences.setting("spoilers") == "hide" and item.get("Type") == "Episode" and not item.get("UserData", {}).get("Played")
    return {"title": item.get("Name", ""), "line": " · ".join(x for x in line if x),
            "text": "" if hidden else item.get("Overview") or ""}


def lang_codes(lang):
    lang = lang.lower()
    return next((list(a) for a in LANG_ALIASES if lang in a), [lang])


def load_tracks():
    """Audio and subtitles chosen per show, and per film: {series or film id: {"audio": lang, "sub": lang or "no"}}."""
    try:
        return json.loads(TRACKS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def tracks_key(item):
    """Where a choice of audio and subtitles is kept: a show's for all its episodes, any other video its own (a film
    switched to its German track and stopped came back in English)."""
    return item.get("SeriesId") if item.get("Type") == "Episode" else item.get("Id")


def track_prefs(item):
    """mpv's track options for an item: the languages from the settings, or what was chosen for its show.
    Audio "auto" follows Matinee's language, "original" takes the track the file marks as its default.
    Subtitles "off" never come by themselves, not even those chosen for the item last time: turned on in the player,
    they are there for that playback only. "auto" only when the audio is in another language, "always" also in the
    same one; with those two forced ones (the lines in another language, a sign, a letter) come in any case."""
    audio, subs, sublang = preferences.setting("audio"), preferences.setting("subtitles"), preferences.setting("sublang")
    alang = "" if audio == "original" else TRACK_LANGS[core.LANG if audio == "auto" else audio][0]
    slang = TRACK_LANGS[core.LANG if sublang == "auto" else sublang][1]
    choice = load_tracks().get(tracks_key(item), {}) if tracks_key(item) else {}
    sub = choice.get("sub")
    if choice.get("audio"):
        alang = ",".join(lang_codes(choice["audio"]) + ([alang] if alang else []))
    if sub and sub != "no":
        slang = ",".join(lang_codes(sub)) + "," + slang
    off = sub == "no" or subs == "off"
    return {"alang": alang, "slang": slang, "sid": "no" if off else "auto",
            # subtitles someone chose are shown even in the language of the audio
            "matching": "yes" if (sub and sub != "no") or subs == "always" else "forced", "sub": sub,
            "subs_off": subs == "off"}


def preferred(streams, langs):
    """The first stream in one of the preferred languages, in their order (none for no languages)."""
    for lang in filter(None, langs.split(",")):
        for s in streams:
            if (s.get("Language") or "").lower() == lang:
                return s
    return None


def sub_allowed(lang, forced, audio, matching):
    """mpv's rule (subs-with-matching-audio) for the subtitles Matinee picks itself: in the language of the audio only
    when asked for ("yes"), otherwise only forced ones."""
    if matching == "yes" or forced or not lang or not audio:
        return True
    return lang.lower() not in lang_codes(audio)


def preferred_sub(streams, langs, audio, matching):
    """The subtitles to pick: the preferred languages in their order, the full ones before the forced ones (for audio
    in another language), but in the language of the audio only forced ones unless asked for."""
    allowed = [s for s in streams if sub_allowed(s.get("Language"), s.get("IsForced"), audio, matching)]
    return preferred(sorted(allowed, key=lambda s: bool(s.get("IsForced"))), langs)


# Jellyfin keeps no position in the first 5 % of a film (its MinResumePct): stopped there after the first minute, it
# is kept here instead (and synced), so that it goes on there next time; from then on it is Jellyfin's again
EARLY_SHARE = float(os.environ.get("KINO_EARLY_SHARE", "0.05"))
EARLY_FROM = float(os.environ.get("KINO_EARLY_FROM", "60"))  # seconds: less is only a look into it
EARLY_KEEP = 200


early_read = [None, {}]  # (the file's mtime and size, what it said): every Jellyfin answer looks, few change it


def early_positions():
    """{item id: second} of the films and episodes stopped too early for Jellyfin to keep where."""
    try:
        st = EARLY_FILE.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if early_read[0] != stamp:
        early_read[1] = {e["id"]: e["pos"] for e in read_json(EARLY_FILE).get("early", [])
                         if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("pos"), (int, float))}
        early_read[0] = stamp
    return dict(early_read[1])


def remember_early(item, pos):
    """Where a film or episode was stopped, kept if Jellyfin does not keep it; forgotten once it does (or at the end)."""
    item_id, runtime = item.get("Id"), (item.get("RunTimeTicks") or 0) / TICKS
    if not item_id or pos is None:
        return
    keep = EARLY_FROM <= pos < runtime * EARLY_SHARE
    try:  # the disk full: not kept, but the end of the playback goes on (it is called on the way out)
        with changing(EARLY_FILE) as data:
            kept = [e for e in data.get("early", []) if isinstance(e, dict)]
            rest = [e for e in kept if e.get("id") != item_id]
            if keep:
                data["early"] = [{"id": item_id, "pos": round(pos, 1), "at": int(time.time())}, *rest][:EARLY_KEEP]
            elif len(rest) != len(kept):
                data["early"] = rest
    except OSError as e:
        print(f"Where it stopped not kept: {e}", file=sys.stderr)


def forget_early(item_id):
    with changing(EARLY_FILE) as data:
        if any(isinstance(e, dict) and e.get("id") == item_id for e in data.get("early", [])):
            data["early"] = [e for e in data["early"] if not isinstance(e, dict) or e.get("id") != item_id]


def forget_early_of(item_id):
    """A title marked watched or not: no position kept here for it any more, nor for any episode of it (a show, a
    season), or Continue Watching would still hold it."""
    early = early_positions()
    if not early:
        return
    gone = {item_id} & early.keys()
    if item_id not in early:
        try:
            children = jf(f"/Items?userId={state['user_id']}&ParentId={item_id}&Recursive=true&IncludeItemTypes=Episode"
                          f"&Fields=ParentId&Limit=5000") or {}
        except Exception:
            children = {}
        gone |= {x.get("Id") for x in children.get("Items") or []} & early.keys()
    for i in gone:
        forget_early(i)


def resume_at(item):
    """The second to go on from: Jellyfin's, else one kept here (not for a film watched to the end meanwhile)."""
    ud = item.get("UserData") or {}
    if ud.get("PlaybackPositionTicks"):
        return ud["PlaybackPositionTicks"] / TICKS
    return 0 if ud.get("Played") else early_positions().get(item.get("Id"), 0)


def with_early(body):
    """Jellyfin's answer to the page with the positions kept here, as if Jellyfin had them: the film's page offers to
    go on from there."""
    early = early_positions()
    if not early or not any(i.encode() in body for i in early):
        return body
    try:
        data = json.loads(body)
    except ValueError:
        return body

    def walk(x):
        if isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            ud = x.get("UserData")
            if x.get("Id") in early and isinstance(ud, dict) and not ud.get("PlaybackPositionTicks") and not ud.get("Played"):
                ud["PlaybackPositionTicks"] = int(early[x["Id"]] * TICKS)
                if x.get("RunTimeTicks"):
                    ud["PlayedPercentage"] = 100 * ud["PlaybackPositionTicks"] / x["RunTimeTicks"]
            for v in x.values():
                if isinstance(v, (list, dict)):
                    walk(v)
    walk(data)
    return json.dumps(data).encode()


RESUME_PATH = re.compile(r"/(UserItems|Users/[\w-]+/Items)/Resume")


def resume_with_early(path, body):
    """Jellyfin's Continue Watching with the films and episodes kept here among them: stopped in their first 5 %,
    Jellyfin has none of them begun, though they are (each where it was last played, the newest first)."""
    u = urllib.parse.urlsplit(path)
    early = early_positions()
    if not early or not RESUME_PATH.fullmatch(u.path):
        return body
    try:
        data = json.loads(body)
        items = data["Items"]
        q = dict(urllib.parse.parse_qsl(u.query))
        limit = int(q.get("Limit") or 0) or None
        missing = [i for i in early if not any(x.get("Id") == i for x in items)]
        if not missing:
            return body
        params = {"userId": q.get("userId") or state["user_id"], "Ids": ",".join(missing),
                  **({"Fields": q["Fields"]} if q.get("Fields") else {}),
                  **({"MediaTypes": q["MediaTypes"]} if q.get("MediaTypes") else {})}
        found = (jf(f"/Items?{urllib.parse.urlencode(params)}") or {}).get("Items") or []
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log_failure("/UserItems/Resume", e)
        return body
    begun = [x for x in found if not (x.get("UserData") or {}).get("Played")
             and not (x.get("UserData") or {}).get("PlaybackPositionTicks")]
    if not begun:
        return body
    data["Items"] = sorted(items + begun, key=lambda x: (x.get("UserData") or {}).get("LastPlayedDate") or "",
                           reverse=True)[:limit]
    data["TotalRecordCount"] = (data.get("TotalRecordCount") or len(items)) + len(begun)
    return json.dumps(data).encode()


def prepare(item_id, resume, at=None, aside=False):
    """Everything mpv needs to play an item; at: from that second (handed over from another Matinee), else where it was
    left (resume_at). aside: as the programme of one of one's own channels (tv.own_on), which leaves the series
    where it is: no next episode, no intro to skip, and not back in "Up Next"."""
    item = jf(f"/Items/{item_id}?userId={state['user_id']}")
    if not age_limit.allowed(item):
        raise PermissionError("age_limit")  # above the age limit (reached through Up Next, a channel, another Matinee)
    source = item["MediaSources"][0]
    start = int(at * TICKS) if at is not None else int(resume_at(item) * TICKS) if resume else 0
    if not aside and item.get("SeriesId") and item["SeriesId"] in library.load_hidden()["nextup"]:
        library.hide("nextup", item["SeriesId"], on=False)  # watched again: back in "Up Next"
    key = urllib.parse.quote(state["token"])
    title = item.get("Name", "")
    if item.get("Type") == "Episode":
        title = f"{item.get('SeriesName', '')} – S{item.get('ParentIndexNumber', 0):02}E{item.get('IndexNumber', 0):02} – {title}"
    streams = source.get("MediaStreams", [])

    def sub_url(s):
        ext = TEXT_SUBS.get((s.get("Codec") or "").lower(), "srt")
        return f"{core.SERVER}/Videos/{item_id}/{source['Id']}/Subtitles/{s['Index']}/Stream.{ext}?ApiKey={key}"

    external = [s for s in streams if s.get("Type") == "Subtitle" and s.get("IsExternal")]
    subs = [sub_url(s) for s in external]
    # mpv does not know the language of subtitle files, Jellyfin does
    sub_streams = {sub_url(s): {"lang": s.get("Language"), "index": s["Index"], "forced": bool(s.get("IsForced"))}
                   for s in external}
    prefs = track_prefs(item)
    url = f"{core.SERVER}/Videos/{item_id}/stream?static=true&MediaSourceId={source['Id']}&ApiKey={key}"
    late_sub = None
    session_id = uuid.uuid4().hex
    method, note = "DirectPlay", None
    try:
        nxt = None if aside else video_player.next_episode(item)
    except Exception as e:
        print(f"Next episode not found: {e}", file=sys.stderr)
        nxt = None

    video = next((s for s in streams if s.get("Type") == "Video"), {})
    unplayable = cannot_decode(video)
    # measuring the line takes up to 2.5 seconds: not for a file that is converted anyway (to far less than it)
    measured = (measure_bandwidth(url, source.get("Size") or 0, enough=source["Bitrate"] * 2)
                if QUALITY == "auto" and source.get("Bitrate") and not unplayable else None)
    bitrate = pick_bitrate(measured, source.get("Bitrate"))
    if unplayable:  # converted to 1080p, no more than the line or the quality setting allow
        bitrate = min(bitrate or CONVERT_BITRATE, CONVERT_BITRATE)
    if bitrate:
        # Too slow for the file: Jellyfin transcodes to H.264/AAC stereo as HLS. That stream has a single
        # audio track, so the preferred language is chosen here; subtitles come as text or burned in.
        audios = [s for s in streams if s.get("Type") == "Audio"]
        audio = preferred(audios, prefs["alang"])
        heard = (audio or next((s for s in audios if s.get("IsDefault")), audios[0] if audios else {})).get("Language")
        sub = None if prefs["sid"] == "no" else preferred_sub([s for s in streams if s.get("Type") == "Subtitle"],
                                                               prefs["slang"], heard, prefs["matching"])
        url = (f"{core.SERVER}/Videos/{item_id}/master.m3u8?MediaSourceId={source['Id']}&PlaySessionId={session_id}"
               f"&DeviceId={state['device_id']}&ApiKey={key}&VideoCodec=h264&AudioCodec=aac&TranscodingMaxAudioChannels=2"
               f"&VideoBitrate={max(bitrate - 128_000, 250_000)}&AudioBitrate=128000&SegmentContainer=ts&BreakOnNonKeyFrames=true")
        if unplayable:
            url += "&MaxWidth=1920&MaxHeight=1080"
        if audio:
            url += f"&AudioStreamIndex={audio['Index']}"
        if sub and (sub.get("Codec") or "").lower() in TEXT_SUBS and sub.get("IsExternal"):
            subs = [sub_url(sub)]
        elif sub and (sub.get("Codec") or "").lower() in TEXT_SUBS:
            # one inside the file: Jellyfin reads the whole file for it first (a minute for a film), and mpv would
            # wait for it with a black screen. The film starts at once, the subtitles follow (Player.add_late_sub).
            subs = []
            late_sub = {"url": sub_url(sub), "lang": sub.get("Language"), "index": sub["Index"],
                        "forced": bool(sub.get("IsForced")), "ext": TEXT_SUBS[sub["Codec"].lower()]}
        elif sub:  # picture subtitles (Blu-ray, DVD) cannot be sent separately
            subs = []
            url += f"&SubtitleStreamIndex={sub['Index']}&SubtitleMethod=Encode"
        method = "Transcode"
        if unplayable:
            note = t("converted", what=unplayable)
            print(f"{unplayable} is not decoded here: Jellyfin converts it ({bitrate / 1e6:.1f} Mbit/s)", file=sys.stderr)
        else:
            note = t("reduced", rate=f"{bitrate / 1e6:.1f}")
            print(f"Connection {measured / 1e6 if measured else 0:.1f} Mbit/s, file {source['Bitrate'] / 1e6:.1f}: "
                  f"transcoding to {bitrate / 1e6:.1f} Mbit/s", file=sys.stderr)
    return {
        "item": item,
        "next": nxt,  # None for movies and the last episode
        "segments": [] if aside else video_player.segments(item_id),
        "url": url,
        "title": title,
        "subs": subs,
        "sub_streams": sub_streams,
        "late_sub": late_sub,
        "fps": next((s.get("RealFrameRate") or s.get("AverageFrameRate") for s in streams if s.get("Type") == "Video"), None),
        "prefs": prefs,
        "start": start,
        "note": note,
        "session": {
            "ItemId": item_id,
            "MediaSourceId": source["Id"],
            "PlaySessionId": session_id,
            "PlayMethod": method,
            "CanSeek": True,
            "PositionTicks": start,
        },
    }


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import preferences, video_player, library  # noqa: E402,F401
