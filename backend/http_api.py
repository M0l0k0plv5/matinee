"""The HTTP API the UI talks to (and the proxy to the servers behind it)."""
import hmac
import http.cookies
import json
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from . import core, diagnose, jellyfin_api, phone_remote, playback, display, services, system
from .core import (ALLOWED_HOSTS, ALLOWED_ORIGINS, BOOT_CLOCK, conf, DISK_ERRORS, PROXIED, save_state,
    state, UI_SECRET, VERSION, WEB)
from .jellyfin_api import conf_changing, jf, log_failure, ready, reload_token, setup_jellyfin
from .age_limit import age_limit, limited
from .local_server import local_setup, local_share, local_state
from .support import support_send, support_url
from .hdmi import cec_there, hdmi_back, hdmi_ok_inputs, hdmi_switch
from .network import jf_discover, WIFI_PSK, wifi_connect, wifi_forget, wifi_networks, wifi_ok_ssid, wifi_there
from .sound import audio_sync_now, bluetooth_there, bt_action, bt_devices, BT_MAC, bt_scan, change_volume, sink_volume
from .playback import forget_early_of, resume_with_early, with_early
from .preferences import save_settings, set_simple, setting, simple_on, sound_level, ui_size_auto
from .display import display_choices
from .handoff import handoff_targets, music_songs, send_handoff
from .sleeping import sleep_timer
from .video_player import player
from .library import (episode_counts, forget, libcheck, libcheck_fix, libcheck_lock, libcheck_scan, libcheck_view,
    load_hidden, put_back, similar_ids, SUB_ID, SUB_SEARCH_LANGS, subtitle_search, unhide_all)
from .live_events import listeners, listeners_lock
from .forecast import find_places, place_name, weather
from .tv import load_tv, tv_guide_grid, tv_live_channel, tv_now, tv_own, tv_remember_live
from .syncing import sync
from .services import (abs_book, ABS_PATHS, abs_request, abs_session, AbsError, absj, BOOK_SPEEDS, configure_service,
    NavidromeError, nd, ND_METHODS, nd_request, nd_session, nd_station, pf_cover, pf_find, pf_follow, pf_heard,
    PF_ID, pf_play, pf_podcast_page, pf_podcasts, pf_recent, pf_session, pf_unfollow, PfError, sign_in_everywhere)
from .music_player import music
from .system import note_active, tailscale_info, time_synced
from .updates import (_update_seen, autoupdate_on, can_install, DESKTOP_UPDATES, overview, probation_ping,
    recent_changes, update_check, update_install, update_said, UPDATES)
from .account import sign_out, transfer_cancel, transfer_devices, transfer_send, transfer_state, transfer_wait


# Jellyfin's version for the settings: asked anew each time they open, but waited for at most a moment. A server
# that is off or a network that is gone would otherwise hold the page up for the connection's whole timeout (20 s);
# the answer still on its way then fills the next opening, and until then the version last known is shown
_jf_version = {"v": None, "asking": None}
_jf_version_lock = threading.Lock()
JF_VERSION_WAIT = 1.0


def _ask_jellyfin_version():
    try:
        _jf_version["v"] = jf("/System/Info/Public").get("Version")
    except Exception:
        pass


def jellyfin_version():
    with _jf_version_lock:
        asking = _jf_version["asking"]
        if asking is None or not asking.is_alive():
            asking = _jf_version["asking"] = threading.Thread(target=_ask_jellyfin_version, daemon=True)
            asking.start()
    asking.join(JF_VERSION_WAIT)
    return _jf_version["v"]


BODY_MAX = 2_000_000  # a request body bigger than this is refused unread

