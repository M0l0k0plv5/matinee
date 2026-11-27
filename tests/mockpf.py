"""Fake Podfetch for the tests.

Answers like Podfetch's own API (/api/v1): with basic auth (a user name and password) or, made with auth=False, without
any. Its files carry the user's API key (?apiKey=), and it builds every address from the request's Host header with
http://, as Podfetch does behind a proxy that does not say otherwise. Two podcasts, three episodes; it records where
Matinee says an episode is.
"""
import base64
import json
import time
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mockjf import png

PF_USER, PF_PASSWORD, PF_API_KEY = "listener", "pf-password", "pf-api-key"
EPISODE_SECONDS = 4


def podcast(pid, name, author, keywords=None):
    return {"id": pid, "legacyId": None, "name": name, "directory_id": pid, "directory_name": pid,
            "podfetch_feed": "", "rssfeed": f"https://feeds.example/{pid}.xml", "image_url": f"podcasts/{pid}/image.png",
            "summary": f"All about {name}.", "language": "en", "explicit": "no", "keywords": keywords, "last_build_date": None,
            "author": author, "active": True, "original_image_url": f"https://feeds.example/{pid}.png", "favorites": False,
            "tags": []}


def episode(uid, eid, pid, name, date, seconds=EPISODE_SECONDS):
    return {"id": uid, "legacyId": None, "podcast_id": pid, "episode_id": eid, "name": name,
            "url": f"https://feeds.example/{eid}.mp3", "date_of_recording": f"{date}T06:00:00",
            "image_url": "", "total_time": seconds, "local_url": f"podcasts/{pid}/{eid}.ogg", "local_image_url": "",
            "description": f"<p>What <b>{name}</b> is about.</p>", "status": True, "download_time": None,
            "guid": f"guid-{eid}", "deleted": False, "episode_numbering_processed": True, "favored": None}


class MockPodfetch:
    def __init__(self, audio_path=None, auth=True):
        self.audio_path = audio_path
        self.auth = auth
        self.accounts = {}    # another person's user name → password
        self.refused = set()  # user names refused from now on (their password changed on the server)
        self.podcasts = [podcast("p1", "Zeitfragen", "Radio One", "News,Society & Culture"),
                         podcast("p2", "Another Show", "Someone", "Technology, News,news")]
        self.episodes = [episode("u1", "e-11111111", "p1", "Episode 2: Later", "2026-10-20"),
                         episode("u2", "e-22222222", "p1", "Episode 1: First", "2026-10-01"),
                         episode("u3", "e-33333333", "p2", "Pilot", "2026-10-10")]
        self.heard = {}      # episode id → seconds, as Matinee reported it last
        self.reports = []    # (episode id, seconds) in the order they came
        self.streamed = []   # episode files fetched
        self.requests = []   # (method, path)
        self.deleted = []    # (podcast id, whether its files went too)
        # The podcast directory, as Podfetch's search finds it: one already followed (its feed), one not
        self.catalog = [{"collectionId": 111, "trackId": 111, "collectionName": "Zeitfragen", "artistName": "Radio One",
                         "feedUrl": "https://feeds.example/p1.xml", "artworkUrl600": "https://art.example/111.jpg",
                         "primaryGenreName": "News", "trackCount": 2},
                        {"collectionId": 222, "trackId": 222, "collectionName": "Zeitgeist Weekly", "artistName": "New Voices",
                         "feedUrl": "https://feeds.example/p3.xml", "artworkUrl600": "http://insecure.example/222.jpg",
                         "primaryGenreName": "Society & Culture", "trackCount": 9}]
        self.followed = []   # directory ids Matinee followed
        self.slow = 0        # seconds the list of podcasts takes, as a slow server's
        self.port = 0

    def start(self):
        mock = self

        class Handler(MockPfHandler):
            m = mock

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def history(self, e):
        if e["episode_id"] not in self.heard:
            return None
        return {"podcast": e["podcast_id"], "episode": e["episode_id"], "timestamp": "2026-11-01T20:00:00",
                "guid": e["guid"], "action": "play", "started": 0, "position": self.heard[e["episode_id"]],
                "total": e["total_time"], "device": "webview"}


