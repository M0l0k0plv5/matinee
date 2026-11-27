"""Thin client setup: scripts, systemd service, mpv configuration."""
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import CHROMIUM_ITSELF, HAVE_MPV, ROOT, temp_dir, wait_for


class DeployTest(unittest.TestCase):
    def test_shell_script_syntax(self):
        for script in ("kino", "deploy/install.sh", "deploy/boot.sh", "deploy/harden.sh", "deploy/pair-remote.sh", "deploy/nightly-update",
                       "deploy/diag", "deploy/check", "deploy/server.sh", "deploy/get-matinee", "deploy/make-release"):
            r = subprocess.run(["sh", "-n", str(ROOT / script)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_update_pulls_the_newest_version(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "clone", "-q", str(ROOT), d], check=True)
            shutil.copy(ROOT / "kino", f"{d}/kino")  # the script as it is now, even before it is committed
            # never the service of a Matinee installed here (on the Fedora VM the test restarted it): a systemctl of the
            # test's own that says none is running
            fake = Path(d) / "fake-bin"
            fake.mkdir()
            (fake / "systemctl").write_text("#!/bin/sh\nexit 3\n")
            (fake / "systemctl").chmod(0o755)
            r = subprocess.run(["sh", f"{d}/kino", "--update"], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, PATH=f"{fake}:/usr/bin:/bin"))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Updated", r.stdout)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_update_restarts_the_service_without_a_password(self):
        """kino --update over ssh, where sudo cannot ask for a password: the service's backend ended, systemd starts
        the session again (Restart=always); sudo never asked."""
        # what kino --update leaves running a moment longer (git's packing in the background) may still write into
        # the clone as it is removed: left to the system's temporary files then, not a failure
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            d = os.path.realpath(d)  # as kino finds its own folder (a Mac's /var is /private/var)
            subprocess.run(["git", "clone", "-q", str(ROOT), d], check=True)
            shutil.copy(ROOT / "kino", f"{d}/kino")
            fake = Path(d) / "fake-bin"
            fake.mkdir()
            (fake / "systemctl").write_text("#!/bin/sh\nexit 0\n")  # the service runs
            (fake / "sudo").write_text(f"#!/bin/sh\ntouch {d}/sudo-asked\nexit 1\n")
            for f in fake.iterdir():
                f.chmod(0o755)
            backend = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"{d}/server.py"])
            try:
                (Path(d) / "kino-server-8765.pid").write_text(f"{backend.pid}\n")
                r = subprocess.run(["sh", f"{d}/kino", "--update"], capture_output=True, text=True, timeout=60,
                                   env=dict(os.environ, PATH=f"{fake}:/usr/bin:/bin", XDG_RUNTIME_DIR=d, KINO_PORT="8765"))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("Matinee restarted", r.stdout)
                self.assertIsNotNone(backend.wait(5), "the backend ended")
                self.assertFalse((Path(d) / "sudo-asked").exists(), "no password asked")
            finally:
                backend.kill()

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_nightly_update_restarts_only_when_new_and_nothing_plays(self):
        with tempfile.TemporaryDirectory() as d:
            origin, clone, fake, state = f"{d}/origin", f"{d}/clone", Path(d) / "bin", f"{d}/state"
            subprocess.run(["git", "clone", "-q", "--bare", str(ROOT), origin], check=True)
            subprocess.run(["git", "clone", "-q", origin, clone], check=True)
            # as the service runs it: a copy outside the checkout, as it is now (even uncommitted), the checkout given
            script = f"{d}/kino-nightly-update"
            shutil.copy(ROOT / "deploy/nightly-update", script)
            fake.mkdir()
            log = Path(d) / "systemctl.log"
            (fake / "systemctl").write_text(f'#!/bin/sh\nif [ "$1" = is-failed ]; then [ -e {log}.failed ]; exit $?; fi\necho "$@" >> {log}\n')
            playing = Path(d) / "playing"
            (fake / "pgrep").write_text(f"#!/bin/sh\n[ -e {playing} ]\n")  # mpv runs while this file is there
            (fake / "curl").write_text("#!/bin/sh\nexit 0\n")  # Matinee's backend answers
            for f in fake.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake}:/usr/bin:/bin", KINO_UPDATE_STATE=state, KINO_UPDATE_WAIT="2", KINO_CONF="/nonexistent")
            run = lambda: subprocess.run(["sh", script, clone], capture_output=True, text=True,
                                         timeout=60, env=env)
            r = run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("up to date", r.stdout)
            self.assertFalse(log.exists(), "nothing new: no restart")
            # a new commit comes while a film plays: pulled, the restart waits for the next night
            work = f"{d}/work"
            subprocess.run(["git", "clone", "-q", origin, work], check=True)
            subprocess.run(["git", "-C", work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "new"], check=True)
            subprocess.run(["git", "-C", work, "push", "-q"], check=True)
            playing.touch()
            old_head = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
            r = run()
            self.assertIn("next night", r.stdout)
            self.assertFalse(log.exists(), "not in the middle of a film")
            self.assertEqual(subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout,
                             old_head, "nor the checkout changed under the Matinee running: its page reads from it")
            playing.unlink()
            r = run()
            self.assertIn("started again", r.stdout)
            self.assertEqual(log.read_text().split("\n")[0], "restart kino", "the night after: the restart owed")
            self.assertEqual(run().stdout.strip(), "Matinee is up to date.", "and only once")
            # the history rewritten (a personal detail taken out of old commits): a pull would refuse it every night
            subprocess.run(["git", "-C", work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--amend", "--allow-empty", "-m", "rewritten"], check=True)
            subprocess.run(["git", "-C", work, "push", "-q", "--force"], check=True)
            r = run()
            self.assertIn("started again", r.stdout, r.stderr)
            self.assertEqual(subprocess.run(["git", "-C", clone, "log", "-1", "--format=%s"], capture_output=True, text=True).stdout.strip(),
                             "rewritten", "on the rewritten history")
            # on probation, for kino --session to judge by its page; one that went back by itself is left out
            probation = Path(clone) / ".git" / "kino-update"
            head = lambda: subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            self.assertEqual((probation / "to").read_text().strip(), head())
            before = head()
            subprocess.run(["git", "-C", work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "bad"], check=True)
            subprocess.run(["git", "-C", work, "push", "-q"], check=True)
            bad = subprocess.run(["git", "-C", work, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            (probation / "bad").write_text(bad + "\n")
            r = run()
            self.assertIn("stays on the one before", r.stdout)
            self.assertEqual(head(), before, "not installed")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_nightly_update_only_when_asked_still_restarts_when_owed(self):
        """"Look for Updates: Only When I Ask": nothing fetched at night; a restart owed for an update made before (a
        film played then) comes all the same."""
        with tempfile.TemporaryDirectory() as d:
            origin, clone, fake, state = f"{d}/origin", f"{d}/clone", Path(d) / "bin", Path(d) / "state"
            subprocess.run(["git", "clone", "-q", "--bare", str(ROOT), origin], check=True)
            subprocess.run(["git", "clone", "-q", origin, clone], check=True)
            work = f"{d}/work"
            subprocess.run(["git", "clone", "-q", origin, work], check=True)
            subprocess.run(["git", "-C", work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "new"], check=True)
            subprocess.run(["git", "-C", work, "push", "-q"], check=True)
            script = f"{d}/kino-nightly-update"
            shutil.copy(ROOT / "deploy/nightly-update", script)
            conf = Path(d) / "config.json"
            conf.write_text(json.dumps({"updatecheck": "manual"}))
            fake.mkdir()
            log = Path(d) / "systemctl.log"
            (fake / "systemctl").write_text(f'#!/bin/sh\nif [ "$1" = is-failed ]; then exit 1; fi\necho "$@" >> {log}\n')
            (fake / "pgrep").write_text("#!/bin/sh\nexit 1\n")
            (fake / "curl").write_text("#!/bin/sh\nexit 0\n")
            for f in fake.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake}:/usr/bin:/bin", KINO_UPDATE_STATE=str(state), KINO_UPDATE_WAIT="2",
                       KINO_CONF=str(conf))
            head = lambda: subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
            before = head()
            run = lambda: subprocess.run(["sh", script, clone], capture_output=True, text=True, timeout=60, env=env)
            r = run()
            self.assertIn("nothing looked for", r.stdout, r.stderr)
            self.assertEqual(head(), before, "the new version not fetched")
            self.assertFalse(log.exists(), "nothing restarted")
            (state / "restart").touch()  # an update the night before, while a film played
            r = run()
            self.assertIn("restart still owed", r.stdout, r.stderr)
            self.assertEqual(log.read_text().split("\n")[0], "restart kino")
            self.assertFalse((state / "restart").exists(), "owed no more")
            self.assertEqual(head(), before, "and still nothing fetched")

    def test_nightly_update_starts_a_fallen_kino_even_without_github(self):
        with tempfile.TemporaryDirectory() as d:
            origin, clone, fake = f"{d}/origin", f"{d}/clone", Path(d) / "bin"
            subprocess.run(["git", "clone", "-q", "--bare", str(ROOT), origin], check=True)
            subprocess.run(["git", "clone", "-q", origin, clone], check=True)
            shutil.rmtree(origin)  # GitHub not reachable that night
            script = f"{d}/kino-nightly-update"
            shutil.copy(ROOT / "deploy/nightly-update", script)
            fake.mkdir()
            log = Path(d) / "systemctl.log"
            (fake / "systemctl").write_text(f'#!/bin/sh\nif [ "$1" = is-failed ]; then [ -e {log}.failed ]; exit $?; fi\necho "$@" >> {log}\n')
            (fake / "pgrep").write_text("#!/bin/sh\nexit 1\n")  # nothing plays
            (fake / "stat").write_text(f"#!/bin/sh\necho {os.environ.get('USER', 'me')}\n")  # the owner (GNU's stat -c)
            for f in fake.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake}:/usr/bin:/bin", KINO_UPDATE_STATE=f"{d}/state",
                       KINO_CONF=f"{d}/none.json")
            run = lambda: subprocess.run(["sh", script, clone], capture_output=True, text=True, timeout=60, env=env)
            r = run()
            self.assertIn("Update failed", r.stderr)
            self.assertFalse(log.exists(), "Matinee running: left alone")
            Path(f"{log}.failed").touch()  # its stop timed out, systemd gave up on it
            r = run()
            self.assertIn("Update failed", r.stderr, "GitHub still away")
            self.assertEqual(log.read_text(), "start kino\n", "started again all the same")
            self.assertIn("started again", r.stdout)

    def test_nightly_update_goes_back_when_kino_does_not_start(self):
        git = lambda *a, cwd: subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                             check=True, capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as d:
            origin, clone, work, fake, state = f"{d}/origin", f"{d}/clone", f"{d}/work", Path(d) / "bin", f"{d}/state"
            subprocess.run(["git", "clone", "-q", "--bare", str(ROOT), origin], check=True)
            subprocess.run(["git", "clone", "-q", origin, clone], check=True)
            subprocess.run(["git", "clone", "-q", origin, work], check=True)
            script = f"{d}/kino-nightly-update"
            shutil.copy(ROOT / "deploy/nightly-update", script)
            fake.mkdir()
            log, broken = Path(d) / "systemctl.log", Path(d) / "broken"
            (fake / "systemctl").write_text(f'#!/bin/sh\nif [ "$1" = is-failed ]; then [ -e {log}.failed ]; exit $?; fi\necho "$@" >> {log}\n')
            (fake / "pgrep").write_text("#!/bin/sh\nexit 1\n")  # nothing plays
            # the backend answers unless the checkout holds the broken version
            (fake / "curl").write_text(f'#!/bin/sh\n[ "$(git -C {clone} log -1 --format=%s)" != broken ]\n')
            for f in fake.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake}:/usr/bin:/bin", KINO_UPDATE_STATE=state, KINO_UPDATE_WAIT="2", KINO_CONF="/nonexistent")
            run = lambda: subprocess.run(["sh", script, clone], capture_output=True, text=True, timeout=120, env=env)
            git("commit", "-q", "--allow-empty", "-m", "good", cwd=work)
            git("push", "-q", cwd=work)
            r = run()
            self.assertIn("started again", r.stdout, r.stderr)
            good = git("rev-parse", "HEAD", cwd=clone)
            git("commit", "-q", "--allow-empty", "-m", "broken", cwd=work)
            git("push", "-q", cwd=work)
            r = run()
            self.assertNotEqual(r.returncode, 0, "told in the journal")
            self.assertIn("back on the one before", r.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=clone), good, "the version that started")
            self.assertEqual(log.read_text().count("restart kino"), 3, "started on the new one, then on the one before")
            r = run()
            self.assertEqual(r.returncode, 0)
            self.assertIn("did not start", r.stdout, "the next night: the broken one left out")
            self.assertEqual(git("rev-parse", "HEAD", cwd=clone), good)
            git("commit", "-q", "--allow-empty", "-m", "fixed", cwd=work)
            git("push", "-q", cwd=work)
            r = run()
            self.assertIn("started again", r.stdout, r.stderr)
            self.assertEqual(git("log", "-1", "--format=%s", cwd=clone), "fixed", "a newer one: taken")

    def test_nightly_update_runs_a_copy_owned_by_root(self):
        text = (ROOT / "deploy/kino-update.service.in").read_text().replace("@KINO@", str(ROOT))
        self.assertIn(f"ExecStart=/usr/local/libexec/kino-nightly-update {ROOT}\n", text,
                      "not the script in the checkout: that is the user's, and the service runs as root")
        self.assertIn("install -D -o root -g root -m 755", (ROOT / "deploy/install.sh").read_text())

    def test_diagnostics_leave_out_what_could_sign_in(self):
        """deploy/diag: one file with the TV computer's state, without any token or password."""
        import tarfile
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "config" / "kino"
            conf.mkdir(parents=True)
            (conf / "config.json").write_text(json.dumps({"server": "http://jf", "password": "pw-secret", "effects": "plain",
                                                          "navidrome": {"server": "http://nd", "token": "nd-secret"},
                                                          "simple": {"salt": "salt-secret", "pin": "pin-secret"}}))
            (conf / "state.json").write_text(json.dumps({"token": "jf-secret"}))
            r = subprocess.run([str(ROOT / "deploy" / "diag")], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, XDG_CONFIG_HOME=str(Path(d) / "config"), KINO_DIAG_DIR=d))
            self.assertEqual(r.returncode, 0, r.stderr)
            bundle = Path(r.stdout.strip().splitlines()[-1])
            self.assertEqual(oct(bundle.stat().st_mode & 0o777), "0o600")
            with tarfile.open(bundle) as t:
                text = "".join(t.extractfile(m).read().decode(errors="replace") for m in t.getmembers() if m.isfile())
                names = [Path(m.name).name for m in t.getmembers()]
            for secret in ("pw-secret", "nd-secret", "salt-secret", "pin-secret", "jf-secret"):
                self.assertNotIn(secret, text)
            self.assertIn('"effects": "plain"', text, "the settings are there")
            self.assertIn("state.json", text, "the other files by name")
            self.assertIn("summary.txt", names)

    def test_self_test(self):
        """deploy/check: line by line whether the TV computer is all right, the number of what is wrong its exit code."""
        from helpers import Backend
        from mockjf import MockJellyfin
        mock = MockJellyfin().start()
        backend = Backend(mock.url).start()
        try:
            run = lambda port: subprocess.run([str(ROOT / "deploy" / "check")], capture_output=True, text=True, timeout=120,
                                              env=dict(os.environ, KINO_PORT=str(port)))
            r = run(backend.port)
            self.assertIn("ok  Matinee answers and is signed in to Jellyfin\n", r.stdout)
            self.assertNotIn("volume cannot be read", r.stdout, "outside cage the volume is not Matinee's")
            self.assertEqual(r.returncode, r.stdout.count("!!  "), r.stdout)
            backend.stop()
            r = run(backend.port)
            self.assertIn("!!  Matinee does not answer", r.stdout)
            self.assertGreaterEqual(r.returncode, 1)
        finally:
            backend.stop()
            mock.stop()

    def test_scripts_are_executable(self):
        for script in ("kino", "deploy/install.sh", "deploy/boot.sh", "deploy/harden.sh", "deploy/pair-remote.sh", "deploy/nightly-update",
                       "deploy/diag", "deploy/check", "set-password", "server.py", "display-mode", "deploy/server.sh",
                       "deploy/get-matinee", "deploy/make-release", "deploy/support-inbox"):
            self.assertTrue(os.access(ROOT / script, os.X_OK), script)

    def render_service(self):
        text = (ROOT / "deploy/kino.service.in").read_text()
        return text.replace("@USER@", getpass.getuser()).replace("@KINO@", str(ROOT))

    def test_service_placeholders_are_filled_in(self):
        text = self.render_service()
        self.assertNotRegex(text, r"@[A-Z]+@", "all placeholders filled in")
        self.assertIn(f"ExecStart=/bin/sh {ROOT}/kino", text)
        self.assertIn("Restart=always", text)
        self.assertIn("PAMName=login", text, "own login session for display and audio")

    def test_service_starts_without_waiting_for_the_network(self):
        text = self.render_service()
        self.assertNotIn("network-online", text, "Matinee signs in by itself once the server is reachable")
        # the boot splash stays until cage draws, and goes away if Matinee cannot start
        self.assertIn("ExecStartPre=-+/usr/bin/plymouth quit --retain-splash", text)
        self.assertIn("ExecStopPost=-+/usr/bin/plymouth quit", text)
        # not by OnFailure: plymouth-quit conflicts with Matinee, and its start cancelled the one a restart had queued
        self.assertNotIn("OnFailure=plymouth-quit.service", text)
        self.assertIn("TimeoutStopSec=20", text, "a hung stop does not hold a restart for 90 seconds")

    def test_service_is_hardened(self):
        text = self.render_service()
        for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes", "ProtectKernelModules=yes",
                     "RestrictSUIDSGID=yes"):
            self.assertIn(line, text)
        # these would break the TV: graphics/input devices, the JavaScript JIT, Chromium's sandbox
        for line in ("PrivateDevices=yes", "MemoryDenyWriteExecute=yes", "RestrictNamespaces=yes", "ProtectHome="):
            self.assertNotIn(line, text)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_service_is_valid_for_systemd(self):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / "kino.service"
            unit.write_text(self.render_service())
            r = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True)
            problems = [l for l in r.stderr.splitlines() if "kino.service" in l]
            self.assertEqual(problems, [], r.stderr)

    def test_cage_gets_the_systems_keyboard_layout(self):
        # Arch keeps the layout in vconsole.conf, Debian in /etc/default/keyboard
        for name, text in (("vconsole.conf", 'KEYMAP=de\nXKBLAYOUT=de\nXKBVARIANT="nodeadkeys"\n'),
                           ("keyboard", 'XKBMODEL="pc105"\nXKBLAYOUT="de"\nXKBVARIANT="nodeadkeys"\nXKBOPTIONS=""\n')):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                (Path(d) / name).write_text(text)
                script = (ROOT / "kino").read_text().replace("/etc/vconsole.conf", f"{d}/vconsole.conf").replace(
                    "/etc/default/keyboard", f"{d}/keyboard").replace(
                    'exec env WLR_DRM_NO_ATOMIC=1 cage -d -s -- "$DIR/kino" --session', "env | grep ^XKB_DEFAULT | sort; exit 0")
                (Path(d) / "kino").write_text(script)
                out = subprocess.run(["sh", f"{d}/kino"], capture_output=True, text=True, timeout=10,
                                     env={"PATH": "/usr/bin:/bin", "XKB_DEFAULT_MODEL": "kept"}).stdout
                self.assertEqual(out.split(), ["XKB_DEFAULT_LAYOUT=de", "XKB_DEFAULT_MODEL=kept", "XKB_DEFAULT_VARIANT=nodeadkeys"],
                                 "a German keyboard has its Z and Y; what is set already stays")

    def session(self, d, server_lives, chromium_lives, chromium_does="", stderr=False, server_code=1):
        """kino --session with stand-ins: a backend and a Chromium that end after so many seconds (each writes its pid,
        the backend ends with server_code: by default as one fallen over),
        and "gone" when it is ended; Chromium runs `chromium_does` first), a screen connected. Returns (exit code,
        seconds it ran, the files), and what it wrote to stderr if asked."""
        d = Path(d)
        for name in ("server.py", "display-mode"):
            (d / name).write_text(f"import os, sys, time, signal\nsignal.signal(signal.SIGTERM, lambda *_: (open('{d}/{name}.gone', 'w'), sys.exit(0)))\n"
                                  f"open('{d}/{name}.pid', 'w').write(str(os.getpid()))\n"
                                  + (f"time.sleep({server_lives})\nsys.exit({server_code})\n" if name == "server.py" else ""))
        (d / "bin").mkdir()
        (d / "bin" / "chromium").write_text(f"#!/bin/sh\necho $$ > {d}/chromium.pid\n{chromium_does}\nsleep {chromium_lives} & p=$!\n"
                                            f"trap 'touch {d}/chromium.gone; kill $p; exit 0' TERM\nwait $p\n")
        (d / "bin" / "chromium").chmod(0o755)
        (d / "drm" / "card0-HDMI-A-1").mkdir(parents=True)
        (d / "drm" / "card0-HDMI-A-1" / "status").write_text("connected\n")
        shutil.copy(ROOT / "kino", d / "kino")
        start = time.monotonic()
        r = subprocess.run(["sh", str(d / "kino"), "--session"], capture_output=True, text=True, timeout=30,
                           env={"PATH": f"{d / 'bin'}:/usr/bin:/bin", "XDG_RUNTIME_DIR": str(d), "KINO_DRM": str(d / "drm"),
                                # on a Mac, kino takes Chromium from /Applications first: the stand-in instead, never
                                # the real one in front of everything
                                "KINO_PORT": "9", "KINO_CHROMIUM": str(d / "bin" / "chromium")})
        took = time.monotonic() - start
        return (r.returncode, took, {p.name for p in d.iterdir()}) + ((r.stderr,) if stderr else ())

    def test_session_unharmed_by_its_script_changing_while_it_runs(self):
        """The shell reads a script as it goes: one written anew in place while Matinee runs (an editor, a copy over it)
        was read on at the old offset after Chromium ended, in the middle of a line of the new one."""
        with tempfile.TemporaryDirectory() as d:
            kino = Path(d) / "kino"
            # longer by a few lines at its top: the old offset falls into the middle of its last lines
            rewrite = f"cp {kino} {d}/kino.was; {{ printf '# a line more\\n%.0s' 1 2 3 4 5 6 7 8; cat {d}/kino.was; }} > {kino}"
            code, took, files, err = self.session(d, server_lives=60, chromium_lives=3, chromium_does=rewrite,
                                                  stderr=True)
            self.assertIn("a line more", kino.read_text(), "the script was written anew")
        self.assertNotIn("syntax error", err)
        self.assertNotIn("not found", err)
        self.assertIn("server.py.gone", files)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_update_that_does_not_come_up_goes_back_by_itself(self):
        """An update on probation whose backend falls over at once: each start is counted, and after three the
        checkout goes back to the version before by itself and starts that one; the new one is kept as bad."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            git = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True,
                                            env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                                     GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")).stdout.strip()
            git("init", "-q")
            shutil.copy(ROOT / "kino", d / "kino")
            (d / "display-mode").write_text("")
            (d / "server.py").write_text(f"open('{d}/started', 'a').write('good\\n')\nimport time; time.sleep(1)\n")
            git("add", "."); git("commit", "-qm", "good")
            good = git("rev-parse", "HEAD")
            (d / "server.py").write_text(f"open('{d}/started', 'a').write('new\\n')\nraise SystemExit(1)\n")
            git("commit", "-qam", "new")
            new = git("rev-parse", "HEAD")
            probation = d / ".git" / "kino-update"
            probation.mkdir()
            (probation / "from").write_text(good + "\n")
            (probation / "to").write_text(new + "\n")
            (d / "drm").mkdir()  # no screen known: Chromium is not waited for
            env = {"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": str(d), "KINO_DRM": str(d / "drm"), "KINO_PORT": "9",
                   "HOME": str(d)}
            bin_ = d / "bin"
            bin_.mkdir()
            (bin_ / "chromium").write_text("#!/bin/sh\nexit 0\n")
            (bin_ / "chromium").chmod(0o755)
            env["PATH"] = f"{bin_}:{env['PATH']}"
            for start in range(1, 4):  # systemd starting it again and again
                subprocess.run(["sh", str(d / "kino"), "--session"], env=env, capture_output=True, timeout=60)
                self.assertEqual((probation / "tries").read_text().strip(), str(start))
                self.assertEqual(git("rev-parse", "HEAD"), new, "still trying the new one")
            r = subprocess.run(["sh", str(d / "kino"), "--session"], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("did not come up after 3 starts", r.stderr)
            self.assertEqual(git("rev-parse", "HEAD"), good, "back on the version before")
            self.assertEqual((probation / "bad").read_text().strip(), new)
            self.assertEqual(sorted(p.name for p in probation.iterdir()), ["bad"], "probation over")
            self.assertEqual((d / "started").read_text().split(), ["new", "new", "new", "good"],
                             "the version before started at once")

    def test_session_ends_when_the_backend_falls_over(self):
        # the page alone would wait for it for ever: cage has to end, so that systemd starts Matinee anew
        with tempfile.TemporaryDirectory() as d:
            # it lives past waiting for its page (wait_server: 5 s and more, curl starts slowly on a Mac)
            code, took, files, err = self.session(d, server_lives=10, chromium_lives=60, stderr=True)
        self.assertNotEqual(code, 0, "systemd sees a failure")
        self.assertIn("The backend has stopped (exit code 1)", err, "and the journal why")
        self.assertLess(took, 20, "at once, not when Chromium ends by itself")
        self.assertIn("chromium.gone", files, "Chromium ended with it")

    def test_backend_ending_to_start_again_is_no_failure(self):
        """An update, another account, kino --update: the backend ends by itself (code 0) to start again. Each showed
        in the journal as a failure without a word ("Failed with result 'exit-code'"), the same as a crash."""
        with tempfile.TemporaryDirectory() as d:
            code, took, files, err = self.session(d, server_lives=10, chromium_lives=60, stderr=True, server_code=0)
        self.assertEqual(code, 0, "no failure for systemd: Restart=always starts it again all the same")
        self.assertIn("The backend ended to start again", err)
        self.assertIn("chromium.gone", files, "Chromium ended with it")

    def test_session_ends_the_backend_with_chromium(self):
        with tempfile.TemporaryDirectory() as d:
            code, took, files = self.session(d, server_lives=60, chromium_lives=6)
        self.assertLess(took, 30, "with Chromium, long before the backend would end by itself")
        self.assertIn("server.py.gone", files, "the backend does not go on alone")

    def test_chromium_falling_over_with_the_tv_off_starts_again_alone(self):
        """Chromium falls over when cage's only screen goes (the TV switched off): the backend goes on (the music with
        it), and Chromium starts again once the screen is back."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            status = d / "drm" / "card0-HDMI-A-1" / "status"
            # the first Chromium: the TV goes off and it falls over; the next ones run on
            does = (f"n=$(cat {d}/chromium.runs 2>/dev/null || echo 0); echo $((n + 1)) > {d}/chromium.runs\n"
                    f'if [ "$n" = 0 ]; then sleep 1; echo disconnected > {status}; exit 133; fi')
            done = threading.Event()
            result = {}
            worker = threading.Thread(target=lambda: (result.update(r=self.session(d, server_lives=20, chromium_lives=60,
                                                                                    chromium_does=does, stderr=True)), done.set()))
            worker.start()
            wait_for(lambda: (d / "chromium.runs").exists() and status.read_text() == "disconnected\n", timeout=15,
                     msg="Chromium fell over with the TV off")
            time.sleep(3)
            self.assertFalse((d / "server.py.gone").exists(), "the backend goes on")
            self.assertEqual((d / "chromium.runs").read_text().strip(), "1", "no Chromium without a screen")
            status.write_text("connected\n")
            wait_for(lambda: (d / "chromium.runs").read_text().strip() == "2", timeout=10, msg="started again with the TV on")
            # with the whole suite at once the session can take a while: given time, then said if it did not end
            self.assertTrue(done.wait(60), "the session ended with its backend")
            code, took, files, err = result["r"]
        self.assertIn("started again once it is back", err)
        self.assertIn("The backend has stopped", err, "the session ends only with its backend (here after 20 s)")

    def test_session_removes_its_pid_file(self):
        """In cage a second EXIT trap replaced the launcher's first, and the backend's pid file stayed behind."""
        with tempfile.TemporaryDirectory() as d:
            code, took, files = self.session(d, server_lives=60, chromium_lives=6,
                                             chromium_does=f"cp {d}/kino-server-9.pid {d}/pidfile.seen")
            self.assertIn("pidfile.seen", files, "written while Matinee runs")
            self.assertEqual((Path(d) / "pidfile.seen").read_text(), (Path(d) / "server.py.pid").read_text() + "\n",
                             "the backend's own pid")
        self.assertNotIn("kino-server-9.pid", files, "gone with the backend")

    def test_start_ends_only_a_backend_of_kinos(self):
        """The pid file of a Matinee gone long ago may name anything by now: only a backend of this Matinee is ended."""
        for name, ours in (("something else", False), ("an older backend", True)):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                # the launcher looks for its own server.py's path (resolved, as its DIR is) in the command line
                arg = str(Path(d).resolve() / "server.py") if ours else "another"
                old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", arg])
                try:
                    (Path(d) / "kino-server-9.pid").write_text(f"{old.pid}\n")
                    self.session(d, server_lives=60, chromium_lives=6)
                    if ours:
                        self.assertIsNotNone(old.poll(), "the older backend is ended")
                    else:
                        self.assertIsNone(old.poll(), "left alone")
                finally:
                    old.kill()
                    old.wait()

    def test_chromium_profile_in_memory_on_the_thin_client(self):
        text = (ROOT / "kino").read_text()
        self.assertIn('PROFILE="${XDG_RUNTIME_DIR:-/tmp}/kino-chromium"', text, "nothing to damage when it is turned off")
        self.assertIn("--disk-cache-size=", text, "the cache stays small in memory")

    def test_chromium_as_a_snap_keeps_its_profile_where_the_snap_may_write(self):
        text = (ROOT / "kino").read_text()
        self.assertIn('PROFILE="$HOME/snap/chromium/common/kino-chromium"', text, "not in a hidden folder: refused to the snap")
        self.assertIn('PROFILE="${XDG_RUNTIME_DIR:-/tmp}/snap.chromium/kino-chromium"', text, "in memory on the thin client")
        install = (ROOT / "deploy" / "install.sh").read_text()
        self.assertIn("sudo snap install chromium", install)
        self.assertIn("'NoNewPrivileges=no'", install, "snap-confine cannot start it otherwise")
        self.assertIn("sudo rm -f /etc/systemd/system/kino.service.d/snap.conf", install, "and back on without the snap")

    def test_install_refuses_an_mpv_too_old_for_kinos_options(self):
        install = (ROOT / "deploy" / "install.sh").read_text()
        self.assertIn('dpkg --compare-versions "${mpv_version#*:}" ge 0.38', install, "Ubuntu 24.04's 0.37 stops at once")
        conf = (ROOT / "mpv" / "mpv.conf").read_text()
        self.assertIn("osd-border-style", conf, "one of the options 0.37 does not know: without them, the check can go")

    def test_packman_of_each_opensuse(self):
        install = (ROOT / "deploy" / "install.sh").read_text()
        for d in ('openSUSE_Leap_$(. /etc/os-release; echo "$VERSION_ID")', "openSUSE_Slowroll", "openSUSE_Tumbleweed"):
            self.assertIn(d, install)

    def test_nothing_in_cage_falls_back_to_x11(self):
        session = (ROOT / "kino").read_text().split("--session)")[1].split(";;")[0]
        self.assertIn("unset DISPLAY", session, "cage's Xwayland can take the whole session down with it")
        self.assertRegex((ROOT / "mpv" / "mpv.conf").read_text(), r"(?m)^vo=gpu-next,gpu\b", "gpu where gpu-next cannot")
        self.assertIn('"--gpu-context=waylandvk,wayland"] if display.IN_CAGE and sys.platform == "linux"', (ROOT / "backend" / "video_player.py").read_text(),
                      "nor mpv: its x11 code falls over without a display")

    def test_one_logo(self):
        self.assertEqual((ROOT / "web" / "icon.svg").read_text(), (ROOT / "deploy" / "kino.svg").read_text(),
                         "the browser's icon is the app's logo")
        self.assertIn('<link rel="icon" href="icon.svg">', (ROOT / "web" / "index.html").read_text())

    def test_kiosk_starts_backend_inside_cage(self):
        # otherwise mpv does not know cage's display and cannot open a window
        text = (ROOT / "kino").read_text()
        self.assertIn('exec env WLR_DRM_NO_ATOMIC=1 cage -d -s -- "$DIR/kino" --session', text,
                      "with the legacy DRM interface (the atomic one refused commits after a rate change)")
        session = text.split("--session)")[1].split(";;")[0]
        self.assertIn("launch_server", session)
        self.assertLess(session.index("launch_server"), session.index("display-mode"), "started while the mode is set")

    @unittest.skipUnless(HAVE_MPV, "needs mpv")
    def test_mpv_config_without_warnings(self):
        r = subprocess.run(["mpv", f"--config-dir={ROOT / 'mpv'}", "--idle=once", "--vo=null", "--ao=null",
                            "--frames=0", "--msg-level=all=warn", "av://lavfi:testsrc=duration=1"],
                           capture_output=True, text=True, timeout=30, env=dict(os.environ, MANGOHUD="0"))
        lines = [l for l in (r.stdout + r.stderr).splitlines()
                 if any(w in l.lower() for w in ("unknown", "invalid", "error parsing", "not found", "ignoring"))
                 and "vdpau" not in l.lower()]
        self.assertEqual(lines, [], "warnings in the mpv configuration")

    def test_kino_lua_draws_without_the_messages_box(self):
        """mpv.conf gives the messages a box (background-box), which libass also draws around each drawing and text of
        an overlay: a grey rectangle from the screen's corner behind the info panel. Each of kino.lua's makes it
        clear."""
        lua = (ROOT / "mpv/scripts/kino.lua").read_text()
        tags = re.findall(r'"\{(\\\\an[^"}]*)\}', lua)
        self.assertGreaterEqual(len(tags), 3)  # the curtain, shape() and text()
        self.assertEqual([t for t in tags if "\\\\4a&HFF&" not in t], [], "a drawing or text with the box behind it")

    def test_no_german_outside_translations(self):
        """Everything in the repository is English; German only in the translation tables."""
        allowed = {"web/i18n.js", "backend/core.py", "backend/playback.py", "backend/tv.py",
                   "deploy/kino.desktop", "tests/test_ui.py", "tests/test_player.py",
                   "tests/test_deploy.py", "remote/app.js",  # remote/app.js: the phone's remote, its own words
                   "docs/handbuch.md"}  # the customers' manual, in German
        german = []
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if not path.is_file() or rel.startswith(".git/") or "__pycache__" in path.parts or path.suffix in (".svg", ".png", ".jpg", ".ttf"):
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if any(c in line for c in "äöüÄÖÜß„") and rel not in allowed:
                    german.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(german, [])

    def test_no_space_before_an_ellipsis_in_the_texts(self):
        """The texts shown write "Suche Untertitel…", never "Suche Untertitel …"; a range such as "1 … 9" may keep its
        spaces. Checked in every string of the translation tables and of the remote."""
        texts = ["web/i18n.js", "backend/core.py", "backend/playback.py", "backend/tv.py", "remote/app.js"]
        found = []
        for rel in texts:
            for n, line in enumerate((ROOT / rel).read_text().splitlines(), 1):
                for s in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
                    if re.search(r"(?<!\d) …", s):
                        found.append(f"{rel}:{n}: {s}")
        self.assertEqual(found, [])

    def test_no_name_defined_twice_in_the_backend(self):
        """A function or a global given the name of another one replaces it (unseen until it is called); one of the
        same name in two parts of the backend is two things where one is meant."""
        import ast
        seen, twice = {}, []
        for path in sorted((ROOT / "backend").glob("*.py")):
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                else:
                    continue
                for name in names:
                    kind = "def" if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else "value"
                    if name in seen and ("def" in (kind, seen[name][0]) or seen[name][1] != path.name):
                        twice.append(f"{name}: {seen[name][1]}:{seen[name][2]} and {path.name}:{node.lineno}")
                    seen[name] = (kind, path.name, node.lineno)
        self.assertEqual(twice, [])

    def test_backend_loads_every_part(self):
        """Each part of the backend loads the next at its end, core first: one left out of that chain would be missing
        until something imports it by chance."""
        load_server()
        parts = {p.stem for p in (ROOT / "backend").glob("*.py")} - {"__init__"}
        self.assertEqual(parts - {m.split(".")[1] for m in sys.modules if m.startswith("backend.")}, set())

    def test_all_remote_keys_are_bound(self):
        conf = (ROOT / "mpv/input.conf").read_text().split()
        for key in ("LEFT", "RIGHT", "UP", "DOWN", "ENTER", "KP_ENTER", "ESC", "BS", "GO_BACK", "PLAYPAUSE",
                    "PLAY", "PAUSE", "STOP", "NEXT", "FORWARD", "REWIND", "MENU", "VOLUME_UP", "VOLUME_DOWN", "MUTE"):
            self.assertIn(key, conf, f"key {key} is not bound")


