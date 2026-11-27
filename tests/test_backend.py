"""Backend: login, proxying, watched state, live events, retry queue, language."""
import http.client
import json
import os
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import unittest
from pathlib import Path

from helpers import ROOT, Backend, free_port, wait_for
from mockjf import PASSWORD, TICKS, USERNAME, MockJellyfin, TOKEN


class SSE:
    """Reads the backend's server-sent events in the background."""

    def __init__(self, port, cookie):
        self.events = []
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        self.conn.request("GET", "/events", headers={"Cookie": cookie})
        self.resp = self.conn.getresponse()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.resp:
                if line.startswith(b"data:"):
                    self.events.append(line[5:].strip().decode())
        except OSError:
            pass

    def close(self):
        self.conn.close()


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.backend = Backend(self.mock.url).start()

    def tearDown(self):
        self.backend.stop()
        try:
            self.mock.stop()
        except Exception:
            pass

    # ---------- Subtitles from the internet ----------

    def test_subtitles_found_and_downloaded(self):
        code, r = self.backend.request("/subtitles/search?id=m1&lang=de")
        self.assertEqual(code, 200)
        self.assertEqual(r["lang"], "de")
        self.assertEqual(r["items"][0], {"id": "os-ger-1", "name": "Movie.1.German.srt", "provider": "Open Subtitles",
                                         "format": "srt", "downloads": 1234, "match": True, "forced": False, "hearing": False})
        self.assertEqual(self.backend.request("/subtitles/search?id=m1&lang=en")[1]["items"], [])
        self.assertEqual(self.backend.request("/subtitles/search?id=m1")[1]["lang"], "en", "the subtitle language of the settings")
        for bad in ("/subtitles/search?id=../x&lang=de", "/subtitles/search?id=m1&lang=xx"):
            self.assertEqual(self.backend.request(bad)[0], 400, bad)
        self.assertEqual(self.backend.request("/subtitles/get", {"id": "m1", "subtitle": "os-ger-1"})[0], 200)
        self.assertEqual(self.mock.subs_downloaded, [("m1", "os-ger-1")], "Jellyfin downloads it")
        self.assertEqual(self.backend.request("/subtitles/get", {"id": "m1", "subtitle": "os-ger-2/../x"})[0], 400,
                         "nothing that leaves the path")

    # ---------- Status (Settings → System) ----------

    def test_status(self):
        code, r = self.backend.request("/diagnose")
        self.assertEqual(code, 200)
        lines = {l["k"]: l for l in r["lines"]}
        self.assertEqual([l["k"] for l in r["lines"]][:3], ["version", "uptime", "jellyfin"], "Matinee first, then its server")
        self.assertEqual((lines["jellyfin"]["state"], lines["jellyfin"]["say"]), ("ok", "diagAnswers"))
        self.assertTrue(lines["jellyfin"]["vars"]["version"])
        self.assertNotIn("navidrome", lines, "a server not set up is left out")
        self.assertEqual(lines["playback"]["state"], "ok")
        self.assertIn(lines["disk"]["state"], ("ok", "warn"))
        self.assertTrue(all(l["state"] in ("ok", "warn", "bad", "info") for l in r["lines"]))
        self.mock.stop()
        start = time.monotonic()
        lines = {l["k"]: l for l in self.backend.request("/diagnose")[1]["lines"]}
        self.assertEqual((lines["jellyfin"]["state"], lines["jellyfin"]["say"]), ("bad", "diagNoAnswer"), "the server gone")
        self.assertLess(time.monotonic() - start, 8, "not waited for long")

    # ---------- Login ----------

    def test_login_and_session(self):
        code, data = self.backend.request("/session")
        self.assertEqual(code, 200)
        self.assertEqual(data["userId"], "u1")
        state = self.backend.conf / "state.json"
        self.assertEqual(json.loads(state.read_text())["token"], TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(state).st_mode), 0o600, "token file must be private")

    def test_stored_token_is_reused(self):
        self.backend.proc.terminate()
        self.backend.proc.wait()
        logins = sum("AuthenticateByName" in r for r in self.mock.requests)
        self.backend.start()
        self.assertEqual(sum("AuthenticateByName" in r for r in self.mock.requests), logins, "no new login needed")

    # ---------- Proxy ----------

    def test_api_proxy_keeps_token_in_backend(self):
        code, data = self.backend.request("/jf/UserViews?userId=u1")
        self.assertEqual(code, 200)
        self.assertEqual([v["Name"] for v in data["Items"]], ["Movies", "Shows", "Collections"])
        self.assertTrue(any(TOKEN in h for h in self.mock.auth_seen), "backend sends the token")
        code, body = self.backend.request("/session")
        self.assertNotIn(TOKEN, json.dumps(body), "the token must not reach the browser")

    def test_images_are_proxied_and_cached(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.backend.port)
        conn.request("GET", "/jf/Items/m1/Images/Primary?maxWidth=400", headers={"Cookie": self.backend.page_cookie()})
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "image/png")
        self.assertIn("max-age", r.getheader("Cache-Control"))
        self.assertTrue(r.read().startswith(b"\x89PNG"))

    def test_connections_to_jellyfin_are_kept_open(self):
        before = self.mock.connections
        for _ in range(20):
            self.assertEqual(self.backend.request("/jf/UserViews?userId=u1")[0], 200)
        for _ in range(10):
            conn = http.client.HTTPConnection("127.0.0.1", self.backend.port)
            conn.request("GET", "/jf/Items/m1/Images/Primary?maxWidth=400", headers={"Cookie": self.backend.page_cookie()})
            self.assertEqual(conn.getresponse().status, 200)
        self.assertLessEqual(self.mock.connections - before, 2, "30 requests, not 30 connections")

    def test_only_the_ui_calls_reach_jellyfin(self):
        before = len(self.mock.requests)
        for path in ("/jf/System/Logs", "/jf/Auth/Keys", "/jf/Users", "/jf/Items/../System/Info", "/jf/Sessions"):
            self.assertEqual(self.backend.request(path)[0], 403, path)
        # none of them sent on to Jellyfin (the backend's own calls, from its loops, may come meanwhile)
        sent = [p for p in self.mock.requests[before:] if p.split("?")[0] in ("/System/Logs", "/Auth/Keys", "/Users", "/Sessions", "/System/Info")]
        self.assertEqual(sent, [], "none of them sent to Jellyfin")
        self.assertEqual(self.backend.request("/jf/Shows/NextUp?userId=u1")[0], 200)

    def test_server_errors_are_passed_through(self):
        code, _ = self.backend.request("/jf/Items/doesnotexist")
        self.assertEqual(code, 404)
        self.mock.busy_paths.add("/UserViews")  # Jellyfin starting: not "signed out" to the page, which would ask again at once
        self.assertEqual(self.backend.request("/jf/UserViews?userId=u1")[0], 502)
        self.assertEqual(self.backend.request("/session")[0], 200, "still signed in")

    def test_static_files_cannot_escape_web_dir(self):
        code, body = self.backend.request("/")
        self.assertEqual(code, 200)
        self.assertIn(b"js/core.js", body)
        for path in ("/../server.py", "/%2e%2e/server.py", "/..%2fserver.py", "/web/../../server.py"):
            self.assertEqual(self.backend.request(path)[0], 404, path)

    # ---------- Watched state ----------

    def test_mark_watched_and_unwatched(self):
        self.assertEqual(self.backend.request("/played", {"itemId": "m2", "played": True})[0], 200)
        self.assertTrue(self.mock.lib.userdata["m2"]["Played"])
        self.assertEqual(self.backend.request("/played", {"itemId": "m2", "played": False})[0], 200)
        self.assertFalse(self.mock.lib.userdata["m2"]["Played"])

    def test_mark_whole_show_watched(self):
        self.backend.request("/played", {"itemId": "s1", "played": True})
        self.assertTrue(all(self.mock.lib.userdata[e]["Played"] for e in self.mock.lib.episodes))

    # ---------- Live events ----------

    def test_websocket_uses_header_auth(self):
        wait_for(lambda: self.mock.ws_connects >= 1, msg="WebSocket connected")
        self.assertEqual(self.mock.ws_rejected, 0, "Jellyfin 12 rejects api_key in the URL, the header must be used")

    def test_server_change_reaches_the_ui(self):
        wait_for(lambda: self.mock.ws_connects >= 1)
        sse = SSE(self.backend.port, self.backend.page_cookie())
        time.sleep(0.3)
        self.mock.change_played("m5", True)  # e.g. marked as watched in another app
        wait_for(lambda: "changed" in sse.events, timeout=5, msg="event sent to the UI")
        sse.close()

    def test_reconnects_after_server_outage(self):
        wait_for(lambda: self.mock.ws_connects >= 1)
        sse = SSE(self.backend.port, self.backend.page_cookie())
        self.mock.stop()
        time.sleep(1.5)
        self.mock.start()
        wait_for(lambda: self.mock.ws_connects >= 2, timeout=15, msg="WebSocket reconnected")
        # after the outage the UI should reload once, something may have changed
        wait_for(lambda: "changed" in sse.events, timeout=5, msg="reload after reconnect")
        self.mock.change_played("m6", True)
        wait_for(lambda: sse.events.count("changed") >= 2, timeout=5, msg="events after reconnect")
        sse.close()

    # ---------- Retry queue ----------

    def write_pending(self, entries):
        (self.backend.conf / "pending.json").write_text(json.dumps(entries))

    def pending(self):
        try:
            return json.loads((self.backend.conf / "pending.json").read_text())
        except FileNotFoundError:
            return []

    def stop_report(self, item="m4", ticks=50_000_000):
        return {"path": "/Sessions/Playing/Stopped", "body": {"ItemId": item, "PositionTicks": ticks}, "at": time.time()}

    def test_queued_report_is_sent(self):
        self.write_pending([self.stop_report()])
        wait_for(lambda: self.mock.reports_for("Stopped"), timeout=10, msg="report sent")
        self.assertEqual(self.mock.lib.userdata["m4"]["PlaybackPositionTicks"], 50_000_000)
        wait_for(lambda: self.pending() == [], msg="queue emptied")

    def test_outdated_report_is_dropped(self):
        # someone kept watching elsewhere in the meantime: our older position must not overwrite that
        self.mock.lib.userdata["m4"].update(PlaybackPositionTicks=150_000_000, LastPlayedDate="2099-01-01T00:00:00.0000000Z")
        self.write_pending([self.stop_report()])
        wait_for(lambda: self.pending() == [], timeout=10, msg="queue emptied")
        self.assertEqual(self.mock.reports_for("Stopped"), [])
        self.assertEqual(self.mock.lib.userdata["m4"]["PlaybackPositionTicks"], 150_000_000)

    def test_report_for_deleted_item_is_dropped(self):
        self.write_pending([self.stop_report("gone", 1)])
        wait_for(lambda: self.pending() == [], timeout=10, msg="queue emptied")

    def test_rejected_report_is_given_up_after_a_day(self):
        self.mock.report_status = 500
        old = {**self.stop_report("m4"), "at": time.time() - 2 * 24 * 3600}
        self.write_pending([old, self.stop_report("m5")])
        wait_for(lambda: len(self.pending()) == 1, timeout=10, msg="old report dropped")
        self.assertEqual(self.pending()[0]["body"]["ItemId"], "m5", "a recent one is still retried")
        self.mock.report_status = None
        wait_for(lambda: self.pending() == [], timeout=10, msg="sent once the server accepts it")
        self.assertEqual([b["ItemId"] for b in self.mock.reports_for("Stopped")], ["m5"])

    def test_queue_survives_while_server_is_down(self):
        self.mock.stop()
        self.write_pending([self.stop_report()])
        time.sleep(3)
        self.assertEqual(len(self.pending()), 1, "must not get lost without a server")
        self.mock.start()
        wait_for(lambda: self.mock.reports_for("Stopped"), timeout=15, msg="sent once the server is back")


class AnotherPersonTest(unittest.TestCase):
    def test_nothing_of_the_one_before_goes_to_another_user(self):
        """Sign In Everywhere as someone else (another Jellyfin user): the one before's channel watched last, hidden
        things and track choices leave the device, or its first sync would give them to the new one."""
        mock = MockJellyfin().start()
        b = Backend(mock.url).start()
        try:
            for name in ("tv.json", "hidden.json", "tracks.json"):
                (b.conf / name).write_text('{"progress": [], "x": 1}')
            mock.accounts["robin"] = "Same-Everywhere-7"  # invented
            mock.user_ids["robin"] = "0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f"
            code, res = b.request("/signin/everywhere", {"username": "robin", "password": "Same-Everywhere-7"})
            self.assertEqual((code, res["results"]["jellyfin"]), (200, "ok"))
            for name in ("tv.json", "hidden.json", "tracks.json"):
                self.assertFalse((b.conf / name).exists() and "x" in (b.conf / name).read_text(), name)
            self.assertIn("server", json.loads((b.conf / "config.json").read_text()), "the servers stay")
        finally:
            b.stop()
            mock.stop()

    def test_the_same_user_again_keeps_everything(self):
        mock = MockJellyfin().start()
        b = Backend(mock.url).start()
        try:
            (b.conf / "tv.json").write_text('{"x": 1}')
            mock.accounts["robin"] = "Same-Everywhere-7"  # invented: the same Jellyfin user under another name
            self.assertEqual(b.request("/signin/everywhere", {"username": "robin", "password": "Same-Everywhere-7"})[0], 200)
            self.assertIn('"x"', (b.conf / "tv.json").read_text())
        finally:
            b.stop()
            mock.stop()


