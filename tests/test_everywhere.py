"""Settings → Account → Sign In Everywhere: one user name and password for every service set up on a device, and
for those found on Jellyfin's computer."""
import json
import unittest

from helpers import USERNAME, Backend
from mockabs import ABS_KEY, MockAudiobookshelf
from mockjf import MockJellyfin
from mocknd import ND_PASSWORD, ND_USER, MockNavidrome
from mockpf import PF_PASSWORD, PF_USER, MockPodfetch

# a second person with the same user name and password on every service (invented)
OTHER, OTHER_PASSWORD = "robin", "Same-Everywhere-7"


class SignInEverywhereTest(unittest.TestCase):
    def setUp(self):
        self.jf, self.nd, self.abs, self.pf = MockJellyfin().start(), MockNavidrome().start(), MockAudiobookshelf().start(), MockPodfetch().start()
        self.b = Backend(self.jf.url).start()
        for svc, body in (("navidrome", {"server": self.nd.url, "username": ND_USER, "password": ND_PASSWORD}),
                          ("audiobookshelf", {"server": self.abs.url, "key": ABS_KEY}),
                          ("podfetch", {"server": self.pf.url, "username": PF_USER, "password": PF_PASSWORD})):
            self.assertEqual(self.b.request("/service", {"service": svc, **body})[0], 200, svc)

    def tearDown(self):
        self.b.stop()
        for m in (self.jf, self.nd, self.abs, self.pf):
            m.stop()

    def ready(self):
        return {svc: self.b.request(path)[1].get("ready")
                for svc, path in (("navidrome", "/music/session"), ("audiobookshelf", "/abs/session"), ("podfetch", "/pf/session"))}

    def test_one_sign_in_for_all(self):
        for m in (self.jf, self.nd, self.abs, self.pf):
            m.accounts[OTHER] = OTHER_PASSWORD
        code, r = self.b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD})
        self.assertEqual(code, 200)
        self.assertEqual(r["results"], {"navidrome": "ok", "audiobookshelf": "ok", "podfetch": "ok", "jellyfin": "ok"})
        s = self.b.request("/settings")[1]
        self.assertEqual((s["username"], s["navidrome"]["username"], s["podfetch"]["username"]), (OTHER, OTHER, OTHER))
        self.assertEqual(json.loads((self.b.conf / "audiobookshelf.json").read_text()), {"token": f"token-{OTHER}"},
                         "Audiobookshelf's own token for them, got with their password")
        self.assertEqual(self.ready(), {"navidrome": True, "audiobookshelf": True, "podfetch": True})
        self.assertNotIn(OTHER_PASSWORD, (self.b.conf / "config.json").read_text(), "no password in the settings")

    def test_refused_where_unknown_the_rest_as_it_was(self):
        self.nd.accounts[OTHER] = OTHER_PASSWORD  # only Navidrome knows them
        code, r = self.b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD})
        self.assertEqual(r["results"], {"navidrome": "ok", "audiobookshelf": "rejected", "podfetch": "rejected", "jellyfin": "rejected"})
        s = self.b.request("/settings")[1]
        self.assertEqual((s["username"], s["navidrome"]["username"], s["podfetch"]["username"]), (USERNAME, OTHER, PF_USER))
        self.assertEqual(self.ready(), {"navidrome": True, "audiobookshelf": True, "podfetch": True}, "still signed in as before")
        self.assertEqual(json.loads((self.b.conf / "audiobookshelf.json").read_text()), {"token": ABS_KEY})

    def test_a_service_not_set_up_and_one_away(self):
        self.b.request("/service", {"service": "podfetch", "remove": True})
        self.abs.stop()
        self.nd.accounts[OTHER] = self.jf.accounts[OTHER] = OTHER_PASSWORD
        r = self.b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD})[1]
        self.assertEqual(r["results"], {"navidrome": "ok", "audiobookshelf": "unreachable", "podfetch": "not_set_up", "jellyfin": "ok"})
        self.abs.start()

    def test_needs_a_user_name(self):
        self.assertEqual(self.b.request("/signin/everywhere", {"username": " ", "password": "x"}), (400, {"error": "service_missing_user"}))


