"use strict";
// Matinee's page, part 18 of 18: the screensaver and the start.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Screensaver ---------- */

// Someone is there: told to the backend at most once a minute (it switches off after hours without a key)
let activeAt = 0;
function noteActive() {
  if (Date.now() - activeAt < 60000) return;
  activeAt = Date.now();
  fetch("/active", { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => {});
}

// cage never turns the screen off: after a while without a key the UI gives way to the screensaver, so
// nothing stands still on the TV for hours: black with a dim clock that moves now and then, the weather below it
// and, with music, the song.
let SAVER_AFTER = 10 * 60 * 1000; // from the settings, null when off; shortened in the tests
const saver = $("#saver"), saverClock = $("#saver .clock");
let saverTimer, saverTick, saverGen = 0;

function useSaver(minutes) {
  SAVER_AFTER = minutes === "off" ? null : Number(minutes) * 60 * 1000;
  wake();
}

// Restart the countdown; true if the screensaver was showing and has been closed
function wake() {
  saverGen++; // a screensaver still asking for the weather does not come any more
  clearTimeout(saverTimer);
  if (SAVER_AFTER) saverTimer = setTimeout(startSaver, SAVER_AFTER);
  if (saver.hidden) return false;
  saver.hidden = true;
  clearInterval(saverTick);
  return true;
}

async function startSaver() {
  if (polling) return wake(); // the player is open: count again from now
  const gen = saverGen;
  const w = await getJson("/weather");
  saverWeather = w?.temp != null ? w : null;
  if (gen !== saverGen || !saver.hidden) return; // a key meanwhile
  placeClock();
  saver.hidden = false;
  saverTick = setInterval(() => {
    if (++saverTicks % 30 === 0) getJson("/weather").then((x) => { saverWeather = x?.temp != null ? x : saverWeather; }); // every 15 minutes
    saverClock.classList.add("out");
    setTimeout(placeClock, 1500); // fade out, move, fade in
  }, 30000);
}

// The weather under the clock: its symbol, the temperature, and the day's highest and lowest
let saverWeather = null, saverTicks = 0;
const WEATHER = [[[0], "wClear"], [[1, 2], "wPartly"], [[3], "wCloudy"], [[45, 48], "wFog"],
  [[51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82], "wRain"], [[71, 73, 75, 77, 85, 86], "wSnow"], [[95, 96, 99], "wStorm"]];
// The settings' tile: no picture of the library behind it but a calm graphite (style.css), and at the top the time
// and the date large, the weather beside the date, as a lock screen; kept current while the tile has the focus
const DATE_LONG = new Intl.DateTimeFormat(LANG, { weekday: "long", day: "numeric", month: "long" });
let deviceTimer;
function deviceStage(el) {
  stageArt(null);
  const draw = () => {
    const box = $("#stage-title");
    if (document.activeElement !== el || !box) return clearInterval(deviceTimer);
    const now = new Date(), w = shelfCache.get("settings")?.data.o.weather;
    const html = `<div class="device-time">${esc(timeOfDay(now))}</div><div class="device-date">${esc(DATE_LONG.format(now))}`
      + `${w?.temp != null ? `<span class="sep"></span>${weatherLine(w)}${w.place ? `<span class="place">${esc(w.place)}</span>` : ""}` : ""}</div>`;
    if (box.dataset.html === html) return;
    const fresh = !box.querySelector(".device-time");
    box.dataset.html = box.innerHTML = html;
    if (fresh) { box.classList.remove("shown"); void box.offsetWidth; box.classList.add("shown"); }
  };
  clearInterval(deviceTimer);
  draw();
  deviceTimer = setInterval(draw, 1000); // the weather comes with the shelf a moment later, the minute changes
}

function weatherLine(w) {
  if (!w) return "";
  const kind = WEATHER.find(([codes]) => codes.includes(w.code))?.[1] ?? "wCloudy";
  const icon = kind === "wClear" && !w.day ? WEATHER_ICONS.wNight : WEATHER_ICONS[kind];
  return `<span class="weather">${icon}<b>${Number(w.temp)}°</b>${esc(t(kind))} · ${Number(w.hi)}° / ${Number(w.lo)}°</span>`;
}

function placeClock() {
  const song = musicState.active && musicState.queue[musicState.index];
  const now = new Date();
  saverClock.innerHTML = esc(timeOfDay(now))
    + `<span class="date">${esc(now.toLocaleDateString(LANG, { weekday: "long", day: "numeric", month: "long" }))}</span>` + weatherLine(saverWeather)
    + (song ? `<small>♪ ${esc(song.title)} · ${esc(song.artist ?? "")}</small>` : "");
  saverClock.style.left = `${5 + Math.random() * 65}vw`;
  saverClock.style.top = `${5 + Math.random() * 75}vh`;
  saverClock.classList.remove("out");
}

// Home held: the main menu, from wherever, on the section last used
function mainMenu() {
  stack = [];
  render({ name: "menu" }, lastSection && `menu-${lastSection}`);
}

// Home: to the start of the part one is in, from there to the main menu
function goHome() {
  if (!stack.length) return;
  const [menu, start] = stack;
  if (stack.length === 1) {
    stack = [];
    return render(menu, menu.focusKey);
  }
  stack = [menu];
  render(start.name === "home" ? { name: "home" } : { name: start.name, args: {} });
}

// Play key: play or resume movies and episodes directly, without the detail page
function quickPlay(el) {
  const it = reg[el.dataset.r];
  if (el.dataset.action === "play" || !it) return activate(el);
  if (it.Type === "Movie" || it.Type === "Episode") play(it.Id, true);
  else activate(el);
}

// Apply changes from the server live (e.g. watched further in another app),
// but not during playback and not while someone is navigating.
let liveTimer;
function liveRefresh() {
  clearTimeout(liveTimer);
  const busy = 3000 - (Date.now() - lastKey);
  if (polling || !USER || current.name === "menu" || ["ownchannels", "guide"].includes(current.name) || MUSIC_VIEWS.has(current.name) || BOOK_VIEWS.has(current.name) || POD_VIEWS.has(current.name)) return;
  if (busy > 0) { liveTimer = setTimeout(liveRefresh, busy); return; }
  refresh();
}
// The person's settings the page uses (from /start): at its start, and again when another device changed them
function personSettings(s) {
  if (s.screensaver != null) useSaver(String(s.screensaver));
  SPOILERS = s.spoilers !== "hide";
  BADGE_MODE = s.badges ?? "all";
  SOUNDS = SOUND_LEVELS[s.sounds] ?? SOUND_LEVELS.medium;
  SOUND_KIND = s.soundkind in SOUND_KINDS ? s.soundkind : "click";
}
// settings changed on another device (Same on Every Device) and taken by the backend: here too, at once; another
// language loads the page anew (its words come with it), only while nothing plays and nobody is typing
async function settingsSynced() {
  const s = await fetch("/start").then((r) => r.json()).catch(() => null);
  if (!s) return;
  personSettings(s);
  if (s.language && s.language !== LANG) {
    if (polling || typing()) return setTimeout(settingsSynced, 30000);
    location.reload();
  }
}
new EventSource("/events").onmessage = (e) => {
  if (e.data === "autooff") { activeAt = 0; return toast(t("autoOffSoon"), 60000); } // any key now keeps it on
  if (e.data === "settings") return settingsSynced();
  // a remote's Menu key, read by the backend (Chromium under cage drops it): taken as if it had come here
  if (e.data === "menukey") return document.dispatchEvent(new KeyboardEvent("keydown", { key: "ContextMenu" }));
  if (e.data === "playing") { wake(); return followPlayback(); } // another device went on here
  if (e.data === "music") { // another device's music goes on here: shown as if started here
    wake();
    musicPoll();
    if (current.name !== "nowplaying") navigate("nowplaying", {});
    return;
  }
  if (e.data.startsWith("remote:")) return remoteCommand(JSON.parse(e.data.slice(7))); // Jellyfin's remote control
  if (e.data.startsWith("handoffback:")) { musicPoll(); return toast(t("handoffBack", { name: e.data.slice(12) }), 5000); }
  similarCache.forEach((e) => (e.stale = true)); // watched marks in those rows may have changed
  shelfCache.clear();
  forgetHome(); // the home's rows too: the next one waits for the server
  titlesChanged(); // a title may have been added
  badgesAt = 0; // and an episode with it: counted again with the next page
  libCache.forEach((e) => (e.stale = true));
  genreCache.clear();
  clearTimeout(liveTimer);
  liveTimer = setTimeout(liveRefresh, 1200);
};

// What Matinee opens when it is switched on (the setting "startwith"): once a browser session, not again when the page
// only reloads (a new backend, the language changed)
function startWith(s) {
  try {
    if (sessionStorage.getItem("kino-started")) return;
    sessionStorage.setItem("kino-started", "1");
  } catch {}
  if (s.startwith === "last" && s.section) openSection(s.section);
  // the channel watched last, over the page of one's own channels (Back leads there)
  if (s.startwith === "live" && s.lastLive) {
    navigate("ownchannels");
    startPlayback("/tv/play", { live: s.lastLive });
  }
}

// The part used last (Jellyfin, Navidrome, the audiobooks) gets the focus in the main menu; the backend keeps it,
// as Chromium's profile on the thin client is gone after a restart
let lastSection = null;

// The logo of the start (index.html) gives way once the main menu stands, a moment after it showed at least (the
// boot splash went straight into it), at the latest after a few seconds whatever happens
const INTRO_SHOWN = performance.now();
function introOut(ready) {
  const intro = $("#intro");
  if (!intro) return;
  let done = false;
  const go = () => {
    if (done) return;
    done = true;
    // less motion: no flight into the logo, the main menu simply in its place
    if (document.documentElement.classList.contains("less-motion")) return intro.remove();
    setTimeout(() => {
      intro.classList.add("out");
      // gone once it has faded and the main menu has come in (style.css), whichever ends last: the menu's coming in
      // belongs to the logo's .out and would stop halfway without it
      let left = 2;
      const end = () => { if (--left === 0) intro.remove(); };
      intro.addEventListener("transitionend", (e) => { if (e.target === intro && e.propertyName === "opacity") end(); });
      // the tiles' own animations end on #view too (they bubble): only the menu's coming in counts
      const view = $("#view");
      const arrived = (e) => {
        if (e.target !== view || e.animationName !== "intro-arrive") return;
        view.removeEventListener("animationend", arrived);
        end();
      };
      view.addEventListener("animationend", arrived);
      setTimeout(() => intro.remove(), 4000); // whatever the transitions do
    }, Math.max(0, 400 - (performance.now() - INTRO_SHOWN)));
  };
  Promise.resolve(ready).finally(go);
  setTimeout(go, 5000);
}

(async () => {
  wake();
  let reopen = null;
  const s = await fetch("/start").then((r) => r.json()).catch(() => ({}));
  lastSection = s.section ?? null;
  personSettings(s);
  useEffects(s.effects ?? "auto");
  useTextSize(s.textsize);
  useMotion(s.motion);
  SIZE_AUTO = s.sizeAuto ?? "tv";
  MENU_PICTURE = s.menuPicture ?? null;
  useSize(s.size);
  SIMPLE = !!s.simple;
  // which services are set up, for the tiles: whether they answer is asked when one is opened (their session anew)
  if (s.services) {
    ndSession ??= { configured: s.services.navidrome };
    absState ??= { configured: s.services.audiobookshelf };
    pfState ??= { configured: s.services.podfetch };
  }
  if (SOUNDS) try { clickCtx(); audioRest(); } catch {} // ready before the first click
  readSinkVolume();
  // switched on: on Up Next, what was begun (Home goes back to the part used last); the logo gives way once it stands
  try { reopen = sessionStorage.getItem("kino-open"); sessionStorage.removeItem("kino-open"); } catch {}
  const welcome = s.welcome || reopen?.startsWith("welcome:");
  // the step a reload came from (the language chosen), not a settings page's part (kino-open "settings:…")
  const step = (reopen?.startsWith("welcome:") && reopen.split(":")[1]) || "lang";
  const menu = welcome ? render({ name: "welcome", args: { step } }) : render({ name: "menu" }, "menu-next");
  introOut(menu);
  musicPoll(); // music may still be playing after a reload
  // tell the backend the UI is there: on the thin client it starts Matinee again if that stops
  // refused (403): the backend has started anew with a new secret for the page (see allowed in backend/http_api.py), so
  // the page loads again to get it
  const alive = () => fetch("/alive", { method: "POST", headers: JSON_HEADERS, body: "{}" })
    .then((r) => { if (r.status === 403) location.reload(); }).catch(() => {});
  alive();
  setInterval(alive, 30000);
  if (welcome) return;
  if (reopen?.startsWith("settings")) navigate("settings", { part: reopen.split(":")[1] || "general" });
  else startWith(s);
})();
