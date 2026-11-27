"""Updates from signed packages (backend/release.py, deploy/get-kino): built, checked, installed, gone back from."""
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path

from helpers import ROOT

sys.path.insert(0, str(ROOT / "backend"))
import release  # noqa: E402

HAVE_TOOLS = all(shutil.which(t) for t in ("git", "ssh-keygen"))
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout.strip()


def working_tree_repo(into):
    """A repository of Matinee as it is now (changes not committed yet too): what a release of it would hold."""
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"], check=True,
                           capture_output=True, text=True).stdout.split("\0")
    for name in filter(None, files):
        src = ROOT / name
        if src.is_file():
            (into / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, into / name)
    git(into, "init", "-q", "-b", "main")
    git(into, "add", "-A")
    git(into, "commit", "-qm", "First version")
    return into


def key_pair(d, name="key"):
    """A signing key of its own and the signers file that trusts it, as deploy/release-signers does Matinee's."""
    key = d / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    pub = " ".join(key.with_suffix(".pub").read_text().split()[:2])
    signers = d / f"{name}-signers"
    signers.write_text(f'kino namespaces="kino-release" {pub}\n')
    return key, signers


class Server:
    """The packages' place on the internet: a folder served over HTTP."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    def __init__(self, folder):
        handler = functools.partial(self.Quiet, directory=str(folder))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/manifest.json"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(HAVE_TOOLS, "needs git and ssh-keygen")
class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kino-release-")).resolve()  # macOS: /var is /private/var
        cls.key, cls.signers = key_pair(cls.tmp)
        cls.repo = cls.tmp / "repo"
        cls.repo.mkdir()
        working_tree_repo(cls.repo)
        # its versions trust the test's key, as Matinee's trust Matinee's (deploy/release-signers, also in get-kino)
        shutil.copy(cls.signers, cls.repo / "deploy" / "release-signers")
        git(cls.repo, "commit", "-qam", "The test's key")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=self.tmp))
        self.srv_dir = self.d / "srv"
        self.server = Server(self.srv_dir)
        self.addCleanup(self.server.stop)
        self.link = self.d / "home" / "kino"
        self.link.parent.mkdir()

    def publish(self, manifest_url=""):
        release.build(self.repo, self.srv_dir, self.key, "", manifest_url)
        return release.manifest(self.server.url, self.signers)

    def test_installed_updated_and_back(self):
        first = self.publish()
        self.assertEqual(release.install(self.link, first), (None, first["commit"]))
        here = self.link.resolve()
        self.assertTrue((here / "kino").is_file() and release.is_release(here), "a version of its own behind the link")
        self.assertEqual(here.parent, self.d / "home" / ".kino-versions")
        self.assertEqual(release.link_of(here), self.link)
        self.assertEqual(release.installed(here)["commit"], first["commit"])
        self.assertEqual(release.check(here, first)["behind"], 0, "up to date")
        self.assertEqual(release.check(here, first)["changes"], [])

        git(self.repo, "commit", "-q", "--allow-empty", "-m", "A second version", "-m", "Note-de: Eine zweite Version")
        second = self.publish()
        r = release.check(here, second)
        self.assertEqual(r["behind"], 1)
        self.assertEqual([(c["s"], c["de"]) for c in r["changes"]], [("A second version", "Eine zweite Version")],
                         "only what it does not have, in both languages")
        self.assertEqual(release.install(self.link, second), (first["commit"], second["commit"]))
        self.assertEqual(release.head(self.link), second["commit"])
        self.assertTrue((self.d / "home" / ".kino-versions" / first["commit"]).is_dir(), "the one before kept")
        self.assertTrue(release.set_to(first["commit"], self.link), "and gone back to")
        self.assertEqual(release.head(self.link), first["commit"])
        self.assertFalse(release.set_to("0" * 40, self.link), "not to one that is not there")
        self.assertEqual(release.behind(second, "f" * 40), None, "one not listed is not counted")

    def test_an_older_version_is_not_installed_over_a_newer(self):
        first = self.publish()
        release.install(self.link, first)
        served_before = self.d / "before"
        shutil.copytree(self.srv_dir, served_before)
        time.sleep(1.1)  # a commit's time is in seconds: the fix's later than the first's
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "A fix")
        second = self.publish()
        release.install(self.link, second)
        shutil.rmtree(self.srv_dir)
        shutil.copytree(served_before, self.srv_dir)  # signed, but the one from before: served again
        again = release.manifest(self.server.url, self.signers)
        self.assertEqual(again["commit"], first["commit"])
        self.assertEqual(release.check(self.link.resolve(), again)["behind"], 0, "nothing offered")
        with self.assertRaises(release.ReleaseError) as e:
            release.install(self.link, again)
        self.assertEqual(str(e.exception), "older")
        self.assertEqual(release.head(self.link), second["commit"], "the newer one kept")
        self.assertTrue(release.set_to(first["commit"], self.link), "going back by hand still can")

    def test_only_the_newest_versions_are_kept(self):
        for n in range(release.KEEP + 2):
            git(self.repo, "commit", "-q", "--allow-empty", "-m", f"Version {n}")
            m = self.publish()
            release.install(self.link, m)
            os.utime(self.link.resolve(), (1_700_000_000 + n, 1_700_000_000 + n))  # in the order they came
        kept = sorted(p.name for p in (self.d / "home" / ".kino-versions").iterdir())
        self.assertEqual(len(kept), release.KEEP)
        self.assertIn(m["commit"], kept)

    def test_a_package_not_signed_by_the_key_is_refused(self):
        self.publish()
        other, _ = key_pair(self.d, "other")
        (self.srv_dir / "manifest.json.sig").unlink()
        subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(other), "-n", "kino-release",
                        str(self.srv_dir / "manifest.json")], check=True)
        with self.assertRaises(release.ReleaseError) as e:
            release.manifest(self.server.url, self.signers)
        self.assertEqual(str(e.exception), "signature", "signed by another key")
        self.publish()
        text = (self.srv_dir / "manifest.json").read_text()
        (self.srv_dir / "manifest.json").write_text(text.replace('"commits"', '"commits" ', 1))
        with self.assertRaises(release.ReleaseError) as e:
            release.manifest(self.server.url, self.signers)
        self.assertEqual(str(e.exception), "signature", "changed after it was signed")

    def test_a_package_other_than_the_one_signed_is_refused(self):
        m = self.publish()
        archive = self.srv_dir / m["archive"]
        archive.write_bytes(archive.read_bytes() + b"\0")
        with self.assertRaises(release.ReleaseError) as e:
            release.install(self.link, m)
        self.assertEqual(str(e.exception), "package")
        self.assertFalse(self.link.exists(), "nothing installed")
        self.assertEqual(list((self.d / "home" / ".kino-versions").iterdir()), [], "nothing left behind")

    def test_a_package_bigger_than_signed_is_not_fetched_on(self):
        m = self.publish()
        archive = self.srv_dir / m["archive"]
        archive.write_bytes(archive.read_bytes() + b"\0" * (1 << 20))  # an endless one, as far as the device can tell
        with self.assertRaises(release.ReleaseError) as e:
            release.install(self.link, m)
        self.assertEqual(str(e.exception), "package", "stopped at the size signed, not after all of it")
        self.assertEqual(list((self.d / "home" / ".kino-versions").iterdir()), [], "nothing left behind")

    def test_nothing_reached_is_said_so(self):
        with self.assertRaises(release.ReleaseError) as e:
            release.manifest("http://127.0.0.1:9/manifest.json", self.signers)
        self.assertEqual(str(e.exception), "fetch")

    @unittest.skipUnless(shutil.which("sha256sum") and shutil.which("curl"), "needs sha256sum and curl")
    def test_get_kino_installs_the_newest(self):
        m = self.publish(manifest_url=self.server.url)
        script = (self.srv_dir / "get-kino").read_text()
        self.assertNotIn("@SIGNERS@", script)
        self.assertIn(self.signers.read_text().split()[-1], script, "the key it trusts in it")
        installed = self.d / "installed"
        r = subprocess.run(["sh", str(self.srv_dir / "get-kino")], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, HOME=str(self.d / "home"), KINO_INSTALL=f"touch {installed}"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(release.head(self.link), m["commit"])
        self.assertTrue(installed.exists(), "then deploy/install.sh")
        archive = self.srv_dir / m["archive"]
        archive.write_bytes(archive.read_bytes() + b"\0")
        r = subprocess.run(["sh", str(self.srv_dir / "get-kino")], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, HOME=str(self.d / "home2"), KINO_INSTALL="true"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not the one the list names", r.stderr)

    def test_updates_only_when_asked(self):
        """"Look for Updates: Only When I Ask": the main menu's look and the nightly update leave GitHub alone."""
        probe = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import updates
