"""Bluetooth speakers and headphones (bluetoothctl), the sound output, its delay and volume."""
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .core import BT_AUDIO_FILE, conf, CONF_DIR, ROOT, t


BT_MAC = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}")
BT_SCAN_SECONDS = 20
_bt_scan = {"until": 0.0}


def bluetoothctl(*args, timeout=10):
    """bluetoothctl's output, "" if it is missing or did not answer in time."""
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# Without the Bluetooth service (a computer that has none) bluetoothctl waits for it without end: the settings, which
# ask whether there is Bluetooth each time they open, waited 10 s for it. Asked briefly, and one that did not answer
# is held to have none for a while.
BT_ASK = float(os.environ.get("KINO_BT_ASK", 2))
BT_ASK_AGAIN = 300
_bt_silent = {"until": 0.0}


def bluetooth_there():
    """Whether the computer has Bluetooth (a controller bluetoothctl knows)."""
    return "Controller" in bt_show()


def bt_show():
    """What `bluetoothctl show` says, asked briefly ("" without Bluetooth, or while it did not answer a moment ago)."""
    if time.monotonic() < _bt_silent["until"]:
        return ""
    try:
        out = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=BT_ASK,
                             stdin=subprocess.DEVNULL).stdout
    except subprocess.TimeoutExpired:
        _bt_silent["until"] = time.monotonic() + BT_ASK_AGAIN
        print("bluetoothctl does not answer: no Bluetooth here", file=sys.stderr)
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    return out or ""


# A paired speaker that does not answer (switched off, set to another input, or connected to another device: most
# take one at a time): BlueZ tries again and again and logs "Host is down" each time. Read from its log, kept 10 s.
BT_DOWN = re.compile(r"Host is down|Page Timeout", re.I)
BT_DOWN_SINCE = "-5min"
_bt_down = {"at": -60.0, "macs": set()}


