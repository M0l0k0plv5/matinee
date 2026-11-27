"""Taking the account over to another device, and signing out."""
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core
from .core import (ascii_name, conf, CONF_DIR, CONF_FILE, device_name, EARLY_FILE, file_lock, forget_kept, HIDDEN_FILE,
    PENDING_FILE, read_conf, state, STATE_FILE, SYNC_FILE, TRACKS_FILE, TV_FILE, VERSION, write_private)
from .jellyfin_api import conf_changing, jf_device_header, request
from .library import SIMILAR_FILE
from .syncing import sync
from .system import TAILSCALE_APP
from .updates import restart_kino


# ---------- Taking everything over to another device, and signing out ----------
#
# The account is what Matinee keeps of the person (the servers and the sign-ins to them, the channels and programmes
# followed, what was hidden, the settings), apart from what belongs to the device (its screen, graphics, Bluetooth,
# sound output). Handed over once, over Tailscale: the device that is to have it waits with a code on its screen
# (Settings → Account, or the welcome screen); the one that has it finds it among the tailnet's devices and sends it
# with that code. Only the receiving one listens (a Mac's firewall often lets nothing in); Jellyfin signs it in with a
# token of its own, the password typed for it on the sending one and kept nowhere. Signing out leaves a device as
# freshly installed.
DEVICE_KEYS = {"effects", "quality", "framerate", "display", "thin_client", "sleepend", "autooff", "autoupdate", "audiosync",
               "volume", "simple", "textsize", "size", "device_name", "updatecheck", "hdmi_inputs", "welcomed", "maxage"}
# the next person most likely speaks the language too; they go through the first start's steps again (their Jellyfin,
# their remote)
KEEP_SIGNED_OUT = (DEVICE_KEYS - {"welcomed"}) | {"language"}
ACCOUNT_FILES = {"tv": TV_FILE, "hidden": HIDDEN_FILE, "tracks": TRACKS_FILE,
                 "navidrome": CONF_DIR / "navidrome.json", "audiobookshelf": CONF_DIR / "audiobookshelf.json",
                 "podfetch": CONF_DIR / "podfetch.json", "early": EARLY_FILE}  # early: the films stopped early, the account's
TRANSFER_PORT = int(os.environ.get("KINO_TRANSFER_PORT", "8766"))
TRANSFER_SECONDS = float(os.environ.get("KINO_TRANSFER_SECONDS", "600"))  # how long a device waits
TRANSFER_TRIES = 5  # wrong codes before it stops waiting: one in 200,000 to guess it
TRANSFER_ERRORS = {"transfer_code", "transfer_gone", "transfer_unreachable"}
DEVICE_ID = re.compile(r"[0-9a-f]{32}")
transfer = {"wait": None, "servers": []}
transfer_lock = threading.Lock()


def tailscale_status():
    binary = os.environ.get("KINO_TAILSCALE") or shutil.which("tailscale") or TAILSCALE_APP
    try:
        out = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=3)
        d = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return d if d.get("BackendState") == "Running" else None


def ipv4(ips):
    return next((ip for ip in ips or [] if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip)), None)


def jf_sign_in_as(device_id, name, password):
    """Jellyfin's token for another device (its id and name): (token, user id); ValueError if refused."""
    try:
        resp = request("/Users/AuthenticateByName", "POST", json.dumps({"Username": conf.get("username", ""), "Pw": password}).encode(),
                       {"Authorization": jf_device_header(device_id, name), "Content-Type": "application/json"})
    except OSError:
        raise ValueError("service_unreachable") from None
    if resp.status == 401:
        raise ValueError("service_rejected")
    if resp.status >= 400:
        raise ValueError("service_unreachable")
    r = json.loads(resp.body)
    return r["AccessToken"], r["User"]["Id"]


def jf_sign_out(device_id, name, token):
    """End a session on Jellyfin (it leaves the server's devices); never mind if the server is not there."""
    try:
        request("/Sessions/Logout", "POST", b"", {"Authorization": jf_device_header(device_id, name, token)})
    except OSError:
        pass


