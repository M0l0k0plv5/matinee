"use strict";
// Matinee's page, part 9 of 18: music.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Music (Navidrome) ---------- */

const MUSIC_VIEWS = new Set(["music", "albums", "artists", "artist", "album", "playlists", "playlist", "musicsearch",
  "nowplaying", "favorites", "radio"]);
let ndSession = null;
let jfUnset = false; // no Jellyfin server set up yet (a fresh install): its tile says how to sign in
// whether there is a Navidrome (the menu shows its tile) and whether Matinee is signed in to it
async function musicSession(fresh = false) {
  if (!ndSession || fresh) ndSession = await fetch("/music/session").then((r) => r.json()).catch(() => ({ configured: false }));
  return ndSession;
}

// Navidrome through the backend, which signs the requests; like api(), a newer render makes it STALE (unless it is
// asked for something that goes on whatever page is shown: songs for the queue)
async function nd(method, params = {}, forPage = true) {
  const gen = renderGen;
  const r = await fetch(`/nd/${method}?${new URLSearchParams(params)}`);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(t(`err_${data.error || "nd_unreachable"}`));
  if (forPage && gen !== renderGen) throw STALE;
  return data;
}

const SQUARE_W = tileWidth(0.115), COVER_W = tileWidth(0.3);
// Navidrome's covers, kept by the browser for a week: "v" counts up when what the backend sends changes (2: no
// placeholders, a 404 instead), so they are asked for anew
const cover = (id, size) => (id ? `/nd/getCoverArt?id=${encodeURIComponent(id)}&size=${size}&v=2` : null);
const sumSeconds = (songs) => songs.reduce((n, x) => n + (x.duration || 0), 0);

// Square tiles: albums, playlists and songs with their cover, artists round
function musicCard(x, kind, key) {
  const name = kind === "song" ? x.title : x.name;
  const sub = { album: x.artist, song: x.artist, playlist: x.songCount != null ? songCount(x.songCount) : "",
                artist: x.albumCount != null ? (x.albumCount === 1 ? t("albumOne") : t("albumCount", { n: x.albumCount })) : "" }[kind];
  return tile({ item: { ...x, kind }, key, action: `m${kind}`, shape: kind === "artist" ? "sq round" : "sq",
    art: cardImg(cover(x.coverArt, SQUARE_W), initials(name || "?")), label: name, sub: sub || null });
}
const songCount = (n) => (n === 1 ? t("song") : t("songs", { n }));
const musicRow = (title, items, kind, prefix) => !items?.length ? ""
  : `<section class="row"><h2>${esc(title)}</h2><div class="strip">${items.map((x, i) => musicCard(x, kind, `${prefix}-${i}`)).join("")}</div></section>`;
const musicGrid = (items, kind, prefix) => `<div class="grid">${items.map((x, i) => musicCard(x, kind, `${prefix}-${x.id ?? i}`)).join("")}</div>`;

