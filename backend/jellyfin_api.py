"""The Jellyfin client: connections, requests, signing in (and staying signed in), the network watch."""
import errno
import http.client
import http.cookies
import io
import json
import os
import queue
import select
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

from . import core
from .core import (conf, CONF_FILE, device_name, EARLY_FILE, file_lock, GZIP, HIDDEN_FILE, PENDING_FILE, read_conf,
    RETRY_MAX, save_state, state, STATE_FILE, SYNC_FILE, TRACKS_FILE, TV_FILE, unzipped, VERSION, write_private)


def auth_header():
    return jf_device_header(state["device_id"], device_name(), state.get("token"))


class Response:
    def __init__(self, r, body):
        self.status, self.reason, self.headers, self.body = r.status, r.reason, r.headers, body

    def read(self):
        return self.body


class Pool:
    """Connections to one server, kept open: no new TCP and TLS handshake for every request and image.
    Kept connections the server has closed in the meantime (they do after some idle time) are dropped
    before they are used. One that fails all the same is tried again on a new connection only when the request
    cannot have reached the server or may go twice: a report or a change must not be made twice. Answers are asked
    for packed (gzip) and handed on unpacked."""

    SAFE_TWICE = ("GET", "HEAD", "OPTIONS", "PUT", "DELETE")

    def __init__(self, url, timeout=20):
        self.url = urllib.parse.urlsplit(url)
        self.idle = queue.LifoQueue()
        self.timeout = timeout

    def kept(self):
        """A kept connection still open, or None. One the server has closed is readable (its end of the stream)."""
        while True:
            try:
                conn = self.idle.get_nowait()
            except queue.Empty:
                return None
            try:
                closed = conn.sock is None or bool(select.select([conn.sock], [], [], 0)[0])
            except (OSError, ValueError):
                closed = True
            if not closed:
                return conn
            conn.close()

    def request(self, path, method="GET", data=None, headers=None):
        u = self.url
        headers = {**GZIP, **(headers or {})}
        while True:
            conn = self.kept()
            reused = conn is not None
            if not reused:
                cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
                kw = {"context": ssl.create_default_context()} if u.scheme == "https" else {}
                conn = cls(u.hostname, u.port, timeout=self.timeout, **kw)
            sent = False
            try:
                conn.request(method, u.path.rstrip("/") + path, body=data, headers=headers)
                sent = True
                r = conn.getresponse()
                body = r.read()
            except (ConnectionError, TimeoutError, http.client.HTTPException):
                conn.close()
                if reused and (not sent or method in self.SAFE_TWICE):
                    continue
                raise
            except BaseException:
                conn.close()
                raise
            if r.will_close:
                conn.close()
            else:
                self.idle.put(conn)
            body = unzipped(r.headers, body)
            del r.headers["Content-Encoding"]  # as it is handed on: no longer packed
            return Response(r, body)


jellyfin = Pool(core.SERVER) if core.SERVER else None


def request(path, method="GET", data=None, headers=None):
    """One request to Jellyfin (none set up yet: as if it were not reachable)."""
    if not jellyfin:
        raise ConnectionRefusedError("no Jellyfin server set up")
    return jellyfin.request(path, method, data, headers)


def jf(path, method="GET", body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    used = state.get("token")
    headers = {"Authorization": auth_header()}
    if data is not None:
        headers["Content-Type"] = "application/json"
    resp = request(path, method, data, headers)
    if resp.status >= 400:
        # set-password signed in again meanwhile, which replaces this device's token on the server
        if resp.status == 401 and newer_token(used, 2 if used else 0):
            return jf(path, method, body, raw)
        if resp.status == 401 and state.get("token"):
            signed_out()
        raise urllib.error.HTTPError(core.SERVER + path, resp.status, resp.reason, resp.headers, io.BytesIO(resp.body))
    if raw:
        return resp
    return json.loads(resp.body) if resp.body else None


def reload_token():
    """Take over a token that set-password stored. True if it is a new one."""
    try:
        stored = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return False
    if not stored.get("token") or stored["token"] == state.get("token"):
        return False
    state.update(stored)
    return True


def newer_token(used, seconds):
    """True if there is a token other than the one a request was sent with. The server drops the old token
    as soon as set-password signs in, a moment before set-password stores the new one: wait that long."""
    end = time.monotonic() + seconds
    while True:
        reload_token()
        if state.get("token") != used:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)


