""""More Like This", the episode badges, checking the library, rows taken off by hand, subtitles from the internet, the
search log."""
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import core
from .core import (changing, EARLY_FILE, file_lock, HIDDEN_FILE, read_json, RETRY_MAX, state, STRINGS, write_private)
from .jellyfin_api import jf, log_failure, ready
from .playback import forget_early, TEXT_SUBS
from .preferences import setting
from .jf_reports import parse_jf_date


# Jellyfin takes one to three seconds to find similar titles whenever it has not done so recently. Matinee keeps
# which titles are similar to which in a file and works through the whole library in the background, one title
# at a time, so a detail page has its row at once. Only the ids are kept: the UI loads the titles themselves
# fresh (fast), so watched marks are always current.
SIMILAR_FILE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "kino" / "similar.json"
SIMILAR_LIMIT = 16
SIMILAR_MAX_AGE = 7 * 24 * 3600  # older lists are looked up again in the background, so new titles join them
SIMILAR_PAUSE = float(os.environ.get("KINO_SIMILAR_PAUSE", "0.5"))  # between two titles, to spare the server
SIMILAR_TRIES = 3  # a title Jellyfin keeps failing for: given up after this many passes (and as many in a row: the server)
similar = {}  # item id → {"ids": [...], "at": time}
similar_fails = {}  # item id → how many passes its lookup failed in
similar_lock = threading.Lock()
similar_wanted = queue.Queue()  # ids to look up before the rest; None: the library has changed


