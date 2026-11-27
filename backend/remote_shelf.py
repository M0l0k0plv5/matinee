"""What Matinee Remote shows of the parts a phone is not signed in to: music, audiobooks and podcasts.

The phone only ever talks to Jellyfin. So Matinee, which is signed in to the other servers, puts a short list of each
part with the Jellyfin account (a display preference of its own, as the sync's): what was heard last and what is new,
with small pictures in it, since the phone cannot ask Navidrome or Audiobookshelf for a cover. No address, token or
password of the other servers goes there. A tile tapped comes back as "play this" with its kind and id
(REMOTE_PLAY_HEADER in live_events.py), and Matinee gets the rest from its own servers."""
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time

from .core import state
from .jellyfin_api import jf, log_net, ready
from .preferences import setting
from . import music_player, services

SHELF_PREFS = "kino-remote"   # the display preference's id and client, and the key of the list in it
SHELF_EVERY = float(os.environ.get("KINO_REMOTE_SHELF_EVERY", 300))
SHELF_AFTER = float(os.environ.get("KINO_REMOTE_SHELF_AFTER", 20))  # after the start: the sign-ins first
SHELF_ITEMS = 8               # tiles of a row
THUMB = 96                    # pixels: a tile on a phone, small enough for some dozens of them in one answer
THUMB_MAX = 9000              # bytes: a picture a server did not make small is left out (or made small by ffmpeg)
ID = re.compile(r"[\w-]{1,100}")

thumbs = {}  # (part, id) → a picture as a data: address, or None; kept while Matinee runs
thumbs_lock = threading.Lock()
kick = threading.Event()  # something was played: the lists anew soon


def smaller(data):
    """A picture made small by ffmpeg (a podcast's own is some hundred KB), or None without ffmpeg."""
    if not shutil.which("ffmpeg"):
        return None
    try:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vf", f"scale={THUMB * 2}:-2", "-frames:v", "1",
                            "-q:v", "6", "-f", "mjpeg", "pipe:1"], input=data, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and 0 < len(r.stdout) <= THUMB_MAX else None


def thumb(part, item_id, fetch):
    """A tile's picture as a data: address, asked for once (fetch: → (content type, bytes)); None if there is none.
    One that could not be had (the server away a moment) is asked for again next time, not kept as none."""
    key = (part, item_id)
    with thumbs_lock:
        if key in thumbs:
            return thumbs[key]
    found = None
    try:
        ctype, data = fetch()
        ctype = str(ctype or "").split(";")[0].strip().lower()
        if ctype.startswith("image/") and data:
            if len(data) > THUMB_MAX:
                ctype, data = "image/jpeg", smaller(data)
            if data:
                found = f"data:{ctype};base64,{base64.b64encode(data).decode()}"
    except (OSError, ValueError, LookupError, services.NavidromeError, services.AbsError, services.PfError):
        return None
    with thumbs_lock:
        if len(thumbs) > 2000:
            thumbs.clear()
        thumbs[key] = found
    return found



def music_shelves():
    if not (services.navidrome and (services.nd_creds.get("token") or services.nd_load_creds())):
        return []

    def cover(art):
        def fetch():
            status, ctype, body = services.nd_request("getCoverArt", {"id": art, "size": THUMB * 2})
            # no picture of its own: Navidrome's placeholder, a small WebP (see nd_proxy)
            return (None, None) if status != 200 or (ctype == "image/webp" and len(body) < 2048) else (ctype, body)
        return thumb("nd", art, fetch) if art else None

    shelves = []
    for name, kind in (("musicRecent", "recent"), ("musicNew", "newest")):
        albums = services.nd("getAlbumList2", type=kind, size=SHELF_ITEMS)["albumList2"].get("album") or []
        items = [{"kind": "album", "id": str(a["id"]), "title": str(a.get("name") or ""), "sub": str(a.get("artist") or ""),
                  "pic": cover(a.get("coverArt"))} for a in albums if ID.fullmatch(str(a.get("id", "")))]
        shelves.append({"id": name, "items": items})
    return shelves


def book_shelves():
    if not services.audiobookshelf:
        return []

    def book(li):
        meta = (li.get("media") or {}).get("metadata") or {}
        item_id = str(li.get("id", ""))

        def fetch():
            status, ctype, body = services.abs_request(f"/api/items/{item_id}/cover?width={THUMB * 2}&format=jpeg")
            return (ctype, body) if status == 200 else (None, None)
        return {"kind": "book", "id": item_id, "title": str(meta.get("title") or ""),
                "sub": str(meta.get("authorName") or ""), "pic": thumb("abs", item_id, fetch)}

    begun = services.absj("/api/me/items-in-progress").get("libraryItems") or []
    newest = []
    for lib in services.absj("/api/libraries").get("libraries") or []:
        if lib.get("mediaType", "book") != "book" or not ID.fullmatch(str(lib.get("id", ""))):
            continue
        r = services.absj(f"/api/libraries/{lib['id']}/items?limit={SHELF_ITEMS}&sort=addedAt&desc=1&minified=1")
        newest += r.get("results") or []
    newest.sort(key=lambda x: x.get("addedAt") or 0, reverse=True)
    from . import library  # loads after this one's import of services (see core's end)
    hidden = set(library.load_hidden()["books"])  # taken off Continue Listening here, as on Matinee's own start

    def playable(li):  # a book, with its files there (Audiobookshelf also lists its podcasts and what it lost)
        return (ID.fullmatch(str(li.get("id", ""))) and li.get("mediaType", "book") == "book"
                and not li.get("isMissing") and not li.get("isInvalid"))
    ok = lambda xs: [book(x) for x in xs if playable(x)][:SHELF_ITEMS]
    return [{"id": "booksBegun", "items": ok([x for x in begun if x.get("id") not in hidden])}, {"id": "booksNew", "items": ok(newest)}]


