"""Live changes from Jellyfin (its WebSocket) passed on to the UI (server-sent events)."""
import base64
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import core
from .core import RETRY_MAX, state, t, TICKS
from .jellyfin_api import auth_header, jf, log_net, ready
from .handoff import (answer_handoff, HANDOFF_ACK_HEADER, HANDOFF_ACK_WAIT, handoff_acks, handoff_acks_lock,
    HANDOFF_HEADER, handoff_plan, HANDOFF_SKEW)
from .library import badges_cache, similar_wanted


listeners = set()
listeners_lock = threading.Lock()


def broadcast(event):
    with listeners_lock:
        for q in listeners:
            q.put(event)


class WebSocket:
    """Minimal WebSocket client (text messages only), enough for Jellyfin's /socket."""

    def __init__(self, url, headers=()):
        u = urllib.parse.urlsplit(url)
        secure = u.scheme in ("https", "wss")
        port = u.port or (443 if secure else 80)
        sock = socket.create_connection((u.hostname, port), timeout=30)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        host = u.hostname if u.port is None else f"{u.hostname}:{u.port}"
        sock.sendall((
            f"GET {u.path}?{u.query} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n"
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("connection closed during handshake")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise OSError(f"WebSocket rejected: {status.decode(errors='replace')}")
        self.sock = sock
        self.parts = []

    def send(self, text, opcode=1):
        data = text.encode() if isinstance(text, str) else text
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _frame(self):
        """Take one complete frame from the buffer, None if it is still incomplete."""
        b = self.buf
        if len(b) < 2:
            return None
        n, pos = b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, pos = int.from_bytes(b[2:4], "big"), 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, pos = int.from_bytes(b[2:10], "big"), 10
        if b[1] & 0x80:
            pos += 4  # servers normally never mask
        if len(b) < pos + n:
            return None
        self.buf = b[pos + n:]
        return b[0] & 0x80, b[0] & 0x0F, b[pos:pos + n]

    def recv(self, timeout):
        """Next text message, or None on timeout."""
        self.sock.settimeout(timeout)
        while True:
            f = self._frame()
            if f is None:
                try:
                    chunk = self.sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    return None
                if not chunk:
                    raise OSError("WebSocket closed")
                self.buf += chunk
                continue
            fin, op, data = f
            if op == 8:
                raise OSError("WebSocket closed by server")
            if op == 9:
                self.send(data, opcode=10)
            elif op in (0, 1):
                self.parts.append(data)
                if fin:
                    msg, self.parts = b"".join(self.parts).decode(), []
                    return msg

    def close(self):
        self.sock.close()


server_epoch = 0  # counts the switches to another server or user (setup_jellyfin)


def same_user(data):
    """A message to this session sent by this Matinee's own user: another user's is never followed. Jellyfin 12 names
    none (all zeros): then what is asked must be checked by itself."""
    sender = str(data.get("ControllingUserId") or "").replace("-", "")
    return not sender.strip("0") or sender == str(state.get("user_id") or "").replace("-", "")


def handoff_message(data):
    """A message to this session: another Matinee of the same user asking it to go on (send_handoff)."""
    args = data.get("Arguments") if isinstance(data.get("Arguments"), dict) else {}
    if data.get("Name") != "DisplayMessage" or args.get("Header") not in (HANDOFF_HEADER, HANDOFF_ACK_HEADER):
        return
    # another user's never. Jellyfin 12 names none (all zeros), so what is asked is done only for a Matinee of this
    # user (answer_handoff); the answer is a secret of the asking Matinee's
    if not same_user(data):
        return
    if args.get("Header") == HANDOFF_ACK_HEADER:
        with handoff_acks_lock:
            told = handoff_acks.get(str(args.get("Text") or ""))
        if told:
            told.set()
        return
    try:
        what = json.loads(args.get("Text") or "")
    except ValueError:
        return
    if not isinstance(what, dict):
        return

    def go():
        until = what.get("until")
        if isinstance(until, (int, float)) and time.time() > until + HANDOFF_SKEW:
            print(f"Going on here: asked {time.time() - until + HANDOFF_ACK_WAIT:.0f} s ago, gone on there by now",
                  file=sys.stderr)
            return
        try:
            looking = time.monotonic()
            plan = handoff_plan(what)  # what this Matinee cannot play it does not say it goes on with
            if plan and time.monotonic() - looking > HANDOFF_ACK_WAIT / 2:
                # looking it up took so long that the other may have gone on itself: an answer now could play it
                # twice (measured here: the clocks of the two need not agree)
                print("Going on here: found too late, gone on there by now", file=sys.stderr)
                return
            if not plan or not answer_handoff(what):
                return
        except Exception as e:
            print(f"Going on here: not answered ({e})", file=sys.stderr)
            return
        try:
            plan()
            if what.get("kind") != "music":
                broadcast("playing")  # the page follows it, as if it had started it
        except Exception as e:
            print(f"Going on here failed: {e}", file=sys.stderr)
    threading.Thread(target=go, daemon=True).start()


# ---------- Jellyfin's remote control ----------

# Its commands to this session (from its web app's cast menu, a phone app) taken as the remote's keys would be: while a
# film or video plays as key presses in mpv (input.conf, kino.lua: the info panel, seeking, skipping as with the remote),
# otherwise as keys on the page (remoteCommand in input.js). What no key does goes straight to the player. Who may send
# them is Jellyfin's to say (a user's right to control others' sessions): someone helping on another's Matinee is meant.
REMOTE_KEYS = {  # GeneralCommand → (mpv's key, the page's key), None where it has none
    "MoveUp": ("UP", "ArrowUp"), "MoveDown": ("DOWN", "ArrowDown"),
    "MoveLeft": ("LEFT", "ArrowLeft"), "MoveRight": ("RIGHT", "ArrowRight"),
    "PageUp": ("PGUP", None), "PageDown": ("PGDWN", None),
    "Select": ("ENTER", "Enter"), "Back": ("ESC", "Escape"), "GoHome": (None, "Home"),
    "GoToSearch": (None, "BrowserSearch"),
    "ToggleContextMenu": ("MENU", "ContextMenu"), "ToggleOsdMenu": ("MENU", None),
    "ChannelUp": ("CHANNEL_UP", None), "ChannelDown": ("CHANNEL_DOWN", None),
    "VolumeUp": ("VOLUME_UP", "AudioVolumeUp"), "VolumeDown": ("VOLUME_DOWN", "AudioVolumeDown"),
}
REMOTE_PLAYSTATE = {  # Playstate → (mpv's key, the page's key)
    "PlayPause": ("PLAYPAUSE", "MediaPlayPause"), "Pause": ("PAUSEONLY", None), "Unpause": ("PLAYONLY", None),
    "Stop": ("STOP", "MediaStop"), "NextTrack": ("NEXT", "MediaTrackNext"), "PreviousTrack": ("PREV", "MediaTrackPrevious"),
    "FastForward": ("FORWARD", "MediaFastForward"), "Rewind": ("REWIND", "MediaRewind"),
}
# what this session tells Jellyfin it can be sent (DisplayMessage: also what another Matinee asks of it, handoff_message)
REMOTE_COMMANDS = ["DisplayMessage", *REMOTE_KEYS, "Mute", "Unmute", "ToggleMute", "SetVolume",
                   "SetAudioStreamIndex", "SetSubtitleStreamIndex", "SendString"]
REMOTE_PLAYABLE = ("Movie", "Episode", "Video", "MusicVideo")  # what "Play on" may start here: what the player plays
# What Jellyfin's "Play" cannot name, as Matinee Remote asks for it: a message (DisplayMessage) with this header and what
# to play as its text, in the form another Matinee hands it over (handoff_plan: one of its own channels, say). Whoever
# Jellyfin lets control this session may send it, as any command of its remote control.
REMOTE_PLAY_HEADER = "kino-remote-play"


def remote_page(what):
    """A key, a text or a message for the page (remoteCommand in input.js), as JSON: a line break in a text would end
    the event (/events)."""
    broadcast("remote:" + json.dumps(what))


def remote_sound(mute=None, volume=None):
    """Muted or not ("toggle" switches), or a volume in percent: the player's own while it plays on a desktop, the
    device's on the TV computer (as the volume keys, volume_keys)."""
    player = video_player.player
    if not display.IN_CAGE:
        if mute == "toggle":
            player.remote("cycle", "mute")
        elif mute is not None:
            player.remote("set_property", "mute", mute)
        if volume is not None:
            player.remote("set_property", "volume", volume)
        return
    args = (["set-mute", "@DEFAULT_AUDIO_SINK@", "toggle" if mute == "toggle" else "1" if mute else "0"]
            if mute is not None else ["set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{volume}%"])
    try:
        subprocess.run(["wpctl", *args], capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return
    if volume is not None:
        player.say(t("volume", v=volume), 3)


def remote_play(data):
    """Jellyfin's "Play on": a film, an episode or a video started here, where it was left or from where it says."""
    ids = data.get("ItemIds") if isinstance(data.get("ItemIds"), list) else []
    if data.get("PlayCommand", "PlayNow") != "PlayNow" or not ids or not re.fullmatch(r"[\w-]{1,64}", str(ids[0])):
        return
    ticks = data.get("StartPositionTicks")
    at = ticks / TICKS if isinstance(ticks, (int, float)) and ticks >= 0 else None

    def go():
        try:
            item = jf(f"/Items/{ids[0]}?userId={state['user_id']}")
            if item.get("Type") not in REMOTE_PLAYABLE:
                return print(f"Play on Matinee: a {item.get('Type')} is not played here", file=sys.stderr)
            video_player.player.play(item["Id"], True, at)
            broadcast("playing")  # the page follows it, as if it had started it
        except Exception as e:
            print(f"Play on Matinee failed: {e}", file=sys.stderr)
    threading.Thread(target=go, daemon=True).start()


def remote_play_other(text):
    """Matinee Remote's "play this", for what is no Jellyfin item: an album, a book or a podcast's episode of the lists
    Matinee puts there for the phone (remote_shelf.py), by its kind and id."""
    try:
        what = json.loads(text or "")
    except (TypeError, ValueError):
        return
    if not isinstance(what, dict) or what.get("kind") not in ("album", "book", "episode"):
        return
    what = {k: v for k, v in what.items() if k in ("kind", "id", "pos")}
    from . import remote_shelf  # it needs the players and the servers' parts, which load after this one

    def go():
        try:
            plan = remote_shelf.play_plan(what)
            if not plan:
                return print(f"Matinee Remote: {str(what.get('kind'))} {str(what.get('id'))[:64]} is not played here", file=sys.stderr)
            plan()
            broadcast("music")  # the page follows it, as if it had started it
        except Exception as e:
            print(f"Matinee Remote: not played ({e})", file=sys.stderr)
    threading.Thread(target=go, daemon=True).start()


def remote_command(kind, data):
    """A message of Jellyfin's remote control: kind "GeneralCommand", "Playstate" or "Play" with its data."""
    system.note_active()  # someone is there: no "Still watching?" and no switching off
    if kind == "Play":
        return remote_play(data)
    player, music = video_player.player, music_player.music
    playing = player.playing
    if kind == "Playstate":
        cmd = data.get("Command")
        if cmd == "Seek" and isinstance(data.get("SeekPositionTicks"), (int, float)):
            secs = max(0.0, data["SeekPositionTicks"] / TICKS)
            if playing:
                player.remote("seek", secs, "absolute")
            else:
                music.control("seek", secs)
        elif cmd in ("Pause", "Unpause") and not playing:
            music.control("setpause", cmd == "Pause")
        elif cmd in REMOTE_PLAYSTATE:
            mpv_key, page_key = REMOTE_PLAYSTATE[cmd]
            if playing:
                player.remote("keypress", mpv_key)
            elif page_key:
                remote_page({"key": page_key})
        return
    name = data.get("Name")
    args = data.get("Arguments") if isinstance(data.get("Arguments"), dict) else {}
    if name in REMOTE_KEYS:
        mpv_key, page_key = REMOTE_KEYS[name]
        if playing:
            if mpv_key:
                player.remote("keypress", mpv_key)
        elif page_key:
            remote_page({"key": page_key})
    elif name in ("Mute", "Unmute", "ToggleMute"):
        remote_sound(mute="toggle" if name == "ToggleMute" else name == "Mute")
    elif name == "SetVolume" and str(args.get("Volume", "")).isdigit():
        remote_sound(volume=min(100, int(args["Volume"])))
    elif name in ("SetAudioStreamIndex", "SetSubtitleStreamIndex") and playing:  # the monitor: remote_track
        player.remote("script-message", "kino-remote-track", "audio" if name == "SetAudioStreamIndex" else "sub",
                      str(args.get("Index", "")))
    elif name == "SendString" and isinstance(args.get("String"), str) and not playing:
        remote_page({"text": args["String"][:500]})
    elif name == "DisplayMessage" and args.get("Header") == REMOTE_PLAY_HEADER:
        if same_user(data):
            remote_play_other(args.get("Text"))
    elif name == "DisplayMessage":
        text = " – ".join(str(args.get(k) or "").strip() for k in ("Header", "Text") if str(args.get(k) or "").strip())
        ms = int(args["TimeoutMs"]) if str(args.get("TimeoutMs", "")).isdigit() else 5000
        ms = max(2000, min(ms, 30000))
        if text and playing:
            player.say(text[:300], ms / 1000)
        elif text:
            remote_page({"toast": text[:300], "ms": ms})


def ws_loop():
    delay, first = min(2, RETRY_MAX), True
    while True:
        ready.wait()
        try:
            epoch = server_epoch
            url = core.SERVER.replace("http", "ws", 1) + f"/socket?deviceId={state['device_id']}"
            ws = WebSocket(url, [("Authorization", auth_header())])  # Jellyfin 12 rejects api_key here
            if not first:
                broadcast("changed")  # something may have changed while disconnected
            first, delay = False, min(2, RETRY_MAX)
            try:  # what this session can be sent: Jellyfin's remote control, and another Matinee asking it to go on
                jf("/Sessions/Capabilities/Full", "POST", {"PlayableMediaTypes": ["Video", "Audio"],
                                                           "SupportedCommands": REMOTE_COMMANDS, "SupportsMediaControl": True})
            except Exception as e:
                print(f"Capabilities not told to Jellyfin: {e}", file=sys.stderr)
            keepalive, last = 30, time.monotonic()
            while True:
                if epoch != server_epoch:  # signed in to another server: the old one's news are not ours
                    ws.close()
                    raise ConnectionResetError("switched to another server")
                if time.monotonic() - last > keepalive / 2:
                    ws.send(json.dumps({"MessageType": "KeepAlive"}))
                    last = time.monotonic()
                msg = ws.recv(timeout=5)
                if msg is None:
                    continue
                m = json.loads(msg)
                kind = m.get("MessageType")
                if kind == "ForceKeepAlive":
                    keepalive = max(10, int(m.get("Data") or 60))
                elif kind in ("UserDataChanged", "LibraryChanged"):
                    broadcast("changed")
                elif kind in ("GeneralCommand", "Playstate", "Play") and isinstance(m.get("Data"), dict):
                    args = m["Data"].get("Arguments") if isinstance(m["Data"].get("Arguments"), dict) else {}
                    if kind == "GeneralCommand" and m["Data"].get("Name") == "DisplayMessage" \
                            and args.get("Header") in (HANDOFF_HEADER, HANDOFF_ACK_HEADER):
                        handoff_message(m["Data"])  # another Matinee of this user asking this one to go on
                    else:
                        try:
                            remote_command(kind, m["Data"])
                        except Exception as e:  # a command it could not do must not lose the connection
                            print(f"Remote control: {kind} not done ({e})", file=sys.stderr)
                if kind == "LibraryChanged":
                    similar_wanted.put(None)  # new titles get their similar ones
                    badges_cache.clear()  # an episode added or gone: counted again
        except Exception as e:
            log_net(f"Live connection to Jellyfin lost: {e}", e)
        time.sleep(delay)
        delay = min(delay * 2, RETRY_MAX * 2)


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import forecast, video_player, music_player, system, display  # noqa: E402,F401
