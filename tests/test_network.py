"""The network from the sofa (backend/network.py): Wi-Fi through a fake NetworkManager, and the Jellyfin servers
that answer Jellyfin's discovery on the local network."""
import json
import socket
import threading
import unittest
from pathlib import Path

from helpers import ROOT, Backend, temp_dir
from mockjf import MockJellyfin

NETWORKS = [{"ssid": "Home", "signal": 80, "security": "WPA2", "password": "correct horse"},
            {"ssid": "Home", "signal": 30, "security": "WPA2", "password": "correct horse"},  # a second access point
            {"ssid": "Café: Guests", "signal": 55, "security": ""},  # open, a colon in its name
            {"ssid": "", "signal": 90, "security": "WPA2"}]  # hidden: nothing to choose it by


def fake_network(tmp, **state):
    tmp = Path(tmp)
    (tmp / "nm.json").write_text(json.dumps({"networks": NETWORKS, "known": [], "active": None, **state}))
    fake = tmp / "nmcli"
    fake.symlink_to(ROOT / "tests" / "fake-nmcli")
    return {"KINO_NMCLI": str(fake), "FAKE_NM_STATE": str(tmp / "nm.json"), "FAKE_NM_LOG": str(tmp / "nm.log")}


class Discovery:
    """A Jellyfin server answering "who is JellyfinServer?" on a UDP port of its own."""

    def __init__(self, answers):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.addr = f"127.0.0.1:{self.sock.getsockname()[1]}"
        self.answers = answers
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                data, peer = self.sock.recvfrom(1024)
            except OSError:
                return
            if data == b"who is JellyfinServer?":
                for a in self.answers:
                    self.sock.sendto(a if isinstance(a, bytes) else json.dumps(a).encode(), peer)

    def stop(self):
        self.sock.close()


class WifiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(temp_dir("kino-net-"))
        self.mock = MockJellyfin().start()
        self.addCleanup(self.mock.stop)
        self.env = fake_network(self.tmp, known=["Home"], active="Home")
        self.backend = Backend(self.mock.url, env=self.env).start()
        self.addCleanup(self.backend.stop)

    def state(self):
        return json.loads((self.tmp / "nm.json").read_text())

    def test_the_networks_nearby(self):
        d = self.backend.request("/wifi")[1]
        self.assertTrue(d["available"])
        self.assertEqual([(n["ssid"], n["signal"], n["secure"], n["active"], n["known"]) for n in d["networks"]],
                         [("Home", 80, True, True, True), ("Café: Guests", 55, False, False, False)],
                         "the one in use first, each name once, none without a name")
        self.assertTrue(self.backend.request("/settings")[1]["wifi"], "the settings offer it")

    def test_joining_with_the_password_and_without_it(self):
        state = self.state()
        state.update(known=[], active=None)
        (self.tmp / "nm.json").write_text(json.dumps(state))
        code, r = self.backend.request("/wifi/connect", {"ssid": "Home", "password": "wrong one"})
        self.assertEqual((code, r), (502, {"error": "wifi_password"}))
        self.assertEqual(self.state()["known"], [], "not kept with a wrong password")
        self.assertEqual(self.backend.request("/wifi/connect", {"ssid": "Home", "password": "short"})[0], 400,
                         "WPA's passwords have 8 characters at least")
        code, r = self.backend.request("/wifi/connect", {"ssid": "Home", "password": "correct horse"})
        self.assertEqual(code, 200)
        self.assertEqual([n["ssid"] for n in r["networks"] if n["active"]], ["Home"])
        code, r = self.backend.request("/wifi/connect", {"ssid": "Café: Guests"})
        self.assertEqual(code, 200, "an open one without")
        self.assertEqual(self.state()["active"], "Café: Guests")
        code, r = self.backend.request("/wifi/connect", {"ssid": "Home"})
        self.assertEqual(code, 200, "a known one as it is kept")
        self.assertIn('["-t", "--wait", "45", "connection", "up", "id", "Home"]', (self.tmp / "nm.log").read_text())

    def test_forgotten(self):
        code, r = self.backend.request("/wifi/forget", {"ssid": "Home"})
        self.assertEqual(code, 200)
        self.assertEqual(self.state()["known"], [])
        self.assertEqual(self.backend.request("/wifi/forget", {"ssid": "x" * 40})[0], 400, "no such name")

    def test_none_without_networkmanager(self):
        b = Backend(self.mock.url, env={"KINO_NMCLI": str(self.tmp / "none")}).start()
        try:
            self.assertEqual(b.request("/wifi")[1], {"available": False, "networks": []})
            self.assertFalse(b.request("/settings")[1]["wifi"])
        finally:
            b.stop()


class DiscoveryTest(unittest.TestCase):
    def test_the_servers_that_answer(self):
        server = Discovery([{"Address": "http://192.168.1.10:8096/", "Id": "a1", "Name": "Living Room"},
                            {"Address": "http://192.168.1.10:8096", "Id": "a1", "Name": "Living Room"},  # twice
                            {"Address": "javascript:alert(1)", "Id": "b2", "Name": "Odd"}, b"not json"])
        mock = MockJellyfin().start()
        b = Backend(mock.url, env={"KINO_JF_DISCOVER": server.addr, "KINO_JF_DISCOVER_WAIT": "0.5"}).start()
        try:
            self.assertEqual(b.request("/jellyfin/discover")[1],
                             {"servers": [{"name": "Living Room", "address": "http://192.168.1.10:8096"}]})
        finally:
            b.stop()
            mock.stop()
            server.stop()


if __name__ == "__main__":
    unittest.main()
