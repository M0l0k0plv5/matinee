"use strict";
// Matinee Remote (remote/index.html): signs in to Jellyfin, lists the account's Matinees that are on and sends them
// Jellyfin's remote-control commands. Everything the server says is put on the page as text, never as HTML.

// A phone may keep the page and this script apart, one old and one new: then parts this script needs are missing and
// nothing would show. The page is fetched anew past the phone's cache and loaded again, once.
const VERSION = "5";
if (document.querySelector("meta[name=kino-remote-version]")?.content !== VERSION) {
  let tried = false;
  try {
    tried = sessionStorage.getItem("kino-remote-reloaded") === VERSION;
    sessionStorage.setItem("kino-remote-reloaded", VERSION);
  } catch {}
  if (!tried) fetch(location.href, { cache: "reload" }).finally(() => location.reload());
  throw new Error("Matinee Remote: the page and its script are of different versions");
}

/* ---------- Words ---------- */

const WORDS = {
  en: {
    signIn: "Sign In", signInNote: "With your Jellyfin account.", server: "Server", user: "User Name", password: "Password",
    signInButton: "Sign In", signingIn: "Signing In…", wrongLogin: "User name or password is wrong.",
    noServer: "The server cannot be reached.", devices: "Matinee", noDevices: "No Matinee is on.",
    noDevicesHint: "Turn on a Matinee signed in with this Jellyfin account. It shows up here by itself.", serverHint: "Trying again…", signOut: "Sign Out",
    on: "Connected", off: "Not reachable", audio: "Audio", subtitles: "Subtitles", subtitlesOff: "Off",
    padLabel: "Swipe to move, tap to select", back: "Back", home: "Home", menu: "Menu", quieter: "Quieter",
    louder: "Louder", playPause: "Play/Pause", search: "Search", sent: "Sent", failed: "Not sent",
    signedOut: "Signed out. Sign in again.", left: "{t} left", playingOn: "Playing",
    library: "Library", remote: "Remote", continueWatching: "Continue Watching", nextUp: "Up Next", movies: "Movies",
    shows: "Shows", play: "Play", resumeAt: "Resume at {t}", fromStart: "Start Over", nothingHere: "Nothing here yet.",
    musicRecent: "Music · Recently Played", musicNew: "Music · New", booksBegun: "Audiobooks · Continue Listening",
    booksNew: "Audiobooks · New", podcastsBegun: "Podcasts · Continue Listening", podcastsNew: "Podcasts · New Episodes",
    episode: "S{s} E{e}", previous: "Previous", next: "Next", rewind: "Rewind", forward: "Fast Forward", stop: "Stop",
  },
  de: {
    signIn: "Anmelden", signInNote: "Mit deinem Jellyfin-Konto.", server: "Server", user: "Benutzername", password: "Passwort",
    signInButton: "Anmelden", signingIn: "Anmelden…", wrongLogin: "Benutzername oder Passwort ist falsch.",
    noServer: "Der Server ist nicht erreichbar.", devices: "Matinee", noDevices: "Kein Gerät mit Matinee ist an.",
    noDevicesHint: "Schalte einen Matinee ein, der mit diesem Jellyfin-Konto angemeldet ist. Er erscheint dann von selbst hier.", serverHint: "Neuer Versuch läuft…", signOut: "Abmelden",
    on: "Verbunden", off: "Nicht erreichbar", audio: "Ton", subtitles: "Untertitel", subtitlesOff: "Aus",
    padLabel: "Wischen zum Bewegen, tippen zum Auswählen", back: "Zurück", home: "Home", menu: "Menü", quieter: "Leiser",
    louder: "Lauter", playPause: "Wiedergabe/Pause", search: "Suchen", sent: "Gesendet", failed: "Nicht gesendet",
    signedOut: "Abgemeldet. Bitte neu anmelden.", left: "noch {t}", playingOn: "Läuft",
    library: "Bibliothek", remote: "Fernbedienung", continueWatching: "Weiterschauen", nextUp: "Als Nächstes", movies: "Filme",
    shows: "Serien", play: "Abspielen", resumeAt: "Weiter bei {t}", fromStart: "Von vorne", nothingHere: "Noch nichts da.",
    musicRecent: "Musik · Zuletzt gespielt", musicNew: "Musik · Neu", booksBegun: "Hörbücher · Weiterhören",
    booksNew: "Hörbücher · Neu", podcastsBegun: "Podcasts · Weiterhören", podcastsNew: "Podcasts · Neue Folgen",
    episode: "S{s} E{e}", previous: "Vorige", next: "Nächste", rewind: "Zurückspulen", forward: "Vorspulen", stop: "Stopp",
  },
};
const LANG = (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en";
const t = (k, vars = {}) => (WORDS[LANG][k] ?? WORDS.en[k]).replace(/\{(\w+)\}/g, (_, v) => vars[v] ?? "");
document.documentElement.lang = LANG;
document.querySelectorAll("[data-t]").forEach((el) => (el.textContent = t(el.dataset.t)));
document.querySelectorAll("[data-t-label]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.tLabel)));

const $ = (id) => document.getElementById(id);
// a name a screen reader keeps saying once something is typed, where a placeholder is gone
for (const [id, k] of [["server", "server"], ["user", "user"], ["password", "password"], ["text", "search"]]) {
  $(id).placeholder = t(k);
  $(id).setAttribute("aria-label", t(k));
}

/* ---------- What is kept on the phone ---------- */

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(`kino-remote-${k}`)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(`kino-remote-${k}`, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem(`kino-remote-${k}`); } catch {} },
};
let account = store.get("account"); // { server, token, userId }
let chosen = store.get("device"); // the DeviceId of the Matinee controlled, kept across its restarts
let deviceId = store.get("id");
if (!deviceId) {
  deviceId = crypto.randomUUID?.() ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
  store.set("id", deviceId);
}
const DEVICE_NAME = /iPad/.test(navigator.userAgent) ? "iPad" : /iPhone/.test(navigator.userAgent) ? "iPhone" : "Browser";