asked = []
updates.update_check = lambda: asked.append(1) or updates._update_seen.update(at=1e12, r={"behind": 0, "checked": 0})
updates.UPDATES = True
updates._update_seen.clear()
updates.conf.update(updatecheck="manual", autoupdate="on")
manual = (updates.update_look_on(), updates.autoupdate_on(), updates.overview()["update"], len(asked))
updates.conf["updatecheck"] = "auto"
print(json.dumps({"manual": manual, "auto": (updates.update_look_on(), updates.autoupdate_on(), updates.overview()["update"] is not None, len(asked))}))
"""
        home = self.d / "cfg-look"
        r = subprocess.run([sys.executable, "-c", probe, str(ROOT)], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, KINO_REPO=str(ROOT), XDG_CONFIG_HOME=str(home), XDG_RUNTIME_DIR=str(home),
                                    XDG_CACHE_HOME=str(home), XDG_STATE_HOME=str(home), KINO_SERVER=""))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual(out["manual"], [False, False, None, 0], "not looked by itself")
        self.assertEqual(out["auto"], [True, True, True, 1])

    def test_the_backend_updates_from_packages(self):
        """The settings' update on a device installed from packages: what is new, then installed (link turned)."""
        first = self.publish()
        release.install(self.link, first)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "Newer")
        second = self.publish()
        probe = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from backend import updates
