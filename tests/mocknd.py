"""Fake Navidrome (Subsonic API) for the tests.

Checks the signature like Navidrome (t = md5(password + s)), serves a small library and a short audio file
for every song, and records scrobbles and streamed songs.
"""
import hashlib
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mockjf import png

ND_USER = "listener"
ND_PASSWORD = "tunes"
SONG_SECONDS = 2


def song(i, album, artist):
    return {"id": f"s{i}", "title": f"Song {i}", "album": album["name"], "albumId": album["id"],
            "artist": artist["name"], "artistId": artist["id"], "coverArt": album["id"], "duration": SONG_SECONDS,
            "track": i % 10, "isDir": False}


class MockNavidrome:
    def __init__(self, audio_path=None):
        self.audio_path = audio_path
        self.accounts = {}    # another person's user name → password
        self.scrobbles = []   # (song id, submission) in the order they came
        self.streamed = []    # song ids mpv fetched
        self.requests = []    # every method called
        self.no_picture = set()  # cover ids without a picture of their own: Navidrome's placeholder
        self.stream_cut = 0   # the next song breaks off at this place of its file, and the server goes away
        self.port = 0
        artists = [{"id": "ar1", "name": "Alpha Band"}, {"id": "ar2", "name": "Beta Singer"}]
        self.albums = [{"id": "al1", "name": "First Album", "artist": "Alpha Band", "artistId": "ar1", "year": 2020},
                       {"id": "al2", "name": "Second Album", "artist": "Alpha Band", "artistId": "ar1", "year": 2022},
                       {"id": "al3", "name": "Solo", "artist": "Beta Singer", "artistId": "ar2", "year": 2024}]
        self.artists = artists
        by_id = {a["id"]: a for a in artists}
        self.songs = {}
        n = 0
        for al in self.albums:
            al["coverArt"] = al["id"]
            al["songCount"] = 3
            al["duration"] = 3 * SONG_SECONDS
            al["song"] = []
            for _ in range(3):
                n += 1
                s = song(n, al, by_id[al["artistId"]])
                al["song"].append(s)
                self.songs[s["id"]] = s
        self.playlists = [{"id": "pl1", "name": "Road Trip", "songCount": 2, "duration": 2 * SONG_SECONDS,
                           "coverArt": "pl1", "entry": [self.songs["s2"], self.songs["s7"]]}]
        self.starred = set()  # ids of starred songs, albums and artists
        self.stations = [{"id": "st1", "name": "Mock FM", "streamUrl": "", "homePageUrl": "https://example.com"}]
        self.lyrics = {"s1": {"synced": True, "line": [{"start": 0, "value": "First line"}, {"start": 1000, "value": "Second line"}]},
                       "s2": {"synced": False, "line": [{"value": "Plain words"}, {"value": "More words"}]}}

    def start(self):
        mock = self

        class Handler(MockNdHandler):
            m = mock

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)  # back at the same address
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.stations[0]["streamUrl"] = f"{self.url}/radio.ogg"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class MockNdHandler(BaseHTTPRequestHandler):
    m: MockNavidrome

    def log_message(self, *args):
        pass

    def reply(self, body=None, error=None):
        r = {"status": "failed" if error else "ok", "version": "1.16.1", "type": "navidrome",
             "serverVersion": "0.64.2", "openSubsonic": True}
        if error:
            r["error"] = {"code": error[0], "message": error[1]}
        r.update(body or {})
        data = json.dumps({"subsonic-response": r}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        m = self.m
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        method = u.path.removeprefix("/rest/").removesuffix(".view")
        m.requests.append(method)
        if u.path == "/radio.ogg":  # a station: no signature, like a real one
            return self.audio()
        known = {ND_USER: ND_PASSWORD, **m.accounts}
        if q.get("u") not in known or q.get("t") != hashlib.md5((known[q["u"]] + q.get("s", "")).encode()).hexdigest():
            return self.reply(error=(40, "Wrong username or password"))
        if method == "ping":
            return self.reply()
        if method == "getAlbumList2":
            albums = [{k: v for k, v in a.items() if k != "song"} for a in m.albums]
            if q.get("type") == "newest":
                albums.reverse()
            start = int(q.get("offset", 0))
            return self.reply({"albumList2": {"album": albums[start:start + int(q.get("size", 10))]}})
        if method == "getAlbum":
            al = next((a for a in m.albums if a["id"] == q.get("id")), None)
            if not al:
                return self.reply(error=(70, "not found"))
            star = lambda x: {**x, "starred": "2026-10-23T08:00:00Z"} if x["id"] in m.starred else x
            return self.reply({"album": {**star(al), "song": [star(x) for x in al["song"]]}})
        if method == "getArtists":
            return self.reply({"artists": {"index": [{"name": a["name"][0], "artist": [
                {**a, "albumCount": sum(al["artistId"] == a["id"] for al in m.albums)}]} for a in m.artists]}})
        if method == "getArtist":
            a = next((a for a in m.artists if a["id"] == q.get("id")), None)
            albums = [{k: v for k, v in al.items() if k != "song"} for al in m.albums if a and al["artistId"] == a["id"]]
            return self.reply({"artist": {**a, "album": albums}}) if a else self.reply(error=(70, "not found"))
        if method == "getPlaylists":
            return self.reply({"playlists": {"playlist": [{k: v for k, v in p.items() if k != "entry"} for p in m.playlists]}})
        if method == "getPlaylist":
            p = next((p for p in m.playlists if p["id"] == q.get("id")), None)
            return self.reply({"playlist": p}) if p else self.reply(error=(70, "not found"))
        if method == "search3":
            term = q.get("query", "").lower()
            return self.reply({"searchResult3": {
                "artist": [a for a in m.artists if term in a["name"].lower()],
                "album": [{k: v for k, v in a.items() if k != "song"} for a in m.albums if term in a["name"].lower()],
                "song": [s for s in m.songs.values() if term in s["title"].lower()]}})
        if method == "getRandomSongs":
            return self.reply({"randomSongs": {"song": list(m.songs.values())[:int(q.get("size", 10))]}})
        if method in ("star", "unstar"):
            ids = {q.get(k) for k in ("id", "albumId", "artistId")} - {None}
            (m.starred.update if method == "star" else m.starred.difference_update)(ids)
            return self.reply()
        if method == "getStarred2":
            mark = lambda x: {**x, "starred": "2026-10-23T08:00:00Z"}
            return self.reply({"starred2": {
                "artist": [mark(a) for a in m.artists if a["id"] in m.starred],
                "album": [mark({k: v for k, v in a.items() if k != "song"}) for a in m.albums if a["id"] in m.starred],
                "song": [mark(x) for x in m.songs.values() if x["id"] in m.starred]}})
        if method == "getInternetRadioStations":
            return self.reply({"internetRadioStations": {"internetRadioStation": m.stations}})
        if method == "getLyricsBySongId":
            lyr = m.lyrics.get(q.get("id"))
            return self.reply({"lyricsList": {"structuredLyrics": [lyr] if lyr else []}})
        if method == "scrobble":
            m.scrobbles.append((q.get("id"), q.get("submission") == "true"))
            return self.reply()
        if method == "getCoverArt":
            data = png(4, 4, (200, 60, 60))
            kind = "image/png"
            if q.get("id") in m.no_picture:  # as Navidrome: its placeholder, a small WebP of about half a kilobyte
                data, kind = b"RIFF\x1e\x02\x00\x00WEBPVP8 " + bytes(530), "image/webp"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if method == "stream":
            m.streamed.append(q.get("id"))
            return self.audio()
        self.reply(error=(0, f"unknown method {method}"))

    def audio(self):
        m = self.m
        data = m.audio_path.read_bytes()
        rng = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        start = int(rng.group(1)) if rng else 0  # range requests like Navidrome, so mpv can start in the middle
        part = data[start:int(rng.group(2)) + 1 if rng and rng.group(2) else len(data)]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "audio/ogg")
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{start + len(part) - 1}/{len(data)}")
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        try:
            cut = m.stream_cut
            if cut and start < cut:
                # as a server going away in the middle of a song: what comes before that place comes at a pace (not
                # all at once: mpv reads the file's start again while it opens it, as a real server lets it), then
                # the connection breaks off and the server is gone (mpv 0.42 tries again at once, in vain)
                end = min(cut, start + len(part))
                for at in range(start, end, 16384):
                    self.wfile.write(data[at:min(at + 16384, end)])
                    self.wfile.flush()
                    time.sleep(0.05)
                if m.stream_cut:  # the first to get there takes the server away
                    m.stream_cut = 0
                    m.stop()
                return
            self.wfile.write(part)
        except OSError:
            pass
