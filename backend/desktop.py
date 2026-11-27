"""On a desktop: focusing windows in niri, the desktop's scroll direction, mpv-mpris."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .core import conf


def niri_focus(match, timeout=5, given_up=lambda: False):
    """On a niri desktop, focus the first window for which match(window) is true (cage needs none of this); that
    window, or None."""
    if not os.environ.get("NIRI_SOCKET"):
        return
    end = time.monotonic() + timeout
    while time.monotonic() < end and not given_up():
        try:
            out = subprocess.run(["niri", "msg", "--json", "windows"], capture_output=True, text=True, timeout=2).stdout
            win = next((w for w in json.loads(out) if match(w)), None)
            if win:
                subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(win["id"])], capture_output=True, timeout=2)
                return win
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):  # niri not answering in time, too
            return
        time.sleep(0.1)


def focus_player(proc):
    # a channel live only gets its window once its stream has been found, which can take a while
    found = niri_focus(lambda w: w.get("pid") == proc.pid, timeout=60, given_up=lambda: proc.poll() is not None)
    if found and proc.poll() is None:
        park_ui()


def focus_ui():
    unpark_ui()
    niri_focus(is_kino, timeout=1)


# While a video plays on a niri desktop, Matinee's own window (black behind the player) waits on an empty workspace of
# its screen, so the windows next to the player are the desktop's (a terminal), not it; back where it was at the end.
# niri has no minimizing. Not in cage, where Matinee is all there is.
is_kino = lambda w: w.get("title") == "Matinee" and (w.get("app_id") or "").startswith("chrome-")
parked = {}  # "window", "workspace": Matinee's window and the workspace it came from, while it waits


def niri_json(what):
    out = subprocess.run(["niri", "msg", "--json", what], capture_output=True, text=True, timeout=2).stdout
    return json.loads(out)


def park_ui():
    if not os.environ.get("NIRI_SOCKET") or parked:
        return
    try:
        win = next((w for w in niri_json("windows") if is_kino(w)), None)
        spaces = niri_json("workspaces")
        home = next((x for x in spaces if win and x["id"] == win.get("workspace_id")), None)
        if not win or not home or win.get("is_floating"):
            return
        # niri keeps an empty workspace after the last one of each screen: Matinee goes there, the focus stays
        last = max((x for x in spaces if x.get("output") == home.get("output")), key=lambda x: x["idx"])
        if last["id"] == home["id"]:
            return
        subprocess.run(["niri", "msg", "action", "move-window-to-workspace", "--window-id", str(win["id"]), "--focus", "false",
                        str(last["idx"])], capture_output=True, timeout=2)
        parked.update(window=win["id"], workspace=home["id"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(f"niri: Matinee's window not put aside ({e})", file=sys.stderr)


def unpark_ui():
    if not parked:
        return
    window, workspace = parked.pop("window"), parked.pop("workspace")
    try:
        spaces = niri_json("workspaces")
        home = next((x for x in spaces if x["id"] == workspace), None) or next((x for x in spaces if x.get("is_focused")), None)
        if home:
            subprocess.run(["niri", "msg", "action", "move-window-to-workspace", "--window-id", str(window), "--focus", "false",
                            str(home["idx"])], capture_output=True, timeout=2)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(f"niri: Matinee's window not brought back ({e})", file=sys.stderr)


def mpris_script():
    """mpv-mpris, if installed: the music then shows up as a media player on a desktop, so its media keys (which
    the desktop keeps for itself, e.g. niri with playerctl) and widgets control it."""
    for path in ("/usr/lib/mpv-mpris/mpris.so", "/usr/share/mpv/scripts/mpris.so", "/etc/mpv/scripts/mpris.so"):
        if os.path.exists(path):
            return [f"--script={path}"]
    return []


NATURAL_KEEP = 60  # s: a change in the desktop's settings counts from the next minute
_natural_seen = {}


def _niri_natural():
    """niri's config: natural-scroll in its touchpad block (a laptop's touchpad is what swipes)."""
    try:
        text = (Path.home() / ".config" / "niri" / "config.kdl").read_text()
    except OSError:
        return None
    depth, block = 0, []
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith("touchpad") and line.endswith("{") and depth >= 1:
            block = [depth]
        elif block and line == "natural-scroll" and depth == block[0] + 1:
            return True
        depth += line.count("{") - line.count("}")
        if block and depth <= block[0]:
            block = []
    return False if text else None


def _command_out(*cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def natural_scroll():
    """Whether the desktop scrolls "naturally" (the page follows the fingers: swiped up is the wheel down). Pages and
    lists go as the desktop has it, but for the volume up is louder however the fingers swipe: so it is turned round
    there. Asked of macOS, niri, GNOME and KDE; KINO_NATURAL_SCROLL (1 or 0) or "natural_scroll" in the config
    decide instead. The TV (cage, a remote) has none."""
    forced = os.environ.get("KINO_NATURAL_SCROLL") or conf.get("natural_scroll")
    if forced is not None and forced != "":
        return forced in (True, "1", "true", "on")
    if display.IN_CAGE:
        return False
    seen = _natural_seen.get("v")
    if seen and time.time() - seen[0] < NATURAL_KEEP:
        return seen[1]
    if sys.platform == "darwin":  # unset is macOS's default: natural
        natural = _command_out("defaults", "read", "-g", "com.apple.swipescrolldirection") != "0"
    else:
        natural = _niri_natural() if os.environ.get("NIRI_SOCKET") else None
        if natural is None:
            gnome = _command_out("gsettings", "get", "org.gnome.desktop.peripherals.touchpad", "natural-scroll")
            natural = gnome == "true" if gnome in ("true", "false") else None
        if natural is None:
            try:
                kde = (Path.home() / ".config" / "kcminputrc").read_text()
                natural = re.search(r"(?m)^NaturalScroll=true\s*$", kde) is not None
            except OSError:
                natural = _niri_natural() or False
    _natural_seen["v"] = (time.time(), natural)
    return natural


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import sound, display  # noqa: E402,F401