class StartupTest(unittest.TestCase):
    """Startup behaviour when something is missing at boot."""

    def test_wrong_password(self):
        mock = MockJellyfin().start()
        b = Backend(mock.url, password="wrong")
        b.env["KINO_RETRY_MAX"] = "0.2"  # tried again many times in the seconds below
        b.start(wait_ready=False)
        try:
            wait_for(lambda: b.request("/session")[1].get("error") == "auth_rejected", msg="error code auth_rejected")
            self.assertEqual(b.request("/session")[0], 503)
            time.sleep(2)
            self.assertIsNone(b.proc.poll(), "backend must not crash")
            self.assertEqual(b.request("/session")[1].get("error"), "auth_rejected", "the reason stays visible")
            self.assertIn("Login failed: {'error': 'auth_rejected'}", b.logtext(), "and in the log")
            self.assertEqual(b.logtext().count("Login failed"), 1, "said once, not at every try")
            self.assertEqual(sum("AuthenticateByName" in r for r in mock.requests), 1,
                             "a wrong password is tried only once, Jellyfin would lock the account")
            self.assertNotIn("password", json.loads((b.conf / "config.json").read_text()))
        finally:
            b.stop()
            mock.stop()

    def test_fresh_install_set_up_from_the_tv(self):
        """No config.json: the backend starts all the same and is set up with the server, user and password."""
        mock = MockJellyfin().start()
        b = Backend(mock.url)
        (b.conf / "config.json").unlink()
        b.start(wait_ready=False)
        try:
            self.assertEqual(b.request("/session"), (503, {"error": "setup", "server": "", "username": ""}))
            self.assertEqual(b.request("/status")[0], 200, "running")
            setup = {"server": mock.url, "username": USERNAME}
            self.assertEqual(b.request("/jellyfin/setup", {**setup, "password": "wrong"}), (400, {"error": "service_rejected"}))
            self.assertFalse((b.conf / "config.json").exists(), "nothing kept when the sign-in fails")
            self.assertEqual(b.request("/jellyfin/setup", {**setup, "server": "http://127.0.0.1:9", "password": PASSWORD}),
                             (400, {"error": "service_unreachable"}))
            self.assertEqual(b.request("/jellyfin/setup", {**setup, "password": PASSWORD})[0], 200)
            self.assertEqual(b.request("/session")[1]["userId"], "u1", "signed in at once")
            conf = json.loads((b.conf / "config.json").read_text())
            self.assertEqual((conf["server"], conf["username"]), (mock.url, USERNAME))
            self.assertNotIn(PASSWORD, (b.conf / "config.json").read_text() + (b.conf / "state.json").read_text())
            self.assertEqual(stat.S_IMODE(os.stat(b.conf / "config.json").st_mode), 0o600)
            self.assertEqual(b.request("/jf/UserViews?userId=u1")[0], 200)
            # a wrong password later, from the settings: the session there is stays
            self.assertEqual(b.request("/jellyfin/setup", {**setup, "password": "wrong"})[0], 400)
            time.sleep(2.5)
            self.assertEqual(b.request("/session")[0], 200, "not signed out by a wrong password")
            b.proc.terminate()
            b.proc.wait(5)
            b.start()  # and after a restart, from what was kept
        finally:
            b.stop()
            mock.stop()

    def test_switch_to_another_server(self):
        """Signed in to another server from the settings: the old one's live connection ends, the new one's opens."""
        first, other = MockJellyfin().start(), MockJellyfin().start()
        b = Backend(first.url).start()
        try:
            wait_for(lambda: len(first.ws_clients) == 1, msg="live connection to the first server")
            self.assertEqual(b.request("/jellyfin/setup", {"server": other.url, "username": USERNAME, "password": PASSWORD})[0], 200)
            wait_for(lambda: not first.ws_clients, timeout=15, msg="the old live connection ends")
            wait_for(lambda: len(other.ws_clients) == 1, timeout=15, msg="the new server's opens")
            self.assertEqual(b.request("/session")[1]["server"], other.url)
        finally:
            b.stop()
            first.stop()
            other.stop()

    def test_no_network_at_all(self):
        """A server whose name does not resolve, and no other name either: the sign-in says the network is missing."""
        b = Backend("http://jellyfin.invalid:8096", env={"KINO_NET_CHECK_HOST": "nothing.invalid"}).start(wait_ready=False)
        try:
            wait_for(lambda: b.request("/session")[1].get("error") == "unreachable", msg="error code unreachable")
            self.assertIs(b.request("/session")[1].get("network"), False)
        finally:
            b.stop()
        b = Backend(f"http://127.0.0.1:{free_port()}").start(wait_ready=False)
        try:
            wait_for(lambda: b.request("/session")[1].get("error") == "unreachable", msg="error code unreachable")
            self.assertNotIn("network", b.request("/session")[1], "only the server gone: not the network")
        finally:
            b.stop()

    def test_server_reachable_only_later(self):
        port = free_port()
        b = Backend(f"http://127.0.0.1:{port}").start(wait_ready=False)
        mock = MockJellyfin()
        try:
            wait_for(lambda: b.request("/session")[1].get("error") == "unreachable", msg="error code unreachable")
            mock.start(port)
            wait_for(lambda: b.request("/session")[0] == 200, timeout=15, msg="login once the server is up")
            log = b.logtext()
            self.assertEqual(log.count("Login failed"), 1, "the tries before said once, not each")
            self.assertIn("Logged in after", log, "and when it answered")
        finally:
            b.stop()
            if getattr(mock, "httpd", None):  # started
                mock.stop()

    def test_half_written_files_after_a_power_cut(self):
        """config.json empty and state.json cut off (power gone while writing): Matinee starts all the same, from the
        copies from before the last change, still signed in; the broken ones are kept aside."""
        mock = MockJellyfin().start()
        b = Backend(mock.url, password=None)
        good_conf = (b.conf / "config.json").read_text()
        (b.conf / "config.json.bak").write_text(good_conf)
        (b.conf / "state.json.bak").write_text(json.dumps({"device_id": "d1", "token": TOKEN, "user_id": "u1"}))
        (b.conf / "config.json").write_text("")
        (b.conf / "state.json").write_text('{"device_id": "d1", "tok')
        try:
            b.start()  # signed in with the token kept: no password anywhere
            self.assertEqual(b.request("/session")[1]["userId"], "u1")
            self.assertEqual(json.loads((b.conf / "config.json").read_text()), json.loads(good_conf))
            self.assertEqual((b.conf / "config.json.broken").read_text(), "")
            self.assertTrue((b.conf / "state.json.broken").exists())
            self.assertEqual(json.loads((b.conf / "state.json").read_text())["device_id"], "d1", "the same device to Jellyfin")
        finally:
            b.stop()
            mock.stop()

    def test_a_file_that_cannot_be_read_stays_where_it_is(self):
        """config.json without the right to read it, its contents fine: Matinee starts from its copy from before, the
        file itself left where it is rather than put aside as broken."""
        mock = MockJellyfin().start()
        b = Backend(mock.url)
        conf = b.conf / "config.json"
        (b.conf / "config.json.bak").write_text(conf.read_text())
        conf.chmod(0)
        try:
            b.start()
            self.assertEqual(b.request("/session")[0], 200, "signed in with what the copy holds")
            self.assertTrue(conf.exists())
            self.assertFalse((b.conf / "config.json.broken").exists(), "not taken for broken")
        finally:
            conf.chmod(0o600)
            b.stop()
            mock.stop()

    def test_broken_files_without_copies(self):
        """Nothing to fall back on: Matinee starts as freshly installed and asks for the server, rather than not at all."""
        mock = MockJellyfin().start()
        b = Backend(mock.url)
        (b.conf / "config.json").write_text('{"server": ')
        (b.conf / "state.json").write_bytes(b"\0" * 64)
        try:
            b.start(wait_ready=False)
            self.assertEqual(b.request("/session")[1].get("error"), "setup")
            self.assertIsNone(b.proc.poll(), "running")
        finally:
            b.stop()
            mock.stop()

    def test_changes_keep_the_version_before(self):
        mock = MockJellyfin().start()
        b = Backend(mock.url).start()
        try:
            before = json.loads((b.conf / "config.json").read_text())
            self.assertEqual(b.request("/settings", {"subsize": "l"})[0], 200)
            self.assertEqual(json.loads((b.conf / "config.json.bak").read_text()), before)
            self.assertEqual(json.loads((b.conf / "config.json").read_text())["subsize"], "l")
            self.assertEqual(stat.S_IMODE(os.stat(b.conf / "config.json.bak").st_mode), 0o600, "as private as the file")
            self.assertFalse(list(b.conf.glob("*.tmp")), "nothing half done left")
        finally:
            b.stop()
            mock.stop()

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("hdiutil"), "a full disk of its own: macOS only")
    def test_full_disk(self):
        """The disk full: a change answers that it could not be kept (the page says so), config.json stays whole,
        the backend runs on."""
        mock = MockJellyfin().start()
        b = Backend(mock.url)
        dev = subprocess.run(["hdiutil", "attach", "-nomount", "ram://4096"], capture_output=True, text=True).stdout.split()[0]
        mnt = b.tmp / "full"
        mnt.mkdir()
        try:
            subprocess.run(["newfs_hfs", "-v", "kinotest", dev], check=True, capture_output=True)
            subprocess.run(["mount", "-t", "hfs", dev, str(mnt)], check=True)
            conf = mnt / "kino"
            shutil.copytree(b.conf, conf)
            b.env["XDG_CONFIG_HOME"] = str(mnt)
            b.conf = conf
            b.start()
            before = (conf / "config.json").read_text()
            for i in range(10_000):  # every last block taken
                try:
                    (mnt / f"fill{i}").write_bytes(b"\0" * (65536 if i < 64 else 512))
                except OSError:
                    if i >= 64:
                        break
            self.assertEqual(b.request("/settings", {"subsize": "l"}), (507, {"error": "disk_full"}))
            self.assertEqual((conf / "config.json").read_text(), before)
            self.assertEqual(b.request("/session")[0], 200, "running on")
        finally:
            b.stop()
            mock.stop()
            subprocess.run(["umount", str(mnt)], capture_output=True)
            subprocess.run(["hdiutil", "detach", dev], capture_output=True)
            shutil.rmtree(b.tmp, ignore_errors=True)  # stop() could not: the disk was still mounted in it

    def test_server_shown_there_through_a_busy_moment(self):
        """The device's panel: a server whose first answer is late (the line busy with a film) and that answers with a
        redirect to somewhere unreachable is shown as there; one that never answers as gone."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []

        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                if len(seen) == 1:
                    time.sleep(2)  # past the backend's patience
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/app/")
                self.end_headers()

            def log_message(self, *_):
                pass
        slow = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=slow.serve_forever, daemon=True).start()
        mock = MockJellyfin().start()
        b = Backend(mock.url, env={"KINO_REACH_TIMEOUT": "1"},
                    conf={"navidrome": {"server": f"http://127.0.0.1:{slow.server_port}", "username": "me"},
                          "audiobookshelf": {"server": f"http://127.0.0.1:{free_port()}"}}).start()
        try:
            servers = {s["name"]: s["ok"] for s in b.request("/overview")[1]["servers"]}
            self.assertEqual(servers, {"Jellyfin": True, "Navidrome": True, "Audiobookshelf": False})
            self.assertEqual(len(seen), 2, "asked once more, the redirect not followed")
        finally:
            b.stop()
            mock.stop()
            slow.shutdown()
            slow.server_close()

    def test_expired_token_leads_to_new_login(self):
        mock = MockJellyfin().start()
        b = Backend(mock.url)
        (b.conf / "state.json").write_text(json.dumps({"device_id": "d", "token": "expired", "user_id": "u1"}))
        try:
            b.start()
            self.assertIn(TOKEN, (b.conf / "state.json").read_text())
        finally:
            b.stop()
            mock.stop()


class MeasureTest(unittest.TestCase):
    """Measuring the line before playback: short on a fast line, the full time on a slow one."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="kino-measure-")
        cls.big = os.path.join(cls.tmp, "big.mkv")
        with open(cls.big, "wb") as f:
            f.write(os.urandom(30_000_000))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def measure(self, rate, enough, burst=0):
        mock = MockJellyfin(video_path=self.big).start()
        mock.stream_rate = rate
        mock.stream_burst = burst
        b = Backend(mock.url)
        try:
            code = ("import sys, time; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); from backend import playback; "
                    "t = time.monotonic(); r = playback.measure_bandwidth(sys.argv[2], 0, enough=float(sys.argv[3]) or None); "
                    "print(r, time.monotonic() - t)")
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            out = subprocess.run([sys.executable, "-c", code, server_dir, f"{mock.url}/Videos/m1/stream?static=true&ApiKey={TOKEN}",
                                  str(enough)], env=b.env, capture_output=True, text=True, timeout=30)
            rate_bits, seconds = map(float, out.stdout.split())
            return rate_bits, seconds
        finally:
            b.stop()
            mock.stop()

    def test_fast_line_stops_early(self):
        bits, seconds = self.measure(rate=4_000_000, enough=8_000_000)
        self.assertGreater(bits, 8_000_000)
        self.assertLess(seconds, 1.5, "no need to wait the full time when the line is clearly fast enough")

    def test_slow_line_is_measured_fully(self):
        bits, seconds = self.measure(rate=200_000, enough=8_000_000)
        self.assertLess(bits, 3_000_000)
        self.assertGreater(seconds, 1.8)

    def test_burst_at_the_start_does_not_count(self):
        # 1.25 MB/s (10 Mbit/s) after 2 MB at once: before, that measured over 40 Mbit/s and a file of 16 played as it is
        bits, seconds = self.measure(rate=1_250_000, enough=32_000_000, burst=2_000_000)
        self.assertLess(bits, 13_000_000, "the line's steady speed, not the burst")
        self.assertGreater(seconds, 1.8, "no early stop because of the burst")


class KeptConnectionTest(unittest.TestCase):
    def test_connection_closed_by_the_server_is_replaced(self):
        mock = MockJellyfin()
        mock.idle_timeout = 0.5  # like Jellyfin, which closes idle connections after a while
        mock.start()
        b = Backend(mock.url).start()
        try:
            self.assertEqual(b.request("/jf/UserViews?userId=u1")[0], 200)
            time.sleep(1.2)
            self.assertEqual(b.request("/jf/UserViews?userId=u1")[0], 200, "no error for the user")
            self.assertEqual(b.request("/played", {"itemId": "m2", "played": True})[0], 200)
            time.sleep(1.2)
            self.assertEqual(b.request("/played", {"itemId": "m2", "played": False})[0], 200, "also for commands")
            self.assertFalse(mock.lib.userdata["m2"]["Played"])
        finally:
            b.stop()
            mock.stop()


