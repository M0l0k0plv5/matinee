"""The weather on the start page (Open-Meteo)."""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import core
from .core import conf, CUT_SHORT
from .jellyfin_api import log_net
from .preferences import setting


# The weather under the screensaver's clock, from Open-Meteo (no account, no key): for the place "weather" in
# config.json (a town's name), found once with Open-Meteo's geocoding; only the place's coordinates go out
OPEN_METEO = os.environ.get("KINO_OPEN_METEO", "https://api.open-meteo.com")  # the tests use their own
GEOCODING = os.environ.get("KINO_GEOCODING", "https://geocoding-api.open-meteo.com")
WEATHER_SECONDS = 900
weather_cache = {}  # "place": (lat, lon, name); "now": (time, weather)


def valid_place(p):
    """A place as the settings store it: {"name", "lat", "lon"}."""
    try:
        return (isinstance(p, dict) and isinstance(p.get("name"), str) and 0 < len(p["name"]) <= 100
                and -90 <= float(p["lat"]) <= 90 and -180 <= float(p["lon"]) <= 180)
    except (KeyError, TypeError, ValueError):
        return False


def place_name():
    """The weather's place as config.json has it: a town's name, or one chosen in the settings."""
    w = conf.get("weather")
    return w["name"] if valid_place(w) else str(w or "").strip()


def find_places(name):
    """Places of that name for the settings, from Open-Meteo's geocoding: [{name, region, lat, lon}]."""
    q = urllib.parse.urlencode({"name": name, "count": 8, "language": core.LANG, "format": "json"})
    with urllib.request.urlopen(f"{GEOCODING}/v1/search?{q}", timeout=5) as r:
        found = json.loads(r.read()).get("results") or []
    return [{"name": str(x["name"]), "region": ", ".join(str(x[k]) for k in ("admin1", "country") if x.get(k)),
             "lat": float(x["latitude"]), "lon": float(x["longitude"])} for x in found if "latitude" in x and "longitude" in x]


def weather():
    """{place, temp, code (WMO), day, hi, lo} for now, kept for a quarter of an hour; None without a place, with
    the weather turned off in the settings, or when Open-Meteo cannot be reached."""
    place = place_name()
    if not place or setting("saverweather") == "off":
        return None
    hit = weather_cache.get("now")
    if hit and hit[1].get("q") == place and time.time() - hit[0] < WEATHER_SECONDS:
        return hit[1]["w"]
    try:
        where = weather_cache.get("place")
        chosen = conf.get("weather")
        if valid_place(chosen):  # chosen in the settings: where it is is known
            where = (float(chosen["lat"]), float(chosen["lon"]), chosen["name"], place)
        elif not where or where[3] != place:
            q = urllib.parse.urlencode({"name": place, "count": 1, "language": core.LANG, "format": "json"})
            with urllib.request.urlopen(f"{GEOCODING}/v1/search?{q}", timeout=5) as r:
                found = (json.loads(r.read()).get("results") or [None])[0]
            if not found:
                print(f"Weather: no place called {place!r}", file=sys.stderr)
                return None
            where = weather_cache["place"] = (float(found["latitude"]), float(found["longitude"]), str(found["name"]), place)
        q = urllib.parse.urlencode({"latitude": where[0], "longitude": where[1], "current": "temperature_2m,weather_code,is_day",
                                    "daily": "temperature_2m_max,temperature_2m_min", "timezone": "auto", "forecast_days": 1})
        with urllib.request.urlopen(f"{OPEN_METEO}/v1/forecast?{q}", timeout=5) as r:
            d = json.loads(r.read())
        now, day = d["current"], d.get("daily") or {}
        w = {"place": where[2], "temp": round(float(now["temperature_2m"])), "code": int(now["weather_code"]),
             "day": bool(now.get("is_day", 1)), "hi": round(float(day["temperature_2m_max"][0])),
             "lo": round(float(day["temperature_2m_min"][0]))}
    except (OSError, CUT_SHORT, ValueError, KeyError, IndexError, TypeError) as e:
        log_net(f"Weather not available: {e}", e)
        return hit[1]["w"] if hit and hit[1].get("q") == place else None  # the last one known, rather than none
    weather_cache["now"] = (time.time(), {"q": place, "w": w})
    return w


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import tv  # noqa: E402,F401
