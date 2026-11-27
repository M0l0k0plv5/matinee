"use strict";
// Matinee's page, part 1 of 18: the API, formatting, the background and the building blocks every page uses.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const TICKS = 10_000_000;
const JSON_HEADERS = { "Content-Type": "application/json" };  // the backend rejects anything else
const FIELDS = "Fields=Overview,Genres,ProductionYear,OfficialRating,CommunityRating,RunTimeTicks,ChildCount&EnableImageTypes=Primary,Backdrop,Logo,Thumb&ImageTypeLimit=1";
let USER = null;
let reg = [];          // items of the current view, referenced via data-r
let stack = [];        // navigation history
let current = null;

/* ---------- API ---------- */

// Every render gets a number. A slower, older render (e.g. a live refresh still loading when
// something is opened) must not overwrite the newer page or mix up its items afterwards:
// its pending requests throw STALE and it stops before touching the page.
let renderGen = 0, busyTimer;
const STALE = new Error("stale render"), SIGNED_OUT = new Error("signed out");
// A new page cancels what the one before still asks for (its answers would only be thrown away), so the browser's
// few connections to the backend are free for the new one at once. And a request that hangs (the network gone, the
// packets lost on the way) gives up after a while: it held the spinner for 20 seconds before anything was said.
let renderAbort = new AbortController();
const API_TIMEOUT = 10000;
// an error for the log only: the page that failed says it in plain words, not with the request's address
const technical = (message) => Object.assign(new Error(message), { technical: true });

async function api(path) {
  const gen = renderGen;
  let r;
  try {
    r = await fetch("/jf" + path, { signal: AbortSignal.any([renderAbort.signal, AbortSignal.timeout(API_TIMEOUT)]) });
  } catch (e) {
    if (e === STALE || gen !== renderGen) throw STALE;
    throw technical(e?.name === "TimeoutError" ? `timeout ${path}` : `${e?.message ?? e} ${path}`);
  }
  // the backend is no longer signed in (e.g. the device was removed in Jellyfin): the page says what to do where it
  // stands and comes back by itself once that is done (render), with the way back as it was. The page loaded anew
  // for it, and every page before it was gone.
  if (r.status === 503 || r.status === 401) {
    USER = null;
    throw SIGNED_OUT;
  }
  if (!r.ok) throw technical(`${r.status} ${path}`);
  const data = await r.json();
  if (gen !== renderGen) throw STALE;
  return data;
}
addEventListener("unhandledrejection", (e) => { if (e.reason === STALE || e.reason === SIGNED_OUT) e.preventDefault(); });

// "More like this": the backend knows which titles are similar ahead of time (Jellyfin needs 1-3 s for
// a title it has not looked at recently); the titles themselves are loaded fresh, in that order. The page
// does not wait for it: the row is filled in once it arrives. Seen rows are kept; after a change on the
// server the kept one is still shown right away (no jumping focus) while a fresh one loads for next time.
const similarCache = new Map(); // id → { p: Promise, stale }
async function loadSimilar(id) {
  const r = await fetch(`/similar/${id}`);
  if (!r.ok) return null;
  const { ids } = await r.json();
  if (!ids.length) return { Items: [] };
  const res = await fetchJSON(`/Items?userId=${USER}&Ids=${ids.join(",")}&${FIELDS}`);
  if (!res) return null;
  const byId = new Map(res.Items.map((it) => [it.Id, it]));
  return { Items: ids.map((i) => byId.get(i)).filter(Boolean) };
}
function similarOf(id) {
  const load = () => loadSimilar(id).catch(() => null);
  const entry = similarCache.get(id);
  if (!entry) {
    const p = load().then((res) => { if (!res) similarCache.delete(id); return res ?? { Items: [] }; });
    similarCache.set(id, { p });
    if (similarCache.size > 300) similarCache.delete(similarCache.keys().next().value); // runs for weeks on a TV
    return p;
  }
  if (entry.stale) {
    entry.stale = false;
    load().then((res) => { if (res) similarCache.set(id, { p: Promise.resolve(res) }); });
  }
  return entry.p;
}

// Resting on a movie or show for a moment loads its row ahead, so it is usually there on opening.
// One request at a time and only after a pause, so holding an arrow key does not flood the server.
let warmTimer, warming = false, warmNext = null;
function warmSimilar(el) {
  clearTimeout(warmTimer);
  const it = el.dataset.action === "open" && reg[el.dataset.r];
  if (!it || !["Movie", "Series"].includes(it.Type) || similarCache.has(it.Id)) return;
  warmTimer = setTimeout(() => { warmNext = it.Id; warmUp(); }, 600);
}
function warmUp() {
  if (warming || !warmNext) return;
  const id = warmNext;
  warmNext = null;
  warming = true;
  similarOf(id).finally(() => { warming = false; warmUp(); });
}

async function fillSimilar(gen, id, title) {
  const res = await similarOf(id);
  const slot = $("#similar");
  if (gen !== renderGen || !slot) return;
  slot.outerHTML = row(title, res.Items, { prefix: "similar" });
  // coming back to this page with the focus in that row: restore it, unless someone already moved on
  const want = current.focusKey;
  const el = want?.startsWith("similar") && lastKey < current.shownAt && view.querySelector(`[data-k="${CSS.escape(want)}"]`);
  if (el) focusEl(el, false);
}

const img = (id, type, tag, w) => `/jf/Items/${encodeURIComponent(id)}/Images/${type}?maxWidth=${w}&quality=90&tag=${encodeURIComponent(tag)}`;
// Each part by what it holds (tiles, the bar, the small line over a page, the settings), not by the server
// program behind it: "Navidrome" says nothing to someone who wants music
const PART_NAMES = { jellyfin: "partMovies", music: "partMusic", books: "audiobooks", podcasts: "podcasts" };
const partName = (id) => t(PART_NAMES[id]);
// A library still under the name its server gave it by default (in English, or in German) in Matinee's language;
// one named by hand keeps its name
const LIB_NAMES = { movies: "libMovies", tvshows: "libShows", boxsets: "libCollections", books: "audiobooks" };
function libName(name, type) {
  const key = LIB_NAMES[type];
  const given = key && [STRINGS.en[key], STRINGS.de[key], ...(type === "tvshows" ? ["TV Shows"] : [])];
  return given?.includes(name) ? t(key) : name;
}
// Request tiles at the size they are shown (in steps of 50 px so the cache still hits):
// weak machines decode less, 4K screens still get sharp images.
const tileWidth = (vw) => Math.ceil((innerWidth * vw * devicePixelRatio * 1.1) / 50) * 50;
const POSTER_W = tileWidth(0.115), LAND_W = tileWidth(0.21);