# Jellyfin for the UI, with Matinee's token: only the reading calls the UI makes (the lists, a title, its seasons and
# episodes, its pictures and trailers). Nothing else of the server (its users, logs, keys) goes through, whatever asks.
JF_PATHS = re.compile(r"/(Items(/Latest|/[\w-]+(/(Images/\w+(/\d+)?|LocalTrailers))?)?|Shows/(NextUp|[\w-]+/(Seasons|Episodes))"
                      r"|UserViews|UserItems/Resume|Genres)")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json", headers=()):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Chromium gave up on it (an image scrolled away, a page left): nothing to tell anyone

    def allowed(self):
        """Only Matinee's own page may talk to the backend. The Host check stops DNS rebinding (a website
        pretending to be 127.0.0.1), the Origin check stops other websites from sending commands, and the cookie
        (UI_SECRET) requests no website can make with it (SameSite=Strict, HttpOnly). The page itself needs none:
        it brings the cookie. Not kept out: programs on the computer itself, which can load the page and take the cookie from it like Chromium."""
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return False
        if self.command == "GET" and self.path in ("/", "/index.html"):
            return True
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return False
        sent = cookies.get("kino")
        return sent is not None and hmac.compare_digest(sent.value, UI_SECRET)

    def do_GET(self):
        if not self.allowed():
            return self.send(403, b"forbidden", "text/plain")
        if self.path.startswith("/jf/"):
            return self.proxy()
        if self.path == "/session":
            if not ready.is_set():
                return self.send(503, json.dumps({**(jellyfin_api.login_error or {"error": "connecting"}), "server": core.SERVER,
                                                  "username": conf.get("username", "")}).encode())
            return self.send(200, json.dumps({"userId": state["user_id"], "server": core.SERVER}).encode())
        if self.path == "/events":
            return self.events()
        mt = re.fullmatch(r"/similar/([\w-]+)", self.path)
        if mt:
            if not ready.is_set():
                return self.send(503, b"{}")
            try:
                ids = similar_ids(mt.group(1))
            except urllib.error.HTTPError as e:
                return self.send(e.code, b"{}")
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200, json.dumps({"ids": ids}).encode())
        if self.path == "/library/check":
            return self.send(200, json.dumps(libcheck_view()).encode())
        if self.path == "/badges":
            if not ready.is_set():
                return self.send(503, b"{}")
            if setting("badges") == "off":
                return self.send(200, json.dumps({"series": {}, "seasons": {}}).encode())
            try:
                return self.send(200, json.dumps(episode_counts()).encode())
            except urllib.error.HTTPError as e:
                return self.send(e.code, b"{}")
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(502, json.dumps({"error": str(e)}).encode())
        if self.path == "/status":
            return self.send(200, json.dumps({"playing": player.playing, "itemId": player.item_id,
                                              "error": player.error, "sleep": sleep_timer.state()}).encode())
        if self.path.startswith("/nd/"):
            return self.nd_proxy()
        if self.path == "/music/session":
            return self.send(200, json.dumps(nd_session()).encode())
        if self.path == "/abs/session":
            return self.send(200, json.dumps(abs_session()).encode())
        if self.path == "/abs/progress":  # how far each book has been heard, for the marks on their covers
            try:
                me = absj("/api/me")
            except (AbsError, LookupError) as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            return self.send(200, json.dumps({
                p["libraryItemId"]: {"progress": p.get("progress") or 0, "finished": bool(p.get("isFinished")),
                                     "at": int((p.get("lastUpdate") or 0) / 1000)}  # when last heard, for Up Next
                for p in me.get("mediaProgress", []) if p.get("libraryItemId") and not p.get("episodeId")}).encode())
        if self.path.startswith("/abs/"):
            return self.abs_proxy()
        if self.path.startswith("/pf/"):
            return self.pf_get()
        if self.path == "/handoff":  # the other Matinees of the account that are on, to go on on
            return self.send(200, json.dumps([{"id": d["id"], "name": d["name"]} for d in handoff_targets()]).encode(),
                             "application/json", [("Cache-Control", "no-store")])
        if self.path.startswith("/music/status"):
            have = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("have", [None])[0]
            return self.send(200, json.dumps(music.status(have)).encode())
        if self.path == "/tv/own":  # the library as channels, for their button and page among the libraries
            return self.send(200, json.dumps({"own": tv_own(), "lastLive": load_tv().get("lastLive")}).encode())
        if self.path == "/tv/guide":
            return self.send(200, json.dumps(tv_guide_grid()).encode())
        if self.path == "/tv/now":
            return self.send(200, json.dumps(tv_now()).encode())
        if self.path == "/start":  # what the UI needs at once when it starts: without asking Jellyfin anything
            if not system.ui_started:  # for the log: how long from switching on to Matinee's first page
                system.ui_started = True
                print(f"UI started {time.clock_gettime(BOOT_CLOCK):.1f} s after boot", file=sys.stderr)
            return self.send(200, json.dumps({"section": state.get("section"), "language": core.LANG, "screensaver": setting("screensaver"),
                                              "startwith": setting("startwith"), "lastLive": load_tv().get("lastLive"),
                                              "spoilers": setting("spoilers"),
                                              "sounds": sound_level(), "soundkind": setting("soundkind"), "badges": setting("badges"),
                                              "effects": setting("effects"), "textsize": setting("textsize"), "motion": setting("motion"), "size": setting("size"), "sizeAuto": ui_size_auto(), "simple": simple_on(),
                                              # which services are set up (their tiles), without asking them: one
                                              # that is slow to answer does not hold up the main menu
                                              "services": {"navidrome": bool(services.navidrome), "audiobookshelf": bool(services.audiobookshelf),
                                                           "podfetch": bool(services.podfetch)},
                                              # a new device: the first start's steps instead of the main menu
                                              # the main menu's picture, if one is chosen (config.json: menupicture,
                                              # a Jellyfin title's id): that one always, none chosen by itself
                                              "menuPicture": conf.get("menupicture") or None,
                                              "welcome": not conf.get("welcomed") and not core.SERVER}).encode())
        if self.path == "/weather":
            return self.send(200, json.dumps(weather() or {}).encode())
        if self.path.startswith("/weather/places?"):
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0].strip()[:100]
            if not name:
                return self.send(400, b"{}")
            try:
                return self.send(200, json.dumps({"places": find_places(name)}).encode())
            except (OSError, core.CUT_SHORT, ValueError, KeyError) as e:
                return self.send(502, json.dumps({"error": str(e)}).encode())
        if self.path == "/overview":
            return self.send(200, json.dumps(overview()).encode())
        if self.path == "/bt/devices":
            return self.send(200, json.dumps(bt_devices()).encode())
        if self.path in ("/wifi", "/wifi?rescan=1"):  # the networks nearby (rescan: looked for anew, a few seconds)
            return self.send(200, json.dumps(wifi_networks(self.path.endswith("rescan=1"))).encode())
        if self.path == "/jellyfin/local":  # Jellyfin on this computer (deploy/server.sh), and its share
            return self.send(200, json.dumps(local_state()).encode())
        if self.path == "/network":  # whether there is a network at all (the first start's second step)
            return self.send(200, json.dumps({"up": jellyfin_api.net_up()}).encode())
        if self.path == "/hdmi":  # the TV's other inputs, for the main menu's tiles and their settings
            return self.send(200, json.dumps(hdmi_state()).encode())
        if self.path == "/jellyfin/discover":  # the Jellyfin servers on the local network, for the first sign-in
            return self.send(200, json.dumps({"servers": jf_discover()}).encode())
        if self.path.startswith("/subtitles/search?"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            item_id = q.get("id", [""])[0]
            lang = q.get("lang", [""])[0] or (setting("sublang") if setting("sublang") != "auto" else core.LANG)
            if not re.fullmatch(r"[\w-]{1,64}", item_id) or lang not in SUB_SEARCH_LANGS:
                return self.send(400, b"{}")
            try:
                items = subtitle_search(item_id, lang)
            except Exception as e:
                log_failure("/subtitles/search", e)
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200, json.dumps({"lang": lang, "items": items}).encode())
        if self.path == "/hidden":
            return self.send(200, json.dumps(load_hidden()).encode())
        if self.path == "/diagnose":  # Settings → System → Status
            return self.send(200, json.dumps({"lines": diagnose.status_lines()}).encode())
        if self.path == "/phoneremote.svg":  # the phone remote's address as a QR code (phone_remote.py)
            svg = phone_remote.qr_svg()
            return self.send(200, svg, "image/svg+xml", [("Cache-Control", "no-store")]) if svg else self.send(404, b"", "text/plain")
        if self.path == "/settings":
            server_version = jellyfin_version()
            return self.send(200, json.dumps({"language": core.LANG, "quality": playback.QUALITY, "server": core.SERVER,
                                              "username": conf.get("username"), "screensaver": setting("screensaver"),
                                              "sleepend": setting("sleepend"),
                                              "weather": place_name() or None, "saverweather": setting("saverweather"),
                                              "subsize": setting("subsize"), "autooff": setting("autooff"),
                                              "startwith": setting("startwith"), "tvoff": conf.get("tvoff", []), "maxage": setting("maxage"),
                                              "autoupdate": "on" if autoupdate_on() else "off", "updatecheck": setting("updatecheck"), "support": bool(support_url()), "share": local_share() is not None, "updates": display.IN_CAGE or DESKTOP_UPDATES, "updateInstall": can_install(), "update": update_said(_update_seen.get("r")), "updateRecent": recent_changes() if display.IN_CAGE or DESKTOP_UPDATES else [], "autoUpdates": UPDATES or DESKTOP_UPDATES, "autoUpdatesNight": UPDATES,
                                              "ownchannels": tv_own(hidden=True, wait=False), "scrobble": setting("scrobble"),
                                              "hidden": sum(map(len, load_hidden().values())),
                                              "ask": setting("ask"), "version": VERSION, "tailscale": tailscale_info(), "timeSynced": time_synced(),
                                              "audio": setting("audio"), "subtitles": setting("subtitles"),
                                              "sublang": setting("sublang"), "framerate": setting("framerate"),
                                              "moviesound": setting("moviesound"), "skipintro": setting("skipintro"), "substyle": setting("substyle"), "spoilers": setting("spoilers"), "sounds": sound_level(), "soundkind": setting("soundkind"),
                                              "badges": setting("badges"), "effects": setting("effects"), "textsize": setting("textsize"), "motion": setting("motion"), "size": setting("size"), "audiosync": audio_sync_now(),
                                              "serverVersion": server_version, "jellyfinReady": ready.is_set(),
                                              "sync": setting("sync"), "syncStatus": sync.status, "devices": sync.device_list(),
                                              "navidrome": {"server": services.ND.get("server"), "username": services.ND.get("username")} if services.ND.get("server") else None,
                                              "audiobookshelf": services.ABS_SERVER or None,
                                              "podfetch": {"server": services.PF.get("server"), "username": services.PF.get("username")} if services.PF.get("server") else None,
                                              "display": str(conf.get("display", "auto")),
                                              "displayModes": display_choices() if display.IN_CAGE else None,
                                              "bluetooth": bluetooth_there(), "wifi": wifi_there(), "hdmi": hdmi_state(), "phoneRemote": phone_remote.remote_state(),
                                              "system": display.IN_CAGE, "volume": sink_volume() if display.IN_CAGE else None, "simple": simple_on(),
                                              "section": state.get("section")}).encode())
        name = "index.html" if self.path in ("/", "") else self.path.lstrip("/").split("?")[0]
        f = (WEB / name).resolve()
        if WEB not in f.parents or not f.is_file():
            return self.send(404, b"not found", "text/plain")
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                 ".ttf": "font/ttf"}.get(f.suffix, "application/octet-stream")
        body = f.read_bytes()
        headers = [("Cache-Control", "no-cache")]
        if f.name == "index.html":  # the UI takes its language from <html lang>
            body = body.replace(b'<html lang="en"', f'<html lang="{core.LANG}"'.encode(), 1)
            # the secret for every later request (see allowed); no other website may show Matinee in a frame; only
            # Matinee's own script files run, so text from a server or a video's title slipped into the page as HTML
            # could not reach the backend with the page's rights
            headers += [("Set-Cookie", f"kino={UI_SECRET}; HttpOnly; SameSite=Strict; Path=/"),
                        ("Content-Security-Policy", "script-src 'self'; object-src 'none'; base-uri 'none'; "
                                                    "frame-ancestors 'none'"), ("X-Frame-Options", "DENY")]
        self.send(200, body, ctype + ("; charset=utf-8" if ctype.startswith("text") else ""), headers)

    def do_POST(self):
        try:
            return self.post()
        except OSError as e:
            # the disk full (or gone read-only): an answer the page can say so with, not a connection just dropped,
            # which left a change looking made that was not kept
            if e.errno not in DISK_ERRORS:
                raise
            print(f"Could not keep {self.path}: {e}", file=sys.stderr)
            return self.send(507, json.dumps({"error": "disk_full"}).encode())

    def post(self):
        if not self.allowed():
            return self.send(403, b"{}")
        # a website can only send JSON here after a CORS preflight, which the backend never answers
        if self.headers.get_content_type() != "application/json":
            return self.send(415, b"{}")
        # only digits (read(-1) would wait for the connection's end) and no more than any page ever sends
        length = self.headers.get("Content-Length") or "0"
        if not length.isdigit() or int(length) > BODY_MAX:
            return self.send(413, b"{}")
        try:
            body = json.loads(self.rfile.read(int(length)) or b"{}")
        except ValueError:
            return self.send(400, b"{}")
        if not isinstance(body, dict):  # every request here sends an object: anything else is a mistake, said as one
            return self.send(400, b"{}")
        if self.path == "/play":
            item_id = body.get("itemId")
            if not isinstance(item_id, str) or not re.fullmatch(r"[\w-]{1,64}", item_id):  # as /subtitles and the others
                return self.send(400, b"{}")
            try:
                player.play(item_id, body.get("resume", True))
            except PermissionError as e:  # above the age limit
                return self.send(403, json.dumps({"error": str(e)}).encode())
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/library/check":  # look through the films again
            if not ready.is_set():
                return self.send(503, b"{}")
            with libcheck_lock:
                busy = libcheck["state"] in ("scanning", "looking")
                if not busy:
                    libcheck.update(state="scanning", items=[], error=None)
            if not busy:
                threading.Thread(target=libcheck_scan, daemon=True).start()
            return self.send(200, json.dumps(libcheck_view()).encode())
        if self.path == "/library/fix":  # {"ids": [...]}: what the check found for them, applied
            ids = body.get("ids") if isinstance(body, dict) else None
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or not ready.is_set():
                return self.send(400, b"{}")
            threading.Thread(target=libcheck_fix, args=(set(ids),), daemon=True).start()
            return self.send(200, b"{}")
        if self.path == "/update/check":
            if not (display.IN_CAGE or DESKTOP_UPDATES):
                return self.send(403, b"{}")
            return self.send(200, json.dumps(update_said(update_check())).encode())
        if self.path in ("/transfer/wait", "/transfer/devices", "/transfer/send", "/signout"):
            try:
                if self.path == "/transfer/wait":
                    return self.send(200, json.dumps(transfer_wait()).encode())
                if self.path == "/transfer/devices":
                    return self.send(200, json.dumps({"devices": transfer_devices()}).encode())
                if self.path == "/transfer/send":
                    if not isinstance(body, dict):
                        return self.send(400, b"{}")
                    name = transfer_send(str(body.get("ip", "")), str(body.get("code", "")), body.get("password"))
                    return self.send(200, json.dumps({"name": name}).encode())
                sign_out()
                return self.send(200, b"{}")
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
        if self.path == "/transfer/state":
            return self.send(200, json.dumps(transfer_state()).encode())
        if self.path == "/transfer/cancel":
            transfer_cancel()
            return self.send(200, b"{}")
        if self.path == "/update/install":
            if not can_install():
                return self.send(403, b"{}")
            error = update_install()
            return self.send(502 if error else 200, json.dumps({"error": error} if error else {}).encode())
        if self.path == "/active":  # a key in the UI (at most once a minute): someone is there (see autooff_loop)
            note_active()
            return self.send(200, b"{}")
        if self.path == "/alive":  # the UI, every 30 seconds (see ui_watchdog)
            system.ui_alive = time.monotonic()
            probation_ping()
            return self.send(200, b"{}")
        if self.path == "/bt/scan":
            bt_scan()
            return self.send(200, b"{}")
        if self.path == "/bt/action":
            mac = str(body.get("mac", "")).upper()
            if not BT_MAC.fullmatch(mac) or body.get("action") not in ("connect", "disconnect", "remove"):
                return self.send(400, b"{}")
            ok = bt_action(mac, body["action"])
            return self.send(200 if ok else 502, json.dumps({"devices": bt_devices()["devices"]}).encode())
        if self.path == "/support/send":  # the person asked for it, on the TV: the report to support
            r = support_send(VERSION)
            return self.send(502 if "error" in r else 200, json.dumps(r).encode())
        if self.path == "/welcome/done":  # through the first start's steps (or left for later): not shown again
            with conf_changing() as c:
                c["welcomed"] = conf["welcomed"] = True
            return self.send(200, b"{}")
        if self.path == "/hdmi/inputs":
            inputs = hdmi_ok_inputs(body.get("inputs"))
            if inputs is None:
                return self.send(400, b"{}")
            with conf_changing() as c:
                c["hdmi_inputs"] = conf["hdmi_inputs"] = inputs
            return self.send(200, json.dumps(hdmi_state()).encode())
        if self.path in ("/hdmi/switch", "/hdmi/back"):
            port = body.get("port")
            if self.path == "/hdmi/switch" and port not in [x["port"] for x in hdmi_state()["inputs"]]:
                return self.send(400, b"{}")
            error = hdmi_switch(port) if self.path == "/hdmi/switch" else hdmi_back()
            return self.send(502 if error else 200, json.dumps({"error": error} if error else {}).encode())
        if self.path == "/wifi/connect":
            ssid, password = body.get("ssid"), body.get("password")
            if not wifi_ok_ssid(ssid) or not (password is None or isinstance(password, str) and WIFI_PSK.fullmatch(password)):
                return self.send(400, json.dumps({"error": "wifi_password"}).encode())
            error = wifi_connect(ssid, password)
            return self.send(502 if error else 200, json.dumps({"error": error} if error else wifi_networks()).encode())
        if self.path == "/wifi/forget":
            if not wifi_ok_ssid(body.get("ssid")):
                return self.send(400, b"{}")
            ok = wifi_forget(body["ssid"])
            return self.send(200 if ok else 502, json.dumps(wifi_networks()).encode())
        if self.path == "/section":  # the part of Matinee used last, for the main menu's focus
            if body.get("id") not in ("jellyfin", "music", "books", "podcasts"):
                return self.send(400, b"{}")
            reload_token()  # do not write back an old token if set-password ran meanwhile
            state["section"] = body["id"]
            save_state()
            return self.send(200, b"{}")
        if self.path == "/sleep":
            if body.get("cycle"):
                sleep_timer.cycle(book=music.playing_book())
            elif body.get("mode") in (None, "end", "chapter", 30, 60, 90):
                sleep_timer.set(body.get("mode"))
            else:
                return self.send(400, b"{}")
            return self.send(200, json.dumps({"sleep": sleep_timer.state(), "label": sleep_timer.label()}).encode())
        if self.path == "/volume":  # the TV computer's sound; on a desktop that is the desktop's own business
            if not display.IN_CAGE or body.get("step") not in (-5, 0, 5):
                return self.send(403 if not display.IN_CAGE else 400, b"{}")
            volume = change_volume(body["step"]) if body["step"] else sink_volume()
            return self.send(200 if volume is not None else 502, json.dumps({"volume": volume}).encode())
        if self.path == "/system":
            # restart or shut down the TV computer, only there: logind allows it to the session on its screen
            if not display.IN_CAGE or body.get("action") not in ("reboot", "poweroff"):
                return self.send(403, b"{}")
            music.stop()
            subprocess.Popen(["systemctl", body["action"]])
            return self.send(200, b"{}")
        if self.path == "/music/star":
            kind = {"song": "id", "album": "albumId", "artist": "artistId"}.get(body.get("type"))
            if not kind or not re.fullmatch(r"[\w-]+", str(body.get("id", ""))):
                return self.send(400, b"{}")
            try:
                nd("star" if body.get("on") else "unstar", **{kind: body["id"]})
            except NavidromeError as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/music/play" and "radio" in body:
            try:
                music.play([nd_station(str(body["radio"]))])
            except NavidromeError as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            except (OSError, ValueError) as e:
                return self.send(404, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path in ("/music/play", "/music/queue"):
            songs = music_songs(body.get("songs"))
            if not songs:
                return self.send(400, b"{}")
            if self.path == "/music/queue":
                try:
                    joined = music.enqueue(songs, bool(body.get("next")))
                except (OSError, ValueError, NavidromeError) as e:
                    return self.send(500, json.dumps({"error": str(e)}).encode())
                return self.send(200, json.dumps({"joined": joined}).encode())
            try:
                music.play(songs, int(body.get("start", 0)) % len(songs))
            except (OSError, ValueError, NavidromeError) as e:
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/abs/play":
            if not re.fullmatch(r"[\w-]+", str(body.get("id", ""))):
                return self.send(400, b"{}")
            try:
                at = body.get("at")
                music.play([abs_book(body["id"], None if at is None else max(0.0, float(at)))])
            except AbsError as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            except (LookupError, ValueError, TypeError) as e:
                return self.send(404, json.dumps({"error": str(e)}).encode())
            except OSError as e:
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/pf/play":
            if not PF_ID.fullmatch(str(body.get("id", ""))):
                return self.send(400, b"{}")
            try:
                at = body.get("at")
                music.play([pf_play(body["id"], None if at is None else max(0.0, float(at)))])
            except PfError as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            except (LookupError, ValueError, TypeError) as e:
                return self.send(404, json.dumps({"error": str(e)}).encode())
            except OSError as e:
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/pf/follow":  # a podcast from the podcast directory (its id there), followed in Podfetch
            directory_id = body.get("id") if isinstance(body, dict) else None
            if not isinstance(directory_id, int) or isinstance(directory_id, bool) or not 0 < directory_id < 2 ** 31:  # Podfetch takes it as an i32
                return self.send(400, b"{}")
            try:
                return self.send(200, json.dumps({"id": pf_follow(directory_id)}).encode())
            except PfError as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            except LookupError:
                return self.send(404, b"{}")
        if self.path == "/pf/unfollow":  # a podcast no longer followed, its downloaded episodes kept or not
            if not PF_ID.fullmatch(str(body.get("id", ""))):
                return self.send(400, b"{}")
            try:
                pf_unfollow(body["id"], body.get("files") is True)
            except PfError as e:
                return self.send(503, json.dumps({"error": str(e)}).encode())
            except LookupError:
                return self.send(404, b"{}")
            return self.send(200, b"{}")
        if self.path == "/pf/finished":  # an episode marked as heard (at its end), or as not heard (at its start)
            if not PF_ID.fullmatch(str(body.get("id", ""))):
                return self.send(400, b"{}")
            try:
                pf_heard(body["id"], max(0, int(body.get("duration") or 0)) if body.get("on") else 0)
            except (PfError, LookupError, ValueError, TypeError) as e:
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/abs/finished":  # marked as heard, or as not heard (then from the start)
            if not re.fullmatch(r"[\w-]+", str(body.get("id", ""))):
                return self.send(400, b"{}")
            done = bool(body.get("on"))
            try:
                absj(f"/api/me/progress/{body['id']}", "PATCH",
                     {"isFinished": True} if done else {"isFinished": False, "currentTime": 0, "progress": 0})
            except (AbsError, LookupError) as e:
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/music/handoff":  # "Continue on" in the music's player: the queue goes on on that Matinee
            device = body.get("device")
            if not isinstance(device, str) or not re.fullmatch(r"[\w-]{1,128}", device):
                return self.send(400, b"{}")
            what = music.handing_off()
            if not what:
                return self.send(409, b"{}")
            threading.Thread(target=send_handoff, args=(device, what), daemon=True).start()
            return self.send(200, b"{}")
        if self.path == "/music/control":
            if body.get("cmd") not in ("pause", "next", "prev", "seek", "index", "stop", "skip", "speed"):
                return self.send(400, b"{}")
            if body["cmd"] == "speed" and body.get("value") not in BOOK_SPEEDS:
                return self.send(400, b"{}")
            try:
                ok = music.control(body["cmd"], body.get("value"))
            except (OSError, ValueError, TypeError) as e:
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200 if ok else 409, b"{}")
        if self.path == "/forget":
            what, value = body.get("what"), body.get("id")
            if not isinstance(value, str) or not value or len(value) > 200 or what not in (
                    "resume", "nextup", "album", "book"):
                return self.send(400, b"{}")
            if not re.fullmatch(r"[\w-]+", value):
                return self.send(400, b"{}")  # ids only: it goes into a Jellyfin path
            try:
                forget(what, value)
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/forget/undo":  # the last one taken off a row, back on it
            try:
                done = put_back()
            except Exception as e:
                log_failure(self.path, e)
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200 if done else 404, b"{}")
        if self.path == "/subtitles/get":
            item_id, sub = body.get("id"), body.get("subtitle")
            if not isinstance(item_id, str) or not re.fullmatch(r"[\w-]{1,64}", item_id) or not isinstance(sub, str) \
                    or not SUB_ID.fullmatch(sub):
                return self.send(400, b"{}")
            try:
                jf(f"/Items/{item_id}/RemoteSearch/Subtitles/{urllib.parse.quote(sub, safe='')}", "POST")
            except Exception as e:
                log_failure("/subtitles/get", e)
                return self.send(502, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/unhide":
            unhide_all()
            return self.send(200, b"{}")
        if self.path == "/tv/play":  # one of one's own channels, live
            live = tv_live_channel(body.get("live"))
            if not live:
                return self.send(404, b"{}")
            on_now = tv_now().get(live[0])
            item = {"id": f"live-{live[0]}", "channel": live[1], "url": live[2], "live": live[0],
                    "title": f"{live[1]} – {on_now['title']}" if on_now else live[1]}
            try:
                tv_remember_live(live[0])
                player.play_tv(item)
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/settings":
            try:
                save_settings(body)
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
            if body.get("sync") == "on":
                sync.kick.set()  # turned on: at once
            return self.send(200, b"{}")
        if self.path == "/simple":
            try:
                set_simple(body)
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
            except PermissionError as e:
                return self.send(403, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/jellyfin/local/setup":
            try:
                local_setup(body if isinstance(body, dict) else {})
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/jellyfin/setup":
            try:
                setup_jellyfin(body)
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/signin/everywhere":
            try:
                return self.send(200, json.dumps({"results": sign_in_everywhere(body)}).encode())
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
        if self.path == "/service":
            try:
                configure_service(body)
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path in ("/favorite", "/played") and not re.fullmatch(r"[\w-]+", str(body.get("itemId", ""))):
            return self.send(400, b"{}")  # it goes into a Jellyfin path
        if self.path == "/favorite":
            try:
                jf(f"/UserFavoriteItems/{body['itemId']}?userId={state['user_id']}", "POST" if body.get("on") else "DELETE")
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(500, json.dumps({"error": str(e)}).encode())
            return self.send(200, b"{}")
        if self.path == "/played":
            try:
                jf(f"/UserPlayedItems/{body['itemId']}?userId={state['user_id']}", "POST" if body.get("played") else "DELETE")
            except Exception as e:
                log_failure(self.path.split("?")[0], e)
                return self.send(500, json.dumps({"error": str(e)}).encode())
            try:
                forget_early_of(body["itemId"])  # watched or not, either way not begun any more
            except OSError as e:  # done on Jellyfin all the same: not said to have failed
                print(f"Could not forget where {body['itemId']} was stopped: {e}", file=sys.stderr)
            return self.send(200, b"{}")
        self.send(404, b"{}")

    def events(self):
        """Server-sent events: tell the UI about changes on the server."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = queue.Queue()
        with listeners_lock:
            listeners.add(q)
        try:
            while True:
                try:
                    msg = f"data: {q.get(timeout=25)}\n\n"
                except queue.Empty:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with listeners_lock:
                listeners.discard(q)

    def nd_proxy(self):
        """Navidrome for the UI, signed in by the backend: only the reading calls in ND_METHODS."""
        u = urllib.parse.urlsplit(self.path)
        method = u.path[len("/nd/"):]
        if method not in ND_METHODS:
            return self.send(403, b"{}")
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if method == "getCoverArt":  # the picture itself, kept by Chromium for a week
                status, ctype, body = nd_request(method, params)
                # no picture of its own: Navidrome sends its placeholder (a star or a disc on grey, a small WebP of
                # about half a kilobyte in any size); the UI leaves it out, as a picture that is not there
                if status == 200 and ctype == "image/webp" and len(body) < 2048:
                    return self.send(404, b"", "text/plain", PROXIED)
                return self.send(status, body, ctype, ([("Cache-Control", "max-age=604800")] if status == 200 else []) + PROXIED)
            r = nd(method, **params)
        except NavidromeError as e:
            return self.send(503, json.dumps({"error": str(e)}).encode())
        self.send(200, json.dumps(r).encode(), "application/json", [("Cache-Control", "no-store"), *PROXIED])

    def pf_get(self):
        """Podfetch for the UI, through the backend that signs in: the podcasts, one with its episodes, what is
        begun and new, a podcast's picture."""
        path = urllib.parse.urlsplit(self.path).path[len("/pf"):]
        try:
            if path == "/session":
                return self.send(200, json.dumps(pf_session()).encode())
            if path == "/podcasts":
                return self.send(200, json.dumps({"podcasts": pf_podcasts()}).encode(), "application/json", [("Cache-Control", "no-store")])
            if path == "/recent":
                return self.send(200, json.dumps(pf_recent()).encode(), "application/json", [("Cache-Control", "no-store")])
            if path == "/find":  # ?q=: podcasts to follow, from the podcast directory
                term = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0].strip()[:100]
                return self.send(200, json.dumps({"podcasts": pf_find(term) if len(term) >= 2 else []}).encode(),
                                 "application/json", [("Cache-Control", "no-store")])
            m = re.fullmatch(r"/(podcast|cover)/([\w-]{1,100})", path)
            if not m:
                return self.send(404, b"{}")
            if m.group(1) == "podcast":
                return self.send(200, json.dumps(pf_podcast_page(m.group(2))).encode(), "application/json", [("Cache-Control", "no-store")])
            ctype, data = pf_cover(m.group(2))  # kept by Chromium for a week, as the other covers
            return self.send(200, data, ctype, [("Cache-Control", "max-age=604800")])
        except PfError as e:
            return self.send(503, json.dumps({"error": str(e)}).encode())
        except LookupError:
            return self.send(404, b"{}")
        except (OSError, ValueError):
            return self.send(502, json.dumps({"error": "pf_unreachable"}).encode())

    def abs_proxy(self):
        """Audiobookshelf for the UI, with the backend's token: only reading, only the paths in ABS_PATHS."""
        u = urllib.parse.urlsplit(self.path[len("/abs"):])
        if not ABS_PATHS.fullmatch(u.path):
            return self.send(403, b"{}")
        try:
            status, ctype, body = abs_request(u.path + (f"?{u.query}" if u.query else ""))
        except AbsError as e:
            return self.send(503, json.dumps({"error": str(e)}).encode())
        cover = u.path.endswith("/cover") and status == 200  # the picture, kept by Chromium for a week
        self.send(status, body, ctype, [("Cache-Control", "max-age=604800" if cover else "no-store"), *PROXIED])

    def proxy(self):
        if not ready.is_set():  # e.g. signed out: the UI goes back to its start screen
            return self.send(503, json.dumps(jellyfin_api.login_error or {"error": "connecting"}).encode())
        if not JF_PATHS.fullmatch(urllib.parse.urlsplit(self.path[3:]).path):
            return self.send(403, b"{}")
        try:
            resp = jf(self.path[3:], raw=True)
        except urllib.error.HTTPError as e:
            # the page takes 503 and 401 for "signed out" and asks /session again: Jellyfin's own (starting, behind a
            # proxy being restarted) while still signed in would have it ask again and again, at once
            code = 502 if e.code in (401, 503) and ready.is_set() else e.code
            return self.send(code, e.read(), e.headers.get("Content-Type", "text/plain"), PROXIED)
        except Exception as e:
            log_failure(self.path.split("?")[0], e)
            return self.send(502, str(e).encode(), "text/plain")
        is_image = "/Images/" in self.path
        cache = [("Cache-Control", "max-age=604800")] if is_image else [("Cache-Control", "no-store")]
        ctype = resp.headers.get("Content-Type", "application/json")
        body = resp.read()
        if not is_image and "json" in ctype:
            body = resume_with_early(self.path[3:], body)  # which may add titles: the age limit after it
            if age_limit() is not None and body:
                # above the age limit: left out of lists, and an item's own page not shown
                try:
                    kept = limited(json.loads(body))
                except ValueError:
                    kept = None
                if kept is None:
                    return self.send(403, json.dumps({"error": "age_limit"}).encode())
                body = json.dumps(kept).encode()
            body = with_early(body)
        self.send(200, body, ctype, cache + PROXIED)


def hdmi_state():
    """Whether the TV's input can be switched here, and the inputs set up (config.json's hdmi_inputs)."""
    return {"available": cec_there(), "inputs": hdmi_ok_inputs(conf.get("hdmi_inputs", [])) or []}


# the last part: what Matinee Remote shows of the other servers, which needs all of the above (see core's end)
from . import remote_shelf  # noqa: E402,F401