Object.assign(views, {
  // Start of the music: what there is (and what is playing), then albums new, recent, often played, random
  async music() {
    const s = await musicSession(true);
    if (!s.ready) return serviceSignIn("navidrome", s.configured ? s.error || "nd_unreachable" : "nd_not_configured");
    const [lists, starred, playlists] = await Promise.all([
      Promise.all(["newest", "recent", "frequent", "random"].map((type) =>
        nd("getAlbumList2", { type, size: { random: 40, newest: 60 }[type] ?? 20 }).then((r) => r.albumList2.album ?? []))),
      nd("getStarred2").then((r) => r.starred2).catch(() => ({})), // only for the pictures on the tiles
      nd("getPlaylists").then((r) => r.playlists.playlist ?? []).catch(() => []), loadHidden()]);
    lists[1] = lists[1].filter(showAlbum);
    lists[2] = lists[2].filter(showAlbum);
    // above, each button shows its name over a wall of its covers
    const wallOf = (xs) => xs.map((x) => x.coverArt && cover(x.coverArt, tileWidth(0.1)));
    const sections = [
      ...(musicState.active ? [{ Id: "nowplaying", Name: t("nowPlaying"), Type: "NowPlaying" }] : []),
      { Id: "albums", Name: t("albums"), Type: "Albums", wall: wallOf(lists[0]) },
      { Id: "artists", Name: t("artists"), Type: "Artists",
        wall: wallOf([...lists[2], ...lists[1]]) },
      { Id: "favorites", Name: t("favorites"), Type: "Favorites",
        wall: wallOf([...(starred.album ?? []), ...(starred.song ?? [])]) },
      { Id: "playlists", Name: t("playlists"), Type: "Playlists", wall: wallOf(playlists) },
      { Id: "radio", Name: t("radio"), Type: "Radio" },
      { Id: "shuffle", Name: t("shuffleAll"), Type: "Shuffle", wall: wallOf(lists[3]) }];
    let html = libChips(sections.map((x, i) => libChip(x, `msec-${i}`, "msection")));
    html += musicRow(t("recentlyAdded"), lists[0].slice(0, 20), "album", "new"); // the 60 for the pictures of many
    html += musicRow(t("recentlyPlayed"), lists[1], "album", "recent");
    html += musicRow(t("mostPlayed"), lists[2], "album", "often");
    html += musicRow(t("randomAlbums"), lists[3].slice(0, 20), "album", "random");
    html += musicRow(t("moreToDiscover"), lists[3].slice(20), "album", "random2"); // fills the bottom of the screen
    view.innerHTML = `${await tabBar("music")}<div id="stage-art" class="beside"></div><div class="home music-home"><section class="hero" id="hero">${chipHero(sections[0])}</section><div class="rows"><div class="rows-track">${html}</div></div></div>`;
    view.dataset.plainHero = "1";
    const first = lists[0][0];
    if (first) setBackdrop(cover(first.coverArt, 1920));
    coverWall("music").then((urls) => { // the covers' wall of the main menu, until a tile has one of its own
      homeWall = { key: "music", urls };
      if (current.name === "music") tileWall(reg[document.activeElement?.dataset.r]);
    });
  },

  async albums({ sort = "alphabeticalByArtist" }) { // by artist first, an artist's albums together
    const page = await nd("getAlbumList2", { type: sort, size: ALBUMS_FIRST });
    const albums = page.albumList2.album ?? [];
    const chips = ALBUM_SORTS.map(([v, l]) => `<div class="chip f${v === sort ? " sel" : ""}" tabindex="-1" data-k="asort-${v}" data-action="albumsort" data-val="${v}">${t(l)}</div>`).join("");
    // how many once all are there (a page at a time; shuffled, never all)
    const count = albums.length < ALBUMS_FIRST && sort !== "random" ? ` <span class="count">${albums.length}</span>` : "";
    view.innerHTML = `<div class="grid-page">${kicker(partName("music"))}<h1>${t("albums")}${count}</h1><section class="row bar"><div class="strip">${chips}</div></section>
      ${albums.length ? musicGrid(albums, "album", "album") : `<div class="hint">${t("nothingFound")}</div>`}</div>`;
    // the focus on the first album, not on the sorting above (Back from an album or a chip keeps its own)
    view.querySelector(".grid .card")?.setAttribute("data-autofocus", "");
    if (albums[0]) setBackdrop(cover(albums[0].coverArt, 1920));
    if (albums.length === ALBUMS_FIRST && sort !== "random") moreAlbums(renderGen, sort, albums.length);
  },

  async artists() {
    const r = await nd("getArtists");
    const artists = (r.artists.index ?? []).flatMap((i) => i.artist ?? []);
    view.innerHTML = `<div class="grid-page">${kicker(partName("music"))}<h1>${t("artists")} <span class="count">${artists.length}</span></h1>
      ${artists.length ? musicGrid(artists, "artist", "artist") : `<div class="hint">${t("nothingFound")}</div>`}</div>`;
  },

  async artist({ id }) {
    const a = (await nd("getArtist", { id })).artist;
    const albums = a.album ?? [];
    // like an album: a fan of the artist's albums beside the name, all of them below
    view.innerHTML = `<div id="stage-art" class="beside"></div><div class="grid-page artist-page"><section class="hero with-poster">
        <div class="poster sq fan closed">${mosaic(albums.slice(0, 3).map((x) => x.coverArt).filter(Boolean), "square")}</div>
        <div class="info">${kicker(t("kindArtist"))}<h1 class="title">${esc(a.name)}</h1>
          <div class="meta"><span>${albums.length === 1 ? t("albumOne") : t("albumCount", { n: albums.length })}</span></div>
          <div class="actions"><button class="btn f" data-k="artist-shuffle" data-action="artistshuffle" data-id="${esc(a.id)}">${ICON_SHUFFLE}${t("shuffle")}</button></div></div></section>
      ${musicGrid(albums, "album", "album")}</div>`;
    if (albums[0]) setBackdrop(cover(albums[0].coverArt, 1920));
    pageWall(`artist-${a.id}`, albums.map((x) => x.coverArt));
    const fan = view.querySelector(".poster.fan");
    requestAnimationFrame(() => requestAnimationFrame(() => fan.classList.remove("closed"))); // it opens as the page comes
  },

  async album({ id }) {
    const al = (await nd("getAlbum", { id })).album;
    musicDetail(al, al.song ?? [], [al.artist && `<b>${esc(al.artist)}</b>`, al.year],
      starButton("album", al) + (al.artistId ? `<button class="btn f" data-k="toartist" data-action="toartist" data-id="${esc(al.artistId)}">${ICON_ARTIST}${t("toArtist")}</button>` : ""));
    const gen = renderGen; // the artist's albums behind it, once asked for
    if (al.artistId) nd("getArtist", { id: al.artistId }).then((r) => {
      if (gen === renderGen) pageWall(`album-${al.id}`, (r.artist.album ?? []).map((x) => x.coverArt));
    }).catch(() => {});
  },

  async playlists() {
    const r = await nd("getPlaylists");
    const lists = r.playlists.playlist ?? [];
    view.innerHTML = `<div class="grid-page">${kicker(partName("music"))}<h1>${t("playlists")} <span class="count">${lists.length}</span></h1>
      ${lists.length ? musicGrid(lists, "playlist", "pl") : `<div class="hint">${t("nothingFound")}</div>`}</div>`;
  },

  async playlist({ id }) {
    const pl = (await nd("getPlaylist", { id })).playlist;
    musicDetail(pl, pl.entry ?? [], [], "", true);
    pageWall(`playlist-${pl.id}`, (pl.entry ?? []).map((s) => s.coverArt));
  },

  async favorites() {
    const r = (await nd("getStarred2")).starred2;
    const artists = r.artist ?? [], albums = r.album ?? [], songs = r.song ?? [];
    pageSongs = songs;
    const part = (title, items, kind) => items.length ? `<h2 class="results-title">${title}</h2>${musicGrid(items, kind, kind)}` : "";
    view.innerHTML = `<div class="grid-page">${kicker(partName("music"))}<h1>${t("favorites")}</h1>
      ${songs.length ? `<div class="page-actions"><button class="btn f" data-k="fav-play" data-action="mplayall">${ICON_PLAY}${t("playSongs")}</button>
        <button class="btn f" data-k="fav-shuffle" data-action="mshuffleall">${ICON_SHUFFLE}${t("shuffle")}</button></div>` : ""}
      ${part(t("songsTitle"), songs, "song")}${part(t("albums"), albums, "album")}${part(t("artists"), artists, "artist")}
      ${artists.length || albums.length || songs.length ? "" : `<div class="hint">${t("noFavorites")}</div>`}</div>`;
  },

  async radio() {
    const r = await nd("getInternetRadioStations");
    const stations = r.internetRadioStations?.internetRadioStation ?? [];
    view.innerHTML = `<div class="grid-page">${kicker(partName("music"))}<h1>${t("radio")} <span class="count">${stations.length}</span></h1>${stations.length
      ? `<div class="grid">${stations.map((st) => tile({ item: { ...st, kind: "radio" }, key: `radio-${st.id}`, action: "mradio",
        shape: "sq", art: `<div class="ph icon">${ICON_RADIO}</div>`, label: st.name })).join("")}</div>`
      : `<div class="hint">${t("noStations")}</div>`}</div>`;
  },

  async musicsearch({ q = "" }) {
    view.innerHTML = searchPage();
    showQuery(q);
    await runMusicSearch(q);
  },

  nowplaying() {
    view.innerHTML = `<div class="nowplaying"><div class="np-cover"><img alt=""></div><div class="np-info">
      <div class="np-album"></div><h1 class="np-title"></h1><div class="np-artist"></div>
      <div class="np-time"><span class="np-pos"></span><div class="np-bar"><i></i></div><span class="np-dur"></span></div>
      <div class="actions np-buttons">
        <button class="btn f" data-k="np-prev" data-action="mctl" data-cmd="prev">${ICON_PREV}</button>
        <button class="btn f" data-k="np-back" data-action="mctl" data-cmd="back" hidden>${ICON_BACK30}</button>
        <button class="btn f" data-k="np-pause" data-action="mctl" data-cmd="pause" data-autofocus></button>
        <button class="btn f" data-k="np-next" data-action="mctl" data-cmd="next">${ICON_NEXT}</button>
        <button class="btn f" data-k="np-fwd" data-action="mctl" data-cmd="ahead" hidden>${ICON_FWD30}</button>
        <button class="btn f np-speed" data-k="np-speed" data-action="npspeed" hidden></button>
        <button class="btn f" data-k="np-star" data-action="npstar"></button>
        <button class="btn f" data-k="np-lyrics" data-action="nplyrics" aria-label="${esc(t("lyrics"))}">${ICON_LYRICS}</button>
        <button class="btn f" data-k="np-handoff" data-action="nphandoff" aria-label="${esc(t("handoffTip"))}">${ICON_TV}</button>
        <button class="btn f np-sleep" data-k="np-sleep" data-action="npsleep" aria-label="${esc(t("sleepTimer"))}">${ICON_MOON}<span></span></button></div>
      <h2 class="np-up">${t("upNextMusic")}</h2><div class="tracks np-queue"></div><div class="np-lyrics" hidden></div></div></div>`;
    lyrics = null; // the page is new: the lyrics kept of this song are drawn into it again
    showMusic(true);
    showLyrics(lyricsOn);
  },
});