updates.restart_kino = lambda: None
check = updates.update_check()
print(json.dumps({"release": updates.RELEASE, "check": check, "install": updates.update_install(),
                  "behind_first": updates.commits_behind(sys.argv[2])}))
"""
        home = self.d / "cfg"
        r = subprocess.run([sys.executable, "-c", probe, str(ROOT), first["commit"]], capture_output=True, text=True,
                           timeout=60, env=dict(os.environ, KINO_REPO=str(self.link.resolve()), KINO_RELEASES=self.server.url,
                                                KINO_RELEASE_SIGNERS=str(self.signers), XDG_CONFIG_HOME=str(home),
                                                XDG_RUNTIME_DIR=str(home), XDG_CACHE_HOME=str(home), XDG_STATE_HOME=str(home)))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertTrue(out["release"])
        self.assertEqual(out["check"]["behind"], 1)
        self.assertEqual([c["s"] for c in out["check"]["changes"]], ["Newer"])
        self.assertIsNone(out["install"], "installed")
        self.assertEqual(release.head(self.link), second["commit"])
        self.assertEqual(out["behind_first"], 1, "the devices' overview counts from the manifest")

    def test_the_nightly_update_installs_the_newest_package(self):
        first = self.publish()
        release.install(self.link, first)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "Overnight")
        second = self.publish()
        fake = self.d / "bin"
        fake.mkdir()
        log = self.d / "systemctl.log"
        (fake / "systemctl").write_text(f'#!/bin/sh\nif [ "$1" = is-failed ]; then [ -e {log}.failed ]; exit $?; fi\necho "$@" >> {log}\n')
        (fake / "pgrep").write_text("#!/bin/sh\nexit 1\n")  # nothing plays
        (fake / "curl").write_text("#!/bin/sh\nexit 0\n")  # the backend answers
        (fake / "stat").write_text(f"#!/bin/sh\necho {os.environ.get('USER', 'me')}\n")  # the owner (GNU's stat -c)
        for f in fake.iterdir():
            f.chmod(0o755)
        probation = self.d / "probation"
        conf = self.d / "config.json"
        env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", KINO_UPDATE_STATE=str(self.d / "state"),
                   KINO_UPDATE_WAIT="2", KINO_UPDATE_PROBATION=str(probation), KINO_RELEASES=self.server.url,
                   KINO_CONF=str(conf))
        conf.write_text('{"updatecheck": "manual"}')
        r = subprocess.run(["sh", str(ROOT / "deploy" / "nightly-update"), str(self.link)], capture_output=True,
                           text=True, timeout=120, env=env)
        self.assertIn("nothing looked for", r.stdout, "updates only when asked: not at night either")
        self.assertEqual(release.head(self.link), first["commit"])
        conf.write_text('{"updatecheck": "auto"}')
        r = subprocess.run(["sh", str(ROOT / "deploy" / "nightly-update"), str(self.link)], capture_output=True,
                           text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Updated, Matinee started again", r.stdout)
        self.assertEqual(release.head(self.link), second["commit"])
        self.assertEqual((probation / "to").read_text().strip(), second["commit"], "on probation")
        self.assertEqual((probation / "from").read_text().strip(), first["commit"])
        self.assertIn("restart kino", log.read_text())


@unittest.skipUnless(HAVE_TOOLS, "needs git and ssh-keygen")
class PackageProbationTest(unittest.TestCase):
    def test_a_package_that_does_not_come_up_goes_back_by_itself(self):
        """As for a checkout (test_deploy): after three starts that never came up, the link goes back to the version
        before, which starts at once; the new one is kept as bad."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home = d / "home"
            versions = home / ".kino-versions"
            good, new = "a" * 40, "b" * 40
            for commit, server in ((good, "raise SystemExit(0) if False else None"), (new, "raise SystemExit(1)")):
                v = versions / commit
                (v / "backend").mkdir(parents=True)
                shutil.copy(ROOT / "kino", v / "kino")
                shutil.copy(ROOT / "backend" / "release.py", v / "backend" / "release.py")
                (v / "display-mode").write_text("")
                (v / "RELEASE").write_text(json.dumps({"commit": commit, "version": commit[:4], "at": 1}))
                name = "good" if commit == good else "new"
                (v / "server.py").write_text(f"open('{d}/started', 'a').write('{name}\\n')\n"
                                             f"import time; time.sleep(1)\n{server}\n")
            link = home / "kino"
            link.symlink_to(versions / new)
            probation = d / "state" / "kino" / "update"
            probation.mkdir(parents=True)
            (probation / "from").write_text(good + "\n")
            (probation / "to").write_text(new + "\n")
            (d / "drm").mkdir()
            bin_ = d / "bin"
            bin_.mkdir()
            (bin_ / "chromium").write_text("#!/bin/sh\nexit 0\n")
            (bin_ / "chromium").chmod(0o755)
            env = {"PATH": f"{bin_}:/usr/bin:/bin", "XDG_RUNTIME_DIR": str(d), "KINO_DRM": str(d / "drm"),
                   "KINO_PORT": "9", "HOME": str(home), "XDG_STATE_HOME": str(d / "state")}
            for start in range(1, 4):
                subprocess.run(["sh", str(link / "kino"), "--session"], env=env, capture_output=True, timeout=60)
                self.assertEqual((probation / "tries").read_text().strip(), str(start))
                self.assertEqual(release.head(link), new, "still trying the new one")
            r = subprocess.run(["sh", str(link / "kino"), "--session"], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("did not come up after 3 starts", r.stderr)
            self.assertEqual(release.head(link), good, "back on the version before")
            self.assertEqual((probation / "bad").read_text().strip(), new)
            self.assertEqual(sorted(p.name for p in probation.iterdir()), ["bad"], "probation over")
            self.assertEqual((d / "started").read_text().split(), ["new", "new", "new", "good"])


if __name__ == "__main__":
    unittest.main()
