"""Shared test helpers: start the backend, test video, stand-in mpv, remote-controlled Chromium."""
import atexit
import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mockjf import PASSWORD, USERNAME  # noqa: E402

HAVE_MPV = shutil.which("mpv") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None

def headless_shell():
    """Chrome for Testing's headless shell: no app of its own, so on a Mac it never shows in the Dock while the tests
    start and end dozens of browsers (Chromium.app's did, now and then). Installed with
    npx @puppeteer/browsers install chrome-headless-shell@stable --path ~/.cache/chrome-headless-shell"""
    found = sorted(Path("~/.cache/chrome-headless-shell/chrome-headless-shell").expanduser().glob("*/*/chrome-headless-shell"),
                   key=lambda p: [int(n) for n in p.parent.parent.name.rsplit("-", 1)[-1].split(".") if n.isdigit()])
    return str(found[-1]) if found else shutil.which("chrome-headless-shell")


# Chromium itself: on the PATH ("chromium-browser" on Fedora), or on macOS in Applications (e.g. brew install --cask ungoogled-chromium); for the
# tests the headless shell where there is one, Chromium itself only for what only it has (its New Tab page)
CHROMIUM_ITSELF = next((c for c in [os.environ.get("KINO_CHROMIUM"), shutil.which("chromium"), shutil.which("chromium-browser"),
                                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                                    os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium")]
                        if c and os.access(c, os.X_OK)), None)
CHROMIUM = next((c for c in [os.environ.get("KINO_CHROMIUM"), headless_shell()] if c and os.access(c, os.X_OK)), CHROMIUM_ITSELF)
HAVE_CHROMIUM = CHROMIUM is not None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(cond, timeout=10, interval=0.1, msg="condition"):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = cond()
            if v:
                return v
        except Exception:
            pass
        time.sleep(interval)
    raise AssertionError(f"{msg}: not met after {timeout} s")


def make_video(path, seconds=20):
    """Test video with two audio tracks (English, German) so the language choice can be checked."""
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x180:rate=10",
        "-f", "lavfi", "-i", f"sine=duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=600:duration={seconds}",
        "-map", "0", "-map", "1", "-map", "2",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=deu",
        "-g", "10", "-shortest", str(path),
    ], check=True)
    return path


def fake_mpv_dir(tmp, speed=8):
    """mpv without video and audio output, played faster: runs anywhere, even without a display."""
    d = Path(tmp) / "bin"
    d.mkdir(exist_ok=True)
    real = shutil.which("mpv")
    (d / "mpv").write_text(f'#!/bin/sh\nexec {real} --vo=null --ao=null --speed={speed} "$@"\n')
    (d / "mpv").chmod(0o755)
    return d


def temp_dir(prefix):
    """A directory of a test's own (as tempfile.mkdtemp), removed again when the tests' process ends."""
    path = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def end_with_the_tests(proc):
    """A process a test started ends with the tests' process at the latest, also when no test stopped it (before
    its directory goes: what is registered last runs first)."""
    def end():
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
    atexit.register(end)
    return proc


def _leave(signum, frame):
    sys.exit(128 + signum)


# told to end (a run stopped, its terminal closed): as an exit of its own, so what is registered above still runs
if threading.current_thread() is threading.main_thread():
    for _sig in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(_sig) is signal.SIG_DFL:
            signal.signal(_sig, _leave)


