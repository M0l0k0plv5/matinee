"""Fake Audiobookshelf for the tests.

Checks the API key like Audiobookshelf (a bearer header, or ?token= on the audio files), serves one library with
three books (one of them in two files, with two chapters), opens listening sessions and records what Matinee tells it.
"""
import json
import re
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mockjf import png

ABS_KEY = "abs-api-key"
TRACK_SECONDS = 4


def book(item_id, title, files, added, seconds=TRACK_SECONDS):
    """A library item as Audiobookshelf lists it: files is how many audio files it has, one chapter each."""
    chapters = [{"id": i, "start": i * seconds, "end": (i + 1) * seconds, "title": f"Part {i + 1}"}
                for i in range(files)]
    return {"id": item_id, "libraryId": "lib1", "mediaType": "book", "addedAt": added,
            "media": {"metadata": {"title": title, "authorName": "The Author", "narratorName": "A Reader",
                                   "publishedYear": "1999", "description": "<p>What it is <b>about</b>.</p>"},
                      "duration": files * seconds, "numChapters": files, "coverPath": f"/books/{item_id}/cover.jpg", "chapters": chapters,
                      "tracks": [{"index": i + 1, "ino": f"{item_id}-{i}", "startOffset": i * seconds,
                                  "duration": seconds} for i in range(files)]}}


class MockAudiobookshelf:
    def __init__(self, audio_path=None):
        self.audio_path = audio_path
        self.books = [book("bk2", "Folge 2: The Second", 1, 3), book("bk10", "Folge 10: The Tenth", 2, 1),
                      book("bk1", "Folge 1: The First", 1, 2), {**book("bk3", "Folge 3: Gone", 1, 4), "isMissing": True}]
        self.progress = {}    # item id → {"currentTime", "duration", "progress", "isFinished"}
        self.accounts = {}    # another person's user name → password: their token "token-<name>" from /login
        self.sessions = {}    # session id → item id
        self.reports = []     # (what, session id, body): sync and close, in the order they came
        self.patches = []     # (item id, body)
        self.hidden = []      # items taken off "Continue Listening"
        self.streamed = []    # files mpv fetched (their ino)
        self.requests = []    # (method, path)
        self.stream_cut = 0   # the next file breaks off after this many bytes, and the server goes away
        self.port = 0

    def start(self):
        mock = self

        class Handler(MockAbsHandler):
            m = mock

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)  # back at the same address
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def item(self, item_id):
        return next((b for b in self.books if b["id"] == item_id), None)

    def begun(self):
        return [b for b in self.books if self.progress.get(b["id"], {}).get("currentTime", 0) > 0
                and not self.progress[b["id"]].get("isFinished")]


