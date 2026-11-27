"""Continue on another Matinee of the same Jellyfin user: what plays handed over by a message through Jellyfin, the
other saying it goes on (or it going on here again); what another Matinee hands over, checked and started here."""
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse

from .core import state, t
from .jellyfin_api import jf


HANDOFF_HEADER = "kino-handoff"
# Jellyfin takes a message for a session whether anything is there to read it or not (a laptop closed a few minutes
# ago is still among its sessions): the device going on says so (HANDOFF_ACK_HEADER, with the message's own "ack");
# none in so long, it goes on here instead, from where it was
HANDOFF_ACK_HEADER = "kino-handoff-ack"
HANDOFF_ACK_WAIT = float(os.environ.get("KINO_HANDOFF_ACK_WAIT", "6"))
HANDOFF_SKEW = 3  # seconds two devices' clocks may be apart (both set by the network: mostly far less)
handoff_acks = {}  # ack → threading.Event, set when the device going on said so
handoff_acks_lock = threading.Lock()


def handoff_targets():
    """The other Matinees of this Jellyfin user that are on now: connected to Jellyfin over the WebSocket ("IsActive").
    Not by when a session was last active: the WebSocket's KeepAlive does not count for that, so a Matinee left on
    without anything played for a quarter of an hour was not offered."""
    try:
        sessions = jf("/Sessions") or []
    except Exception as e:
        print(f"Devices to go on on not found: {e}", file=sys.stderr)
        return []
    out, seen = [], set()
    for x in sessions:
        did = x.get("DeviceId")
        if x.get("Client") != "Matinee" or not did or did == state["device_id"] or did in seen or not x.get("IsActive"):
            continue
        if x.get("UserId") and state.get("user_id") and x["UserId"].replace("-", "") != state["user_id"].replace("-", ""):
            continue
        seen.add(did)
        out.append({"id": did, "name": x.get("DeviceName") or did, "session": x.get("Id")})
    return out


def send_handoff(device_id, what):
    """Tell another Matinee (by its device id) to go on with what played here; it not saying it does (or not there, or
    the message not taken), going on here again."""
    target = next((d for d in handoff_targets() if d["id"] == device_id), None)
    name = target["name"] if target else device_id
    ack = secrets.token_hex(8)
    told = threading.Event()
    with handoff_acks_lock:
        handoff_acks[ack] = told
    try:
        if not target or not target["session"]:
            print(f"Going on on {device_id}: not on", file=sys.stderr)
        else:
            try:
                # until: after that it goes on here (a device that takes it later, its Matinee stalled, leaves it)
                jf(f"/Sessions/{urllib.parse.quote(target['session'])}/Message", "POST",
                   {"Header": HANDOFF_HEADER, "Text": json.dumps({**what, "from": state["device_id"], "ack": ack,
                                                                  "until": time.time() + HANDOFF_ACK_WAIT}),
                    "TimeoutMs": 1000})
            except Exception as e:
                print(f"Going on on {name} failed: {e}", file=sys.stderr)
            else:
                if told.wait(HANDOFF_ACK_WAIT):
                    print(f"Going on on {name}", file=sys.stderr)
                    return
                print(f"Going on on {name}: no answer in {HANDOFF_ACK_WAIT:g} s", file=sys.stderr)
    finally:
        with handoff_acks_lock:
            handoff_acks.pop(ack, None)
    take_handoff(what)  # here again, from where it was kept
    if what.get("kind") == "music":  # no player window to say it in: the page does (a toast)
        live_events.broadcast(f"handoffback:{name[:100]}")
        return
    end = time.monotonic() + 30
    while not video_player.player.playing and time.monotonic() < end:
        time.sleep(0.2)
    video_player.player.say(t("handoff_back", name=name), 5)