// The parts of a start (Jellyfin's libraries, the music's albums, artists…, the audiobooks' libraries): small buttons
// with a symbol, as the seasons', not tiles as big as a row; the rows below are what the page is for
// (attrs: more attributes, as a search's text)
function libChip(x, key, action, attrs = "") {
  const icon = TILE_ICONS[x.icon ?? x.Type] ?? "";
  return `<div class="chip lib f" tabindex="-1" data-r="${reg.push(x) - 1}" data-k="${esc(key)}" data-action="${action}"${attrs}>${icon}${esc(x.Name)}</div>`;
}
// What Tailscale says of this computer, under System (read only; nothing without Tailscale)
const tsState = (ts) => ({ Running: "tsOn", Stopped: "tsOff", NeedsLogin: "tsLogin", NeedsMachineAuth: "tsLogin", Starting: "tsStarting" }[ts.state] ?? "tsUnknown");
function tailscaleAbout(ts, about) {
  if (!ts) return "";
  const rows = [[t("tsState"), t(tsState(ts))]];
  if (ts.state === "Running") {
    rows.push([t("tsName"), ts.dns || ts.name], [t("tsAddress"), ts.ips?.join(", ")], [t("tsNet"), ts.tailnet],
      [t("tsDevices"), t("tsOnline", { n: ts.online, of: ts.peers })]);
    if (ts.exit) rows.push([t("tsExit"), ts.exit]);
  }
  return `<h2 class="settings-part">Tailscale</h2>${about(rows)}`;
}
// the parts at the top of a start without a heading; further down (the latest searches) under one
const libChips = (chips, title = "") => (chips.length
  ? `<section class="row libs">${title ? `<h2>${esc(title)}</h2>` : ""}<div class="chips">${chips.join("")}</div></section>` : ""); // many of them: in lines one above the other, all in view
