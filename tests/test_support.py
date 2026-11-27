"""A report to support (backend/support.py) sent to deploy/support-inbox, and what the inbox takes."""
import subprocess
import sys
import tarfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import ROOT, Backend, free_port, temp_dir
from mockjf import MockJellyfin


class Inbox:
    def __init__(self):
        self.dir = Path(temp_dir("kino-inbox-"))
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "deploy" / "support-inbox"), "--dir", str(self.dir / "in"),
                                      "--port", str(self.port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.url = f"http://127.0.0.1:{self.port}/report"
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url, timeout=1)
            except urllib.error.HTTPError:
                return  # it answers (404 to anything but a report)
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the inbox did not start")

    def post(self, path, data, headers=None):
        req = urllib.request.Request(f"{self.url}{path}", data=data, method="POST", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)
        self.proc.stdout.close()


class SupportTest(unittest.TestCase):
    def setUp(self):
        self.inbox = Inbox()
        self.addCleanup(self.inbox.stop)
        self.mock = MockJellyfin().start()
        self.addCleanup(self.mock.stop)

    def test_a_report_sent_and_filed_under_its_code(self):
        b = Backend(self.mock.url, env={"KINO_SUPPORT_URL": self.inbox.url}).start()
        try:
            self.assertTrue(b.request("/settings")[1]["support"], "the settings offer it")
            code, r = b.request("/support/send", {})
            self.assertEqual(code, 200, r)
            self.assertRegex(r["code"], r"^[ACDEFGHJKLMNPQRTUVWXY3479]{3}-[ACDEFGHJKLMNPQRTUVWXY3479]{3}$")
            files = list((self.inbox.dir / "in").iterdir())
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].name.endswith(f"-{r['code'].replace('-', '')}.tar.gz"))
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600, "for the one who supports alone")
            with tarfile.open(files[0]) as tar:
                names = {Path(n).name for n in tar.getnames()}
                self.assertTrue({"summary.txt", "config.txt"} <= names, names)
                conf = tar.extractfile(next(m for m in tar.getmembers() if Path(m.name).name == "config.txt")).read().decode()
            self.assertIn(self.mock.url, conf, "the settings, to see how it is set up")
            for f in Path(b.conf).iterdir():  # what could sign someone in stays home
                if "token" in f.name:
                    self.assertNotIn(f.read_text().strip(), conf)
        finally:
            b.stop()

    def test_not_offered_without_an_inbox_or_over_plain_http(self):
        for url in ("", "http://support.example.com/report", "ftp://example.com"):
            b = Backend(self.mock.url, env={"KINO_SUPPORT_URL": url}).start()
            try:
                self.assertFalse(b.request("/settings")[1]["support"], url)
                self.assertEqual(b.request("/support/send", {}), (502, {"error": "support_off"}))
            finally:
                b.stop()

    def test_what_the_inbox_takes(self):
        gz = b"\x1f\x8b" + b"x" * 100
        self.assertEqual(self.inbox.post("/ACD347", b"not gzip"), 400)
        self.assertEqual(self.inbox.post("/ABC123", gz), 404, "letters a code never has")
        self.assertEqual(self.inbox.post("/../../x", gz), 404)
        self.assertEqual(self.inbox.post("/ACD347", gz, {"Content-Length": str(30 * 1024 * 1024)}), 413)
        self.assertEqual(self.inbox.post("/ACD347", gz), 200)
        self.assertEqual(len(list((self.inbox.dir / "in").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
