"""Navidrome, Audiobookshelf and Podfetch: their clients, and adding them from the settings."""
import base64
import hashlib
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
from datetime import datetime

from . import core
from .core import conf, CONF_DIR, state, VERSION, write_private
from .jellyfin_api import conf_changing, log_net, Pool, setup_jellyfin
from .library import hide


# "navidrome": {"server":…, "username":…} in config.json. Navidrome speaks the Subsonic API, which signs
# every request with md5(password + salt): set-password --navidrome keeps only that token and its salt,
# in their own file (the backend and set-password would otherwise overwrite each other's state.json).
NAVIDROME_FILE = CONF_DIR / "navidrome.json"
ND = conf.get("navidrome") or {}
ND_SERVER = (ND.get("server") or "").rstrip("/")
navidrome = Pool(ND_SERVER) if ND_SERVER else None
SUBSONIC = "1.16.1"
# what the UI may ask Navidrome for: reading only
ND_METHODS = {"ping", "getAlbumList2", "getAlbum", "getArtists", "getArtist", "getPlaylists", "getPlaylist",
              "search3", "getRandomSongs", "getCoverArt", "getTopSongs", "getLyricsBySongId", "getStarred2",
              "getInternetRadioStations"}
nd_creds = {}


class NavidromeError(Exception):
    """The message is an error code the UI translates: nd_login_required, nd_auth_rejected, nd_unreachable."""



def read_within(resp, seconds):
    """All of an answer, but not for longer than this: urlopen's timeout counts each read, so one that trickles in
    (a large list, some MB) could otherwise take minutes. A TimeoutError (an OSError) when it takes longer."""
    end = time.monotonic() + seconds
    chunks = []
    while chunk := resp.read(65536):
        chunks.append(chunk)
        if time.monotonic() > end:
            raise TimeoutError(f"the answer took longer than {seconds:g} s")
    return b"".join(chunks)

