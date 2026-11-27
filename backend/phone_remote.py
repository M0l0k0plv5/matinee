"""The phone remote (remote/) served from this device, so no web server of one's own is needed: with the setting
"phoneremote" on, a second small server answers in the home network with the remote's files only, and the settings
page shows its address as a QR code for the phone's camera. The backend itself stays on 127.0.0.1: this server knows
no cookie, no account and none of its requests, only the few files below and the Jellyfin address to prefill
(config.json, as remote/README.md describes for a server of one's own). The remote then talks to Jellyfin only."""

import json
import os
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import qr
from .core import ROOT, conf

REMOTE = ROOT / "remote"
FILES = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
         "manifest.webmanifest": "application/manifest+json", "icon.svg": "image/svg+xml",
         "icon-512.png": "image/png", "apple-touch-icon.png": "image/png"}
PHONE_PORT = int(os.environ.get("KINO_PHONE_PORT", "8767"))
PHONE_HOST = os.environ.get("KINO_PHONE_HOST", "0.0.0.0")  # every network of the device; the tests: 127.0.0.1
served = {"server": None}
served_lock = threading.Lock()


class PhoneHandler(BaseHTTPRequestHandler):
    server_version = "remote"
    sys_version = ""

    def log_message(self, *_):
        pass

    def do_GET(self, body=True):
        name = urllib.parse.urlsplit(self.path).path.lstrip("/") or "index.html"
        if name == "config.json":
            server = conf.get("server")
            data = json.dumps({"server": server} if isinstance(server, str) and server else {}).encode()
            ctype = "application/json"
        elif name in FILES:
            try:
                data = (REMOTE / name).read_bytes()
            except OSError:
                return self.answer(404, b"", "text/plain", body)
            ctype = FILES[name]
        else:
            return self.answer(404, b"", "text/plain", body)
        self.answer(200, data, ctype, body)

    def do_HEAD(self):
        self.do_GET(body=False)

    def answer(self, status, data, ctype, body=True):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_POST(self):
        self.answer(405, b"", "text/plain")

    do_PUT = do_DELETE = do_PATCH = do_POST


def apply():
    """Serve the remote, or stop, as the setting says; True when it is served."""
    on = conf.get("phoneremote") == "on" and (REMOTE / "index.html").is_file()
    with served_lock:
        srv = served["server"]
        if on and srv is None:
            try:
                srv = ThreadingHTTPServer((PHONE_HOST, PHONE_PORT), PhoneHandler)
            except OSError:
                return False  # the port taken: the settings page says the remote cannot be reached
            srv.daemon_threads = True
            served["server"] = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        elif not on and srv is not None:
            served["server"] = None
            srv.shutdown()  # the port free again before this returns: turned on again at once, it binds anew
            srv.server_close()
        return served["server"] is not None


def own_address():
    """This device's address in the network the phone most likely shares with it: the one it reaches Jellyfin from
    (a home network or a tailnet), else the one of its way out. A UDP socket only chooses it: nothing is sent."""
    if PHONE_HOST not in ("0.0.0.0", "::", ""):
        return PHONE_HOST
    host = urllib.parse.urlsplit(str(conf.get("server") or "")).hostname
    for target in ([host] if host else []) + ["192.0.2.1"]:  # 192.0.2.1: an address for examples, never reached
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 9))
                ip = s.getsockname()[0]
        except OSError:
            continue
        if not ip.startswith("127."):
            return ip
    return None


def remote_state():
    """For the settings page: {"on": the setting, "url": where the phone opens it, or None when it cannot be served}."""
    on = conf.get("phoneremote") == "on"
    with served_lock:
        srv = served["server"]
    ip = own_address() if srv else None
    return {"on": on, "url": f"http://{ip}:{srv.server_address[1]}/" if srv and ip else None}


def qr_svg():
    """The QR code of the address, or None while there is none."""
    url = remote_state()["url"]
    return qr.svg(url).encode() if url else None