/* ---------- Jellyfin ---------- */

const quoted = (s) => String(s).replace(/["\\]/g, "");
function authHeader() {
  const h = `MediaBrowser Client="Matinee Remote", Device="${DEVICE_NAME}", DeviceId="${quoted(deviceId)}", Version="1.0"`;
  return account?.token ? `${h}, Token="${quoted(account.token)}"` : h;
}
class SignedOut extends Error {}
async function api(path, { method = "GET", body, server = account?.server } = {}) {
  const r = await fetch(server + path, {
    method,
    headers: { Authorization: authHeader(), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (r.status === 401 && account) throw new SignedOut();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.status === 204 ? null : r.headers.get("content-type")?.includes("json") ? r.json() : null;
}
function serverAddress(raw) {
  let s = raw.trim().replace(/\/+$/, "");
  if (s && !/^https?:\/\//i.test(s)) s = `https://${s}`;
  return s;
}

/* ---------- Screens ---------- */

let polling = null;
const live = () => !$("remote").hidden || !$("library").hidden; // the screens that follow what the Matinee does
function show(id) {
  for (const s of ["login", "devices", "remote", "library"]) $(s).hidden = s !== id;
  clearTimeout(polling);
  polling = null;
  if (id !== "library" || libraryStale()) scrollTo(0, 0);
  if (id === "devices") listDevices();
  if (live()) poll();
  if (id === "library" && libraryStale()) loadLibrary();
}
function signedOut(message) {
  if (account?.token) api("/Sessions/Logout", { method: "POST" }).catch(() => {});
  account = null;
  store.del("account");
  $("loginError").textContent = message || "";
  $("password").value = "";
  show("login");
}
let toastTimer;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 1800);
}

/* ---------- Signing in ---------- */

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const server = serverAddress($("server").value);
  $("server").value = server;
  $("loginError").textContent = "";
  $("loginButton").disabled = true;
  $("loginButton").textContent = t("signingIn");
  try {
    const r = await fetch(`${server}/Users/AuthenticateByName`, {
      method: "POST",
      headers: { Authorization: authHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({ Username: $("user").value.trim(), Pw: $("password").value }),
    });
    if (r.status === 401) throw new Error(t("wrongLogin"));
    if (!r.ok) throw new Error(t("noServer"));
    const data = await r.json();
    account = { server, token: data.AccessToken, userId: data.User.Id };
    store.set("account", account);
    store.set("server", server);
    $("password").value = "";
    show("devices");
  } catch (err) {
    $("loginError").textContent = err instanceof TypeError ? t("noServer") : err.message;
  } finally {
    $("loginButton").disabled = false;
    $("loginButton").textContent = t("signInButton");
  }
});

/* ---------- The Matinees ---------- */

// the account's Matinees that are on and take commands, by Jellyfin's sessions
async function kinos() {
  const sessions = await api(`/Sessions?ControllableByUserId=${encodeURIComponent(account.userId)}&ActiveWithinSeconds=960`);
  return (sessions || []).filter((s) => s.Client === "Matinee" && s.SupportsRemoteControl);
}
function nowLine(s) {
  const item = s.NowPlayingItem;
  if (!item) return "";
  return item.SeriesName ? `${item.SeriesName} · ${item.Name}` : item.Name;
}
async function listDevices() {
  const list = $("deviceList");
  try {
    const found = await kinos();
    list.replaceChildren(...found.map((s) => {
      const row = document.createElement("button");
      row.className = "row";
      const texts = document.createElement("span");
      texts.className = "texts";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = s.DeviceName;
      texts.append(name);
      const line = nowLine(s);
      if (line) {
        const sub = document.createElement("span");
        sub.className = "sub";
        sub.textContent = `${t("playingOn")}: ${line}`;
        texts.append(sub);
      }
      row.append(texts);
      if (s.DeviceId === chosen) {
        const check = document.createElement("span");
        check.className = "check";
        check.textContent = "✓";
        row.append(check);
      }
      row.addEventListener("click", () => choose(s.DeviceId));
      return row;
    }));
    list.hidden = !found.length;
    $("noDevices").hidden = found.length > 0;
    emptyText("noDevices", "noDevicesHint");
  } catch (err) {
    if (err instanceof SignedOut) return signedOut(t("signedOut"));
    list.hidden = true;
    $("noDevices").hidden = false;
    emptyText("noServer", "serverHint");
  }
  // one look at a time: two under way (the page back in front while one ran) end in one, none while the page is away
  clearTimeout(polling);
  polling = !$("devices").hidden && document.visibilityState === "visible" ? setTimeout(listDevices, 3000) : null;
}
function emptyText(title, hint) {
  $("noDevicesText").textContent = t(title);
  $("noDevicesHint").textContent = t(hint);
}
function choose(id) {
  chosen = id;
  store.set("device", id);
  show("library");
}
$("signOut").addEventListener("click", () => signedOut(""));
$("deviceButton").addEventListener("click", () => show("devices"));

/* ---------- The remote ---------- */

let session = null; // the chosen Matinee's session as last seen
let streams = { item: null, audio: [], subs: [] };
const clock = (secs) => {
  secs = Math.max(0, Math.round(secs));
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
let seeking = false;

async function poll() {
  try {
    const found = (await kinos()).find((s) => s.DeviceId === chosen) || null;
    session = found;
    showSession();
  } catch (err) {
    if (err instanceof SignedOut) return signedOut(t("signedOut"));
    session = null;
    showSession();
  }
  clearTimeout(polling);
  polling = live() && document.visibilityState === "visible" ? setTimeout(poll, 2000) : null;
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && live() && !polling) poll();
  if (document.visibilityState === "visible" && !$("devices").hidden && !polling) listDevices();
});

function showSession() {
  $("deviceName").textContent = session?.DeviceName || store.get("deviceName") || t("devices");
  if (session) store.set("deviceName", session.DeviceName);
  $("status").textContent = session ? t("on") : t("off");
  $("status").classList.toggle("off", !session);
  const item = session?.NowPlayingItem;
  $("now").hidden = !item;
  $("playPause").dataset.state = $("miniPlay").dataset.state = !item ? "" : session.PlayState?.IsPaused ? "paused" : "playing";
  $("mini").hidden = !item;
  if (!item) return;
  const cover = poster(item, 180);
  for (const img of [$("nowCover"), $("miniCover")]) {
    if (img.dataset.src === (cover || "")) continue;
    img.dataset.src = cover || "";
    img.hidden = !cover;
    if (cover) img.src = cover;
  }
  $("miniTitle").textContent = item.SeriesName || item.Name;
  $("miniWhat").textContent = item.SeriesName ? item.Name : item.ProductionYear || "";
  $("nowTitle").textContent = item.SeriesName || item.Name;
  $("nowWhat").textContent = item.SeriesName ? item.Name : item.ProductionYear || "";
  $("nowWhat").hidden = !$("nowWhat").textContent;
  const total = (item.RunTimeTicks || 0) / 1e7, pos = (session.PlayState?.PositionTicks || 0) / 1e7;
  const seek = $("seek");
  seek.max = String(Math.max(1, Math.round(total)));
  if (!seeking) {
    seek.value = String(Math.round(pos));
    seekShown(pos, total);
  }
  // the audio and subtitles of the file itself (a stream Jellyfin converts has others): asked once per item
  const direct = session.PlayState?.PlayMethod === "DirectPlay";
  $("tracks").hidden = !direct;
  if (direct && streams.item !== item.Id) loadStreams(item.Id);
}
function seekShown(pos, total) {
  $("seek").style.setProperty("--p", `${total ? (100 * pos) / total : 0}%`);
  $("elapsed").textContent = clock(pos);
  $("remaining").textContent = total ? `-${clock(total - pos)}` : "";
}
async function loadStreams(id) {
  const mine = (streams = { item: id, audio: [], subs: [] });
  try {
    const item = await api(`/Items/${encodeURIComponent(id)}?userId=${encodeURIComponent(account.userId)}`);
    const all = item?.MediaSources?.[0]?.MediaStreams || item?.MediaStreams || [];
    mine.audio = all.filter((s) => s.Type === "Audio");
    mine.subs = all.filter((s) => s.Type === "Subtitle");
  } catch {}
  if (streams !== mine) return; // the next one plays already: its own are asked for
  $("subsButton").hidden = !streams.subs.length;
  $("audioButton").hidden = streams.audio.length < 2;
  $("tracks").hidden = $("subsButton").hidden && $("audioButton").hidden;
}

/* ---------- Sending ---------- */

async function send(path, body) {
  if (!session) return toast(t("off"));
  try {
    await api(`/Sessions/${encodeURIComponent(session.Id)}${path}`, { method: "POST", body });
    return true;
  } catch (err) {
    if (err instanceof SignedOut) return signedOut(t("signedOut"));
    toast(t("failed"));
    return false;
  }
}
const command = (name, args = {}) => send("/Command", { Name: name, Arguments: args });
async function playstate(cmd, query = "") {
  const sent = await send(`/Playing/${cmd}${query}`);
  if (sent && live()) { // what it changed (paused, the next episode) shown soon, not at the next look
    clearTimeout(polling);
    polling = setTimeout(poll, 600);
  }
  return sent;
}

// a key held goes on: the volume, winding. A key pressed with the keyboard comes as a click without a pointer.
const HOLD = 420, AGAIN = 170; // ms until a held key repeats, and between its repeats
function holdable(el, fire) {
  let wait, again;
  const stop = () => {
    clearTimeout(wait);
    clearInterval(again);
    el.classList.remove("pressed");
  };
  el.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    stop();
    el.classList.add("pressed");
    fire();
    wait = setTimeout(() => (again = setInterval(fire, AGAIN)), HOLD);
  });
  for (const end of ["pointerup", "pointercancel", "pointerleave"]) el.addEventListener(end, stop);
  el.addEventListener("click", (e) => { if (e.detail === 0) fire(); });
  el.addEventListener("contextmenu", (e) => e.preventDefault());
}
document.querySelectorAll("[data-cmd], [data-play]").forEach((b) => {
  const fire = () => (b.dataset.cmd ? command(b.dataset.cmd) : playstate(b.dataset.play));
  if ("repeat" in b.dataset) holdable(b, fire);
  else b.addEventListener("click", fire);
});

