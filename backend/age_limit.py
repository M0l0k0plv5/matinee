"""The age limit (settings → System, "maxage"): films and shows rated above it are left out of everything Matinee shows of
Jellyfin (lists, search, similar titles, Up Next, an item's own page) and are not played. Of the device, kept with the
simple settings' code: while those are on it cannot be changed. Works with any Jellyfin account, no administrator's
rights needed; a title without a rating stays, as with Jellyfin's own limit unless told to block those."""
import re
import threading

from .core import conf, state
from .jellyfin_api import jf

AGE_LIMITS = ("off", "0", "6", "12", "16")
# the ages of ratings that are no number (a number, "FSK-12", "DE-12", "12A": that age); Jellyfin's own tables for
# the US and the UK
RATING_AGES = {"G": 0, "PG": 10, "PG-13": 13, "R": 17, "NC-17": 18, "TV-Y": 0, "TV-Y7": 7, "TV-Y7-FV": 7, "TV-G": 0,
               "TV-PG": 10, "TV-14": 14, "TV-MA": 17, "U": 0, "UC": 0, "X": 18, "XXX": 18, "AO": 18,
               "APPROVED": 0, "E": 0, "EC": 0}
NOT_RATED = {"NR", "UR", "UNRATED", "NOT RATED", "NOTRATED", "", "?"}
RATED_TYPES = {"Movie", "Series", "Season", "Episode", "Trailer", "MusicVideo", "Video"}
AGE_IN = re.compile(r"(?:^|[^0-9])(\d{1,2})\+?[A-Z]?$")
series_ages = {}  # a series' age by its Id, for its seasons and episodes without a rating of their own
series_lock = threading.Lock()


def age_limit():
    """The highest age shown, or None: no limit."""
    v = str(conf.get("maxage", "off"))
    return int(v) if v in AGE_LIMITS and v != "off" else None


def rating_age(rating):
    """The age a rating stands for ("FSK-12", "DE-16", "PG-13", "TV-MA", "18"), None for none or one not known."""
    r = str(rating or "").strip().upper()
    if r in NOT_RATED:
        return None
    if r in RATING_AGES:
        return RATING_AGES[r]
    r = re.sub(r"^[A-Z]{2}[-: ]", "", r)  # "DE-12", "GB-15": the country
    if r in RATING_AGES:
        return RATING_AGES[r]
    m = AGE_IN.search(r)
    return int(m.group(1)) if m else None


def series_age(series_id):
    with series_lock:
        if series_id in series_ages:
            return series_ages[series_id]
    try:
        age = rating_age(jf(f"/Items/{series_id}?userId={state['user_id']}").get("OfficialRating"))
    except Exception:
        return None  # not known now: asked again the next time
    with series_lock:
        series_ages[series_id] = age
    return age


def item_age(item):
    age = rating_age(item.get("OfficialRating"))
    if age is None and item.get("Type") in ("Season", "Episode") and item.get("SeriesId"):
        age = series_age(item["SeriesId"])
    return age


def allowed(item, limit=None):
    """Whether an item may be shown and played under the limit (of the settings, unless given)."""
    limit = age_limit() if limit is None else limit
    if limit is None or not isinstance(item, dict) or item.get("Type") not in RATED_TYPES:
        return True
    age = item_age(item)
    return age is None or age <= limit


def limited(data):
    """Jellyfin's answer as JSON, with what is above the limit left out: of a list (and its count), an "Items" list,
    or the item itself (None then: not to be shown)."""
    limit = age_limit()
    if limit is None:
        return data
    if isinstance(data, list):
        return [x for x in data if allowed(x, limit)]
    if not isinstance(data, dict):
        return data
    if isinstance(data.get("Items"), list):
        kept = [x for x in data["Items"] if allowed(x, limit)]
        gone = len(data["Items"]) - len(kept)
        data = {**data, "Items": kept}
        if gone and isinstance(data.get("TotalRecordCount"), int):
            data["TotalRecordCount"] = max(0, data["TotalRecordCount"] - gone)
        return data
    return data if allowed(data, limit) else None


def forget_series_ages():
    """The library changed (or the person did): the series' ratings looked up again."""
    with series_lock:
        series_ages.clear()

