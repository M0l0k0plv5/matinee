"""The same on every device: Matinee's lists and choices kept with the Jellyfin account."""
import hashlib
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

from . import core
from .core import (COMMIT, CONF_FILE, device_name, EARLY_FILE, file_lock, HIDDEN_FILE, read_conf, state, SYNC_FILE,
    TRACKS_FILE, VERSION, write_private)
from .jellyfin_api import jf, log_net, ready
from .playback import EARLY_KEEP
from .preferences import setting, synced_setting, synced_setting_ok, take_synced_settings
from .live_events import broadcast


# ---------- The same on every device: kept with the Jellyfin account ----------
#
# What one leaves off rows and how one likes it belongs to the person, not the device: what was taken off a row, the
# audio and subtitles chosen for a show, the settings, where a film began early. Every Matinee
# signed in to the same Jellyfin user keeps them alike, without an account of its own: Jellyfin keeps them for its
# clients among the user's display preferences ("kino"). Each entry carries when it last changed on a device, an entry
# removed a mark of its removal, so two devices are put together entry by entry, the newer winning; marks of removals
# are dropped after SYNC_TOMBSTONE_DAYS. Every device looks at the server every SYNC_EVERY seconds and right after a
# change of its own. The time of a change is the device's own: their clocks are set from the network.
SYNC_EVERY = float(os.environ.get("KINO_SYNC_EVERY", "120"))
SYNC_DEBOUNCE = float(os.environ.get("KINO_SYNC_DEBOUNCE", "2"))  # changes close together go up as one
SYNC_TOMBSTONE_DAYS = 60
SYNC_DEVICE_EVERY = 600  # how often a device says it is still there (the devices' overview)
SYNC_DEVICE_DAYS = 90  # a device not heard of for so long: off the overview
SYNC_PREFS = "kino"  # the display preferences' id and client: the same for every Matinee
SYNC_KEY = "kino-sync"
# what is kept alike: the file, its list, an entry's key, how many go up (the newest), and how many a device keeps
SYNC_LISTS = {
    "hiddenNextup": (HIDDEN_FILE, "nextup", lambda e: e, 500, 5000),
    "hiddenAlbums": (HIDDEN_FILE, "albums", lambda e: e, 500, 5000),
    "hiddenBooks": (HIDDEN_FILE, "books", lambda e: e, 500, 5000),
    "jfEarly": (EARLY_FILE, "early", lambda e: e.get("id"), EARLY_KEEP, EARLY_KEEP),
}
# and what is kept as a file's keys rather than a list: the file, which of its keys, how many go up. Each key is an
# entry {"k": key, "v": value} to the sync; one the device cannot take (an unknown setting or value) is left out.
TRACK_CHOICE = re.compile(r"[\w-]{1,64}")
SYNC_MAPS = {
    # the audio and subtitles chosen by hand for a show or film
    "tracks": (TRACK_CHOICE.fullmatch, lambda v: isinstance(v, dict) and all(isinstance(x, str) for x in v.values()), 2000),
    # the person's settings (synced_setting): languages, subtitles, the screensaver, sounds, …
    "settings": (lambda k: synced_setting(k), lambda v: True, 100),
}
SYNC_MAP_FILES = {"tracks": TRACKS_FILE, "settings": CONF_FILE}
SYNC_MAP_IN = {}  # a map kept under a key of its file, not as the file's own keys
SYNC_NAMES = [*SYNC_LISTS, *SYNC_MAPS]


def sync_path(name):
    return SYNC_LISTS[name][0] if name in SYNC_LISTS else SYNC_MAP_FILES[name]


def sync_key(name):
    return SYNC_LISTS[name][2] if name in SYNC_LISTS else (lambda e: e.get("k") if isinstance(e, dict) else None)


# the lists whose entries are their own keys (an id as a string); the others' entries are dicts, keyed by a field
SYNC_PLAIN = {"hiddenNextup", "hiddenAlbums", "hiddenBooks"}


def sync_entry_shaped(name, entry):
    """An entry of the shape its list has: one of another would break whatever reads the list (e.get, x["id"])."""
    return isinstance(entry, str) if name in SYNC_PLAIN else isinstance(entry, dict)