$("seek").addEventListener("input", () => {
  seeking = true;
  seekShown(Number($("seek").value), Number($("seek").max));
});
$("seek").addEventListener("change", async () => {
  await playstate("Seek", `?seekPositionTicks=${Math.round(Number($("seek").value) * 1e7)}`);
  setTimeout(() => (seeking = false), 2500); // until the next report says where it is now
});

$("searchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  if (await command("SendString", { String: text })) {
    $("text").value = "";
    toast(t("sent"));
  }
});

/* ---------- The touch surface: swipe to move, tap to select (at an edge: that way) ---------- */

const pad = $("pad"), flash = $("flash");
const STEP = 46; // pixels a swipe goes for one step
const DIRS = { up: "MoveUp", down: "MoveDown", left: "MoveLeft", right: "MoveRight" };
let touch = null, holdWait, holdAgain;
function endHold() {
  clearTimeout(holdWait);
  clearInterval(holdAgain);
}
// where on the surface: the middle (null) or towards one of its edges
function edge(e) {
  const r = pad.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
  if (Math.hypot(x, y) < 0.3) return null;
  return Math.abs(x) > Math.abs(y) ? (x > 0 ? "right" : "left") : (y > 0 ? "down" : "up");
}
function glow(dir) {
  const at = { up: "50% 0%", down: "50% 100%", left: "0% 50%", right: "100% 50%", ok: "50% 50%" }[dir];
  flash.style.background = `radial-gradient(circle at ${at}, rgba(255,255,255,0.16), transparent 55%)`;
  flash.classList.add("on");
  requestAnimationFrame(() => requestAnimationFrame(() => flash.classList.remove("on")));
}
function step(dir) {
  glow(dir);
  command(DIRS[dir]);
}
pad.addEventListener("pointerdown", (e) => {
  pad.setPointerCapture(e.pointerId);
  endHold();
  touch = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false, held: false };
  // held at an edge: that way again and again, for a long list
  const dir = edge(e), held = touch;
  if (dir) {
    holdWait = setTimeout(() => {
      if (touch !== held || held.moved) return;
      held.held = true;
      step(dir);
      holdAgain = setInterval(() => step(dir), AGAIN);
    }, HOLD);
  }
});
pad.addEventListener("pointermove", (e) => {
  if (!touch || e.pointerId !== touch.id || touch.held) return;
  let dx = e.clientX - touch.x, dy = e.clientY - touch.y;
  // a long swipe goes several steps, one each STEP along its main direction
  while (Math.max(Math.abs(dx), Math.abs(dy)) >= STEP) {
    touch.moved = true;
    endHold();
    if (Math.abs(dx) > Math.abs(dy)) {
      step(dx > 0 ? "right" : "left");
      touch.x += Math.sign(dx) * STEP;
    } else {
      step(dy > 0 ? "down" : "up");
      touch.y += Math.sign(dy) * STEP;
    }
    dx = e.clientX - touch.x;
    dy = e.clientY - touch.y;
  }
});
pad.addEventListener("pointerup", (e) => {
  if (!touch || e.pointerId !== touch.id) return;
  const done = touch.moved || touch.held;
  touch = null;
  endHold();
  if (done) return;
  const dir = edge(e);
  if (!dir) {
    glow("ok");
    return command("Select");
  }
  step(dir);
});
pad.addEventListener("pointercancel", () => {
  touch = null;
  endHold();
});
pad.addEventListener("contextmenu", (e) => e.preventDefault());

