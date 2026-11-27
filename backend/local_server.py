"""Jellyfin on the TV computer itself (deploy/server.sh: a container, the media in /srv/kino-media, shared on the
network by Samba): seen from the first start's Jellyfin step, and set up from there without Jellyfin's own web pages
(its startup wizard through its API, the libraries for films and shows, then Matinee signed in to it like any other)."""
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import core
from .jellyfin_api import jf_device_header, setup_jellyfin

LOCAL_JF = os.environ.get("KINO_LOCAL_JELLYFIN", "http://127.0.0.1:8096")
LOCAL_MEDIA = Path(os.environ.get("KINO_MEDIA", "/srv/kino-media"))  # on this computer
LOCAL_MEDIA_IN_SERVER = os.environ.get("KINO_MEDIA_IN_SERVER", "/media")  # the same folder as Jellyfin's container sees it
LOCAL_SHARE_FILE = core.CONF_DIR / "share.json"  # the Samba share, written by deploy/server.sh: {"name", "user", "password"}
# Jellyfin's startup wizard is open to the whole home network until done, and whoever does it first is the server's
# administrator: deploy/server.sh does it at once, with a first user of its own (a password made up there), kept here
# ({"user", "password"}) until the TV makes that user the person's own (local_setup), then removed
LOCAL_ADMIN_FILE = core.CONF_DIR / "local-admin.json"
# in each library's folder: Jellyfin skips an empty one, and does not watch it for new films after (seen with 12.4)
LOCAL_KEEP = ".kino"
LOCAL_KEEP_TEXT = "Matinee keeps this file here so that Jellyfin watches this folder for new films. Leave it here.\n"
LOCAL_FOLDERS = {"de": (("Filme", "movies"), ("Serien", "tvshows")), "en": (("Movies", "movies"), ("Shows", "tvshows"))}