// The setting "sounds": a soft tick for each step of the focus; made here, no sound file.
// SOUNDS is its loudness, 0 for off: silent until /start says (and in a browser without it)
const SOUND_LEVELS = { off: 0, low: 0.1, medium: 0.2, high: 0.35 };
// The setting "soundkind": what the tick sounds like, each made of a few tones (or a burst of noise) that start at
// once and die away (decay: seconds to fall to a third); no tone slides in pitch, that sounded like a bird. The
// gains are set so all of them sound about as loud (their energy measured, weighted as the ear hears: low tones
// need more, a short burst of noise much more).
const SOUND_KINDS = {
  click: [{ type: "sine", f: 520, decay: 0.007 }], // a low tone gone at once: a soft knock
  tap: [{ noise: true, f: 3200, q: 1.2, decay: 0.004, gain: 3.2 }], // a key tapped: a click of noise, no tone
  wood: [{ type: "triangle", f: 880, decay: 0.009, gain: 0.8 }, { type: "sine", f: 2150, decay: 0.004, gain: 0.35 }], // a woodblock
  glass: [{ type: "sine", f: 2640, decay: 0.03, gain: 0.3 }, { type: "sine", f: 3960, decay: 0.018, gain: 0.12 }], // a glass touched
  soft: [{ type: "sine", f: 240, decay: 0.012, gain: 1.4 }], // muffled, as through a cloth
};
let SOUNDS = 0, SOUND_KIND = "click", audioCtx = null, noiseBuffer = null;
// The clicks' output asks for no less buffering than the sound server's own (PipeWire: 1024 samples at 48 kHz, 21 ms).
// Asked for as little as possible (latencyHint 0), every waking of it switched the whole output down to 128 samples
// and back, the music playing on with it: on a Bluetooth speaker a short gap in the music at a key.
const CLICK_LATENCY = 0.025;
const clickCtx = () => (audioCtx ??= new AudioContext({ latencyHint: CLICK_LATENCY }));
function tick(kind = SOUND_KIND) {
  if (!SOUNDS) return;
  try {
    clickCtx();
    if (audioCtx.state === "suspended") audioCtx.resume();
    playTick(audioCtx, kind, SOUNDS * sinkFactor());
    audioRest();
  } catch {} // no sound output: nothing to hear anyway
}
// The sound output kept running with nothing to play, waking every few milliseconds: on the thin client an eighth
// of a core all the time, the screensaver too. It rests after a while without a click; the next key wakes it on its
// way in (input.js), so the click is there with it.
const AUDIO_REST = 10000;
let audioRestTimer;
function audioRest() {
  clearTimeout(audioRestTimer);
  audioRestTimer = setTimeout(() => { if (audioCtx?.state === "running") audioCtx.suspend().catch(() => {}); }, AUDIO_REST);
}
function audioWake() {
  if (audioCtx?.state !== "suspended") return;
  audioCtx.resume().catch(() => {});
  readSinkVolume(); // a while without a key: the volume may have changed meanwhile (a Bluetooth speaker's buttons)
}
// On the TV computer the clicks ride on the device's volume, which the player's volume keys change for a film: one
// turned up made every click as much louder afterwards. Above SINK_REF they are turned down by as much as the sound
// went up (wpctl's volume is cubic), so they are never louder than there; below it they stay as they were. The volume
// is read at the start, after the player, after a rest and whenever the volume bar shows it.
const SINK_REF = 60;
let sinkVolume = null, sinkAsk = true; // percent; null on a desktop, where the volume is the desktop's own business
const sinkFactor = () => (sinkVolume > SINK_REF ? (SINK_REF / sinkVolume) ** 3 : 1);
function readSinkVolume() {
  if (!sinkAsk) return;
  fetch("/volume", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ step: 0 }) })
    .then((r) => { if (r.status === 403) sinkAsk = false; return r.ok ? r.json() : null; }) // not the TV computer
    .then((x) => { if (x?.volume != null) sinkVolume = x.volume; })
    .catch(() => {});
}
function playTick(ctx, kind, level) {
  const at = ctx.currentTime;
  for (const p of SOUND_KINDS[kind] ?? SOUND_KINDS.click) {
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(level * (p.gain ?? 1), at + 0.0008);
    gain.gain.setTargetAtTime(0, at + 0.0008, p.decay);
    let src;
    if (p.noise) {
      if (noiseBuffer?.sampleRate !== ctx.sampleRate) {
        noiseBuffer = ctx.createBuffer(1, Math.round(ctx.sampleRate * 0.05), ctx.sampleRate);
        const d = noiseBuffer.getChannelData(0);
        for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      }
      src = Object.assign(ctx.createBufferSource(), { buffer: noiseBuffer });
      const band = ctx.createBiquadFilter();
      band.type = "bandpass";
      band.frequency.value = p.f;
      band.Q.value = p.q;
      src.connect(band).connect(gain);
    } else {
      src = ctx.createOscillator();
      src.type = p.type;
      src.frequency.setValueAtTime(p.f, at);
      src.connect(gain);
    }
    gain.connect(ctx.destination);
    src.start(at);
    src.stop(at + p.decay * 7 + 0.002);
  }
}
// Whether a show has all its aired episodes (the setting "badges"): on its poster "✓ 13" in green, or "11/13" in red;
// the same on its seasons, and its missing episodes dimmed in a season's row. "missing" shows only the red ones.
// The counts come from the backend (/badges), which keeps them for a while; empty without missing-episode placeholders.
// The setting "effects": "light" (Reduced) leaves out what weak graphics chips struggle with (the classes light:
// no frosted glass, nothing gliding, quicker moves, tokens.css) and the pictures that are only effects (plain: none
// at the top, no covers' walls; a film's own stays, see stageArt), the blurred backdrop behind everything kept. "calm"
// leaves out only those pictures: the glass and the dock's liquid glass stay. "auto" is Reduced on a computer with at
// most four cores or 4 GB of memory, as the thin clients Matinee is made for. The choices from before ("plain", "black")
// are Reduced too.
const weakDevice = () => (navigator.hardwareConcurrency || 8) <= 4 || (navigator.deviceMemory || 8) <= 4;
// The setting "textsize": larger text everywhere (tokens.css), for reading from further away
const useTextSize = (val) => document.documentElement.classList.toggle("large-text", val === "large");
// The setting "motion": "less" leaves out the springs, glides, zooms and drifting pictures (style.css: less-motion), as
// does the system asking for less motion; a TV computer under cage has no such setting of its own, so Matinee has one
const useMotion = (val) => document.documentElement.classList.toggle("less-motion",
  val === "less" || matchMedia("(prefers-reduced-motion: reduce)").matches);