/* ---------- The audio and subtitles ---------- */

function sheet(title, rows) {
  $("sheetTitle").textContent = title;
  $("sheetList").replaceChildren(...rows.map((r) => {
    const row = document.createElement("button");
    row.className = "row";
    const texts = document.createElement("span");
    texts.className = "texts";
    const name = document.createElement("span");
    name.textContent = r.label;
    texts.append(name);
    row.append(texts);
    if (r.on) {
      const check = document.createElement("span");
      check.className = "check";
      check.textContent = "✓";
      row.append(check);
    }
    row.addEventListener("click", () => { closeSheet(); r.pick(); });
    return row;
  }));
  $("sheetBg").hidden = false;
}
function closeSheet() { $("sheetBg").hidden = true; }
$("sheetBg").addEventListener("click", (e) => { if (e.target === $("sheetBg")) closeSheet(); });
const streamName = (s) => s.DisplayTitle || s.Title || s.Language || `#${s.Index}`;
$("audioButton").addEventListener("click", () => {
  const now = session?.PlayState?.AudioStreamIndex;
  sheet(t("audio"), streams.audio.map((s) => ({
    label: streamName(s), on: s.Index === now, pick: () => command("SetAudioStreamIndex", { Index: String(s.Index) }),
  })));
});
$("subsButton").addEventListener("click", () => {
  const now = session?.PlayState?.SubtitleStreamIndex;
  sheet(t("subtitles"), [
    { label: t("subtitlesOff"), on: now == null || now < 0, pick: () => command("SetSubtitleStreamIndex", { Index: "-1" }) },
    ...streams.subs.map((s) => ({
      label: streamName(s), on: s.Index === now, pick: () => command("SetSubtitleStreamIndex", { Index: String(s.Index) }),
    })),
  ]);
});

