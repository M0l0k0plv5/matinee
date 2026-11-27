"""The thin client: the UI watchdog, switching off when nobody watches, Tailscale and the clock, the processes Matinee
started."""
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

from .core import RUNTIME, t
from .preferences import setting
from .display import display_connected
from .video_player import player
from .live_events import broadcast
from .music_player import music


TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def time_synced():
    """Whether the clock is set by time servers (systemd-timesyncd, set up by install.sh): True or False, None where
    that cannot be asked (a Mac sets its clock by itself)."""
    binary = os.environ.get("KINO_TIMEDATECTL") or shutil.which("timedatectl")  # KINO_TIMEDATECTL: the tests'
    if not binary or not os.path.exists(binary):
        return None
    try:
        out = subprocess.run([binary, "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return {"yes": True, "no": False}.get(out.stdout.strip())


def tailscale_info():
    """What the settings show of Tailscale (read only): signed in or not, this device's name and addresses, the
    tailnet, the exit node in use and how many of the other devices are online. None without Tailscale."""
    binary = os.environ.get("KINO_TAILSCALE") or shutil.which("tailscale") or TAILSCALE_APP  # KINO_TAILSCALE: the tests'
    if not os.path.exists(binary):
        return None
    try:
        out = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=3)
        d = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"state": "unknown"}
    me, peers = d.get("Self") or {}, list((d.get("Peer") or {}).values())
    exit_node = next((p for p in peers if p.get("ExitNode")), None)
    return {"state": d.get("BackendState", "unknown"), "name": me.get("HostName"),
            "dns": (me.get("DNSName") or "").rstrip(".") or None, "ips": me.get("TailscaleIPs") or [],
            "tailnet": (d.get("CurrentTailnet") or {}).get("Name"),
            "exit": exit_node and ((exit_node.get("DNSName") or "").split(".")[0] or exit_node.get("HostName")),
            "online": sum(1 for p in peers if p.get("Online")), "peers": len(peers)}


# On the thin client: if the UI stops saying it is there (Chromium hangs, or shows a blank page after
# starting), Matinee starts again: the service restarts it once cage has gone. Not while a video plays in front.
UI_ALIVE_TIMEOUT = float(os.environ.get("KINO_ALIVE_TIMEOUT", "120"))
UI_ALIVE_FIRST = float(os.environ.get("KINO_ALIVE_FIRST", "180"))
# Started again by the watchdog a moment ago and still silent: mostly the TV is off (Chromium then runs no page at
# all). The next look comes after this long instead, rather than restarting every few minutes (each of which may wake
# the TV).
UI_WATCHDOG_CALM = float(os.environ.get("KINO_WATCHDOG_CALM", str(30 * 60)))
UI_WATCHDOG_STAMP = RUNTIME / "kino-watchdog"
ui_alive = None
ui_started = False


# Switching off by itself (autooff, in cage only): so many hours without a key, in the UI or the player, and the
# thin client switches off, a minute after saying so (on the screen and in the player)
last_active = time.monotonic()
AUTOOFF_HOUR = float(os.environ.get("KINO_AUTOOFF_HOUR", 3600))  # shortened in the tests
AUTOOFF_WARN = float(os.environ.get("KINO_AUTOOFF_WARN", 60))


def note_active():
    global last_active
    last_active = time.monotonic()


def autooff_loop():
    warned = False
    while True:
        time.sleep(min(10, AUTOOFF_WARN / 4))
        off = setting("autooff")
        idle = time.monotonic() - last_active
        if off == "off" or idle < int(off) * AUTOOFF_HOUR - AUTOOFF_WARN:
            warned = False
        elif idle < int(off) * AUTOOFF_HOUR:
            if not warned:
                warned = True
                broadcast("autooff")
                player.say(t("autooff"), AUTOOFF_WARN)
        else:
            print("Switching off: nobody there for a while", file=sys.stderr)
            subprocess.Popen(["systemctl", "poweroff"])
            return


