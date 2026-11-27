"""Software updates, with a probation for each new version: a checkout from its origin (git), a device installed from
packages from the newest package (release.py)."""
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import core, release, services, system
from .core import conf, ROOT, VERSION
from .sound import bt_devices
from .video_player import player
from .forecast import weather
from .music_player import music
from .system import end_chromium, tailscale_info


# Software update (under kino --session only, see the settings' System): Matinee's checkout pulls the newest version from where
# it was cloned (like kino --update) and starts again; "autoupdate" does it at night, when nothing plays and nobody
# pressed a key for half an hour
REPO = Path(os.environ.get("KINO_REPO", ROOT))  # the tests use a clone of their own
# installed only under kino --session (systemd starts it again); a desktop with "thin_client" may look for one, to
# try it, but is a working copy: updated with git there
UPDATES = os.environ.get("KINO_SESSION") == "1"
# a desktop (Matinee.app on a Mac, kino --window on a laptop) installs them too, as long as nothing would be lost: a
# checkout without changes of its own or commits not pushed, so a developer's working copy stays as it is
# (KINO_DESKTOP_UPDATES: the tests')
DESKTOP_UPDATES = not UPDATES and os.environ.get("KINO_DESKTOP_UPDATES", "1") == "1"
# installed from packages, not a checkout: updated by turning the link to a newer package (release.py)
RELEASE = release.is_release(REPO)
_manifest_seen = {}  # the last manifest read: {"m"} (how far behind the other devices are, the newest commit)