/* ---------- The library: covers to swipe through, a tap plays one on the Matinee ---------- */

const me = () => encodeURIComponent(account.userId);
// an item's cover (an episode's: its show's), h pixels high; null if it has none
function poster(item, h = 360) {
  const [id, tag] = item.Type === "Episode" && item.SeriesId && item.SeriesPrimaryImageTag
    ? [item.SeriesId, item.SeriesPrimaryImageTag] : [item.Id, item.ImageTags?.Primary];
  if (!tag || !account) return null;
  return `${account.server}/Items/${encodeURIComponent(id)}/Images/Primary?fillHeight=${h}&fillWidth=${Math.round((h * 2) / 3)}&quality=90&tag=${encodeURIComponent(tag)}`;
}
const episodeNo = (item) => (item.IndexNumber != null ? t("episode", { s: item.ParentIndexNumber ?? 1, e: item.IndexNumber }) : "");
const PAGE = 40; // the covers asked for at once in a long shelf
const SHELVES = [
  { title: "continueWatching", path: () => `/UserItems/Resume?userId=${me()}&mediaTypes=Video&limit=24` },
  { title: "nextUp", path: () => `/Shows/NextUp?userId=${me()}&limit=24` },
  { title: "movies", paged: true, path: (from) => `/Items?userId=${me()}&includeItemTypes=Movie&recursive=true&sortBy=SortName&sortOrder=Ascending&startIndex=${from}&limit=${PAGE}` },
  { title: "shows", paged: true, path: (from) => `/Items?userId=${me()}&includeItemTypes=Series&recursive=true&sortBy=SortName&sortOrder=Ascending&startIndex=${from}&limit=${PAGE}` },
  ...["musicRecent", "musicNew", "booksBegun", "booksNew", "podcastsBegun", "podcastsNew"].map((id) => ({ title: id, kino: id })),
];
let libraryRun = 0; // the library's loads belong to the last time it was opened
let libraryAt = 0, libraryFor = null; // when it was loaded, and for which Matinee and account
const LIBRARY_KEEP = 60000; // back on the library within a minute: as it was, where it was
const libraryStale = () => Date.now() - libraryAt > LIBRARY_KEEP || libraryFor !== `${account?.userId}|${chosen}`;

