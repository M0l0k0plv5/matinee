"""One's own channels: the Jellyfin library as television, with a programme guide and live tiles."""
import bisect
import hashlib
import os
import re
import threading
import time
from datetime import datetime

from . import age_limit
from .core import changing, conf, read_json, state, t, TICKS, TV_FILE
from .jellyfin_api import jf, log_net


# ---------- One's own channels: the Jellyfin library as television ----------
# A channel for each of the library's larger genres (their films, and the episodes of their series) and for each
# decade of its films. Each runs its programme without end, whether anyone watches or not: all of its films and
# episodes in a fixed order, begun anew when it is through. Tuning in joins what runs where it is by now, like a
# channel of television; nothing of it is told to Jellyfin (no position kept, nothing marked as watched).
#
# The order holds when the library grows: a film has its place by its id, a series' episodes are spread evenly over
# the whole round in their own order, so a new film or episode only moves in among the others.
OWN_MIN = int(os.environ.get("KINO_OWN_MIN", 12))  # films and episodes a channel needs
OWN_GENRES = 8             # the genres with the most of them
OWN_KEEP = 6 * 3600        # the library is looked at again after this
OWN_SINCE = 1_700_000_000  # when every channel began its first round
# A library filled in two languages has a genre under both names ("Crime" and "Krimi"): one channel, under the name
# more of its films and series carry
OWN_SAME = {"krimi": "crime", "komödie": "comedy", "abenteuer": "adventure", "sci-fi": "science fiction",
            "science-fiction": "science fiction", "dokumentarfilm": "documentary", "dokumentation": "documentary",
            "doku": "documentary", "familie": "family", "familienfilm": "family", "liebesfilm": "romance",
            "romantik": "romance", "kriegsfilm": "war", "krieg": "war", "historie": "history", "geschichte": "history",
            "musik": "music", "zeichentrick": "animation", "kinder": "kids", "kinderfilm": "kids", "children": "kids"}
OWN_ID = re.compile(r"own-[\w-]{1,60}")
OWN_LATE = 600             # s: a programme begun no longer ago than this is played from its start after the one before
own_lock = threading.Lock()
own_first = threading.Lock()  # the first look at the library: one at a time
own_epoch = 0  # counted up by own_forget: a look begun before it keeps what it makes to itself
own_cache = {}  # "channels": [{"id", "name", "items", "ends", "total"}], "until" (they are looked for again), "looking"


def is_own(live_id):
    return str(live_id).startswith("own-")


def own_place(seed, key):
    """A number from 0 to 1 that is always the same for a channel and a film or series."""
    return int(hashlib.sha1(f"{seed}\t{key}".encode()).hexdigest()[:12], 16) / 16 ** 12


def own_lineup(channel_id, movies, shows):
    """A channel's round: its films each at their own place, each series' episodes evenly over it in their order."""
    placed = [(own_place(channel_id, m["jf"]), m) for m in movies]
    for series, episodes in shows.items():
        shift = own_place(channel_id, series)
        placed += [(((n + shift) / len(episodes)) % 1, e) for n, e in enumerate(episodes)]
    placed.sort(key=lambda x: (x[0], x[1]["jf"]))
    return [x for _, x in placed]