def ui_watchdog():
    started = dark = time.monotonic()
    first = UI_ALIVE_FIRST
    try:  # a new version on probation is looked at again soon: it is to go back by itself if its page does not come
        if time.time() - UI_WATCHDOG_STAMP.stat().st_mtime < UI_WATCHDOG_CALM and not updates._probation["to"]:
            first = UI_WATCHDOG_CALM
    except OSError:
        pass
    while True:
        time.sleep(min(10, UI_ALIVE_TIMEOUT / 4, first / 4))
        if player.playing or music.active:  # nobody needs the UI now, and a restart would stop what plays
            continue
        now = time.monotonic()
        # kino --session starts Chromium once the TV is on, and again once it is back after Chromium fell over with
        # it switched off: nothing to look after meanwhile, and the time to start from the moment it is on
        if not display_connected():
            started = dark = now
            continue
        if (ui_alive is None and now - started > first) or (ui_alive is not None and now - max(ui_alive, dark) > UI_ALIVE_TIMEOUT):
            print("The UI is not answering: starting Matinee again", file=sys.stderr)
            try:
                UI_WATCHDOG_STAMP.write_text("")
            except OSError:
                pass
            end_chromium()
            os._exit(1)


def processes():
    """(pid, parent pid, command line) of every process: from /proc on Linux, from ps elsewhere (e.g. macOS)."""
    found = []
    if Path("/proc").is_dir():
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                ppid = int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1])
                cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except (OSError, ValueError, IndexError):
                continue
            found.append((int(proc.name), ppid, cmd))
        return found
    try:
        out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return found
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            found.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return found


def end_chromium():
    """Under the kino --session script: end Chromium beside the backend. The script then ends (it watches both), cage
    ends with it, and systemd starts Matinee again."""
    parent = os.getppid()
    procs = processes()
    if not any(pid == parent and "--session" in cmd.split() for pid, _, cmd in procs):
        return
    for pid, ppid, _ in procs:
        if ppid == parent and pid != os.getpid():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # there, but someone else's
        return True
    return True


def players_left_over():
    """mpv processes started by a Matinee backend that has gone (e.g. killed on a desktop): their socket is
    RUNTIME/kino-mpv-<pid>-… or kino-music-<pid>-…, with the pid of the backend that started them."""
    pattern = re.compile(rf"--input-ipc-server={re.escape(str(RUNTIME))}/kino-(?:mpv|music)-(\d+)-")
    for pid, _, cmd in processes():
        m = pattern.search(cmd)
        if m and int(m.group(1)) != os.getpid() and not alive(int(m.group(1))):
            yield pid



def sockets_left_over():
    """The players' sockets (RUNTIME/kino-mpv-<pid>-….sock, kino-music-…) of Matinee backends that have gone: a player
    ended with its backend killed leaves its socket behind, one more at every such start."""
    pattern = re.compile(r"kino-(?:mpv|music)-(\d+)-[0-9a-f]+\.sock")
    for path in RUNTIME.glob("kino-*-*.sock"):
        m = pattern.fullmatch(path.name)
        if m and int(m.group(1)) != os.getpid() and not alive(int(m.group(1))):
            yield path


def end_players(wait=0):
    """The players ended, where the one playing was kept first, as Back would have; waited for up to
    `wait` seconds each (before the backend starts again in its place: one left on would play on out of its reach)."""
    try:
        player.keep_now()
    except Exception as e:  # never in the way of ending
        print(f"Could not keep where it was: {e}", file=sys.stderr)
    for p in (music.proc, player.proc):
        if p and p.poll() is None:
            p.terminate()
            if wait:
                try:
                    p.wait(wait)
                except subprocess.TimeoutExpired:
                    p.kill()


def stop_players(*_):
    """When the backend ends, its players end with it: no music playing on with nobody to stop it."""
    end_players()
    # at once: sys.exit would wait for every worker thread, and one waiting on a server slow to answer
    # held up stopping by minutes. Nothing is lost: files are written whole or not at all (write_private)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def exit_with_parent(parent):
    """Ends the backend once the process that started it is gone (KINO_EXIT_WITH_PARENT): it was handed on to init."""
    while os.getppid() == parent:
        time.sleep(1)
    print("The process that started Matinee is gone: ending", file=sys.stderr)
    stop_players()


