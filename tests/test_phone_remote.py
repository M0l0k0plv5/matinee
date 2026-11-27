"""The phone remote served by the device itself (backend/phone_remote.py) and the QR code of its address (backend/qr.py)."""
import hashlib
import http.client
import json
import sys
import unittest
import urllib.parse

from helpers import ROOT, Backend
from mockjf import MockJellyfin

sys.path.insert(0, str(ROOT))
from backend import qr  # noqa: E402

# Matrices checked module for module against the reference library "qrcode" (byte mode, level M, its mask)
SMALL = ("1111111011000100101111111100000100000110000100000110111010100011100010111011011101000100101001011101101110100100"
         "0100101011101100000101101100000100000111111110101010101011111110000000000011001000000000101000110011001100010010"
         "1111101010110011101100101100101110010100111110111010010110011000001101011000001100101111101011100000101000001110"
         "0111100100001111010010000011111000111010011010111011000001000000110010101000101011111001000000000111000111000101"
         "0111111110100100001010110011000001001111010100010010101110100010101111111101010111010010100101001111101011101011"
         "01000010011001110000010001110011001100001111111010110011101001001")


def bits(m):
    return "".join("1" if c else "0" for row in m for c in row)


class QrTest(unittest.TestCase):
    def test_codes_as_the_reference_makes_them(self):
        self.assertEqual(bits(qr.matrix("http://192.168.2.20:8767/")), SMALL, "version 2")
        big = qr.matrix("http://[fd7a:115c:a1e0::1]:8767/" + "x" * 150)
        self.assertEqual(len(big), 57, "version 10: two bytes for the length, blocks of two sizes")
        self.assertEqual(hashlib.sha256(bits(big).encode()).hexdigest(), "4a802c3c763457a202a00120a3caa93dea17bd9b3d0a68f68b03acd7b963c7f8")
        with self.assertRaises(ValueError):
            qr.matrix("x" * 214)

    def test_svg_with_its_quiet_zone(self):
        svg = qr.svg("http://192.168.2.20:8767/")
        self.assertTrue(svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 33 33"'), "25 modules and 4 around")
        self.assertEqual(svg.count("h1v1h-1z"), SMALL.count("1"))


class PhoneRemoteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = MockJellyfin().start()
        cls.backend = Backend(cls.mock.url).start()

    @classmethod
    def tearDownClass(cls):
        cls.backend.stop()
        cls.mock.stop()

    def get(self, url, method="GET"):
        u = urllib.parse.urlsplit(url)
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        c.request(method, u.path)
        r = c.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()

    def test_off_until_turned_on_then_only_the_remote(self):
        b = self.backend
        self.assertEqual(b.request("/settings")[1]["phoneRemote"], {"on": False, "url": None}, "off unless turned on")
        self.assertEqual(b.request("/phoneremote.svg")[0], 404)
        self.assertEqual(b.request("/settings", {"phoneremote": "on"})[0], 200)
        try:
            p = b.request("/settings")[1]["phoneRemote"]
            self.assertTrue(p["on"])
            self.assertRegex(p["url"], r"^http://127\.0\.0\.1:\d+/$")
            self.assertNotEqual(p["url"], f"http://127.0.0.1:{b.port}/", "a server of its own, not the backend")
            status, ctype, page = self.get(p["url"])
            self.assertEqual((status, ctype), (200, "text/html; charset=utf-8"))
            self.assertEqual(page, (ROOT / "remote" / "index.html").read_bytes())
            self.assertEqual(self.get(p["url"] + "app.js")[0], 200)
            self.assertEqual(json.loads(self.get(p["url"] + "config.json")[2]), {"server": self.mock.url}, "Jellyfin's address prefilled")
            for other in ("settings", "README.md", "../backend/core.py", "%2e%2e/server.py", "transfer/give", "jf/Users"):
                self.assertEqual(self.get(p["url"] + other)[0], 404, other)
            self.assertEqual(self.get(p["url"], "POST")[0], 405)
            c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=10)
            c.request("GET", "/phoneremote.svg", headers={"Cookie": b.page_cookie()})
            r = c.getresponse()
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/svg+xml"))
            self.assertEqual(r.read().decode(), qr.svg(p["url"]), "its address as a QR code")
        finally:
            self.assertEqual(b.request("/settings", {"phoneremote": "off"})[0], 200)
        with self.assertRaises(OSError):
            self.get(p["url"])
        self.assertEqual(b.request("/settings")[1]["phoneRemote"], {"on": False, "url": None})
        self.assertEqual(b.request("/settings", {"phoneremote": "on"})[0], 200, "on again at once")
        try:
            self.assertEqual(self.get(b.request("/settings")[1]["phoneRemote"]["url"])[0], 200)
        finally:
            b.request("/settings", {"phoneremote": "off"})


if __name__ == "__main__":
    unittest.main()