class Backend:
    """Runs server.py as its own process with its own config, port and runtime directory."""

    def __init__(self, server_url, password=PASSWORD, env=None, mpv_dir=None, language="en", conf=None):
        self.tmp = Path(temp_dir("kino-test-"))
        self.conf = self.tmp / "config" / "kino"
        self.conf.mkdir(parents=True)
        # no navigation clicks: the browser of the UI tests would play them on the developer's speakers
        conf = {"server": server_url, "username": USERNAME, "language": language, "sounds": "off", **(conf or {})}
        if password is not None:  # an older setup with the password in config.json
            conf["password"] = password
        (self.conf / "config.json").write_text(json.dumps(conf))
        self.runtime = self.tmp / "run"
        self.runtime.mkdir()
        self.port = free_port()
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.tmp / "config"), XDG_RUNTIME_DIR=str(self.runtime),
                        XDG_CACHE_HOME=str(self.tmp / "cache"), XDG_DATA_HOME=str(self.tmp / "data"), KINO_PORT=str(self.port), KINO_RETRY_MAX="1", KINO_NATURAL_SCROLL="0", KINO_SIZE_AUTO="tv",
                        KINO_FLUSH_SECONDS="1", KINO_SIMILAR_PAUSE="0", MANGOHUD="0", KINO_PHONE_PORT="0", KINO_PHONE_HOST="127.0.0.1",
                        # one's own channels made when a test asks for them, not ahead of it
                        KINO_OWN_WARM_AFTER="86400",
                        KINO_EXIT_WITH_PARENT="1", KINO_HANDOFF_ACK_WAIT="2", KINO_TAILSCALE="/nonexistent", KINO_TIMEDATECTL="/nonexistent", KINO_DESKTOP_UPDATES="0")  # never the developer's own Tailscale
        self.env.pop("NIRI_SOCKET", None)  # tests must never move windows on the developer's desktop
        self.env["WAYLAND_DISPLAY"] = "kino-test-none"  # nor switch its screen's mode (a real wlr-randr reaches none)
        if mpv_dir:
            self.env["PATH"] = f"{mpv_dir}:{self.env['PATH']}"
        self.env.update(env or {})
        self.log = open(self.tmp / "server.log", "w+")
        self.proc = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait_ready=True):
        self.cookie = ""  # a new backend, a new secret
        self.proc = end_with_the_tests(subprocess.Popen([sys.executable, str(Path(os.environ.get("KINO_SERVER", ROOT / "server.py")))],
                                                        env=self.env, stdout=self.log, stderr=self.log))
        wait_for(lambda: self.request("/status")[0] == 200, msg="backend started")
        if wait_ready:
            wait_for(lambda: self.request("/session")[0] == 200, timeout=15, msg="backend logged in")
        return self

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:  # stuck: gone all the same, and the cleanup below still runs
                self.proc.kill()
                self.proc.wait(5)
        # stop any mpv processes left over from this test
        subprocess.run(["pkill", "-f", "--", f"--input-ipc-server={self.runtime}"], check=False)
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_password(self, password):
        """Run set-password against this backend's config."""
        return subprocess.run([sys.executable, str(ROOT / "set-password"), "--stdin"], input=password + "\n",
                              env=self.env, capture_output=True, text=True, timeout=30)

    def device_id(self):
        """The id this Matinee tells Jellyfin it is (state.json)."""
        return json.loads((self.conf / "state.json").read_text())["device_id"]

    def logtext(self):
        self.log.flush()
        return (self.tmp / "server.log").read_text()

    def page_cookie(self):
        """The cookie Matinee's page gets with the page (the backend refuses requests without it)."""
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + "/"), timeout=10) as r:
                return (r.headers.get("Set-Cookie") or "").split(";")[0]
        except OSError:
            return ""

    def request(self, path, body=None, headers=None, cookie=True):
        """A request as Matinee's page makes it (cookie=False: as any other program on the computer)."""
        if cookie and not getattr(self, "cookie", ""):
            self.cookie = self.page_cookie()
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json",
                                              **({"Cookie": self.cookie} if cookie and self.cookie else {}), **(headers or {})})
        try:
            r = urllib.request.urlopen(req, timeout=10)
            data = r.read()
            return r.status, (json.loads(data) if data and r.headers.get_content_type() == "application/json" else data)
        except urllib.error.HTTPError as e:
            data = e.read()
            e.close()
            try:
                return e.code, json.loads(data)
            except ValueError:
                return e.code, data
        except OSError:
            return None, None

    def mpv_socket(self):
        socks = list(self.runtime.glob("kino-mpv-*.sock"))
        return socks[0] if socks else None

    def mpv(self, *command):
        """Send a command to the running mpv (e.g. a key press)."""
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(self.mpv_socket()))
        s.settimeout(5)
        s.sendall(json.dumps({"command": list(command), "request_id": 1}).encode() + b"\n")
        buf = b""
        while True:
            got = s.recv(65536)
            if not got:  # mpv gone: fail the test instead of turning in this loop forever
                s.close()
                raise ConnectionError("mpv closed the connection")
            buf += got
            for line in buf.split(b"\n")[:-1]:
                msg = json.loads(line)
                if msg.get("request_id") == 1:
                    s.close()
                    return msg.get("data")