def nd_load_creds():
    """The token set-password --navidrome stored; read again each time it is missing or rejected."""
    try:
        nd_creds.update(json.loads(NAVIDROME_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return nd_creds.get("token") and nd_creds.get("salt")


def nd_query(params, creds=None):
    if not creds and not nd_creds.get("token") and not nd_load_creds():  # nothing asked of Navidrome since the start
        raise NavidromeError("nd_login_required")
    c = creds or nd_creds
    # the sign-in last: no parameter from the UI can stand in for it
    return urllib.parse.urlencode({**params, "u": ND.get("username", ""), "t": c["token"], "s": c["salt"], "v": SUBSONIC,
                                   "c": "Matinee", "f": "json"}, doseq=True)


def nd_request(method, params, creds=None):
    """A Subsonic request: (status, content type, body), signed in."""
    if not navidrome:
        raise NavidromeError("nd_not_configured")
    if not creds and not nd_creds.get("token") and not nd_load_creds():
        raise NavidromeError("nd_login_required")
    try:
        r = navidrome.request(f"/rest/{method}?{nd_query(params, creds)}")
    except OSError as e:
        raise NavidromeError("nd_unreachable") from e
    return r.status, r.headers.get("Content-Type", "application/json"), r.body


def nd(method, creds=None, **params):
    """A Subsonic call: its "subsonic-response". A rejected token is read again from the file once,
    set-password --navidrome may have signed in anew."""
    for attempt in range(2):
        status, _, body = nd_request(method, params, creds)
        try:
            r = json.loads(body)["subsonic-response"]
        except (ValueError, KeyError):
            raise NavidromeError("nd_unreachable") from None
        if r.get("status") == "ok":
            return r
        code = (r.get("error") or {}).get("code")
        if code in (40, 41, 44) and not creds and attempt == 0 and nd_load_creds():
            continue
        raise NavidromeError("nd_auth_rejected" if code in (40, 41, 44) else "nd_unreachable")


def nd_stream_url(song_id):
    """The address mpv plays a song from. It carries the token: mpv only ever gets it over IPC."""
    return f"{ND_SERVER}/rest/stream?{nd_query({'id': song_id})}"


def nd_station(station_id):
    """An internet radio station from Navidrome, as a queue entry; its address comes from Navidrome only."""
    r = nd("getInternetRadioStations").get("internetRadioStations", {})
    st = next((x for x in r.get("internetRadioStation", []) if x.get("id") == station_id), None)
    if not st or urllib.parse.urlsplit(st.get("streamUrl", "")).scheme not in ("http", "https"):
        raise ValueError("no such station")
    return {"id": st["id"], "title": st["name"], "radio": True, "url": st["streamUrl"]}


def nd_authenticate(password):
    """Sign in to Navidrome: keep md5(password + salt) and the salt, never the password."""
    salt = os.urandom(8).hex()
    creds = {"salt": salt, "token": hashlib.md5((password + salt).encode()).hexdigest()}
    nd("ping", creds=creds)
    write_private(NAVIDROME_FILE, json.dumps(creds))
    nd_creds.update(creds)


def nd_session():
    """For the UI: is there a Navidrome, and is Matinee signed in to it?"""
    if not navidrome:
        return {"configured": False}
    try:
        nd("ping")
        return {"configured": True, "ready": True}
    except NavidromeError as e:
        return {"configured": True, "ready": False, "error": str(e)}


ABS_FILE = CONF_DIR / "audiobookshelf.json"
ABS = conf.get("audiobookshelf") or {}
ABS_SERVER = (ABS.get("server") or "").rstrip("/")
audiobookshelf = Pool(ABS_SERVER) if ABS_SERVER else None
# what the UI may read from Audiobookshelf: the libraries, their books, a book, its cover, what is begun, a search
ABS_PATHS = re.compile(r"/api/(libraries|libraries/[\w-]+/(items|search)|items/[\w-]+(/cover)?|me/items-in-progress|authors/[\w-]+)")
BOOK_SPEEDS = (0.8, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
ABS_SYNC = float(os.environ.get("KINO_ABS_SYNC", "15"))  # seconds between two reports of where a book is
abs_creds = {}


class AbsError(Exception):
    """The message is an error code the UI translates: abs_login_required, abs_auth_rejected, abs_unreachable."""


def abs_request(path, method="GET", body=None, token=None):
    """A request to Audiobookshelf with the API token in its header: (status, content type, body). A rejected
    token is read again from the file once, set-password --audiobookshelf may have stored a new one."""
    if not audiobookshelf:
        raise AbsError("abs_not_configured")
    for attempt in range(2):
        if not token and not abs_creds.get("token"):
            try:
                abs_creds.update(json.loads(ABS_FILE.read_text()))
            except (OSError, ValueError):
                pass
        key = token or abs_creds.get("token")
        if not key:
            raise AbsError("abs_login_required")
        headers = {"Authorization": f"Bearer {key}"}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        try:
            r = audiobookshelf.request(path, method, data, headers)
        except OSError as e:
            raise AbsError("abs_unreachable") from e
        if r.status == 401 and not token and attempt == 0:
            abs_creds.clear()
            continue
        if r.status == 401:
            raise AbsError("abs_auth_rejected")
        return r.status, r.headers.get("Content-Type", "application/json"), r.body


def absj(path, method="GET", body=None, token=None):
    """An Audiobookshelf call: its JSON (some answer a plain "OK", that is {})."""
    status, _, data = abs_request(path, method, body, token)
    if status == 404:
        raise LookupError(path)
    if status >= 400:
        raise AbsError("abs_unreachable")
    try:
        return json.loads(data) if data.strip()[:1] in (b"{", b"[") else {}
    except ValueError:
        raise AbsError("abs_unreachable") from None


def abs_authenticate(token):
    """Keep an API token (Audiobookshelf: Settings → Users → API keys) once it has been tried."""
    absj("/api/libraries", token=token)
    write_private(ABS_FILE, json.dumps({"token": token}))
    abs_creds.clear()
    abs_creds["token"] = token


def abs_session():
    """For the UI: is there an Audiobookshelf, and does it take Matinee's token?"""
    if not audiobookshelf:
        return {"configured": False}
    try:
        absj("/api/libraries")
        return {"configured": True, "ready": True}
    except AbsError as e:
        return {"configured": True, "ready": False, "error": str(e)}


# Podcasts from Podfetch, by its own API (/api/v1): signed in with a user name and password where Podfetch was set up
# with them (its basic auth), or with nothing where it asks for none. Its OpenID Connect sign-in is not supported.
PF_FILE = CONF_DIR / "podfetch.json"
PF = conf.get("podfetch") or {}
PF_SERVER = (PF.get("server") or "").rstrip("/")
podfetch = Pool(PF_SERVER) if PF_SERVER else None
pf_creds = {}
pf_covers = {}  # podcast id → the address of its picture, as Podfetch listed it last
PF_DONE = 15    # seconds: an episode heard to this close to its end (at most a 20th of it) has been heard
PF_ID = re.compile(r"[\w-]{1,100}")  # an episode's id (Podfetch gives each a uuid of its own): never a path


class PfError(Exception):
    """The message is an error code the UI translates: pf_not_configured, pf_login_required, pf_auth_rejected,
    pf_unreachable."""


def pf_auth_header(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()} if user else {}


def pf_password():
    """The password kept for Podfetch (in its own file, read again after it was rejected)."""
    if "password" not in pf_creds:
        try:
            pf_creds.update(json.loads(PF_FILE.read_text()))
        except (OSError, ValueError):
            pass
    return pf_creds.get("password", "")


def pf_request(path, method="GET", body=None, auth=None):
    """A request to Podfetch: (status, content type, body). auth: (user name, password) to try instead of the
    ones kept (the password read again once when Podfetch rejects it)."""
    if not podfetch:
        raise PfError("pf_not_configured")
    for attempt in range(2):
        user, password = auth if auth is not None else (PF.get("username"), pf_password())
        headers = {"Accept": "application/json", **pf_auth_header(user, password)}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        try:
            r = podfetch.request(path, method, data, headers)
        except OSError as e:
            raise PfError("pf_unreachable") from e
        if r.status in (401, 403):
            if auth is None and attempt == 0:
                pf_creds.clear()
                continue
            raise PfError("pf_auth_rejected" if user else "pf_login_required")
        return r.status, r.headers.get("Content-Type", "application/json"), r.body


def pfj(path, method="GET", body=None, auth=None):
    """A Podfetch call: its JSON (an answer without a body is {})."""
    status, _, data = pf_request(path, method, body, auth)
    if status == 404:
        raise LookupError(path)
    if status >= 400:
        raise PfError("pf_unreachable")
    try:
        return json.loads(data) if data.strip() else {}
    except ValueError:
        raise PfError("pf_unreachable") from None


def pf_session():
    """For the UI: is there a Podfetch, and does it let Matinee in?"""
    if not podfetch:
        return {"configured": False}
    try:
        pfj("/api/v1/podcasts")
        return {"configured": True, "ready": True}
    except PfError as e:
        return {"configured": True, "ready": False, "error": str(e)}
    except LookupError:
        return {"configured": True, "ready": False, "error": "pf_unreachable"}


def pf_url(url):
    """An address Podfetch gave: its own files at the address Matinee knows it by (Podfetch builds them from the
    request, as http:// behind a proxy that does not say otherwise), anything else as it is."""
    if not url:
        return ""
    u, base = urllib.parse.urlsplit(url), urllib.parse.urlsplit(PF_SERVER)
    if not u.scheme:
        return f"{PF_SERVER}/{url.lstrip('/')}"
    if u.hostname == base.hostname:
        return urllib.parse.urlunsplit((base.scheme, base.netloc, u.path, u.query, ""))
    return url


def pf_podcast(p):
    """A podcast for the UI; its picture's address stays here (it may carry Podfetch's API key)."""
    pid = str(p.get("id", ""))
    pf_covers[pid] = p.get("image_url") or p.get("original_image_url") or ""
    return {"id": pid, "name": p.get("name") or "", "author": p.get("author") or "",
            "about": p.get("summary") or "", "cover": bool(pf_covers[pid]), "categories": pf_categories(p)}


def pf_categories(p):
    """A podcast's categories, as its feed gives them (Podfetch keeps the feed's iTunes categories, comma-separated,
    as its keywords), each once."""
    seen = []
    for c in str(p.get("keywords") or "").split(","):
        c = " ".join(c.split())[:60]
        if c and c.casefold() not in (x.casefold() for x in seen):
            seen.append(c)
    return seen[:10]


def pf_episode(e, h=None, podcast=None):
    """An episode for the UI: how long it is, where it was left (pos) and whether it has been heard to the end."""
    total = e.get("total_time") or (h or {}).get("total") or 0
    pos = (h or {}).get("position") or 0
    try:  # when it was heard last (Podfetch's time is UTC), for Up Next
        at = int(datetime.fromisoformat(str((h or {}).get("timestamp"))[:19] + "+00:00").timestamp())
    except ValueError:
        at = 0
    return {"at": at, "id": e.get("episode_id") or "", "podcast": str(e.get("podcast_id") or ""), "title": e.get("name") or "",
            "date": (e.get("date_of_recording") or "")[:10], "duration": total, "pos": pos,
            "finished": bool(total and pos >= total - min(PF_DONE, total * 0.05)), "about": e.get("description") or "",
            **({"show": podcast.get("name") or ""} if podcast else {})}


def pf_podcasts():
    return sorted((pf_podcast(p) for p in pfj("/api/v1/podcasts")), key=lambda p: p["name"].casefold())


def pf_podcast_page(pid):
    """A podcast and its episodes, the newest first (as many as Podfetch gives at once)."""
    podcast = pf_podcast(pfj(f"/api/v1/podcasts/{pid}"))
    episodes = [pf_episode(x.get("podcastEpisode") or {}, x.get("podcastHistoryItem"))
                for x in pfj(f"/api/v1/podcasts/{pid}/episodes")]
    return {"podcast": podcast, "episodes": [e for e in episodes if e["id"]]}


def pf_recent():
    """What is begun (the latest first), the podcasts heard (the last one first) and the newest episodes of all
    podcasts, each with its podcast's name."""
    begun, heard = [], []
    for x in pfj("/api/v1/podcasts/episode/lastwatched"):
        p = pf_podcast(x.get("podcast") or {})
        e = pf_episode(x.get("podcastEpisode") or {}, x.get("episode"), p)
        if e["id"] and e["pos"] > 0 and not e["finished"]:
            begun.append(e)
        if p["id"] and p["id"] not in heard:
            heard.append(p["id"])
    try:
        timeline = pfj("/api/v1/podcasts/timeline?favoredOnly=false&notListened=false&favoredEpisodes=false")
    except (PfError, LookupError):
        timeline = {}
    latest = []
    for x in timeline.get("data") or []:
        p = pf_podcast(x.get("podcast") or {})
        e = pf_episode(x.get("podcast_episode") or {}, x.get("history"), p)
        if e["id"]:
            latest.append(e)
    return {"begun": begun, "heard": heard, "latest": latest}


def pf_cover(pid):
    """A podcast's picture: (content type, bytes), from Podfetch (signed in) or from where its feed has it."""
    url = pf_covers.get(pid)
    if url is None:
        url = pf_covers.get(pf_podcast(pfj(f"/api/v1/podcasts/{pid}"))["id"], "")
    url = pf_url(url)
    if not url.startswith(("http://", "https://")):
        raise LookupError(pid)
    own = urllib.parse.urlsplit(url).hostname == urllib.parse.urlsplit(PF_SERVER).hostname
    headers = {"User-Agent": "Matinee", **(pf_auth_header(PF.get("username"), pf_password()) if own else {})}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
        ctype = r.headers.get("Content-Type", "")
        data = r.read(10_000_001)
    if not ctype.startswith("image/") or len(data) > 10_000_000:
        raise LookupError(pid)
    return ctype, data


def pf_play(episode_id, at=None):
    """An episode as an entry of the music player's queue, played like a book (faster or slower, 30 s back and on,
    the sleep timer), from where it was left or at "at". Its address may carry Podfetch's API key: mpv only ever
    gets it over IPC."""
    x = pfj(f"/api/v1/episodes/{urllib.parse.quote(episode_id, safe='')}")
    e = x.get("podcastEpisode") or {}
    url = pf_url(e.get("local_url") or e.get("url"))
    if not url:
        raise LookupError(episode_id)
    try:
        podcast = pf_podcast(pfj(f"/api/v1/podcasts/{urllib.parse.quote(str(e.get('podcast_id')), safe='')}"))
    except (PfError, LookupError):
        podcast = {"id": str(e.get("podcast_id") or ""), "name": ""}
    ep = pf_episode(e, x.get("podcastHistoryItem"))
    try:
        chapters = pfj(f"/api/v1/podcasts/episodes/{urllib.parse.quote(str(e.get('id')), safe='')}/chapters")
    except (PfError, LookupError):
        chapters = []
    duration = float(ep["duration"] or 0)
    start = float(ep["pos"]) if at is None else float(at)
    if ep["finished"] and at is None:  # heard to the end: from the start again
        start = 0
    return {"id": ep["id"], "book": True, "podcast": podcast["id"], "url": url, "start": start,
            "title": ep["title"], "artist": podcast["name"], "duration": duration or None,
            "chapters": [{"start": c.get("startTime", 0), "end": c.get("endTime", 0), "title": c.get("title") or ""}
                         for c in chapters if isinstance(c, dict)]}


def pf_find(term):
    """Podcasts to follow, from the podcast directory through Podfetch's search: [{"id" (the directory's), "name", "author", "art",
    "genre", "episodes", "following"}], the ones already followed marked (by their feed)."""
    found = pfj(f"/api/v1/podcasts/0/{urllib.parse.quote(term, safe='')}/search")
    followed = {str(p.get("rssfeed") or "").rstrip("/").lower() for p in pfj("/api/v1/podcasts")}
    out = []
    for r in (found.get("results") or []) if isinstance(found, dict) else []:
        pid = r.get("collectionId") or r.get("trackId")
        if not isinstance(pid, int) or not r.get("feedUrl"):
            continue  # nothing Podfetch could add
        art = r.get("artworkUrl600") or r.get("artworkUrl100") or ""
        out.append({"id": pid, "name": r.get("collectionName") or r.get("trackName") or "", "author": r.get("artistName") or "",
                    "art": art if art.startswith("https://") else "", "genre": r.get("primaryGenreName") or "",
                    "episodes": r.get("trackCount") or 0,
                    "following": str(r["feedUrl"]).rstrip("/").lower() in followed})
    return out[:30]


def pf_follow(directory_id):
    """Follow a podcast from the podcast directory: Podfetch adds it (and fetches its episodes). Its id in Podfetch."""
    added = pfj("/api/v1/podcasts/itunes", "POST", {"trackId": int(directory_id), "userId": 0})
    return str((added or {}).get("id") or "")


def pf_unfollow(pid, files):
    """Unfollow a podcast: Podfetch drops it with its episodes and where they were heard, and with files=True also the
    episodes it downloaded."""
    pfj(f"/api/v1/podcasts/{pid}", "DELETE", {"delete_files": bool(files)})
    pf_covers.pop(pid, None)


def pf_heard(episode_id, seconds):
    """Tell Podfetch where an episode is (seconds; its whole length: heard to the end)."""
    pfj("/api/v1/podcasts/episode", "POST", {"podcastEpisodeId": episode_id, "time": int(seconds)})


# Adding Navidrome, Audiobookshelf or Podfetch from the settings, while Matinee runs: the server (and user name) go into
# config.json, the sign-in into its own file as with set-password; nothing is kept unless the sign-in works.
SERVICE_FILES = {"navidrome": NAVIDROME_FILE, "audiobookshelf": ABS_FILE, "podfetch": PF_FILE}


SERVER_HOST = re.compile(r"[A-Za-z0-9.-]+|[0-9A-Fa-f:.]+")  # a name or IPv4 address, or an IPv6 one (its brackets off)


def service_url(value):
    """A server's address as typed: https:// when no scheme is given, no slash at the end. Words with spaces, a host of
    other marks or a port that is none are no address (said at once, not as a server that does not answer)."""
    url = str(value or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    u = urllib.parse.urlsplit(url)
    try:
        port_ok = u.port is None or u.port > 0
    except ValueError:  # not a number, or above 65535
        port_ok = False
    if (u.scheme not in ("http", "https") or not u.hostname or len(url) > 300 or u.query or u.fragment or u.username
            or any(c.isspace() for c in url) or not SERVER_HOST.fullmatch(u.hostname) or not port_ok):
        raise ValueError("service_bad_server")
    return url


def use_service(name, settings):
    """Point Matinee at a Navidrome, Audiobookshelf or Podfetch (settings None: at none)."""
    global ND, ND_SERVER, navidrome, ABS, ABS_SERVER, audiobookshelf, PF, PF_SERVER, podfetch
    if name == "podfetch":
        PF = settings or {}
        PF_SERVER = (PF.get("server") or "").rstrip("/")
        podfetch = Pool(PF_SERVER) if PF_SERVER else None
        pf_creds.clear()
        pf_covers.clear()
    elif name == "navidrome":
        ND = settings or {}
        ND_SERVER = (ND.get("server") or "").rstrip("/")
        navidrome = Pool(ND_SERVER) if ND_SERVER else None
        nd_creds.clear()
    else:
        ABS = settings or {}
        ABS_SERVER = (ABS.get("server") or "").rstrip("/")
        audiobookshelf = Pool(ABS_SERVER) if ABS_SERVER else None
        abs_creds.clear()


def configure_service(body):
    """{"service": "navidrome", "server", "username", "password"}, {"service": "audiobookshelf", "server", "key"},
    {"service": "podfetch", "server", "username", "password"} (both left empty for a Podfetch without sign-in),
    or {"service": …, "remove": true}. Raises ValueError with an error code the UI translates."""
    name = body.get("service") if isinstance(body, dict) else None
    if name not in SERVICE_FILES:
        raise ValueError("service_unknown")
    if body.get("remove"):
        with conf_changing() as c:
            c.pop(name, None)
        conf.pop(name, None)
        SERVICE_FILES[name].unlink(missing_ok=True)
        use_service(name, None)
        return
    server_url = service_url(body.get("server"))
    if name == "podfetch":
        return configure_podfetch(server_url, body)
    secret = str(body.get("password" if name == "navidrome" else "key") or "")
    if not secret or len(secret) > 4096:
        raise ValueError("service_missing_secret")
    settings = {"server": server_url}
    if name == "navidrome":
        user = str(body.get("username") or "").strip()
        if not user or len(user) > 200:
            raise ValueError("service_missing_user")
        settings["username"] = user
    before = ND if name == "navidrome" else ABS
    use_service(name, settings)
    try:
        if name == "navidrome":
            nd_authenticate(secret)
        else:
            abs_authenticate(secret)
    except Exception as e:  # also what is no answer of either (a 404 there, one that cannot be read)
        use_service(name, before)  # what was there stays, signed in as before
        code = str(e) if isinstance(e, (NavidromeError, AbsError)) else ""
        raise ValueError("service_rejected" if code.endswith("auth_rejected") else "service_unreachable") from None
    with conf_changing() as c:
        c[name] = conf[name] = settings


def abs_login_token(server_url, user, password):
    """Audiobookshelf's own token for a user name and password (its sign-in), kept as a typed API key would be: so a
    second person signs in there with what they use everywhere, needing no API key made for them by an admin."""
    req = urllib.request.Request(f"{server_url}/login", data=json.dumps({"username": user, "password": password}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            token = json.loads(read_within(r, 15))["user"]["token"]
    except urllib.error.HTTPError as e:
        raise ValueError("service_rejected" if e.code in (401, 403) else "service_unreachable") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("service_unreachable") from None
    if not isinstance(token, str) or not token:
        raise ValueError("service_unreachable")
    return token


# Where Navidrome, Audiobookshelf and Podfetch usually listen, and what only each of them answers there: so Sign In
# Everywhere finds them on Jellyfin's computer when they are not set up yet (a new TV computer, all on one server).
# KINO_BESIDE_PORTS ({"navidrome": [port], …}) puts other ports in their place (the tests' servers)
BESIDE_JELLYFIN = {
    "navidrome": ((4533,), "/rest/ping.view?f=json&v=1.16.1&c=Matinee",
                  lambda d: d.get("subsonic-response", {}).get("type") == "navidrome"),
    "audiobookshelf": ((13378, 13379), "/status", lambda d: d.get("app") == "audiobookshelf"),
    "podfetch": ((8000,), "/api/v1/sys/config", lambda d: "podindexConfigured" in d),
}
for _name, _ports in json.loads(os.environ.get("KINO_BESIDE_PORTS") or "{}").items():
    BESIDE_JELLYFIN[_name] = (tuple(_ports),) + BESIDE_JELLYFIN[_name][1:]


def beside_jellyfin(name, jellyfin_url):
    """The address of this service on Jellyfin's computer, at one of its usual ports, or None: each tried at once, in
    Jellyfin's scheme first, and only one answering as that service counts."""
    u = urllib.parse.urlsplit(jellyfin_url or "")
    if not u.hostname or u.scheme not in ("http", "https"):
        return None
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    ports, path, answers = BESIDE_JELLYFIN[name]
    tried = [f"{scheme}://{host}:{port}" for scheme in (u.scheme, "http" if u.scheme == "https" else "https") for port in ports]
    found = [None] * len(tried)

    def probe(i):
        try:
            with urllib.request.urlopen(tried[i] + path, timeout=4) as r:
                data = json.loads(read_within(r, 4))
            found[i] = isinstance(data, dict) and answers(data)
        except (OSError, ValueError, AttributeError):
            pass

    threads = [threading.Thread(target=probe, args=(i,), daemon=True) for i in range(len(tried))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(8)
    return next((url for url, ok in zip(tried, found) if ok), None)


def sign_in_everywhere(body):
    """Settings → Account → Sign In Everywhere: one user name and password for every service set up on this device,
    and for those not set up yet that run on Jellyfin's computer at their usual ports (beside_jellyfin). Jellyfin not
    set up yet (a new TV computer): its address comes along as "server". Each refusing them stays signed in as it was.
    {service: "ok" | "rejected" | "unreachable" | "not_set_up"}; Jellyfin last, as another user there starts its part
    of Matinee anew."""
    if not isinstance(body, dict):
        raise ValueError("service_unknown")
    user, password = str(body.get("username") or "").strip(), str(body.get("password") or "")
    if not user or len(user) > 200:
        raise ValueError("service_missing_user")
    if len(password) > 4096:
        raise ValueError("service_missing_secret")
    jellyfin_url = core.SERVER or (service_url(body["server"]) if str(body.get("server") or "").strip() else None)
    if not (jellyfin_url or ND.get("server") or ABS_SERVER or PF.get("server")):
        raise ValueError("everywhere_need_server")  # nothing to sign in to, nor a computer to look on
    results = {}

    def attempt(name, server_url, sign_in):
        server_url = server_url or (beside_jellyfin(name, jellyfin_url) if name in BESIDE_JELLYFIN else None)
        if not server_url:
            results[name] = "not_set_up"
            return
        try:
            sign_in(server_url)
            results[name] = "ok"
        except ValueError as e:
            results[name] = "unreachable" if str(e) == "service_unreachable" else "rejected"

    attempt("navidrome", ND.get("server"), lambda url: configure_service(
        {"service": "navidrome", "server": url, "username": user, "password": password}))
    attempt("audiobookshelf", ABS_SERVER, lambda url: configure_service(
        {"service": "audiobookshelf", "server": url, "key": abs_login_token(url, user, password)}))
    attempt("podfetch", PF.get("server"), lambda url: configure_service(
        {"service": "podfetch", "server": url, "username": user, "password": password}))
    attempt("jellyfin", jellyfin_url, lambda url: setup_jellyfin({"server": url, "username": user, "password": password}))
    return results


def configure_podfetch(server_url, body):
    """Podfetch with a user name and password, or with neither (one without sign-in)."""
    user = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if len(user) > 200 or len(password) > 4096:
        raise ValueError("service_missing_user")
    if user and not password:
        raise ValueError("service_missing_secret")
    if password and not user:
        raise ValueError("service_missing_user")
    settings = {"server": server_url, **({"username": user} if user else {})}
    before = PF
    use_service("podfetch", settings)
    try:
        pfj("/api/v1/podcasts", auth=(user, password))
    except (PfError, LookupError) as e:
        use_service("podfetch", before)  # what was there stays, signed in as before
        code = str(e)
        raise ValueError("service_rejected" if code in ("pf_auth_rejected", "pf_login_required") else "service_unreachable") from None
    if user:
        write_private(PF_FILE, json.dumps({"password": password}))
    else:
        PF_FILE.unlink(missing_ok=True)
    pf_creds.clear()
    with conf_changing() as c:
        c["podfetch"] = conf["podfetch"] = settings


def abs_book(item_id, at=None):
    """A book as an entry of the music player's queue: Audiobookshelf opens a listening session for it, its files
    become one (mpv's edl, so its chapters can be reached across them), and it starts where it was left
    (or at "at", e.g. a chapter). The address carries the token: mpv only ever gets it over IPC."""
    s = absj(f"/api/items/{item_id}/play", "POST", {
        "deviceInfo": {"clientName": "Matinee", "deviceId": state["device_id"], "clientVersion": VERSION},
        "forceDirectPlay": True, "mediaPlayer": "mpv",
        "supportedMimeTypes": ["audio/mpeg", "audio/mp4", "audio/x-m4b", "audio/aac", "audio/ogg", "audio/flac",
                               "audio/opus", "audio/webm", "audio/wav"]})
    tracks = sorted(s.get("audioTracks") or [], key=lambda x: x.get("startOffset", 0))
    if not tracks:
        raise LookupError("no audio")
    token = urllib.parse.quote(abs_creds["token"], safe="")
    urls = [f"{ABS_SERVER}{x['contentUrl']}?token={token}" for x in tracks]
    url = urls[0] if len(urls) == 1 else "edl://" + ";".join(
        f"%{len(u.encode())}%{u},length={x['duration']}" for u, x in zip(urls, tracks))
    duration = float(s.get("duration") or sum(x.get("duration", 0) for x in tracks))
    start = float(s.get("currentTime") or 0) if at is None else float(at)
    if start >= duration - 10:  # heard to the end: from the start again
        start = 0
    hide("books", item_id, on=False)  # played again: back in "Continue Listening"
    return {"id": item_id, "book": True, "session": s["id"], "url": url, "start": start,
            "v": (s.get("libraryItem") or {}).get("updatedAt"),  # for the cover's address, see bookCover()
            "title": s.get("displayTitle") or "", "artist": s.get("displayAuthor") or "", "duration": duration,
            "chapters": [{"start": c.get("start", 0), "end": c.get("end", 0), "title": c.get("title", "")}
                         for c in s.get("chapters") or []]}


# The reports of where a book or episode is go one after another, in the order made, from one thread: each in a thread
# of its own, a report from before (where it was a second ago) could arrive after the one of its end and undo it
book_reports = queue.Queue()
BOOK_REPORT_RETRY = 2  # seconds before the report of an end is sent once more


def book_reporter():
    while True:
        progress, what, report, finished = book_reports.get()
        try:
            progress._send(what, report, finished)
        except Exception as e:  # never the end of the reports
            print(f"Progress report failed: {e}", file=sys.stderr)


threading.Thread(target=book_reporter, daemon=True).start()


class BookProgress:
    """While a book plays, Audiobookshelf hears where it is every ABS_SYNC seconds and when it stops, so its own
    app (on the phone, say) carries on from there; heard to the end, it is marked as finished. A podcast's episode
    (Podfetch) the same way: Podfetch hears where it is, at the end its whole length."""

    def __init__(self, book):
        self.book, self.pos, self.listened = book, book.get("start", 0), 0.0
        self.at = self.synced = time.monotonic()

    def chapter_ends(self, now, lead):
        """Whether playing on from the last position to now passed the point lead seconds before a chapter's end
        (not a jump: that is no chapter played to its end)."""
        before = self.pos
        if before is None or now is None or not 0 < now - before < 8:
            return False
        return any(before < c["end"] - lead <= now for c in self.book.get("chapters") or [])

    def tick(self, pos, paused):
        now = time.monotonic()
        if pos is not None and not paused and pos != self.pos:
            self.listened += min(now - self.at, 5)
        if pos is not None:
            self.pos = pos
        self.at = now
        if now - self.synced >= ABS_SYNC:
            self.synced = now
            self.send("sync")

    def send(self, what, finished=False):
        report = {"currentTime": (self.book["duration"] or self.pos) if finished else self.pos,
                  "timeListened": round(self.listened, 1), "duration": self.book["duration"]}
        self.listened = 0.0
        book_reports.put((self, what, report, finished))

    def _send(self, what, report, finished):
        b = self.book
        if b.get("podcast") is not None:
            for attempt in range(2 if finished else 1):  # the end once more: else it stays unheard
                try:
                    if report["currentTime"] is not None:
                        pf_heard(b["id"], report["currentTime"])
                    return
                except (PfError, LookupError, OSError) as e:
                    log_net(f"Podfetch progress failed: {e}", e)
                    if attempt == 0 and finished:
                        time.sleep(BOOK_REPORT_RETRY)
            return
        try:
            try:
                absj(f"/api/session/{b['session']}/{what}", "POST", report)
            except LookupError:  # the session is gone (Audiobookshelf restarted): the progress all the same
                absj(f"/api/me/progress/{b['id']}", "PATCH", {
                    "currentTime": report["currentTime"], "duration": b["duration"],
                    "progress": min(1, report["currentTime"] / b["duration"]) if b["duration"] else 0})
            if finished:
                absj(f"/api/me/progress/{b['id']}", "PATCH", {"isFinished": True})
        except (AbsError, LookupError, OSError) as e:
            print(f"Audiobookshelf progress failed: {e}", file=sys.stderr)


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import music_player  # noqa: E402,F401