function backdropOf(it) {
  if (it.BackdropImageTags?.length) return img(it.Id, "Backdrop", it.BackdropImageTags[0], 1920);
  if (it.ParentBackdropItemId && it.ParentBackdropImageTags?.length) return img(it.ParentBackdropItemId, "Backdrop", it.ParentBackdropImageTags[0], 1920);
  return null;
}
function logoOf(it) {
  if (it.ImageTags?.Logo) return img(it.Id, "Logo", it.ImageTags.Logo, 900);
  if (it.ParentLogoItemId && it.ParentLogoImageTag) return img(it.ParentLogoItemId, "Logo", it.ParentLogoImageTag, 900);
  return null;
}
function posterOf(it) {
  if (it.Type !== "Episode" && it.ImageTags?.Primary) return img(it.Id, "Primary", it.ImageTags.Primary, POSTER_W);
  if (it.SeriesId && it.SeriesPrimaryImageTag) return img(it.SeriesId, "Primary", it.SeriesPrimaryImageTag, POSTER_W);
  if (it.ImageTags?.Primary) return img(it.Id, "Primary", it.ImageTags.Primary, POSTER_W);
  return null;
}
// "spoilers": "hide": an episode not watched yet shows its still blurred (landOf) and no text
const spoiler = (it) => !SPOILERS && it.Type === "Episode" && !it.UserData?.Played;
const overviewOf = (it) => (spoiler(it) ? "" : it.Overview ?? "");
function landOf(it) {
  // not watched yet with spoilers hidden: its own still, blurred by Jellyfin, so each looks different but tells nothing
  if (it.Type === "Episode" && it.ImageTags?.Primary) return img(it.Id, "Primary", it.ImageTags.Primary, LAND_W) + (spoiler(it) ? "&blur=40" : "");
  if (it.ImageTags?.Thumb) return img(it.Id, "Thumb", it.ImageTags.Thumb, LAND_W);
  if (it.ParentThumbItemId && it.ParentThumbImageTag) return img(it.ParentThumbItemId, "Thumb", it.ParentThumbImageTag, LAND_W);
  if (it.BackdropImageTags?.length) return img(it.Id, "Backdrop", it.BackdropImageTags[0], LAND_W);
  if (it.ParentBackdropItemId && it.ParentBackdropImageTags?.length) return img(it.ParentBackdropItemId, "Backdrop", it.ParentBackdropImageTags[0], LAND_W);
  if (it.ImageTags?.Primary) return img(it.Id, "Primary", it.ImageTags.Primary, LAND_W);
  return null;
}

/* ---------- Formatting ---------- */

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function duration(ticks) {
  const m = Math.max(Math.round(ticks / TICKS / 60), ticks > 0 ? 1 : 0); // something short is "1 min", never "0 min"
  return m >= 60 ? t("hoursMinutes", { h: Math.floor(m / 60), m: m % 60 }) : t("minutes", { m });
}
function clock(ticks) {
  const s = Math.floor(ticks / TICKS);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}