// The setting "size": "desk" draws everything smaller (tokens.css), for a screen close by; Automatic is the desk but
// on the TV computer (SIZE_AUTO, from the backend)
let SIZE_AUTO = "tv";
const useSize = (val) => document.documentElement.classList.toggle("desk", (val === "auto" || !val ? SIZE_AUTO : val) === "desk");
function useEffects(val) {
  const reduced = val === "auto" ? weakDevice() : val !== "full" && val !== "calm", root = document.documentElement.classList;
  root.toggle("light", reduced);
  root.toggle("plain", reduced || val === "calm");
}
let BADGE_MODE = "all";
let BADGES = { series: {}, seasons: {} };
let badgesAt = 0;
function countBadge(c, chip = false) {
  if (!c || BADGE_MODE === "off" || !c[1]) return "";
  const done = c[0] >= c[1];
  if (done && BADGE_MODE === "missing") return "";
  const text = done ? `✓ ${c[1]}` : `${c[0]}/${c[1]}`;
  const tip = done ? t("allEpisodes") : (c[1] - c[0] === 1 ? t("episodeMissingOne") : t("episodesMissing", { n: c[1] - c[0] }));
  return `<span class="count-badge${chip ? " in-chip" : ""} ${done ? "complete" : "incomplete"}" title="${esc(tip)}">${esc(text)}</span>`;
}
async function loadBadges(force = false) {
  if (BADGE_MODE === "off" || (!force && Date.now() - badgesAt < 5 * 60 * 1000)) return;
  badgesAt = Date.now();
  const got = await fetch("/badges").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!got?.series) { badgesAt = 0; return; }
  const before = JSON.stringify(BADGES);
  BADGES = got;
  if (JSON.stringify(BADGES) !== before) showBadges();
}
// counts that came after a page was built: onto its show posters and season choices as they stand, without building it again
function showBadges() {
  for (const el of view.querySelectorAll(".card[data-r]")) {
    const it = reg[el.dataset.r];
    if (it?.Type !== "Series") continue;
    el.querySelector(".art .count-badge")?.remove();
    el.querySelector(".art")?.insertAdjacentHTML("beforeend", countBadge(BADGES.series[it.Id]));
  }
  for (const chip of view.querySelectorAll(".chip[data-action=season]")) {
    chip.querySelector(".count-badge")?.remove();
    chip.insertAdjacentHTML("beforeend", countBadge(BADGES.seasons[chip.dataset.id], true));
  }
}
let SPOILERS = true; // the setting "spoilers": off, episodes not watched yet without their still and text
// up to three pictures fanned out, as an artist's beside the name (covers by their id, or addresses), three of up to
// FAN_POOL, others each time the page is opened
const FAN_POOL = 12;
const fanUrl = (id) => (/^(\/|https?:)/.test(id) ? id : cover(id, tileWidth(0.11)));
const mosaic = (pics, shape = "square") => {
  const pool = [...new Set(pics.map(fanUrl))].slice(0, FAN_POOL), from = Math.floor(Math.random() * pool.length);
  const urls = [...pool.slice(from), ...pool.slice(0, from)].slice(0, 3);
  return urls.length ? `<div class="mosaic n${urls.length}" data-shape="${shape}">${urls.map((u) =>
    `<img src="${esc(u)}"${/^https?:/.test(u) ? ' crossorigin="anonymous"' : ""} loading="lazy" decoding="async" alt="">`).join("")}</div>` : "";
};
let ALBUMS_FIRST = 60, ALBUMS_PAGE = 120; // shortened in the tests
const ALBUM_SORTS = [["alphabeticalByArtist", "sortArtist"], ["alphabeticalByName", "sortName"], ["newest", "sortAdded"], ["random", "sortRandom"]];
async function moreAlbums(gen, sort, offset) {
  while (gen === renderGen) {
    // an answer that is not a page (an error, Navidrome's "failed") neither ends the albums nor says how many
    const page = await fetch(`/nd/getAlbumList2?type=${sort}&size=${ALBUMS_PAGE}&offset=${offset}`)
      .then((r) => (r.ok ? r.json() : null)).then((x) => (x?.albumList2 ? x : null)).catch(() => null);
    const albums = page?.albumList2.album ?? [];
    const grid = $(".grid-page .grid");
    if (gen !== renderGen || !grid) return;
    if (albums.length < ALBUMS_PAGE && page) $(".grid-page h1")?.insertAdjacentHTML("beforeend", ` <span class="count">${grid.children.length + albums.length}</span>`);
    if (!albums.length) return;
    grid.insertAdjacentHTML("beforeend", albums.map((x) => musicCard(x, "album", `album-${x.id}`)).join(""));
    offset += albums.length;
    // back from an album further down: it only comes with a later page; unless someone moved on already
    const want = current.focusKey, el = want && lastKey < current.shownAt && grid.querySelector(`[data-k="${CSS.escape(want)}"]`);
    if (el && document.activeElement !== el) focusEl(el, false);
    if (albums.length < ALBUMS_PAGE) return;
  }
}

// Album or playlist: cover, title, buttons, then the songs. OK on a song plays from there on.
let pageSongs = [];
function musicDetail(x, songs, meta, more = "", artists = false) {
  pageSongs = songs;
  const facts = [...meta, songs.length && songCount(songs.length), songs.length && duration(sumSeconds(songs) * TICKS)].filter(Boolean);
  const src = cover(x.coverArt, COVER_W);
  view.innerHTML = `<div id="stage-art" class="beside"></div><div class="detail music-detail moving"><section class="hero with-poster">
      <div class="poster sq">${src ? `<img src="${src}" decoding="async" alt="">` : ""}</div>
      <div class="info">${kicker(t(x.entry ? "kindPlaylist" : "kindAlbum"))}<h1 class="title">${esc(x.name)}</h1><div class="meta">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
        <div class="actions"><button class="btn f" data-k="mplay" data-action="mplayall">${ICON_PLAY}${t("play")}</button>
          <button class="btn f" data-k="mshuffle" data-action="mshuffleall">${ICON_SHUFFLE}${t("shuffle")}</button>${more}</div></div></section>
    <section class="tracks">${trackRows(songs, "track", artists || songs.some((s) => s.artist !== x.artist))}</section></div>`;
  if (src) setBackdrop(cover(x.coverArt, 1920));
}

function trackRows(songs, prefix, artists, playing = -1) {
  return songs.map((s, i) => `<div class="track f${i === playing ? " on" : ""}" tabindex="-1" data-k="${prefix}-${i}" data-action="${prefix === "queue" ? "mqueue" : "mtrack"}" data-i="${i}">
    <span class="no">${prefix === "queue" ? i + 1 : s.track || i + 1}</span><span class="t">${esc(s.title)}${artists && s.artist ? `<small>${esc(s.artist)}</small>` : ""}</span>
    <span class="d">${s.duration ? clock(s.duration * TICKS) : ""}</span></div>`).join("");
}

async function runMusicSearch(q) {
  const box = $("#results");
  if (!box) return;
  const term = q.trim();
  if (term.length < 2) {
    if (!term) return searchSuggestions(box, t("musicHint"));
    box.innerHTML = `<div class="hint">${t("oneMoreLetter")}</div>`;
    return;
  }
  let r;
  try {
    r = (await nd("search3", { query: term, artistCount: 12, albumCount: 30, songCount: 40 })).searchResult3;
  } catch (e) {
    if (e === STALE) return;
    if (current.name === "musicsearch" && (current.args?.q || "") === q) box.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
    return;
  }
  if (current.name !== "musicsearch" || (current.args?.q || "") !== q) return; // typed on meanwhile
  const artists = r.artist ?? [], albums = r.album ?? [], songs = r.song ?? [];
  pageSongs = songs;
  if (!artists.length && !albums.length && !songs.length) {
    box.innerHTML = nothingFoundHere(term);
    return;
  }
  const part = (title, items, kind) => items.length ? `<h2 class="results-title">${title}</h2>${musicGrid(items, kind, kind)}` : "";
  box.innerHTML = part(t("artists"), artists, "artist") + part(t("albums"), albums, "album") + part(t("songsTitle"), songs, "song");
}