# The Menu key of a remote, read from the input devices themselves: Chromium drops KEY_MENU (what a Fire TV remote
# sends; a keyboard's Menu key is KEY_COMPOSE, which it takes), the page never seeing it. Read here, the page is
# told ("menukey") and takes it as the key; while a video plays mpv has the screen, and the key is its own.
MENU_KEYS = (139,)  # KEY_MENU
INPUT_DEVICES = Path(os.environ.get("KINO_INPUT_DEVICES", "/proc/bus/input/devices"))
INPUT_DIR = Path(os.environ.get("KINO_INPUT_DIR", "/dev/input"))
INPUT_RESCAN = 1.0  # seconds: a remote waking up comes back as a new device, its first key with it
INPUT_EVENT = struct.Struct("llHHi")  # struct input_event: the time, type, code, value
EV_KEY, EV_ABS = 1, 3

# A game controller (an Xbox controller by USB or Bluetooth, or any the kernel calls a gamepad): neither cage nor
# Chromium take it as keys, so it is read here as well and its buttons become the remote's keys. Each goes where the
# remote's would: to mpv while a video plays (keydown and keyup over its IPC, so a held key repeats there as a held
# remote key does), to the page otherwise (remoteCommand in input.js).
PAD_KEYS = {  # what each key is: (mpv's key, the page's key), None where it has none
    "up": ("UP", "ArrowUp"), "down": ("DOWN", "ArrowDown"), "left": ("LEFT", "ArrowLeft"), "right": ("RIGHT", "ArrowRight"),
    "ok": ("ENTER", "Enter"), "back": ("ESC", "Escape"), "play": ("PLAYPAUSE", "MediaPlayPause"),
    "menu": ("MENU", "ContextMenu"), "search": (None, "BrowserSearch"), "home": (None, "Home"),
    "rewind": ("REWIND", "MediaRewind"), "forward": ("FORWARD", "MediaFastForward"),
    "louder": ("VOLUME_UP", "AudioVolumeUp"), "quieter": ("VOLUME_DOWN", "AudioVolumeDown"),
}
PAD_BUTTONS = {  # the kernel's button codes (an Xbox controller's names)
    304: "ok",  # A (BTN_SOUTH)
    305: "back",  # B (BTN_EAST)
    307: "play",  # X
    308: "search",  # Y
    310: "rewind", 311: "forward",  # LB, RB
    312: "quieter", 313: "louder",  # LT, RT where they are buttons
    314: "menu", 315: "menu",  # View, Menu
    316: "home",  # the Xbox button (BTN_MODE)
    544: "up", 545: "down", 546: "left", 547: "right",  # a D-pad that comes as buttons
}
PAD_REPEATS = {"up", "down", "left", "right", "louder", "quieter"}  # held: again and again, as a held remote key
PAD_REPEAT_FIRST, PAD_REPEAT_EVERY = 0.4, 0.11  # seconds: the page's own pace for a held arrow (STEP_MS in input.js)
BTN_SOUTH = 304
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 9, 10, 16, 17
STICK_ON, STICK_OFF = 0.6, 0.4  # of the way to the edge: a direction there, and let go back here (no flicker between)


def key_bits(text, kind):
    """The bits of a device's B: line (KEY, ABS, …) as one number: words of a long, the highest first, without their
    zeros."""
    width = struct.calcsize("l") * 2
    found = re.search(rf"^B: {kind}=([0-9a-f ]+)$", text, re.M)
    return int("".join(w.rjust(width, "0") for w in found.group(1).split()), 16) if found else 0