class BluetoothTest(unittest.TestCase):
    """Speakers and headphones in the settings, against a fake bluetoothctl."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kino-bt-"))
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "bluetoothctl").symlink_to(ROOT / "tests" / "fake-bluetoothctl")
        (self.tmp / "bin" / "journalctl").symlink_to(ROOT / "tests" / "fake-journalctl")
        # the headphones, switched off: BlueZ cannot reach them
        (self.tmp / "journal.txt").write_text("profiles/audio/avdtp.c:avdtp_connect_cb() connect to 34:0E:22:00:00:01: Host is down (112)\n")
        self.state = self.tmp / "bt.json"
        self.log = self.tmp / "bt.log"
        self.write({"devices": {
            "34:0E:22:00:00:01": {"name": "Headphones", "icon": "audio-headphones", "known": True, "paired": True, "trusted": True,
                                  "away": True},
            "50:F5:DA:00:00:02": {"name": "Remote", "known": True, "paired": True, "trusted": True, "connected": True},
            "11:22:33:00:00:03": {"name": "Box", "icon": "audio-card", "nearby": True, "blank": True},
            "11:22:33:00:00:04": {"name": "11-22-33-00-00-04", "nearby": True},
            "11:22:33:00:00:05": {"name": "Stubborn", "icon": "audio-card", "nearby": True, "refuses": True},
            "44:16:22:00:00:06": {"name": "Xbox Wireless Controller", "icon": "input-gaming", "nearby": True, "confirm": True}}})
        self.mock = MockJellyfin().start()
        self.env = {"PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin", "FAKE_BT_STATE": str(self.state), "FAKE_BT_LOG": str(self.log),
                    "FAKE_JOURNAL": str(self.tmp / "journal.txt")}
        self.b = Backend(self.mock.url, env=self.env).start()

    def tearDown(self):
        self.b.stop()
        self.mock.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_computer_without_bluetooth_does_not_hold_up_the_settings(self):
        """Without the Bluetooth service bluetoothctl waits for it without end: asked briefly, and not again so soon."""
        self.b.stop()
        (self.tmp / "bin" / "bluetoothctl").unlink()
        (self.tmp / "bin" / "bluetoothctl").write_text("#!/bin/sh\nexec sleep 30\n")
        (self.tmp / "bin" / "bluetoothctl").chmod(0o755)
        self.b = Backend(self.mock.url, env={**self.env, "KINO_BT_ASK": "0.5"}).start()
        began = time.monotonic()
        self.assertIs(self.b.request("/settings")[1]["bluetooth"], False)
        first = time.monotonic() - began
        self.assertLess(first, 4, "not the 10 s it waited before")
        began = time.monotonic()
        self.assertIs(self.b.request("/settings")[1]["bluetooth"], False)
        self.assertLess(time.monotonic() - began, max(0.4, first - 0.3), "not asked again at once")
        began = time.monotonic()
        self.assertIsNone(self.b.request("/overview")[1]["bluetooth"], "nor does the main menu's panel wait for it")
        self.assertEqual(self.b.request("/bt/devices")[1], {"available": False, "devices": []})
        self.assertLess(time.monotonic() - began, 4)

    def write(self, st):
        self.state.write_text(json.dumps(st))

    def devices(self):
        return self.b.request("/bt/devices")[1]

    def test_lists_scans_connects_and_forgets(self):
        d = self.devices()
        self.assertEqual((d["available"], d["powered"], d["scanning"]), (True, True, False))
        self.assertEqual([(x["name"], x["paired"], x["connected"]) for x in d["devices"]],
                         [("Remote", True, True), ("Headphones", True, False)], "connected first")
        self.assertEqual([x["unreachable"] for x in d["devices"]], [False, True], "BlueZ cannot reach the headphones")
        self.assertEqual(self.b.request("/bt/scan", {})[0], 200)
        wait_for(lambda: len(self.devices()["devices"]) == 5, timeout=20, msg="the ones nearby show up")  # the fake scan: slow when busy
        names = [x["name"] for x in self.devices()["devices"]]
        self.assertEqual(names[2:], ["Box", "Stubborn", "Xbox Wireless Controller"], "nameless ones are left out")
        self.assertTrue(self.devices()["scanning"])
        # a new one: paired, trusted (so it comes back by itself) and connected
        r = self.b.request("/bt/action", {"mac": "11:22:33:00:00:03", "action": "connect"})
        self.assertEqual(r[0], 200)
        box = next(x for x in r[1]["devices"] if x["name"] == "Box")
        self.assertEqual((box["paired"], box["connected"]), (True, True))
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIn(["pair", "11:22:33:00:00:03"], calls)
        self.assertIn(["trust", "11:22:33:00:00:03"], calls)
        self.assertEqual(self.b.request("/bt/action", {"mac": "11:22:33:00:00:05", "action": "connect"})[0], 502,
                         "one that will not pair")
        # one that asks to confirm the pairing (an Xbox controller): only with an agent, which bluetoothctl registers
        # only for commands on stdin
        r = self.b.request("/bt/action", {"mac": "44:16:22:00:00:06", "action": "connect"})
        self.assertEqual(r[0], 200, "the controller confirmed and paired")
        pad = next(x for x in r[1]["devices"] if x["mac"] == "44:16:22:00:00:06")
        self.assertEqual((pad["paired"], pad["connected"]), (True, True))
        self.assertEqual(self.b.request("/bt/action", {"mac": "11:22:33:00:00:03", "action": "disconnect"})[0], 200)
        self.assertEqual(self.b.request("/bt/action", {"mac": "34:0E:22:00:00:01", "action": "remove"})[0], 200)
        self.assertNotIn("Headphones", [x["name"] for x in self.devices()["devices"]], "forgotten")

    def test_speakers_paired_before_are_connected_at_the_start(self):
        self.b.stop()
        st = json.loads(self.state.read_text())
        st["devices"]["34:0E:22:00:00:01"]["away"] = False  # switched on, but its link dropped with Matinee's restart
        self.write(st)
        self.b = Backend(self.mock.url, env=self.env).start()
        wait_for(lambda: self.devices()["devices"][0]["connected"] and self.devices()["devices"][1]["connected"],
                 msg="headphones connected again")
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertNotIn(["connect", "50:F5:DA:00:00:02"], calls, "only sound devices, and only those not connected")

    def test_one_speaker_at_a_time(self):
        """The sound plays on one: headphones connected let go of the box connected before; the remote stays."""
        st = json.loads(self.state.read_text())
        st["devices"]["34:0E:22:00:00:01"]["away"] = False
        st["devices"]["11:22:33:00:00:03"].update(known=True, paired=True, trusted=True, connected=True)
        self.write(st)
        r = self.b.request("/bt/action", {"mac": "34:0E:22:00:00:01", "action": "connect"})
        self.assertEqual(r[0], 200)
        self.assertEqual({x["name"]: x["connected"] for x in r[1]["devices"]}, {"Headphones": True, "Remote": True, "Box": False})
        self.assertTrue(next(x for x in r[1]["devices"] if x["name"] == "Box")["paired"], "the box stays paired")
        # one that does not answer: the one playing stays
        st = json.loads(self.state.read_text())
        st["devices"]["11:22:33:00:00:03"]["away"] = True
        self.write(st)
        self.assertEqual(self.b.request("/bt/action", {"mac": "11:22:33:00:00:03", "action": "connect"})[0], 502)
        self.assertTrue(next(x for x in self.devices()["devices"] if x["name"] == "Headphones")["connected"])

    def test_the_one_chosen_last_comes_back_at_the_start(self):
        self.b.stop()
        st = json.loads(self.state.read_text())
        st["devices"]["34:0E:22:00:00:01"]["away"] = False
        st["devices"]["11:22:33:00:00:03"].update(known=True, paired=True, trusted=True)
        self.write(st)
        self.b = Backend(self.mock.url, env=self.env)
        self.b.conf.mkdir(parents=True, exist_ok=True)
        (self.b.conf / "bt-audio").write_text("11:22:33:00:00:03\n")
        self.b.start()
        wait_for(lambda: next(x for x in self.devices()["devices"] if x["name"] == "Box")["connected"], msg="the box again")
        time.sleep(1)
        self.assertFalse(next(x for x in self.devices()["devices"] if x["name"] == "Headphones")["connected"],
                         "not the headphones as well")

    def test_rejects_bad_requests_and_no_adapter(self):
        self.assertEqual(self.b.request("/bt/action", {"mac": "34:0E:22:00:00:01; reboot", "action": "connect"})[0], 400)
        self.assertEqual(self.b.request("/bt/action", {"mac": "34:0E:22:00:00:01", "action": "pair-all"})[0], 400)
        self.write({"controller": False, "devices": {}})
        self.assertEqual(self.devices(), {"available": False, "devices": []}, "no adapter, as on the thin client now")


# Switching a screen's mode (display.switch_mode) in a process of its own: wlr-randr missing, and wlr-randr hanging
SWITCH_PROBE = r"""
import json, os, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import display

mode, out = {"width": 1920, "height": 1080, "refresh": 60.0}, {}
os.environ["PATH"] = sys.argv[2]  # a folder without wlr-randr
out["missing"] = display.switch_mode("HDMI-A-1", mode)

def hangs(args, **kw):
    raise subprocess.TimeoutExpired(args, kw.get("timeout"))
