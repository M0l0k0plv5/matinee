"""The account handed over to another device (over Tailscale, with a code), and signing out."""

import http.client
import json
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import PASSWORD, USERNAME, Backend, free_port, wait_for
from mockjf import TOKEN, USER_ID, MockJellyfin


def fake_tailscale(where):
    """A tailnet of two: this device and the other one, both at 127.0.0.1 (the tests' two backends). where: a backend
    or a directory."""
    status = {"BackendState": "Running", "Self": {"HostName": "here", "TailscaleIPs": ["127.0.0.1"]},
              "Peer": {"a": {"HostName": "there", "Online": True, "TailscaleIPs": ["127.0.0.1", "fd7a::2"]},
                       "b": {"HostName": "away", "Online": False, "TailscaleIPs": ["100.64.0.9"]}}}
    fake = Path(getattr(where, "tmp", where)) / "tailscale"
    fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(status) + "\nEOF\n")
    fake.chmod(0o755)
    return str(fake)


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        port = str(free_port())
        # the Mac: signed in everywhere, with its own things and settings
        self.src = Backend(self.mock.url, conf={"effects": "plain", "skipintro": "auto", "device_name": "Desk Mac",
                                                "podfetch": {"server": "https://pf.example", "username": "me"}})
        (self.src.conf / "hidden.json").write_text(json.dumps({"nextup": ["s1"]}))
        (self.src.conf / "podfetch.json").write_text(json.dumps({"password": "pf-secret"}))
        # the thin client: freshly installed, its own screen setting
        self.dst = Backend("", password=None, conf={"effects": "light", "language": "de", "device_name": "Café’s \"TV\""})
        for b in (self.src, self.dst):
            b.env.update(KINO_TAILSCALE=fake_tailscale(b), KINO_TRANSFER_PORT=port)
        self.src.start()
        self.dst.start(wait_ready=False)

    def tearDown(self):
        self.src.stop()
        self.dst.stop()
        self.mock.stop()

    def files(self, b, name):
        path = b.conf / name
        return json.loads(path.read_text()) if path.exists() else None

    def restarted(self, b, what):
        """Matinee started again after `what` (in the log): the page's cookie anew."""
        wait_for(lambda: what in b.logtext(), msg=what)
        runs = b.logtext().count("Matinee running")
        wait_for(lambda: b.logtext().count("Matinee running") > runs, timeout=15, msg="started again")
        b.cookie = ""

    def test_hand_over_and_sign_out(self):
        self.assertEqual(self.dst.request("/session")[1]["error"], "setup", "a fresh install")
        early = lambda film: {"early": [{"id": film, "pos": 180, "at": 1}]}  # a film stopped in its first minutes
        (self.src.conf / "early.json").write_text(json.dumps(early("f" * 32)))
        (self.dst.conf / "early.json").write_text(json.dumps(early("e" * 32)))  # someone's before
        self.assertEqual(self.src.request("/transfer/devices", {})[1], {"devices": []}, "nobody waiting yet")
        code, wait = self.dst.request("/transfer/wait", {})
        self.assertEqual(code, 200)
        self.assertRegex(wait["code"], r"^\d{6}$")
        self.assertEqual(wait["name"], "Cafe's TV", "its own name, in ASCII (for Jellyfin's header) and without quotes")
        self.assertEqual(self.src.request("/transfer/devices", {})[1], {"devices": [{"name": "Cafe's TV", "ip": "127.0.0.1"}]})

        send = lambda code, password=PASSWORD, ip="127.0.0.1": self.src.request("/transfer/send", {"ip": ip, "code": code, "password": password})
        self.assertEqual(send(wait["code"], "wrong")[1], {"error": "service_rejected"}, "Jellyfin's password first")
        wrong = f"{(int(wait['code']) + 1) % 10 ** 6:06d}"
        self.assertEqual(send(wrong)[1], {"error": "transfer_code"})
        self.assertEqual(self.mock.logouts, self.mock.logins[-1:], "the sign-in for a wrong code ended again")
        self.assertEqual(send(wait["code"], ip="100.64.0.1")[1], {"error": "transfer_gone"}, "only a device of the tailnet")
        self.assertEqual(send(wait["code"]), (200, {"name": "Cafe's TV"}))
        self.restarted(self.dst, "Account taken over from")
        wait_for(lambda: self.dst.request("/session")[0] == 200, timeout=15, msg="signed in to Jellyfin")

        conf = self.files(self.dst, "config.json")
        self.assertEqual((conf["server"], conf["username"], conf["skipintro"], conf["podfetch"]),
                         (self.mock.url, USERNAME, "auto", {"server": "https://pf.example", "username": "me"}))
        self.assertEqual((conf["effects"], conf["language"]), ("light", "en"), "the device's own kept, the person's taken")
        self.assertEqual(conf["device_name"], "Café’s \"TV\"", "its own name, not the Mac's")
        self.assertNotIn("password", conf)
        self.assertEqual(self.files(self.dst, "hidden.json")["nextup"], ["s1"])
        self.assertEqual(self.files(self.dst, "podfetch.json"), {"password": "pf-secret"})
        self.assertEqual(self.files(self.dst, "early.json"), early("f" * 32), "the films stopped early: the account's, not the one's before")
        dst_state, src_state = self.files(self.dst, "state.json"), self.files(self.src, "state.json")
        self.assertEqual((dst_state["token"], dst_state["user_id"]), (TOKEN, USER_ID))
        self.assertNotEqual(dst_state["device_id"], src_state["device_id"], "a device of its own to Jellyfin")
        self.assertEqual(self.mock.logins[-1], dst_state["device_id"], "signed in by the Mac, for the thin client")
        self.assertEqual(self.src.request("/transfer/devices", {})[1], {"devices": []}, "taken once, then closed")
        for name in ("config.json.bak", "state.json.bak"):
            self.assertIsNone(self.files(self.dst, name), f"nothing of the account before in {name}")

        # given away: signed out, as freshly installed with its own settings
        device = dst_state["device_id"]
        self.assertEqual(self.dst.request("/welcome/done", {})[0], 200)  # the next person goes through it again
        self.assertEqual(self.dst.request("/signout", {})[0], 200)
        self.restarted(self.dst, "Signed out: starting Matinee again")
        wait_for(lambda: self.dst.request("/session")[1].get("error") == "setup", timeout=15, msg="the start screen")
        self.assertEqual(self.files(self.dst, "config.json"), {"effects": "light", "language": "en", "device_name": "Café’s \"TV\""})
        for name in ("hidden.json", "podfetch.json", "pending.json", "early.json"):
            self.assertIsNone(self.files(self.dst, name), name)
        new = self.files(self.dst, "state.json")
        self.assertNotIn("token", new)
        for name in ("config.json.bak", "state.json.bak", "hidden.json.bak"):
            self.assertIsNone(self.files(self.dst, name), f"the account cannot come back from {name}")
        self.assertNotEqual(new["device_id"], device)
        self.assertIn(device, self.mock.logouts, "its session on Jellyfin ended")
        self.assertIsNotNone(self.files(self.src, "hidden.json"), "the Mac keeps everything")

    def test_a_bad_account_leaves_this_one_syncing(self):
        mine = Backend(self.mock.url, env={"KINO_SYNC_EVERY": "0.5", "KINO_SYNC_DEBOUNCE": "0.1"})
        mine.env.update(KINO_TAILSCALE=fake_tailscale(mine), KINO_TRANSFER_PORT=str(free_port()))
        mine.start()
        try:
            wait = mine.request("/transfer/wait", {})[1]
            req = urllib.request.Request(f"http://127.0.0.1:{mine.env['KINO_TRANSFER_PORT']}/transfer/give", method="POST",
                                         data=json.dumps({"code": wait["code"], "account": {"config": "nonsense"}}).encode())
            with self.assertRaises(urllib.error.HTTPError) as answer:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(answer.exception.code, 400)
            mine.request("/transfer/cancel", {})
            self.assertEqual(mine.request("/settings", {"skipintro": "auto"})[0], 200)
            wait_for(lambda: "skipintro" in json.dumps(self.mock.display_prefs.get(("kino", "kino"))), timeout=8,
                     msg="nothing taken: synced as before")
        finally:
            mine.stop()

    def test_an_account_brings_only_settings_the_page_could_set(self):
        """Whoever has the code sends an account: of its config.json only the person's settings with allowed values,
        the servers and the user name are taken; unknown keys, wrong values and the device's own are left out."""
        mine = Backend(self.mock.url, conf={"display": "1920x1080@60"})
        mine.env.update(KINO_TAILSCALE=fake_tailscale(mine), KINO_TRANSFER_PORT=str(free_port()))
        mine.start()
        try:
            wait = mine.request("/transfer/wait", {})[1]
            config = {"skipintro": "auto", "subsize": "xl", "username": "them", "server": "https://jf.example",
                      "podfetch": {"server": "https://pf.example", "username": "them"},
                      "quality": "--script=/tmp/x.lua", "sounds": True, "mpv_args": ["--script=/tmp/x.lua"],
                      "display": "640x480@30", "navidrome": {"server": "file:///etc/passwd"}, "menupicture": "../x"}
            req = urllib.request.Request(f"http://127.0.0.1:{mine.env['KINO_TRANSFER_PORT']}/transfer/give", method="POST",
                                         data=json.dumps({"code": wait["code"], "account": {"config": config, "files": {}}}).encode())
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(r.status, 200)
            taken = json.loads((mine.conf / "config.json").read_text())
            for k, v in {"skipintro": "auto", "subsize": "xl", "username": "them", "server": "https://jf.example",
                         "podfetch": {"server": "https://pf.example", "username": "them"}, "display": "1920x1080@60"}.items():
                self.assertEqual(taken.get(k), v, k)
            for k in ("quality", "sounds", "mpv_args", "navidrome", "menupicture"):
                self.assertNotIn(k, taken, k)
        finally:
            mine.stop()

    def test_a_length_that_is_no_size_is_refused(self):
        """Content-Length negative (read to the end, without a limit) or no number: refused at once."""
        mine = Backend(self.mock.url)
        mine.env.update(KINO_TAILSCALE=fake_tailscale(mine), KINO_TRANSFER_PORT=str(free_port()))
        mine.start()
        try:
            mine.request("/transfer/wait", {})
            for length in ("-1", "abc"):
                conn = http.client.HTTPConnection("127.0.0.1", int(mine.env["KINO_TRANSFER_PORT"]), timeout=5)
                conn.putrequest("POST", "/transfer/give")
                conn.putheader("Content-Length", length)
                conn.endheaders()
                conn.send(b"{")  # and the connection left open: a read to the end would wait here
                self.assertEqual(conn.getresponse().status, 404, length)
                conn.close()
            mine.request("/transfer/cancel", {})
        finally:
            mine.stop()

    def test_waiting_ends(self):
        wait = self.dst.request("/transfer/wait", {})[1]
        send = lambda code: self.src.request("/transfer/send", {"ip": "127.0.0.1", "code": code, "password": PASSWORD})[1]
        for i in range(5):  # guessed: at the fifth wrong code it stops waiting, the right one comes too late
            self.assertEqual(send(f"{(int(wait['code']) + 1 + i) % 10 ** 6:06d}"), {"error": "transfer_code"})
        self.assertEqual(send(wait["code"]), {"error": "transfer_gone"})
        self.dst.request("/transfer/wait", {})
        self.dst.request("/transfer/cancel", {})
        wait_for(lambda: self.src.request("/transfer/devices", {})[1] == {"devices": []}, msg="cancelled: closed")
        self.assertEqual(self.files(self.dst, "config.json")["language"], "de", "nothing taken")
        self.assertEqual(send("12ab56"), {"error": "transfer_code"})
        time.sleep(1)  # closed for good (its port let go)
        code, again = self.dst.request("/transfer/wait", {})
        self.assertEqual(code, 200, f"waiting once more opens the door again: {again}")
        wait_for(lambda: self.src.request("/transfer/devices", {})[1]["devices"], msg="seen again")
        self.dst.request("/transfer/cancel", {})

    def test_without_tailscale(self):
        self.src.stop()
        self.src = Backend(self.mock.url).start()
        self.assertEqual(self.src.request("/transfer/wait", {})[1], {"error": "transfer_no_tailscale"})
        self.assertEqual(self.src.request("/transfer/devices", {})[1], {"error": "transfer_no_tailscale"})


if __name__ == "__main__":
    unittest.main()