def transfer_wait():
    """Wait for an account from another device for a while: the code to type there. A door opens on the tailnet."""
    here = ipv4(((tailscale_status() or {}).get("Self") or {}).get("TailscaleIPs"))
    if not here:
        raise ValueError("transfer_no_tailscale")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with transfer_lock:
        transfer["wait"] = {"code": code, "until": time.monotonic() + TRANSFER_SECONDS, "tries": 0, "from": None}
        if not transfer["servers"]:
            try:
                srv = ThreadingHTTPServer((here, TRANSFER_PORT), TransferHandler)
            except OSError:
                transfer["wait"] = None
                raise ValueError("transfer_busy") from None
            transfer["servers"].append(srv)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            threading.Thread(target=transfer_watch, daemon=True).start()
    return {"code": code, "name": device_name(), "seconds": int(TRANSFER_SECONDS)}


def waiting(w):
    return bool(w) and w["from"] is None and time.monotonic() < w["until"] and w["tries"] < TRANSFER_TRIES


def transfer_watch():
    """No longer waiting (time up, taken, given up): the door closes again."""
    while True:
        time.sleep(0.5)
        with transfer_lock:
            if waiting(transfer["wait"]):
                continue
            # closed while the lock is held: a wait right after binds the port anew, which the old socket would
            # still hold (shutdown only ends serve_forever; its own handlers do not need the lock to end)
            for srv in transfer["servers"]:
                srv.shutdown()
                srv.server_close()
            transfer["servers"] = []
        return


def transfer_state():
    with transfer_lock:
        w = transfer["wait"]
        if not w:
            return {"waiting": False}
        return {"waiting": waiting(w), "from": w["from"], "seconds": max(0, int(w["until"] - time.monotonic()))}


def transfer_cancel():
    with transfer_lock:
        if transfer["wait"] and transfer["wait"]["from"] is None:
            transfer["wait"] = None


def transfer_given(body, peer):
    """Another device sends its account with the code: taken in place of this one's, Matinee starts again."""
    if not isinstance(body, dict):
        return 400, {}
    sender = re.sub(r"[^\w .-]", "", str(body.get("from", ""))[:64]) or peer
    with transfer_lock:
        w = transfer["wait"]
        if not waiting(w):
            return 404, {"error": "transfer_gone"}
        if not hmac.compare_digest(str(body.get("code", "")), w["code"]):
            w["tries"] += 1
            return 403, {"error": "transfer_code"}
        try:
            with sync.lock:  # the account before is not synced back over the one taken
                sync.stopped = True
                account_apply(body.get("account"))
        except ValueError:
            sync.stopped = False  # nothing was taken: this account goes on as before
            return 400, {"error": "transfer_unreachable"}
        except AccountNotTaken as e:  # the disk full while writing it aside: nothing of it in place, this
            # account goes on as before
            sync.stopped = False
            print(f"Account not taken over: {e}", file=sys.stderr)
            return 507, {"error": "transfer_unreachable"}
        except OSError as e:  # failed while putting it in place (renames only, hardly ever): the sync left stopped,
            # and nothing kept of what was synced before (account_apply), so no restart takes the mix for removals
            print(f"Account taken over in part: {e}", file=sys.stderr)
            return 507, {"error": "transfer_unreachable"}
        w["from"] = sender
    print(f"Account taken over from {sender} ({peer}): starting Matinee again", file=sys.stderr)
    restart_kino()
    return 200, {}