// The parts the phone is not signed in to (music, audiobooks, podcasts): the rows a Matinee put with the
// Jellyfin account for it (backend/remote_shelf.py), each {id, items: [{kind, id, title, sub, pic, pos, duration}]};
// asked once for all of them. Only what a tile needs is taken, and a picture only as data or from https.
const KINO_KINDS = new Set(["album", "book", "episode"]);
let kinoShelves = null;
function kinoList(id) {
  kinoShelves ??= api(`/DisplayPreferences/kino-remote?userId=${me()}&client=kino-remote`).then((p) => {
    try { return JSON.parse(p?.CustomPrefs?.["kino-remote"] || "{}").shelves || []; } catch { return []; }
  });
  return kinoShelves.then((shelves) => ((Array.isArray(shelves) ? shelves : []).find((s) => s?.id === id)?.items || [])
    .filter((x) => x && KINO_KINDS.has(x.kind) && typeof x.id === "string" && x.id.length <= 200)
    .map((x) => ({ kino: true, kind: x.kind, id: x.id, title: String(x.title || ""), sub: String(x.sub || ""),
      pic: typeof x.pic === "string" && /^(data:image\/[\w.+-]+;base64,|https:\/\/)/.test(x.pic) ? x.pic : null,
      pos: Number(x.pos) > 0 ? Number(x.pos) : 0, duration: Number(x.duration) > 0 ? Number(x.duration) : 0 })));
}
// one of them, square as a cover
function kinoCard(x) {
  const el = document.createElement("button");
  el.className = "card square";
  const art = document.createElement("span");
  art.className = "art";
  if (x.pic) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = x.pic;
    img.addEventListener("error", () => img.remove());
    art.append(img);
  }
  if (x.pos > 0 && x.duration > 0) {
    const bar = document.createElement("span"), fill = document.createElement("i");
    bar.className = "bar";
    fill.style.width = `${Math.min(100, (100 * x.pos) / x.duration)}%`;
    bar.append(fill);
    art.append(bar);
  }
  const name = document.createElement("span"), sub = document.createElement("span");
  name.className = "name";
  name.textContent = x.title;
  sub.className = "sub";
  sub.textContent = x.sub;
  el.append(art, name, sub);
  el.addEventListener("click", () => pickKino(x));
  return el;
}
function pickKino(x) {
  // what Matinee takes: its kind and id
  const play = (pos) => playOther({ kind: x.kind, id: x.id, ...(pos == null ? {} : { pos }) });
  const begun = x.pos > 0 && x.kind === "episode";
  sheet(x.title || t("library"), begun
    ? [{ label: t("resumeAt", { t: clock(x.pos) }), pick: () => play(null) }, { label: t("fromStart"), pick: () => play(0) }]
    : [{ label: t("play"), pick: () => play(null) }]);
}
function card(item) {
  if (item.kino) return kinoCard(item);
  const el = document.createElement("button");
  el.className = "card";
  const art = document.createElement("span");
  art.className = "art";
  const src = poster(item);
  if (src) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = src;
    img.addEventListener("error", () => img.remove());
    art.append(img);
  }
  const played = item.UserData?.PlayedPercentage;
  if (played > 0 && played < 100) {
    const bar = document.createElement("span"), fill = document.createElement("i");
    bar.className = "bar";
    fill.style.width = `${played}%`;
    bar.append(fill);
    art.append(bar);
  }
  const name = document.createElement("span"), sub = document.createElement("span");
  name.className = "name";
  name.textContent = item.SeriesName || item.Name || "";
  sub.className = "sub";
  sub.textContent = item.Type === "Episode" ? [episodeNo(item), item.Name].filter(Boolean).join(" · ") : item.ProductionYear || "";
  el.append(art, name, sub);
  el.addEventListener("click", () => pick(item));
  return el;
}
async function loadLibrary() {
  const run = ++libraryRun;
  kinoShelves = null;
  libraryAt = Date.now();
  libraryFor = `${account?.userId}|${chosen}`;
  $("shelves").replaceChildren();
  $("libraryEmpty").hidden = true;
  let failed = 0, shown = 0;
  await Promise.all(SHELVES.map(async (shelf, at) => {
    let data;
    try {
      data = shelf.kino ? { Items: await kinoList(shelf.kino) } : await api(shelf.path(0));
    } catch (err) {
      if (err instanceof SignedOut && run === libraryRun) signedOut(t("signedOut"));
      return failed++;
    }
    const items = data?.Items || [];
    if (run !== libraryRun || !items.length) return;
    shown++;
    const box = document.createElement("div"), title = document.createElement("h2"), strip = document.createElement("div");
    box.className = "shelf";
    box.style.order = String(at); // in the shelves' order, whichever answers first
    title.textContent = t(shelf.title);
    strip.className = "strip";
    strip.append(...items.map(card));
    box.append(title, strip);
    $("shelves").append(box);
    if (!shelf.paged) return;
    // a long shelf: the next covers as its end comes near
    let have = items.length, loading = false;
    const total = data.TotalRecordCount ?? have;
    strip.addEventListener("scroll", async () => {
      if (loading || have >= total || strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 800) return;
      loading = true;
      try {
        const more = (await api(shelf.path(have)))?.Items || [];
        if (!more.length) have = total;
        have += more.length;
        strip.append(...more.map(card));
      } catch {}
      loading = false;
    }, { passive: true });
  }));
  if (failed && !shown) libraryAt = 0; // asked again next time
  if (run !== libraryRun || shown || $("library").hidden) return;
  $("libraryEmpty").textContent = failed ? t("noServer") : t("nothingHere");
  $("libraryEmpty").hidden = false;
}
// a cover tapped: a show's next episode (else its first), a film or an episode itself; asked before it plays
async function pick(item) {
  try {
    if (item.Type === "Series") {
      const id = encodeURIComponent(item.Id);
      const next = (await api(`/Shows/NextUp?userId=${me()}&seriesId=${id}&limit=1`))?.Items?.[0]
        || (await api(`/Shows/${id}/Episodes?userId=${me()}&limit=1`))?.Items?.[0];
      if (!next) return toast(t("nothingHere"));
      item = next;
    }
  } catch (err) {
    if (err instanceof SignedOut) return signedOut(t("signedOut"));
    return toast(t("noServer"));
  }
  const title = item.Type === "Episode" ? [item.SeriesName, episodeNo(item), item.Name].filter(Boolean).join(" · ") : item.Name;
  const at = (item.UserData?.PlaybackPositionTicks || 0) / 1e7;
  sheet(title, at > 0
    ? [{ label: t("resumeAt", { t: clock(at) }), pick: () => playOn(item.Id) }, { label: t("fromStart"), pick: () => playOn(item.Id, 0) }]
    : [{ label: t("play"), pick: () => playOn(item.Id) }]);
}
// the Matinee's session as it is now: it may have restarted since it was last looked at
async function freshSession() {
  try {
    session = (await kinos()).find((s) => s.DeviceId === chosen) || null;
    return true;
  } catch (err) {
    if (err instanceof SignedOut) signedOut(t("signedOut"));
    session = null;
    return false;
  }
}
// Jellyfin's "Play on": Matinee goes on where it was left unless told where to start
async function playOn(id, startTicks) {
  if (!(await freshSession()) && !account) return;
  const from = startTicks == null ? "" : `&startPositionTicks=${startTicks}`;
  if (await send(`/Playing?playCommand=PlayNow&itemIds=${encodeURIComponent(id)}${from}`)) show("remote");
}
// what Jellyfin's "Play on" cannot name (an album, a book, an episode of the parts above): a message Matinee takes as
// "play this" (REMOTE_PLAY_HEADER in backend/live_events.py)
async function playOther(what) {
  if (!(await freshSession()) && !account) return;
  if (await send("/Message", { Header: "kino-remote-play", Text: JSON.stringify(what), TimeoutMs: 1000 })) show("remote");
}
$("libraryButton").addEventListener("click", () => show("library"));
$("libraryBack").addEventListener("click", () => show("remote"));
$("miniOpen").addEventListener("click", () => show("remote"));

/* ---------- Start ---------- */

(async () => {
  // the server's address, prefilled: the one used last, else config.json beside this page (written where it is put)
  let server = store.get("server");
  if (!server) {
    try { server = (await (await fetch("config.json", { cache: "no-store" })).json()).server; } catch {}
  }
  $("server").value = server || "";
  if (!account) return show("login");
  show(chosen ? "library" : "devices");
})();
