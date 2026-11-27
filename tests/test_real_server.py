"""Smoke test against the real Jellyfin from ~/.config/kino. Read-only, changes nothing.

Only runs with KINO_REAL=1, e.g.:  KINO_REAL=1 tests/run test_real_server
"""
import http.client
import json
import os
import shutil
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from helpers import Backend

REAL_CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kino"


@unittest.skipUnless(os.environ.get("KINO_REAL") == "1" and (REAL_CONF / "config.json").exists(),
                     "only with KINO_REAL=1 and a configured server")
class RealServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conf = json.loads((REAL_CONF / "config.json").read_text())
        cls.b = Backend(conf["server"], password=conf.get("password"), language=conf.get("language", "en"))
        (cls.b.conf / "config.json").write_text((REAL_CONF / "config.json").read_text())
        if (REAL_CONF / "state.json").exists():  # reuse the token, do not register a new device
            shutil.copy(REAL_CONF / "state.json", cls.b.conf / "state.json")
        cls.b.start()
        cls.user = cls.b.request("/session")[1]["userId"]
        cls.views = cls.get(f"/UserViews?userId={cls.user}")["Items"]

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()

    @classmethod
    def get(cls, path):
        code, data = cls.b.request("/jf" + path)
        assert code == 200, f"{path} → {code}"
        return data

    def lib(self, kind):
        v = [v for v in self.views if v.get("CollectionType") == kind]
        if not v:
            self.skipTest(f"no library of type {kind}")
        return v[0]["Id"]

    def test_home_requests(self):
        self.assertTrue(self.views)
        self.get(f"/UserItems/Resume?userId={self.user}&Limit=5&MediaTypes=Video")
        self.get(f"/Shows/NextUp?userId={self.user}&Limit=5")
        for v in self.views:
            if v.get("CollectionType") in ("movies", "tvshows"):
                self.assertIsInstance(self.get(f"/Items/Latest?userId={self.user}&parentId={v['Id']}&Limit=5"), list)

    def test_movie_has_everything_needed_to_play(self):
        lib = self.lib("movies")
        movie = self.get(f"/Items?userId={self.user}&parentId={lib}&Recursive=true&IncludeItemTypes=Movie&Limit=1")["Items"][0]
        item = self.get(f"/Items/{movie['Id']}?userId={self.user}")
        self.assertTrue(item["MediaSources"], "MediaSources missing")
        self.assertIn("UserData", item)

    def test_show_seasons_and_episodes(self):
        lib = self.lib("tvshows")
        series = self.get(f"/Items?userId={self.user}&parentId={lib}&Recursive=true&IncludeItemTypes=Series&Limit=1")["Items"][0]
        seasons = self.get(f"/Shows/{series['Id']}/Seasons?userId={self.user}")["Items"]
        self.assertTrue(seasons)
        eps = self.get(f"/Shows/{series['Id']}/Episodes?userId={self.user}&seasonId={seasons[-1]['Id']}")["Items"]
        self.assertTrue(eps)
        nxt = self.get(f"/Shows/{series['Id']}/Episodes?userId={self.user}&startItemId={eps[0]['Id']}&Limit=2")["Items"]
        self.assertEqual(nxt[0]["Id"], eps[0]["Id"], "startItemId works for the next episode")

    def test_image(self):
        lib = self.lib("movies")
        movie = self.get(f"/Items?userId={self.user}&parentId={lib}&Recursive=true&IncludeItemTypes=Movie&Limit=1")["Items"][0]
        conn = http.client.HTTPConnection("127.0.0.1", self.b.port, timeout=20)
        conn.request("GET", f"/jf/Items/{movie['Id']}/Images/Primary?maxWidth=200")
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("image/"))

    def test_stream_with_apikey_and_range(self):
        conf = json.loads((self.b.conf / "config.json").read_text())
        token = json.loads((self.b.conf / "state.json").read_text())["token"]
        lib = self.lib("movies")
        movie = self.get(f"/Items?userId={self.user}&parentId={lib}&Recursive=true&IncludeItemTypes=Movie&Limit=1")["Items"][0]
        req = urllib.request.Request(f"{conf['server'].rstrip('/')}/Videos/{movie['Id']}/stream?static=true&ApiKey={token}",
                                     headers={"Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=20) as r:
            self.assertEqual(r.status, 206, "the server must support range requests, otherwise mpv cannot seek")

    def test_live_connection_stays_up(self):
        events = []
        conn = http.client.HTTPConnection("127.0.0.1", self.b.port, timeout=40)
        conn.request("GET", "/events")
        resp = conn.getresponse()
        threading.Thread(target=lambda: [events.append(l) for l in resp], daemon=True).start()
        time.sleep(35)  # longer than half of Jellyfin's keep-alive
        self.assertNotIn("connection to Jellyfin lost", self.b.logtext(), "WebSocket to Jellyfin dropped")
        conn.close()


if __name__ == "__main__":
    unittest.main()