class TransferHandler(BaseHTTPRequestHandler):
    """The door on the tailnet while a device waits: asking whether it does, and giving it the account."""
    timeout = 20  # one that connects and then sends nothing does not hold a thread for as long as the door is open

    def log_message(self, *_):
        pass

    def answer(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/transfer/hello" or not transfer_state()["waiting"]:
            return self.answer(404, {})
        return self.answer(200, {"kino": VERSION, "name": device_name(), "device": state["device_id"]})

    def do_POST(self):
        length = self.headers.get("Content-Length") or "0"
        if self.path != "/transfer/give" or not length.isdigit() or int(length) > 2_000_000:
            return self.answer(404, {})
        n = int(length)  # only digits: never negative (read(-1) would read without end)
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            return self.answer(400, {})
        return self.answer(*transfer_given(body, self.client_address[0]))


def account_settings(given):
    """Of another device's config.json, what an account may bring: the person's settings with values this Matinee allows,
    the servers and the user name. Anything else (a key not known, a value of the wrong kind) is left out, so whoever
    has the code cannot slip in more than the settings page could set."""
    from .preferences import SETTINGS, synced_setting_ok  # imports this module
    def server_ok(v):
        return isinstance(v, str) and len(v) < 512 and re.fullmatch(r"https?://[^\s\"'<>]+", v)
    def part_ok(v):  # Navidrome, Audiobookshelf, Podfetch: their address and sign-in, as their settings keep them
        return isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, (str, int, bool)) for k, x in v.items()) \
            and (not v.get("server") or server_ok(v["server"]))
    kept = {}
    for k, v in given.items():
        if not isinstance(k, str) or k in DEVICE_KEYS or k == "password":
            continue
        if k == "server" and server_ok(v) or k == "username" and isinstance(v, str) and len(v) < 256 \
                or k in ("navidrome", "audiobookshelf", "podfetch") and part_ok(v) \
                or k == "menupicture" and isinstance(v, str) and re.fullmatch(r"[\w-]{1,64}", v) \
                or k == "sync" and v in SETTINGS["sync"] \
                or synced_setting_ok(k, v):
            kept[k] = v
    return kept


class AccountNotTaken(OSError):
    """account_apply could not write the other account aside: nothing of it is in place."""


def account_apply(bundle):
    """Write another account's things in place of this one's (the device's own settings stay)."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("config"), dict) or not isinstance(bundle.get("files"), dict):
        raise ValueError("bad account")
    jfa = bundle.get("jellyfin")
    if jfa is not None and not (isinstance(jfa, dict) and isinstance(jfa.get("token"), str) and isinstance(jfa.get("user_id"), str)):
        raise ValueError("bad account")
    new = {"device_id": state["device_id"]}
    if jfa:
        new.update(token=jfa["token"], user_id=jfa["user_id"])
    if isinstance(bundle.get("section"), str):
        new["section"] = bundle["section"]
    with file_lock(CONF_FILE):  # the other account's settings, with this device's own (one writer: conf_changing)
        before = read_conf()
        settings = account_settings(bundle["config"])
        settings.update({k: v for k, v in before.items() if k in DEVICE_KEYS})
        # all of it written aside first: a disk full on the way leaves this account as it was, not half the other's
        texts = {path: json.dumps(bundle["files"][key]) for key, path in ACCOUNT_FILES.items() if key in bundle["files"]}
        texts[CONF_FILE] = json.dumps(settings, indent=2) + "\n"
        texts[STATE_FILE] = json.dumps(new)
        staged = {}
        try:
            for path, text in texts.items():
                staged[path] = path.with_name(path.name + ".taking")
                write_private(staged[path], text)
        except OSError as e:
            for aside in staged.values():
                aside.unlink(missing_ok=True)
            raise AccountNotTaken(e) from e
        # what was synced from here before goes first: the lists taken are new to this device, so the server's win
        # over them (else what this device had, and the account taken has not, would be taken off the server as
        # removed, also by a restart after a failure further on)
        SYNC_FILE.unlink(missing_ok=True)
        sync.known = None
        for path, aside in staged.items():
            os.replace(aside, path)
    for key, path in ACCOUNT_FILES.items():
        if key not in bundle["files"]:
            path.unlink(missing_ok=True)
    PENDING_FILE.unlink(missing_ok=True)
    SIMILAR_FILE.unlink(missing_ok=True)
    forget_kept()
    # this device's own session ends once the other account's is in place (not before: a disk full on the way leaves
    # it signed in as it was)
    if core.SERVER and state.get("token"):
        jf_sign_out(state["device_id"], device_name(), state["token"])


def transfer_peer(ip, body=None, timeout=3):
    req = urllib.request.Request(f"http://{ip}:{TRANSFER_PORT}/transfer/{'hello' if body is None else 'give'}",
                                 data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def tailnet_peers():
    st = tailscale_status()
    if not st:
        raise ValueError("transfer_no_tailscale")
    return sorted({ip for ip in (ipv4(p.get("TailscaleIPs")) for p in (st.get("Peer") or {}).values() if p.get("Online")) if ip})


def transfer_devices():
    """The tailnet's devices waiting for an account just now: [{name, ip}]."""
    ips = tailnet_peers()

    def hello(ip):
        try:
            return {"name": str(transfer_peer(ip, timeout=2).get("name") or ip)[:64], "ip": ip}
        except (OSError, ValueError, AttributeError):
            return None
    with ThreadPoolExecutor(min(16, max(1, len(ips)))) as pool:
        return [d for d in pool.map(hello, ips) if d]


