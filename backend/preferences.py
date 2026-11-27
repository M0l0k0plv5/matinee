"""The settings page: what may be changed, to which values, saving them; the PIN of the simple mode."""
import hashlib
import hmac
import os
import re
import secrets
import time

from . import core, playback
from .core import conf, CONF_FILE, detect_lang
from .age_limit import AGE_LIMITS, forget_series_ages
from .jellyfin_api import conf_changing
from .sound import AUDIO_SYNC, SYNC_DEVICE


# What the settings page may change, and to which values
SETTINGS = {"language": core.LANGS, "quality": ("auto", "original", "20", "8", "4", "2"),
            "screensaver": ("off", "5", "10", "30"), "ask": ("off", "2", "3", "5"),
            "audio": ("auto", *core.LANGS, "original"), "subtitles": ("off", "auto", "always"),
            "sublang": ("auto", *core.LANGS), "framerate": ("off", "on"), "saverweather": ("on", "off"),
            "sleepend": ("stop", "poweroff"), "scrobble": ("on", "off"),
            "moviesound": ("level", "original"), "subsize": ("s", "m", "l", "xl"), "autooff": ("off", "2", "4", "8"),
            "startwith": ("menu", "last", "live"), "autoupdate": ("off", "on"), "updatecheck": ("auto", "manual"),
            "skipintro": ("off", "offer", "auto"), "substyle": ("outline", "box"), "phoneremote": ("off", "on"),
            "spoilers": ("show", "hide"), "sounds": ("off", "low", "medium", "high"),
            "soundkind": ("click", "tap", "wood", "glass", "soft"), "badges": ("all", "missing", "off"),
            "effects": ("auto", "full", "calm", "light"), "textsize": ("normal", "large"), "motion": ("full", "less"), "size": ("auto", "tv", "desk"),
            "sync": ("on", "off"), "maxage": AGE_LIMITS}
# Without an entry in config.json: screensaver after 10 minutes, "Still watching?" after 3 episodes,
# audio in Matinee's language, no subtitles until turned on in the player (for that playback)
DEFAULTS = {"screensaver": 10, "ask": 3, "audio": "auto", "subtitles": "off", "sublang": "auto", "framerate": "on",
            "saverweather": "on", "sleepend": "stop", "scrobble": "on",
            "moviesound": "level", "subsize": "m", "autooff": "off", "startwith": "menu", "autoupdate": "off", "updatecheck": "auto", "phoneremote": "off",
            "skipintro": "offer", "substyle": "outline", "spoilers": "show", "sounds": "medium", "soundkind": "click",
            "badges": "all", "effects": "auto", "textsize": "normal", "motion": "full",
            "size": "auto", "sync": "on", "maxage": "off"}
SUB_SCALE = {"s": 0.8, "m": 1.0, "l": 1.25, "xl": 1.5}  # subsize: the subtitles' size, as mpv's --sub-scale
# substyle "box": white letters without their outline on a dark box (70 %), a little room around them, easier to read
# on bright pictures; styled subtitles (ASS) keep their own look
SUB_BOX = ["--sub-border-style=background-box", "--sub-back-color=#B4000000", "--sub-outline-color=#00000000",
           "--sub-border-size=10", "--sub-shadow-offset=0"]


# Films are mixed far quieter than music, with whispers next to explosions, and 5.1 brought down to stereo
# puts the voices (the centre) lower still: "level" lifts the quiet parts, a few seconds at a time, up to 10 times
LEVEL_FILTER = "lavfi=[dynaudnorm=f=250:g=15:p=0.9:m=10]"


def movie_sound():
    return [f"--af={LEVEL_FILTER}"] if setting("moviesound") == "level" else []


# choices that are no more, as what took their place: effects "plain" and "black" are "light" (Reduced), starting
# with public TV is starting with the menu
SETTING_WAS = {"effects": {"plain": "light", "black": "light"}, "startwith": {"tv": "menu"}}


def setting(key):
    value = conf.get(key, DEFAULTS[key])
    return SETTING_WAS.get(key, {}).get(value, value)


def ui_size_auto():
    """How large the page is drawn with the setting "size" on Automatic: "tv" for the sofa on the TV computer, "desk"
    (smaller, more on the screen) on a Mac or laptop, seen from close by."""
    return os.environ.get("KINO_SIZE_AUTO") or ("tv" if display.IN_CAGE else "desk")  # the tests': a television's


def sound_level():
    """The navigation clicks' level; "on", from before there were levels, is the middle one."""
    level = setting("sounds")
    return "medium" if level == "on" else level


