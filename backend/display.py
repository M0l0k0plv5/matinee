"""The TV's display mode (resolution and refresh rate) under cage, and whether a screen is connected."""
import json
import os
import subprocess
import sys
from pathlib import Path

from .core import conf
from .preferences import setting


# The display mode, in cage only (kino --session): on a desktop the screen belongs to the desktop.
# "auto": the screen's own mode, unless it is below 50 Hz (a 4K screen on an HDMI 1.4 computer only gets 30):
# then the highest refresh rate at 1080p or more. "native": the screen's own mode. "1920x1080@120": that one.
# "thin_client": true in config.json gives a desktop the same (display mode, restart and shut down): Matinee then
# switches that computer's screen and turns it off
IN_CAGE = os.environ.get("KINO_SESSION") == "1" or conf.get("thin_client") is True
DISPLAY_MIN_HZ = 50


def mode_name(m):
    return f"{m['width']}x{m['height']}@{round(m['refresh'])}"


def native_mode(modes):
    """The screen's own mode: the preferred one, or the biggest at its highest refresh rate."""
    return next((m for m in modes if m.get("preferred")), None) or \
        max(modes, key=lambda m: (m["width"] * m["height"], m["refresh"]), default=None)


def pick_mode(modes, want):
    """The mode for a "display" setting, None if there is no such mode."""
    if want == "native":
        return native_mode(modes)
    if want != "auto":
        size, _, hz = want.partition("@")
        w, _, h = size.partition("x")
        match = [m for m in modes if (m["width"], m["height"]) == (int(w), int(h))
                 and (not hz or abs(m["refresh"] - float(hz)) < 0.5)]
        return max(match, key=lambda m: m["refresh"], default=None)
    native = native_mode(modes)
    if not native or native["refresh"] >= DISPLAY_MIN_HZ - 0.5:
        return native
    big = [m for m in modes if m["height"] >= 1080] or modes
    return max(big, key=lambda m: (round(m["refresh"]), m["width"] * m["height"], m["refresh"]))


def display_outputs():
    """The screens cage shows Matinee on, with their modes (wlr-randr --json); [] without wlr-randr."""
    try:
        out = subprocess.run(["wlr-randr", "--json"], capture_output=True, text=True, timeout=5).stdout
        return [o for o in json.loads(out) if o.get("enabled")]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def display_choices():
    """Modes to offer in the settings, biggest first, one per size and (rounded) refresh rate: 16:9 from 720p
    at 50 Hz or more, and the screen's own mode."""
    outs = display_outputs()
    all_modes = outs[0]["modes"] if outs else []
    native = native_mode(all_modes)
    modes = {mode_name(m): m for m in sorted(all_modes, key=lambda m: m["refresh"])
             if m is native or (m["height"] >= 720 and abs(m["width"] / m["height"] - 16 / 9) < 0.01
                                and m["refresh"] >= DISPLAY_MIN_HZ - 0.5)}
    return sorted(modes, key=lambda n: (-modes[n]["width"] * modes[n]["height"], -modes[n]["refresh"]))


def rate_for(modes, fps):
    """The mode at the current size whose refresh rate is the highest whole multiple of fps (25 fps: 50 Hz), so
    every frame stays on screen equally long; None if none fits, or if the current one is within 0.2 % of one
    (23.976 fps at 120 Hz): mpv evens that out itself (video-sync=display-resample, the sound 0.1 % slower),
    and the screen does not go black for a few seconds while it switches, at the start and again at the end."""
    current = next((m for m in modes if m.get("current")), None)
    if not current or not fps or fps < 10:
        return None

    def fits(m, within):
        k = round(m["refresh"] / fps)
        return k >= 1 and abs(m["refresh"] - k * fps) <= m["refresh"] * within

    if fits(current, 0.002):
        return None
    same = [m for m in modes if (m["width"], m["height"]) == (current["width"], current["height"]) and fits(m, 0.0005)]
    return max(same, key=lambda m: m["refresh"], default=None)


def switch_mode(output, mode):
    """Switch a screen to mode; False if wlr-randr is missing or hangs. Never raises: before a video that would stop
    it from playing, after one the refresh rate of the others would not be put back."""
    spec = f"{mode['width']}x{mode['height']}@{mode['refresh']:.3f}Hz"
    print(f"Display: {output} → {spec}", file=sys.stderr)
    try:
        subprocess.run(["wlr-randr", "--output", output, "--mode", spec], timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Display: {output} not switched ({e})", file=sys.stderr)
        return False
    return True


def match_rate(fps):
    """Before a video: the refresh rate for its frame rate. Returns what to restore afterwards."""
    if not IN_CAGE or setting("framerate") != "on":
        return []
    restore = []
    for out in display_outputs():
        modes = out.get("modes") or []
        mode = rate_for(modes, fps)
        if mode:
            restore.append((out["name"], next(m for m in modes if m.get("current"))))
            switch_mode(out["name"], mode)
    return restore


def apply_display(want):
    """Switch every screen to the mode for want, unless it is in that mode already."""
    for out in display_outputs():
        mode = pick_mode(out.get("modes") or [], want)
        current = next((m for m in out.get("modes") or [] if m.get("current")), None)
        if not mode:
            print(f"Display: {out['name']} has no mode {want}", file=sys.stderr)
        elif not current or mode_name(mode) != mode_name(current):
            switch_mode(out["name"], mode)


def display_back():
    """At the start: every screen in the mode the settings want. A film's refresh rate is put back when it ends; Matinee
    ended while one played (stopped, crashed, updated), it stayed: on a desktop with "thin_client" until the next
    film, where cage's start (display-mode) does not set it."""
    try:
        apply_display(str(conf.get("display", "auto")))  # as display-mode reads it
    except Exception as e:  # any: a start must not fail on the screen
        print(f"Display: mode not put back ({e})", file=sys.stderr)


DRM = Path(os.environ.get("KINO_DRM", "/sys/class/drm"))


def display_connected():
    """A screen at one of the graphics card's outputs (on the thin client: the TV on). True where none are seen."""
    try:
        states = [p.read_text().strip() for p in DRM.glob("card*-*/status")]
    except OSError:
        return True
    return not states or "connected" in states


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import mpv_ipc  # noqa: E402,F401