def authenticate(password):
    """Sign in with the password. Only the access token is kept, never the password. Sent without a token and past
    jf()'s handling of a rejected one: a wrong password must never sign out the session there is."""
    header = auth_header().split(", Token=")[0]
    resp = request("/Users/AuthenticateByName", "POST",
                   json.dumps({"Username": conf.get("username", ""), "Pw": password}).encode(),
                   {"Authorization": header, "Content-Type": "application/json"})
    if resp.status >= 400:
        raise urllib.error.HTTPError(core.SERVER + "/Users/AuthenticateByName", resp.status, resp.reason, resp.headers,
                                     io.BytesIO(resp.body))
    r = json.loads(resp.body)
    state["token"] = r["AccessToken"]
    state["user_id"] = r["User"]["Id"]
    save_state()


@contextmanager
def conf_changing():
    """with conf_changing() as c: … — config.json as it is now (its copy from before if it cannot be read), changed
    in the block and written back, one writer at a time: the sync writes it too (Sync.once takes the same lock, after
    its own; nothing here may wait for the sync's)."""
    with file_lock(CONF_FILE):
        c = read_conf()
        yield c
        write_private(CONF_FILE, json.dumps(c, indent=2) + "\n")


def forget_person():
    """Another Jellyfin user signed in on this device: what was the one before's goes (the channel watched last, what
    was hidden, the track choices, the reports not sent yet), or the first sync would give it all to the new one; the
    servers set up and the settings stay, as the device's."""
    held = syncing.sync.lock.acquire(timeout=10)  # a sync under way finishes first (never waited for for ever: login_lock)
    try:
        for path in (TV_FILE, HIDDEN_FILE, TRACKS_FILE, EARLY_FILE, SYNC_FILE, PENDING_FILE):
            path.unlink(missing_ok=True)
        # and their copies from before (write_private): else a file that cannot be read would bring the one before's
        # back, and the sync give them to the new one
        for path in (TV_FILE, HIDDEN_FILE):
            path.with_name(path.name + ".bak").unlink(missing_ok=True)
        syncing.sync.known = None
    finally:
        if held:
            syncing.sync.lock.release()
    print("Another user signed in: the one before's things are gone from this device", file=sys.stderr)


def forget_password():
    """Older setups kept the password in config.json: remove it, the token is all Matinee needs."""
    conf.pop("password", None)
    with file_lock(CONF_FILE):
        c = read_conf()
        if "password" in c:
            del c["password"]
            write_private(CONF_FILE, json.dumps(c, indent=2) + "\n")
            CONF_FILE.with_name(CONF_FILE.name + ".bak").unlink(missing_ok=True)  # not kept there either


ready = threading.Event()
login_error = None
login_lock = threading.Lock()


class LoginRequired(Exception):
    """No valid token and no password: someone has to run set-password."""


def login():
    if not core.SERVER:
        raise LoginRequired
    reload_token()
    if state.get("token"):
        try:
            jf("/Users/Me")
            forget_password()
            return
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            state.pop("token", None)
            save_state()
    password = read_conf().get("password")
    if password is None:
        raise LoginRequired
    try:
        authenticate(password)
    except urllib.error.HTTPError as e:
        # never retry a wrong password: Jellyfin locks the account after a few failed logins
        if e.code == 401:
            forget_password()
        raise
    forget_password()


def signed_out():
    """The server no longer accepts the token (device removed under Dashboard → Devices, password changed):
    back to the start screen, which asks for set-password, and on as soon as there is a new token."""
    global login_error
    with login_lock:
        if not ready.is_set():
            return
        ready.clear()
        login_error = {"error": "signed_out"}
    print("Signed out by the server, waiting for set-password", file=sys.stderr)
    threading.Thread(target=login_loop, daemon=True).start()


