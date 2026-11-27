"""Where Matinee keeps its files, config.json and state.json, the version, the language and its texts, and writing files
safely."""
import base64
import errno
import gzip
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path

from . import release  # standalone: nothing of Matinee's in it


ROOT = Path(__file__).resolve().parent.parent  # the checkout

# Answers asked for packed: a list of some MB (a library, a programme guide) is a tenth of it on the way, which a slow
# line (a server reached from elsewhere through the tailnet) shows as seconds
GZIP = {"Accept-Encoding": "gzip"}


def unzipped(headers, data):
    """An answer's body as it was before the server packed it (headers: the answer's). An OSError if it cannot be
    unpacked."""
    if (headers.get("Content-Encoding") or "").strip().lower() != "gzip":
        return data
    try:
        return gzip.decompress(data)
    except (OSError, EOFError, zlib.error) as e:
        raise OSError(f"a packed answer could not be unpacked: {e}") from e
WEB = ROOT / "web"
CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kino"
CONF_FILE = CONF_DIR / "config.json"
STATE_FILE = CONF_DIR / "state.json"
PENDING_FILE = CONF_DIR / "pending.json"
SYNC_FILE = CONF_DIR / "sync.json"
TRACKS_FILE = CONF_DIR / "tracks.json"
TV_FILE = CONF_DIR / "tv.json"
HIDDEN_FILE = CONF_DIR / "hidden.json"
EARLY_FILE = CONF_DIR / "early.json"
BT_AUDIO_FILE = CONF_DIR / "bt-audio"  # this device's own (not synced): the speaker or headphones chosen last
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
# seconds since boot; macOS has no CLOCK_BOOTTIME, its monotonic clock also starts at boot
BOOT_CLOCK = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)
PORT = int(os.environ.get("KINO_PORT", "8765"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# Matinee's page gets this secret as a cookie with the page itself; every other request needs it. Other programs on the
# computer (and other websites) do not have it, so they cannot use the backend and the accounts it holds.
UI_SECRET = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
# what the media servers send on through the backend (covers, pictures, their answers) is shown, never run:
# not taken for another type than it says, and if it were opened as a page, without scripts
PROXIED = [("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", "sandbox; default-src 'none'")]
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
TICKS = 10_000_000
# Retry intervals, shortened in the tests
RETRY_MAX = float(os.environ.get("KINO_RETRY_MAX", "30"))
FLUSH_SECONDS = float(os.environ.get("KINO_FLUSH_SECONDS", "20"))
# Stored reports the server keeps rejecting (not just unreachable) are given up after this long
PENDING_GIVE_UP = 24 * 3600
# "Still watching?" is asked after "ask" episodes in a row without a key press (see DEFAULTS); without an
# answer playback stops after ASK_TIMEOUT seconds. A movie paused for PAUSE_LIMIT seconds stops too, so its
# picture does not stand still on the TV for hours, unless the screensaver is off. Both shortened in the tests.
ASK_TIMEOUT = float(os.environ.get("KINO_ASK_TIMEOUT", "60"))
PAUSE_LIMIT = float(os.environ.get("KINO_PAUSE_LIMIT", str(15 * 60)))
# A stream that breaks off (network or server gone) looks to mpv like the end of the file once its own few seconds
# of trying again are over. More than CUT_OFF_MARGIN seconds before the end Matinee waits for the server instead, up to
# RECONNECT_FOR seconds, and goes on where the picture stopped.
CUT_OFF_MARGIN = 5
END_FILE_WAIT = 3  # seconds the music waits at most, at a new playlist position, for mpv to say why the last one ended
# (it says so at once mostly; on a busy computer it came after more than a second, and an episode heard to its end
# stayed unheard)
RECONNECT_EVERY = 3
RECONNECT_FOR = float(os.environ.get("KINO_RECONNECT_FOR", str(10 * 60)))


def read_kept(path):
    """config.json or state.json as a dict ({} if there is none). One a power cut left empty or half written is put
    aside as <name>.broken and the copy from before its last change (<name>.bak, see write_private) comes back in its
    place: a file that cannot be read must not stop Matinee from starting, again and again, with a black TV."""
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except OSError as e:
        if e.errno in (errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN, errno.EINTR):
            raise  # the file is fine, the moment is not: put aside, a change of it would be lost
        # no right to read it, a disk error: what is in it may be fine, so it stays where it is (not put aside as
        # broken, its copy from before not put in its place); that copy only read, for now
        print(f"{path.name} cannot be read ({e.strerror}): its copy from before taken for now", file=sys.stderr)
        try:
            data = json.loads(path.with_name(path.name + ".bak").read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
    except ValueError:
        pass
    print(f"{path.name} cannot be read: put aside as {path.name}.broken", file=sys.stderr)
    bak = path.with_name(path.name + ".bak")
    try:
        os.replace(path, path.with_name(path.name + ".broken"))
    except OSError:
        pass
    try:
        data = json.loads(bak.read_text())
        if isinstance(data, dict):
            # whole or not at all: display-mode reads it at the same moment as the backend (kino --session)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            shutil.copy2(bak, tmp)
            os.replace(tmp, path)
            print(f"{path.name}: the copy from before its last change taken instead", file=sys.stderr)
            return data
    except (OSError, ValueError):
        pass
    return {}


def read_conf():
    return read_kept(CONF_FILE)


# Without config.json (a fresh install) Matinee starts all the same and asks for the server and the sign-in on the TV
conf = read_conf()
SERVER = (conf.get("server") or "").rstrip("/")
state = read_kept(STATE_FILE)
state.setdefault("device_id", uuid.uuid4().hex)


def detect_version():
    """Shown under Dashboard → Devices in Jellyfin: the git commit Matinee runs from, if it is a checkout; a package's own
    version (release.py)."""
    if release.is_release(ROOT):
        return str(release.installed(ROOT).get("version") or "0.1")
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "0.1"
    return out.stdout.strip() or "0.1"


VERSION = detect_version()


def detect_commit():
    """The commit Matinee runs from (the devices' overview compares them), None outside a checkout or a package."""
    if release.is_release(ROOT):
        commit = str(release.installed(ROOT).get("commit", ""))
        return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if re.fullmatch(r"[0-9a-f]{40}", out.stdout.strip()) else None


COMMIT = detect_commit()


def detect_lang():
    """UI language (German or English): "language" in config.json, else KINO_LANG, else the system locale."""
    if conf.get("language") in ("de", "en"):
        return conf["language"]
    for var in ("KINO_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return "de" if os.environ[var].lower().startswith("de") else "en"
    return "en"


LANG = detect_lang()
STRINGS = {
    "en": {
        "Intro": "Skip Intro", "Recap": "Skip Recap", "Preview": "Skip Preview", "next": "Next Episode",
        "countdown": "Up next: {label}  ({left})",
        "reduced": "Slow connection: lower quality ({rate} Mbit/s)",
        "converted": "{what}: converted by Jellyfin for this device",
        "still": "Still watching?   OK ▸ Next Episode   Back ▸ Stop",
        "volume": "Volume: {v} %", "ends": "Ends at {time}", "playerMenu": "Tab or right click: audio & subtitles",
        "p_handoff": "Continue on", "p_handing": "Going on on {name}…",
        "p_info": "Info", "p_chapters": "Chapters", "p_audio": "Audio", "p_subtitles": "Subtitles", "p_off": "Off",
        "p_chapter": "Chapter {n}", "p_forced": "Forced", "p_speed": "Speed", "p_normal": "Normal", "p_decimal": ".",
        "p_find": "Find Subtitles…", "p_looking": "Looking for subtitles…", "p_none": "No subtitles found",
        "p_failed": "The search for subtitles did not work",
        "sub_getting": "Downloading subtitles…", "sub_added": "Subtitles added",
        "sub_failed": "These subtitles could not be downloaded",
        "lost": "Connection to the server lost, waiting for it…   Back ▸ Stop",
        "handoff_back": "{name} did not answer: going on here",
        "tv_left": "{min} min left", "tv_next": "Then at {time}: {title}", "own_decade": "Movies of the {decade}s",
        "sleep_off": "Sleep timer off", "sleep_min": "Sleep timer: {m} min", "sleep_end": "Sleep timer: at the end",
        "sleep_chapter": "Sleep timer: at the end of the chapter",
        "skipped_Intro": "Intro skipped", "skipped_Recap": "Recap skipped", "skipped_Preview": "Preview skipped",
        "sb_skip": "Skip {what}", "sb_skipped": "{what} skipped",
        "sb_sponsor": "Sponsor", "sb_selfpromo": "Self-promotion", "sb_interaction": "Reminder",
        "autooff": "Turning off in one minute   Any button ▸ Stay On",
    },
    "de": {
        "Intro": "Intro überspringen", "Recap": "Rückblick überspringen", "Preview": "Vorschau überspringen",
        "next": "Nächste Folge", "countdown": "Gleich geht's weiter: {label}  ({left})",
        "reduced": "Langsame Verbindung: geringere Qualität ({rate} Mbit/s)",
        "converted": "{what}: von Jellyfin für dieses Gerät umgewandelt",
        "still": "Schaust du noch?   OK ▸ Nächste Folge   Zurück ▸ Beenden",
        "volume": "Lautstärke: {v} %", "ends": "Endet um {time}", "playerMenu": "Tab oder Rechtsklick: Ton & Untertitel",
        "p_handoff": "Weiter auf", "p_handing": "Geht auf {name} weiter…",
        "p_info": "Info", "p_chapters": "Kapitel", "p_audio": "Audio", "p_subtitles": "Untertitel", "p_off": "Aus",
        "p_chapter": "Kapitel {n}", "p_forced": "Erzwungen", "p_speed": "Tempo", "p_normal": "Normal", "p_decimal": ",",
        "p_find": "Untertitel suchen…", "p_looking": "Suche Untertitel…", "p_none": "Keine Untertitel gefunden",
        "p_failed": "Die Suche nach Untertiteln hat nicht geklappt",
        "sub_getting": "Lade Untertitel herunter…", "sub_added": "Untertitel hinzugefügt",
        "sub_failed": "Diese Untertitel konnten nicht heruntergeladen werden",
        "lost": "Verbindung zum Server verloren, warte auf ihn…   Zurück ▸ Beenden",
        "handoff_back": "{name} antwortet nicht: geht hier weiter",
        "tv_left": "noch {min} Min.", "tv_next": "Danach um {time}: {title}", "own_decade": "Filme der {decade}er",
        "sleep_off": "Einschlaf-Timer aus", "sleep_min": "Einschlaf-Timer: {m} Min.", "sleep_end": "Einschlaf-Timer: am Ende",
        "sleep_chapter": "Einschlaf-Timer: am Ende des Kapitels",
        "skipped_Intro": "Intro übersprungen", "skipped_Recap": "Rückblick übersprungen",
        "skipped_Preview": "Vorschau übersprungen",
        "sb_skip": "{what} überspringen", "sb_skipped": "{what} übersprungen",
        "sb_sponsor": "Sponsor", "sb_selfpromo": "Eigenwerbung", "sb_interaction": "Abo-Erinnerung",
        "autooff": "Ausschalten in einer Minute   Beliebige Taste ▸ Anlassen",
    },
}
# Preferred audio and subtitle languages for mpv
TRACK_LANGS = {"de": ("de,deu,ger,en,eng", "de,deu,ger"), "en": ("en,eng", "en,eng")}


def t(key, **kw):
    return STRINGS[LANG][key].format(**kw)


_device_name = {}  # per language: the TV computer's name is in it


def device_name():
    """What this Matinee is called where another one offers to go on on it ("Continue on"), in Jellyfin's dashboard and
    when an account is handed over: "device_name" in config.json, else the TV computer's "TV" (in its language), a
    Mac's own name (System Settings → General → About), the host's elsewhere. In ASCII without quotes: it goes into
    the Authorization header, which Jellyfin's web server takes in ASCII only ("Anna’s" as "Anna's", "Küche" as
    "Kueche")."""
    if LANG not in _device_name:
        name = conf.get("device_name") if isinstance(conf.get("device_name"), str) else ""
        if not name.strip() and display.IN_CAGE:
            name = {"de": "Fernseher", "en": "TV"}[LANG]
        if not name.strip() and sys.platform == "darwin":
            try:
                name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                                      timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                name = ""
        _device_name[LANG] = ascii_name(name) or socket.gethostname()
    return _device_name[LANG]


def ascii_name(name):
    """A device's name as Jellyfin's Authorization header takes it: ASCII, no quotes, 64 characters at most."""
    for a, b in (("’", "'"), ("‘", "'"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"),
                 ("Ü", "Ue"), ("ß", "ss")):
        name = name.replace(a, b)
    return unicodedata.normalize("NFKD", name.strip()).encode("ascii", "ignore").decode().replace('"', "").strip()[:64]


# what read_kept falls back on: the configuration, and what was taken off rows
KEEP_BAK = (CONF_FILE, STATE_FILE, TV_FILE, HIDDEN_FILE)
# An answer that breaks off halfway, or is no HTTP at all (http.client's IncompleteRead, BadStatusLine): no OSError,
# so it is named beside it wherever something is fetched
CUT_SHORT = http.client.HTTPException
DISK_ERRORS = {errno.ENOSPC, errno.EDQUOT, errno.EROFS}  # nothing can be written


def write_private(path, text):
    """Write a file only the user can read, without ever leaving it half written: on the disk before it takes the
    old one's place (a power cut right after renaming could otherwise leave it empty). The files of KEEP_BAK keep the
    version before as <name>.bak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")  # each write its own
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if path in KEEP_BAK and path.exists():
        try:
            # only one that can be read is worth keeping, and never one with a password (older setups)
            if "password" in json.loads(path.read_text()):
                raise ValueError("not kept")
            bak_tmp = path.with_name(f"{path.name}.bak.{os.getpid()}.{threading.get_ident()}.tmp")
            bak_tmp.unlink(missing_ok=True)
            os.link(path, bak_tmp)
            os.replace(bak_tmp, path.with_name(path.name + ".bak"))
        except (OSError, ValueError):
            pass
    os.replace(tmp, path)
    try:  # the rename itself on the disk too
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def forget_kept():
    """The copies from before (write_private) gone too: after signing out or taking over another account, nothing of
    the account before may come back from them."""
    for path in KEEP_BAK:
        path.with_name(path.name + ".bak").unlink(missing_ok=True)


def save_state():
    write_private(STATE_FILE, json.dumps(state))


# Matinee's own JSON files (tv.json, hidden.json): changed only through changing(), by one at a time
# per file, so that two changes at once (a key, the sync from another thread) do not lose either
_file_locks, _file_locks_lock = {}, threading.Lock()


def file_lock(path):
    with _file_locks_lock:
        return _file_locks.setdefault(path, threading.RLock())


def read_json(path):
    """One of Matinee's JSON files as a dict ({} if there is none, or it cannot be read now); one of KEEP_BAK that a power
    cut left broken comes back from its copy from before (read_kept)."""
    try:
        if path in KEEP_BAK:
            return read_kept(path)
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def changing(path, load=None):
    """with changing(HIDDEN_FILE) as data: ... — the file read (with load, if given), changed in place by the
    block and written back whole if it changed; an exception in the block leaves it as it was."""
    with file_lock(path):
        # read_kept: a file that cannot be read at the moment (too many open) stops the change, rather than it being
        # written back empty
        data = load() if load else read_kept(path) if path in KEEP_BAK else read_json(path)
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            syncing.save_synced(path, json.dumps(data))


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import jellyfin_api, display, syncing  # noqa: E402,F401