async function musicAction(a, el, it) {
  if (a === "msection") {
    if (it.Id === "shuffle") {
      const r = await nd("getRandomSongs", { size: 200 }).catch((e) => { if (e !== STALE) toast(e.message, 5000); });
      if (r) musicPlay(r.randomSongs.song ?? [], 0);
    } else navigate(it.Id, {});
  }
  else if (a === "malbum") navigate("album", { id: it.id });
  else if (a === "martist") navigate("artist", { id: it.id });
  else if (a === "mplaylist") navigate("playlist", { id: it.id });
  else if (a === "msong") musicPlay(pageSongs, pageSongs.findIndex((x) => x.id === it.id));
  else if (a === "mtrack") musicPlay(pageSongs, Number(el.dataset.i));
  else if (a === "mplayall") musicPlay(pageSongs, 0);
  else if (a === "mshuffleall") musicPlay(shuffled(pageSongs), 0);
  else if (a === "toartist") navigate("artist", { id: el.dataset.id });
  else if (a === "albumsort") { current.args = { ...current.args, sort: el.dataset.val }; render(current, el.dataset.k); }
  else if (a === "artistshuffle") {
    try {
      const albums = (await nd("getArtist", { id: el.dataset.id })).artist.album ?? [];
      const songs = (await Promise.all(albums.map((al) => nd("getAlbum", { id: al.id })))).flatMap((r) => r.album.song ?? []);
      musicPlay(shuffled(songs), 0);
    } catch (e) { if (e !== STALE) toast(e.message, 5000); }
  }
  else if (a === "mctl") musicKey(el.dataset.cmd);
  else if (a === "npspeed") nextSpeed();
  else if (a === "bchapter") {
    const song = musicState.active && musicState.queue[musicState.index];
    if (song?.book) musicControl("seek", song.chapters[Number(el.dataset.i)].start);
  }
  else if (a === "mradio") playRadio(it.id);
  else if (a === "mstar") toggleStar(el.dataset.type, el.dataset.id, el.dataset.on !== "1");
  else if (a === "npstar") {
    const song = musicState.active && musicState.queue[musicState.index];
    if (song && !song.radio) toggleStar("song", song.id, !song.starred);
  }
  else if (a === "nplyrics") showLyrics(!lyricsOn);
  else if (a === "nphandoff") chooseHandoff(el);
  else if (a === "npsleep") cycleSleep();
  else if (a === "mqueue") musicControl("index", Number(el.dataset.i));
}

function starButton(type, x) {
  return `<button class="btn f" data-k="star" data-action="mstar" data-type="${type}" data-id="${esc(x.id)}" data-on="${x.starred ? 1 : 0}">${x.starred ? ICON_STAR : ICON_STAR_OPEN}${t(x.starred ? "unfavorite" : "favorite")}</button>`;
}

// Favorites: a star on songs, albums and artists in Navidrome
async function toggleStar(type, id, on) {
  const r = await fetch("/music/star", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ type, id, on }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  toast(t(on ? "favorited" : "unfavorited"));
  for (const list of [pageSongs, musicState.queue ?? []]) for (const x of list) if (x.id === id) x.starred = on ? "now" : undefined;
  if (current.name === "nowplaying") showMusic(true);
  else if (MUSIC_VIEWS.has(current.name)) refresh();
}

// "Continue on": the other Matinees of the account that are on; one chosen, the queue goes on there from where it is and
// stops here (it comes back here by itself if that one does not answer: the event "handoffback")
async function chooseHandoff(el) {
  const devices = await getJson("/handoff");
  if (!devices?.length) return toast(t("handoffNone"), 4000);
  showOptions(el, t("handoffTo"), devices.map((d) => ({ label: d.name, run: async () => {
    const r = await fetch("/music/handoff", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ device: d.id }) }).catch(() => null);
    if (!r?.ok) return toast(t("saveFailed"), 4000);
    toast(t("handoffGoing", { name: d.name }), 4000);
    musicPoll();
  } })));
}

async function playRadio(id) {
  const r = await fetch("/music/play", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ radio: id }) }).catch(() => null);
  if (!r?.ok) return toast(t("playFailed", { error: r?.status ?? "–" }), 5000);
  musicState = { active: true, index: 0, queue: [], paused: false };
  musicPoll();
  if (current.name !== "nowplaying") navigate("nowplaying", {});
}

// Lyrics instead of the queue: lines with times follow the song, plain ones scroll along with its progress
let lyricsOn = false, lyrics = null; // { id, synced, lines: [{ start, value }] }
async function showLyrics(on) {
  lyricsOn = on;
  const np = $(".nowplaying");
  if (!np) return;
  const radio = musicState.active && musicState.queue[musicState.index]?.radio;
  np.querySelector(".np-queue").hidden = on || !!radio;
  np.querySelector(".np-up").hidden = !!radio && !on;
  np.querySelector(".np-lyrics").hidden = !on;
  const book = musicState.active && musicState.queue[musicState.index]?.book;
  np.querySelector(".np-up").textContent = t(on ? "lyrics" : book ? "chapters" : "upNextMusic");
  np.querySelector('[data-k="np-lyrics"]').classList.toggle("on", on);
  if (on) await loadLyrics();
}

async function loadLyrics() {
  const song = musicState.active && musicState.queue[musicState.index];
  const box = $(".np-lyrics");
  if (!box || !song || lyrics?.id === song.id) return;
  lyrics = { id: song.id, lines: [] };
  box.innerHTML = "";
  if (song.radio) return (box.innerHTML = `<p class="hint">${t("noLyrics")}</p>`);
  const r = await fetch(`/nd/getLyricsBySongId?id=${encodeURIComponent(song.id)}`).then((x) => x.json()).catch(() => null);
  if (lyrics.id !== song.id) return; // the next song came meanwhile
  const found = (r?.lyricsList?.structuredLyrics ?? []).find((l) => l.line?.length);
  if (!found) return (box.innerHTML = `<p class="hint">${t("noLyrics")}</p>`);
  lyrics = { id: song.id, synced: !!found.synced, lines: found.line };
  box.innerHTML = found.line.map((l, i) => `<p data-i="${i}">${esc(l.value) || "&nbsp;"}</p>`).join("");
  followLyrics();
}