def sync_entry_ok(name, entry):
    """An entry of a list or map, from the server, that this device can take."""
    if not sync_entry_shaped(name, entry):
        return False
    if name not in SYNC_MAPS:
        return True
    allowed, value_ok, _up = SYNC_MAPS[name]
    k, v = entry.get("k"), entry.get("v")
    if not isinstance(k, str) or not allowed(k) or not value_ok(v):
        return False
    return synced_setting_ok(k, v) if name == "settings" else True


sync_quiet = threading.local()


@contextmanager
def unhurried():
    """Files kept alike written meanwhile go up with the sync's next look (every SYNC_EVERY), not at once: where a
    video is, kept every minute while it plays, would otherwise send the whole of them up each minute."""
    sync_quiet.on = True
    try:
        yield
    finally:
        sync_quiet.on = False


def save_synced(path, text):
    """Write one of the files kept alike (SYNC_LISTS) and tell the sync what changed in it."""
    write_private(path, text)
    sync.changed(path)


class Sync:
    def __init__(self):
        self.lock = threading.RLock()  # the files' lists and what is known of them, changed by one at a time
        self.kick = threading.Event()
        self.kick.set()  # once at the start: what the other devices changed meanwhile
        self.status = {"at": None, "error": None}
        self.stopped = False  # signing out: nothing written any more until Matinee starts again
        self.known = None  # {"account", "seen": {list: {key: [changed at, digest] or [removed at, None]}}}
        self.devices = {}  # the Matinees signed in to the account, as the server had them last (_devices)
        self.noted = False  # whether the last _note found anything changed

    @staticmethod
    def _read(path):
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _digest(entry):
        return hashlib.sha1(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:12]

    def _account(self):
        return f"{core.SERVER}|{state.get('user_id') or ''}"

    def _load_known(self):
        if self.known is None or self.known.get("account") != self._account():
            known = self._read(SYNC_FILE)
            # another server or user (or none yet): nothing known, what is here is older than anything there
            self.known = known if known.get("account") == self._account() else {"account": self._account(), "seen": {}}
        return self.known

    def _lists(self):
        """The synced lists as the files have them now: {list: [entries]}."""
        files = {}
        return {name: self._entries(name, files.setdefault(sync_path(name), self._read(sync_path(name))))
                for name in SYNC_NAMES}

    @staticmethod
    def _entries(name, data):
        """One synced list as a file's contents have it."""
        if name in SYNC_MAPS:
            m = data.get(SYNC_MAP_IN[name]) if name in SYNC_MAP_IN else data
            return [{"k": k, "v": v} for k, v in m.items() if SYNC_MAPS[name][0](k)] if isinstance(m, dict) else []
        entries = data.get(SYNC_LISTS[name][1])
        return [e for e in entries if sync_entry_shaped(name, e)] if isinstance(entries, list) else []

    def _note(self, now=None, lists=None):
        """What changed in the files since last looked at, with when: added or changed entries now, removed ones too.
        An entry seen for the first time on a device that never synced counts as old (its own time, or none)."""
        known = self._load_known()
        first = not known["seen"]
        # the lists synced here before: one new to this version of Matinee (a device's settings, say) counts as never
        # synced, so what the other devices have of it wins over what this one had all along
        names = known.setdefault("names", list(known["seen"]))
        now = now or time.time()
        self.noted = False  # whether anything changed since last looked at
        for name, entries in (lists if lists is not None else self._lists()).items():
            key = sync_key(name)
            fresh = first or name not in names
            seen = known["seen"].setdefault(name, {})
            here = {}
            for e in entries:
                k = key(e)
                if isinstance(k, str) and k not in here:
                    here[k] = e
            for k, e in here.items():
                d = self._digest(e)
                was = seen.get(k)
                if not was or was[1] != d:
                    # never synced before: as old as the entry says, else older than anything on the server
                    seen[k] = [((e.get("at") if isinstance(e, dict) else None) or 0) if fresh else now, d]
                    self.noted = True
            for k, (at, d) in list(seen.items()):
                if d is not None and k not in here:
                    seen[k] = [now, None]  # removed here
                    self.noted = True
        return known

    def changed(self, path):
        """A file kept alike was written: what changed noted at once (its time), sent up a moment later. Its writer
        may hold the file's lock, which once() takes inside self.lock: never waited for here (a sync going on notes
        it itself, the kick makes sure)."""
        if self.stopped or not any(sync_path(n) == path for n in SYNC_NAMES):
            return
        noted = True  # a sync going on: it may have read the file before, the next one has it
        if self.lock.acquire(blocking=False):
            try:
                self._note()
                noted = self.noted
                if noted:
                    self._save_known()
            finally:
                self.lock.release()
        # nothing of what is kept alike changed (where a live channel was left, say): nothing to send
        if noted and not getattr(sync_quiet, "on", False):
            self.kick.set()

    def _save_known(self):
        try:
            write_private(SYNC_FILE, json.dumps(self.known))
        except OSError as e:
            print(f"Could not keep what is synced: {e}", file=sys.stderr)

    def _remote(self):
        prefs = jf(f"/DisplayPreferences/{SYNC_PREFS}?userId={state['user_id']}&client={SYNC_PREFS}") or {}
        try:
            doc = json.loads((prefs.get("CustomPrefs") or {}).get(SYNC_KEY) or "{}")
        except ValueError:
            doc = {}
        doc = doc if isinstance(doc, dict) else {}
        lists, devices = doc.get("lists"), doc.get("devices")
        return prefs, lists if isinstance(lists, dict) else {}, devices if isinstance(devices, dict) else {}

    @staticmethod
    def _devices(there):
        """The Matinees of the account, for the settings' overview: each says its name, the version it runs and when it last
        synced; this one's own line anew when its version changed or every SYNC_DEVICE_EVERY. Gone after SYNC_DEVICE_DAYS."""
        now = int(time.time())
        devices = {k: v for k, v in there.items() if isinstance(k, str) and isinstance(v, dict)
                   and isinstance(v.get("at"), (int, float)) and now - v["at"] < SYNC_DEVICE_DAYS * 86400}
        mine = devices.get(state["device_id"], {})
        me = {"name": device_name(), "version": VERSION, "commit": COMMIT}
        if any(mine.get(k) != v for k, v in me.items()) or now - mine.get("at", 0) >= SYNC_DEVICE_EVERY:
            devices[state["device_id"]] = {**me, "at": now}
        return devices

    def device_list(self):
        """The overview: each Matinee of the account (this one first), how many versions it is behind the newest (from this
        checkout's own look at it; None when not known)."""
        out = []
        for did, d in self.devices.items():
            commit, behind = d.get("commit"), None
            if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
                behind = updates.commits_behind(commit)
            out.append({"name": str(d.get("name") or "?"), "version": str(d.get("version") or ""), "at": d.get("at"),
                        "self": did == state["device_id"], "behind": behind})
        return sorted(out, key=lambda d: (not d["self"], d["name"].lower()))

    def once(self):
        """Put this device and the server together: entry by entry, the newer winning, both written as needed."""
        with self.lock:
            if self.stopped:
                return False
            # the files read once, for noting what changed here and for putting them together alike: read twice, a
            # change in between (a title shown again a moment after it was taken off) counted as removed long ago,
            # lost to the server's older entry and written back, and was never noted again
            local = self._lists()
            known = self._note(lists=local)
            self._save_known()  # the times of this device's changes, whether the server answers or not
            prefs, remote, remote_devices = self._remote()
            # what a newer Matinee keeps there that this one does not know: left as it is
            merged_doc = {n: v for n, v in remote.items() if n not in SYNC_NAMES}
            changed_here, before = {}, {}
            cutoff = time.time() - SYNC_TOMBSTONE_DAYS * 86400
            for name in SYNC_NAMES:
                key = sync_key(name)
                up, keep = SYNC_LISTS[name][3:5] if name in SYNC_LISTS else (SYNC_MAPS[name][2],) * 2
                seen = before[name] = known["seen"].setdefault(name, {})
                there = remote.get(name, {}) if isinstance(remote.get(name), dict) else {}
                entries = {key(e): e for e in local[name] if isinstance(key(e), str)}
                both = {}
                for k, (at, d) in seen.items():
                    both[k] = [at, entries.get(k) if d is not None else None]
                for k, v in there.items():
                    if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], (int, float))):
                        continue
                    if v[1] is not None and (not sync_entry_ok(name, v[1]) or key(v[1]) != k):
                        continue  # nothing this device can take
                    # the newer wins; at the same time the server's (two devices syncing it for the first time)
                    if k not in both or v[0] > both[k][0] or v[0] == both[k][0] and v[1] is not None:
                        both[k] = v
                # up: the newest present ones and the recent removals
                present = sorted(((k, v) for k, v in both.items() if v[1] is not None), key=lambda x: -x[1][0])
                gone = [(k, v) for k, v in both.items() if v[1] is None and v[0] > cutoff]
                merged_doc[name] = dict(present[:up] + gone)
                # here: the present ones newest first, then those of this device the server does not know (past
                # what goes up), as many as a device keeps
                order = [v[1] for _k, v in present]
                mentioned = set(both)
                order += [e for k, e in entries.items() if k not in mentioned]
                order = order[:keep]
                as_map = lambda es: {e["k"]: e["v"] for e in es}  # a map's keys have no order
                if (as_map(order) != as_map(local[name])) if name in SYNC_MAPS else order != local[name]:
                    changed_here[name] = order
                known["seen"][name] = {k: [v[0], self._digest(v[1]) if v[1] is not None else None]
                                       for k, v in both.items() if v[1] is not None or v[0] > cutoff}
            if changed_here:
                for path in {sync_path(n) for n in changed_here}:
                    with file_lock(path):
                        data = read_conf() if path == CONF_FILE else self._read(path)
                        for name, order in changed_here.items():
                            if sync_path(name) != path:
                                continue
                            if self._entries(name, data) != local[name]:
                                # written here meanwhile (the server took its time): left as it is, and as known
                                # before, so the next look merges it with the server's again
                                known["seen"][name] = before[name]
                                self.kick.set()
                                continue
                            if name in SYNC_LISTS:
                                data[SYNC_LISTS[name][1]] = order
                            else:  # a map: its keys as the entries say, the file's others left alone
                                allowed = SYNC_MAPS[name][0]
                                if name in SYNC_MAP_IN:
                                    data[SYNC_MAP_IN[name]] = {e["k"]: e["v"] for e in order}
                                else:
                                    data = {**{k: v for k, v in data.items() if not allowed(k)}, **{e["k"]: e["v"] for e in order}}
                        write_private(path, json.dumps(data, indent=2) + "\n" if path == CONF_FILE else json.dumps(data))
                    if path == CONF_FILE:
                        take_synced_settings(data)
                        broadcast("settings")  # the page takes them too
            known["names"] = list(SYNC_NAMES)
            self._save_known()
            devices = self._devices(remote_devices)
            self.devices = devices
            if merged_doc != remote or devices != remote_devices:
                prefs = {"Id": SYNC_PREFS, "Client": SYNC_PREFS, "SortBy": "SortName", "SortOrder": "Ascending",
                         "RememberIndexing": False, "RememberSorting": False, "ShowBackdrop": True, "ShowSidebar": False,
                         "ScrollDirection": "Horizontal", "PrimaryImageHeight": 250, "PrimaryImageWidth": 250, **prefs}
                prefs["CustomPrefs"] = {**(prefs.get("CustomPrefs") or {}),
                                        SYNC_KEY: json.dumps({"v": 1, "lists": merged_doc, "devices": devices}, separators=(",", ":"))}
                jf(f"/DisplayPreferences/{SYNC_PREFS}?userId={state['user_id']}&client={SYNC_PREFS}", "POST", prefs)
            self.status = {"at": time.time(), "error": None}
            return bool(changed_here)

    def loop(self):
        while True:
            self.kick.wait(SYNC_EVERY)
            if self.kick.is_set():
                time.sleep(SYNC_DEBOUNCE)  # what else changes with it goes up too
                self.kick.clear()
            ready.wait()
            if setting("sync") != "on" or not state.get("user_id"):
                continue
            try:
                self.once()
            except Exception as e:  # the server away, a network gone: the next time, nothing lost meanwhile
                self.status = {**self.status, "error": str(e)}
                log_net(f"Could not sync with Jellyfin: {e}", e)


sync = Sync()


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import services, updates  # noqa: E402,F401