def account_bundle():
    c = read_conf()
    files = {}
    for key, path in ACCOUNT_FILES.items():
        try:
            files[key] = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    return {"config": {k: v for k, v in c.items() if k not in DEVICE_KEYS and k != "password"}, "files": files,
            "section": state.get("section")}


def transfer_send(ip, code, password):
    """Send this account to a device waiting at ip, with the code it shows; Jellyfin signs it in (password)."""
    if ip not in tailnet_peers():
        raise ValueError("transfer_gone")
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("transfer_code")
    try:
        there = transfer_peer(ip)
    except (OSError, ValueError):
        raise ValueError("transfer_gone") from None
    if not isinstance(there, dict):  # not a Matinee waiting there
        raise ValueError("transfer_gone")
    device, name = str(there.get("device", "")), re.sub(r"[^\w .'-]", "", ascii_name(str(there.get("name", ""))[:64])) or "Matinee"
    if not DEVICE_ID.fullmatch(device):
        raise ValueError("transfer_gone")
    bundle = account_bundle()
    token = None
    if core.SERVER and state.get("token"):
        if not isinstance(password, str) or len(password) > 4096:
            raise ValueError("service_missing_secret")
        token, user = jf_sign_in_as(device, name, password)
        bundle["jellyfin"] = {"token": token, "user_id": user}
    try:
        transfer_peer(ip, {"code": code, "from": device_name(), "account": bundle}, timeout=20)
    except urllib.error.HTTPError as e:  # a wrong code, no longer waiting
        try:
            error = json.loads(e.read()).get("error")
        except (OSError, ValueError, AttributeError):
            error = None
        if token:
            jf_sign_out(device, name, token)
        raise ValueError(error if error in TRANSFER_ERRORS else "transfer_gone") from None
    except (OSError, ValueError):
        if token:
            jf_sign_out(device, name, token)
        raise ValueError("transfer_unreachable") from None
    print(f"Account sent to {name} ({ip})", file=sys.stderr)
    return name


def sign_out():
    """Everything of the account off this device (its own settings stay): as freshly installed, Matinee starts again."""
    if core.SERVER and state.get("token"):
        jf_sign_out(state["device_id"], device_name(), state["token"])
    with sync.lock:  # a sync under way finishes first, none after it: nothing of the account written back
        sync.stopped = True
        for path in [*ACCOUNT_FILES.values(), PENDING_FILE, SIMILAR_FILE, SYNC_FILE]:
            path.unlink(missing_ok=True)
    with conf_changing() as c:
        kept = {k: v for k, v in c.items() if k in KEEP_SIGNED_OUT}
        c.clear()
        c.update(kept)
    write_private(STATE_FILE, json.dumps({"device_id": uuid.uuid4().hex}))  # to Jellyfin, a new device
    forget_kept()
    print("Signed out: starting Matinee again", file=sys.stderr)
    restart_kino()


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import http_api  # noqa: E402,F401
