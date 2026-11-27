"""Fake Jellyfin server for the tests.

Keeps state (watched, position, last played), checks authentication like Jellyfin 12
(WebSocket only with an Authorization header) and pushes changes over a WebSocket.
"""
import base64
import hashlib
import json
import re
import socket
import struct
import threading
import time
import urllib.parse
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = "tok-123"
USER_ID = "u1"
USERNAME = "me"
PASSWORD = "secret"
TICKS = 10_000_000
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def logo_png(w=64, h=24, rgb=(20, 20, 30)):
    """A logo as Jellyfin's usually are: dark letters (a bar here) on a transparent ground (RGBA)."""
    row = lambda y: b"\x00" + b"".join(bytes(rgb) + (b"\xff" if h // 3 <= y < 2 * h // 3 and 4 <= x < w - 4 else b"\x00")
                                        for x in range(w))
    raw = b"".join(row(y) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")


class Library:
    def __init__(self, runtime_s):
        self.runtime_s = runtime_s
        self.trickplay = False  # Jellyfin made pictures for seeking
        rt = int(runtime_s * TICKS)
        self.items = {}
        self.genres = {"g1": "Drama", "g2": "Action", "g3": "Comedy"}
        for i in range(1, 13):
            gid = f"g{i % 3 + 1}"
            self.items[f"m{i}"] = {
                "Id": f"m{i}", "Name": f"Movie {i}", "Type": "Movie", "ProductionYear": 2010 + i, "RunTimeTicks": rt,
                "Overview": f"Description of movie {i}.", "Genres": [self.genres[gid]], "GenreIds": [gid], "ParentId": "lm",
                "PremiereDate": f"{2010 + i}-01-01T00:00:00.0000000Z", "DateCreated": f"2026-01-{i:02}T00:00:00.0000000Z",
                "CommunityRating": (i * 7) % 10 + 0.5, "SortName": f"movie {i:02}",
                "ImageTags": {"Primary": "p", "Logo": "l"}, "BackdropImageTags": ["b"],
                "Path": f"/media/movies/Movie.{i}.{2010 + i}.1080p.mkv", "ProviderIds": {"Tmdb": str(1000 + i)},
                "PrimaryImageAspectRatio": 2 / 3,
            }
        self.items["m4"]["RemoteTrailers"] = [{"Url": "https://www.youtube.com/watch?v=trailer0004", "Name": "Trailer"}]
        # Collections: movies 3, 1, 2 (deliberately not in release order)
        self.boxsets = {"bx1": ["m3", "m1", "m2"], "bx2": ["m10", "m11"]}
        for bid, name in (("bx1", "First Collection"), ("bx2", "Second Collection")):
            self.items[bid] = {"Id": bid, "Name": name, "Type": "BoxSet", "ParentId": "lc", "SortName": name.lower(),
                               "Overview": f"{name}.", "ImageTags": {"Primary": "p"}, "BackdropImageTags": ["b"]}
        self.items["s1"] = {"Id": "s1", "Name": "Test Show", "Type": "Series", "ProductionYear": 2020, "ChildCount": 3,
                            "Overview": "A show.", "ParentId": "lt", "ImageTags": {"Primary": "p"}, "BackdropImageTags": ["b"]}
        self.episodes = []
        for s in (1, 2):
            self.items[f"se{s}"] = {"Id": f"se{s}", "Name": f"Season {s}", "Type": "Season", "SeriesId": "s1", "IndexNumber": s}
            for e in range(1, 5):
                eid = f"e{s}{e}"
                self.items[eid] = {
                    "Id": eid, "Name": f"Episode {e}", "Type": "Episode", "SeriesId": "s1", "SeriesName": "Test Show",
                    "SeasonId": f"se{s}", "SeasonName": f"Season {s}", "ParentIndexNumber": s, "IndexNumber": e, "RunTimeTicks": rt,
                    "Overview": f"Plot of S{s}E{e}.", "ImageTags": {"Primary": "p"}, "SeriesPrimaryImageTag": "p",
                    "ParentBackdropItemId": "s1", "ParentBackdropImageTags": ["b"],
                }
                self.episodes.append(eid)
        # Like Jellyfin: a Specials season with a placeholder for a missing special (from the metadata, no file)
        # and a real one, both placed before season 2 (they count in ChildCount, series order puts them before e21)
        self.items["se0"] = {"Id": "se0", "Name": "Specials", "Type": "Season", "SeriesId": "s1", "IndexNumber": 0}
        self.items["e01"] = {"Id": "e01", "Name": "Missing Special", "Type": "Episode", "SeriesId": "s1",
                             "SeriesName": "Test Show", "SeasonId": "se0", "SeasonName": "Specials", "ParentIndexNumber": 0,
                             "IndexNumber": 1, "AirsBeforeSeasonNumber": 2, "LocationType": "Virtual", "ImageTags": {}}
        self.items["e02"] = {"Id": "e02", "Name": "Special", "Type": "Episode", "SeriesId": "s1", "SeriesName": "Test Show",
                             "SeasonId": "se0", "SeasonName": "Specials", "ParentIndexNumber": 0, "IndexNumber": 2,
                             "AirsBeforeSeasonNumber": 2, "RunTimeTicks": rt, "ImageTags": {"Primary": "p"}}
        # Placeholders at the end of season 2: one aired that is not in the library, one not aired yet
        for e, aired in ((5, "2021-03-01T00:00:00.0000000Z"), (6, "2999-01-01T00:00:00.0000000Z")):
            self.items[f"e2{e}"] = {"Id": f"e2{e}", "Name": f"Episode {e}", "Type": "Episode", "SeriesId": "s1",
                                    "SeriesName": "Test Show", "SeasonId": "se2", "SeasonName": "Season 2", "ParentIndexNumber": 2,
                                    "IndexNumber": e, "PremiereDate": aired, "LocationType": "Virtual", "ImageTags": {}}
        self.series_order = self.episodes[:4] + ["e01", "e02"] + self.episodes[4:] + ["e25", "e26"]
        # m3 has an external subtitle file, m5 picture subtitles (as from a Blu-ray)
        self.subtitled = {"m3", "e21", "e22"}
        self.pgs = {"m5"}
        self.embedded_subs = {"m6"}  # text subtitles inside the file: Jellyfin takes them out first
        # someone of the cast (view's People): their page and what with them is in the library
        self.people = {"p1": {"Id": "p1", "Name": "Actress", "Type": "Person", "PremiereDate": "1970-05-01T00:00:00.0000000Z",
                              "ProductionLocations": ["Berlin, Germany"], "Overview": "An actress.", "ImageTags": {"Primary": "x"}}}
        self.person_items = {"p1": ["m1", "m2", "s1"]}
        self.forced_subs = set()  # a forced German subtitle file beside the full one (the lines in another language)
        # m6 as a 4K HDR film in HEVC with 10 bits: what a Skylake thin client cannot decode itself
        self.video_info = {"m6": {"Codec": "hevc", "BitDepth": 10, "Width": 3840, "Height": 2160, "VideoRange": "HDR"}}
        self.userdata = {i: {"Played": False, "PlaybackPositionTicks": 0, "PlayCount": 0, "LastPlayedDate": None} for i in self.items}
        self.favorites = set()

    def add_missing_season(self):
        """A season 3 with no file at all: only the placeholders of two aired episodes (and a Season item of its own,
        as Jellyfin makes one for missing episodes)."""
        self.items["se3"] = {"Id": "se3", "Name": "Season 3", "Type": "Season", "SeriesId": "s1", "IndexNumber": 3,
                             "LocationType": "Virtual"}
        for e in (1, 2):
            self.items[f"e3{e}"] = {"Id": f"e3{e}", "Name": f"Episode {e}", "Type": "Episode", "SeriesId": "s1",
                                    "SeriesName": "Test Show", "SeasonId": "se3", "SeasonName": "Season 3", "ParentIndexNumber": 3,
                                    "IndexNumber": e, "PremiereDate": "2022-01-01T00:00:00.0000000Z", "LocationType": "Virtual",
                                    "ImageTags": {}}
            self.series_order.append(f"e3{e}")
            self.userdata[f"e3{e}"] = {"Played": False, "PlaybackPositionTicks": 0, "PlayCount": 0, "LastPlayedDate": None}
        self.userdata["se3"] = {"Played": False, "PlaybackPositionTicks": 0, "PlayCount": 0, "LastPlayedDate": None}

    def add_movies(self, n):
        """A big library: n more movies (Movie 13 and up) without genre."""
        for i in range(13, 13 + n):
            self.items[f"m{i}"] = {"Id": f"m{i}", "Name": f"Movie {i}", "Type": "Movie", "ProductionYear": 2000,
                                   "ParentId": "lm", "SortName": f"movie z{i:03}", "ImageTags": {"Primary": "p"}}
            self.userdata[f"m{i}"] = {"Played": False, "PlaybackPositionTicks": 0, "PlayCount": 0, "LastPlayedDate": None}

    def view(self, iid, full=False):
        it = dict(self.items[iid])
        ud = dict(self.userdata[iid], IsFavorite=iid in self.favorites)
        if it.get("RunTimeTicks") and ud["PlaybackPositionTicks"]:
            ud["PlayedPercentage"] = 100 * ud["PlaybackPositionTicks"] / it["RunTimeTicks"]
        if it["Type"] in ("Series", "BoxSet"):
            eps = [self.userdata[e]["Played"] for e in (self.episodes if it["Type"] == "Series" else self.boxsets[iid])]
            ud["UnplayedItemCount"] = eps.count(False)
            ud["Played"] = all(eps)
            ud["PlayedPercentage"] = 100 * eps.count(True) / len(eps)  # as Jellyfin counts it for a folder
        it["UserData"] = ud
        if full and it["Type"] in ("Movie", "Episode"):
            streams = [{"Type": "Video", "Index": 0, "RealFrameRate": 25.0, "Codec": "h264", "BitDepth": 8,
                        **self.video_info.get(iid, {})}, {"Type": "Audio", "Index": 1, "Language": "eng"},
                       {"Type": "Audio", "Index": 2, "Language": "deu"}]
            if iid in self.subtitled:
                streams.append({"Type": "Subtitle", "Index": 3, "IsExternal": True, "Codec": "srt", "Language": "ger"})
            if iid in self.forced_subs:
                streams.append({"Type": "Subtitle", "Index": 6, "IsExternal": True, "Codec": "srt", "Language": "ger", "IsForced": True})
            if iid in self.embedded_subs:
                streams.append({"Type": "Subtitle", "Index": 5, "Codec": "subrip", "Language": "eng", "IsForced": True})
            if iid in self.pgs:
                streams.append({"Type": "Subtitle", "Index": 4, "Codec": "PGSSUB", "Language": "deu"})
            # as Jellyfin reports it: a high bitrate, so a slow line leads to transcoding
            it["MediaSources"] = [{"Id": f"ms-{iid}", "MediaStreams": streams, "Bitrate": 8_000_000}]
            if self.trickplay:  # a picture every 10 s, all in one tile (MockJellyfin.trickplay_tile)
                count = -(-self.runtime_s // 10)
                it["Trickplay"] = {f"ms-{iid}": {"320": {"Width": 320, "Height": 180, "TileWidth": 10, "TileHeight": 10,
                                                         "ThumbnailCount": count, "Interval": 10000, "Bandwidth": 1}}}
            it["People"] = [{"Id": "p1", "Name": "Actress", "Role": "Role", "Type": "Actor", "PrimaryImageTag": "x"},
                            {"Id": "p2", "Name": "Some Director", "Type": "Director"}]
        return it

    def query(self, q):
        """Subset of /Items: parent, types, search, filters, genre, sorting."""
        parent = q.get("parentId", [""])[0]
        if parent in self.boxsets:
            ids = list(self.boxsets[parent])
        elif parent:
            ids = [i for i, it in self.items.items() if it.get("ParentId") == parent]
        else:
            ids = list(self.items)
        if "Ids" in q:
            ids = [i for i in q["Ids"][0].split(",") if i in self.items]
        # placeholders for missing episodes: only in a query for episodes alone, as with Jellyfin's
        # "Display missing episodes" (Matinee's episode counts)
        if q.get("IncludeItemTypes") != ["Episode"] or "IsMissing" in q:
            ids = [i for i in ids if self.items[i].get("LocationType") != "Virtual"]
        if "IncludeItemTypes" in q:
            types = q["IncludeItemTypes"][0].split(",")
            ids = [i for i in ids if self.items[i]["Type"] in types]
        if "PersonIds" in q:
            ids = [i for i in ids if i in self.person_items.get(q["PersonIds"][0], [])]
        if "searchTerm" in q:
            term = q["searchTerm"][0].lower()
            ids = [i for i in ids if term in self.items[i]["Name"].lower()]
        if q.get("Filters") == ["IsUnplayed"]:
            ids = [i for i in ids if not self.view(i)["UserData"]["Played"]]
        if q.get("Filters") == ["IsFavorite"]:
            ids = [i for i in ids if i in self.favorites]
        if "GenreIds" in q:
            ids = [i for i in ids if q["GenreIds"][0] in self.items[i].get("GenreIds", [])]
        sort = q.get("SortBy", ["SortName"])[0].split(",")[0]
        ids.sort(key=lambda i: self.items[i].get(sort) or self.items[i].get("SortName") or self.items[i]["Name"],
                 reverse=q.get("SortOrder") == ["Descending"])
        return ids

    def segments(self, iid):
        if self.items.get(iid, {}).get("Type") != "Episode" or "RunTimeTicks" not in self.items[iid]:
            return []
        rt = self.items[iid]["RunTimeTicks"]
        return [{"Id": f"{iid}-i", "ItemId": iid, "Type": "Intro", "StartTicks": int(rt * 0.1), "EndTicks": int(rt * 0.4)},
                {"Id": f"{iid}-o", "ItemId": iid, "Type": "Outro", "StartTicks": int(rt * 0.75), "EndTicks": int(rt * 0.98)}]

    def set_played(self, iid, played):
        kind = self.items[iid]["Type"]
        ids = self.episodes if kind == "Series" else self.boxsets[iid] if kind == "BoxSet" else [iid]
        for i in ids:
            ud = self.userdata[i]
            ud["Played"] = played
            ud["PlaybackPositionTicks"] = 0
            ud["PlayCount"] = ud["PlayCount"] + 1 if played else 0
            ud["LastPlayedDate"] = now_iso() if played else None


class MockJellyfin:
    def __init__(self, video_path=None, subtitle=b"1\n00:00:00,500 --> 00:00:20,000\nSubtitle\n", runtime_s=20):
        self.lib = Library(runtime_s)
        self.video_path = video_path
        self.subtitle = subtitle
        self.subtitle_delay = 0  # seconds until a subtitle file is sent
        self.reports = []         # (path, body) of all playback reports
        self.requests = []        # all paths
        self.display_prefs = {}   # (id, client): what a client keeps with the user (Matinee's sync)
        self.titles_slow = 0      # seconds the list of all titles takes (as a small server's with hundreds of them)
        self.accounts = {}        # another person's user name → password, signed in as the same user here
        self.user_ids = {}        # a user name → a Jellyfin user of its own (otherwise the one user, USER_ID)
        # subtitles a subtitle plugin finds on the internet, by language (ISO 639-2); those downloaded: (item, id)
        self.remote_subs = {"ger": [{"Id": "os-ger-1", "Name": "Movie.1.German.srt", "ProviderName": "Open Subtitles",
                                     "Format": "srt", "DownloadCount": 1234, "IsHashMatch": True},
                                    {"Id": "os-ger-2/../x", "Name": "Bad id", "ProviderName": "Open Subtitles", "Format": "srt"}],
                            "eng": []}
        self.subs_downloaded = []
        self.auth_seen = []       # Authorization headers
        self.ws_clients = []      # open WebSocket connections (queues)
        self.ws_devices = {}      # device id → (its queue, its name, its client): connected over the WebSocket now
        # device id → {"name", "client", "last"}: the sessions, as /Sessions lists them. As Jellyfin's, one stays
        # listed after its WebSocket closed (IsActive false), and its LastActivityDate is when it last connected or
        # played: the WebSocket's KeepAlive does not count, so a Matinee left on for a while is "inactive" by that date
        self.sessions = {}
        self.messages = []        # (session id, header, text) sent to a session (/Sessions/{id}/Message)
        self.remote_sent = []     # (device id, kind, data): Jellyfin's remote control as sent to a session, by Matinee Remote
        self.now_playing = {}     # device id → {"NowPlayingItem", "PlayState"}: what /Sessions says a session plays
        self.min_resume = 0       # Jellyfin keeps no position before so much of a film (its MinResumePct: 5 %)
        self.busy_paths = set()   # paths answered 503 (Jellyfin starting, or a proxy in front of it restarting)
        self.busy_next = {}       # {path: n}: its next n requests answered 503, then it answers again
        self.deaf = set()         # device ids whose messages are taken but never arrive (a laptop closed meanwhile)
        self.ws_connects = 0
        self.ws_rejected = 0
        self.token = TOKEN       # handed out on login, the only one accepted
        self.fresh = False       # a server just installed: its startup wizard not done (Startup/*), as Jellyfin's own
        self.startup = []        # what the startup wizard was told, in order: (path, body)
        # so many answers to /System/Info/Public first from Jellyfin 12's stand-in while it starts: camelCase, and
        # saying its wizard is done whether it is or not
        self.stand_in = 0
        self.folders = []        # the libraries added: (name, collectionType, paths)
        self.folder_options = []  # and the LibraryOptions each came with
        self.refreshes = 0        # whole scans asked for (Library/Refresh)
        self.system_conf = {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en",
                            "ServerName": "Mock"}  # /System/Configuration, an administrator's
        self.user_changes = []    # what was changed of users, in order: ("name" | "password", user id, the new one)
        self.logins = []          # the DeviceId of each sign-in
        self.logouts = []         # the DeviceId of each session ended
        self.similar_delay = 0
        self.items_delay = 0
        self.views_delay = 0     # seconds until the libraries are listed (a slow line)
        self.info_delay = 0      # seconds until the server says its version (off, or the network gone)
        self.stopped_delay = 0   # seconds until a stop report is taken
        self.progress_delay = 0  # seconds until a progress report is taken; progress_held is set once one waits
        self.progress_held = threading.Event()
        self.prefs_open = threading.Event()  # cleared: a look at the display preferences waits until it is set again
        self.prefs_open.set()
        self.prefs_waiting = 0   # the looks waiting meanwhile
        # the library check: an administrator's account, what TheMovieDb would find, the posters to choose from
        self.admin = True
        self.remote_movies = []   # search results as Jellyfin's RemoteSearch gives them
        self.remote_images = {}   # item id → [{Url, ProviderName, Width, Height}]
        self.applied = []         # (item id, what was applied)
        self.latest_extra = {}  # library id → more ids "Latest" names, e.g. a new season instead of its show
        self.report_status = None  # answer playback reports with this error code instead
        self.stream_rate = 0      # bytes per second for /stream, 0 = as fast as possible
        self.stream_burst = 0     # bytes sent at once before stream_rate applies, as buffers and shapers let through
        self.stream_cut = 0       # mpv's next /stream breaks off after this many bytes, and the server goes away
        self.hls_path = None      # what a transcode delivers (a TS file)
        self.image = None         # (item id, image kind) → PNG instead of the plain colours, None for those
        self.images_down = False  # every picture answered with 500, as a server not reachable yet
        self.trickplay_tile = None  # a JPEG of the pictures for seeking, sent for every tile (with lib.trickplay)
        self.trickplay_requests = []
        self.transcodes = []      # query of each master.m3u8 request
        self.stopped_encodings = []
        self.stream_requests = 0
        self.connections = 0          # TCP connections accepted, to check that clients keep them open
        self.open_connections = set()
        self.idle_timeout = None      # close a kept-open connection after this many idle seconds
        self.keepalives = 0
        self.lock = threading.Lock()
        self.httpd = None
        self.port = None

    # ---------- Control from the tests ----------

    def start(self, port=0):
        mock = self

        class Handler(MockHandler):
            pass
        Handler.mock = mock
        Handler.timeout = self.idle_timeout
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port or self.port or 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        """Server gone, as if the network failed (open WebSockets are dropped)."""
        with self.lock:
            for q in self.ws_clients:
                q.append(None)
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.lock:  # kept-open connections go down with the server, too
            for c in list(self.open_connections):
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def push(self, message_type="UserDataChanged"):
        with self.lock:
            for q in self.ws_clients:
                q.append({"MessageType": message_type, "Data": {"UserId": USER_ID}})

    def change_played(self, iid, played):
        self.lib.set_played(iid, played)
        self.push()

    def reports_for(self, kind):
        return [b for p, b in self.reports if p.endswith(kind)]


class MockHandler(BaseHTTPRequestHandler):
    mock = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        with self.mock.lock:
            self.mock.connections += 1
            self.mock.open_connections.add(self.connection)

    def finish(self):
        with self.mock.lock:
            self.mock.open_connections.discard(self.connection)
        try:
            super().finish()
        except OSError:
            pass

    # ---------- Helpers ----------

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body, ctype):
        body = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_empty(self, code=204):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def authorized(self, query):
        h = self.headers.get("Authorization", "")
        if h:
            self.mock.auth_seen.append(h)
        t = self.mock.token
        return f'Token="{t}"' in h or query.get("ApiKey") == [t] or query.get("api_key") == [t]

    # ---------- HTTP ----------

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)
        m = self.mock
        lib = m.lib
        m.requests.append(self.path)

        if p == "/socket":
            return self.websocket()
        if p in m.busy_paths or m.busy_next.get(p, 0) > 0:
            if p in m.busy_next:
                m.busy_next[p] -= 1
            return self.send_json({"error": "starting"}, 503)
        if p.startswith("/remote/"):  # Matinee Remote's page, served from here: it asks Jellyfin at its own address
            name = p[len("/remote/"):] or "index.html"
            file = Path(__file__).resolve().parent.parent / "remote" / name
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
                     ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml", ".png": "image/png"}
            if "/" in name or file.suffix not in types or not file.is_file():
                return self.send_json({}, 404)
            return self.send_text(file.read_bytes(), types[file.suffix])
        if p == "/System/Info/Public":
            time.sleep(m.info_delay)
            if m.stand_in > 0:
                m.stand_in -= 1
                return self.send_json({"serverName": "Mock", "version": "12.4.0", "startupWizardCompleted": True})
            return self.send_json({"ServerName": "Mock", "Version": "12.2.0", "StartupWizardCompleted": not m.fresh})
        if p.startswith("/Startup/"):  # only while it is fresh, without a sign-in, as Jellyfin
            if not m.fresh:
                return self.send_json({"error": "done"}, 401)
            if p == "/Startup/User":
                return self.send_json({"Name": "root", "Password": ""})
            if p == "/Startup/Configuration":
                return self.send_json({"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"})
        mt = re.fullmatch(r"/Items/([^/]+)/RemoteSearch/Subtitles/([a-z]{3})", p)
        if mt:
            if not self.authorized(q):
                return self.send_json({"error": "unauthorized"}, 401)
            return self.send_json(m.remote_subs.get(mt.group(2), []))
        if p == "/Sessions":  # the devices connected over the WebSocket, as Jellyfin's sessions
            if not self.authorized(q):
                return self.send_json({"error": "unauthorized"}, 401)
            within = float(q.get("activeWithinSeconds", ["0"])[0] or 0)
            with m.lock:
                return self.send_json([
                    {"Id": f"session-{did}", "DeviceId": did, "DeviceName": x["name"], "Client": x["client"], "UserId": USER_ID,
                     "IsActive": did in m.ws_devices, "SupportsRemoteControl": True, **m.now_playing.get(did, {}),
                     "LastActivityDate": time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(x["last"]))}
                    for did, x in m.sessions.items() if not within or time.time() - x["last"] <= within])
        mt = re.fullmatch(r"/DisplayPreferences/([^/]+)", p)
        if mt:
            if not self.authorized(q):
                return self.send_json({"error": "unauthorized"}, 401)
            client = q.get("client", [""])[0]
            if not m.prefs_open.is_set():  # a slow server: the answer comes late
                with m.lock:
                    m.prefs_waiting += 1
                m.prefs_open.wait()
            # as Jellyfin: defaults for one never written
            return self.send_json(m.display_prefs.get((mt.group(1), client), {
                "Id": mt.group(1), "Client": client, "SortBy": "SortName", "SortOrder": "Ascending", "CustomPrefs": {}}))
        if "/Images/" in p and m.images_down:
            return self.send_json({"error": "down"}, 500)
        if "/Images/" in p:
            kind = p.split("/Images/")[1].split("/")[0]
            body = logo_png() if kind == "Logo" else png(64, 36, (60, 40, 90)) if kind in ("Backdrop", "Thumb") else png(40, 60, (40, 90, 120))
            if m.image:  # pictures of its own (make-screenshots)
                body = m.image(p.split("/")[2], kind) or body
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if not self.authorized(q):
            return self.send_json({"error": "unauthorized"}, 401)

        if p == "/UserViews" and m.views_delay:
            time.sleep(m.views_delay)
        if p in ("/System/Configuration", "/Library/VirtualFolders"):
            if not self.authorized(q):
                return self.send_json({"error": "unauthorized"}, 401)
            if p == "/System/Configuration":
                return self.send_json(m.system_conf)
            return self.send_json([{"Name": name, "CollectionType": kind, "Locations": paths} for name, kind, paths in m.folders])
        if p == "/Users/Me":
            return self.send_json({"Id": USER_ID, "Name": USERNAME, "Policy": {"IsAdministrator": m.admin}})
        mt = re.fullmatch(r"/Items/([^/]+)/RemoteImages", p)
        if mt:
            images = m.remote_images.get(mt.group(1), [])
            return self.send_json({"Images": images, "TotalRecordCount": len(images)})
        mt = re.fullmatch(r"/Items/([^/]+)/Ancestors", p)
        if mt and mt.group(1) in lib.items:
            parent = lib.items[mt.group(1)].get("ParentId")
            return self.send_json([{"Id": parent, "Type": "CollectionFolder"}, {"Id": "root", "Type": "UserRootFolder"}])
        if p == "/UserViews":
            return self.send_json({"Items": [
                {"Id": "lm", "Name": "Movies", "CollectionType": "movies", "ImageTags": {"Primary": "p"}},
                {"Id": "lt", "Name": "Shows", "CollectionType": "tvshows", "ImageTags": {"Primary": "p"}},
                {"Id": "lc", "Name": "Collections", "CollectionType": "boxsets", "ImageTags": {"Primary": "p"}},
            ]})
        if p == "/UserItems/Resume":
            ids = [i for i, ud in lib.userdata.items() if ud["PlaybackPositionTicks"] and not ud["Played"]]
            ids.sort(key=lambda i: lib.userdata[i]["LastPlayedDate"] or "", reverse=True)
            return self.send_json({"Items": [lib.view(i) for i in ids]})
        if p == "/Shows/NextUp":
            played = [i for i, e in enumerate(lib.episodes) if lib.userdata[e]["Played"]]
            nxt = []
            if played and played[-1] + 1 < len(lib.episodes):
                nxt = [lib.view(lib.episodes[played[-1] + 1])]
            elif not played and any(lib.userdata[e]["LastPlayedDate"] for e in lib.episodes):
                nxt = [lib.view(lib.episodes[0])]  # as Jellyfin: only looked into (no position kept), still "next"
            return self.send_json({"Items": nxt})
        if p == "/Items/Latest":
            parent = q.get("parentId", [""])[0]
            ids = [i for i, it in lib.items.items() if it.get("ParentId") == parent] + m.latest_extra.get(parent, [])
            return self.send_json([lib.view(i) for i in ids])
        if p == "/Items" and m.titles_slow and q.get("EnableImages") == ["false"] and "OriginalTitle" in q.get("Fields", [""])[0]:
            time.sleep(m.titles_slow)
        if p == "/Items":
            ids = lib.query(q)
            start = int(q.get("StartIndex", ["0"])[0])
            page = ids[start:start + int(q["Limit"][0])] if "Limit" in q else ids[start:]
            if m.items_delay and start > 0:
                time.sleep(m.items_delay)  # the rest of a big library is slow on Jellyfin
            return self.send_json({"Items": [lib.view(i) for i in page], "TotalRecordCount": len(ids)})
        if p == "/Genres":
            return self.send_json({"Items": [{"Id": g, "Name": n} for g, n in sorted(lib.genres.items(), key=lambda x: x[1])]})
        mt = re.fullmatch(r"/Items/([^/]+)/Similar", p)
        if mt and m.similar_delay:
            time.sleep(m.similar_delay)  # Jellyfin often takes a second or two for this one
        if mt:  # same genre, without the item itself
            gids = lib.items[mt.group(1)].get("GenreIds", [])
            ids = [i for i, it in lib.items.items() if i != mt.group(1) and set(gids) & set(it.get("GenreIds", []))]
            return self.send_json({"Items": [lib.view(i) for i in ids]})
        mt = re.fullmatch(r"/MediaSegments/([^/]+)", p)
        if mt:
            segs = lib.segments(mt.group(1))
            return self.send_json({"Items": segs, "TotalRecordCount": len(segs)})
        mt = re.fullmatch(r"/Items/([^/]+)", p)
        if mt and mt.group(1) in lib.people:
            return self.send_json(lib.people[mt.group(1)])
        if mt and mt.group(1) in lib.items:
            return self.send_json(lib.view(mt.group(1), full=True))
        missing = q.get("isMissing") != ["false"]
        if p == "/Shows/s1/Seasons":
            return self.send_json({"Items": [lib.view(s) for s in ("se0", "se1", "se2", "se3") if s in lib.items
                                             and (missing or lib.items[s].get("LocationType") != "Virtual")]})
        if p == "/Shows/s1/Episodes":
            eps = [e for e in lib.series_order if missing or lib.items[e].get("LocationType") != "Virtual"]
            if "startItemId" in q:
                eps = eps[eps.index(q["startItemId"][0]):]
            elif "seasonId" in q:
                season = lib.items[q["seasonId"][0]]
                eps = [e for e in eps if lib.items[e]["SeasonId"] == season["Id"]
                       or lib.items[e].get("AirsBeforeSeasonNumber") == season["IndexNumber"]]
            if "Limit" in q:
                eps = eps[:int(q["Limit"][0])]
            return self.send_json({"Items": [lib.view(e) for e in eps]})
        if re.fullmatch(r"/Videos/[^/]+/stream", p):
            m.stream_requests += 1
            with open(m.video_path, "rb") as f:
                data = f.read()
            rng = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            if rng:  # range requests like Jellyfin, otherwise mpv cannot seek in the stream
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else len(data) - 1
                part = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{start + len(part) - 1}/{len(data)}")
            else:
                part = data
                self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(part)))
            self.end_headers()
            try:
                if m.stream_cut and not self.headers.get("User-Agent", "").startswith("Python"):  # mpv, not Matinee
                    cut, m.stream_cut = m.stream_cut, 0
                    self.wfile.write(part[:cut])
                    self.wfile.flush()
                    # gone in the middle of the playback, as a server is: after the start was reported (mpv reads
                    # a small file's half faster than Matinee reports the start when the machine is busy)
                    end = time.monotonic() + 10
                    while not m.reports_for("/Playing") and time.monotonic() < end:
                        time.sleep(0.05)
                    m.stop()
                    return
                if not m.stream_rate:
                    self.wfile.write(part)
                burst = m.stream_burst if m.stream_rate else 0
                if burst:
                    self.wfile.write(part[:burst])
                for i in range(burst, len(part) if m.stream_rate else 0, 4096):  # a slow line
                    self.wfile.write(part[i:i + 4096])
                    time.sleep(4096 / m.stream_rate)
            except OSError:
                pass  # mpv aborts requests when seeking
            return
        # Transcoding as HLS: one master playlist, one media playlist, a single segment
        if re.fullmatch(r"/Videos/[^/]+/Trickplay/320/\d+\.jpg", p) and m.trickplay_tile:
            m.trickplay_requests.append(p)
            return self.send_text(m.trickplay_tile, "image/jpeg")
        if re.fullmatch(r"/Videos/[^/]+/master\.m3u8", p):
            m.transcodes.append({k: v[0] for k, v in q.items()})
            return self.send_text("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\n" + f"main.m3u8?{u.query}\n",
                                  "application/vnd.apple.mpegurl")
        if re.fullmatch(r"/Videos/[^/]+/main\.m3u8", p):
            return self.send_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:30\n#EXT-X-PLAYLIST-TYPE:VOD\n"
                                  f"#EXTINF:{lib.runtime_s},\nhls1/main/0.ts?{u.query}\n#EXT-X-ENDLIST\n",
                                  "application/vnd.apple.mpegurl")
        if re.fullmatch(r"/Videos/[^/]+/hls1/main/0\.ts", p):
            with open(m.hls_path, "rb") as f:
                return self.send_text(f.read(), "video/mp2t")
        if re.fullmatch(r"/Videos/[^/]+/[^/]+/Subtitles/\d+/Stream\.srt", p):
            time.sleep(m.subtitle_delay)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(m.subtitle)))
            self.end_headers()
            return self.wfile.write(m.subtitle)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)
        m = self.mock
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw) if raw else {}
        m.requests.append(self.path)
        if p in m.busy_paths:
            return self.send_json({"error": "starting"}, 503)
        device = (re.search(r'DeviceId="([^"]*)"', self.headers.get("Authorization", "")) or [None, None])[1]
        if p == "/Users/AuthenticateByName":
            if (body.get("Username") == USERNAME and body.get("Pw") == PASSWORD) or \
                    (body.get("Username") in m.accounts and m.accounts[body["Username"]] == body.get("Pw")):
                m.logins.append(device)
                return self.send_json({"AccessToken": m.token, "User": {"Id": m.user_ids.get(body.get("Username"), USER_ID),
                                                                        "Name": body.get("Username"), "Policy": {}}})
            return self.send_json({"error": "bad login"}, 401)
        if p.startswith("/Startup/"):
            if not m.fresh:
                return self.send_json({"error": "done"}, 401)
            m.startup.append((p, body))
            if p == "/Startup/User":  # the first user: an administrator, signed in with that from then on
                m.accounts[body["Name"]] = body.get("Password", "")
            if p == "/Startup/Complete":
                m.fresh = False
            return self.send_empty()
        if not self.authorized(q):
            return self.send_json({"error": "unauthorized"}, 401)
        if p == "/Library/VirtualFolders":
            m.folders.append((q.get("name", [""])[0], q.get("collectionType", [""])[0], q.get("paths", [])))
            m.folder_options.append(body.get("LibraryOptions", {}))
            return self.send_empty()
        if p == "/System/Configuration":
            m.system_conf = body
            return self.send_empty()
        if p in ("/Users", "/Users/Password"):  # as Jellyfin 10.9 and later: the user by ?userId=
            uid = q.get("userId", [""])[0]
            names = [n for n in m.accounts if m.user_ids.get(n, USER_ID) == uid]
            if not names:
                return self.send_json({"error": "no such user"}, 404)
            if p == "/Users":
                m.accounts[body["Name"]] = m.accounts.pop(names[0])
                m.user_changes.append(("name", uid, body["Name"]))
            else:
                if m.accounts[names[0]] != body.get("CurrentPw"):
                    return self.send_json({"error": "wrong password"}, 403)
                m.accounts[names[0]] = body["NewPw"]
                m.user_changes.append(("password", uid, body["NewPw"]))
            return self.send_empty()
        if p == "/Library/Refresh":
            m.refreshes += 1
            return self.send_empty()
        if p == "/Sessions/Logout":
            m.logouts.append(device)
            return self.send_empty()
        if p == "/Sessions/Capabilities/Full":
            return self.send_empty()
        mt = re.fullmatch(r"/Sessions/session-([^/]+)/(Command|Playing|Playing/\w+)", p)
        if mt:  # Jellyfin's remote control: on to that session's WebSocket, as a GeneralCommand, Play or Playstate
            did, what = mt.group(1), mt.group(2)
            if what == "Command":
                kind, data = "GeneralCommand", {"Name": body.get("Name"), "Arguments": body.get("Arguments") or {}, "ControllingUserId": USER_ID}
            elif what == "Playing":
                start = q.get("startPositionTicks", [None])[0]
                kind, data = "Play", {"ItemIds": q.get("itemIds", [""])[0].split(","), "PlayCommand": q.get("playCommand", [""])[0],
                                      **({"StartPositionTicks": int(start)} if start is not None else {})}
            else:
                seek = q.get("seekPositionTicks", [None])[0]
                kind, data = "Playstate", {"Command": what.split("/")[1], **({"SeekPositionTicks": int(seek)} if seek is not None else {})}
            with m.lock:
                target = m.ws_devices.get(did)
                m.remote_sent.append((did, kind, data))
                if target:
                    target[0].append({"MessageType": kind, "Data": data})
            return self.send_empty() if target else self.send_json({"error": "no such session"}, 404)
        mt = re.fullmatch(r"/Sessions/session-([^/]+)/Message", p)
        if mt:  # as Jellyfin: a GeneralCommand "DisplayMessage" to that session's WebSocket
            with m.lock:
                target = m.ws_devices.get(mt.group(1))
                m.messages.append((mt.group(1), body.get("Header"), body.get("Text")))
                if target and mt.group(1) not in m.deaf:
                    target[0].append({"MessageType": "GeneralCommand", "Data": {"Name": "DisplayMessage", "ControllingUserId": "0" * 32,  # as Jellyfin 12: no user named
                                      "Arguments": {"Header": body.get("Header"), "Text": body.get("Text"), "TimeoutMs": "1000"}}})
            return self.send_empty() if target else self.send_json({"error": "no such session"}, 404)
        mt = re.fullmatch(r"/Items/([^/]+)/RemoteSearch/Subtitles/([^/]+)", p)
        if mt:
            m.subs_downloaded.append((mt.group(1), urllib.parse.unquote(mt.group(2))))
            m.lib.subtitled.add(mt.group(1))  # kept with the video: among its subtitle files from now on
            return self.send_empty()
        mt = re.fullmatch(r"/DisplayPreferences/([^/]+)", p)
        if mt:
            if not isinstance(body.get("CustomPrefs"), dict) or not all(isinstance(v, str) for v in body["CustomPrefs"].values()):
                return self.send_json({"error": "CustomPrefs: strings only"}, 400)
            m.display_prefs[(mt.group(1), q.get("client", [""])[0])] = body
            return self.send_empty()
        if p.startswith("/Sessions/Playing"):
            if m.report_status:
                return self.send_json({"error": "rejected"}, m.report_status)
            if p.endswith("Stopped") and m.stopped_delay:  # a slow server: the answer, and taking it, come late
                time.sleep(m.stopped_delay)
            if p.endswith("Progress") and m.progress_delay:
                delay = m.progress_delay
                m.progress_held.set()
                time.sleep(delay)
            m.reports.append((p, body))
            iid = body.get("ItemId")
            if iid in m.lib.userdata:
                ud = m.lib.userdata[iid]
                pos = int(body.get("PositionTicks") or 0)
                ud["LastPlayedDate"] = now_iso()
                if p.endswith("Stopped") and pos >= 0.9 * m.lib.items[iid]["RunTimeTicks"]:
                    ud.update(Played=True, PlaybackPositionTicks=0, PlayCount=ud["PlayCount"] + 1)
                elif p.endswith("Stopped") and pos < m.min_resume * m.lib.items[iid]["RunTimeTicks"]:
                    ud["PlaybackPositionTicks"] = 0  # as Jellyfin: no position kept so early (MinResumePct)
                elif not p.endswith("/Playing"):
                    ud["PlaybackPositionTicks"] = pos
                m.push()
            return self.send_empty()
        mt = re.fullmatch(r"/UserItems/([^/]+)/UserData", p)
        if mt and mt.group(1) in m.lib.userdata:  # e.g. "Remove from Continue Watching": the position back to 0
            if "PlaybackPositionTicks" in body:
                m.lib.userdata[mt.group(1)]["PlaybackPositionTicks"] = int(body["PlaybackPositionTicks"])
            m.push()
            return self.send_json({})
        if p == "/Items/RemoteSearch/Movie":
            info = body.get("SearchInfo") or {}
            ids = info.get("ProviderIds") or {}
            if ids:
                found = [r for r in m.remote_movies if any(r["ProviderIds"].get(k) == v for k, v in ids.items())]
            else:
                found = [r for r in m.remote_movies if info.get("Name", "").lower() in r["Name"].lower()]
            return self.send_json(found)
        mt = re.fullmatch(r"/Items/RemoteSearch/Apply/([^/]+)", p)
        if mt and mt.group(1) in m.lib.items:
            it = m.lib.items[mt.group(1)]
            it.update(Name=body["Name"], ProductionYear=body.get("ProductionYear"), ProviderIds=dict(body["ProviderIds"]),
                      PrimaryImageAspectRatio=2 / 3)
            m.applied.append((mt.group(1), body["Name"], q.get("replaceAllImages")))
            m.push()
            return self.send_empty()
        mt = re.fullmatch(r"/Items/([^/]+)/RemoteImages/Download", p)
        if mt and mt.group(1) in m.lib.items:
            m.lib.items[mt.group(1)]["PrimaryImageAspectRatio"] = 2 / 3
            m.applied.append((mt.group(1), q.get("imageUrl", [""])[0], "poster"))
            return self.send_empty()
        mt = re.fullmatch(r"/UserPlayedItems/([^/]+)", p)
        if mt:
            m.change_played(mt.group(1), True)
            return self.send_json({})
        mt = re.fullmatch(r"/UserFavoriteItems/([^/]+)", p)
        if mt:
            m.lib.favorites.add(mt.group(1))
            m.push()
            return self.send_json({"IsFavorite": True})
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        u = urllib.parse.urlsplit(self.path)
        if not self.authorized(urllib.parse.parse_qs(u.query)):
            return self.send_json({"error": "unauthorized"}, 401)
        mt = re.fullmatch(r"/UserPlayedItems/([^/]+)", u.path)
        if mt:
            self.mock.change_played(mt.group(1), False)
            return self.send_json({})
        mt = re.fullmatch(r"/UserFavoriteItems/([^/]+)", u.path)
        if mt:
            self.mock.lib.favorites.discard(mt.group(1))
            self.mock.push()
            return self.send_json({"IsFavorite": False})
        if u.path == "/Videos/ActiveEncodings":
            self.mock.stopped_encodings.append(urllib.parse.parse_qs(u.query).get("playSessionId", [""])[0])
            return self.send_empty()
        self.send_json({"error": "not found"}, 404)

    # ---------- WebSocket ----------

    def websocket(self):
        m = self.mock
        h = self.headers.get("Authorization", "")
        if f'Token="{self.mock.token}"' not in h:  # like Jellyfin 12: api_key in the URL is not enough
            m.ws_rejected += 1
            return self.send_json({"error": "forbidden"}, 403)
        accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        out = []
        did = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("deviceId", [""])[0]
        name = (re.search(r'Device="([^"]*)"', h) or [None, ""])[1]
        client = (re.search(r'Client="([^"]*)"', h) or [None, ""])[1]
        with m.lock:
            m.ws_clients.append(out)
            m.ws_connects += 1
            if did:
                m.ws_devices[did] = (out, name, client)
                m.sessions[did] = {"name": name, "client": client, "last": time.time()}
        conn = self.connection
        conn.settimeout(0.1)
        buf = b""

        def send(obj):
            data = json.dumps(obj).encode()
            n = len(data)
            head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + n.to_bytes(2, "big"))
            conn.sendall(head + data)

        try:
            send({"MessageType": "ForceKeepAlive", "Data": 60})
            while True:
                while out:
                    msg = out.pop(0)
                    if msg is None:
                        return
                    send(msg)
                try:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                except (socket.timeout, TimeoutError):
                    pass
                while len(buf) >= 6:  # client frames are masked
                    n, pos = buf[1] & 0x7F, 2
                    if n == 126:
                        n, pos = int.from_bytes(buf[2:4], "big"), 4
                    if len(buf) < pos + 4 + n:
                        break
                    mask, data = buf[pos:pos + 4], buf[pos + 4:pos + 4 + n]
                    buf = buf[pos + 4 + n:]
                    text = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                    if b"KeepAlive" in text:
                        m.keepalives += 1
        except OSError:
            pass
        finally:
            with m.lock:
                if out in m.ws_clients:
                    m.ws_clients.remove(out)
                if did and m.ws_devices.get(did, (None,))[0] is out:
                    del m.ws_devices[did]
            self.close_connection = True