def podcast_shelves():
    if not services.podfetch:
        return []
    recent = services.pf_recent()

    def episode(e):
        pid = e["podcast"]
        return {"kind": "episode", "id": e["id"], "title": e["title"], "sub": e.get("show") or "",
                "pic": thumb("pf", pid, lambda: services.pf_cover(pid)) if pid else None,
                **({"pos": round(e["pos"]), "duration": round(e["duration"])} if e["pos"] and e["duration"] else {})}
    ok = lambda xs: [episode(e) for e in xs if services.PF_ID.fullmatch(str(e["id"]))][:SHELF_ITEMS]
    return [{"id": "podcastsBegun", "items": ok(recent["begun"])},
            {"id": "podcastsNew", "items": ok([e for e in recent["latest"] if not e["finished"]])}]


def shelves():
    """The rows, part by part; a part that does not answer has none this time."""
    found = []
    for part in (music_shelves, book_shelves, podcast_shelves):
        try:
            found += [s for s in part() if s["items"]]
        except (OSError, ValueError, KeyError, TypeError, LookupError, services.NavidromeError, services.AbsError,
                services.PfError) as e:
            log_net(f"Matinee Remote's list, {part.__name__}: {e}", e)
    return found


sent = None  # the digest of the rows put there last


def publish():
    """The rows put with the Jellyfin account, when they are not the ones there already."""
    global sent
    rows = shelves()
    digest = hashlib.sha1(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    if digest == sent:
        return False
    path = f"/DisplayPreferences/{SHELF_PREFS}?userId={state['user_id']}&client={SHELF_PREFS}"
    prefs = jf(path) or {}
    prefs = {"Id": SHELF_PREFS, "Client": SHELF_PREFS, "SortBy": "SortName", "SortOrder": "Ascending",
             "RememberIndexing": False, "RememberSorting": False, "ShowBackdrop": True, "ShowSidebar": False,
             "ScrollDirection": "Horizontal", "PrimaryImageHeight": 250, "PrimaryImageWidth": 250, **prefs}
    prefs["CustomPrefs"] = {**(prefs.get("CustomPrefs") or {}),
                            SHELF_PREFS: json.dumps({"v": 1, "at": int(time.time()), "shelves": rows}, separators=(",", ":"))}
    jf(path, "POST", prefs)
    sent = digest
    return True


def withdraw():
    """The rows taken off the Jellyfin account again ("Same on Every Device" turned off): True when none are there."""
    path = f"/DisplayPreferences/{SHELF_PREFS}?userId={state['user_id']}&client={SHELF_PREFS}"
    prefs = jf(path) or {}
    custom = prefs.get("CustomPrefs") or {}
    if SHELF_PREFS in custom:
        prefs["CustomPrefs"] = {k: v for k, v in custom.items() if k != SHELF_PREFS}
        jf(path, "POST", prefs)
    return True


def loop():
    """Now and then, and soon after something was played from the phone. Not with "Same on Every Device" off: then
    nothing of this Matinee's is kept with the account, and what it put there before is taken off it (once)."""
    global sent
    time.sleep(SHELF_AFTER)
    withdrawn = False
    while True:
        ready.wait()
        if setting("sync") == "on" and state.get("user_id"):
            withdrawn = False
            try:
                publish()
            except Exception as e:  # the server away: the next time
                log_net(f"Matinee Remote's list not put with Jellyfin: {e}", e)
        else:
            sent = None  # switched on again, or signed in as someone else: put there anew
            if not withdrawn and state.get("user_id"):
                try:
                    withdrawn = withdraw()
                except Exception as e:  # the server away: the next time
                    log_net(f"Matinee Remote's list not taken off Jellyfin: {e}", e)
        if kick.wait(SHELF_EVERY):
            time.sleep(min(30, SHELF_EVERY))  # what it started has begun by then
            kick.clear()


def play_plan(what):
    """What Matinee Remote asks to play of these rows, as what starts it, or None: an album of Navidrome, a book of
    Audiobookshelf, an episode of Podfetch, each by its id alone; a book or an episode from where it was left, or
    from "pos" (0: from the start)."""
    kind, item_id, pos = what.get("kind"), str(what.get("id", "")), what.get("pos")
    if kind not in ("album", "book", "episode") or not ID.fullmatch(item_id):
        return None
    at = max(0.0, float(pos)) if isinstance(pos, (int, float)) and not isinstance(pos, bool) and math.isfinite(pos) else None

    def play():
        if kind == "album":
            songs = services.nd("getAlbum", id=item_id)["album"].get("song") or []
            songs = [{k: s.get(k) for k in ("id", "title", "artist", "album", "albumId", "artistId", "coverArt", "duration", "starred")}
                     for s in songs if re.fullmatch(r"[\w-]+", str(s.get("id", "")))]
            if not songs:
                raise LookupError("an album without songs")
            music_player.music.play(songs, 0)
        elif kind == "book":
            music_player.music.play([services.abs_book(item_id, at)])
        else:
            music_player.music.play([services.pf_play(item_id, at)])
        kick.set()
    return play