subprocess.run = hangs
out["hangs"] = display.switch_mode("HDMI-A-1", mode)
print(json.dumps(out))
"""


class DisplaySwitchTest(unittest.TestCase):
    def test_a_missing_or_hanging_wlr_randr_is_said_not_raised(self):
        with tempfile.TemporaryDirectory() as home:
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            r = subprocess.run([sys.executable, "-c", SWITCH_PROBE, server_dir, home], capture_output=True, text=True,
                               timeout=30, env={**os.environ, "XDG_CONFIG_HOME": home, "XDG_RUNTIME_DIR": home})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(json.loads(r.stdout.splitlines()[-1]), {"missing": False, "hangs": False})
        self.assertIn("not switched", r.stderr, "said in the log")


class DisplaySettingTest(unittest.TestCase):
    """The display mode in the settings: only in cage (kino --session), against a fake wlr-randr."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kino-display-"))
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "wlr-randr").symlink_to(ROOT / "tests" / "fake-wlr-randr")
        self.log = self.tmp / "randr.log"
        self.env = {"PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin", "FAKE_RANDR_STATE": str(self.tmp / "screen.json"),
                    "FAKE_RANDR_LOG": str(self.log)}
        self.mock = MockJellyfin().start()

    def tearDown(self):
        self.mock.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def modes_set(self):
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [c[c.index("--mode") + 1] for c in calls if "--mode" in c]

    def test_a_films_refresh_rate_put_back_at_the_start(self):
        """Matinee ended while a 24p film played at 23.976 Hz: its next start puts the screen back in the mode the
        settings want (auto: here the highest refresh rate at 1080p)."""
        subprocess.run([str(ROOT / "tests" / "fake-wlr-randr"), "--json"], env={**os.environ, **self.env}, capture_output=True, check=True)
        state = json.loads((self.tmp / "screen.json").read_text())
        for m in state[0]["modes"]:
            m["current"] = (m["width"], m["height"], m["refresh"]) == (1920, 1080, 23.976)
        (self.tmp / "screen.json").write_text(json.dumps(state))
        self.log.unlink()
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1"}).start()
        try:
            wait_for(lambda: self.modes_set() == ["1920x1080@120.000Hz"], timeout=5, msg="put back")
        finally:
            b.stop()

    def test_in_cage(self):
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1"}).start()
        try:
            # at the start: the mode for "auto" (display_back), the fake screen being at 4K and 30 Hz
            wait_for(lambda: self.modes_set() == ["1920x1080@120.000Hz"], timeout=5, msg="the mode for auto at the start")
            s = b.request("/settings")[1]
            self.assertEqual(s["display"], "auto")
            self.assertEqual(s["displayModes"], ["3840x2160@30", "2560x1440@60", "1920x1080@120", "1920x1080@60",
                                                 "1920x1080@50", "1280x720@60"], "the screen's own, then 16:9 from 720p at 50 Hz")
            self.assertEqual(b.request("/settings", {"display": "1920x1080@60"})[0], 200)
            self.assertEqual(self.modes_set()[1:], ["1920x1080@60.000Hz"], "switched at once")
            self.assertEqual(json.loads((b.conf / "config.json").read_text())["display"], "1920x1080@60")
            self.assertEqual(b.request("/settings", {"display": "auto"})[0], 200)
            self.assertEqual(self.modes_set()[-1], "1920x1080@120.000Hz", "auto: 120 Hz instead of 4K at 30")
            self.assertEqual(b.request("/settings", {"display": "auto"})[0], 200)
            self.assertEqual(len(self.modes_set()), 3, "already in that mode: left alone")
            self.assertEqual(b.request("/settings", {"display": "native"})[0], 200)
            self.assertEqual(self.modes_set()[-1], "3840x2160@30.000Hz")
            self.assertEqual(b.request("/settings", {"display": "5120x2880@60"})[0], 400, "not a mode of the screen")
        finally:
            b.stop()

    def fake_systemctl(self):
        log = self.tmp / "systemctl.log"
        (self.tmp / "bin" / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
        (self.tmp / "bin" / "systemctl").chmod(0o755)
        return log

    def test_restart_and_shut_down(self):
        log = self.fake_systemctl()
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1"}).start()
        try:
            self.assertTrue(b.request("/settings")[1]["system"])
            self.assertEqual(b.request("/system", {"action": "reboot"})[0], 200)
            wait_for(lambda: log.exists() and log.read_text() == "reboot\n", msg="systemctl reboot")
            self.assertEqual(b.request("/system", {"action": "poweroff"})[0], 200)
            wait_for(lambda: log.read_text() == "reboot\npoweroff\n", msg="systemctl poweroff")
            self.assertEqual(b.request("/system", {"action": "halt"})[0], 403)
        finally:
            b.stop()

    def git_repos(self):
        """An origin, Matinee's checkout of it and another clone that pushes new versions; returns run, repo, other."""
        run = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True,
                                                  env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                                                           GIT_COMMITTER_EMAIL="t@t"))
        origin, repo, other = self.tmp / "origin.git", self.tmp / "repo", self.tmp / "other"
        run("init", "--bare", "-q", "-b", "main", str(origin))
        run("clone", "-q", str(origin), str(other))
        (other / "a").write_text("1")
        run("add", "a", cwd=other); run("commit", "-qm", "one", cwd=other); run("push", "-q", "origin", "main", cwd=other)
        run("clone", "-q", str(origin), str(repo))  # Matinee's checkout
        return run, repo, other

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_software_update(self):
        run, repo, other = self.git_repos()
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1", "KINO_ALIVE_FIRST": "600", "KINO_REPO": str(repo)}).start()
        try:
            self.assertTrue(b.request("/settings")[1]["updates"])
            wait_for(lambda: [n for _q, n, _c in self.mock.ws_devices.values()] == ["TV"],
                     msg="the TV computer known to Jellyfin as the TV, not by its host's name (as another Matinee offers it)")
            wp = b.tmp / "config" / "wireplumber" / "wireplumber.conf.d" / "51-kino-bluetooth.conf"
            wait_for(wp.exists, msg="Bluetooth in stereo only, put in place by Matinee on a TV computer")
            self.assertIn("bluez5.roles = [ a2dp_sink a2dp_source", wp.read_text())
            self.assertEqual([c["s"] for c in b.request("/settings")[1]["updateRecent"]], ["one"], "the patch notes, before any look")
            self.assertEqual(b.request("/update/check", {})[1]["behind"], 0, "up to date")
            (other / "a").write_text("2")
            run("commit", "-qam", "two", cwd=other); run("push", "-q", "origin", "main", cwd=other)
            r = b.request("/update/check", {})[1]
            self.assertEqual(r["behind"], 1, "one new version")
            self.assertEqual([c["s"] for c in r["changes"]], ["two"], "what it changes, in its own words")
            self.assertTrue(r["at"] and r["installed"] and r["checked"], "when each was made, and when it was looked")
            self.assertEqual(b.request("/settings")[1]["update"]["changes"], r["changes"], "the settings have it at once")
            (other / "a").write_text("3")
            run("commit", "-qam", "three\n\nWhy it was done.\n\nNote-de: drei", cwd=other); run("push", "-q", "origin", "main", cwd=other)
            self.assertEqual([c["s"] for c in b.request("/update/check", {})[1]["changes"]], ["three", "two"],
                             "in English the subject, not the German note")
            self.assertEqual(b.request("/settings", {"language": "de"})[0], 200)
            self.assertEqual([c["s"] for c in b.request("/settings")[1]["update"]["changes"]], ["drei", "two"],
                             "the last look's, in the language switched to since")
            self.assertEqual([c["s"] for c in b.request("/update/check", {})[1]["changes"]], ["drei", "two"],
                             "in German the note where there is one, else the subject")
            self.assertEqual(b.request("/update/install", {})[0], 200)
            self.assertEqual((repo / "a").read_text(), "3", "pulled")
            wait_for(lambda: b.proc.poll() is not None, msg="Matinee ends, for systemd to start it again")
        finally:
            b.stop()

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_software_update_on_probation(self):
        """The new version is on probation until its page has said twice that it is there; one that went back by
        itself (kino --session, "bad") is not offered again until a newer one comes."""
        run, repo, other = self.git_repos()
        head = lambda: run("rev-parse", "HEAD", cwd=repo).stdout.strip()
        env = {**self.env, "KINO_SESSION": "1", "KINO_ALIVE_FIRST": "600", "KINO_REPO": str(repo)}
        first = head()
        (other / "a").write_text("2")
        run("commit", "-qam", "two", cwd=other); run("push", "-q", "origin", "main", cwd=other)
        b = Backend(self.mock.url, env=env).start()
        try:
            self.assertEqual(b.request("/update/install", {})[0], 200)
            wait_for(lambda: b.proc.poll() is not None, msg="Matinee ends, for systemd to start it again")
        finally:
            b.stop()
        probation = repo / ".git" / "kino-update"
        self.assertEqual([(probation / n).read_text().strip() for n in ("from", "to")], [first, head()])
        b = Backend(self.mock.url, env=env).start()
        try:
            wait_for(lambda: "on probation" in b.logtext(), msg="the new version on probation")
            b.request("/alive", {})
            self.assertTrue((probation / "to").exists(), "not after the page's first word")
            b.request("/alive", {})
            self.assertFalse((probation / "to").exists(), "passed")
            self.assertIn("probation over", b.logtext())
            # a newer one that went back by itself: not offered again
            (other / "a").write_text("3")
            run("commit", "-qam", "three", cwd=other); run("push", "-q", "origin", "main", cwd=other)
            run("fetch", "-q", cwd=repo)
            (probation / "bad").write_text(run("rev-parse", "origin/main", cwd=repo).stdout)
            self.assertEqual(b.request("/update/check", {})[1]["behind"], 0, "the bad one is not offered")
            self.assertEqual(b.request("/update/install", {})[1], {"error": "pull"}, "nor installed")
            self.assertEqual((repo / "a").read_text(), "2")
            # a history rewritten upstream: the TV computer follows it all the same (a pull would refuse)
            run("reset", "-q", "--hard", "HEAD~1", cwd=other)
            (other / "a").write_text("4")
            run("commit", "-qam", "four", cwd=other); run("push", "-q", "-f", "origin", "main", cwd=other)
            self.assertEqual(b.request("/update/check", {})[1]["behind"], 1)
            self.assertEqual(b.request("/update/install", {})[0], 200)
            self.assertEqual((repo / "a").read_text(), "4")
        finally:
            b.stop()

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_software_update_on_a_mac(self):
        run, repo, other = self.git_repos()
        b = Backend(self.mock.url, env={**self.env, "KINO_DESKTOP_UPDATES": "1", "KINO_REPO": str(repo)}).start()
        try:
            (repo / "a").write_text("mine")  # a change of its own: nothing installed over it
            s = b.request("/settings")[1]
            self.assertEqual((s["updates"], s["updateInstall"]), (True, False))
            self.assertEqual(b.request("/update/install", {})[0], 403)
            run("checkout", "a", cwd=repo)
            self.assertTrue(b.request("/settings")[1]["updateInstall"], "a clean checkout")
            (other / "a").write_text("2")
            run("commit", "-qam", "two", cwd=other); run("push", "-q", "origin", "main", cwd=other)
            self.assertEqual(b.request("/update/check", {})[1]["behind"], 1)
            old = b.cookie
            self.assertEqual(b.request("/update/install", {})[0], 200)
            self.assertEqual((repo / "a").read_text(), "2", "pulled")
            time.sleep(3)
            self.assertIsNone(b.proc.poll(), "the backend starts again in its own place, Matinee.app keeps it")
            b.cookie = ""  # a new backend, a new secret
            wait_for(lambda: b.request("/status")[0] == 200, msg="answering again")
            self.assertNotEqual(b.cookie, old)
        finally:
            b.stop()

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_desktop_follows_a_history_rewritten_upstream(self):
        """The server's history rewritten (the same commits with new ids, as when a detail is taken out of old ones):
        a pull refuses it for ever, though nothing here is the checkout's own; a commit amended here stays its own."""
        run, repo, other = self.git_repos()
        dated = lambda when, *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True,
                                                     env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                                              GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                                                              GIT_COMMITTER_DATE=f"{when} +0000"))
        (other / "a").write_text("2")
        dated(1_700_000_000, "commit", "-qam", "two", cwd=other)
        run("push", "-q", "origin", "main", cwd=other)
        run("pull", "-q", "--ff-only", cwd=repo)
        # the server's: "two" rewritten later, its author date and subject kept; a new one after it
        (other / "a").write_text("2 rewritten")
        dated(1_700_000_100, "commit", "-qa", "--amend", "--no-edit", cwd=other)
        (other / "b").write_text("new")
        run("add", "b", cwd=other); run("commit", "-qm", "three", cwd=other)
        run("push", "-q", "-f", "origin", "main", cwd=other)
        b = Backend(self.mock.url, env={**self.env, "KINO_DESKTOP_UPDATES": "1", "KINO_REPO": str(repo)}).start()
        try:
            self.assertEqual(b.request("/update/check", {})[1]["behind"], 2)
            self.assertTrue(b.request("/settings")[1]["updateInstall"], "nothing of its own: it may be installed")
            self.assertEqual(b.request("/update/install", {})[0], 200)
            self.assertEqual(((repo / "a").read_text(), (repo / "b").read_text()), ("2 rewritten", "new"), "followed")
        finally:
            b.stop()
        # amended here, after the server's: the checkout's own, never installed over
        (repo / "a").write_text("mine")
        dated(int(time.time()) + 600, "commit", "-qa", "--amend", "--no-edit", cwd=repo)
        b = Backend(self.mock.url, env={**self.env, "KINO_DESKTOP_UPDATES": "1", "KINO_REPO": str(repo)}).start()
        try:
            self.assertFalse(b.request("/settings")[1]["updateInstall"], "a commit of its own")
            self.assertEqual(b.request("/update/install", {})[0], 403)
            self.assertEqual((repo / "a").read_text(), "mine")
        finally:
            b.stop()

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_desktop_updates_itself_when_not_in_use(self):
        run, repo, other = self.git_repos()
        b = Backend(self.mock.url, env={**self.env, "KINO_DESKTOP_UPDATES": "1", "KINO_REPO": str(repo),
                                        "KINO_AUTOUPDATE_EVERY": "0.5", "KINO_AUTOUPDATE_QUIET": "0",
                                        "KINO_AUTOUPDATE_LOOK": "1"}).start()
        try:
            self.assertEqual(b.request("/settings")[1]["autoupdate"], "on", "on by itself on a desktop")
            (other / "a").write_text("2")
            run("commit", "-qam", "two", cwd=other); run("push", "-q", "origin", "main", cwd=other)
            wait_for(lambda: (repo / "a").read_text() == "2", timeout=10, msg="pulled by itself")
            b.cookie = ""  # started again: a new secret
            wait_for(lambda: b.request("/status")[0] == 200, msg="answering again")
            self.assertEqual(b.request("/settings", {"autoupdate": "off"})[0], 200)
            (repo / "a").write_text("mine")  # and a change of its own
            (other / "a").write_text("3")
            run("commit", "-qam", "three", cwd=other); run("push", "-q", "origin", "main", cwd=other)
            time.sleep(2)
            self.assertEqual((repo / "a").read_text(), "mine", "off, and never over a change of its own")
        finally:
            b.stop()

    def test_no_software_update_on_a_desktop(self):
        b = Backend(self.mock.url, env={**self.env, "KINO_REPO": str(self.tmp)}).start()  # not under kino --session
        try:
            self.assertFalse(b.request("/settings")[1]["updateInstall"])
            self.assertEqual(b.request("/update/install", {})[0], 403, "a working copy is not Matinee's to update")
        finally:
            b.stop()

    def test_a_remotes_menu_key_reaches_the_page(self):
        """Chromium drops KEY_MENU: the backend reads it from the remote itself and tells the page."""
        width = struct.calcsize("l") * 2

        def key_bits(*codes):  # as the kernel lists them: words of a long, the highest first, without their zeros
            h = format(sum(1 << c for c in codes), "x")
            h = h.rjust(-(-len(h) // width) * width, "0")
            return " ".join(h[i:i + width].lstrip("0") or "0" for i in range(0, len(h), width))
        inp = self.tmp / "input"
        inp.mkdir()
        devices = self.tmp / "devices"
        devices.write_text(f'I: Bus=0005 Vendor=0001 Product=0002 Version=0001\nN: Name="Made-up Remote"\n'
                           f"H: Handlers=kbd event7\nB: KEY={key_bits(103, 108, 139, 158)}\n\n"
                           f'I: Bus=0003 Vendor=0003 Product=0004 Version=0001\nN: Name="Made-up Keyboard"\n'
                           f"H: Handlers=sysrq kbd event3 leds\nB: KEY={key_bits(30, 127)}\n\n")
        os.mkfifo(inp / "event7")
        remote = os.open(inp / "event7", os.O_RDWR)  # the remote, there before the backend opens it
        self.addCleanup(os.close, remote)
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1", "KINO_ALIVE_FIRST": "600",
                                         "KINO_INPUT_DEVICES": str(devices), "KINO_INPUT_DIR": str(inp)}).start()
        sse = SSE(b.port, b.page_cookie())
        try:
            time.sleep(1.5)  # the devices looked through
            press = lambda code: struct.pack("llHHi", 0, 0, 1, code, 1) + struct.pack("llHHi", 0, 0, 1, code, 0)
            os.write(remote, press(158) + press(139))
            wait_for(lambda: "menukey" in sse.events, msg="the page told of the Menu key")
            time.sleep(0.5)
            self.assertEqual(sse.events.count("menukey"), 1, "once a press, its release and Back not")
        finally:
            sse.close()
            b.stop()

    def test_a_game_controllers_buttons_are_the_remotes_keys(self):
        """An Xbox controller: its buttons, D-pad and stick reach the page as the remote's keys, pressed and let go
        apart (a held OK opens the options), a held direction again and again."""
        width = struct.calcsize("l") * 2

        def bits(*codes):
            h = format(sum(1 << c for c in codes), "x")
            h = h.rjust(-(-len(h) // width) * width, "0")
            return " ".join(h[i:i + width].lstrip("0") or "0" for i in range(0, len(h), width))
        inp = self.tmp / "input"
        inp.mkdir()
        devices = self.tmp / "devices"
        devices.write_text(f'I: Bus=0003 Vendor=0005 Product=0006 Version=0001\nN: Name="Made-up Controller"\n'
                           f"H: Handlers=event9 js0\nB: EV=20000b\nB: KEY={bits(304, 305, 307, 308, 310, 311, 314, 315, 316)}\n"
                           f"B: ABS={bits(0, 1, 2, 3, 4, 5, 16, 17)}\n\n")
        os.mkfifo(inp / "event9")
        pad = os.open(inp / "event9", os.O_RDWR)
        self.addCleanup(os.close, pad)
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1", "KINO_ALIVE_FIRST": "600",
                                         "KINO_INPUT_DEVICES": str(devices), "KINO_INPUT_DIR": str(inp)}).start()
        sse = SSE(b.port, b.page_cookie())
        ev = lambda kind, code, value: struct.pack("llHHi", 0, 0, kind, code, value)
        keys = lambda: [json.loads(e[7:]) for e in sse.events if e.startswith("remote:")]
        try:
            time.sleep(1.5)  # the devices looked through
            os.write(pad, ev(1, 304, 1) + ev(0, 0, 0))  # A pressed
            wait_for(lambda: keys(), msg="the page told of A")
            time.sleep(0.3)
            os.write(pad, ev(1, 304, 0) + ev(0, 0, 0))  # and let go
            wait_for(lambda: len(keys()) == 2, msg="and of its letting go")
            self.assertEqual(keys(), [{"key": "Enter", "type": "keydown"}, {"key": "Enter", "type": "keyup"}])
            os.write(pad, ev(1, 305, 1) + ev(1, 305, 0) + ev(0, 0, 0))  # B
            wait_for(lambda: len(keys()) == 4, msg="B")
            self.assertEqual(keys()[2]["key"], "Escape")
            sse.events.clear()
            os.write(pad, ev(3, 17, -1) + ev(0, 0, 0))  # the D-pad held up
            time.sleep(1)
            os.write(pad, ev(3, 17, 0) + ev(0, 0, 0))
            wait_for(lambda: keys() and keys()[-1]["type"] == "keyup", msg="the D-pad let go")
            got = keys()
            self.assertTrue(all(k["key"] == "ArrowUp" for k in got))
            self.assertNotIn("repeat", got[0])
            self.assertGreaterEqual(sum(1 for k in got if k.get("repeat")), 3, "held: again and again")
            sse.events.clear()
            # the stick a little to the right: nothing; far: right, as long as it stays past the middle
            os.write(pad, ev(3, 0, 8000) + ev(0, 0, 0))
            time.sleep(0.3)
            self.assertEqual(keys(), [], "a stick barely moved")
            os.write(pad, ev(3, 0, 30000) + ev(0, 0, 0) + ev(3, 0, 16000) + ev(0, 0, 0))
            wait_for(lambda: keys(), msg="the stick to the right")
            self.assertEqual(keys()[0], {"key": "ArrowRight", "type": "keydown"})
            self.assertFalse(any(k["type"] == "keyup" for k in keys()), "still past the middle: still held")
            os.write(pad, ev(3, 0, 0) + ev(0, 0, 0))
            wait_for(lambda: keys()[-1] == {"key": "ArrowRight", "type": "keyup"}, msg="the stick back in the middle")
            self.assertNotIn("menukey", sse.events, "a controller is not a remote with a Menu key")
        finally:
            sse.close()
            b.stop()

    def test_switches_off_when_nobody_is_there(self):
        log = self.fake_systemctl()
        # an hour is a second here, and the warning comes a second before
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1", "KINO_AUTOOFF_HOUR": "1", "KINO_AUTOOFF_WARN": "1",
                                         "KINO_ALIVE_FIRST": "600"}).start()
        try:
            self.assertEqual(b.request("/settings", {"autooff": "4"})[0], 200)
            for _ in range(5):  # someone pressing keys now and then: it stays on
                time.sleep(1)
                self.assertEqual(b.request("/active", {})[0], 200)
            self.assertFalse(log.exists(), "still on while keys are pressed")
            wait_for(lambda: log.exists() and log.read_text() == "poweroff\n", timeout=15, msg="switched off after four 'hours'")
            self.assertEqual(b.request("/settings", {"autooff": "1"})[0], 400, "only 2, 4 or 8 hours")
        finally:
            b.stop()

    def test_volume_of_the_sound_output(self):
        level = self.tmp / "volume"
        level.write_text("0.40")
        # a stand-in for PipeWire's wpctl: the volume in a file, up to the limit given with -l
        (self.tmp / "bin" / "wpctl").write_text(f"""#!/bin/sh
f={level}
if [ "$1" = get-volume ]; then echo "Volume: $(cat $f)"; exit; fi
step=$(echo "$5" | tr -d '%+-'); case "$5" in *+) sign=1;; *) sign=-1;; esac
python3 -c "import sys; v=float(open('$f').read())+$sign*$step/100; open('$f','w').write(f'{{max(0, min($3, v)):.2f}}')"
""")
        (self.tmp / "bin" / "wpctl").chmod(0o755)
        b = Backend(self.mock.url, env={**self.env, "KINO_SESSION": "1"}).start()
        try:
            self.assertEqual(b.request("/settings")[1]["volume"], 40)
            self.assertEqual(b.request("/volume", {"step": 5})[1], {"volume": 45})
            self.assertEqual(b.request("/volume", {"step": -5})[1], {"volume": 40})
            level.write_text("0.98")
            self.assertEqual(b.request("/volume", {"step": 5})[1], {"volume": 100}, "no louder than 100 %")
            self.assertEqual(b.request("/volume", {"step": 50})[0], 400)
        finally:
            b.stop()
        b = Backend(self.mock.url, env=self.env).start()
        try:
            self.assertIsNone(b.request("/settings")[1]["volume"], "a desktop has its own")
            self.assertEqual(b.request("/volume", {"step": 5})[0], 403)
        finally:
            b.stop()

    def test_a_desktop_that_asks_for_it(self):
        log = self.fake_systemctl()
        b = Backend(self.mock.url, env=self.env, conf={"thin_client": True}).start()
        try:
            self.assertTrue(b.request("/settings")[1]["system"])
            self.assertTrue(b.request("/settings")[1]["displayModes"], "display modes too")
            self.assertEqual(b.request("/system", {"action": "reboot"})[0], 200)
            wait_for(lambda: log.exists() and log.read_text() == "reboot\n", msg="systemctl reboot")
        finally:
            b.stop()

    def test_never_restarts_a_desktop(self):
        log = self.fake_systemctl()
        b = Backend(self.mock.url, env=self.env).start()
        try:
            self.assertFalse(b.request("/settings")[1]["system"])
            self.assertEqual(b.request("/system", {"action": "reboot"})[0], 403)
            time.sleep(0.5)
            self.assertFalse(log.exists())
        finally:
            b.stop()

    def test_not_on_a_desktop(self):
        b = Backend(self.mock.url, env=self.env).start()
        try:
            self.assertIsNone(b.request("/settings")[1]["displayModes"])
            self.assertEqual(b.request("/settings", {"display": "1920x1080@60"})[0], 400)
            self.assertEqual(self.modes_set(), [], "the desktop's screen is never touched")
        finally:
            b.stop()

    def test_display_mode_at_start(self):
        conf = self.tmp / "conf"
        (conf / "kino").mkdir(parents=True)
        (conf / "kino" / "config.json").write_text(json.dumps({"server": self.mock.url, "username": "x"}))
        r = subprocess.run([sys.executable, str(ROOT / "display-mode")], capture_output=True, text=True, timeout=30,
                           env={**os.environ, **self.env, "XDG_CONFIG_HOME": str(conf)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.modes_set(), ["1920x1080@120.000Hz"])


class SectionAndWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()

    def tearDown(self):
        self.mock.stop()

    def test_navigation_clicks_have_levels(self):
        b = Backend(self.mock.url, conf={"sounds": "on"}).start()
        try:
            self.assertEqual(b.request("/start")[1]["sounds"], "medium", "on, from before the levels: the middle one")
            self.assertEqual(b.request("/settings", {"sounds": "high"})[0], 200)
            self.assertEqual((b.request("/start")[1]["sounds"], b.request("/settings")[1]["sounds"]), ("high", "high"))
            self.assertEqual(b.request("/settings", {"sounds": "on"})[0], 400, "only the levels")
            self.assertEqual(b.request("/start")[1]["soundkind"], "click", "the click from before")
            self.assertEqual(b.request("/settings", {"soundkind": "glass"})[0], 200)
            self.assertEqual((b.request("/start")[1]["soundkind"], b.request("/settings")[1]["soundkind"]), ("glass", "glass"))
            self.assertEqual(b.request("/settings", {"soundkind": "bell"})[0], 400, "only the sounds there are")
        finally:
            b.stop()

    def test_last_section_survives_a_restart(self):
        b = Backend(self.mock.url).start()
        try:
            self.assertEqual(b.request("/start")[1]["section"], None)
            self.assertEqual(b.request("/section", {"id": "music"})[0], 200)
            self.assertEqual(b.request("/section", {"id": "../x"})[0], 400)
            b.proc.terminate()  # the backend alone, its config stays
            b.proc.wait(5)
            b.start()
            self.assertEqual(b.request("/start")[1], {"section": "music", "language": "en", "screensaver": 10, "startwith": "menu", "lastLive": None,
                                                       "spoilers": "show", "sounds": "off", "soundkind": "click", "badges": "all",
                                                       "effects": "auto", "textsize": "normal", "motion": "full", "size": "auto", "sizeAuto": "tv", "simple": False,
                                               "services": {"navidrome": False, "audiobookshelf": False, "podfetch": False}, "menuPicture": None, "welcome": False})
        finally:
            b.stop()

    def test_episode_counts_for_the_badges(self):
        b = Backend(self.mock.url).start()
        try:
            status, counts = b.request("/badges")
            self.assertEqual(status, 200)
            self.assertEqual(counts["series"], {"s1": [8, 9]}, "the aired placeholder counts, the one to come and specials not")
            self.assertEqual(counts["seasons"], {"se1": [4, 4], "se2": [4, 5], "se0": [1, 1]})
            self.assertEqual(b.request("/settings", {"badges": "off"})[0], 200)
            self.assertEqual(b.request("/badges")[1], {"series": {}, "seasons": {}}, "none with the setting off")
            self.assertEqual(b.request("/settings", {"badges": "some"})[0], 400)
        finally:
            b.stop()

    def test_no_badges_without_placeholders(self):
        """Without any missing-episode placeholders Jellyfin knows nothing missing: no show would look incomplete."""
        for e in ("e01", "e25", "e26"):
            del self.mock.lib.items[e]
            self.mock.lib.series_order.remove(e)
        b = Backend(self.mock.url).start()
        try:
            self.assertEqual(b.request("/badges")[1], {"series": {}, "seasons": {}})
        finally:
            b.stop()

    def test_watchdog_starts_kino_again_when_the_ui_is_silent(self):
        b = Backend(self.mock.url, env={"KINO_SESSION": "1", "KINO_ALIVE_FIRST": "1.5", "KINO_ALIVE_TIMEOUT": "1.5"}).start()
        try:
            b.request("/alive", {})
            for _ in range(8):  # the UI says it is there: Matinee keeps running
                time.sleep(0.5)
                self.assertEqual(b.request("/alive", {})[0], 200)
            wait_for(lambda: b.proc.poll() is not None, timeout=8, msg="the backend ends when the UI goes silent")
            self.assertEqual(b.proc.returncode, 1)
            self.assertIn("The UI is not answering", b.logtext())
        finally:
            b.stop()

    def test_watchdog_ends_the_session_so_systemd_starts_it_again(self):
        """Like kino --session: the backend in the background, Chromium in front (here a sleep). The script must end."""
        b = Backend(self.mock.url, env={"KINO_SESSION": "1", "KINO_ALIVE_FIRST": "1.5", "KINO_ALIVE_TIMEOUT": "1.5"})
        script = b.tmp / "session"
        script.write_text(f'#!/bin/sh\n{sys.executable} {ROOT / "server.py"} &\nSERVER=$!\n'
                          "trap 'kill $SERVER 2>/dev/null' EXIT INT TERM\nsleep 600  # Chromium\n")
        script.chmod(0o755)
        start = lambda: subprocess.Popen([str(script), "--session"], env=b.env, stdout=b.log, stderr=b.log, start_new_session=True)
        session = start()
        try:
            session.wait(20)
            self.assertIn("The UI is not answering", b.logtext())
            self.assertTrue((b.runtime / "kino-watchdog").exists(), "it notes the restart")
            # started again at once, and the UI still silent (the TV off): no restart every few minutes
            again = start()
            time.sleep(4)
            self.assertIsNone(again.poll(), "calm for a while")
        finally:
            for p in (session, locals().get("again")):  # the script, its backend and its sleep
                if p and p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
            b.stop()

    def test_no_watchdog_while_the_tv_is_off(self):
        drm = Path(tempfile.mkdtemp())
        (drm / "card1-HDMI-A-1").mkdir()
        (drm / "card1-HDMI-A-1" / "status").write_text("disconnected\n")
        b = Backend(self.mock.url, env={"KINO_SESSION": "1", "KINO_ALIVE_FIRST": "1", "KINO_ALIVE_TIMEOUT": "1",
                                        "KINO_DRM": str(drm)}).start()
        try:
            time.sleep(3)
            self.assertIsNone(b.proc.poll(), "Chromium is not started yet: nothing to wait for")
            (drm / "card1-HDMI-A-1" / "status").write_text("connected\n")
            wait_for(lambda: b.proc.poll() is not None, timeout=8, msg="the TV on: the UI is looked after again")
        finally:
            b.stop()
            shutil.rmtree(drm)

    def test_no_watchdog_while_the_tv_is_off_after_the_page_came(self):
        """The page came, then the TV went off and Chromium fell over with it: kino --session starts it again once the
        TV is back, so no restart meanwhile, and its usual time to come once it is on."""
        drm = Path(tempfile.mkdtemp())
        (drm / "card1-HDMI-A-1").mkdir()
        status = drm / "card1-HDMI-A-1" / "status"
        status.write_text("connected\n")
        b = Backend(self.mock.url, env={"KINO_SESSION": "1", "KINO_ALIVE_FIRST": "1.5", "KINO_ALIVE_TIMEOUT": "1.5",
                                        "KINO_DRM": str(drm)}).start()
        try:
            self.assertEqual(b.request("/alive", {})[0], 200)
            status.write_text("disconnected\n")
            time.sleep(4)
            self.assertIsNone(b.proc.poll(), "the TV off: nothing to look after")
            status.write_text("connected\n")
            time.sleep(0.8)
            self.assertIsNone(b.proc.poll(), "on again: Chromium's time to come first")
            wait_for(lambda: b.proc.poll() is not None, timeout=8, msg="the page silent with the TV on: Matinee anew")
        finally:
            b.stop()
            shutil.rmtree(drm)

    def test_stopped_while_waiting_for_the_tv(self):
        """kino --session with the TV off waits for it; stopped meanwhile (systemctl restart kino, e.g. by the nightly
        update), it ends at once with its backend, instead of being killed by systemd 90 s later."""
        drm = Path(tempfile.mkdtemp())
        (drm / "card1-HDMI-A-1").mkdir()
        (drm / "card1-HDMI-A-1" / "status").write_text("disconnected\n")
        b = Backend(self.mock.url)
        env = {**b.env, "KINO_DRM": str(drm), "WAYLAND_DISPLAY": "kino-test-none"}  # wlr-randr reaches no screen
        session = subprocess.Popen([str(ROOT / "kino"), "--session"], env=env, stdout=b.log, stderr=b.log,
                                   start_new_session=True)
        try:
            wait_for(lambda: b.request("/session")[0] == 200, timeout=15, msg="backend up")
            time.sleep(2.5)  # in the loop waiting for the TV
            self.assertIsNone(session.poll())
            session.send_signal(signal.SIGTERM)
            session.wait(4)
            wait_for(lambda: subprocess.run(["curl", "-s", "-o", "/dev/null", f"{b.url}/"]).returncode != 0,
                     timeout=5, msg="the backend ended too")
        finally:
            if session.poll() is None:
                os.killpg(session.pid, signal.SIGKILL)
            b.stop()
            shutil.rmtree(drm)

    def test_no_watchdog_on_a_desktop(self):
        b = Backend(self.mock.url, env={"KINO_ALIVE_FIRST": "0.5", "KINO_ALIVE_TIMEOUT": "0.5"}).start()
        try:
            time.sleep(2)
            self.assertIsNone(b.proc.poll(), "still running")
        finally:
            b.stop()


class PasswordTest(unittest.TestCase):
    """The password is used to sign in once and never kept on disk."""

    def setUp(self):
        self.mock = MockJellyfin().start()

    def tearDown(self):
        self.mock.stop()

    def config(self, b):
        return json.loads((b.conf / "config.json").read_text())

    def test_password_from_older_config_is_removed_after_login(self):
        b = Backend(self.mock.url).start()
        try:
            conf = self.config(b)
            self.assertNotIn("password", conf)
            self.assertEqual((conf["server"], conf["username"], conf["language"]), (self.mock.url, USERNAME, "en"),
                             "everything else is kept")
            self.assertEqual(stat.S_IMODE(os.stat(b.conf / "config.json").st_mode), 0o600)
            self.assertNotIn(PASSWORD, "".join(p.read_text() for p in b.conf.iterdir()), "password nowhere on disk")
        finally:
            b.stop()

    def test_without_login_kino_asks_for_set_password(self):
        b = Backend(self.mock.url, password=None).start(wait_ready=False)
        try:
            wait_for(lambda: b.request("/session")[1].get("error") == "login_required", msg="error code login_required")
            r = b.set_password(PASSWORD)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("not stored", r.stdout)
            wait_for(lambda: b.request("/session")[0] == 200, timeout=10, msg="running backend picks up the login")
            self.assertNotIn(PASSWORD, "".join(p.read_text() for p in b.conf.iterdir()), "password nowhere on disk")
            self.assertEqual(b.request("/jf/UserViews?userId=u1")[0], 200)
        finally:
            b.stop()

    def test_set_password_rejects_wrong_password(self):
        b = Backend(self.mock.url, password=None).start(wait_ready=False)
        try:
            r = b.set_password("wrong")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Login rejected", r.stderr)
            self.assertNotIn(TOKEN, (b.conf / "state.json").read_text() if (b.conf / "state.json").exists() else "")
            self.assertNotIn("wrong", (b.conf / "config.json").read_text())
        finally:
            b.stop()

    def test_signing_in_again_while_running(self):
        """A new login replaces the device's token on the server; the running backend switches to it."""
        b = Backend(self.mock.url).start()
        try:
            self.mock.token = "tok-new"  # the next login hands out a new token, the old one stops working
            self.assertEqual(b.set_password(PASSWORD).returncode, 0)
            self.assertEqual(b.request("/jf/UserViews?userId=u1")[0], 200)
        finally:
            b.stop()


class SecurityTest(unittest.TestCase):
    """Other websites in a normal browser on the same computer must not be able to use the backend."""

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.backend = Backend(self.mock.url).start()

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def raw(self, method, path, headers, body=b"", length=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.backend.port, timeout=10)
        conn.putrequest(method, path, skip_host=True)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.putheader("Content-Length", str(len(body)) if length is None else length)
        conn.endheaders(body)
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status

    def test_dns_rebinding_is_blocked(self):
        # evil.example resolving to 127.0.0.1: same origin for the browser, but a foreign Host header
        for path in ("/", "/session", "/jf/UserViews?userId=u1", "/jf/Items/m1/Images/Primary"):
            self.assertEqual(self.raw("GET", path, {"Host": f"evil.example:{self.backend.port}"}), 403, path)
        self.assertEqual(self.raw("GET", "/session", {}), 403, "no Host header at all")

    def test_own_page_is_allowed(self):
        cookie = self.backend.page_cookie()
        for host in (f"127.0.0.1:{self.backend.port}", f"localhost:{self.backend.port}"):
            self.assertEqual(self.raw("GET", "/session", {"Host": host, "Cookie": cookie}), 200, host)

    def test_other_programs_are_refused(self):
        # a program on the same computer can send the right Host and no Origin: without the page's cookie it is refused
        host = {"Host": f"127.0.0.1:{self.backend.port}"}
        for path in ("/session", "/jf/UserViews?userId=u1", "/settings", "/js/core.js"):
            self.assertEqual(self.raw("GET", path, host), 403, path)
        body = json.dumps({"action": "poweroff"}).encode()
        self.assertEqual(self.raw("POST", "/system", {**host, "Content-Type": "application/json"}, body), 403)
        self.assertEqual(self.raw("GET", "/session", {**host, "Cookie": "kino=guessed"}), 403, "a guessed secret")
        self.assertEqual(self.raw("GET", "/", host), 200, "the page itself, which brings the cookie")
        with urllib.request.urlopen(f"{self.backend.url}/", timeout=10) as r:
            cookie = r.headers.get("Set-Cookie")
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY", "no other website shows Matinee in a frame")
            self.assertIn("script-src 'self'", r.headers.get("Content-Security-Policy"), "only Matinee's own scripts run")
            self.assertNotRegex(r.read().decode(), r"<script(?![^>]*\bsrc=)", "no inline script, which it would block")
        self.assertEqual(self.raw("GET", "/session", {**host, "Cookie": cookie.split(";")[0]}), 200)

    def test_proxied_answers_are_never_run(self):
        code, _ = self.backend.request("/jf/Items/m1/Images/Primary")
        r = urllib.request.urlopen(urllib.request.Request(f"{self.backend.url}/jf/Items/m1/Images/Primary",
                                                          headers={"Cookie": self.backend.page_cookie()}), timeout=10)
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("sandbox", r.headers.get("Content-Security-Policy"))

    def test_commands_from_other_websites_are_blocked(self):
        host = {"Host": f"127.0.0.1:{self.backend.port}", "Cookie": self.backend.page_cookie()}
        body = json.dumps({"itemId": "m2", "played": True}).encode()
        # a form or fetch without preflight can only send text/plain or form data (refused even if it had the cookie,
        # which SameSite=Strict keeps from other websites anyway)
        self.assertEqual(self.raw("POST", "/played", {**host, "Content-Type": "text/plain"}, body), 415)
        self.assertEqual(self.raw("POST", "/played", {**host, "Content-Type": "application/x-www-form-urlencoded"}, body), 415)
        self.assertEqual(self.raw("POST", "/played", {**host, "Content-Type": "application/json",
                                                       "Origin": "https://evil.example"}, body), 403)
        self.assertFalse(self.mock.lib.userdata["m2"]["Played"])
        self.assertEqual(self.raw("POST", "/played", {**host, "Content-Type": "application/json", "Cookie": self.backend.page_cookie(),
                                                       "Origin": f"http://127.0.0.1:{self.backend.port}"}, body), 200)
        self.assertTrue(self.mock.lib.userdata["m2"]["Played"])

    def test_a_body_too_big_or_of_no_length_is_refused_unread(self):
        host = {"Host": f"127.0.0.1:{self.backend.port}", "Content-Type": "application/json", "Cookie": self.backend.page_cookie()}
        # answered at once: neither waits for bytes that never come
        self.assertEqual(self.raw("POST", "/played", host, b"{}", length="-1"), 413)
        self.assertEqual(self.raw("POST", "/played", host, b"{}", length=str(50 << 20)), 413)
        self.assertEqual(self.raw("POST", "/played", host, b"{}", length="2e3"), 413)

    def test_broken_json_is_rejected(self):
        host = {"Host": f"127.0.0.1:{self.backend.port}", "Content-Type": "application/json", "Cookie": self.backend.page_cookie()}
        self.assertEqual(self.raw("POST", "/play", host, b"{not json"), 400)
        self.assertIsNone(self.backend.proc.poll())


class SignedOutTest(unittest.TestCase):
    """The token is revoked while Matinee runs (device removed in Jellyfin): it says so and carries on after set-password."""

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.backend = Backend(self.mock.url).start()

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def test_revoked_token_leads_back_to_set_password(self):
        self.mock.token = "revoked-and-replaced"
        self.assertEqual(self.backend.request("/jf/UserViews?userId=u1")[0], 401)
        wait_for(lambda: self.backend.request("/session") == (503, {"error": "signed_out", "server": self.mock.url,
                                                                    "username": USERNAME}), msg="says it was signed out")
        self.assertEqual(self.backend.request("/jf/UserViews?userId=u1")[0], 503, "nothing is sent to Jellyfin meanwhile")
        self.assertEqual(self.backend.set_password(PASSWORD).returncode, 0)
        wait_for(lambda: self.backend.request("/session")[0] == 200, timeout=10, msg="signed in again by itself")
        self.assertEqual(self.backend.request("/jf/UserViews?userId=u1")[0], 200)


# Matinee's connections to Jellyfin (jellyfin_api.Pool), in a process of its own with an empty configuration, against a
# small server: a kept connection it closes while idle, one that hangs, a request it takes but never answers, and an
# answer it sends packed.
POOL_PROBE = r"""
import gzip, json, socket, sys, threading, time
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from backend import jellyfin_api

taken = {"POST": 0}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def answer(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def do_GET(self):
        self.n = getattr(self, "n", 0) + 1  # the requests on this connection
        if self.path == "/hangs-second" and self.n > 1:
            time.sleep(1.5)  # a connection gone dead
        if self.path == "/packed":
            taken["asked_packed"] = self.headers.get("Accept-Encoding")
            data = gzip.compress(b"a long list " * 100)
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        self.answer()
        if self.path == "/closes-idle":
            time.sleep(0.1)
            self.close_connection = True  # as a server does with a connection idle for a while
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        taken["POST"] += 1
        if self.path == "/taken-unanswered":
            self.close_connection = True  # done, but the answer never comes
            return
        self.answer()

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}"
out = {}

pool = jellyfin_api.Pool(url)
pool.request("/closes-idle")
time.sleep(0.4)
out["after_idle_close"] = pool.request("/report", "POST", b"{}").status
out["posts_after_idle_close"] = taken["POST"]

pool = jellyfin_api.Pool(url, timeout=0.5)
pool.request("/hangs-second")
try:
    out["after_hang"] = pool.request("/hangs-second").status
except Exception as e:
    out["after_hang"] = type(e).__name__

pool, taken["POST"] = jellyfin_api.Pool(url), 0
pool.request("/ok")
try:
    pool.request("/taken-unanswered", "POST", b"{}")
    out["unanswered"] = "answered"
except Exception as e:
    out["unanswered"] = type(e).__name__
out["posts_unanswered"] = taken["POST"]

r = jellyfin_api.Pool(url).request("/packed")
out["packed"] = [taken.get("asked_packed"), r.body == b"a long list " * 100, r.headers.get("Content-Encoding")]
print(json.dumps(out))
"""


class PoolTest(unittest.TestCase):
    def test_kept_connections_retried_only_when_safe(self):
        with tempfile.TemporaryDirectory() as home:
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            r = subprocess.run([sys.executable, "-c", POOL_PROBE, server_dir], capture_output=True, text=True, timeout=30,
                               env={**os.environ, "XDG_CONFIG_HOME": home, "XDG_RUNTIME_DIR": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual((out["after_idle_close"], out["posts_after_idle_close"]), (200, 1),
                         "a connection the server closed while idle not used for a report: sent once, on a new one")
        self.assertEqual(out["after_hang"], 200, "a kept connection gone dead: the question asked again on a new one")
        self.assertEqual(out["posts_unanswered"], 1, "a report the server took is not sent twice")
        self.assertNotEqual(out["unanswered"], "answered")
        self.assertEqual(out["packed"], ["gzip", True, None], "an answer asked for packed, and handed on unpacked")


# What another Matinee or Matinee Remote asks to play (handoff_plan), in a process of its own: only what this Matinee plays
# itself; anything that is not of its own servers is nothing to go on with here.
HANDOFF_PROBE = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import handoff, tv

tv.own_channels = lambda: [{"id": "own-g-action", "name": "Action"}]
asks = [{"kind": "yt", "id": "abcdefghijk", "title": "T"},
        {"kind": "tv", "item": {"id": "x", "title": "T", "channel": "C", "url": "https://a.example/v.mp4"}},
        {"kind": "tv", "live": "daserste"}, {"kind": "tv", "live": "own-g-drama"}, {"kind": "tv", "live": "own-g-action"}]
print(json.dumps([handoff.handoff_plan(x) is not None for x in asks]))
"""


class HandoffTest(unittest.TestCase):
    def test_only_what_this_kino_plays_itself(self):
        with tempfile.TemporaryDirectory() as home:
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            r = subprocess.run([sys.executable, "-c", HANDOFF_PROBE, server_dir], capture_output=True, text=True, timeout=30,
                               env={**os.environ, "XDG_CONFIG_HOME": home, "XDG_RUNTIME_DIR": home, "XDG_CACHE_HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1]), [False, False, False, False, True],
                         "one of its own channels only")


# Watched to the credits: a film or episode of Jellyfin's in its "Outro" segment.
CREDITS_PROBE = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import video_player

segs = [{"type": "Intro", "start": 60, "end": 90}, {"type": "Outro", "start": 1250, "end": 1330}]
print(json.dumps({"credits": [video_player.in_credits(segs, p) for p in (80, 1200, 1251)],
                  "none": video_player.in_credits([], 1300)}))
"""


class CreditsTest(unittest.TestCase):
    def test_watched_to_the_credits_is_watched(self):
        with tempfile.TemporaryDirectory() as home:
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            r = subprocess.run([sys.executable, "-c", CREDITS_PROBE, server_dir], capture_output=True, text=True, timeout=30,
                               env={**os.environ, "XDG_CONFIG_HOME": home, "XDG_RUNTIME_DIR": home, "XDG_CACHE_HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual(out["credits"], [False, False, True], "only in the Outro segment")
        self.assertFalse(out["none"], "a server without segments: Jellyfin's own 90 %")


# One's own channels when another user signs in, and a film seen before stopped in its credits
OWN_PROBE = r"""
import json, sys, threading, time
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import core, tv, video_player

core.state["user_id"] = "u1"
tv.own_build = lambda: ["first's"]
first = tv.own_channels()
tv.own_forget()  # another user
tv.own_build = lambda: ["second's"]
second = tv.own_channels()
# a look begun for the one before, finished after the next signed in: what it made is not kept
def late():
    tv.own_forget()
    return ["stale"]
tv.own_cache["until"] = 0
tv.own_build = late
tv.own_channels()
for _ in range(100):
    if not any(t.daemon and t.is_alive() and t is not threading.current_thread() for t in threading.enumerate()):
        break
    time.sleep(0.02)
asked = []
video_player.jf = lambda path, method="GET", *a, **k: asked.append((method, path))
video_player.mark_watched({"Id": "m1", "UserData": {"Played": True}})
video_player.mark_watched({"Id": "m2", "RunTimeTicks": 1000, "UserData": {}}, 950)  # counted by Jellyfin itself
print(json.dumps({"first": first, "second": second, "after": tv.own_cache.get("channels"), "asked": asked}))
"""


class OwnAndCreditsTest(unittest.TestCase):
    def test_own_channels_forgotten_and_seen_film_marked(self):
        with tempfile.TemporaryDirectory() as home:
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            r = subprocess.run([sys.executable, "-c", OWN_PROBE, server_dir], capture_output=True, text=True, timeout=30,
                               env={**os.environ, "XDG_CONFIG_HOME": home, "XDG_RUNTIME_DIR": home, "XDG_CACHE_HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual(out["first"], ["first's"])
        self.assertEqual(out["second"], ["second's"], "the next user's library, not the one before's")
        self.assertIsNone(out["after"], "a look for the one before keeps what it made to itself")
        self.assertEqual(len(out["asked"]), 1, "seen before and stopped in the credits: marked anew, off Continue Watching")
        self.assertEqual(out["asked"][0][0], "POST")

# What the review's fixes in Matinee's core do where no server and no player is needed to see it, each in a process of
# its own with the parts around it stood in for.
PROBE_HEAD = r"""
import json, os, sys, threading, time
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
"""

# The account transfer with the disk full while the other account is written aside.
ACCOUNT_PROBE = PROBE_HEAD + r"""
from backend import account, core

core.CONF_FILE.parent.mkdir(parents=True, exist_ok=True)
core.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
mine = {core.CONF_FILE: '{"username": "mine", "quality": "high"}', core.STATE_FILE: json.dumps(dict(core.state)),
        core.HIDDEN_FILE: '{"nextup": ["mine"]}', core.SYNC_FILE: '{"synced": "mine"}'}
for path, text in mine.items():
    path.write_text(text)
written, real = [], account.write_private
def full_after_one(path, text):
    if written:
        raise OSError(28, "No space left on device")
    written.append(path)
    real(path, text)
account.write_private = full_after_one
bundle = {"config": {"username": "theirs"}, "jellyfin": {"token": "t", "user_id": "u"},
          "files": {"hidden": {"nextup": ["theirs"]}, "tv": {"theirs": 1}}}
try:
    account.account_apply(bundle)
    raised = None
except Exception as e:
    raised = type(e).__name__
dirs = {path.parent for path in mine} | {core.TV_FILE.parent}
print(json.dumps({"raised": raised, "staged": len(written),
                  "unchanged": all(path.read_text() == text for path, text in mine.items()),
                  "tv": core.TV_FILE.exists(),
                  "left": sorted(f.name for d in dirs for f in d.iterdir() if f.name.endswith((".taking", ".tmp")))}))
"""

# A title whose similar ones Jellyfin keeps failing for, among others it answers for.
SIMILAR_PROBE = PROBE_HEAD + r"""
from backend import core, library

core.state["user_id"] = "u"
library.SIMILAR_PAUSE = 0
library.RETRY_MAX = 60  # waiting for the server would end the probe by its timeout
library.jf = lambda path, *a, **k: {"Items": [{"Id": i} for i in "abc"]}
asked = []
def fetch(item_id):
    asked.append(item_id)
    if item_id == "b":
        raise ValueError("an answer that cannot be read")
    library.similar[item_id] = {"ids": ["x"], "at": time.time()}
library.fetch_similar = fetch
library.save_similar = lambda: library.similar_wanted.put(None)  # a pass is over: the library "changed", the next one
library.ready.set()
threading.Thread(target=library.similar_loop, daemon=True).start()
end = time.time() + 20
while "b" not in library.similar and time.time() < end:
    time.sleep(0.05)
print(json.dumps({"asked": {i: asked.count(i) for i in "abc"}, "b": library.similar.get("b", {}).get("ids"),
                  "a": library.similar.get("a", {}).get("ids")}))
"""

# A restart ends the players first, also one that does not end when asked; and the end of a playback with the disk full.
PLAYERS_PROBE = PROBE_HEAD + r"""
import contextlib, subprocess
from backend import jf_reports, player_tracks, system, updates
from backend.video_player import player

out = {}
player.keep_now = lambda: None
deaf = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                                               "print('up', flush=True); time.sleep(60)"], stdout=subprocess.PIPE)
deaf.stdout.readline()  # it ignores TERM from here on
player.proc = deaf
start = time.time()
system.end_players(wait=1)
out["waited"] = time.time() - start >= 1
try:  # killed, not waited for again: gone a moment later
    out["ended"] = deaf.wait(5) != 0
except subprocess.TimeoutExpired:
    out["ended"] = False
    deaf.kill()
player.proc = None

order, at_execv = [], threading.Event()
system.end_players = lambda wait=0: order.append(f"players {wait}")
updates.UPDATES = False  # a desktop: the backend starts again in its place
updates.time.sleep = lambda s: None
def execv(*a):
    order.append("execv")
    at_execv.set()
    threading.Event().wait()  # the real one never comes back
updates.os.execv = execv
updates.restart_kino()
at_execv.wait(10)
out["order"] = order

@contextlib.contextmanager
def full(*a, **k):
    raise OSError(28, "No space left on device")
    yield
def full_write(*a, **k):
    raise OSError(28, "No space left on device")
player_tracks.tracks_key = lambda item: "show"
player_tracks.changing = full
player_tracks.save_state = full_write
player_tracks.reload_token = lambda: None
jf_reports.jf = lambda *a, **k: (_ for _ in ()).throw(OSError("the server away"))
jf_reports.load_pending = lambda: []
jf_reports.save_pending = full_write
try:
    player.remember_tracks({"item": {}, "choice": {"sub": "eng"}, "initial": {"sub": ""}, "prefs": {},
                            "session": {"PlayMethod": "DirectPlay"}})
    player.remember_volume(None, 31)
    jf_reports.report("/Sessions/Playing/Stopped", {"ItemId": "i"}, critical=True)
    out["went_on"] = True
except Exception as e:
    out["went_on"] = repr(e)
print(json.dumps(out))
"""


class CoreReviewTest(unittest.TestCase):
    def probe(self, code):
        with tempfile.TemporaryDirectory() as home:
            server_dir = os.path.dirname(os.environ.get("KINO_SERVER", str(ROOT / "server.py")))
            r = subprocess.run([sys.executable, "-c", code, server_dir], capture_output=True, text=True, timeout=60,
                               env={**os.environ, "XDG_CONFIG_HOME": home, "XDG_RUNTIME_DIR": home, "XDG_CACHE_HOME": home,
                                    "XDG_STATE_HOME": home, "XDG_DATA_HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.splitlines()[-1]), r.stderr

    def test_an_account_not_written_aside_leaves_this_one_as_it_was(self):
        out, _ = self.probe(ACCOUNT_PROBE)
        self.assertEqual(out["raised"], "AccountNotTaken")
        self.assertEqual(out["staged"], 1, "the disk was full at the second file")
        self.assertTrue(out["unchanged"], "settings, sign-in, lists and what was synced: all this account's still")
        self.assertFalse(out["tv"], "nothing of the other account in place")
        self.assertEqual(out["left"], [], "and nothing of it left aside")

    def test_a_title_that_keeps_failing_does_not_hold_up_the_rest(self):
        out, err = self.probe(SIMILAR_PROBE)
        self.assertEqual(out["a"], ["x"], "the others are looked up")
        self.assertEqual(out["asked"], {"a": 1, "b": 3, "c": 1}, "tried in three passes, the others once")
        self.assertEqual(out["b"], [], "then it counts as having none")

    def test_a_restart_ends_the_players_first(self):
        out, _ = self.probe(PLAYERS_PROBE)
        self.assertTrue(out["ended"], "also one that does not end when asked")
        self.assertTrue(out["waited"], "after it was waited for")
        self.assertEqual(out["order"], ["players 3", "execv"], "before the backend starts again in its place")

    def test_the_end_of_a_playback_goes_on_with_the_disk_full(self):
        out, err = self.probe(PLAYERS_PROBE)
        self.assertIs(out["went_on"], True)
        for line in ("Tracks not kept", "Volume not kept", "not kept for later"):
            self.assertIn(line, err)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.backend = Backend(self.mock.url).start()

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def config(self):
        return json.loads((self.backend.conf / "config.json").read_text())

    def test_effects_from_before_are_reduced(self):
        """The effects "plain" and "black" are no more: a config.json from before has Reduced ("light")."""
        b = Backend(self.mock.url, conf={"effects": "black"}).start()
        try:
            self.assertEqual(b.request("/settings")[1]["effects"], "light")
            self.assertEqual(b.request("/start")[1]["effects"], "light", "as the UI starts with it")
            self.assertEqual(b.request("/settings", {"effects": "plain"})[0], 400, "not to be chosen any more")
        finally:
            b.stop()

    def test_simple_settings_need_their_code_to_go_off(self):
        b = self.backend
        self.assertFalse(b.request("/settings")[1]["simple"])
        for bad in ({"on": True, "pin": "12"}, {"on": True, "pin": "abcd"}, {"on": "yes", "pin": "1234"}, {"on": False, "pin": "1234"}):
            self.assertEqual(b.request("/simple", bad)[0], 400, bad)
        self.assertEqual(b.request("/simple", {"on": True, "pin": "4711"})[0], 200)
        self.assertTrue(b.request("/settings")[1]["simple"])
        self.assertTrue(b.request("/start")[1]["simple"], "known at once when the UI starts")
        kept = self.config()["simple"]
        self.assertNotIn("4711", json.dumps(kept), "only a salted hash of the code is kept")
        self.assertEqual(b.request("/simple", {"on": True, "pin": "0000"})[0], 400, "on already: no new code over it")
        self.assertEqual(b.request("/simple", {"on": False, "pin": "1234"}), (403, {"error": "pin_wrong"}))
        self.assertTrue(b.request("/settings")[1]["simple"], "still on")
        self.assertEqual(b.request("/simple", {"on": False, "pin": "4711"})[0], 200)
        self.assertFalse(b.request("/settings")[1]["simple"])
        self.assertNotIn("simple", self.config())
        self.assertEqual(b.request("/settings", {"simple": "off"})[0], 400, "not by the settings' own way, which needs no code")

    def test_library_check_finds_and_fixes_mixed_up_films(self):
        lib = self.mock.lib.items
        lib["m3"]["Path"] = "/media/movies/Right.Film.2013.German.1080p [tmdbid-555].mkv"  # taken for another film
        lib["m5"]["Path"] = "/media/movies/Other.Title.1999.German.DL.1080p.mkv"  # the year says another film
        lib["m7"]["PrimaryImageAspectRatio"] = 1.78  # a still from the film as its poster
        lib["m8"].update(ProviderIds={}, Path="/media/movies/Lost.Film.2018.mkv")  # not identified, nothing found
        lib["m9"]["Path"] = "/media/movies/Movie.2049.2019.1080p.mkv"  # a year in the title is no year
        lib["m10"]["Path"] = "/media/movies/Movie.10.2020.1080p [tmdbid-999].mkv"  # its tag leads to a film of no year
        lib["m9"]["ProductionYear"] = 2019
        tmdb = lambda name, year, tid: {"Name": name, "ProductionYear": year, "ProviderIds": {"Tmdb": tid},
                                        "SearchProviderName": "TheMovieDb", "ImageUrl": f"https://image.example.org/{tid}.jpg"}
        self.mock.remote_movies = [tmdb("Right Film", 2013, "555"),
                                   {"Name": "Other Title Show", "ProductionYear": None, "ProviderIds": {"Tvdb": "9"},
                                    "SearchProviderName": "TheTVDB"},  # first, as TheTVDB's hits come: passed over
                                   tmdb("Other Title Again", 2021, "778"), tmdb("Other Title", 1999, "777"),
                                   {**tmdb("Movie Ten", None, "999"), "ProductionYear": None}]
        self.mock.remote_images = {"m7": [{"Url": "https://image.example.org/wide.jpg", "ProviderName": "TheMovieDb", "Width": 1920, "Height": 1080},
                                          {"Url": "https://image.example.org/poster.jpg", "ProviderName": "TheMovieDb", "Width": 1000, "Height": 1500}]}
        b = self.backend
        self.assertEqual(b.request("/library/check", {})[0], 200)
        wait_for(lambda: b.request("/library/check")[1]["state"] == "done", msg="checked")
        found = {e["id"]: e for e in b.request("/library/check")[1]["items"]}
        self.assertEqual({i: e["kind"] for i, e in found.items()}, {"m3": "wrong", "m5": "year", "m7": "poster", "m8": "unknown", "m10": "wrong"})
        self.assertIsNone(found["m10"]["proposal"], "not of the file's year: no proposal")
        self.assertEqual((found["m3"]["proposal"]["name"], found["m3"]["proposal"]["year"]), ("Right Film", 2013), "the file's own TMDB id")
        self.assertEqual(found["m5"]["proposal"]["name"], "Other Title", "TheMovieDb's of the file's year")
        self.assertEqual(found["m7"]["proposal"]["image"], "https://image.example.org/poster.jpg", "an upright one")
        self.assertEqual((found["m8"]["state"], found["m8"]["proposal"]), ("none", None))
        self.assertEqual(self.mock.applied, [], "nothing changed before asked")
        self.assertEqual(b.request("/library/fix", {"ids": ["m3", "m5", "m7", "m8"]})[0], 200)
        wait_for(lambda: {e["id"]: e["state"] for e in b.request("/library/check")[1]["items"]}
                 == {"m3": "fixed", "m5": "fixed", "m7": "fixed", "m8": "none", "m10": "none"}, msg="fixed")
        self.assertEqual(sorted(self.mock.applied), [("m3", "Right Film", ["true"]), ("m5", "Other Title", ["true"]),
                                                     ("m7", "https://image.example.org/poster.jpg", "poster")])
        self.assertEqual(lib["m3"]["ProviderIds"], {"Tmdb": "555"})
        self.assertEqual(b.request("/library/fix", {"ids": "m3"})[0], 400)
        b.request("/library/check", {})
        wait_for(lambda: b.request("/library/check")[1]["state"] == "done", msg="checked again")
        self.assertEqual([e["id"] for e in b.request("/library/check")[1]["items"]], ["m10", "m8"], "only what could not be fixed")
        self.mock.admin = False
        b.request("/library/check", {})
        wait_for(lambda: b.request("/library/check")[1]["state"] == "error", msg="no administrator")
        self.assertEqual(b.request("/library/check")[1]["error"], "admin_required")

    def test_tailscale_in_the_settings(self):
        self.assertIsNone(self.backend.request("/settings")[1]["tailscale"], "nothing without Tailscale")
        self.assertIsNone(self.backend.request("/settings")[1]["timeSynced"], "nothing to ask without timedatectl")
        fake = self.backend.tmp / "tailscale"
        status = {"BackendState": "Running", "CurrentTailnet": {"Name": "example.org"},
                  "Self": {"HostName": "tv", "DNSName": "tv.example.ts.net.", "TailscaleIPs": ["100.64.0.1", "fd7a::1"]},
                  "Peer": {"a": {"HostName": "nas", "DNSName": "nas.example.ts.net.", "Online": True, "ExitNode": True},
                           "b": {"HostName": "phone", "Online": False}}}
        fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(status) + "\nEOF\n")
        fake.chmod(0o755)
        self.backend.proc.terminate()
        self.backend.proc.wait()
        self.backend.env["KINO_TAILSCALE"] = str(fake)
        clock = self.backend.tmp / "timedatectl"  # and whether the clock is set by time servers
        clock.write_text('#!/bin/sh\n[ "$*" = "show -p NTPSynchronized --value" ] && echo yes\n')
        clock.chmod(0o755)
        self.backend.env["KINO_TIMEDATECTL"] = str(clock)
        self.backend.start()
        s = self.backend.request("/settings")[1]
        self.assertEqual(s["tailscale"],
                         {"state": "Running", "name": "tv", "dns": "tv.example.ts.net", "ips": ["100.64.0.1", "fd7a::1"],
                          "tailnet": "example.org", "exit": "nas", "online": 1, "peers": 2})
        self.assertIs(s["timeSynced"], True)

    def test_read_settings(self):
        code, s = self.backend.request("/settings")
        self.assertEqual(code, 200)
        self.assertEqual((s["language"], s["quality"], s["server"], s["username"]), ("en", "auto", self.mock.url, USERNAME))
        self.assertNotIn(TOKEN, json.dumps(s))
        self.assertEqual((s["screensaver"], s["ask"]), (10, 3), "defaults without an entry in config.json")
        self.assertEqual((s["audio"], s["subtitles"], s["sublang"]), ("auto", "off", "auto"))
        self.assertEqual(s["serverVersion"], "12.2.0")
        self.assertTrue(s["version"])

    def test_settings_do_not_wait_for_a_server_that_does_not_answer(self):
        self.assertEqual(self.backend.request("/settings")[1]["serverVersion"], "12.2.0")
        self.mock.info_delay = 4
        try:
            start = time.monotonic()
            code, s = self.backend.request("/settings")
            self.assertEqual(code, 200)
            self.assertLess(time.monotonic() - start, 2.5, "there after a moment, not after the server's timeout")
            self.assertEqual(s["serverVersion"], "12.2.0", "the version last known")
        finally:
            self.mock.info_delay = 0

    def test_change_screensaver_and_ask(self):
        self.assertEqual(self.backend.request("/settings", {"screensaver": "off", "ask": "5"})[0], 200)
        self.assertEqual((self.config()["screensaver"], self.config()["ask"]), ("off", 5))
        s = self.backend.request("/settings")[1]
        self.assertEqual((s["screensaver"], s["ask"]), ("off", 5))

    def test_change_settings(self):
        self.assertEqual(self.backend.request("/settings", {"quality": "4"})[0], 200)
        self.assertEqual(self.config()["quality"], 4)
        self.assertEqual(self.backend.request("/settings", {"language": "de"})[0], 200)
        self.assertEqual(self.config()["language"], "de")
        self.assertEqual(self.backend.request("/settings")[1]["language"], "de", "applies at once")
        self.assertIn(b'<html lang="de"', self.backend.request("/")[1])
        self.assertEqual(stat.S_IMODE(os.stat(self.backend.conf / "config.json").st_mode), 0o600)
        self.assertEqual(self.config()["server"], self.mock.url, "everything else kept")

    def test_invalid_settings_are_rejected(self):
        before = self.config()
        for body in ({"quality": "999"}, {"language": "xx"}, {"screensaver": "1"}, {"ask": "0"}, {"server": "http://evil.example"}, {"password": "x"},
                     {"quality": "4", "server": "http://evil.example"}, {}):
            self.assertEqual(self.backend.request("/settings", body)[0], 400, body)
        self.assertEqual(self.config(), before, "nothing written")


class ForgetTest(unittest.TestCase):
    """Taking things off rows: Continue Watching on the server, the rest in Matinee."""
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.backend = Backend(self.mock.url).start()

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def forget(self, what, value):
        return self.backend.request("/forget", {"what": what, "id": value})[0]

    def test_continue_watching_forgets_the_position_on_the_server(self):
        self.mock.lib.userdata["m1"]["PlaybackPositionTicks"] = 600 * TICKS
        self.assertEqual(self.forget("resume", "m1"), 200)
        self.assertEqual(self.mock.lib.userdata["m1"]["PlaybackPositionTicks"], 0)
        self.assertFalse(self.mock.lib.userdata["m1"]["Played"], "not marked watched")

    def test_shows_and_albums_are_hidden_until_shown_again(self):
        self.assertEqual(self.forget("nextup", "s1"), 200)
        self.assertEqual(self.forget("album", "al1"), 200)
        self.assertEqual(self.forget("album", "al1"), 200)
        self.assertEqual(self.backend.request("/hidden")[1], {"nextup": ["s1"], "albums": ["al1"], "books": []})
        self.assertEqual(self.backend.request("/settings")[1]["hidden"], 2)
        self.assertEqual(self.backend.request("/unhide", {})[0], 200)
        self.assertEqual(self.backend.request("/hidden")[1], {"nextup": [], "albums": [], "books": []})
        self.assertEqual(self.backend.request("/settings")[1]["hidden"], 0)

    def test_the_last_one_taken_off_is_put_back(self):
        undo = lambda: self.backend.request("/forget/undo", {})[0]
        self.assertEqual(undo(), 404, "nothing taken off yet")
        self.mock.lib.userdata["m1"]["PlaybackPositionTicks"] = 600 * TICKS
        self.assertEqual(self.forget("resume", "m1"), 200)
        self.assertEqual(undo(), 200)
        self.assertEqual(self.mock.lib.userdata["m1"]["PlaybackPositionTicks"], 600 * TICKS, "where it was stopped")
        self.assertEqual(undo(), 404, "once only")
        self.assertEqual(self.forget("nextup", "s0"), 200)
        self.assertEqual(self.forget("nextup", "s1"), 200)
        self.assertEqual(undo(), 200)
        self.assertEqual(self.backend.request("/hidden")[1]["nextup"], ["s0"], "the last one only")

    def test_checked(self):
        for what, value in (("everything", "m1"), ("resume", "../Users"), ("resume", ""), ("album", 5), ("nextup", None),
                            ("ytsearch", "x"), ("ytprogress", "abcdefghijk"), ("tvprogress", "x")):
            self.assertEqual(self.forget(what, value), 400, (what, value))
        self.assertEqual(self.backend.request("/hidden")[1], {"nextup": [], "albums": [], "books": []})


class SimilarTest(unittest.TestCase):
    """More Like This: worked out in the background and kept, so a detail page has its row at once."""

    def setUp(self):
        self.mock = MockJellyfin().start()
        self.backend = Backend(self.mock.url).start()
        self.titles = {i for i, it in self.mock.lib.items.items() if it["Type"] in ("Movie", "Series")}

    def tearDown(self):
        self.backend.stop()
        self.mock.stop()

    def looked_up(self):
        return [r.split("/")[2] for r in self.mock.requests if "/Similar" in r]

    def saved(self):
        try:
            return json.loads((self.backend.tmp / "cache" / "kino" / "similar.json").read_text())["items"]
        except (OSError, ValueError, KeyError):
            return {}

    def wait_all_known(self):
        wait_for(lambda: set(self.saved()) >= self.titles, msg="whole library looked up and saved")

    def test_whole_library_is_looked_up_in_the_background(self):
        self.wait_all_known()
        self.mock.similar_delay = 2
        start = time.time()
        code, data = self.backend.request("/similar/m1")
        self.assertEqual(code, 200)
        self.assertLess(time.time() - start, 0.5, "known at once, Jellyfin is not asked")
        self.assertEqual(data["ids"][:3], ["m4", "m7", "m10"])
        self.assertEqual(stat.S_IMODE(os.stat(self.backend.tmp / "cache" / "kino" / "similar.json").st_mode), 0o600)

    def test_kept_across_restarts(self):
        self.wait_all_known()
        self.backend.proc.terminate()
        self.backend.proc.wait(5)
        before = len(self.looked_up())
        self.mock.similar_delay = 2
        self.backend.start()
        start = time.time()
        self.assertEqual(self.backend.request("/similar/m1")[0], 200)
        self.assertLess(time.time() - start, 0.5, "known from the file")
        time.sleep(1)
        self.assertEqual(len(self.looked_up()), before, "nothing looked up again")

    def test_new_titles_are_looked_up_when_the_library_changes(self):
        self.wait_all_known()
        self.mock.lib.add_movies(1)
        self.mock.push("LibraryChanged")
        wait_for(lambda: "m13" in self.saved(), timeout=10, msg="new movie looked up")

    def test_title_without_a_list_is_looked_up_at_once(self):
        code, data = self.backend.request("/similar/se1")  # seasons are not worked through
        self.assertEqual(code, 200)
        self.assertIsInstance(data["ids"], list)

    def test_bad_id_is_rejected(self):
        self.assertEqual(self.backend.request("/similar/..%2Fsecret")[0], 404)

    def test_play_checks_its_id_and_a_body_must_be_an_object(self):
        self.assertEqual(self.backend.request("/play", {"itemId": "../Users"})[0], 400, "an id checked as the others are")
        self.assertEqual(self.backend.request("/play", {})[0], 400, "none at all")
        self.assertEqual(self.backend.request("/settings", ["not", "an", "object"])[0], 400, "said, not a broken connection")


class LanguageTest(unittest.TestCase):
    """The backend picks the UI language and hands it to the page via <html lang>."""

    def page_lang(self, **kw):
        mock = MockJellyfin().start()
        b = Backend(mock.url, **kw).start()
        try:
            return b.request("/")[1].split(b"<html lang=\"")[1][:2].decode()
        finally:
            b.stop()
            mock.stop()

    def test_language_from_config(self):
        self.assertEqual(self.page_lang(language="de"), "de")
        self.assertEqual(self.page_lang(language="en"), "en")

    def test_language_from_system_locale(self):
        self.assertEqual(self.page_lang(language=None, env={"LANG": "de_DE.UTF-8", "LC_ALL": ""}), "de")
        self.assertEqual(self.page_lang(language=None, env={"LANG": "en_US.UTF-8", "LC_ALL": ""}), "en")


class ServiceSetupTest(unittest.TestCase):
    """Navidrome and Audiobookshelf added, changed and removed from the settings while the backend runs."""

    def setUp(self):
        from mockabs import MockAudiobookshelf
        from mocknd import MockNavidrome
        self.mock = MockJellyfin().start()
        self.nd = MockNavidrome().start()
        self.abs = MockAudiobookshelf().start()
        self.b = Backend(self.mock.url).start()

    def tearDown(self):
        self.b.stop()
        self.abs.stop()
        self.nd.stop()
        self.mock.stop()

    def conf(self):
        return json.loads((self.b.conf / "config.json").read_text())

    def test_navidrome_from_the_settings(self):
        from mocknd import ND_PASSWORD, ND_USER
        self.assertEqual(self.b.request("/music/session")[1], {"configured": False})
        nd = {"service": "navidrome", "server": self.nd.url, "username": ND_USER}
        status, r = self.b.request("/service", {**nd, "password": "wrong"})
        self.assertEqual((status, r), (400, {"error": "service_rejected"}))
        self.assertNotIn("navidrome", self.conf(), "nothing kept when the sign-in fails")
        self.assertEqual(self.b.request("/music/session")[1], {"configured": False}, "and Matinee as before")
        self.assertEqual(self.b.request("/service", {**nd, "password": ND_PASSWORD})[0], 200)
        self.assertEqual(self.conf()["navidrome"], {"server": self.nd.url, "username": ND_USER})
        self.assertNotIn(ND_PASSWORD, (self.b.conf / "navidrome.json").read_text(), "only md5(password + salt)")
        self.assertEqual((self.b.conf / "navidrome.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.b.request("/music/session")[1], {"configured": True, "ready": True}, "at once, no restart")
        self.assertEqual(self.b.request("/nd/getArtists")[0], 200)
        self.assertEqual(self.b.request("/service", {"service": "navidrome", "remove": True})[0], 200)
        self.assertNotIn("navidrome", self.conf())
        self.assertFalse((self.b.conf / "navidrome.json").exists())
        self.assertEqual(self.b.request("/music/session")[1], {"configured": False})

    def test_audiobookshelf_from_the_settings(self):
        from mockabs import ABS_KEY
        host = self.abs.url.removeprefix("http://")
        self.assertEqual(self.b.request("/service", {"service": "audiobookshelf", "server": host, "key": ABS_KEY})[1],
                         {"error": "service_unreachable"}, "no scheme typed: https://, which the test server does not speak")
        self.assertEqual(self.b.request("/service", {"service": "audiobookshelf", "server": self.abs.url + "/", "key": "wrong"})[1],
                         {"error": "service_rejected"})
        self.assertEqual(self.b.request("/service", {"service": "audiobookshelf", "server": self.abs.url + "/", "key": ABS_KEY})[0], 200)
        self.assertEqual(self.conf()["audiobookshelf"], {"server": self.abs.url}, "without the slash at the end")
        self.assertEqual(self.b.request("/abs/session")[1], {"configured": True, "ready": True})
        # a wrong key later: the working one stays
        self.assertEqual(self.b.request("/service", {"service": "audiobookshelf", "server": self.abs.url, "key": "wrong"})[0], 400)
        self.assertEqual(self.b.request("/abs/session")[1], {"configured": True, "ready": True})

    def test_what_is_refused(self):
        for body, error in [({"service": "jellyfin", "server": "x", "password": "y"}, "service_unknown"),
                            ({"service": "navidrome", "server": "ftp://x", "username": "u", "password": "p"}, "service_bad_server"),
                            ({"service": "navidrome", "server": "https://u:p@x", "username": "u", "password": "p"}, "service_bad_server"),
                            ({"service": "navidrome", "server": self.nd.url, "password": "p"}, "service_missing_user"),
                            ({"service": "audiobookshelf", "server": self.abs.url}, "service_missing_secret")]:
            self.assertEqual(self.b.request("/service", body), (400, {"error": error}), body)
        self.assertNotIn("navidrome", self.conf())


if __name__ == "__main__":
    unittest.main()