def git(*args, timeout=60):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def checkout_clean():
    """Nothing in the checkout an update could touch: no changes of its own, no commits that are not pushed (but those
    the server has rewritten: rewritten_upstream). A package's version has nothing of its own."""
    if RELEASE:
        return True
    try:
        dirty = git("status", "--porcelain", "--untracked-files=no", timeout=10)
        ahead = git("rev-list", "--count", "@{u}..HEAD", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if dirty.returncode or dirty.stdout.strip() or ahead.returncode:
        return False
    return ahead.stdout.strip() == "0" or rewritten_upstream()


def rewritten_upstream():
    """Whether every commit here that the server lacks is there rewritten: the same subject and author date, committed
    there later (a history rewritten on the server, a detail taken out of old commits). A pull refuses such a history
    for ever, though nothing here is the checkout's own. Not a commit amended here: that one is the later."""
    try:
        here = git("log", "--format=%at%x09%ct%x09%s", "@{u}..HEAD", timeout=10)
        there = git("log", "--format=%at%x09%ct%x09%s", "HEAD..@{u}", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if here.returncode or there.returncode:
        return False
    lines = lambda out: [line.split("\t", 2) for line in out.splitlines() if line.count("\t") >= 2]
    rewritten = {}
    for authored, committed, subject in lines(there.stdout):
        rewritten[(authored, subject)] = max(int(committed), rewritten.get((authored, subject), 0))
    mine = lines(here.stdout)
    return bool(mine) and all(int(committed) <= rewritten.get((authored, subject), -1)
                              for authored, committed, subject in mine)


def can_install():
    return UPDATES or (DESKTOP_UPDATES and checkout_clean())


UPDATE_CHANGES = 30  # the changes of an update listed, newest first
_update_seen = {}  # the last update_check(): {"at", "r"}
UPDATE_RECENT = 12  # the patch notes: the last changes installed, newest first
_recent_seen = {}  # recent_changes() for the version it was read for: {"head", "r"}


# A change's own words for the patch notes: its subject, in English; a trailer "Note-de: …" in its message says it in
# German, for a Matinee in German (a change without one shows its subject there too).
CHANGE_FORMAT = "--format=%ct%x09%s%x09%(trailers:key=Note-de,valueonly=true,unfold=true,separator=%x20)"


def change_lines(out):
    """[{"at", "s", "de"}] of git log's lines in CHANGE_FORMAT."""
    r = []
    for line in out.splitlines():
        when, _, rest = line.partition("\t")
        subject, _, de = rest.partition("\t")
        if when.isdigit():
            r.append({"at": int(when), "s": subject, "de": de.strip()})
    return r


def said_here(change):
    """{"at", "s"}: the change in Matinee's language."""
    return {"at": change["at"], "s": core.LANG == "de" and change.get("de") or change["s"]}


def update_said(r):
    """An update_check() as shown: its changes in Matinee's language now (switched since the look, too)."""
    return r and {**r, "changes": [said_here(c) for c in r.get("changes", [])]}


def recent_changes():
    """[{"at", "s"}] of the last changes in the version installed, newest first, from the checkout itself (no network);
    [] without one. Read again only when the version changed."""
    if RELEASE:
        return [said_here(c) for c in release.installed(REPO).get("changes", [])
                if isinstance(c, dict) and isinstance(c.get("s"), str) and isinstance(c.get("at"), int)][:UPDATE_RECENT]
    head = git_head()
    if not head:
        return []
    if _recent_seen.get("head") != head:
        try:
            out = git("log", f"-n{UPDATE_RECENT}", "--no-merges", CHANGE_FORMAT, "HEAD", timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        _recent_seen.update(head=head, r=change_lines(out))
    return [said_here(c) for c in _recent_seen["r"]]


def update_check():
    """{"behind": how many commits the checkout is behind, "version", "date" and "at" (its time) of the newest,
    "installed": when the version installed was made, "changes": [{"at", "s": what it changes}] of those not installed,
    "checked": when looked} or {"error":…, "checked"}. Kept for the settings and the main menu (_update_seen)."""
    r = _update_check()
    r["checked"] = int(time.time())
    _update_seen.update(at=time.time(), r=r)
    return r


def _update_check():
    if RELEASE:
        try:
            m = release.manifest()
        except release.ReleaseError as e:
            print(f"Update: the newest version cannot be had ({e})", file=sys.stderr)
            return {"error": "fetch"}
        _manifest_seen["m"] = m
        r = release.check(REPO, m)
        r.pop("newest")
        r["version"] = VERSION
        return {**r, "behind": 0, "changes": []} if probation_file("bad") == m["commit"] else r
    try:
        if git("fetch", "--quiet").returncode:
            return {"error": "fetch"}
        behind = git("rev-list", "--count", "HEAD..@{u}")
        newest = git("log", "-1", "--format=%cs%x09%ct", "@{u}")
        installed = git("log", "-1", "--format=%ct", "HEAD")
        ahead = git("log", f"-n{UPDATE_CHANGES}", CHANGE_FORMAT, "HEAD..@{u}")
    except (OSError, subprocess.TimeoutExpired):
        return {"error": "fetch"}
    if behind.returncode:
        return {"error": "fetch"}
    date, _, at = newest.stdout.strip().partition("\t")
    r = {"behind": int(behind.stdout.strip() or 0), "version": VERSION, "date": date, "at": int(at or 0) or None,
         "installed": int(installed.stdout.strip() or 0) or None, "changes": []}
    if probation_file("bad") == git_head("@{u}"):  # one that did not come up: not offered until a newer one comes
        return {**r, "behind": 0}
    r["changes"] = change_lines(ahead.stdout)  # in both languages: told in the one of the moment (update_said)
    return r


# An update on probation (on the TV computer): before Matinee starts again on a new version, .git/kino-update keeps the
# version before ("from") and the new one ("to"). The new one passes once its page has said twice that it is there
# (in 30 seconds: it came up and runs). Each start of it until then is counted by kino --session; after
# KINO_PROBATION_TRIES of them it goes back to "from" by itself and keeps the new one as "bad", not installed again
# until a newer one comes. The nightly update (deploy/nightly-update) and the one from the settings both do this.
PROBATION = (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "kino" / "update" if RELEASE
             else REPO / ".git" / "kino-update")  # a package's: outside its version (kino --session uses the same)
PROBATION_PINGS = 2
_probation = {"to": None, "pings": 0}


def probation_file(name):
    try:
        return (PROBATION / name).read_text().strip() or None
    except OSError:
        return None


def git_head(ref="HEAD"):
    """The commit of the version in use ("HEAD") or of the newest ("@{u}", for a package: of the last manifest read)."""
    if RELEASE:
        if ref == "HEAD":
            return release.head(release.link_of(REPO))
        return _manifest_seen.get("m", {}).get("commit")
    try:
        r = git("rev-parse", ref, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if not r.returncode else None


def start_probation(before, after):
    try:
        PROBATION.mkdir(parents=True, exist_ok=True)
        if not probation_file("to"):  # a version still on probation keeps the last good one as where to go back to
            (PROBATION / "from").write_text(before + "\n")
        (PROBATION / "to").write_text(after + "\n")
        (PROBATION / "tries").unlink(missing_ok=True)
    except OSError as e:
        print(f"Update: probation not kept ({e})", file=sys.stderr)


def check_probation():
    """At the start: the version on probation, if it is the one running (else it is over)."""
    to = probation_file("to") if UPDATES else None
    if to and git_head() == to:
        _probation["to"] = to
        print(f"Update to {to[:9]} on probation", file=sys.stderr)
    elif to:
        end_probation()


def end_probation():
    for name in ("from", "to", "tries"):
        (PROBATION / name).unlink(missing_ok=True)


def probation_ping():
    """The page said it is there: a version on probation has passed after PROBATION_PINGS of these."""
    if not _probation["to"]:
        return
    _probation["pings"] += 1
    if _probation["pings"] >= PROBATION_PINGS:
        print(f"Update to {_probation['to'][:9]} works: probation over", file=sys.stderr)
        _probation["to"] = None
        try:
            end_probation()
            (PROBATION / "bad").unlink(missing_ok=True)
        except OSError:
            pass


UPDATE_CHECK_SECONDS = 1800


class KeepRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # the redirect itself is the answer (an HTTPError then)


NO_REDIRECT = urllib.request.build_opener(KeepRedirect)
REACH_TRIES = 2
REACH_TIMEOUT = float(os.environ.get("KINO_REACH_TIMEOUT", "4"))


def overview():
    """The main menu's look at the device, above its settings: {"servers": [{name, ok}], "bluetooth": the devices
    connected (None without Bluetooth Matinee can use), "tailscale": tailscale_info() (None without it), "weather",
    "update": update_check() where Matinee updates itself (asked once in half an hour), else None, "version"}."""
    def reachable(url):
        # any answer will do, a redirect too (not followed: one more connection that may be slow); a moment the line
        # is busy (a film loading) is tried once more, so a server that is there is not shown as gone
        for attempt in range(REACH_TRIES):
            try:
                with NO_REDIRECT.open(url, timeout=REACH_TIMEOUT):
                    return True
            except urllib.error.HTTPError:
                return True  # it answers, if not to this
            except (OSError, ValueError):
                continue
        return False
    servers = [("Jellyfin", core.SERVER), ("Navidrome", services.ND_SERVER), ("Audiobookshelf", services.ABS_SERVER), ("Podfetch", services.PF_SERVER)]
    servers = [(name, url) for name, url in servers if url]

    def update():
        if not (UPDATES or DESKTOP_UPDATES):
            return None
        if update_look_on() and (time.time() - _update_seen.get("at", 0) > UPDATE_CHECK_SECONDS or "error" in _update_seen.get("r", {})):
            update_check()
        return update_said(_update_seen.get("r"))
    with ThreadPoolExecutor(len(servers) + 4) as pool:
        ok = [pool.submit(reachable, f"{url}/") for _, url in servers]
        bt, ts, wx, up = pool.submit(bt_devices), pool.submit(tailscale_info), pool.submit(weather), pool.submit(update)
        bt = bt.result()
        return {"servers": [{"name": name, "ok": f.result()} for (name, _), f in zip(servers, ok)],
                "bluetooth": [d for d in bt["devices"] if d["connected"]] if bt["available"] else None,
                "tailscale": ts.result(), "weather": wx.result(), "update": up.result(), "version": VERSION}


def update_install():
    """Pull the newest version and start Matinee again; an error code if it cannot be had (nothing changed then).
    On the TV computer the checkout is set to the newest version whatever happened (a history rewritten, which a
    pull would refuse every night from then on), as deploy/nightly-update does, and the new version is on probation;
    a Mac's working copy is only fast-forwarded."""
    before = git_head()
    if RELEASE:
        try:
            m = release.manifest()
            _manifest_seen["m"] = m
            if probation_file("bad") == m["commit"]:
                return "pull"
            before, after = release.install(release.link_of(REPO), m)
        except (release.ReleaseError, OSError) as e:
            print(f"Update failed: {e}", file=sys.stderr)
            return "pull"
        if UPDATES and before and after != before:
            start_probation(before, after)
        print("Updated: starting Matinee again", file=sys.stderr)
        restart_kino()
        return None
    try:
        if UPDATES:
            r = git("fetch", "--quiet")
            if not r.returncode:
                if probation_file("bad") == git_head("@{u}"):
                    return "pull"
                r = git("reset", "--quiet", "--hard", "@{u}")
        else:
            r = git("pull", "--ff-only", "--quiet")
            if r.returncode and rewritten_upstream():  # the server's history rewritten: followed, nothing here is lost
                r = git("reset", "--quiet", "--hard", "@{u}")
    except (OSError, subprocess.TimeoutExpired):
        return "pull"
    if r.returncode:
        print(f"Update failed: {r.stderr.strip()[-300:]}", file=sys.stderr)
        return "pull"
    after = git_head()
    if UPDATES and before and after and after != before:
        start_probation(before, after)
    print("Updated: starting Matinee again", file=sys.stderr)
    restart_kino()
    return None


def commits_behind(commit):
    """How many versions a commit is behind the newest (the devices' overview); None when not known."""
    if RELEASE:
        m = _manifest_seen.get("m")
        return release.behind(m, commit) if m else None
    try:
        r = git("rev-list", "--count", f"{commit}..@{{u}}", timeout=5)
        return int(r.stdout.strip()) if not r.returncode else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def restart_kino():
    """Start Matinee again in a moment (after an update, with another account's things): everything read anew."""
    def again():
        time.sleep(2)  # the answer reaches the UI first
        # what plays ends first: the backend after it would neither see nor stop it (music, an audiobook)
        system.end_players(wait=3)
        if not UPDATES:  # a Mac: the backend starts again in its place (same process, so Matinee.app keeps it);
            os.execv(sys.executable, [sys.executable, *sys.argv])  # the page reloads once it answers
        end_chromium()
        os._exit(0)
    threading.Thread(target=again, daemon=True).start()


# "autoupdate": the TV computer at night, when nothing plays and nobody pressed a key for half an hour (off unless
# chosen: the nightly timer of deploy/ does it there as well); a desktop, which is often off at night, whenever Matinee
# was not used for AUTOUPDATE_QUIET and nothing plays (on unless chosen otherwise), and never over changes of its own
AUTOUPDATE_EVERY = float(os.environ.get("KINO_AUTOUPDATE_EVERY", "300"))
AUTOUPDATE_QUIET = float(os.environ.get("KINO_AUTOUPDATE_QUIET", "600"))
# nothing new found: looked again only after so long (a desktop idle all night fetched every 5 minutes otherwise)
AUTOUPDATE_LOOK = float(os.environ.get("KINO_AUTOUPDATE_LOOK", "3600"))


def autoupdate_on():
    return conf.get("autoupdate", "on" if DESKTOP_UPDATES else "off") == "on" and update_look_on()


def update_look_on():
    """Whether Matinee looks for updates by itself ("updatecheck": the main menu now and then, at night); "manual": only
    when asked in the settings, so nothing goes out to GitHub otherwise (no security updates either, then)."""
    return conf.get("updatecheck", "auto") != "manual"


def autoupdate_loop():
    look_at = 0.0  # when the remote is looked at again (AUTOUPDATE_LOOK)
    while True:
        time.sleep(AUTOUPDATE_EVERY)
        idle = time.monotonic() - system.last_active
        if not autoupdate_on() or player.playing or music.active:
            continue
        if UPDATES:
            due = idle > 1800 and 3 <= datetime.now().hour < 5
        else:
            due = DESKTOP_UPDATES and idle > AUTOUPDATE_QUIET and checkout_clean()
        if not due or time.monotonic() < look_at:
            continue
        if update_check().get("behind"):
            update_install()
        else:
            look_at = time.monotonic() + AUTOUPDATE_LOOK


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import account  # noqa: E402,F401