function followLyrics() {
  const box = $(".np-lyrics");
  if (!box || box.hidden || !lyrics?.lines.length) return;
  const s = musicState, pos = (s.pos || 0) * 1000;
  let y;
  if (lyrics.synced) {
    let i = lyrics.lines.findLastIndex((l) => (l.start ?? 0) <= pos);
    i = Math.max(0, i);
    box.querySelectorAll("p.on").forEach((p) => p.classList.remove("on"));
    const p = box.querySelector(`p[data-i="${i}"]`);
    p?.classList.add("on");
    box.classList.toggle("synced", !!p); // the others dimmed (style.css)
    y = p ? p.offsetTop - box.clientHeight / 3 : 0;
  } else {
    y = s.duration ? ((s.pos || 0) / s.duration) * (box.scrollHeight - box.clientHeight) : 0;
  }
  box.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
}

// Sleep timer: 30, 60, 90 minutes, (a book: at the end of the chapter,) at the end of the queue, off; the backend
// fades out and stops
async function cycleSleep() {
  const r = await fetch("/sleep", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ cycle: true }) }).then((x) => x.json()).catch(() => null);
  if (!r) return toast(t("saveFailed"), 4000);
  toast(r.label);
  musicState.sleep = r.sleep;
  showMusic();
}
const sleepText = (s) => (!s ? "" : s.end ? t("sleepEnd") : s.chapter ? t("sleepChapter") : t("minutes", { m: Math.max(1, Math.round(s.left / 60)) }));

function shuffled(list) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

async function musicPlay(songs, start) {
  if (!songs.length) return;
  const keep = songs.map(({ id, title, artist, album, albumId, coverArt, duration, starred }) => ({ id, title, artist, album, albumId, coverArt, duration, starred }));
  const r = await fetch("/music/play", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ songs: keep, start: Math.max(0, start) }) }).catch(() => null);
  if (!r?.ok) return toast(t("playFailed", { error: r?.status ?? "–" }), 5000);
  musicState = { active: true, index: Math.max(0, start), queue: keep, pos: 0, duration: keep[Math.max(0, start)].duration, paused: false };
  musicPoll();
  if (current.name !== "nowplaying") navigate("nowplaying", {});
}

// "Play Next" and "Add to Queue": the music goes on, the songs join the queue
async function musicQueue(x, next) {
  let songs;
  try {
    songs = x.kind === "album" ? (await nd("getAlbum", { id: x.id }, false)).album.song ?? []
      : x.kind === "playlist" ? (await nd("getPlaylist", { id: x.id }, false)).playlist.entry ?? [] : [x];
  } catch (e) { return toast(e.message, 5000); }
  if (!songs.length) return;
  const keep = songs.map(({ id, title, artist, album, albumId, coverArt, duration, starred }) => ({ id, title, artist, album, albumId, coverArt, duration, starred }));
  const r = await fetch("/music/queue", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ songs: keep, next }) }).catch(() => null);
  if (!r?.ok) return toast(t("playFailed", { error: r?.status ?? "–" }), 5000);
  const name = x.kind === "song" ? x.title : x.name;
  toast(t(next ? "queuedNext" : "queuedEnd", { name }));
  musicPoll();
}

// the buttons and keys of the music, which mean something else with a book
function musicKey(cmd) {
  const book = musicState.active && musicState.queue?.[musicState.index]?.book;
  if (cmd === "back" || (book && cmd === "prev")) return musicControl("skip", -30);
  if (cmd === "ahead" || (book && cmd === "next")) return musicControl("skip", 30);
  return musicControl(cmd);
}

async function musicControl(cmd, value) {
  await fetch("/music/control", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ cmd, value }) }).catch(() => null);
  musicPoll();
}

// What plays, asked every second while music plays: for "Now Playing" and the bar at the bottom
let musicState = { active: false }, musicTimer = null;
async function musicPoll() {
  clearTimeout(musicTimer);
  const s = await fetch(`/music/status${musicState.version != null ? `?have=${musicState.version}` : ""}`).then((r) => r.json()).catch(() => ({ active: false, unknown: true }));
  // mpv knows no position for a moment while it loads more of the song (the stream stalled): the same song keeps
  // the last one, rather than its line jumping to the start and back
  if (s.active && s.index === musicState.index && musicState.active) {
    s.pos ??= musicState.pos;
    s.duration ??= musicState.duration;
  }
  musicState = s.active ? { ...s, queue: s.queue ?? musicState.queue ?? [] } : { active: false, unknown: !!s.unknown };
  showMusic();
  if (musicState.active) musicTimer = setTimeout(musicPoll, 1000);
}

// The bar of the music playing can take the focus: down from a page's last row (move), OK opens Now Playing, OK
// held its options (a star), up or Back go back to where the focus came from
let nowbarFrom = null;
function toNowbar(from) {
  const bar = $("#nowbar");
  if (bar.hidden || !bar.offsetWidth) return; // none, or not on this page (the main menu)
  nowbarFrom = from;
  focusEl(bar);
}
function leaveNowbar() {
  const back = nowbarFrom?.isConnected ? nowbarFrom : view.querySelector(".f");
  nowbarFrom = null;
  if (back) focusEl(back);
}

// On the main menu (no bar there), the tile of what plays shows its cover: Music, Audiobooks for a book, Podcasts for an episode
function syncDockCover() {
  const song = musicState.active ? musicState.queue?.[musicState.index] : null;
  const src = song && !song.radio && songCover(song, tileWidth(0.12));
  const part = song ? (song.podcast != null ? "podcasts" : song.book ? "books" : "music") : null;
  const playing = src ? part : null;
  for (const id of ["music", "books", "podcasts"]) {
    const art = view.querySelector(`.menu [data-k=menu-${id}] .art`);
    if (!art) continue;
    // a ring going round the tile of what sounds (the radio too, which has no cover), standing still while paused
    let ring = art.parentElement.querySelector(".ring");
    if (id === part && !ring) {
      art.insertAdjacentHTML("beforebegin", `<i class="ring"></i>`);
      ring = art.previousElementSibling;
    } else if (id !== part) ring?.remove();
    ring?.classList.toggle("paused", !!musicState.paused);
    let img = art.querySelector("img.now");
    if (id !== playing) {
      img?.remove();
      continue;
    }
    if (!img) {
      art.insertAdjacentHTML("beforeend", `<img class="now" decoding="async" alt="">`);
      img = art.querySelector("img.now");
    }
    if (img.getAttribute("src") !== src) img.src = src;
    art.classList.toggle("paused", !!musicState.paused);
  }
}