def answer_handoff(what):
    """Tell the Matinee that asked to go on here that it does (send_handoff waits for that). True if it is one of this
    user's Matinees that are on: only then is what it asks done."""
    sender, ack = what.get("from"), what.get("ack")
    if not isinstance(sender, str) or not isinstance(ack, str):
        print("Going on here: not asked by a Matinee", file=sys.stderr)
        return False
    target = next((d for d in handoff_targets() if d["id"] == sender), None)
    if not target or not target["session"]:
        print(f"Going on here: {sender[:64]} is not a Matinee of this account", file=sys.stderr)
        return False
    jf(f"/Sessions/{urllib.parse.quote(target['session'])}/Message", "POST",
       {"Header": HANDOFF_ACK_HEADER, "Text": ack[:64], "TimeoutMs": 1000})
    return True


def handoff_plan(what):
    """What another Matinee of this user asks to go on with here, as what starts it (no argument), or None if this Matinee
    cannot: asked before it says it goes on (answer_handoff), so the other goes on itself rather than nowhere. Only
    what Matinee itself would play: an item's id, one of its own channels live, songs of its own Navidrome."""
    if not isinstance(what, dict):
        return None
    kind = what.get("kind")
    pos = what.get("pos") if isinstance(what.get("pos"), (int, float)) and math.isfinite(what["pos"]) else None
    at = max(0.0, pos) if pos is not None else None  # where it starts here
    if kind == "jf" and re.fullmatch(r"[\w-]{1,64}", str(what.get("id", ""))):
        return lambda: video_player.player.play(what["id"], True, at)
    if kind == "tv" and what.get("live"):
        try:
            live = tv.tv_live_channel(str(what["live"]))
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"Going on here: channel {str(what['live'])[:64]} unknown ({e})", file=sys.stderr)
            return None
        if not live:
            return None

        def play_live():  # what is on now asked only then, and waited for a moment at most: the sender has stopped
            found = {}

            def ask():
                try:
                    found.update(tv.tv_now())
                except Exception as e:  # the guides cannot be had: the channel's name does as the title
                    print(f"Going on here: no guide ({e})", file=sys.stderr)
            asking = threading.Thread(target=ask, daemon=True)
            asking.start()
            asking.join(tv.LIVE_TITLE_WAIT)
            on_now = dict(found).get(live[0])
            video_player.player.play_tv({"id": f"live-{live[0]}", "channel": live[1], "url": live[2], "live": live[0],
                            "title": f"{live[1]} – {on_now['title']}" if on_now else live[1]})
        return play_live
    if kind == "music":
        songs, index = music_songs(what.get("songs")), what.get("index")
        if not songs or not isinstance(index, int) or not 0 <= index < len(songs):
            return None
        if not (services.navidrome and services.nd_load_creds()):
            print("Going on here: no Navidrome set up for the music", file=sys.stderr)
            return None
        def play_music():
            music_player.music.play(songs, index, at=at or 0.0)  # the song playing goes on where it was
            live_events.broadcast("music")  # the page shows what plays now
        return play_music
    return None


def take_handoff(what):
    """Go on here with what another Matinee of this user asked for (or, it not answering, with what played here)."""
    plan = handoff_plan(what)
    if not plan:
        return
    plan()
    if what.get("kind") != "music":
        live_events.broadcast("playing")  # the page follows it, as if it had started it


MUSIC_SONG_KEYS = ("id", "title", "artist", "album", "albumId", "artistId", "coverArt", "duration", "starred")


def music_songs(songs):
    """Songs of Navidrome as the page or another Matinee gives them: only their ids and what is shown of them; None if
    any is not one."""
    if not isinstance(songs, list) or not 0 < len(songs) <= 1000 or \
            not all(isinstance(x, dict) and re.fullmatch(r"[\w-]+", str(x.get("id", ""))) for x in songs):
        return None
    return [{k: x.get(k) for k in MUSIC_SONG_KEYS} for x in songs]


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import (  # noqa: E402,F401
    video_player, live_events, tv, services, music_player,
)