def input_devices(text):
    """The event devices worth reading, from the kernel's list of input devices: {"event11": ("menu", 0) or ("pad",
    its axes' bits)}. A game controller is one with the gamepad's south button; a remote one with a Menu key."""
    out = {}
    for block in text.split("\n\n"):
        handler = re.search(r"^H: Handlers=.*?\b(event\d+)\b", block, re.M)
        if not handler:
            continue
        keys = key_bits(block, "KEY")
        if keys >> BTN_SOUTH & 1:
            out[handler.group(1)] = ("pad", key_bits(block, "ABS"))
        elif any(keys >> k & 1 for k in MENU_KEYS):
            out[handler.group(1)] = ("menu", 0)
    return out


def axis_range(fd, axis):
    """An axis's lowest and highest value, from the device (EVIOCGABS); a stick's of an Xbox controller by USB where
    the device cannot say (as a stand-in in the tests)."""
    import fcntl
    info = struct.Struct("6i")  # struct input_absinfo: value, minimum, maximum, fuzz, flat, resolution
    request = (2 << 30) | (info.size << 16) | (ord("E") << 8) | (0x40 + axis)  # _IOR('E', 0x40 + axis, …)
    try:
        _, low, high, *_ = info.unpack(fcntl.ioctl(fd, request, bytes(info.size)))
    except OSError:
        return -32768, 32767
    return (low, high) if high > low else (-32768, 32767)


class Pad:
    """One game controller: its buttons, D-pad, left stick and triggers made into the remote's keys."""

    def __init__(self, fd, axes):
        self.fd = fd
        # the triggers: ABS_GAS and ABS_BRAKE where it has them (Bluetooth, where ABS_Z and ABS_RZ are the right
        # stick), ABS_Z and ABS_RZ where the right stick is ABS_RX and ABS_RY (USB); none otherwise (many other
        # gamepads: ABS_Z and ABS_RZ are their right stick, at rest halfway)
        self.triggers = ({ABS_BRAKE: "quieter", ABS_GAS: "louder"} if axes >> ABS_GAS & 1
                         else {ABS_Z: "quieter", ABS_RZ: "louder"} if axes >> ABS_RX & 1 and axes >> ABS_RY & 1
                         else {})
        self.ranges = {a: axis_range(fd, a) for a in (ABS_X, ABS_Y, *self.triggers) if axes >> a & 1}
        self.stick = [0.0, 0.0]
        self.hat = [0, 0]
        self.holding = {}  # a key → what holds it (a button, the D-pad, the stick, a trigger)
        self.sent = {}  # a key held → where it went ("mpv" or "page"), its next repeat
        self.out = []  # what to send: (key, "down" or "up")

    def hold(self, key, by, on):
        """Key held or let go by one of the things that may hold it; down at the first, up once none does."""
        by_now = self.holding.setdefault(key, set())
        was = bool(by_now)
        (by_now.add if on else by_now.discard)(by)
        if bool(by_now) != was:
            self.out.append((key, "down" if by_now else "up"))

    def direction(self, by, x, y):
        """A direction from the D-pad or the stick: the one it points to most, if any."""
        want = None
        if x or y:
            want = (("left" if x < 0 else "right") if abs(x) > abs(y) else ("up" if y < 0 else "down"))
        for key in ("up", "down", "left", "right"):
            self.hold(key, by, key == want)

    def event(self, kind, code, value):
        if kind == EV_KEY and code in PAD_BUTTONS and value in (0, 1):  # 2: the kernel's own repeat
            self.hold(PAD_BUTTONS[code], code, value == 1)
        elif kind == EV_ABS and code in (ABS_HAT0X, ABS_HAT0Y):
            self.hat[code - ABS_HAT0X] = max(-1, min(1, value))
            self.direction("hat", *self.hat)
        elif kind == EV_ABS and code in (ABS_X, ABS_Y) and code in self.ranges:
            low, high = self.ranges[code]
            self.stick[code] = (value - (low + high) / 2) / ((high - low) / 2)
            held = any(self.holding.get(k) and "stick" in self.holding[k] for k in ("up", "down", "left", "right"))
            limit = STICK_OFF if held else STICK_ON
            x, y = (s if abs(s) >= limit else 0 for s in self.stick)
            self.direction("stick", x, y)
        elif kind == EV_ABS and code in self.triggers and code in self.ranges:
            low, high = self.ranges[code]
            pressed = (value - low) / (high - low)
            key = self.triggers[code]
            on = pressed >= (STICK_OFF if self.holding.get(key) and code in self.holding[key] else STICK_ON)
            self.hold(key, code, on)

    def send(self):
        """What came, sent: each key to mpv while a video plays, to the page otherwise; its letting go to where it
        went."""
        now = time.monotonic()
        for key, what in self.out:
            mpv_key, page_key = PAD_KEYS[key]
            if what == "down":
                note_active()
                where = "mpv" if player.playing else "page"
                self.sent[key] = (where, now + PAD_REPEAT_FIRST)
                if where == "mpv" and mpv_key:
                    player.remote("keydown", mpv_key)
                elif where == "page" and page_key:
                    broadcast("remote:" + json.dumps({"key": page_key, "type": "keydown"}))
            elif key in self.sent:
                where, _ = self.sent.pop(key)
                if where == "mpv" and mpv_key:
                    player.remote("keyup", mpv_key)
                elif where == "page" and page_key:
                    broadcast("remote:" + json.dumps({"key": page_key, "type": "keyup"}))
        self.out = []

    def repeat(self):
        """A key held long enough for the page: again (mpv repeats its own). The seconds to the next one."""
        now, wait = time.monotonic(), INPUT_RESCAN
        for key, (where, due) in list(self.sent.items()):
            if where != "page" or key not in PAD_REPEATS:
                continue
            if now >= due:
                broadcast("remote:" + json.dumps({"key": PAD_KEYS[key][1], "type": "keydown", "repeat": True}))
                due = now + PAD_REPEAT_EVERY
                self.sent[key] = (where, due)
            wait = min(wait, due - now)
        return max(0.0, wait)

    def let_go(self):
        """The controller gone (switched off, out of range): whatever it held let go, no key left down."""
        for key, by in self.holding.items():
            if by:
                by.clear()
                self.out.append((key, "up"))
        self.send()