class Chromium:
    """Headless Chromium with a minimal DevTools client (standard library only)."""

    VK = {"Enter": 13, "Escape": 27, "Backspace": 8, "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39,
          "ArrowDown": 40, "Home": 36, "ContextMenu": 93, "MediaPlayPause": 179, "MediaFastForward": 228,
          "MediaRewind": 227, "MediaTrackNext": 176, "MediaTrackPrevious": 177, "MediaStop": 178, "AudioVolumeUp": 175, "AudioVolumeDown": 174}

    def __init__(self, url):
        self.tmp = temp_dir("kino-chromium-")
        self.port = free_port()
        self.proc = end_with_the_tests(subprocess.Popen([
            CHROMIUM, "--headless", "--disable-gpu", f"--user-data-dir={self.tmp}", "--window-size=1920,1080",
            f"--remote-debugging-port={self.port}", "--no-first-run",
            "--use-mock-keychain", "--password-store=basic", url,  # never ask for the macOS keychain or a keyring
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        tabs = wait_for(lambda: [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")) if t["type"] == "page"],
                        msg="Chromium started")
        path = tabs[0]["webSocketDebuggerUrl"].split(str(self.port), 1)[1]
        self.sock = socket.create_connection(("127.0.0.1", self.port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.n = 0
        self.errors = []
        self.call("Runtime.enable")
        # the page as on a computer of 8 cores and 8 GB, wherever the tests run: on one of 4 (the Fedora VM) Matinee
        # reduces its effects by itself (weakDevice), and the tests of its picture walls found none. A test of that
        # sets them anew (configurable). The full Chromium (Fedora's) runs the script only once Page is enabled.
        self.call("Page.enable")
        self.call("Page.addScriptToEvaluateOnNewDocument", source="for (const [k, v] of [['hardwareConcurrency', 8], "
                  "['deviceMemory', 8]]) Object.defineProperty(navigator, k, {value: v, configurable: true});")
        self.call("Page.reload")
        self.tv_size()

    def tv_size(self):
        """The page at the TV's 1920×1080. --window-size alone gives that in the headless shell only: the full
        Chromium's headless window (Linux) keeps room for a frame it does not draw, and the page gets 937 high."""
        self.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)

    def _recvn(self, n):
        while len(self.buf) < n:
            got = self.sock.recv(1 << 20)
            if not got:  # Chromium gone (crashed): fail the test instead of waiting forever
                raise ConnectionError("Chromium closed the connection")
            self.buf += got
        r, self.buf = self.buf[:n], self.buf[n:]
        return r

    def _recv(self):
        _, b2 = self._recvn(2)
        n = b2 & 127
        if n == 126:
            n = struct.unpack(">H", self._recvn(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recvn(8))[0]
        return json.loads(self._recvn(n))

    def call(self, method, **params):
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask, n = os.urandom(4), len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            msg = self._recv()
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(msg["params"]["exceptionDetails"].get("exception", {}).get("description") or msg["params"]["exceptionDetails"]["text"])
            if msg.get("id") == self.n:
                return msg.get("result")

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise AssertionError(f"JS error in {expr!r}: {r['exceptionDetails']}")
        return r["result"].get("value")

    def wait_js(self, expr, timeout=10, msg=None):
        # DOM nodes come back as empty objects, so check the truthiness in JS
        try:
            return wait_for(lambda: self.js(f"!!({expr})"), timeout=timeout, msg=msg or expr)
        except AssertionError as e:  # where the UI was instead, for a test that fails only now and then
            seen = self.js("""(() => { try { const a = document.activeElement;
                return `page ${current?.name}, focus ${a?.dataset?.k ?? a?.tagName}, options ${!$('#options').hidden}`; }
                catch (e) { return String(e); } })()""")
            raise AssertionError(f"{e} ({seen})") from None

    def key(self, k, hold_ms=0, pause=0.35):
        vk = self.VK[k]
        self.call("Input.dispatchKeyEvent", type="rawKeyDown", key=k, code=k, windowsVirtualKeyCode=vk)
        end = time.time() + hold_ms / 1000
        while time.time() < end:  # key repeat like a held key
            time.sleep(0.05)
            self.call("Input.dispatchKeyEvent", type="rawKeyDown", key=k, code=k, windowsVirtualKeyCode=vk, autoRepeat=True)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=k, code=k, windowsVirtualKeyCode=vk)
        time.sleep(pause)

    def type(self, text, pause=0.15):
        """Type characters like on a keyboard."""
        for ch in text:
            code = f"Key{ch.upper()}" if ch.isalpha() else f"Digit{ch}" if ch.isdigit() else "Space"
            self.call("Input.dispatchKeyEvent", type="keyDown", key=ch, code=code, text=ch, windowsVirtualKeyCode=ord(ch.upper()))
            self.call("Input.dispatchKeyEvent", type="keyUp", key=ch, code=code, windowsVirtualKeyCode=ord(ch.upper()))
            time.sleep(pause)

    def focus_key(self):
        return self.js("document.activeElement?.dataset?.k")

    def alive(self):
        """Whether Chromium still runs and answers: one that crashed fails every test after it otherwise."""
        if self.proc.poll() is not None:
            return False
        try:
            return self.call("Runtime.evaluate", expression="1", returnByValue=True)["result"]["value"] == 1
        except (OSError, ConnectionError, ValueError, KeyError, TypeError):
            return False

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)