# Without a network (the Mac asleep, the Wi-Fi gone) every server, channel and feed tried failed on a line of its own,
# dozens a minute: the first failure for want of a network is logged, the rest only counted until it is back. A name
# not resolved or a host not reached is also one server away (switched off, gone): the network counts as gone only
# when a name everyone has (NET_CHECK_HOST) does not resolve either. Gone for an hour, it is said again with what was
# left out, and the next failure logged as the first (the check host blocked by a filter, say, while the rest work).
NET_GONE = ("nodename nor servname", "Name or service not known", "Temporary failure in name resolution",
            "No address associated with hostname", "Network is unreachable", "No route to host")
NET_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN}
NET_CHECK_EVERY = float(os.environ.get("KINO_NET_CHECK_EVERY", "30"))
NET_CHECK_HOST = os.environ.get("KINO_NET_CHECK_HOST", "example.com")
NET_GONE_SAY_AGAIN = float(os.environ.get("KINO_NET_GONE_SAY_AGAIN", "3600"))
net_gone = {"since": None, "left": 0, "checked": 0.0, "up": True}
net_gone_lock = threading.Lock()


def no_network(e):
    """Whether e (or what caused it) is what a missing network gives: no name resolved, no route."""
    for _ in range(5):
        if e is None:
            return False
        reason = getattr(e, "reason", None)  # URLError: the OSError below it
        for x in (e, reason):
            if isinstance(x, socket.gaierror) or isinstance(x, OSError) and x.errno in NET_ERRNOS \
                    or any(s in str(x) for s in NET_GONE):
                return True
        e = e.__cause__ or e.__context__
    return False


def net_up():
    """Whether NET_CHECK_HOST resolves: looked up again after 5 seconds at the earliest."""
    with net_gone_lock:
        if time.monotonic() - net_gone["checked"] < 5:
            return net_gone["up"]
    try:
        socket.getaddrinfo(NET_CHECK_HOST, 443)
        up = True
    except Exception:  # OSError, or UnicodeError for a name that cannot be one
        up = False
    with net_gone_lock:
        net_gone.update(checked=time.monotonic(), up=up)
    return up


def log_net(line, e):
    """A failure to reach something in the log, unless the network is gone and that is said already."""
    with net_gone_lock:
        if net_gone["since"] is not None and no_network(e):
            net_gone["left"] += 1
            return
    if not no_network(e) or net_up():
        print(line, file=sys.stderr)
        return
    with net_gone_lock:
        if net_gone["since"] is not None:  # another thread said it meanwhile
            net_gone["left"] += 1
            return
        net_gone.update(since=time.monotonic(), left=0)
    print(f"{line}\nThe network is gone: failures for want of it are left out of the log until it is back",
          file=sys.stderr)
    threading.Thread(target=net_watch, daemon=True).start()


def net_watch():
    """Waits for the network to come back (at most NET_GONE_SAY_AGAIN) and says so."""
    while True:
        time.sleep(NET_CHECK_EVERY)
        with net_gone_lock:
            since = net_gone["since"]
        if net_up() or time.monotonic() - since >= NET_GONE_SAY_AGAIN:
            break
    with net_gone_lock:
        took, left = time.monotonic() - net_gone["since"], net_gone["left"]
        net_gone.update(since=None, left=0)
    how = "is back" if net_gone["up"] else "is still gone"
    print(f"The network {how} after {round(took / 60)} min, {left} failures for want of it left out of the log",
          file=sys.stderr)