class MockPfHandler(BaseHTTPRequestHandler):
    m: MockPodfetch

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except ConnectionError:
            pass

    def reply(self, body=None, status=200, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body if body is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def signed_in(self):
        known = {u: p for u, p in {PF_USER: PF_PASSWORD, **self.m.accounts}.items() if u not in self.m.refused}
        return not self.m.auth or self.headers.get("Authorization") in {
            "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode() for u, p in known.items()}

    def address(self, path):
        """As Podfetch builds them: from the Host header, with http://, with the API key on its files."""
        url = f"http://{self.headers.get('Host')}/{path}"
        return url + (f"?apiKey={PF_API_KEY}" if self.m.auth else "")

    def dto(self, e):
        return {**e, "local_url": self.address(e["local_url"])}

    def pod(self, p):
        return {**p, "image_url": self.address(p["image_url"])}

    def route(self, method):
        m = self.m
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path
        m.requests.append((method, p))
        if p == "/api/v1/podcasts" and m.slow:
            time.sleep(m.slow)
        if p.startswith("/podcasts/"):  # its files: with the API key where there is a sign-in
            if m.auth and q.get("apiKey") != PF_API_KEY:
                return self.reply(b"Forbidden", 403, "text/plain")
            if p.endswith("/image.png"):
                return self.reply(png(4, 4, (90, 60, 200)), ctype="image/png")
            if p.endswith(".ogg") and m.audio_path:
                m.streamed.append(p.rsplit("/", 1)[1][:-4])
                return self.audio()
            return self.reply(b"Not Found", 404, "text/plain")
        if not p.startswith("/api/v1/"):
            return self.reply(b"Not Found", 404, "text/plain")
        if p == "/api/v1/sys/config":  # as Podfetch answers anyone (Sign In Everywhere looks for it)
            return self.reply({"podindexConfigured": False, "serverUrl": m.url + "/"})
        if not self.signed_in():
            return self.reply(b"Forbidden", 403, "text/plain")
        pods = {x["id"]: x for x in m.podcasts}
        eps = {x["episode_id"]: x for x in m.episodes}
        if method == "GET" and p == "/api/v1/podcasts":
            return self.reply([self.pod(x) for x in m.podcasts])
        if method == "GET" and p == "/api/v1/podcasts/episode/lastwatched":
            begun = [eps[i] for i in reversed(m.heard) if i in eps]  # the last heard first, as Podfetch's
            return self.reply([{"podcastEpisode": self.dto(e), "podcast": self.pod(pods[e["podcast_id"]]),
                                "episode": m.history(e)} for e in begun])
        if method == "GET" and p == "/api/v1/podcasts/timeline":
            if q.get("notListened") is None or q.get("favoredEpisodes") is None:  # Podfetch needs both
                return self.reply(b"Bad Request", 400, "text/plain")
            items = sorted(m.episodes, key=lambda e: e["date_of_recording"], reverse=True)
            return self.reply({"data": [{"podcast_episode": self.dto(e), "podcast": self.pod(pods[e["podcast_id"]]),
                                         "history": m.history(e), "favorite": None} for e in items],
                               "totalElements": len(items)})
        if method == "POST" and p == "/api/v1/podcasts/episode":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if body.get("podcastEpisodeId") not in eps or not isinstance(body.get("time"), int):
                return self.reply(b"Not Found", 404, "text/plain")
            m.heard.pop(body["podcastEpisodeId"], None)  # now the last heard
            m.heard[body["podcastEpisodeId"]] = body["time"]
            m.reports.append((body["podcastEpisodeId"], body["time"]))
            return self.reply(b"", 200, "text/plain")
        found = re.fullmatch(r"/api/v1/podcasts/0/([^/]+)/search", p)
        if method == "GET" and found:  # the directory, as Podfetch passes its answer on
            term = urllib.parse.unquote(found.group(1)).lower()
            hits = [r for r in m.catalog if term in r["collectionName"].lower()]
            return self.reply({"resultCount": len(hits), "results": hits})
        if method == "POST" and p == "/api/v1/podcasts/itunes":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            r = next((r for r in m.catalog if r["trackId"] == body.get("trackId")), None)
            if not r or not isinstance(body.get("userId"), int):
                return self.reply(b"Internal Server Error", 500, "text/plain")  # Podfetch unwraps the directory's lookup
            pid = f"p{len(m.podcasts) + 10}"
            m.podcasts.append({**podcast(pid, r["collectionName"], r["artistName"], r["primaryGenreName"]), "rssfeed": r["feedUrl"]})
            m.followed.append(r["trackId"])
            return self.reply(self.pod(m.podcasts[-1]))
        found = re.fullmatch(r"/api/v1/podcasts/([\w-]+)", p)
        if method == "DELETE" and found:  # as Podfetch: the body must say delete_files
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
            if not isinstance(body, dict) or not isinstance(body.get("delete_files"), bool):
                return self.reply(b"Unprocessable Entity", 422, "text/plain")
            if found.group(1) not in pods:
                return self.reply(b"Not Found", 404, "text/plain")
            m.podcasts = [x for x in m.podcasts if x["id"] != found.group(1)]
            m.episodes = [x for x in m.episodes if x["podcast_id"] != found.group(1)]
            m.deleted.append((found.group(1), body["delete_files"]))
            return self.reply(b"", 200, "text/plain")
        found = re.fullmatch(r"/api/v1/podcasts/([\w-]+)(/episodes)?", p)
        if method == "GET" and found:
            pod = pods.get(found.group(1))
            if not pod:
                return self.reply(b"Not Found", 404, "text/plain")
            if not found.group(2):
                return self.reply(self.pod(pod))
            mine = sorted((e for e in m.episodes if e["podcast_id"] == pod["id"]), key=lambda e: e["date_of_recording"], reverse=True)
            return self.reply([{"podcastEpisode": self.dto(e), "podcastHistoryItem": m.history(e)} for e in mine])
        found = re.fullmatch(r"/api/v1/episodes/([\w-]+)", p)
        if method == "GET" and found:
            e = eps.get(found.group(1))
            if not e:
                return self.reply(b"Not Found", 404, "text/plain")
            return self.reply({"podcastEpisode": self.dto(e), "podcastHistoryItem": m.history(e)})
        found = re.fullmatch(r"/api/v1/podcasts/episodes/([\w-]+)/chapters", p)
        if method == "GET" and found:
            return self.reply([{"id": "c1", "startTime": 0, "endTime": 2, "title": "Intro"},
                               {"id": "c2", "startTime": 2, "endTime": EPISODE_SECONDS, "title": "Main"}]
                              if found.group(1) == "u1" else [])
        self.reply(b"Not Found", 404, "text/plain")

    def audio(self):
        data = self.m.audio_path.read_bytes()
        rng = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if not rng:
            return self.reply(data, ctype="audio/ogg")
        start = int(rng.group(1))
        part = data[start:int(rng.group(2)) + 1 if rng.group(2) else len(data)]
        self.send_response(206)
        self.send_header("Content-Type", "audio/ogg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{start + len(part) - 1}/{len(data)}")
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        try:
            self.wfile.write(part)
        except OSError:
            pass

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")