def load_similar():
    try:
        data = json.loads(SIMILAR_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data.get("items", {}) if data.get("user") == state.get("user_id") else {}  # others see other titles


def save_similar():
    with similar_lock:
        text = json.dumps({"user": state.get("user_id"), "items": similar})
    try:  # the disk full: kept in memory only, looked up again after a restart
        write_private(SIMILAR_FILE, text)
    except OSError as e:
        print(f"Similar titles not kept: {e}", file=sys.stderr)


def fetch_similar(item_id):
    res = jf(f"/Items/{item_id}/Similar?userId={state['user_id']}&Limit={SIMILAR_LIMIT}"
             "&EnableImages=false&EnableUserData=false")
    entry = {"ids": [it["Id"] for it in res.get("Items", [])], "at": time.time()}
    with similar_lock:
        similar[item_id] = entry
    return entry


# Episode counts for the badges on show posters and on the season choices, as the Jellyfin Season Badge plugin counts
# them: "have" the episodes with a file, "total" those plus the missing ones that have aired. Missing episodes are
# the placeholders a metadata provider makes (e.g. TheTVDB's Missing Episode Fetcher); without any, every show would
# look complete, so there are no badges then. Specials stay out of a show's count. One request for all episodes,
# kept for ten minutes and dropped when Jellyfin's library changes.
BADGES_MAX_AGE = 600
badges_cache = {}
badges_lock = threading.Lock()


def episode_counts():
    with badges_lock:
        hit = badges_cache.get("counts")
        if hit and time.monotonic() - hit[0] < BADGES_MAX_AGE:
            return hit[1]
        items = jf(f"/Items?userId={state['user_id']}&IncludeItemTypes=Episode&Recursive=true&Fields=PremiereDate"
                   "&EnableImages=false&EnableUserData=false")["Items"]
        now, series, seasons, placeholders = time.time(), {}, {}, False
        for ep in items:
            missing = ep.get("LocationType") == "Virtual"
            placeholders = placeholders or missing
            if missing:
                try:
                    if not ep.get("PremiereDate") or parse_jf_date(ep["PremiereDate"]) > now:
                        continue  # not aired yet, or no date: not missing yet
                except ValueError:
                    continue
            for counts, key in ((seasons, ep.get("SeasonId")), (series, ep.get("SeriesId") if ep.get("ParentIndexNumber") != 0 else None)):
                if key:
                    c = counts.setdefault(key, [0, 0])
                    c[0] += not missing
                    c[1] += 1
        result = {"series": series, "seasons": seasons} if placeholders else {"series": {}, "seasons": {}}
        badges_cache["counts"] = (time.monotonic(), result)
        return result


def similar_ids(item_id):
    """For the UI: known lists at once (an old one is looked up again in the background), others right now."""
    with similar_lock:
        entry = similar.get(item_id)
    if entry is None:
        entry = fetch_similar(item_id)
        save_similar()
    elif time.time() - entry["at"] > SIMILAR_MAX_AGE:
        similar_wanted.put(item_id)
    return entry["ids"]


def similar_loop():
    """Look up every movie and show without a list (or with an old one), then wait for the library to change."""
    while True:
        ready.wait()
        try:
            items = jf(f"/Items?userId={state['user_id']}&Recursive=true&IncludeItemTypes=Movie,Series"
                       "&EnableImages=false&EnableUserData=false")["Items"]
        except Exception as e:
            print(f"Could not list the library for similar titles: {e}", file=sys.stderr)
            time.sleep(RETRY_MAX)
            continue
        present = {it["Id"] for it in items}
        now = time.time()
        with similar_lock:
            for gone in [i for i in similar if i not in present]:  # removed from the library
                del similar[gone]
            todo = [i for i in present if now - similar.get(i, {}).get("at", 0) > SIMILAR_MAX_AGE]
        relist, failed_in_a_row = False, 0
        for n, item_id in enumerate(todo, 1):
            try:
                while True:  # titles the UI asked for go first
                    wanted = similar_wanted.get_nowait()
                    if wanted is None:
                        relist = True
                    else:
                        fetch_similar(wanted)
            except queue.Empty:
                pass
            except Exception as e:
                print(f"Could not look up similar titles: {e}", file=sys.stderr)
            try:
                fetch_similar(item_id)
                failed_in_a_row = 0
                similar_fails.pop(item_id, None)
            except Exception as e:
                print(f"Could not look up similar titles: {e}", file=sys.stderr)
                failed_in_a_row += 1
                # Jellyfin's answer for this one title (an error of its own, one that cannot be read): the others go
                # on, and after a few tries it counts as having none until its list is old. Anything else (the server
                # away, the sign-in gone), or several titles in a row: later, with the library listed again
                if isinstance(e, ValueError) or isinstance(e, urllib.error.HTTPError) and e.code != 401:
                    if failed_in_a_row < SIMILAR_TRIES:
                        similar_fails[item_id] = similar_fails.get(item_id, 0) + 1
                        if similar_fails[item_id] >= SIMILAR_TRIES:
                            similar_fails.pop(item_id)
                            with similar_lock:
                                similar[item_id] = {"ids": [], "at": time.time()}
                        time.sleep(SIMILAR_PAUSE)
                        continue
                time.sleep(RETRY_MAX)
                relist = True
                break
            if n % 20 == 0:
                save_similar()
            time.sleep(SIMILAR_PAUSE)
        if todo:
            save_similar()
            print(f"Similar titles known for {len(present)} movies and shows", file=sys.stderr)
        while not relist:
            try:
                wanted = similar_wanted.get(timeout=6 * 3600)
            except queue.Empty:
                break  # look for old lists now and then
            if wanted is None:
                break
            try:
                fetch_similar(wanted)
                save_similar()
            except Exception as e:
                print(f"Could not look up similar titles: {e}", file=sys.stderr)


# Films Jellyfin took for another one (its metadata providers' first hit, often TheTVDB's without a year) or whose
# picture is a still from the film instead of a poster: found by comparing what the file's name says (a
# "[tmdbid-…]" or "[imdbid-…]" tag, the year) with what Jellyfin made of it, and fixed only when asked, film by film
# or all at once, through Jellyfin's own Identify (RemoteSearch/Apply) and its remote images. Changing metadata
# needs an administrator's account.
LIBCHECK_TAG = re.compile(r"[\[{(](tmdbid|imdbid)[-=]((?:tt)?\d+)[\]})]", re.I)
LIBCHECK_YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
# where a release's name stops being the title: its resolution, language, sound, codec or source
LIBCHECK_RELEASE = re.compile(r"[ ._-](?:2160p|1080p|720p|576p|480p|German|DL|AC3D?|DTS|x26[45]|h\.?26[45]|BluRay|BDRip"
                              r"|WEB(?:-?DL|Rip)?|HDTV|Remux|UHD)(?=[ ._-]|$)", re.I)
LIBCHECK_WIDE = 0.9  # a poster is 2:3 (0.67); wider than this is a still from the film
libcheck = {"state": "idle", "items": [], "error": None}  # idle, scanning, looking (for proposals), done, error
libcheck_lock = threading.Lock()


def file_hints(path):
    """What a film's file name (and its folder's) says of it: {"tmdb", "imdb", "title", "year"}, each may be None."""
    p = Path(path or "")
    tags = {k.lower(): v for k, v in LIBCHECK_TAG.findall(f"{p.parent.name} {p.name}")}
    stem = LIBCHECK_TAG.sub("", p.stem).strip()
    release = LIBCHECK_RELEASE.search(stem)
    head = stem[:release.start()] if release else stem
    years = [m for m in LIBCHECK_YEAR.finditer(head) if m.start() > 0]  # one at the very start is the title (1917)
    year = years[-1] if years else None
    title = re.sub(r"[._]+", " ", head[:year.start()] if year else head).strip(" -([") or None
    return {"tmdb": tags.get("tmdbid"), "imdb": tags.get("imdbid"), "title": title, "year": int(year.group(1)) if year else None}


def movie_problem(it, hints):
    """What is wrong with a film: "wrong" (its file names another one), "unknown" (not identified), "year" (the
    file's year is another), "poster" (no poster, or a still in its place), or None."""
    ids = it.get("ProviderIds") or {}
    if hints["tmdb"] or hints["imdb"]:
        if (hints["tmdb"] != ids.get("Tmdb")) if hints["tmdb"] else (hints["imdb"] != ids.get("Imdb")):
            return "wrong"
    elif not ids.get("Tmdb") and not ids.get("Imdb"):
        return "unknown"
    elif (hints["year"] and it.get("ProductionYear") and abs(hints["year"] - it["ProductionYear"]) > 1
          and str(hints["year"]) not in it.get("Name", "")):  # 2049 in "Blade Runner 2049" is no year
        return "year"
    if not (it.get("ImageTags") or {}).get("Primary") or (it.get("PrimaryImageAspectRatio") or 0) > LIBCHECK_WIDE:
        return "poster"
    return None


def libcheck_proposal(entry):
    """What would fix it: {"name", "year", "image", "result"} (Jellyfin's search result, for Apply), for a poster
    {"image", "provider"}; None when nothing fitting is found."""
    iid, hints = entry["id"], entry["hints"]
    if entry["kind"] == "poster":
        images = (jf(f"/Items/{iid}/RemoteImages?type=Primary&limit=20") or {}).get("Images") or []
        img = next((i for i in images if (i.get("Height") or 0) > (i.get("Width") or 0)), None)  # upright: a poster
        return img and {"image": img["Url"], "provider": img.get("ProviderName"), "name": entry["name"], "year": entry["year"]}
    if hints["tmdb"] or hints["imdb"]:
        key, value = ("Tmdb", hints["tmdb"]) if hints["tmdb"] else ("Imdb", hints["imdb"])
        found = jf("/Items/RemoteSearch/Movie", "POST", {"SearchInfo": {"ProviderIds": {key: value}}, "ItemId": iid}) or []
        # and of the file's year, if it says one: a tag can be wrong too (one of TheTVDB's films without a year)
        found = [r for r in found if (r.get("ProviderIds") or {}).get(key) == value
                 and (not hints["year"] or abs((r.get("ProductionYear") or 0) - hints["year"]) <= 1)]
    elif hints["title"]:
        found = jf("/Items/RemoteSearch/Movie", "POST", {"SearchInfo": {"Name": hints["title"], "Year": hints["year"]}, "ItemId": iid}) or []
        # TheMovieDb's, with the year (TheTVDB's hits for films come without one: how they got mixed up)
        found = [r for r in found if r.get("SearchProviderName") == "TheMovieDb"
                 and (not hints["year"] or abs((r.get("ProductionYear") or 0) - hints["year"]) <= 1)]
    else:
        return None
    r = found[0] if found else None
    if not r or entry["tmdb"] and (r.get("ProviderIds") or {}).get("Tmdb") == entry["tmdb"]:
        return None  # nothing found, or the film it already is
    return {"name": r.get("Name"), "year": r.get("ProductionYear"), "image": r.get("ImageUrl"), "result": r}


def libcheck_scan():
    def update(**kw):
        with libcheck_lock:
            libcheck.update(**kw)
    try:
        if not ((jf("/Users/Me") or {}).get("Policy") or {}).get("IsAdministrator"):
            return update(state="error", error="admin_required", items=[])
        items = jf(f"/Items?userId={state['user_id']}&Recursive=true&IncludeItemTypes=Movie&EnableUserData=false"
                   "&Fields=ProviderIds,Path,ProductionYear,PrimaryImageAspectRatio&EnableImageTypes=Primary")["Items"]
        found = []
        for it in items:
            hints = file_hints(it.get("Path"))
            kind = movie_problem(it, hints)
            if kind:
                ids = it.get("ProviderIds") or {}
                found.append({"id": it["Id"], "name": it.get("Name", ""), "year": it.get("ProductionYear"), "kind": kind,
                              "file": Path(it.get("Path") or "").name, "hints": hints, "tmdb": ids.get("Tmdb"),
                              "tvdb": "Tvdb" in ids, "state": "looking", "proposal": None})
        found.sort(key=lambda e: e["name"].lower())
        update(state="looking", items=found, error=None)

        def propose(e):
            try:
                return libcheck_proposal(e)
            except Exception as err:
                print(f"No proposal for {e['name']}: {err}", file=sys.stderr)
                return None
        with ThreadPoolExecutor(4) as pool:  # Jellyfin asks TheMovieDb for each: not all at once
            for e, prop in zip(found, pool.map(propose, found)):
                with libcheck_lock:
                    e.update(proposal=prop, state="ready" if prop else "none")
        update(state="done")
    except Exception as e:
        print(f"Checking the library failed: {e}", file=sys.stderr)
        update(state="error", error="unreachable")


def libcheck_fix(ids):
    """Fix the films asked for, one after the other (Jellyfin fetches each one's metadata and pictures anew)."""
    with libcheck_lock:
        todo = [e for e in libcheck["items"] if e["id"] in ids and e["state"] in ("ready", "failed")]
        for e in todo:
            e["state"] = "waiting"
    for e in todo:
        with libcheck_lock:
            e["state"] = "fixing"
        try:
            p = e["proposal"]
            if e["kind"] == "poster":
                q = urllib.parse.urlencode({"type": "Primary", "imageUrl": p["image"], "providerName": p.get("provider") or ""})
                jf(f"/Items/{e['id']}/RemoteImages/Download?{q}", "POST", raw=True)
            else:
                jf(f"/Items/RemoteSearch/Apply/{e['id']}?replaceAllImages=true", "POST", p["result"], raw=True)
            result = "fixed"
        except Exception as err:
            print(f"Could not fix {e['name']}: {err}", file=sys.stderr)
            result = "failed"
        with libcheck_lock:
            e["state"] = result
    if todo:
        live_events.broadcast("changed")  # new titles and pictures on the pages


def libcheck_view():
    with libcheck_lock:
        return json.loads(json.dumps(libcheck))


# Taken off a row in Matinee (the menu key: "Remove"): what Jellyfin and Navidrome cannot forget themselves.
# A show in "Up Next" or a book comes back once it is played again; albums stay hidden until
# "Show hidden again" in the settings. Navidrome itself still has them in its history.
HIDDEN_KINDS = ("nextup", "albums", "books")


def load_hidden():
    data = read_json(HIDDEN_FILE)
    return {k: [x for x in data.get(k, []) if isinstance(x, str)] for k in HIDDEN_KINDS}


def hide(kind, item_id, on=True):
    with changing(HIDDEN_FILE, load_hidden) as data:
        rest = [x for x in data[kind] if x != item_id]
        data[kind] = ([item_id] if on else []) + rest


# Subtitles looked for on the internet for a movie or an episode (its options: "Find Subtitles"): Jellyfin's own
# search, through a subtitle plugin on the server such as OpenSubtitles; the one chosen Jellyfin downloads and keeps
# with the video, so it is among the subtitles from then on, on every device
SUB_SEARCH_LANGS = {"de": "ger", "en": "eng", "fr": "fre", "es": "spa", "nl": "dut"}  # as Jellyfin names them (ISO 639-2)
SUB_ID = re.compile(r"[^/\\\s]{1,300}")


def subtitle_search(item_id, lang):
    found = jf(f"/Items/{item_id}/RemoteSearch/Subtitles/{SUB_SEARCH_LANGS[lang]}?isPerfectMatch=false") or []
    return [{"id": s["Id"], "name": s.get("Name") or "", "provider": s.get("ProviderName") or "",
             "format": (s.get("Format") or "").lower(), "downloads": s.get("DownloadCount"),
             "match": bool(s.get("IsHashMatch")), "forced": bool(s.get("IsForced")),
             "hearing": bool(s.get("HearingImpaired"))} for s in found if s.get("Id")][:40]


def find_remote_subs(p):
    """The info panel's "Find Subtitles…" (in a thread of its own): those of the subtitle language set first, then
    the other's; Player.show_remote_subs puts them into the panel."""
    lang = setting("sublang") if setting("sublang") in SUB_SEARCH_LANGS else core.LANG
    names = dict(zip(SUB_SEARCH_LANGS, ("Deutsch", "English")))
    try:
        found = [(x, s) for x in [lang, *(x for x in SUB_SEARCH_LANGS if x != lang)]
                 for s in subtitle_search(p["item"]["Id"], x)[:REMOTE_SUBS_SHOWN]]
        p["subs_found"] = {"state": "done", "items": [
            {"id": s["id"], "name": s["name"] or s["provider"],
             "extra": " · ".join(filter(None, [names[x], s["forced"] and STRINGS[core.LANG]["p_forced"],
                                               s["hearing"] and "SDH", s["provider"]]))} for x, s in found]}
    except Exception as e:
        log_failure("subtitles from the internet", e)
        p["subs_found"] = {"state": "failed"}


REMOTE_SUBS_SHOWN = 12  # of each language
REMOTE_SUB_WAIT = float(os.environ.get("KINO_REMOTE_SUB_WAIT", 20))  # for Jellyfin to list what it downloaded


def get_remote_sub(p, sub_id):
    """Subtitles chosen in the info panel (in a thread): downloaded by Jellyfin, which keeps them with the video, then
    waited for among its streams; Player.show_remote_subs adds them to mpv."""
    item_id = p["item"]["Id"]

    def externals():
        source = jf(f"/Items/{item_id}?userId={state['user_id']}")["MediaSources"][0]
        return source["Id"], {(s.get("Path") or s["Index"]): s for s in source.get("MediaStreams", [])
                              if s.get("Type") == "Subtitle" and s.get("IsExternal")}
    try:
        if not SUB_ID.fullmatch(sub_id):
            raise ValueError("invalid subtitles")
        _, before = externals()
        jf(f"/Items/{item_id}/RemoteSearch/Subtitles/{urllib.parse.quote(sub_id, safe='')}", "POST")
        deadline = time.monotonic() + REMOTE_SUB_WAIT
        while True:
            source_id, now = externals()
            new = [s for k, s in now.items() if k not in before]
            if new:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("not listed by Jellyfin")
            time.sleep(1)
        s = new[-1]
        ext = TEXT_SUBS.get((s.get("Codec") or "").lower(), "srt")
        url = (f"{core.SERVER}/Videos/{item_id}/{source_id}/Subtitles/{s['Index']}/Stream.{ext}"
               f"?ApiKey={urllib.parse.quote(state['token'])}")
        p["sub_got"] = {"url": url, "lang": s.get("Language"), "index": s["Index"]}
    except Exception as e:
        log_failure("subtitles downloaded", e)
        p["sub_got"] = False


def unhide_all():
    with file_lock(HIDDEN_FILE):
        HIDDEN_FILE.unlink(missing_ok=True)
    syncing.sync.changed(HIDDEN_FILE)  # shown again on every device


def forget(what, value):
    """Take something off a row: "resume" (Jellyfin's Continue Watching: its position is reset on the server),
    "nextup" (a show), "album" (Navidrome's recently/most played), "book" (Audiobookshelf's begun). What it took is kept for put_back (the last
    one only, until Matinee is restarted)."""
    back = None
    if what == "resume":
        try:  # where it was, for put_back; taken off the row as well when that cannot be asked
            ticks = (jf(f"/Items/{value}?userId={state['user_id']}").get("UserData") or {}).get("PlaybackPositionTicks") or 0
        except Exception:
            ticks = 0
        early = [e for e in read_json(EARLY_FILE).get("early", []) if isinstance(e, dict) and e.get("id") == value]
        jf(f"/UserItems/{value}/UserData?userId={state['user_id']}", "POST", {"PlaybackPositionTicks": 0})
        forget_early(value)
        back = {"ticks": ticks, "early": early}
    elif what == "nextup":
        hide("nextup", value)
    elif what == "album":
        hide("albums", value)
    elif what == "book":  # Audiobookshelf's own app hides it as well; it comes back when it is played again
        hide("books", value)
        try:
            services.absj(f"/api/me/progress/{value}/remove-from-continue-listening")
        except (services.AbsError, LookupError):
            pass
    else:
        raise ValueError(what)
    last_forgotten[0] = (what, value, back)


last_forgotten = [None]  # (what, id, what it took), see put_back


def put_back():
    """Undo the last forget(): the title back on its row as it was, with where it had been stopped. False when
    there is nothing to put back."""
    if not last_forgotten[0]:
        return False
    what, value, back = last_forgotten[0]
    if what == "resume":
        if back["ticks"]:
            jf(f"/UserItems/{value}/UserData?userId={state['user_id']}", "POST", {"PlaybackPositionTicks": back["ticks"]})
        if back["early"]:
            with changing(EARLY_FILE) as data:
                data["early"] = back["early"] + [e for e in data.get("early", []) if not isinstance(e, dict) or e.get("id") != value]
    elif what in ("nextup", "album", "book"):
        hide({"nextup": "nextup", "album": "albums", "book": "books"}[what], value, on=False)
        if what == "book":
            try:
                services.absj(f"/api/me/progress/{value}", "PATCH", {"hideFromContinueListening": False})
            except (services.AbsError, LookupError):
                pass
    last_forgotten[0] = None
    return True


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import live_events, syncing, services  # noqa: E402,F401