class FoundBesideJellyfinTest(unittest.TestCase):
    """A new TV computer with only Jellyfin set up: music, audiobooks and podcasts found on its computer."""

    def test_found_and_signed_in(self):
        jf, nd, ab, pf = MockJellyfin().start(), MockNavidrome().start(), MockAudiobookshelf().start(), MockPodfetch().start()
        ports = {"navidrome": [nd.port], "audiobookshelf": [jf.port, ab.port], "podfetch": [pf.port]}  # Jellyfin is not it
        b = Backend(jf.url, env={"KINO_BESIDE_PORTS": json.dumps(ports)}).start()
        try:
            for m in (jf, nd, ab, pf):
                m.accounts[OTHER] = OTHER_PASSWORD
            code, r = b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD})
            self.assertEqual((code, r["results"]), (200, {"navidrome": "ok", "audiobookshelf": "ok", "podfetch": "ok", "jellyfin": "ok"}))
            s = b.request("/settings")[1]
            self.assertEqual((s["navidrome"]["server"], s["audiobookshelf"], s["podfetch"]["server"]), (nd.url, ab.url, pf.url))
        finally:
            b.stop()
            for m in (jf, nd, ab, pf):
                m.stop()

    def test_a_new_tv_computer_without_jellyfin(self):
        """Nothing set up at all: Jellyfin's address comes with the sign-in, the others are found beside it."""
        jf, nd = MockJellyfin().start(), MockNavidrome().start()
        ports = {"navidrome": [nd.port], "audiobookshelf": [jf.port], "podfetch": [jf.port]}
        b = Backend("", password=None, env={"KINO_BESIDE_PORTS": json.dumps(ports)}).start(wait_ready=False)
        try:
            jf.accounts[OTHER] = nd.accounts[OTHER] = OTHER_PASSWORD
            self.assertEqual(b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD}),
                             (400, {"error": "everywhere_need_server"}))
            self.assertEqual(b.request("/signin/everywhere", {"username": OTHER, "password": "x", "server": "ftp://media.example"}),
                             (400, {"error": "service_bad_server"}))
            code, r = b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD, "server": jf.url})
            self.assertEqual((code, r["results"]), (200, {"navidrome": "ok", "audiobookshelf": "not_set_up", "podfetch": "not_set_up", "jellyfin": "ok"}))
            s = b.request("/settings")[1]
            self.assertEqual((s["server"], s["username"], s["navidrome"]["server"]), (jf.url, OTHER, nd.url))
        finally:
            b.stop()
            jf.stop()
            nd.stop()

    def test_without_jellyfin_the_services_set_up(self):
        """No Jellyfin and no address for it, but Navidrome set up: signed in there all the same."""
        nd = MockNavidrome().start()
        b = Backend("", password=None).start(wait_ready=False)
        try:
            nd.accounts[OTHER] = OTHER_PASSWORD
            self.assertEqual(b.request("/service", {"service": "navidrome", "server": nd.url, "username": ND_USER, "password": ND_PASSWORD})[0], 200)
            code, r = b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD})
            self.assertEqual((code, r["results"]), (200, {"navidrome": "ok", "audiobookshelf": "not_set_up", "podfetch": "not_set_up",
                                                         "jellyfin": "not_set_up"}))
        finally:
            b.stop()
            nd.stop()

    def test_none_there(self):
        jf = MockJellyfin().start()
        b = Backend(jf.url, env={"KINO_BESIDE_PORTS": json.dumps({"navidrome": [jf.port], "audiobookshelf": [jf.port], "podfetch": [jf.port]})}).start()
        try:
            jf.accounts[OTHER] = OTHER_PASSWORD
            r = b.request("/signin/everywhere", {"username": OTHER, "password": OTHER_PASSWORD})[1]
            self.assertEqual(r["results"], {"navidrome": "not_set_up", "audiobookshelf": "not_set_up", "podfetch": "not_set_up", "jellyfin": "ok"})
        finally:
            b.stop()
            jf.stop()


if __name__ == "__main__":
    unittest.main()
