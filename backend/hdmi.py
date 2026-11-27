"""The TV's other inputs through HDMI-CEC: a tile on the main menu switches the TV to a streaming stick (or a console,
a player) at another HDMI port, and a key on Matinee's remote switches it back. Through the kernel's CEC framework
(/dev/cec*, with cec-ctl from v4l-utils): a Pulse-Eight USB adapter, a DisplayPort to HDMI adapter that passes CEC
on, or a computer with CEC built in. Most graphics chips do not do CEC on their HDMI ports by themselves."""
import glob
import os
import re
import shutil
import subprocess

CEC_CTL = os.environ.get("KINO_CEC_CTL", "cec-ctl")  # the tests: a fake
CEC_DEV = os.environ.get("KINO_CEC_DEV", "")  # the adapter, else the first there is
HDMI_PORTS = range(1, 10)  # the TV's HDMI inputs, as written beside them
HDMI_INPUTS_MAX = 6
HDMI_NAME_MAX = 30
CEC_PA = re.compile(r"Physical Address\s*:\s*([0-9a-f]\.[0-9a-f]\.[0-9a-f]\.[0-9a-f])")
CEC_LA = re.compile(r"Logical Addresses\s*:\s*(\d+)")


def cec_device():
    if CEC_DEV:
        return CEC_DEV if os.path.exists(CEC_DEV) else None
    return next(iter(sorted(glob.glob("/dev/cec[0-9]*"))), None)


def cec_there():
    """Whether Matinee can switch the TV's input here: cec-ctl and an adapter."""
    return bool(shutil.which(CEC_CTL) and cec_device())


def cec_ctl(*args):
    """cec-ctl on the adapter: its output, and whether it went well (a message a TV did not take: False)."""
    dev = cec_device()
    if not dev:
        return "", False
    try:
        r = subprocess.run([CEC_CTL, "-d", dev, *args], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    return r.stdout + r.stderr, not r.returncode


def cec_own_address():
    """Matinee's own place on the HDMI bus ("1.0.0.0": the TV's input 1), the adapter set up as a player first if it is
    not yet (that stays until it is unplugged). None without a TV answering on it."""
    out, ok = cec_ctl()
    if ok and (m := CEC_LA.search(out)) and m.group(1) == "0":
        out, ok = cec_ctl("--playback", "--osd-name", "Matinee")
    pa = CEC_PA.search(out) if ok else None
    return pa.group(1) if pa and pa.group(1) != "f.f.f.f" else None


def hdmi_ok_inputs(inputs):
    """[{"name", "port"}] as kept: a name and a port each, a port once."""
    if not isinstance(inputs, list) or len(inputs) > HDMI_INPUTS_MAX:
        return None
    seen, kept = set(), []
    for x in inputs:
        name = x.get("name") if isinstance(x, dict) else None
        port = x.get("port") if isinstance(x, dict) else None
        if not isinstance(name, str) or not name.strip() or len(name) > HDMI_NAME_MAX or "\n" in name \
                or type(port) is not int or port not in HDMI_PORTS or port in seen:
            return None
        seen.add(port)
        kept.append({"name": name.strip(), "port": port})
    return kept


def hdmi_switch(port):
    """The TV to its HDMI input port. None when it went out, else why not: "hdmi_no_tv", "hdmi_failed"."""
    own = cec_own_address()
    if not own:
        return "hdmi_no_tv"
    if own.startswith(f"{port}."):
        return "hdmi_same_port"  # that is Matinee's own input
    # Set Stream Path, to all: the TV goes to that input (and a device there wakes up and says it is the one shown)
    _, ok = cec_ctl("--to", "15", "--set-stream-path", f"phys-addr={port}.0.0.0")
    return None if ok else "hdmi_failed"


def hdmi_back():
    """The TV back to Matinee: woken if it is asleep, then Matinee the source shown."""
    own = cec_own_address()
    if not own:
        return "hdmi_no_tv"
    cec_ctl("--to", "0", "--image-view-on")
    _, ok = cec_ctl("--to", "15", "--active-source", f"phys-addr={own}")
    return None if ok else "hdmi_failed"