class MockAbsHandler(BaseHTTPRequestHandler):
    m: MockAudiobookshelf

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except ConnectionError:  # mpv lets go of a file it has read enough of
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

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def authorized(self, q):
        keys = {ABS_KEY, *(f"token-{name}" for name in self.m.accounts)}
        return self.headers.get("Authorization", "").removeprefix("Bearer ") in keys or q.get("token") in keys

    def route(self, method):
        m = self.m
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path
        m.requests.append((method, p))
        if p == "/status":  # as Audiobookshelf answers anyone: what it is (Sign In Everywhere looks for it)
            return self.reply({"app": "audiobookshelf", "serverVersion": "2.37.2", "isInit": True})
        if method == "POST" and p == "/login":  # as Audiobookshelf: the user with their own token
            body = self.body()
            if m.accounts.get(body.get("username")) == body.get("password") and body.get("username"):
                return self.reply({"user": {"username": body["username"], "token": f"token-{body['username']}",
                                            "accessToken": "short.lived.jwt"}})
            return self.reply({"error": "Unauthorized"}, 401)
        if not self.authorized(q):
            return self.reply(b"Unauthorized", 401, "text/plain")
        parts = p.strip("/").split("/")
        if method == "GET" and p == "/api/libraries":
            return self.reply({"libraries": [{"id": "lib1", "name": "Stories", "mediaType": "book"}]})
        if method == "GET" and p == "/api/libraries/lib1/items":
            items = sorted(m.books, key=lambda b: b["addedAt"], reverse=q.get("desc") == "1") \
                if q.get("sort") == "addedAt" else list(m.books)
            limit = int(q.get("limit") or 0) or len(items)
            return self.reply({"results": items[:limit], "total": len(items)})
        if method == "GET" and p == "/api/libraries/lib1/search":
            # as Audiobookshelf: books by their titles; an author found by itself (their books asked for apart), a
            # series with its books
            term = q.get("q", "").lower()
            return self.reply({"book": [{"libraryItem": b} for b in m.books if term in b["media"]["metadata"]["title"].lower()],
                               "authors": [{"id": "au1", "name": "The Author", "numBooks": len(m.books)}] if term in "the author" else [],
                               "series": [{"series": {"id": "se1", "name": "Bedtime Series"}, "books": [m.item("bk10")]}]
                               if term in "bedtime series" else []})
        if method == "GET" and p == "/api/authors/au1":
            return self.reply({"id": "au1", "name": "The Author", **({"libraryItems": m.books} if q.get("include") == "items" else {})})
        if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "items"]:
            b = m.item(parts[2])
            if not b:
                return self.reply(b"Not Found", 404, "text/plain")
            extra = {"userMediaProgress": m.progress.get(b["id"])} if "progress" in q.get("include", "") else {}
            return self.reply({**b, **extra})
        if method == "GET" and len(parts) == 4 and parts[:2] == ["api", "items"] and parts[3] == "cover":
            if parts[2] == "bk2":  # its cover file has gone missing
                return self.reply(b"Not Found", 404, "text/plain")
            return self.reply(png(4, 4, (200, 150, 60)), ctype="image/png")
        if method == "GET" and len(parts) == 5 and parts[:2] == ["api", "items"] and parts[3] == "file":
            m.streamed.append(parts[4])
            if m.stream_cut:
                cut, m.stream_cut = m.stream_cut, 0
                data = m.audio_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/ogg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[:cut])
                self.wfile.flush()
                m.stop()
                return
            data = m.audio_path.read_bytes()
            rng = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            if not rng:
                return self.reply(data, ctype="audio/ogg")
            start = int(rng.group(1))  # range requests like Audiobookshelf, so mpv can start in the middle
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
                pass  # mpv lets go when it seeks
            return
        if method == "GET" and p == "/api/me":
            return self.reply({"id": "u1", "username": "me", "token": "secret-user-token", "mediaProgress": [
                {"id": f"p-{k}", "libraryItemId": k, "episodeId": None, **v} for k, v in m.progress.items()]})
        if method == "GET" and p == "/api/me/items-in-progress":
            return self.reply({"libraryItems": m.begun()})
        if method == "GET" and len(parts) == 5 and parts[:3] == ["api", "me", "progress"] and parts[4] == "remove-from-continue-listening":
            m.hidden.append(parts[3])
            return self.reply(b"OK", ctype="text/plain")
        if method == "PATCH" and len(parts) == 4 and parts[:3] == ["api", "me", "progress"]:
            body = self.body()
            m.patches.append((parts[3], body))
            m.progress.setdefault(parts[3], {"currentTime": 0, "isFinished": False}).update(body)
            return self.reply(b"OK", ctype="text/plain")
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "items"] and parts[3] == "play":
            b = m.item(parts[2])
            if not b:
                return self.reply(b"Not Found", 404, "text/plain")
            sid = uuid.uuid4().hex
            m.sessions[sid] = b["id"]
            md = b["media"]
            return self.reply({"id": sid, "libraryItemId": b["id"], "displayTitle": md["metadata"]["title"],
                               "displayAuthor": md["metadata"]["authorName"], "duration": md["duration"],
                               "currentTime": m.progress.get(b["id"], {}).get("currentTime", 0), "chapters": md["chapters"],
                               "audioTracks": [{**t, "contentUrl": f"/api/items/{b['id']}/file/{t['ino']}"} for t in md["tracks"]],
                               "libraryItem": b})
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] in ("sync", "close"):
            if parts[2] not in m.sessions:
                return self.reply(b"Not Found", 404, "text/plain")
            body = self.body()
            m.reports.append((parts[3], parts[2], body))
            item_id = m.sessions[parts[2]]
            if parts[3] == "close":
                del m.sessions[parts[2]]
            m.progress.setdefault(item_id, {"isFinished": False}).update(
                currentTime=body.get("currentTime", 0), duration=body.get("duration"))
            return self.reply(b"OK", ctype="text/plain")
        self.reply(b"Not Found", 404, "text/plain")

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")