def save_settings(changes):
    """Validate and store settings from the UI in config.json; they apply right away."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("nothing to change")
    if "maxage" in changes and simple_on():
        raise ValueError("locked")  # the age limit: kept with the simple settings' code
    for key, value in changes.items():
        if key == "display" and display.IN_CAGE and str(value) in ("auto", "native", *display.display_choices()):
            continue
        if key == "weather" and (value == "" or forecast.valid_place(value)):
            continue
        if key == "tvoff" and isinstance(value, list) and tv.tv_off_ok(value):
            continue  # the channels left out of their rows and zapping (live ones, and those made of the library)
        if key == "audiosync" and isinstance(value, dict) and SYNC_DEVICE.fullmatch(str(value.get("device", ""))) \
                and str(value.get("ms")) in AUDIO_SYNC:
            continue  # for one sound output
        if key not in SETTINGS or str(value) not in SETTINGS[key]:
            raise ValueError(f"invalid setting {key}")
    with conf_changing() as c:
        for key, value in changes.items():
            if key == "weather":  # a place chosen in the settings: its name and where it is
                c[key] = conf[key] = {"name": value["name"], "lat": float(value["lat"]), "lon": float(value["lon"])} if value else ""
                continue
            if key == "tvoff":
                c[key] = conf[key] = sorted(set(map(str, value)))
                continue
            if key == "audiosync":
                delays = {**c.get(key, {}), value["device"]: int(value["ms"])}
                c[key] = conf[key] = {d: ms for d, ms in delays.items() if ms}
                continue
            value = str(value)
            c[key] = int(value) if value.isdigit() else value
            conf[key] = c[key]
    core.LANG = detect_lang()
    playback.QUALITY = str(conf.get("quality", "auto"))
    if "maxage" in changes:
        forget_series_ages()
        tv.own_forget()
    if "display" in changes:
        display.apply_display(str(changes["display"]))
    if "phoneremote" in changes:
        phone_remote.apply()
    syncing.sync.changed(CONF_FILE)


def synced_setting(key):
    """A setting of the person's, kept alike on every device: not those of the device (its screen, effects, updates,
    sound output, …), nor whether it syncs at all."""
    return key in SETTINGS and key not in account.DEVICE_KEYS and key != "sync" or key in ("tvoff", "weather")


def synced_setting_ok(key, value):
    """A setting from another device that this one can take: one it knows, with a value it allows."""
    if key == "tvoff":
        return isinstance(value, list) and tv.tv_off_ok(value)
    if key == "weather":  # the screensaver's place, as the settings store it
        return value == "" or forecast.valid_place(value)
    return synced_setting(key) and isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) in SETTINGS[key]


def take_synced_settings(c):
    """config.json as the sync wrote it: its person's settings in use at once (the backend's; the page's own at its
    next start)."""
    for key in list(conf):
        if synced_setting(key) and key not in c:
            del conf[key]
    for key, value in c.items():
        if synced_setting(key):
            conf[key] = value
    core.LANG = detect_lang()


# The simple settings, for someone who should not have to see servers, accounts or the network: only what is everyday
# there. Turned on with a code of four digits, which turning them off asks for again; only its salted hash is kept,
# as "simple" in config.json (a code forgotten: remove that). Of the device, not of the person: not moved with an account.
SIMPLE_PIN = re.compile(r"\d{4}")


def simple_on():
    return isinstance(conf.get("simple"), dict)


def pin_hash(pin, salt):
    return hashlib.sha256(f"{salt}:{pin}".encode()).hexdigest()


def set_simple(body):
    """Turn the simple settings on with a new code, or off with the code they were turned on with."""
    on, pin = body.get("on") if isinstance(body, dict) else None, str(body.get("pin", "")) if isinstance(body, dict) else ""
    if not isinstance(on, bool) or not SIMPLE_PIN.fullmatch(pin):
        raise ValueError("invalid")
    if on == simple_on():
        raise ValueError("already")
    with conf_changing() as c:
        if on:
            salt = secrets.token_hex(8)
            c["simple"] = conf["simple"] = {"salt": salt, "pin": pin_hash(pin, salt)}
        else:
            kept = conf["simple"]
            if not hmac.compare_digest(pin_hash(pin, str(kept.get("salt", ""))), str(kept.get("pin", ""))):
                time.sleep(1)  # not tried through quickly
                raise PermissionError("pin_wrong")
            c.pop("simple", None)
            conf.pop("simple", None)


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import display, forecast, tv, syncing, account, phone_remote  # noqa: E402,F401