// one formatter for all times of day: toLocaleTimeString builds a new one each call (the guide asks hundreds of times);
// always as 00:00, as a TV guide's
const TIME_OF_DAY = new Intl.DateTimeFormat(LANG, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const timeOfDay = (date) => TIME_OF_DAY.format(date);
// a day as "2. November" in the language set (the year only when it is not this one); iso: "2026-11-02", as git's
const DAY_DATE = new Intl.DateTimeFormat(LANG, { day: "numeric", month: "long" });
const DAY_DATE_YEAR = new Intl.DateTimeFormat(LANG, { day: "numeric", month: "long", year: "numeric" });
function calendarDay(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  if (!y || !m || !d) return String(iso ?? "");
  return (y === new Date().getFullYear() ? DAY_DATE : DAY_DATE_YEAR).format(new Date(y, m - 1, d));
}
// a moment (seconds since 1970) as "22:14" today, else with its day: "8. Nov., 22:14"
function dayTime(sec) {
  const d = new Date(sec * 1000), today = d.toDateString() === new Date().toDateString();
  return d.toLocaleString(LANG, today ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
// "38 min left · Ends at 22:47": when it would end if started (or resumed) now
function endsAt(it) {
  const pos = it.UserData?.PlaybackPositionTicks || 0;
  const left = Math.max(0, it.RunTimeTicks - pos);
  const end = t("endsAt", { time: timeOfDay(new Date(Date.now() + (left / TICKS) * 1000)) });
  return pos ? `${t("timeLeft", { time: duration(Math.max(left, 60 * TICKS)) })} · ${end}` : end; // never "0 min left"
}
const epCode = (it) => `S${it.ParentIndexNumber ?? "?"} E${it.IndexNumber ?? "?"}`;
const progressOf = (it) => it.UserData?.PlayedPercentage || 0;

const ICON_PLAY = '<svg style="--optical:1.12" viewBox="0 0 24 24"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>';
const ICON_CHECK = '<svg viewBox="0 0 24 24"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>';
// a gear with rounded teeth (after Material Icons' "settings", Apache License 2.0)
const ICON_SETTINGS = '<svg style="--optical:1.04" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M19.14 12.94c.04-.3.06-.61.06-.94s-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.47.47 0 0 0-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.48.48 0 0 0-.59.22L2.74 8.87a.47.47 0 0 0 .12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.48.48 0 0 0-.12-.61zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z"/></svg>';
const ICON_HDMI = '<svg style="--optical:1.14" viewBox="0 0 24 24"><path d="M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-6v2h3v2H6v-2h3v-2H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm1 2v9h16V6zm5 1.5 5 3-5 3z"/></svg>';
const ICON_WIFI = '<svg viewBox="0 0 24 24"><path d="M12 21 0.6 7.4C3.7 4.9 7.7 3.4 12 3.4s8.3 1.5 11.4 4z"/></svg>';
const ICON_BLUETOOTH = '<svg viewBox="0 0 24 24"><path d="M17.71 7.71 12 2h-1v7.59L6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 11 14.41V22h1l5.71-5.71-4.3-4.29zM13 5.83l1.88 1.88L13 9.59zm1.88 10.46L13 18.17v-3.76z"/></svg>';
const ICON_SEARCH = '<svg style="--optical:1.12" viewBox="0 0 24 24"><path d="M10 3a7 7 0 0 1 5.6 11.2l5.1 5.1-1.4 1.4-5.1-5.1A7 7 0 1 1 10 3zm0 2a5 5 0 1 0 0 10 5 5 0 0 0 0-10z"/></svg>';
// Jellyfin's logo, from github.com/jellyfin/jellyfin-ux (CC BY-SA 4.0); keeps its own colors
// --optical on a mark: how much larger than a full circle it is drawn for the same weight to the eye (a circle fills
// its box, a glyph or a wide mark does not): every mark on the dock looks the same size (style.css: --keyline)
// Matinee's own logo, the fan of cards (web/icon.svg, the boot splash's picture too): where Matinee speaks for itself
const KINO_LOGO = '<img class="kino-logo" src="icon.svg" alt="">';
const ICON_JELLYFIN = '<svg style="--optical:1.12" class="logo" viewBox="0 0 512 512"><defs><linearGradient id="jf-logo" gradientUnits="userSpaceOnUse" x1="110.25" y1="213.3" x2="496.14" y2="436.09"><stop offset="0" stop-color="#AA5CC3"/><stop offset="1" stop-color="#00A4DC"/></linearGradient></defs><g fill="url(#jf-logo)"><path d="M256 201.6c-20.4 0-86.2 119.3-76.2 139.4s142.5 19.9 152.4 0S276.5 201.6 256 201.6z"/><path d="M256 23.3c-61.6 0-259.8 359.4-229.6 420.1s429.3 60 459.2 0S317.6 23.3 256 23.3zM406.5 390.8c-19.6 39.3-281.1 39.8-300.9 0s110.1-275.3 150.4-275.3S426.1 351.4 406.5 390.8z"/></g></svg>';
// a programme guide: rows of programmes of different lengths
const ICON_GUIDE = '<svg viewBox="0 0 24 24"><path d="M3 5h7v4H3zm9 0h9v4h-9zM3 11h11v4H3zm13 0h5v4h-5zM3 17h5v4H3zm7 0h11v4H10z"/></svg>';
// a television on its stand
// what a library holds, on its tile (Material Icons): films, a collection of them, books
const ICON_PODCAST_LINE = '<svg viewBox="0 0 24 24"><path d="M14 12c0 .74-.4 1.38-1 1.72V22h-2v-8.28c-.6-.35-1-.98-1-1.72 0-1.1.9-2 2-2s2 .9 2 2zm-2-6a6 6 0 0 0-4.06 10.4l1.42-1.42A4 4 0 0 1 8 12a4 4 0 0 1 8 0c0 1.19-.53 2.25-1.36 2.98l1.42 1.42A6 6 0 0 0 12 6zm0-4C6.48 2 2 6.48 2 12c0 2.85 1.2 5.41 3.11 7.24l1.42-1.42A7.97 7.97 0 0 1 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8c0 2.29-.98 4.36-2.53 5.82l1.42 1.42A9.95 9.95 0 0 0 22 12c0-5.52-4.48-10-10-10z"/></svg>';
const ICON_FILM = '<svg viewBox="0 0 24 24"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>';
const ICON_COLLECTION = '<svg viewBox="0 0 24 24"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8 12.5v-9l6 4.5-6 4.5z"/></svg>';
const ICON_BOOK = '<svg viewBox="0 0 24 24"><path d="M18 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM6 4h5v8l-2.5-1.5L6 12V4z"/></svg>';
const ICON_TV = '<svg style="--optical:1.02" viewBox="0 0 24 24"><path d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-6v1.5h3V21H7v-1.5h3V18H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm0 2v10h16V6z"/></svg>';
// Navidrome's own logo (the blue record of its web interface), drawn again as SVG so it stays sharp on any screen
const ICON_MUSIC = '<svg style="--optical:1.08" class="logo" viewBox="0 0 512 512"><circle cx="256" cy="256" r="245.5" fill="#0084FF" stroke="#000" stroke-width="21"/><g fill="none" stroke="#000" stroke-width="21" stroke-linecap="round"><g id="nd-grooves"><path d="M66.7 215.8A193.5 193.5 0 0 1 256 62.5M109.5 256A146.5 146.5 0 0 1 256 109.5M62 245v9"/></g><use href="#nd-grooves" transform="rotate(180 256 256)"/></g><circle cx="256" cy="256" r="88" fill="#fff" stroke="#000" stroke-width="20"/><circle cx="256" cy="256" r="19" fill="#fff" stroke="#000" stroke-width="20"/></svg>';
// Podfetch's own mark, as its web interface shows it (its icon and favicon): a microphone (Material Icons), in one
// colour, the colour of the text around it
const ICON_PODCASTS = '<svg style="--optical:1.3" class="logo" fill="currentColor" viewBox="0 0 24 24"><path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/></svg>';
// Audiobookshelf's own logo (its web interface's icon.svg): headphones and books on a golden disc
const ICON_BOOKS = '<svg style="--optical:1" class="logo" viewBox="0 0 1235.7 1235.4"><defs><linearGradient id="abs-logo" gradientUnits="userSpaceOnUse" x1="617.37" y1="20.7" x2="617.37" y2="1216.56"><stop offset="0.32" stop-color="#CD9D49"/><stop offset="0.99" stop-color="#875D27"/></linearGradient></defs><circle cx="618.6" cy="618.6" r="618.6" fill="#fff"/><circle cx="617.4" cy="618.6" r="597.9" fill="url(#abs-logo)"/><g fill="#fff"><path d="M1005.6 574.1c-4.8-4-12.4-10-22.6-17v-79.2c0-201.9-163.7-365.6-365.6-365.6c-201.9 0-365.6 163.7-365.6 365.6v79.2c-10.2 7-17.7 13-22.6 17c-4.1 3.4-6.5 8.5-6.5 13.9v94.9c0 5.4 2.4 10.5 6.5 14c11.3 9.4 37.2 29.1 77.5 49.3v9.2c0 24.9 16 45 35.8 45c19.8 0 35.8-20.2 35.8-45V527.8c0-24.9-16-45-35.8-45c-19 0-34.5 18.5-35.8 41.9h-.1v-46.9c0-171.6 139.1-310.7 310.7-310.7C789 167.2 928 306.3 928 477.9v46.9h0c-1.3-23.4-16.8-41.9-35.8-41.9c-19.8 0-35.8 20.2-35.8 45v227.6c0 24.9 16 45 35.8 45c19.8 0 35.8-20.2 35.8-45v-9.2c40.3-20.2 66.2-39.9 77.5-49.3c4.2-3.5 6.5-8.6 6.5-14V588c0-5.4-2.4-10.5-6.5-13.9z"/><path d="M489.9 969.7c23.9 0 43.3-19.4 43.3-43.3V441.6c0-23.9-19.4-43.3-43.3-43.3h-44.7c-23.9 0-43.3 19.4-43.3 43.3v484.8c0 23.9 19.4 43.3 43.3 43.3zM418.2 514.6h98.7v10.3h-98.7z"/><path d="M639.7 969.7c23.9 0 43.3-19.4 43.3-43.3V441.6c0-23.9-19.4-43.3-43.3-43.3H595c-23.9 0-43.3 19.4-43.3 43.3v484.8c0 23.9 19.4 43.3 43.3 43.3zM568 514.6h98.7v10.3H568z"/><path d="M789.6 969.7c23.9 0 43.3-19.4 43.3-43.3V441.6c0-23.9-19.4-43.3-43.3-43.3h-44.7c-23.9 0-43.3 19.4-43.3 43.3v484.8c0 23.9 19.4 43.3 43.3 43.3zM717.9 514.6h98.7v10.3h-98.7z"/><path d="M327.1 984.7h580.5c18 0 32.6 14.6 32.6 32.6s-14.6 32.6-32.6 32.6H327.1c-18 0-32.6-14.6-32.6-32.6s14.6-32.6 32.6-32.6z"/></g></svg>';
// a book: 30 seconds back and ahead
const ICON_BACK30 = '<svg viewBox="0 0 24 24"><path d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/><text x="12" y="16.3" font-size="6.5" font-weight="700" text-anchor="middle" font-family="sans-serif">30</text></svg>';
const ICON_FWD30 = '<svg viewBox="0 0 24 24"><path d="M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z"/><text x="12" y="16.3" font-size="6.5" font-weight="700" text-anchor="middle" font-family="sans-serif">30</text></svg>';
// weather symbols for the screensaver: drawn as lines in the clock's color
const WX = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const WX_CLOUD = '<path d="M7 18h9.5a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 6.3 10 4 4 0 0 0 7 18z"/>';
const WEATHER_ICONS = {
  wClear: WX('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>'),
  wNight: WX('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>'),
  wPartly: WX('<path d="M8 2.5v1.5M3.2 4.6l1 1M2 9.5h1.5M12.8 4.6l-1 1"/><path d="M5.3 11.6A3.5 3.5 0 1 1 11.6 8"/>' + WX_CLOUD.replace('d="', 'transform="translate(2 1.5) scale(.9)" d="')),
  wCloudy: WX(WX_CLOUD),
  wFog: WX('<path d="M4 9h16M3 13h18M5 17h14"/>'),
  wRain: WX(WX_CLOUD.replace('d="', 'transform="translate(0 -3)" d="') + '<path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>'),
  wSnow: WX(WX_CLOUD.replace('d="', 'transform="translate(0 -3)" d="') + '<path d="M8 19.5h.01M12 20.5h.01M16 19.5h.01M10 22h.01M14 22h.01" stroke-width="2.4"/>'),
  wStorm: WX(WX_CLOUD.replace('d="', 'transform="translate(0 -3)" d="') + '<path d="M12.5 15l-2.5 4h3l-2 3.5"/>'),
};
const ICON_ALBUM = '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zm0 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm0 2a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/></svg>';
const ICON_ARTIST = '<svg viewBox="0 0 24 24"><path d="M12 3a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm0 11c4.4 0 8 2.2 8 5v2H4v-2c0-2.8 3.6-5 8-5z"/></svg>';
const ICON_PLAYLIST = '<svg viewBox="0 0 24 24"><path d="M3 5h13v2H3zm0 4h13v2H3zm0 4h9v2H3zm15-4v7.2A3 3 0 1 0 20 19v-8h3V9z"/></svg>';
const ICON_SHUFFLE = '<svg viewBox="0 0 24 24"><path d="M14 4h6v6l-2.3-2.3-3.6 3.6-1.4-1.4 3.6-3.6zM4 5.4 5.4 4 20 18.6 18.6 20zM14 20v-2h2.3l-3-3 1.4-1.4 3 3V14H20v6z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24"><path d="M7 4h3.5v16H7zm6.5 0H17v16h-3.5z"/></svg>';
const ICON_PREV = '<svg viewBox="0 0 24 24"><path d="M6 5h2v14H6zm3.5 7L19 5.5v13z"/></svg>';
const ICON_NEXT = '<svg viewBox="0 0 24 24"><path d="M16 5h2v14h-2zM5 5.5l9.5 6.5L5 18.5z"/></svg>';
const ICON_STAR = '<svg viewBox="0 0 24 24"><path d="m12 2.5 2.9 6.2 6.8.7-5.1 4.6 1.4 6.7L12 17.3l-5.9 3.4 1.4-6.7-5.1-4.6 6.8-.7z"/></svg>';
const ICON_STAR_OPEN = '<svg viewBox="0 0 24 24"><path d="m12 2.5 2.9 6.2 6.8.7-5.1 4.6 1.4 6.7L12 17.3l-5.9 3.4 1.4-6.7-5.1-4.6 6.8-.7zm0 4.8-1.6 3.4-3.7.4 2.8 2.5-.8 3.7 3.3-1.9 3.3 1.9-.8-3.7 2.8-2.5-3.7-.4z"/></svg>';
const ICON_RADIO = '<svg viewBox="0 0 24 24"><path d="M4 8h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2zm3 3a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zm7 1v2h5v-2zm0 3.5v2h5v-2zM5.6 7 17.4 2.4l.7 1.9L10.9 7z"/></svg>';
const ICON_LYRICS = '<svg viewBox="0 0 24 24"><path d="M4 4h16v2H4zm0 4h16v2H4zm0 4h10v2H4zm0 4h7v2H4zm14-3v4.2a2.5 2.5 0 1 0 2 2.4V15h2v-2z"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24"><path d="M14.5 2.5a9.5 9.5 0 1 0 7 15.9A8 8 0 0 1 14.5 2.5z"/></svg>';
const ICON_HEART = '<svg viewBox="0 0 24 24"><path d="M12 21 10.6 19.7C5.4 15 2 11.9 2 8.1 2 5 4.4 2.6 7.5 2.6c1.7 0 3.4.8 4.5 2.1a6 6 0 0 1 4.5-2.1C19.6 2.6 22 5 22 8.1c0 3.8-3.4 6.9-8.6 11.6z"/></svg>';
const ICON_HEART_OPEN = '<svg viewBox="0 0 24 24"><path d="M16.5 2.6A6 6 0 0 0 12 4.7a6 6 0 0 0-4.5-2.1C4.4 2.6 2 5 2 8.1c0 3.8 3.4 6.9 8.6 11.6L12 21l1.4-1.3C18.6 15 22 11.9 22 8.1c0-3.1-2.4-5.5-5.5-5.5zM12.1 18.3l-.1.1-.1-.1C7.1 14 4 11.2 4 8.1 4 6 5.5 4.5 7.5 4.5c1.5 0 3 1 3.6 2.4h1.9c.5-1.4 2-2.4 3.5-2.4 2 0 3.5 1.5 3.5 3.6 0 3.1-3.1 5.9-7.9 10.2z"/></svg>';
const ICON_RESTART = '<svg viewBox="0 0 24 24"><path d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg>';

// The settings' symbols: each line its own, white on a tile of its color, beside the list as its hint is (sideIcon);
// a line without one shows its part's. Drawn as lines on a 24 grid.
const glyph = (d) => `<svg class="line" viewBox="0 0 24 24">${d}</svg>`;
const G = {
  sliders: glyph('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'),
  screen: glyph('<rect x="2.5" y="4.5" width="19" height="13" rx="2"/><path d="M10.67 8.5v5l4-2.5z"/><path d="M8 20.5h8"/>'),
  person: glyph('<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c.8-4 3.8-6 7.5-6s6.7 2 7.5 6"/>'),
  chip: glyph('<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".5"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>'),
  globe: glyph('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>'),
  home: glyph('<path d="M3.5 11.5 12 4l8.5 7.5"/><path d="M6 10v10h4.5v-5.5h3V20H18V10"/>'),
  speaker: glyph('<path d="M3.5 9.5H7L11.5 5.5v13L7 14.5H3.5z"/><path d="M15 9a4 4 0 0 1 0 6M17.5 6.5a7.5 7.5 0 0 1 0 11"/>'),
  note: glyph('<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>'),
  sparkle: glyph('<path d="M11 3l1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8z"/><path d="M18.5 15v5M16 17.5h5"/>'),
  power: glyph('<path d="M12 3.5v8"/><path d="M7 6.5a7.5 7.5 0 1 0 10 0"/>'),
  eye: glyph('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'),
  eyeOff: glyph('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 3.5l16 17"/>'),
  weather: glyph('<path d="M8.5 2.5v1.5M3 8h1.5M4.6 4.1l1 1M12.4 4.1l-1 1"/><path d="M5.6 10.6a3.5 3.5 0 0 1 6.3-3.3"/><path d="M8.5 20h9a3.5 3.5 0 0 0 .4-7 5 5 0 0 0-9.4 1.3A2.9 2.9 0 0 0 8.5 20z"/>'),
  pin: glyph('<path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.5"/>'),
  talk: glyph('<path d="M4 4.5h10a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H8.5l-4 3.5v-3.5H4A1.5 1.5 0 0 1 2.5 12V6A1.5 1.5 0 0 1 4 4.5z"/><path d="M18.5 8.5h1.5A1.5 1.5 0 0 1 21.5 10v5.5A1.5 1.5 0 0 1 20 17h-.5v3L16 17h-4.5"/>'),
  captions: glyph('<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M6.5 15h4M13 15h4.5M6.5 11.5h7M16 11.5h1.5"/>'),
  textSize: glyph('<path d="M2.5 19 8 5h1l5.5 14M4.5 14.5h8"/><path d="M15.5 19l3-7.5h.5l3 7.5M16.6 16.5h4.3"/>'),
  textBox: glyph('<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M8 8.5h8M12 8.5v8"/>'),
  timer: glyph('<circle cx="12" cy="13.5" r="7.5"/><path d="M12 9.5v4l2.5 2.5M9.5 2.5h5"/>'),
  update: glyph('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v8.5M8 12.5l4 4 4-4"/>'),
  out: glyph('<path d="M12 14.5V3.5M7.5 8 12 3.5 16.5 8"/><path d="M5 11.5v8h14v-8"/>'),
  in: glyph('<path d="M12 3.5v11M7.5 10 12 14.5l4.5-4.5"/><path d="M5 11.5v8h14v-8"/>'),
  leave: glyph('<path d="M10 4H5.5v16H10"/><path d="M14.5 8 19 12l-4.5 4M19 12H9.5"/>'),
  gauge: glyph('<path d="M4.2 17.5a8.5 8.5 0 1 1 15.6 0"/><path d="M12 14l4-5"/><circle cx="12" cy="14" r="1.5"/>'),
  question: glyph('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.5v.4M12 16.8v.2"/>'),
  seal: glyph('<path d="M12 2.8l2.4 1.7 2.9-.1.9 2.8 2.3 1.7-.9 2.8.9 2.8-2.3 1.7-.9 2.8-2.9-.1L12 21.2l-2.4-1.7-2.9.1-.9-2.8-2.3-1.7.9-2.8-.9-2.8 2.3-1.7.9-2.8 2.9.1z"/><path d="M8.5 12l2.3 2.3 4.7-4.7"/>'),
  skip: glyph('<path d="M5 5.5l10 6.5-10 6.5z"/><path d="M19 5.5v13"/>'),
  level: glyph('<path d="M4 9.5v5M8 6.5v11M12 9.5v5M16 6.5v11M20 9.5v5"/>'),
  film: glyph('<rect x="3" y="3.5" width="18" height="17" rx="2"/><path d="M7.5 3.5v17M16.5 3.5v17M3 8h4.5M3 12h4.5M3 16h4.5M16.5 8H21M16.5 12H21M16.5 16H21"/>'),
  check: glyph('<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5l5 5M8 10.5l1.8 1.8 3.2-3.3"/>'),
  title: glyph('<path d="M4 6h16M4 11h16M4 16h9"/><path d="M16 15.5l2.5 2.5 2.5-2.5"/>'),
  remote: glyph('<rect x="7" y="2.5" width="10" height="19" rx="3"/><circle cx="12" cy="8" r="2"/><path d="M10 13.5h.01M14 13.5h.01M10 17h.01M14 17h.01"/>'),
  sync: glyph('<path d="M7 18.5a4.5 4.5 0 0 1-.5-9 6 6 0 0 1 11.5 1.5 3.75 3.75 0 0 1-.5 7.5"/><path d="M10 13.5l2-2 2 2M12 11.5v7"/>'),
  motion: glyph('<circle cx="15.5" cy="12" r="5"/><path d="M2.5 8.5h5M2 12h5.5M2.5 15.5h5"/>'),
  shield: glyph('<path d="M12 3l7 3v5c0 5-3.2 8.6-7 10-3.8-1.4-7-5-7-10V6z"/><path d="M9 12l2 2 4-4"/>'),
  lock: glyph('<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><path d="M12 14.5v2"/>'),
};
// the line's data-k → [symbol, its tile's color]; an app's own logo on the plain tile
const SET_ICONS = {
  "part-basics": [G.sliders, "gray"], "set-general": [G.sliders, "gray"], "part-av": [G.screen, "red"],
  "set-bluetooth": [ICON_BLUETOOTH, "blue"], "set-wifi": [ICON_WIFI, "blue"], "set-hdmi": [ICON_HDMI, "purple"], "part-account": [G.person, "green"], "part-system": [G.chip, "gray"],
  "part-movies": [ICON_JELLYFIN], "part-navidrome": [ICON_MUSIC], "part-books": [ICON_BOOKS], "part-podcasts": [ICON_PODCASTS],
  "set-language": [G.globe, "blue"], "set-startwith": [G.home, "orange"], "set-sounds": [G.speaker, "pink"],
  "set-soundkind": [G.note, "pink"], "part-saver": [G.sparkle, "indigo"], "set-screensaver": [G.sparkle, "indigo"],
  "set-autooff": [G.power, "red"], "set-sleepend": [ICON_MOON, "indigo"], "set-unhide": [G.eye, "gray"],
  "set-saverweather": [G.weather, "sky"], "set-wxplace": [G.pin, "sky"],
  "set-audio": [G.talk, "orange"], "part-subs": [G.captions, "purple"], "set-display": [ICON_TV, "blue"],
  "set-audiosync": [G.timer, "teal"], "set-volume": [G.speaker, "pink"],
  "set-subtitles": [G.captions, "purple"], "set-sublang": [G.globe, "purple"], "set-subsize": [G.textSize, "purple"],
  "set-substyle": [G.textBox, "purple"],
  "part-update": [G.update, "blue"], "set-update": [G.update, "blue"], "set-autoupdate": [G.timer, "blue"],
  "sys-reboot": [ICON_RESTART, "orange"], "sys-poweroff": [G.power, "red"],
  "acc-sync": [G.sync, "sky"], "acc-dev": [G.update, "sky"], "set-sync": [G.sync, "sky"], "acc-out": [G.out, "green"], "acc-in": [G.in, "green"], "acc-signout": [G.leave, "red"],
  "part-everywhere": [G.lock, "indigo"],
  "set-quality": [G.gauge, "blue"], "set-ask": [G.question, "orange"], "set-spoilers": [G.eyeOff, "gray"],
  "set-badges": [G.seal, "green"], "set-skipintro": [G.skip, "orange"], "set-moviesound": [G.level, "pink"],
  "set-framerate": [G.film, "purple"], "set-libcheck": [G.check, "teal"],
  "tvch": [ICON_TV, "blue"],
  "set-controls": [G.remote, "blue"], "set-simple": [G.lock, "gray"], "set-maxage": [G.shield, "green"], "set-legal": [G.seal, "gray"], "set-diagnose": [G.gauge, "green"], "set-share": [G.film, "orange"], "set-support": [G.question, "teal"], "set-welcome": [G.sparkle, "purple"], "set-size": [G.screen, "blue"], "set-textsize": [G.textSize, "blue"], "set-motion": [G.motion, "blue"],
};
// a settings line's symbol: its own, or that of the lines of its kind (each channel, each device, …)
const iconKey = (raw) => raw.startsWith("tvch-") ? "tvch" : raw.startsWith("ctl-") ? "set-controls" : raw.startsWith("pin-") ? "set-simple"
  : raw.startsWith("ev-") ? "part-everywhere" : raw.startsWith("acc-dev-") ? "acc-dev" : raw;
function sideIcon(el) {
  const box = $(".settings-side .settings-icon");
  if (!box) return;
  box.base ??= box.innerHTML; // the part's own, for the lines without one
  const k = iconKey(el.dataset.k ?? "");
  const [svg, tint] = SET_ICONS[k] ?? [];
  const key = svg ? k : "";
  if ((box.dataset.icon ?? "") === key) return;
  box.dataset.icon = key;
  box.innerHTML = svg ?? box.base;
  if (tint) box.dataset.tint = tint; else delete box.dataset.tint;
  box.classList.remove("pop");
  void box.offsetWidth; // the change seen as one: it comes up again
  box.classList.add("pop");
}

/* ---------- Background ---------- */

let bgUrl = null, bgSoft = 1, libNewest = new Map(), libWalls = new Map();
// Blurred background: a small image is blurred once onto a canvas that CSS scales up.
// No CSS filter, so nothing has to be recomputed while scrolling. The canvas is a quarter of the screen
// (480×270 on 1080p): smaller, the scaled-up blur shows blocks. A little noise keeps the soft gradients
// from falling into visible steps, which 8 bits per color cannot avoid on their own: made once and laid over the
// background (style.css), not worked into every picture (that took as long as the blur itself).
const BG_W = Math.max(192, Math.round(innerWidth / 4)), BG_H = Math.round((BG_W * 9) / 16), BG_BLUR = BG_W / 32;
function clearBackdrop() {
  bgUrl = null;
  tintGlass(null);
  for (const old of $("#backdrop").children) {
    old.classList.remove("on");
    setTimeout(() => old.remove(), 900);
  }
}

(function noise() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const ctx = c.getContext("2d"), px = ctx.createImageData(128, 128), d = px.data;
  for (let i = 0; i < d.length; i += 4) {
    d[i] = d[i + 1] = d[i + 2] = Math.random() < 0.5 ? 0 : 255;
    d[i + 3] = 4; // about one step of eight bits up or down
  }
  ctx.putImageData(px, 0, 0);
  $("#backdrop").style.setProperty("--noise", `url(${c.toDataURL()})`);
})();

// A light picture stays light grey after the same darkening as any other, and white text on it is hard to read:
// darkened further until its mean brightness is BG_LIGHT at most
const BG_LIGHT = 0.3;
// Its brightness is measured on a copy 32 pixels wide in the browser's memory: reading back the large canvas from
// the graphics chip held the page up for 50 to 130 ms each time (a key pressed then had to wait), and drawing the
// picture into memory decodes it there again (30 to 50 ms, 8 to 13 ms made small from the <img>). Made from the
// picture's file (from the browser's cache), the small copy is decoded beside the page; drawn the same way as the
// backdrop but for the blur, which leaves the mean as it is.
const BG_PROBE_W = 32, BG_PROBE_H = Math.round((BG_PROBE_W * 9) / 16);
let bgProbe;
async function brightness(img, over, h, tone) {
  const k = BG_PROBE_W / BG_W;
  const size = { resizeWidth: Math.max(1, Math.round((BG_W + 2 * over) * k)), resizeHeight: Math.max(1, Math.round(h * k)), resizeQuality: "low" };
  let small;
  try {
    const file = await fetch(img.currentSrc || img.src).then((r) => (r.ok ? r.blob() : Promise.reject()));
    small = await createImageBitmap(file, size);
  } catch {
    try { small = await createImageBitmap(img, size); } catch { return { mean: 0, tint: null }; } // a picture without CORS cannot be read
  }
  bgProbe ??= Object.assign(document.createElement("canvas"), { width: BG_PROBE_W, height: BG_PROBE_H }).getContext("2d", { willReadFrequently: true });
  bgProbe.filter = tone;
  bgProbe.clearRect(0, 0, BG_PROBE_W, BG_PROBE_H);
  bgProbe.drawImage(small, Math.round(-over * k), Math.round(((BG_H - h) / 2) * k));
  small.close();
  let d;
  try { d = bgProbe.getImageData(0, 0, BG_PROBE_W, BG_PROBE_H).data; } catch { return { mean: 0, tint: null }; } // a picture without CORS cannot be read
  let sum = 0, r = 0, g = 0, b = 0;
  for (let i = 0; i < d.length; i += 4) {
    sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    r += d[i]; g += d[i + 1]; b += d[i + 2];
  }
  const n = d.length / 4;
  return { mean: sum / n / 255, tint: glassTint(r / n, g / n, b / n) };
}
// The glass over the picture takes a breath of its colour, as the light of the screen colours the room: the picture's
// mean hue, at the warm dark's lightness and with little of its saturation. A picture with hardly any colour of its
// own (grey, black and white) leaves the warm dark as it is (tokens.css: --glass).
const GLASS_L = 0.13, GLASS_S_MAX = 0.22, GLASS_S_MIN = 0.09, GLASS_A = 0.5;
function glassTint(r, g, b) {
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 510;
  if (max === min || l <= 0 || l >= 1) return null;
  const s = Math.min(GLASS_S_MAX, ((max - min) / 255 / (1 - Math.abs(2 * l - 1))) * 0.5);
  if (s < GLASS_S_MIN) return null;
  const h = max === r ? ((g - b) / (max - min) + 6) % 6 : max === g ? (b - r) / (max - min) + 2 : (r - g) / (max - min) + 4;
  return `hsl(${Math.round(h * 60)} ${Math.round(s * 100)}% ${GLASS_L * 100}% / ${GLASS_A})`;
}
function tintGlass(tint) {
  const root = document.documentElement.style;
  if (tint) root.setProperty("--glass-pic", tint);
  else root.removeProperty("--glass-pic");
}
function dimLight(ctx, mean) {
  if (mean <= BG_LIGHT) return;
  ctx.fillStyle = `rgba(0,0,0,${1 - BG_LIGHT / mean})`;
  ctx.fillRect(0, 0, BG_W, BG_H);
}

// soft: how many times more blurred than a title's (the main menu's: a glow of colour, nothing to make out in it,
// a little lighter and fuller for it, as the blur spreads its light thinner)
const MENU_SOFT = 2.5;
const bgTone = (soft) => (soft > 1 ? "saturate(1.5) brightness(0.85)" : "saturate(1.35) brightness(0.62)");
async function setBackdrop(url, soft = 1) {
  if (!url || (url === bgUrl && soft === bgSoft)) return;
  bgUrl = url;
  bgSoft = soft;
  const img = new Image();
  img.fetchPriority = "high"; // before the tiles' pictures, which fill the browser's six connections to the backend
  img.src = url.replace(/maxWidth=\d+/, `maxWidth=${BG_W * 2}`).replace(/([?&])size=\d+/, `$1size=${BG_W * 2}`);
  // a picture that failed (the server slow, a hiccup on the way) is asked for again the next time, not taken as shown
  try { await img.decode(); } catch { if (bgUrl === url) bgUrl = null; return; }
  if (bgUrl !== url) return;
  // drawn slightly larger, so the edges do not fade out; covers (square) fill the width
  const blur = BG_BLUR * soft, over = blur * 3, w = BG_W + 2 * over, h = Math.max(BG_H + 2 * over, (w * img.naturalHeight) / img.naturalWidth);
  const { mean, tint } = await brightness(img, over, h, bgTone(soft));
  if (bgUrl !== url) return;
  tintGlass(tint);
  const c = document.createElement("canvas");
  c.width = BG_W;
  c.height = BG_H;
  const ctx = c.getContext("2d");
  ctx.filter = `blur(${blur}px) ${bgTone(soft)}`;
  ctx.drawImage(img, -over, (BG_H - h) / 2, w, h);
  ctx.filter = "none";
  dimLight(ctx, mean);
  const box = $("#backdrop");
  box.append(c);
  c.offsetWidth; // commit the start state so the cross-fade runs
  c.classList.add("on");
  for (const old of [...box.children].slice(0, -1)) {
    old.classList.remove("on");
    setTimeout(() => old.remove(), 900);
  }
}

/* ---------- Liquid glass ---------- */

// The dock's glass bends the picture behind it at its rim, as the edge of a lens does: a map of how far each point of
// it looks inwards (red across, green down, 128 straight through), for the filter #liquid in index.html. Inside the
// rim (as wide as the corners' radius) the bending grows towards the edge with a lens's curve; the middle stays clear.
// Drawn once for the dock's size, again only when that changes; with the reduced effects the filter is not used.
let liquidSize = "";
function liquidMap(w, h, r, bend) {
  const c = Object.assign(document.createElement("canvas"), { width: w, height: h });
  const ctx = c.getContext("2d"), px = ctx.createImageData(w, h), d = px.data;
  const hx = w / 2 - r, hy = h / 2 - r;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const px0 = x + 0.5 - w / 2, py0 = y + 0.5 - h / 2;
      const qx = Math.abs(px0) - hx, qy = Math.abs(py0) - hy;
      let nx = 0, ny = 0, inside; // the way out of the glass at this point, and how far in from its edge it is
      if (qx > 0 && qy > 0) {
        const l = Math.hypot(qx, qy) || 1;
        nx = qx / l; ny = qy / l; inside = r - l;
      } else if (qx > qy) { nx = 1; inside = r - qx; } else { ny = 1; inside = r - qy; }
      const i = (y * w + x) * 4;
      const t = inside > 0 && inside < r ? (1 - inside / r) ** 2 * bend : 0;
      d[i] = 128 - Math.sign(px0) * nx * t * 127;
      d[i + 1] = 128 - Math.sign(py0) * ny * t * 127;
      d[i + 2] = 128;
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(px, 0, 0);
  return c.toDataURL();
}
function liquidGlass(el) {
  if (!el || getComputedStyle(el).backdropFilter.indexOf("url(") < 0) return;
  const w = Math.round(el.offsetWidth), h = Math.round(el.offsetHeight);
  const r = Math.min(parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0, h / 2);
  const bend = parseFloat(getComputedStyle(el).getPropertyValue("--liquid-bend")) || 0;
  if (!w || !h || !r || `${w}×${h}×${r}` === liquidSize) return;
  liquidSize = `${w}×${h}×${r}`;
  const f = $("#liquid"), map = $("#liquid-map");
  for (const n of [f, map]) { n.setAttribute("width", w); n.setAttribute("height", h); }
  map.setAttribute("href", liquidMap(w, h, Math.round(r), 1));
  f.querySelector("feDisplacementMap").setAttribute("scale", Math.round(2 * r * bend));
}
const liquidWatch = new ResizeObserver((all) => all.forEach((e) => liquidGlass(e.target)));

/* ---------- Building blocks ---------- */

// Every card the same way, whatever it shows (a movie, a video, a programme, an album, a book): its picture over
// its name and a line below. shape: "land" (16:9), "sq" (square), "" (a poster); sub null leaves the line out
// (an empty one stays, for showTvNow to fill in).
function tile({ item, key, action, shape = "", art, label, sub = null, attrs = "", artClass = "", tint = "" }) {
  const r = reg.push(item) - 1;
  return `<div class="card f${shape ? ` ${shape}` : ""}" tabindex="-1" data-r="${r}" data-k="${esc(key)}" data-action="${action}"${attrs}>
    <div class="art${artClass ? ` ${artClass}` : ""}"${tint ? ` style="--tint:${tint}"` : ""}>${art}</div>
    <div class="label">${esc(label)}</div>${sub != null ? `<div class="sub">${esc(sub)}</div>` : ""}
  </div>`;
}
// its picture, or its name on a plain tile while there is none
const cardImg = (src, stand) => (src ? `<img src="${esc(src)}" loading="lazy" decoding="async" alt="">` : `<div class="ph"><span>${esc(stand)}</span></div>`);
// how far it was watched or heard, as a bar along the bottom of the picture
const progressBar = (pct) => (pct > 0 && pct < 100 ? `<div class="progress"><i style="width:${pct}%"></i></div>` : "");

function card(it, { land = false, key, action = "open", label, sub, aux = false } = {}) {
  const pct = progressOf(it);
  const subline = sub ?? (it.Type === "Episode" ? `${epCode(it)} · ${it.Name}` : it.Type === "Season" ? it.Name : it.ProductionYear ?? "");
  let overlay = progressBar(pct);
  if (!overlay && it.UserData?.Played) overlay = `<div class="watched">✓</div>`;
  else if (!overlay && it.UserData?.UnplayedItemCount) overlay = `<div class="badge">${it.UserData.UnplayedItemCount}</div>`;
  if (it.UserData?.IsFavorite) overlay += `<div class="fav">${ICON_HEART}</div>`;
  if (it.Type === "Series") overlay += countBadge(BADGES.series[it.Id]);
  const art = TILE_ICONS[it.Type] ? `<div class="ph icon">${TILE_ICONS[it.Type]}</div>` : cardImg(land ? landOf(it) : posterOf(it), it.Name);
  return tile({ item: it, key: key ?? it.Id, action, shape: [land && "land", aux && "aux"].filter(Boolean).join(" "),
    art: art + overlay, label: label ?? (it.Type === "Episode" || it.Type === "Season" ? it.SeriesName : it.Name),
    sub: subline === "" ? null : String(subline) });
}

const initials = (name) => name.split(/\s+/).filter(Boolean).map((w) => w[0]).filter((c) => /\p{L}/u.test(c)).slice(0, 2).join("").toUpperCase();

// The cast of a movie, show or episode, faces in a row: OK on one opens what else with them is in the library
function castRow(it) {
  const cast = (it.People || []).filter((p) => p.Type === "Actor").slice(0, 20);
  if (!cast.length) return "";
  return `<section class="row people"><h2>${t("cast")}</h2><div class="strip">${cast.map((p, i) => {
    const src = p.PrimaryImageTag ? img(p.Id, "Primary", p.PrimaryImageTag, tileWidth(0.05)) : null;
    return `<div class="card f" tabindex="-1" data-k="cast-${i}" data-action="person" data-id="${esc(p.Id)}" data-name="${esc(p.Name)}">
      <div class="art">${src ? `<img src="${src}" loading="lazy" decoding="async" alt="">` : `<div class="ph">${esc(initials(p.Name))}</div>`}</div>
      <div class="label">${esc(p.Name)}</div><div class="sub">${esc(p.Role || "")}</div></div>`;
  }).join("")}</div></section>`;
}

function row(title, items, opts = {}) {
  if (!items.length) return "";
  return `<section class="row"><h2>${esc(title)}</h2><div class="strip">${items.map((it, i) => card(it, { ...opts, key: `${opts.prefix ?? title}-${i}` })).join("")}</div></section>`;
}

function metaLine(it) {
  const parts = [];
  if (it.Type === "Episode") parts.push(`<b>${esc(epCode(it))}</b>`);
  if (it.ProductionYear) parts.push(esc(it.ProductionYear));
  // an age rating in a frame, as on a box, so a bare "12" is not read as a count; "DE-12" loses its country
  if (it.OfficialRating) parts.push(`<span class="rating">${esc(it.OfficialRating.replace(/^[A-Z]{2}-(?=\d+$)/, ""))}</span>`);
  if (it.RunTimeTicks && it.Type !== "Series") parts.push(duration(it.RunTimeTicks));
  if (it.RunTimeTicks && (it.Type === "Movie" || it.Type === "Episode")) parts.push(endsAt(it));
  if (it.Type === "Series" && it.ChildCount) parts.push(it.ChildCount > 1 ? t("seasons", { n: it.ChildCount }) : t("season"));
  if (it.Type === "BoxSet" && it.ChildCount) parts.push(it.ChildCount > 1 ? t("movies", { n: it.ChildCount }) : t("movie"));
  if (it.CommunityRating) parts.push(`★ ${it.CommunityRating.toFixed(1)}`);
  if (it.Genres?.length) parts.push(esc(it.Genres.slice(0, 3).join(", ")));
  // only the full item from a detail page carries People
  const directors = (it.People || []).filter((p) => p.Type === "Director").slice(0, 2).map((p) => p.Name);
  if (directors.length) parts.push(t("directedBy", { names: esc(directors.join(", ")) }));
  return `<div class="meta">${parts.map((p) => `<span>${p}</span>`).join("")}</div>`;
}

// Detail pages: poster (episodes: their still) next to the info and buttons
const DETAIL_POSTER_W = tileWidth(0.16), DETAIL_STILL_W = tileWidth(0.27);
// Detail pages: the backdrop large and sharp behind the top, as on the start (see showDetailArt), what it is above
// its name; only without a backdrop its poster (an episode's still) beside the info
const KIND_OF = { Movie: "kindMovie", Series: "kindSeries", BoxSet: "kindCollection", Episode: "kindEpisode" };
function detailHero(it, actions) {
  const kind = KIND_OF[it.Type] ? kicker(t(KIND_OF[it.Type])) : "";
  if (backdropOf(it)) {
    return `<div id="stage-art" class="beside"></div><section class="hero wide">
      <div class="info">${kind}${heroInner(it)}<div class="actions">${actions}</div></div></section>`;
  }
  const still = it.Type === "Episode";
  const src = still ? landOf(it)?.replace(/maxWidth=\d+/, `maxWidth=${DETAIL_STILL_W}`)
    : posterOf(it)?.replace(/maxWidth=\d+/, `maxWidth=${DETAIL_POSTER_W}`);
  const poster = src ? `<div class="poster${still ? " still" : ""}"><img src="${src}" decoding="async" alt=""></div>` : "";
  return `<section class="hero with-poster">${poster}<div class="info">${kind}${heroInner(it)}<div class="actions">${actions}</div></div></section>`;
}
// once the detail page is on the screen: its backdrop into the place detailHero kept for it
function showDetailArt(it) {
  if (backdropOf(it)) stageArt(backdropOf(it), null, "square", true);
}
// The same head for a page without a Jellyfin item: what it is, its name, facts
// (html), its text and buttons, with the place for its picture (stageArt once the page is there)
const pageHead = (kind, title, facts, about, actions) => `<div id="stage-art" class="beside"></div><section class="hero wide">
  <div class="info">${kicker(kind)}<h1 class="title">${esc(title)}</h1>${facts.length ? `<div class="meta">${facts.map((f) => `<span>${f}</span>`).join("")}</div>` : ""}
  ${about ? `<div class="overview">${esc(about)}</div>` : ""}<div class="actions">${actions}</div></div></section>`;

// The same place at the top of the music and the audiobooks: the name of what is focused, and who made it
const plainHero = (title, sub) => `<h1 class="title">${esc(title)}</h1>${sub ? `<div class="meta">${esc(sub)}</div>` : ""}`;
const kicker = (text) => `<div class="kicker">${esc(text)}</div>`;
// A button at the top of a start page, told about above: its name alone, in every part (no title of what is behind
// it; the wall of its pictures shows that), under what it is where that says more than its name
const chipHero = (x) => (x.kicker ? kicker(x.kicker) : "") + plainHero(x.Name);
function plainHeroOf(x) {
  if (x.kind === "song") return [x.title, x.artist];
  if (x.kind === "book") return [x.title, x.author];
  if (x.kind === "podcast") return [x.name, x.author];
  if (x.kind === "episode") return [x.title, x.show || calendarDay(x.date)];
  if (x.kind) return [x.name, x.kind === "album" ? x.artist : x.kind === "artist" ? t("artistOne") : ""];
  return [x.Name, ""]; // a tile at the top
}

function heroInner(it, { title } = {}) {
  const logo = logoOf(it);
  const name = title ?? (it.Type === "Episode" ? it.SeriesName : it.Name);
  return `${logo ? `<img class="logo" src="${logo}" decoding="async" alt="${esc(name)}">` : `<h1 class="title">${esc(name)}</h1>`}
    ${it.Type === "Episode" ? `<div class="meta"><b>${esc(it.Name)}</b></div>` : ""}
    ${metaLine(it)}
    ${overviewOf(it) ? `<div class="overview">${esc(overviewOf(it))}</div>` : ""}`;
}