@unittest.skipUnless(CHROMIUM_ITSELF, "needs Chromium (its New Tab page)")
class NewTabGuardTest(unittest.TestCase):
    """newtab-guard on macOS: the empty "New Tab" windows Chromium opens beside Matinee's closed as they come, Matinee's own
    page left alone; against a headless Chromium."""

    def test_closes_new_tabs_only_and_ends_with_kino(self):
        import socket
        import urllib.request
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        # Chromium's helpers may still write into the profile as it is removed: that is not what this test is about
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
            chromium = subprocess.Popen([CHROMIUM_ITSELF, "--headless", "--disable-gpu", f"--user-data-dir={profile}", "--no-first-run",
                                         f"--remote-debugging-port={port}", "data:text/html,<title>Matinee</title>"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            kino = subprocess.Popen(["sleep", "60"])  # kino's shell, running Chromium
            guard = subprocess.Popen([sys.executable, str(ROOT / "newtab-guard"), str(port), str(kino.pid)],
                                     env={**os.environ, "KINO_NEWTAB_EVERY": "0.2"})
            pages = lambda: [t["url"] for t in json.load(urllib.request.urlopen(f"{base}/json/list", timeout=5)) if t["type"] == "page"]
            try:
                wait_for(lambda: "data:text/html,<title>Matinee</title>" in pages(), msg="Chromium up")
                time.sleep(1)  # the guard listening
                for _ in range(2):  # macOS asked Chromium to reopen, twice
                    urllib.request.urlopen(urllib.request.Request(f"{base}/json/new?chrome://newtab/", method="PUT"), timeout=5)
                    wait_for(lambda: not any(u.startswith("chrome://newtab") for u in pages()), timeout=5, msg="the new tab closed")
                self.assertEqual(pages(), ["data:text/html,<title>Matinee</title>"], "Matinee's page left alone")
                kino.kill()
                kino.wait()
                self.assertEqual(guard.wait(timeout=10), 0, "it ends with Matinee")
            finally:
                for p in (kino, guard, chromium):
                    p.kill()
                    p.wait()


def load_server():
    """Matinee's backend (backend/) as modules, loaded anew against a throwaway config (loading it only reads the
    config, starts nothing): its parts as attributes, e.g. load_server().display."""
    import importlib
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "kino").mkdir()
        (Path(d) / "kino" / "config.json").write_text('{"server": "http://127.0.0.1:1", "username": "x"}')
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = d
        dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # no __pycache__ in the checkout
        try:
            for name in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
                del sys.modules[name]  # each test its own: a part changed by one is not seen by another
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            mod = importlib.import_module("backend")
        finally:
            sys.dont_write_bytecode = dont_write
            if old is None:
                del os.environ["XDG_CONFIG_HOME"]
            else:
                os.environ["XDG_CONFIG_HOME"] = old
    return mod


def mode(w, h, hz, current=False, preferred=False):
    return {"width": w, "height": h, "refresh": hz, "preferred": preferred, "current": current}


class DisplayModeTest(unittest.TestCase):
    """Which mode a "display" setting means."""

    @classmethod
    def setUpClass(cls):
        cls.pick = staticmethod(load_server().display.pick_mode)

    # a 4K screen on HDMI 1.4: 4K only at 30 Hz, 1080p up to 120
    HDMI14 = [mode(3840, 2160, 30.0, current=True), mode(3840, 2160, 24.0), mode(2560, 1440, 59.951),
              mode(1920, 1080, 120.0), mode(1920, 1080, 119.88), mode(1920, 1080, 60.0), mode(1280, 720, 60.0)]

    def test_auto_goes_to_the_highest_refresh_rate_when_the_screens_own_is_slow(self):
        self.assertEqual(self.pick(self.HDMI14, "auto"), mode(1920, 1080, 120.0))

    def test_auto_keeps_60_hz(self):
        modes = [mode(3840, 2160, 60.0, current=True), mode(1920, 1080, 120.0)]
        self.assertEqual(self.pick(modes, "auto"), modes[0], "a 4K TV at 60 Hz keeps its 4K")

    def test_native(self):
        self.assertEqual(self.pick(self.HDMI14, "native"), self.HDMI14[0], "the biggest, at its highest rate")
        modes = [mode(3840, 2160, 30.0), mode(1920, 1080, 60.0, preferred=True)]
        self.assertEqual(self.pick(modes, "native"), modes[1], "the one the screen prefers")

    def test_exact_mode(self):
        self.assertEqual(self.pick(self.HDMI14, "1920x1080@60"), mode(1920, 1080, 60.0))
        self.assertEqual(self.pick(self.HDMI14, "1920x1080"), mode(1920, 1080, 120.0), "without a rate: the highest")
        self.assertIsNone(self.pick(self.HDMI14, "5120x2880@60"), "a mode the screen does not have")
        with self.assertRaises(ValueError):
            self.pick(self.HDMI14, "big")


class FrameRateTest(unittest.TestCase):
    """Which refresh rate a video's frame rate gets (None: the current one fits, or nothing does)."""

    @classmethod
    def setUpClass(cls):
        cls.rate_for = staticmethod(load_server().display.rate_for)

    MODES = [mode(1920, 1080, 120.0, current=True), mode(1920, 1080, 119.88), mode(1920, 1080, 60.0),
             mode(1920, 1080, 59.94), mode(1920, 1080, 50.0), mode(1920, 1080, 24.0), mode(3840, 2160, 25.0)]

    def rate(self, fps):
        m = self.rate_for(self.MODES, fps)
        return m and m["refresh"]

    def test_frame_rates(self):
        self.assertIsNone(self.rate(24), "24 fps fit 120 Hz already")
        self.assertIsNone(self.rate(30))
        self.assertIsNone(self.rate(60))
        self.assertIsNone(self.rate(23.976), "0.1 % off 120 Hz: mpv evens it out, the screen does not go black")
        self.assertIsNone(self.rate(29.97))
        self.assertIsNone(self.rate(59.94))
        self.assertEqual(self.rate(25), 50.0, "at the same size: not 4K at 25")
        self.assertEqual(self.rate(50), 50.0)

    def test_switches_when_far_off(self):
        modes = [mode(1920, 1080, 50.0, current=True), mode(1920, 1080, 119.88), mode(1920, 1080, 60.0)]
        self.assertEqual(self.rate_for(modes, 23.976)["refresh"], 119.88, "at 50 Hz a film would judder")

    def test_nothing_known(self):
        self.assertIsNone(self.rate(None))
        self.assertIsNone(self.rate(0))
        self.assertIsNone(self.rate(12.5 * 0.3))


# niri as the tests need it: two windows (a terminal and Matinee) on workspace 1 of a screen, its empty workspace 2 after
# them; "msg --json windows|workspaces" answer from that, actions go to a log (one JSON list per call)
FAKE_NIRI = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["msg", "--json"]:
    if args[2] == "windows":
        print(json.dumps([{"id": 7, "title": "Terminal", "app_id": "ghostty", "workspace_id": 11, "is_floating": False},
                          {"id": 8, "title": "Matinee", "app_id": "chrome-127.0.0.1__-Default", "workspace_id": 11, "is_floating": False}]))
    else:
        print(json.dumps([{"id": 11, "idx": 1, "output": "eDP-1", "is_focused": True},
                          {"id": 12, "idx": 2, "output": "eDP-1", "is_focused": False}]))
else:
    with open(os.environ["FAKE_NIRI_LOG"], "a") as f:
        f.write(json.dumps(args) + "\n")
"""


class NiriParkTest(unittest.TestCase):
    """On niri, Matinee's window waits on the empty workspace while a video plays, and comes back after."""

    def test_put_aside_and_back(self):
        server = load_server()
        tmp = Path(temp_dir("kino-niri-"))
        (tmp / "niri").write_text(FAKE_NIRI)
        (tmp / "niri").chmod(0o755)
        log = tmp / "log"
        env = {"PATH": f"{tmp}:{os.environ['PATH']}", "NIRI_SOCKET": str(tmp / "sock"), "FAKE_NIRI_LOG": str(log)}
        with mock.patch.dict(os.environ, env):
            server.desktop.park_ui()
            server.desktop.park_ui()  # once only
            server.desktop.focus_ui()  # the video's end: back, then focused
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        move = ["msg", "action", "move-window-to-workspace", "--window-id", "8", "--focus", "false"]
        self.assertEqual(calls, [move + ["2"], move + ["1"], ["msg", "action", "focus-window", "--id", "8"]],
                         "aside without taking the focus, back to its workspace, then focused")
        self.assertEqual(server.desktop.parked, {})
        with mock.patch.dict(os.environ, {**env, "NIRI_SOCKET": ""}):
            server.desktop.park_ui()
        self.assertEqual(len(log.read_text().splitlines()), 3, "not without niri")


class NaturalScrollTest(unittest.TestCase):
    """Whether the desktop scrolls naturally (then the wheel's volume in the player is turned round)."""

    @classmethod
    def setUpClass(cls):
        cls.server = load_server()
        cls.server.display.IN_CAGE = False

    def setUp(self):
        self.server.desktop._natural_seen.clear()
        self.home = Path(temp_dir("kino-home-"))
        patches = [mock.patch.object(self.server.desktop.Path, "home", return_value=self.home),
                   mock.patch.dict(os.environ, {"NIRI_SOCKET": "/nonexistent"}),
                   mock.patch.object(self.server.desktop, "conf", {})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("KINO_NATURAL_SCROLL", None)

    def niri(self, text):
        (self.home / ".config" / "niri").mkdir(parents=True, exist_ok=True)
        (self.home / ".config" / "niri" / "config.kdl").write_text(text)
        self.server.desktop._natural_seen.clear()
        with mock.patch.object(self.server.desktop.sys, "platform", "linux"):
            return self.server.desktop.natural_scroll()

    def test_niri(self):
        self.assertTrue(self.niri("input {\n    touchpad {\n        tap\n        natural-scroll\n    }\n}\n"))
        self.assertFalse(self.niri("input {\n    touchpad {\n        tap\n        // natural-scroll\n    }\n}\n"),
                         "commented out")
        self.assertFalse(self.niri("input {\n    mouse {\n        natural-scroll\n    }\n    touchpad { tap; }\n}\n"),
                         "the mouse's, not the touchpad's")

    def test_macos(self):
        with mock.patch.object(self.server.desktop.sys, "platform", "darwin"):
            for out, natural in ((None, True), ("1", True), ("0", False)):
                self.server.desktop._natural_seen.clear()
                with mock.patch.object(self.server.desktop, "_command_out", return_value=out):
                    self.assertEqual(self.server.desktop.natural_scroll(), natural, f"defaults says {out}")

    def test_told(self):
        with mock.patch.dict(os.environ, {"KINO_NATURAL_SCROLL": "1"}):
            self.assertTrue(self.server.desktop.natural_scroll())
        self.server.display.IN_CAGE = True
        try:
            self.assertFalse(self.server.desktop.natural_scroll(), "the TV: a remote")
        finally:
            self.server.display.IN_CAGE = False


if __name__ == "__main__":
    unittest.main()


class ReleaseSignersTest(unittest.TestCase):
    def test_a_spare_signing_key_trusted_from_the_start(self):
        lines = [l.split() for l in (ROOT / "deploy" / "release-signers").read_text().splitlines() if l.strip()]
        self.assertTrue(all(l[:2] == ["kino", 'namespaces="kino-release"'] for l in lines), lines)
        self.assertGreaterEqual(len({l[3] for l in lines}), 2, "a second key: updates go on with one of them lost")