def menu_key_loop():
    """Watch the devices with a Menu key and the game controllers (looked for again every INPUT_RESCAN seconds): tell
    the page of each Menu key, make each controller's buttons the remote's keys."""
    fds, pads = {}, {}
    while True:
        try:
            wanted = input_devices(INPUT_DEVICES.read_text())
        except OSError:
            wanted = {}
        for name in [n for n in fds if n not in wanted]:
            gone = pads.pop(fds[name], None)
            if gone:
                gone.let_go()
            os.close(fds.pop(name))
        for name in wanted.keys() - fds.keys():
            try:
                fds[name] = os.open(INPUT_DIR / name, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue  # not ours to read (not in the group "input"): the key stays as it was
            kind, axes = wanted[name]
            if kind == "pad":
                pads[fds[name]] = Pad(fds[name], axes)
                print(f"A game controller ({name}): its buttons are the remote's keys", file=sys.stderr)
        if not fds:
            time.sleep(INPUT_RESCAN)
            continue
        wait = min([INPUT_RESCAN, *(p.repeat() for p in pads.values())])
        ready, _, _ = select.select(list(fds.values()), [], [], wait)
        for fd in ready:
            try:
                data = os.read(fd, INPUT_EVENT.size * 64)
            except OSError:
                data = b""
            if not data:  # the device gone (a remote asleep): looked for again
                name = next(n for n, f in fds.items() if f == fd)
                gone = pads.pop(fd, None)
                if gone:
                    gone.let_go()
                os.close(fds.pop(name))
                time.sleep(INPUT_RESCAN)
                continue
            pad = pads.get(fd)
            for i in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                _, _, kind, code, value = INPUT_EVENT.unpack_from(data, i)
                if pad:
                    pad.event(kind, code, value)
                elif kind == EV_KEY and code in MENU_KEYS and value == 1 and not player.playing:
                    broadcast("menukey")
            if pad:
                pad.send()


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import updates  # noqa: E402,F401