def own_build():
    """The channels the library gives, from two questions to Jellyfin (films and series, then all episodes)."""
    user = state["user_id"]
    limit = age_limit.age_limit()  # nothing above the age limit on them, not even in the guide
    found = jf(f"/Items?userId={user}&IncludeItemTypes=Movie,Series&Recursive=true&Fields=Genres,Overview,OfficialRating"
               "&EnableImageTypes=Primary,Backdrop&EnableUserData=false")["Items"]
    found = [x for x in found if age_limit.allowed(x, limit)]
    series = {x["Id"]: x for x in found if x.get("Type") == "Series"}
    episodes = jf(f"/Items?userId={user}&IncludeItemTypes=Episode&Recursive=true&IsMissing=false&Fields=Overview,OfficialRating"
                  "&EnableImageTypes=Primary&EnableUserData=false")["Items"] if series else []
    # an episode of a series kept: above the limit only with a rating of its own
    episodes = [e for e in episodes if limit is None or (age_limit.rating_age(e.get("OfficialRating")) or 0) <= limit]
    picture = lambda x, kind: f"/jf/Items/{x['Id']}/Images/{kind}?maxWidth=960&quality=90"
    seconds = lambda x: (x.get("RunTimeTicks") or 0) / TICKS
    movies = [{"jf": x["Id"], "title": x.get("Name") or "", "sub": str(x.get("ProductionYear") or ""),
               "about": (x.get("Overview") or "")[:600], "seconds": seconds(x), "year": x.get("ProductionYear"),
               "genres": x.get("Genres") or [], "picture": picture(x, "Backdrop") if x.get("BackdropImageTags") else None}
              for x in found if x.get("Type") == "Movie" and seconds(x) > 0]
    shows = {}  # series → its episodes in their order, the specials left out
    for e in sorted(episodes, key=lambda e: (e.get("ParentIndexNumber") or 0, e.get("IndexNumber") or 0, e["Id"])):
        if e.get("SeriesId") in series and seconds(e) > 0 and e.get("ParentIndexNumber") != 0 and e.get("LocationType") != "Virtual":
            number = f"S{e.get('ParentIndexNumber') or 0:02}E{e.get('IndexNumber') or 0:02}"
            shows.setdefault(e["SeriesId"], []).append({
                "jf": e["Id"], "title": e.get("SeriesName") or series[e["SeriesId"]].get("Name") or "",
                "sub": f"{number} · {e.get('Name') or ''}", "about": (e.get("Overview") or "")[:600], "seconds": seconds(e),
                "picture": picture(e, "Primary") if (e.get("ImageTags") or {}).get("Primary") else None})
    wanted = []  # (id, name, films, series)
    genres, names = {}, {}  # by a genre's one name (OWN_SAME): its films and series; how often each of its names is used
    one = lambda g: OWN_SAME.get(g.strip().lower(), g.strip().lower())
    for m in movies:
        for g in {one(g): g for g in m["genres"]}.values():  # under both names: once
            genres.setdefault(one(g), ({}, {}))[0][m["jf"]] = m
            names.setdefault(one(g), {}).setdefault(g, []).append(1)
    for sid, eps in shows.items():
        for g in {one(g): g for g in series[sid].get("Genres") or []}.values():
            genres.setdefault(one(g), ({}, {}))[1][sid] = eps
            names.setdefault(one(g), {}).setdefault(g, []).extend(eps)
    size = lambda g: len(genres[g][0]) + sum(map(len, genres[g][1].values()))
    for g in sorted(genres, key=lambda g: (-size(g), g))[:OWN_GENRES]:
        name = max(sorted(names[g]), key=lambda n: len(names[g][n]))
        wanted.append((f"own-g-{re.sub(r'[^\w]+', '-', g).strip('-')[:40]}", name, list(genres[g][0].values()), genres[g][1]))
    decades = {}
    for m in movies:
        if isinstance(m["year"], int):
            decades.setdefault(m["year"] // 10 * 10, []).append(m)
    for d in sorted(decades, reverse=True):
        wanted.append((f"own-d-{d}", t("own_decade", decade=d), decades[d], {}))
    channels = []
    for cid, name, films, with_series in wanted:
        items = [{k: x[k] for k in ("jf", "title", "sub", "about", "seconds", "picture")}
                 for x in own_lineup(cid, films, with_series)]
        if len(items) < OWN_MIN or cid in {c["id"] for c in channels}:
            continue
        ends, total = [], 0.0
        for x in items:
            total += x["seconds"]
            ends.append(total)
        channels.append({"id": cid, "name": name, "items": items, "ends": ends, "total": total})
    return channels


def own_channels(wait=True):
    """One's own channels (see above), [] without a library. The first look is waited for (one at a time: the others
    take what it made); later ones (every few hours, or some minutes after one that failed) are made aside while the
    channels known go on. What a look made counts only for the library it was made of (own_epoch, see own_forget).
    wait=False: not waited for at all (the settings), [] until they are made aside."""
    def look(epoch):
        try:
            channels = own_build()
            again = OWN_KEEP
        except Exception as e:  # any: a library of another shape must not take the pages with it
            log_net(f"One's own channels not made: {e!r}", e)
            channels, again = None, OWN_RETRY
        with own_lock:
            if epoch != own_epoch:  # another user or server meanwhile: not theirs
                return
            if channels is None:
                channels = own_cache.get("channels", [])
            own_cache.update(channels=channels, until=time.monotonic() + again, looking=False)

    if not state.get("user_id"):  # not signed in (yet): asked again the next time, not held to be none
        return []
    with own_lock:
        if "channels" in own_cache:
            if time.monotonic() > own_cache["until"] and not own_cache.get("looking"):
                own_cache["looking"] = True
                threading.Thread(target=look, args=(own_epoch,), daemon=True).start()
            return own_cache["channels"]
    if not wait:
        threading.Thread(target=own_channels, daemon=True).start()  # made aside: the next ask has them
        return []
    with own_first:
        with own_lock:
            made, epoch = "channels" in own_cache, own_epoch  # read once it is this one's turn (own_forget meanwhile)
        if not made:
            look(epoch)
    with own_lock:
        return own_cache.get("channels", [])


def own_forget():
    """Made again at the next look: another user or server signed in (their library, not the one before's), or the
    age limit changed. A look under way for the one before keeps what it makes to itself."""
    global own_epoch
    with own_lock:
        own_epoch += 1
        own_cache.clear()


def tv_own(hidden=False, wait=True):
    """One's own channels as the lists show them: [{"id", "name"}], without those left out in the settings (hidden:
    with them, for the settings). wait: see own_channels."""
    off = () if hidden else conf.get("tvoff", [])
    return [{"id": c["id"], "name": c["name"]} for c in own_channels(wait) if c["id"] not in off]


def own_at(channel, when):
    """Which of a channel's programmes runs at a time, and when that one began: (its number, start)."""
    into = (when - OWN_SINCE) % channel["total"]
    n = min(bisect.bisect_right(channel["ends"], into), len(channel["items"]) - 1)
    return n, when - into + (channel["ends"][n - 1] if n else 0.0)


def own_guide(start, end):
    """The programmes of one's own channels between two times: {channel id: [programme,…]}, as the other guides."""
    guide = {}
    for c in own_channels():
        n, at = own_at(c, start)
        while at < end:
            x = c["items"][n]
            guide.setdefault(c["id"], []).append({"title": x["title"], "sub": x["sub"], "about": x["about"], "start": at,
                                                  "end": at + x["seconds"], "picture": x["picture"], "jf": x["jf"]})
            at += x["seconds"]
            n = (n + 1) % len(c["items"])
    return guide


def own_on(channel_id, after=None):
    """What to play on one of one's own channels: (programme, seconds into it). Tuning in: what runs now, where it
    is. after: the film or episode that just ended there; then the one after it from its start, when playback is
    ahead of the channel (after seeking: the one ended still runs, or comes later in the round) or only a little
    behind it (after a pause: the next one began a short while ago); otherwise as tuning in. LookupError for a channel
    there is not."""
    c = next((c for c in own_channels() if c["id"] == channel_id), None)
    if not c:
        raise LookupError(f"no channel {channel_id}")
    items, now = c["items"], time.time()
    n, began = own_at(c, now)
    ended = next((k for k, x in enumerate(items) if x["jf"] == after), None) if after is not None else None
    if ended is not None:
        ahead = (ended - n) % len(items)  # by how many programmes; more than half the round: behind
        if ahead < len(items) / 2:
            return items[(ended + 1) % len(items)], 0.0
        if n == (ended + 1) % len(items) and now - began <= OWN_LATE:
            return items[n], 0.0
    return items[n], now - began


# a library that could not be looked at is looked at again after this
OWN_RETRY = float(os.environ.get("KINO_OWN_RETRY", 300))
# the channels are made ahead, a little after the start and again before they are old: nobody waits for them
OWN_WARM_AFTER = float(os.environ.get("KINO_OWN_WARM_AFTER", 5))


def own_warm_loop():
    time.sleep(OWN_WARM_AFTER)
    while True:
        try:
            own_channels()
        except Exception as e:  # any: an answer of another shape must not end the making ahead for good
            log_net(f"One's own channels not made ahead: {e!r}", e)
        time.sleep(OWN_KEEP - 300)


def tv_off_ok(ids):
    """Whether these may be the channels left out in the settings: made of the library (which another device may
    have and this one not)."""
    return all(isinstance(i, str) and OWN_ID.fullmatch(i) for i in ids)


def tv_live_channel(live_id):
    """One of one's own channels by its id: (id, name, "") — what runs there is found when it is played
    (Player.own_tune) — or None."""
    if not is_own(live_id):
        return None
    return next(((c["id"], c["name"], "") for c in own_channels() if c["id"] == live_id), None)


def tv_guides():
    """The programmes of each channel, in order, from four hours ago to fourteen ahead: {live id: [programme,…]}."""
    now = time.time()
    return own_guide(now - 4 * 3600, now + 14 * 3600)


LIVE_TITLE_WAIT = 1.5  # s: a channel handed over waits so long at most for what is on it to be its title


def tv_now():
    """What runs on each channel now, and what comes next: {live id: {"title", "sub", "about", "start", "end",
    "picture", "next"}}."""
    now = time.time()
    result = {}
    for live_id, items in tv_guides().items():
        for n, x in enumerate(items):
            if x["start"] <= now < x["end"]:
                after = next((y for y in items[n + 1:] if y["start"] >= x["end"] - 60), None)
                result[live_id] = {**x, "next": after and {"title": after["title"], "start": after["start"]}}
                break
    return result


def tv_guide_grid():
    """What the channels show from an hour ago to eight hours ahead, for the guide: {"now", "channels": [{"id",
    "name", "items": [{"title", "sub", "start", "end", "jf"}]}]} ("jf": the film or episode it is)."""
    now = time.time()
    start, end = now - 3600, now + 8 * 3600
    guides = tv_guides()
    rows = []
    for c in tv_own():
        items = [{k: x[k] for k in ("title", "sub", "start", "end", "jf") if k in x} for x in guides.get(c["id"], [])
                 if x["end"] > start and x["start"] < end]
        rows.append({"id": c["id"], "name": c["name"], "items": items})
    return {"now": now, "channels": rows}


def tv_info(channel, now):
    """What the player shows of a channel live: what runs, until when, and what comes next."""
    at = lambda s: datetime.fromtimestamp(s).strftime("%H:%M")
    left = max(1, round((now["end"] - time.time()) / 60))
    lines = [f"{channel}  ·  LIVE", now["title"] + (f"  –  {now['sub']}" if now.get("sub") else ""),
             f"{at(now['start'])} – {at(now['end'])}   {t('tv_left', min=left)}"]
    if now.get("next"):
        lines.append(t("tv_next", time=at(now["next"]["start"]), title=now["next"]["title"]))
    return "\n".join(lines)


def tv_remember_live(live_id):
    """The channel watched last: the live row starts on it, and the play key turns it on."""
    with changing(TV_FILE) as data:
        if data.get("lastLive") != live_id:
            data["lastLive"] = live_id


def load_tv():
    """What Matinee keeps of the channels: {"lastLive": the channel watched last}."""
    return read_json(TV_FILE)