def bt_unreachable():
    """The devices BlueZ could not reach lately (its log; empty where there is none, a Mac)."""
    if time.monotonic() - _bt_down["at"] < 10:
        return _bt_down["macs"]
    try:
        out = subprocess.run(["journalctl", "-u", "bluetooth", "--since", BT_DOWN_SINCE, "-o", "cat", "-q", "--no-pager"],
                             capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    _bt_down.update(at=time.monotonic(), macs={m for line in out.splitlines() if BT_DOWN.search(line)
                                                for m in BT_MAC.findall(line.upper())})
    return _bt_down["macs"]


def bt_devices():
    """{"available", "powered", "scanning", "devices": [{mac, name, icon, paired, connected, unreachable}]}: paired
    ones first."""
    show = bt_show()  # asked briefly: a computer without the service would hold up the main menu's panel
    if "Controller" not in show:
        return {"available": False, "devices": []}
    found = []
    for line in bluetoothctl("devices").splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 2 and parts[0] == "Device" and BT_MAC.fullmatch(parts[1]):
            found.append((parts[1], parts[2] if len(parts) > 2 else parts[1]))
    devices, down = [], bt_unreachable()
    for mac, name in found[:40]:
        info = {}
        for line in bluetoothctl("info", mac).splitlines():  # "\tName: Box"; some with nothing after the colon
            key, colon, value = line.strip().partition(":")
            if colon:
                info.setdefault(key, value.strip())
        if BT_MAC.fullmatch(name.replace("-", ":")) and info.get("Paired") != "yes":
            continue  # nameless devices nearby are of no use to choose from
        battery = re.search(r"\((\d+)\)", info.get("Battery Percentage", ""))  # "0x55 (85)", where the device tells
        devices.append({"mac": mac, "name": info.get("Alias") or name, "icon": info.get("Icon", ""),
                        "paired": info.get("Paired") == "yes", "connected": info.get("Connected") == "yes",
                        "battery": int(battery.group(1)) if battery else None})
        devices[-1]["unreachable"] = devices[-1]["paired"] and not devices[-1]["connected"] and mac in down
    devices.sort(key=lambda d: (not d["paired"], not d["connected"], d["name"].lower()))
    return {"available": True, "powered": "Powered: yes" in show, "scanning": time.time() < _bt_scan["until"],
            "devices": devices}


def bt_scan():
    """Look for devices nearby for a while (in the background); new ones show up in bt_devices meanwhile."""
    if time.time() < _bt_scan["until"]:
        return
    _bt_scan["until"] = time.time() + BT_SCAN_SECONDS
    bluetoothctl("power", "on")
    threading.Thread(target=lambda: bluetoothctl("--timeout", str(BT_SCAN_SECONDS), "scan", "on",
                                                 timeout=BT_SCAN_SECONDS + 10), daemon=True).start()


def bt_audio(info):
    """A speaker or headphones (not a remote or a keyboard), from bluetoothctl's info."""
    return re.search(r"^\s*Icon: audio-", info, re.M) is not None


def bt_last_audio():
    try:
        mac = BT_AUDIO_FILE.read_text().strip()
    except OSError:
        return None
    return mac if BT_MAC.fullmatch(mac) else None


def bt_only(mac):
    """The sound plays on one at a time: the speakers and headphones connected besides this one are let go (they
    stay paired), and this one is the one to connect again at the start."""
    for d in bt_devices()["devices"]:
        if d["connected"] and d["mac"] != mac and d["icon"].startswith("audio-"):
            bluetoothctl("disconnect", d["mac"], timeout=20)
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        BT_AUDIO_FILE.write_text(mac + "\n")
    except OSError:
        pass


BT_PAIR_SECONDS = 45


def bt_pair(mac):
    """Pair, trust and connect a new device, in one bluetoothctl session with an agent: a device that asks to confirm
    the pairing (an Xbox controller) fails without one, and bluetoothctl only registers it when its commands come on
    stdin, not with a command given. Default agent too, so whatever BlueZ asks meanwhile comes here; its question is
    answered yes only while the device chosen is being paired or connected (bluetoothctl does not say which device
    asks), no before that: a stranger's device nearby asking meanwhile is not let in. BlueZ pairable first, otherwise it keeps no key and the
    device is gone after a restart. Looked for meanwhile: a device that only advertises (Bluetooth LE, an Xbox
    controller) drops out of BlueZ's list soon after a scan ends. True if it is connected."""
    try:
        proc = subprocess.Popen(["bluetoothctl", "--agent", "NoInputNoOutput"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError:
        return False

    def say(*lines):
        proc.stdin.write("".join(line + "\n" for line in lines).encode())
        proc.stdin.flush()

    end = time.monotonic() + BT_PAIR_SECONDS
    buf, log, step, retry, ok = "", "", "agent", None, False
    try:
        while time.monotonic() < end:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            chunk = os.read(proc.stdout.fileno(), 4096).decode(errors="replace") if ready else ""
            if ready and not chunk:
                break
            buf += chunk
            log += chunk
            if step == "agent" and ("Agent registered" in buf or "Failed to register agent" in buf):
                step, buf = "pair", ""
                say("default-agent", "pairable on", "scan on", f"pair {mac}")
            elif "(yes/no)" in buf:
                buf = buf.replace("(yes/no)", "", 1)  # what came with it ("Pairing successful") is still looked at
                say("yes" if step in ("pair", "connect") else "no")
            elif step == "pair" and "not available" in buf:  # not seen (yet): asked again once the scan saw it
                step, buf, retry = "wait", "", time.monotonic() + 1
            elif step == "wait" and (mac in buf or time.monotonic() >= retry):
                step, buf = "pair", ""
                say(f"pair {mac}")
            elif step == "pair" and ("Pairing successful" in buf or "AlreadyExists" in buf):
                step, buf = "connect", ""
                say("scan off", f"trust {mac}", f"connect {mac}")
            elif step == "pair" and "Failed to pair" in buf:
                break
            elif step == "connect" and "Connection successful" in buf:
                ok = True
                break
            elif step == "connect" and "Failed to connect" in buf:
                break
        if not ok:
            tail = " | ".join(line.strip() for line in re.sub(r"\x1b\[[0-9;]*m|\r", "\n", log).splitlines()
                              if line.strip() and not line.lstrip().startswith(("[CHG]", "[DEL]")))[-600:]
            print(f"Bluetooth: {mac} not paired and connected ({step}): {tail}", file=sys.stderr)
        say("scan off", "quit")
        proc.wait(timeout=5)
    except (OSError, ValueError, subprocess.SubprocessError):
        proc.kill()
    return ok


def bt_action(mac, action):
    """connect (pairing and trusting it first if it is new), disconnect or remove (forget) a device; True if it worked.
    A speaker or headphones connected takes the place of the one before (bt_only), once it is there."""
    if action == "connect":
        info = bluetoothctl("info", mac)
        if "Paired: yes" not in info:
            bt_pair(mac)  # trusted, so it connects again by itself, and connected
        else:
            if "Trusted: yes" not in info:
                bluetoothctl("trust", mac)  # so it connects again by itself
            bluetoothctl("--timeout", "20", "connect", mac, timeout=30)
        info = bluetoothctl("info", mac)
        if "Connected: yes" not in info:
            return False
        if bt_audio(info):
            bt_only(mac)
        return True
    if action == "disconnect":
        bluetoothctl("disconnect", mac, timeout=20)
        return "Connected: yes" not in bluetoothctl("info", mac)
    if action == "remove":
        bluetoothctl("remove", mac, timeout=20)
        return "Paired: yes" not in bluetoothctl("info", mac)
    return False


# Bluetooth speakers and headphones play the sound later than the picture (100–300 ms): mpv plays it earlier by as
# much as is set for the sound output in use ("audiosync" in config.json: {"<its Bluetooth address>" or "default": ms})
AUDIO_SYNC = ("0", "50", "100", "150", "200", "250", "300")
SYNC_DEVICE = re.compile(r"default|[0-9A-F]{2}(:[0-9A-F]{2}){5}")


def audio_output():
    """The sound output in use: {"id": its Bluetooth address, or "default" for the rest, "name"}; None without wpctl."""
    try:
        out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                             timeout=5, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    props = dict(re.findall(r'^\s*\*?\s*([\w.-]+) = "(.*)"$', out, re.M))
    if not props:
        return None
    mac = props.get("api.bluez5.address", "").upper()
    return {"id": mac if SYNC_DEVICE.fullmatch(mac) else "default", "name": props.get("node.description", "")}


def audio_sync_now():
    """For the settings: the sound output in use and what is set for it; None without wpctl."""
    out = audio_output()
    return out and {**out, "ms": int(conf.get("audiosync", {}).get(out["id"], 0))}


def audio_delay(output):
    """mpv's --audio-delay for the sound output: negative, the sound comes earlier."""
    ms = int(conf.get("audiosync", {}).get(output["id"], 0)) if output else 0
    return [f"--audio-delay={-ms / 1000:g}"] if ms else []


def sink_volume():
    """The volume of the sound output (a Bluetooth speaker as well) in percent, None without PipeWire's wpctl."""
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                             timeout=5, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"Volume: ([\d.]+)", out)
    return round(float(m.group(1)) * 100) if m else None


def change_volume(step):
    """Louder or quieter by `step` percent, up to 100: that is all a speaker gives without distorting."""
    try:
        subprocess.run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{abs(step)}%{'+' if step > 0 else '-'}"],
                       capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass
    return sink_volume()


def volume_keys(mpv):
    """The volume keys in the player (kino.lua's volume mode and the remote's own): on the TV computer the sound
    of the device, so films, music and a Bluetooth speaker go together; on a desktop the player's own."""
    steps = [int(a[1]) for a in ((e.get("args") or []) for e in mpv.events if e.get("event") == "client-message")
             if len(a) == 2 and a[0] == "kino-volume" and a[1] in ("-5", "0", "5")]
    if not steps:
        return
    if display.IN_CAGE:
        v = sink_volume()
        for step in filter(None, steps):
            v = change_volume(step)
    else:
        for step in filter(None, steps):
            mpv.command("add", "volume", step)
        v = round(mpv.get("volume") or 0)
    if v is not None:
        mpv.command("show-text", mpv_ipc.osd_text(t("volume", v=v)), 3000)


WIREPLUMBER_CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wireplumber" / "wireplumber.conf.d" / "51-kino-bluetooth.conf"


def bt_stereo_only():
    """On the TV computer: speakers and headphones in A2DP only (deploy/wireplumber-bluetooth.conf), put in place by
    Matinee itself, so one updated (not installed anew) has it too, and a newer one with the next update. WirePlumber
    started anew when it changed, before the speaker is connected again (nothing plays yet)."""
    try:
        want = (ROOT / "deploy" / "wireplumber-bluetooth.conf").read_text()
        if WIREPLUMBER_CONF.exists() and WIREPLUMBER_CONF.read_text() == want:
            return
        WIREPLUMBER_CONF.parent.mkdir(parents=True, exist_ok=True)
        WIREPLUMBER_CONF.write_text(want)
    except OSError as e:
        print(f"Bluetooth in stereo only not set: {e}", file=sys.stderr)
        return
    # only where WirePlumber is and reads this file (not the tests' own config folder on a Linux desktop)
    if not (shutil.which("wpctl") and shutil.which("systemctl")) or WIREPLUMBER_CONF.parents[2] != Path.home() / ".config":
        return
    try:
        r = subprocess.run(["systemctl", "--user", "try-restart", "wireplumber.service"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return print(f"Bluetooth: WirePlumber not started anew ({e})", file=sys.stderr)
    if r.returncode == 0:
        print("Bluetooth: speakers and headphones in stereo only (WirePlumber started anew)", file=sys.stderr)


def bt_reconnect_loop(tries=6, wait=10):
    """At the start: connect the speaker or headphones paired before. Matinee and the sound server start afresh (a
    restart, an update) and the link drops, but a speaker left on does not call again by itself. One of them, as the
    sound plays on one: the one chosen last first, else the first that answers."""
    for _ in range(tries):
        audio = [d for d in bt_devices()["devices"] if d["paired"] and d["icon"].startswith("audio-")]
        if not audio or any(d["connected"] for d in audio):
            return
        last = bt_last_audio()
        for d in sorted(audio, key=lambda d: d["mac"] != last):
            if "Trusted: yes" in bluetoothctl("info", d["mac"]):
                bluetoothctl("--timeout", "20", "connect", d["mac"], timeout=30)
                if "Connected: yes" in bluetoothctl("info", d["mac"]):
                    return
        time.sleep(wait)


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import playback, display, mpv_ipc  # noqa: E402,F401
