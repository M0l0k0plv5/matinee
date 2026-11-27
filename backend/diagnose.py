"""Settings → System → Status: how this device is doing at a glance, for oneself and for whoever helps on the phone.
Each line a check: the servers (asked all at once, none holding up the page for long), the nightly update, the disk,
the clock, the last playback. Asked only when the page is opened."""
import shutil
import subprocess
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from . import services
from .core import conf, VERSION
from .jellyfin_api import jf
from .system import time_synced
from .video_player import player

CHECK_WAIT = 4.0  # seconds for all the servers together: one that does not answer is said to, not waited for
STARTED = time.time()
DISK_LOW = 2 * 1024 ** 3  # bytes free under which the disk is said to be nearly full
NIGHTLY_UNIT = "kino-update.service"


def line(k, state, say="", v="", **vars):
    """One line: its name (diag_<k> in web/i18n.js), how it is ("ok", "warn", "bad" or "info"), and what it says:
    one of the page's words (say, with vars) or a value as it is (v)."""
    return {"k": k, "state": state, "say": say, "v": str(v), "vars": vars}


def host(url):
    return urllib.parse.urlsplit(url or "").netloc


def jellyfin_line():
    server = (conf.get("server") or "").rstrip("/")
    if not server:
        return line("jellyfin", "warn", "diagNotSetUp")
    try:
        version = jf("/System/Info/Public").get("Version") or ""
    except Exception:
        return line("jellyfin", "bad", "diagNoAnswer", host=host(server))
    return line("jellyfin", "ok", "diagAnswers", host=host(server), version=version)


def service_line(k, session):
    def check():
        s = session()
        if not s.get("configured"):
            return None
        return line(k, "ok", "diagSignedIn") if s.get("ready") else line(k, "bad", "diagServiceFailed")
    check.__name__ = f"{k}_line"  # named so if it takes too long
    return check


def disk_line():
    try:
        free = shutil.disk_usage(Path.home()).free
    except OSError:
        return None
    return line("disk", "warn" if free < DISK_LOW else "ok", "diagFree", gb=f"{free / 1024 ** 3:.1f}")


def clock_line():
    synced = time_synced()
    if synced is None:
        return None
    return line("clock", "ok" if synced else "warn", "timeSynced" if synced else "timeNotSynced")


def nightly_line():
    """The nightly update (kino-update.timer, if installed): when it ran last and how that ended, from systemd."""
    try:
        r = subprocess.run(["systemctl", "show", NIGHTLY_UNIT, "-p", "LoadState", "-p", "ExecMainExitTimestamp",
                            "-p", "ExecMainStatus", "-p", "Result", "--timestamp=unix"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None  # no systemd (a Mac)
    props = dict(p.split("=", 1) for p in r.stdout.splitlines() if "=" in p)
    if props.get("LoadState") != "loaded":
        return line("nightly", "info", "diagNightlyOff") if r.returncode == 0 and props else None
    at = props.get("ExecMainExitTimestamp", "").lstrip("@")
    if not at.isdigit() or at == "0":
        return line("nightly", "info", "diagNightlyNotYet")
    ok = props.get("ExecMainStatus") == "0" and props.get("Result", "success") == "success"
    return line("nightly", "ok" if ok else "warn", "diagNightlyRan" if ok else "diagNightlyFailed", at=int(at))


def playback_line():
    if not player.error:
        return line("playback", "ok", "diagNoError")
    return line("playback", "warn", "diagPlayError", why=player.error)




def status_lines():
    """Every check as a line, in the page's order; one that does not apply (no systemd, no Navidrome) left out."""
    checks = [jellyfin_line, service_line("navidrome", services.nd_session),
              service_line("audiobookshelf", services.abs_session), service_line("podfetch", services.pf_session),
              playback_line, nightly_line, disk_line, clock_line]
    pool = ThreadPoolExecutor(len(checks), thread_name_prefix="diagnose")
    futures = [pool.submit(c) for c in checks]
    wait(futures, timeout=CHECK_WAIT)
    pool.shutdown(wait=False)  # one still asking finishes by itself
    lines = [line("version", "info", v=VERSION), line("uptime", "info", "diagSince", at=int(STARTED))]
    for c, f in zip(checks, futures):
        if not f.done():
            k = getattr(c, "__name__", "").removesuffix("_line")
            lines.append(line(k, "bad", "diagNoAnswer", host=""))
            continue
        try:
            got = f.result()
        except Exception:
            continue
        if got:
            lines.append(got)
    return lines