def login_loop():
    """At boot the network or server is often not up yet: keep trying until it works. In the first minute
    (Matinee starts without waiting for the network) at least every 5 seconds, later less often."""
    global login_error
    delay = min(2, RETRY_MAX)
    started = time.monotonic()
    unreachable = 0  # failed so often for want of the server: said once (again for another cause), and when it answers
    said = set()  # the causes of failures logged as the server not answering, each once until another kind came
    told = None  # any other failure, logged once until it changes (a new device waiting to be set up: not every try)
    while True:
        try:
            login()
            login_error = None
            if unreachable:
                print(f"Logged in after {time.monotonic() - started:.0f} s ({unreachable} tries the server did not "
                      f"answer, mostly the network not up yet)", file=sys.stderr)
            with library.similar_lock:  # known similar titles, before the UI asks for the first one
                library.similar.update({k: v for k, v in library.load_similar().items() if k not in library.similar})
            ready.set()
            return
        except LoginRequired:
            # keep saying why, if the last password was rejected; "setup": no server yet
            if not core.SERVER:
                login_error = {"error": "setup"}
            elif (login_error or {}).get("error") not in ("auth_rejected", "signed_out"):
                login_error = {"error": "login_required"}
        except urllib.error.HTTPError as e:
            # error codes are translated by the UI
            login_error = {"error": "auth_rejected"} if e.code == 401 else {"error": "server_error", "detail": str(e.code)}
        except Exception as e:
            err = {"error": "unreachable", "detail": e.__class__.__name__}
            if no_network(e) and not net_up():  # no network at all, not only the server: the sign-in says so
                err["network"] = False
            login_error = err  # whole at once: the UI may ask meanwhile
            if login_error["detail"] not in said:
                log_net(f"Login failed: {login_error}: tried again until the server answers", e)
                said.add(login_error["detail"])
            unreachable += 1
            told = None  # the same failure as before it, once the server answers again: told again
        if login_error.get("error") != "unreachable":
            if login_error != told:
                print(f"Login failed: {login_error}", file=sys.stderr)
                told = dict(login_error)
            said.clear()  # a failure of the network after it: told again
        time.sleep(delay)
        delay = min(delay * 2, RETRY_MAX if time.monotonic() - started > 60 else min(5, RETRY_MAX))


def setup_jellyfin(body):
    """The server, user name and password from the TV (a fresh install, signed out, another server): signed in with
    them, only the server and user name go into config.json, the password nowhere. Nothing changes if it fails.
    Raises ValueError with an error code the UI translates."""
    global jellyfin, login_error
    if not isinstance(body, dict):
        raise ValueError("service_unknown")
    url = services.service_url(body.get("server"))
    user, password = str(body.get("username") or "").strip(), str(body.get("password") or "")
    if not user or len(user) > 200:
        raise ValueError("service_missing_user")
    if len(password) > 4096:
        raise ValueError("service_missing_secret")  # Jellyfin allows an empty password
    before = (core.SERVER, jellyfin, conf.get("username"), state.get("token"), state.get("user_id"))
    with login_lock:
        core.SERVER, jellyfin = url, Pool(url)
        conf["username"] = user
        state.pop("token", None)
        try:
            authenticate(password)
        except (urllib.error.HTTPError, OSError, ValueError, KeyError) as e:
            core.SERVER, jellyfin = before[:2]
            if before[2] is None:  # a fresh install: none, as before
                conf.pop("username", None)
            else:
                conf["username"] = before[2]
            state.update({k: v for k, v in (("token", before[3]), ("user_id", before[4])) if v})
            raise ValueError("service_rejected" if getattr(e, "code", None) == 401 else "service_unreachable") from None
        with conf_changing() as c:
            c.update(server=url, username=user)
            c.pop("password", None)
        conf.update(server=url)
        login_error = None
        if before[4] and str(state.get("user_id") or "").replace("-", "") != before[4].replace("-", ""):
            forget_person()  # someone else (Sign In Everywhere, another user in the settings)
            from . import tv  # loads after this module (see core's end)
            tv.own_forget()  # their library, not the one before's (also a new user of the same name)
        if (url, user) != (before[0], before[2]):  # another server or user: nothing of the old one stays
            live_events.server_epoch += 1  # its live connection ends (ws_loop)
            library.badges_cache.clear()
            with library.similar_lock:
                library.similar.clear()
            library.similar_wanted.put(None)
            from . import tv  # loads after this module (see core's end)
            tv.own_forget()  # one's own channels were made of the one before's library
        with library.similar_lock:
            library.similar.update({k: v for k, v in library.load_similar().items() if k not in library.similar})
        ready.set()


def log_failure(what, e):
    """An error the UI says in plain words (or that is tried again later): its reason goes to the log, not lost."""
    log_net(f"{what}: {e.__class__.__name__}: {e}", e)


def jf_device_header(device_id, name, token=None):
    h = f'MediaBrowser Client="Matinee", Device="{name}", DeviceId="{device_id}", Version="{VERSION}"'
    return h + (f', Token="{token}"' if token else "")


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import desktop, library, live_events, syncing, services  # noqa: E402,F401