def local_call(path, method="GET", body=None, token=None, timeout=10):
    """Jellyfin on this computer: its answer as JSON (None when empty); raises OSError (HTTPError among them)."""
    headers = {"Authorization": jf_device_header("kino-setup", "Matinee setup", token)}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(LOCAL_JF + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def local_address():
    """This computer's address on the home network (a name does not always resolve there): the one a packet out would
    leave from; nothing is sent for it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname().split(".")[0]


def local_share():
    try:
        d = json.loads(LOCAL_SHARE_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("name", "user", "password")):
        return None
    return {"name": d["name"], "user": d["user"], "password": d["password"], "host": local_address()}


def local_admin():
    """The first user deploy/server.sh made: {"user", "password"}, or None."""
    try:
        d = json.loads(LOCAL_ADMIN_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) and d[k] for k in ("user", "password")):
        return None
    return {"user": d["user"], "password": d["password"]}


def local_state():
    """{"there": Jellyfin answers on this computer, "fresh": its startup wizard not done yet, "share": the Samba share
    or None, "folders": [the folders for films and shows, by name]}"""
    try:
        info = local_call("/System/Info/Public", timeout=2) or {}
    except (OSError, ValueError):
        info = None
    if info is not None and not isinstance(info.get("StartupWizardCompleted"), bool):
        info = None  # Jellyfin 12 still starting: its stand-in answers ("startupWizardCompleted"), not to be gone by
    fresh = bool(info) and (info.get("StartupWizardCompleted") is False or local_admin() is not None)
    return {"there": info is not None, "fresh": fresh,
            "share": local_share(), "folders": [name for name, _ in LOCAL_FOLDERS[core.LANG]]}


def local_setup(body):
    """Jellyfin on this computer set up with the user name and password from the TV (its first user, an
    administrator: the one deploy/server.sh made, renamed, or made here by the startup wizard where that is not done
    yet), its libraries for films and shows made (folders in the media folder, created here), and Matinee signed in to
    it. Raises ValueError with an error code the UI translates."""
    user, password = str(body.get("username") or "").strip(), str(body.get("password") or "")
    if not user or len(user) > 64 or "\n" in user:
        raise ValueError("service_missing_user")
    if len(password) < 4 or len(password) > 200:
        raise ValueError("local_password")  # the first user is the server's administrator: not without a password
    if not local_state()["fresh"]:
        raise ValueError("local_done")
    lang = core.LANG
    # the libraries' folders first: Jellyfin takes none that is not there, and nothing of it is changed before
    for name, _ in LOCAL_FOLDERS[lang]:
        try:
            (LOCAL_MEDIA / name).mkdir(parents=True, exist_ok=True)
            keep = LOCAL_MEDIA / name / LOCAL_KEEP
            if not keep.exists():
                keep.write_text(LOCAL_KEEP_TEXT)
        except OSError as e:
            print(f"Local Jellyfin not set up: {LOCAL_MEDIA / name} not made: {e}", flush=True)
            raise ValueError("local_folders") from None
    try:
        languages = {"UICulture": "de-DE" if lang == "de" else "en-US",
                     "MetadataCountryCode": "DE" if lang == "de" else "US", "PreferredMetadataLanguage": lang}
        # deploy/server.sh's first user, if it got as far as the wizard's end (else the wizard is done here)
        done = (local_call("/System/Info/Public") or {}).get("StartupWizardCompleted") is True
        admin = local_admin() if done else None
        if admin:  # done by deploy/server.sh: its first user becomes the person's
            auth = local_call("/Users/AuthenticateByName", "POST", {"Username": admin["user"], "Pw": admin["password"]})
            token, me = auth["AccessToken"], auth["User"]
            conf = local_call("/System/Configuration", token=token) or {}
            local_call("/System/Configuration", "POST", {**conf, **languages}, token)
        else:  # an older deploy/server.sh: the startup wizard here
            conf = local_call("/Startup/Configuration") or {}
            local_call("/Startup/Configuration", "POST", {**conf, **languages})
            local_call("/Startup/User")  # Jellyfin makes its first user on this
            local_call("/Startup/User", "POST", {"Name": user, "Password": password})
            local_call("/Startup/RemoteAccess", "POST", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
            local_call("/Startup/Complete", "POST", {})
            auth = local_call("/Users/AuthenticateByName", "POST", {"Username": user, "Pw": password})
            token = auth["AccessToken"]
        # those there already left (a setup broken off before, tried again)
        there = {f.get("Name") for f in local_call("/Library/VirtualFolders", token=token) or [] if isinstance(f, dict)}
        for name, kind in LOCAL_FOLDERS[lang]:
            if name in there:
                continue
            q = urllib.parse.urlencode({"name": name, "collectionType": kind, "refreshLibrary": "false",
                                        "paths": f"{LOCAL_MEDIA_IN_SERVER}/{name}"})
            # watched for new files (Jellyfin leaves that off for a library made through its API), and its titles'
            # descriptions in Matinee's language
            local_call(f"/Library/VirtualFolders?{q}", "POST", {"LibraryOptions": {
                "EnableRealtimeMonitor": True, "PreferredMetadataLanguage": lang,
                "MetadataCountryCode": "DE" if lang == "de" else "US"}}, token)
        # one scan of them all: Jellyfin starts watching a library's folders only after one (not on making it), and
        # only those not empty (LOCAL_KEEP)
        local_call("/Library/Refresh", "POST", {}, token)
        if admin:  # last: until then, a setup broken off can be tried again with the first user as it was
            uid = urllib.parse.quote(me["Id"])
            # each step written down at once, so a setup broken off between them is tried again with the user as it
            # is then (its new password, then its new name), not with what is no longer so
            local_call(f"/Users/Password?userId={uid}", "POST", {"CurrentPw": admin["password"], "NewPw": password}, token)
            core.write_private(LOCAL_ADMIN_FILE, json.dumps({"user": me.get("Name") or admin["user"], "password": password}))
            if user != me.get("Name"):
                local_call(f"/Users?userId={uid}", "POST", {**me, "Name": user}, token)
                core.write_private(LOCAL_ADMIN_FILE, json.dumps({"user": user, "password": password}))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Local Jellyfin not set up: {e!r}", flush=True)
        raise ValueError("local_failed") from None
    try:  # the setup's own session: Matinee signs in as itself below (one left open does no harm)
        local_call("/Sessions/Logout", "POST", {}, token)
    except (OSError, ValueError) as e:
        print(f"Local Jellyfin: the setup's session not ended: {e!r}", flush=True)
    try:
        LOCAL_ADMIN_FILE.unlink(missing_ok=True)  # its password is the person's no longer
    except OSError as e:
        print(f"{LOCAL_ADMIN_FILE} not removed: {e}", flush=True)
    setup_jellyfin({"server": LOCAL_JF, "username": user, "password": password})