function showMusic(fresh = false) {
  const s = musicState, song = s.active ? s.queue[s.index] : null;
  const bar = $("#nowbar");
  bar.hidden = !song || current.name === "nowplaying";
  document.body.classList.toggle("with-nowbar", !bar.hidden); // the starts make room for it (style.css)
  if (bar.hidden && document.activeElement === bar) leaveNowbar(); // the music ended while the bar had the focus
  syncNowTab();
  syncDockCover();
  if (song && !bar.hidden) {
    // built anew only when the song or what it says changes (not every second: its cover came anew each time and
    // the line could not glide); else only the line moves
    const sub = (s.lost ? t("lostWaiting") : song.radio ? s.icy : song.artist) ?? "";
    const key = JSON.stringify([song.id, song.title, sub, !!s.paused]);
    if (bar.dataset.key !== key) {
      bar.dataset.key = key;
      bar.innerHTML = `${songCover(song, 1) ? `<img src="${songCover(song, tileWidth(0.04))}" alt="">` : ""}<div><b>${s.paused ? ICON_PAUSE : ""}${esc(song.title)}</b><span>${esc(sub)}</span></div><i></i>`;
    }
    bar.querySelector("i").style.transform = `scaleX(${s.duration ? Math.min(1, (s.pos || 0) / s.duration) : 0})`;
    bar.classList.toggle("paused", !!s.paused);
  }
  const np = $(".nowplaying");
  if (!np) return;
  if (!song) {
    // the music ended while it was open (the backend says so: not a moment between two songs, not a lost
    // answer): back where it was opened from, rather than an empty page
    if (!s.active && !s.unknown && np.dataset.song && current.name === "nowplaying" && stack.length) {
      delete np.dataset.song;
      const prev = stack.pop();
      return render(prev, prev.focusKey, prev.scrollTop);
    }
    np.querySelector(".np-title").textContent = t("nothingPlaying");
    np.querySelector(".np-buttons").hidden = true;
    return;
  }
  np.querySelector(".np-buttons").hidden = false;
  if (np.dataset.song !== song.id || fresh) {
    np.dataset.song = song.id;
    np.querySelector(".np-cover").classList.toggle("radio", !!song.radio);
    np.querySelector(".np-cover").innerHTML = song.radio ? ICON_RADIO : `<img src="${songCover(song, COVER_W) ?? ""}" alt="">`;
    np.querySelector(".np-artist").textContent = song.radio ? "" : song.artist ?? "";
    np.querySelector(".np-album").textContent = song.radio ? t("radio") : song.album ?? "";
    if (songCover(song, 1)) setBackdrop(songCover(song, 1920));
    const star = np.querySelector('[data-k="np-star"]');
    // a station has no star, no lyrics and nothing before or after it; a book has its chapters instead, and goes
    // back and ahead by 30 seconds, faster or slower if wished
    for (const k of ["np-star", "np-lyrics", "np-prev", "np-next", "np-handoff"]) np.querySelector(`[data-k="${k}"]`).hidden = !!(song.radio || song.book);
    for (const k of ["np-back", "np-fwd", "np-speed"]) np.querySelector(`[data-k="${k}"]`).hidden = !song.book;
    if ((song.radio || song.book) && lyricsOn) showLyrics(false);
    np.querySelector(".np-up").hidden = np.querySelector(".np-queue").hidden = !!song.radio;
    np.querySelector(".np-up").textContent = t(song.book ? "chapters" : "upNextMusic");
    star.innerHTML = song.starred ? ICON_STAR : ICON_STAR_OPEN;
    star.classList.toggle("on", !!song.starred);
    star.setAttribute("aria-label", t(song.starred ? "unfavorite" : "favorite"));
    if (lyricsOn) loadLyrics();
  }
  // a station: its name, and below it what it plays right now if it says so
  np.querySelector(".np-title").textContent = song.title;
  if (s.lost || np.dataset.lost) np.querySelector(".np-artist").textContent = s.lost ? t("lostWaiting") : song.radio ? "" : song.artist ?? "";
  np.dataset.lost = s.lost ? "1" : "";
  if (song.radio) np.querySelector(".np-artist").textContent = s.icy ?? "";
  np.querySelector(".np-time").hidden = !!song.radio;
  followLyrics();
  // the queue: the songs after this one, redrawn only when it or the song changes
  // the queue is drawn anew only when it is another one (the same songs again, e.g. confirmed by the backend,
  // are not); a next song only moves the mark, and the list glides along unless someone is moving in it
  const q = np.querySelector(".np-queue"), songs = s.queue.map((x) => x.id).join(",");
  const focused = q.contains(document.activeElement) && document.activeElement;
  if (song.book) showChapters(np, q, song, s, focused);
  else if (q.dataset.songs !== songs) {
    const first = !q.dataset.songs;
    q.dataset.songs = songs;
    q.dataset.index = s.index;
    q.innerHTML = `<div class="np-queue-track">${trackRows(s.queue, "queue", true, s.index)}</div>`;
    const el = focused && q.querySelector(`[data-k="${focused.dataset.k}"]`);
    if (el) focusEl(el, false);
    else slideQueue(q.querySelector(".track.on"), !first, 0.15); // the song playing near the top
  } else if (q.dataset.index !== String(s.index)) {
    q.dataset.index = s.index;
    q.querySelectorAll(".track.on").forEach((r) => r.classList.remove("on"));
    const on = q.querySelector(`[data-k="queue-${s.index}"]`);
    on?.classList.add("on");
    if (!focused) slideQueue(on, true, 0.15);
  }
  np.querySelector(".np-pos").textContent = clock((s.pos || 0) * TICKS);
  np.querySelector(".np-dur").textContent = s.duration ? clock(s.duration * TICKS) : "";
  np.querySelector(".np-bar i").style.transform = `scaleX(${s.duration ? Math.min(1, (s.pos || 0) / s.duration) : 0})`;
  np.querySelector('[data-k="np-pause"]').innerHTML = s.paused ? ICON_PLAY : ICON_PAUSE;
  if (song.book) np.querySelector('[data-k="np-speed"]').textContent = `${s.speed ?? 1}×`;
  const moon = np.querySelector('[data-k="np-sleep"]');
  moon.classList.toggle("on", !!s.sleep);
  moon.querySelector("span").textContent = sleepText(s.sleep);
}
