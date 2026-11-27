"use strict";
// Matinee's page, part 3 of 18: the main menu, Jellyfin's pages and those of the other parts.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Views ---------- */


// Taken off a row with OK held ("Remove from this row"): Jellyfin's Continue Watching forgets the position on
// the server; shows in Up Next and Navidrome's albums played are
// hidden by Matinee (the servers cannot forget them), see /hidden
let hidden = { nextup: [], albums: [], books: [] };
const loadHidden = () => fetch("/hidden").then((r) => (r.ok ? r.json() : null)).then((h) => { if (h) hidden = h; }).catch(() => {});
const showShow = (it) => !hidden.nextup.includes(it.SeriesId);

// Continue Watching, the same wherever it shows (home, the dock's shelf, Up Next): what is begun, then the next
// episode of each show being watched with none begun (unless hidden), at most `limit`. `get` is api or a getJson on /jf.
// A show counts as being watched once an episode of it has been seen: Jellyfin's Next Up also offers episode 1 of a
// show whose first minutes were only looked into (too short to keep a position), which is not watching it.
async function continueWatching(get, user, limit) {
  const [resume, nextUp] = await Promise.all([
    get(`/UserItems/Resume?userId=${user}&Limit=${limit}&MediaTypes=Video&${FIELDS}`),
    get(`/Shows/NextUp?userId=${user}&Limit=${limit}&${FIELDS}`), loadHidden()]); // the shows hidden: showShow
  const begun = resume?.Items ?? [], series = new Set(begun.map((i) => i.SeriesId).filter(Boolean));
  let next = (nextUp?.Items ?? []).filter((i) => !series.has(i.SeriesId) && showShow(i));
  const ids = [...new Set(next.map((i) => i.SeriesId).filter(Boolean))];
  const shows = ids.length ? await get(`/Items?userId=${user}&Ids=${ids.join(",")}&Fields=RecursiveItemCount`).catch((e) => { if (e === STALE) throw e; return null; }) : null;
  if (shows?.Items) { // unknown (the server away a moment): kept as Jellyfin gave them
    const seen = new Set(shows.Items.filter((s) => s.UserData?.Played || (s.UserData?.PlayedPercentage ?? 0) > 0
      || (s.RecursiveItemCount && s.UserData?.UnplayedItemCount < s.RecursiveItemCount)).map((s) => s.Id));
    next = next.filter((i) => !i.SeriesId || seen.has(i.SeriesId));
  }
  return [...begun, ...next].slice(0, limit);
}
const jfJson = (path) => getJson("/jf" + path);
const showAlbum = (al) => !hidden.albums.includes(al.id);
const showBook = (b) => !hidden.books.includes(b.id);

// What "Remove" means for a tile, if anything: [what, id] for /forget
const ROW_KEY = /^(resume|next|recent|often|listen|shelf-\w+)-\d+$/;
function forgettable(el, it) {
  const k = el?.dataset.k ?? "";
  if (!it || !ROW_KEY.test(k)) return null;
  if (it.kind === "album" && /^(recent|often|shelf-music)-/.test(k)) return ["album", it.id];
  if (it.kind === "book" && /^(listen|shelf-books)-/.test(k)) return ["book", it.id];
  if (it.Id && /^(resume|next|shelf-jellyfin)-/.test(k)) {
    if (it.UserData?.PlaybackPositionTicks > 0) return ["resume", it.Id];
    if (it.SeriesId) return ["nextup", it.SeriesId];
  }
  return null;
}

// The last title taken off a row, for half a minute: the options (OK held, wherever) then begin with putting it back,
// as the message says. Continue Watching forgets where a film was stopped, which no one finds again by hand.
let forgotten = null;
const UNDO_MS = 30000;

async function forgetIt(el, [what, id]) {
  const r = await fetch("/forget", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ what, id }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  forgotten = { what, id, until: Date.now() + UNDO_MS };
  if (what === "nextup") hidden.nextup.unshift(id);
  if (what === "album") hidden.albums.unshift(id);
  if (what === "book") hidden.books.unshift(id);
  shelfCache.clear();
  forgetHome();
  toast(t("removedUndo"), 6000);
  // the focus on the tile that takes its place (the next one, at the end the one before)
  const next = el.nextElementSibling?.classList.contains("f") ? el.nextElementSibling : null;
  const prev = el.previousElementSibling?.classList.contains("f") ? el.previousElementSibling : null;
  const key = ROW_KEY.test(el.dataset.k) ? (next ? el.dataset.k : prev?.dataset.k) : (next ?? prev)?.dataset.k;
  render(current, key);
}

async function undoForget() {
  forgotten = null;
  const r = await fetch("/forget/undo", { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  await loadHidden();
  shelfCache.clear();
  forgetHome();
  toast(t("putBack"));
  render(current, document.activeElement?.dataset?.k);
}

// Above the main menu's tiles, what the focused one holds: Jellyfin what is being watched (else what is new),
// Navidrome the albums played last, the audiobooks and podcasts what is begun. Up from the tiles goes into it, OK opens or
// plays. Kept for a minute, so moving along the tiles does not ask the servers each time.
const SHELF_SIZE = 12, SHELF_KEEP = 60000;
const UP_NEXT_LISTEN = 3; // audiobooks and podcasts kept on Up Next, at its end, however many videos were begun
const shelfCache = new Map();
const shelfAsked = new Set(); // the shelves asked for since Matinee started: one gone from shelfCache was changed
// What each shelf showed last, kept in the browser: shown at once when Matinee starts (or a shelf is asked anew), while
// the servers are asked; replaced as soon as they answer. Not the device's look (settings), which changes by itself.
const SHELF_STORE = "kino-shelves";
// Kept for one Jellyfin user: another one signed in (or the language changed, the titles are in it) starts afresh.
function storedShelf(id) { // another user's never (an account taken over, signed in as someone else)
  try {
    const kept = JSON.parse(localStorage.getItem(SHELF_STORE) ?? "{}");
    return USER && kept.user && kept.user !== USER ? null : kept.shelves?.[id] ?? null;
  } catch { return null; }
}
function storeShelf(id, data) {
  if (id === "settings" || data.kind === "signin" || data.kind === "empty") return; // a hint only while it holds
  try {
    const kept = JSON.parse(localStorage.getItem(SHELF_STORE) ?? "{}");
    const all = USER && kept.user && kept.user !== USER ? { user: USER, shelves: {} } : { user: kept.user ?? USER, shelves: kept.shelves ?? {} };
    all.shelves[id] = data;
    localStorage.setItem(SHELF_STORE, JSON.stringify(all));
  } catch {} // full or not allowed: the shelf just waits for the servers, as before
}
function forgetShelves() {
  try { localStorage.removeItem(SHELF_STORE); } catch {}
}
const getJson = (url) => fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);

async function shelfData(id) {
  if (id === "search" || id?.startsWith("hdmi")) return null; // its shelf only says what it looks through, or does
  const hit = shelfCache.get(id);
  if (hit && Date.now() - hit.at < SHELF_KEEP) return hit.data;
  let data = null;
  await loadHidden();
  if (id === "jellyfin") {
    const r = await fetch("/session").catch(() => null);
    const s = await r?.json().catch(() => null);
    jfUnset = s?.error === "setup";
    if (!s?.userId) return SIGNIN_AGAIN.test(s?.error ?? "") ? { kind: "signin" } : null; // not kept: asked again next time
    USER ??= s.userId; // what connect() would do: the shelf's titles open right away
    const items = await continueWatching(jfJson, s.userId, SHELF_SIZE);
    if (items.length) data = { kind: "jf", title: t("continueWatching"), items, land: true };
    else {
      const latest = await getJson(`/jf/Items?userId=${s.userId}&Recursive=true&IncludeItemTypes=Movie,Series`
        + `&SortBy=DateCreated&SortOrder=Descending&Limit=${SHELF_SIZE}&${FIELDS}`);
      if (latest?.Items?.length) data = { kind: "jf", title: t("recentlyAdded"), items: latest.Items, land: true }; // wide, as every shelf
      else if (latest) data = { kind: "empty", hint: "libEmptyHint" }; // a new server, nothing found yet: what fills it
    }
  } else if (id === "music") {
    for (const [type, title] of [["recent", "recentlyPlayed"], ["newest", "recentlyAdded"]]) {
      const albums = await nd("getAlbumList2", { type, size: SHELF_SIZE }).then((r) => (r.albumList2.album ?? []).filter(showAlbum)).catch(() => []);
      if (albums.length) { data = { kind: "nd", title: t(title), items: albums }; break; }
    }
  } else if (id === "books") {
    const begun = await getJson("/abs/api/me/items-in-progress");
    const items = books(begun?.libraryItems ?? []).filter(showBook);
    if (items.length) data = { kind: "abs", title: t("continueListening"), items: items.slice(0, SHELF_SIZE) };
    else {
      const libs = (await getJson("/abs/api/libraries"))?.libraries ?? [];
      const latest = await Promise.all(libs.map((l) => getJson(`/abs/api/libraries/${l.id}/items?limit=${SHELF_SIZE}&sort=addedAt&desc=1&minified=1`)));
      const all = books(latest.flatMap((r) => r?.results ?? [])).sort((a, b) => b.addedAt - a.addedAt);
      if (all.length) data = { kind: "abs", title: t("recentlyAdded"), items: all.slice(0, SHELF_SIZE) };
    }
    if (data) await loadBookProgress();
  } else if (id === "podcasts") {
    const r = await getJson("/pf/recent");
    if (r?.begun?.length) data = { kind: "pf", title: t("continueListening"), items: r.begun.slice(0, SHELF_SIZE) };
    else if (r?.latest?.length) data = { kind: "pf", title: t("newEpisodes"), items: r.latest.slice(0, SHELF_SIZE) };
  } else if (id === "next") { // Up Next: what was begun anywhere, the latest first; to watch before to listen to
    const s = await getJson("/session");
    if (s?.userId) USER ??= s.userId;
    const [shows, begun, pods] = await Promise.all([
      s?.userId ? continueWatching(jfJson, s.userId, SHELF_SIZE) : [], getJson("/abs/api/me/items-in-progress"),
      pfState?.configured ? getJson("/pf/recent") : null, loadBookProgress()]);
    const when = (date) => (date ? Date.parse(date) / 1000 : 0); // the next episode of a show: never played, last
    const all = [
      ...shows.map((it) => ({ kind: "jf", at: when(it.UserData?.LastPlayedDate), it })),
      ...books(begun?.libraryItems ?? []).filter(showBook).map((b) => ({ kind: "abs", at: bookProgress.get(b.id)?.at ?? 0, it: b })),
      ...(pods?.begun ?? []).map((e) => ({ kind: "pf", at: e.at ?? 0, it: e })),
    ].sort((a, b) => b.at - a.at);
    // on a TV a film comes first, not the podcast heard on the way home: audiobooks and podcasts after the videos,
    // a few of them kept however many videos were begun
    const listen = all.filter((x) => ["abs", "pf"].includes(x.kind)), watch = all.filter((x) => !listen.includes(x));
    const items = [...watch.slice(0, SHELF_SIZE - Math.min(listen.length, UP_NEXT_LISTEN)), ...listen].slice(0, SHELF_SIZE);
    if (items.length) data = { kind: "mix", title: t("upNext"), items };
  } else if (id === "settings") { // a look at the device: its servers, what is connected, the weather, Matinee's version
    const o = await getJson("/overview");
    if (o) data = { kind: "status", title: t("thisDevice"), o };
  }
  // set up, but its sign-in refused (a password changed, the server now asking for one): said on the shelf with what
  // OK on the tile does, rather than an empty shelf
  // (asked here without touching the part's own session: a request failing must not make it look not set up)
  const sessionUrl = { music: "/music/session", books: "/abs/session", podcasts: "/pf/session" }[id];
  if (!data && sessionUrl && SIGNIN_AGAIN.test((await getJson(sessionUrl))?.error ?? "")) data = { kind: "signin" };
  // nothing is not kept: a server not answering yet (the network just after switching on) is asked again next time
  if (data) {
    shelfCache.set(id, { at: Date.now(), data });
    shelfAsked.add(id);
    storeShelf(id, data);
  }
  return data;
}

// the errors that need the person (a password changed, signed out by the server, a server now asking for a sign-in),
// not the ones that heal by themselves (a server asleep, the network away)
const SIGNIN_AGAIN = /(^|_)(auth_rejected|login_required|signed_out)$/;

function shelfHtml(id, data) {
  // Up Next with nothing begun (a fresh start): a line saying what comes here, rather than black above the dock
  if (!data && id === "next") return `<section class="row"><h2>${t("upNext")}</h2><div class="hint">${t("upNextEmpty")}</div></section>`;
  if (id === "search") return `<section class="row"><h2>${t("searchAll")}</h2><div class="hint">${t("searchAllHint")}</div></section>`;
  const input = id?.startsWith("hdmi") && hdmiTiles[+id.slice(4)];
  if (input) return `<section class="row"><h2>${esc(input.name)}</h2><div class="hint">${esc(t("hdmiTileHint", { n: input.port }))}</div></section>`;
  // music or audiobooks not set up yet: what OK on the tile does
  const unset = (id === "music" && !ndSession?.configured) || (id === "books" && !absState?.configured) || (id === "jellyfin" && jfUnset);
  if (unset) return `<section class="row"><h2>${esc(partName(id))}</h2><div class="hint">${t(id === "jellyfin" ? "jfSetUp" : "svcSetUp")}</div></section>`;
  if (!data) return "";
  if (data.kind === "status") return statusHtml(data);
  if (data.kind === "empty") return `<section class="row"><h2>${esc(partName(id))}</h2><div class="hint">${t(data.hint)}</div></section>`;
  if (data.kind === "signin") return `<section class="row"><h2>${esc(partName(id))}</h2><div class="hint">${esc(t("svcSignInAgain", { name: partName(id) }))}</div></section>`;
  const key = (i) => `shelf-${id}-${i}`;
  const cards = data.kind === "jf" ? data.items.map((it, i) => card(it, { land: data.land, key: key(i) }))
    : data.kind === "nd" ? data.items.map((x, i) => musicCard(x, "album", key(i)))
    : data.kind === "abs" ? data.items.map((b, i) => bookCard(b, key(i)))
    : data.kind === "pf" ? data.items.map((e, i) => episodeCard(e, key(i)))
    : data.items.map((x, i) => (x.kind === "jf" ? card(x.it, { land: true, key: key(i) }) // Up Next
      : x.kind === "pf" ? episodeCard(x.it, key(i)) : bookCard(x.it, key(i))));
  return `<section class="row"><h2>${esc(data.title)}</h2><div class="strip">${cards.join("")}</div></section>`;
}

// Above the settings in the main menu: is each server there, what is connected by Bluetooth (with its battery; only
// where Matinee pairs them), Tailscale, and Matinee's version and whether it is up to date; the weather is up by the time
// (deviceStage)
function statusHtml({ title, o }) {
  const panel = (name, lines, logo = "") => `<div class="panel"><h3>${logo}${esc(name)}</h3>${lines.join("")}</div>`;
  const line = (text, dot) => `<div class="line">${dot ? `<i class="dot ${dot}"></i>` : ""}${text}</div>`;
  const panels = [panel(t("servers"), o.servers.map((s) => line(`${esc(s.name)}<span>${t(s.ok ? "serverOk" : "serverDown")}</span>`, s.ok ? "ok" : "down")))];
  if (o.bluetooth) panels.push(panel("Bluetooth", o.bluetooth.length ? o.bluetooth.map((d) => line(esc(d.name)
    + (d.battery != null ? `<span>${esc(t("battery", { n: d.battery }))}</span>` : ""), d.battery != null && d.battery < 20 ? "down" : "ok"))
    : [line(`<span>${t("btNothing")}</span>`)]));
  const ts = o.tailscale;
  if (ts) panels.push(panel("Tailscale", [line(`${t(tsState(ts))}${ts.exit ? `<span>${esc(t("tsVia", { name: ts.exit }))}</span>` : ""}`,
    ts.state === "Running" ? "ok" : "down"), ...(ts.state === "Running" ? [line(`<span>${t("tsOnline", { n: ts.online, of: ts.peers })}</span>`)] : [])]));
  const up = o.update, [state, dot] = !up ? ["kinoVersion", ""] : up.error ? ["updateUnknown", "down"] : up.behind ? ["updateReady", "new"]
    : ["updateCurrent", "ok"];
  // an update: how much it brings and from when, below what is installed
  const news = up?.behind ? [line(`<span>${esc([updateCount(up), up.at && dayTime(up.at)].filter(Boolean).join(" · "))}</span>`)] : [];
  panels.push(panel("Matinee", [line(`${t(state)}<span>${esc(o.version ?? "")}</span>`, dot), ...news], KINO_LOGO)); // its own logo, as an About
  return `<section class="row status"><h2>${esc(title)}</h2><div class="panels">${panels.join("")}</div></section>`;
}

let shelfTimer;
async function fillShelf(id) {
  const shelf = $("#shelf");
  if (!shelf || shelf.dataset.for === id) return;
  shelf.dataset.for = id;
  const gen = renderGen;
  // the focus on the shelf being replaced (OK on Up Next went to the first title of the shelf still standing): on the
  // title at its place in the new one, else on the tile below; never left on nothing, where the remote does nothing
  const keepFocus = (put) => {
    const cards = [...shelf.querySelectorAll(".f")], at = cards.indexOf(document.activeElement);
    put();
    if (at < 0) return;
    const now = [...shelf.querySelectorAll(".f")];
    focusEl(now[Math.min(at, now.length - 1)] ?? view.querySelector(`[data-k="menu-${id}"]`), false);
  };
  const show = (data) => keepFocus(() => {
    shelf.innerHTML = shelfHtml(id, data);
    shelf.classList.remove("shown");
    void shelf.offsetWidth; // fade in again for each tile
    shelf.classList.add("shown");
  });
  // not asked within the last minute: what it showed last, at once, while the servers are asked. Not after a change
  // that dropped it (a title marked as watched, one played): that would show what is no longer so.
  const fresh = shelfCache.get(id);
  const early = fresh ? (Date.now() - fresh.at < SHELF_KEEP ? null : storedShelf(id)) : shelfAsked.has(id) ? null : storedShelf(id);
  if (early) show(early);
  const data = await shelfData(id);
  if (gen !== renderGen || shelf.dataset.for !== id) return; // gone on to another tile meanwhile
  if (!early) return show(data);
  // the stored one shown: swapped quietly where the servers said something else, but not under the person's feet
  // (moved up into it meanwhile: the new one is kept for the next time)
  if ((data || id === "next") && JSON.stringify(data) !== JSON.stringify(early) && !shelf.contains(document.activeElement)) {
    shelf.innerHTML = shelfHtml(id, data);
  }
}

// The home's data, asked for together where they do not depend on each other (api() takes the page it belongs to
// when it is called), and the page built from them
let homeKept = null; // { user, data, sig, checked }: the home shown last, see views.home
const homeSig = (d) => JSON.stringify(d);
async function loadHome(get) {
  const [watching, libs, favorites] = await Promise.all([
    continueWatching(get, USER, 16),
    get(`/UserViews?userId=${USER}`),
    get(`/Items?userId=${USER}&Recursive=true&Filters=IsFavorite&IncludeItemTypes=Movie,Series,BoxSet,Episode&SortBy=SortName&Limit=40&${FIELDS}`),
  ]);
  // translated only where no other library has that name already (one renamed by hand to it, say)
  const taken = new Set(libs.Items.map((l) => l.Name));
  for (const l of libs.Items) {
    const name = libName(l.Name, l.CollectionType);
    if (name === l.Name || !taken.has(name)) l.Name = name;
  }
  const media = libs.Items.filter((l) => ["movies", "tvshows"].includes(l.CollectionType));
  const latest = await Promise.all(media.map((l) => get(`/Items/Latest?userId=${USER}&parentId=${l.Id}&Limit=20&${FIELDS}`)));
  // Jellyfin names a show's new season (or its one new episode) there rather than the show, and a season mostly
  // has no text of its own: the show in its place, a new episode with the show's text and genres
  const seriesIds = [...new Set(latest.flat().filter((i) => i.SeriesId && (i.Type === "Season" || !i.Overview)).map((i) => i.SeriesId))];
  if (seriesIds.length) {
    const shows = new Map((await get(`/Items?userId=${USER}&Ids=${seriesIds.join(",")}&${FIELDS}`)).Items.map((x) => [x.Id, x]));
    latest.forEach((list, n) => {
      const seen = new Set();
      latest[n] = list.map((i) => {
        const show = shows.get(i.SeriesId);
        if (!show) return i;
        if (i.Type === "Season") return show;
        return { ...i, Overview: i.Overview || show.Overview, Genres: i.Genres?.length ? i.Genres : show.Genres };
      }).filter((i) => !seen.has(i.Id) && seen.add(i.Id));
    });
  }
  // first the libraries, like the tiles at the top of the music and the audiobooks: movies, shows, collections
  // (in the server's order within each), then search and the settings
  const LIB_ORDER = ["movies", "tvshows", "boxsets"];
  const libraries = libs.Items.filter((l) => LIB_ORDER.includes(l.CollectionType))
    .sort((a, b) => LIB_ORDER.indexOf(a.CollectionType) - LIB_ORDER.indexOf(b.CollectionType));
  // each with its newest posters, for the wall above it when its button is focused (collections: asked for here)
  const newest = Object.fromEntries(media.map((l, i) => [l.Id, latest[i]]));
  await Promise.all(libraries.filter((l) => !newest[l.Id]).map((l) =>
    get(`/Items?userId=${USER}&parentId=${l.Id}&Limit=3&SortBy=DateCreated&SortOrder=Descending&${FIELDS}`)
      .then((r) => (newest[l.Id] = r.Items)).catch((e) => { if (e === STALE) throw e; })));
  return { watching, favorites: favorites.Items, media, latest, libraries, newest };
}

// The kept home shown, the servers asked again behind it: what changed meanwhile (watched on the phone, a title
// added) is put in as a page built again where it stands, the focus and the rows where they were, not while keys
// are being pressed (liveRefresh). Nothing changed: nothing moves.
function homeLatest(gen, kept) {
  loadHome(api).then((data) => {
    if (homeKept !== kept) return; // a change meanwhile: the next home asks anyway
    const sig = homeSig(data);
    if (sig === kept.sig) return;
    homeKept = { user: kept.user, data, sig, checked: true };
    const put = () => {
      if (gen !== renderGen || homeKept?.data !== data) return; // gone on: the next home has it without asking
      const busy = 3000 - (Date.now() - lastKey);
      if (busy > 0 || polling) return setTimeout(put, Math.max(busy, 1000));
      refresh();
    };
    put();
  }).catch(() => {}); // the page gone on (STALE) or the server away: the kept one stays
}
const forgetHome = () => (homeKept = null);


async function buildHome({ watching, favorites, media, latest, libraries, newest }) {
  const featured = watching[0] ?? latest.flat().find((i) => i.BackdropImageTags?.length);
  // each with a symbol of what it holds, as the music's tiles (the same logo on all of them would tell nothing)
  const libTiles = libraries.map((l) => ({ ...l, icon: { movies: "Film", tvshows: "TV", boxsets: "Collection" }[l.CollectionType] ?? "Film" }));
  // the library as channels (tv.py), where it has enough for some: a button after the libraries (see ownChip)
  const chips = [...libTiles.map((l, i) => libChip(l, `libs-${i}`, "library")), ...(ownChannels ? [ownChipHtml()] : [])];
  let html = chips.length ? libChips(chips) : "";
  // what is begun, then the next episodes of the shows being watched: one row, no second one for them
  html += row(t("continueWatching"), watching, { land: true, prefix: "resume" });
  html += row(t("myList"), favorites, { prefix: "fav" });
  media.forEach((l, i) => (html += row(t("newIn", { name: l.Name }), latest[i], { prefix: `latest${i}` })));
  // nothing in it yet (a new server, its libraries still being scanned): what fills it, not a black page
  if (!watching.length && !favorites.length && !latest.some((l) => l?.length)) html += `<div class="hint row-hint">${t("libEmptyHint")}</div>`;
  // each library's newest picture: the settings' picture where nothing else gives one
  libNewest = new Map(media.map((l, i) => [l.Id, latest[i].find(backdropOf)]));
  libWalls = new Map(Object.entries(newest)); // its newest titles, for the wall above it
  view.innerHTML = `${await tabBar("jellyfin")}<div id="stage-art" class="beside"></div><div class="home"><section class="hero" id="hero">${libTiles[0] ? chipHero(libTiles[0]) : featured ? heroInner(featured) : ""}</section><div class="rows"><div class="rows-track">${html}</div></div></div>`;
  // the libraries' first button is focused at once and puts its own above: the featured title's pictures only without
  if (featured && !libTiles[0]) setBackdrop(backdropOf(featured));
  if (featured && !libTiles[0]) stageArt(backdropOf(featured), null, "card", true);
  view.dataset.liveHero = "1";
  ownChip(renderGen);
}

// Whether the library gives channels (tv.py): asked beside Jellyfin's start, which does not wait for it; their button
// comes after the libraries once it is known, and is there at once from then on
let ownChannels = false;
const ownChipHtml = () => libChip({ Id: "own", Name: t("ownChannels"), icon: "Guide" }, "libs-own", "ownchannels");
async function ownChip(gen) {
  const r = await getJson("/tv/own");
  if (!r) return;
  ownChannels = r.own.length > 0;
  const chip = view.querySelector("[data-k=libs-own]"), last = [...view.querySelectorAll("[data-action=library]")].pop();
  if (gen !== renderGen || current.name !== "home" || ownChannels === !!chip) return;
  if (chip && document.activeElement !== chip) chip.remove();
  else if (!chip) last?.insertAdjacentHTML("afterend", ownChipHtml());
}

// The sync with the Jellyfin account (backend/syncing.py, Sync): what it keeps alike and when it last did
function syncHint(s) {
  const st = s.syncStatus ?? {};
  if (s.sync !== "on") return `${t("syncHint")} ${t("syncOff")}`;
  // the last time it worked (with the day, if not today), and that the last try did not, if so
  const at = st.at ? t("syncAt", { time: dayTime(st.at) }) : "";
  const when = [at, st.error ? t("syncFailed") : at ? "" : t("syncNotYet")].filter(Boolean).join(" ");
  return `${t("syncHint")} ${when}`;
}

// The Matinees signed in to the account (from the sync): each with how far behind the newest version it is, to see at
// a glance which one did not update; when each was last heard of in the hint below. One not heard of for an hour
// (a Matinee running says so every ten minutes) is off: when it was last there instead, as how far behind its version
// was then says little of now (it may have been updated since, and gets the update anyway once it is on)
const DEVICE_AWAY = 3600;
function deviceLines(devices, line) {
  const when = dayTime;
  return devices.map((d, i) => {
    const behind = d.behind === 0 ? t("deviceCurrent") : d.behind === 1 ? t("deviceBehindOne")
      : d.behind > 1 ? t("deviceBehind", { n: d.behind }) : "";
    const away = !d.self && d.at && Date.now() / 1000 - d.at > DEVICE_AWAY;
    const state = away ? t("deviceAway", { time: when(d.at) }) : behind || d.version || "–";
    const hint = [d.self ? t("deviceThis") : t("deviceSeen", { time: when(d.at) }), d.version && t("deviceVersion", { v: d.version }),
      away && behind].filter(Boolean).join(" · ");
    return line(`acc-dev-${i}`, d.self ? `${d.name} (${t("deviceThisShort")})` : d.name, esc(state), `data-action="none"${d.behind > 0 && !away ? ' data-behind="1"' : ""}`, hint);
  }).join("");
}

const views = {
  // Main menu: Jellyfin, music, audiobooks, podcasts and the settings, with the focus on the one used last;
  // above them the shelf of the focused one. Music and audiobooks are there before they are set up: their tile then
  // leads to their settings. Podcasts only once a Podfetch is set up (in the settings), not to crowd the dock.
  async menu(args, focusKey) {
    await Promise.all([musicSession(), bookSession(), podSession()]);
    const tiles = [{ Id: "next", Name: t("upNext"), Type: "UpNext" }, { Id: "jellyfin", Name: partName("jellyfin"), Type: "Jellyfin" },
      // with the simple settings only what is set up: their setup is not reachable there
      ...(!SIMPLE || ndSession?.configured ? [{ Id: "music", Name: partName("music"), Type: "Music" }] : []),
      ...(!SIMPLE || absState?.configured ? [{ Id: "books", Name: partName("books"), Type: "Books" }] : []),
      ...(pfState?.configured ? [{ Id: "podcasts", Name: partName("podcasts"), Type: "Podcasts" }] : []),
      ...hdmiTiles.map((x, i) => ({ Id: `hdmi${i}`, Name: x.name, Type: "Hdmi" })), // the TV's other inputs (hdmiCheck)
      { Id: "search", Name: t("searchAll"), Type: "Search" }, { Id: "settings", Name: t("settings"), Type: "Settings" }];
    view.innerHTML = `<div id="stage-art"></div><div id="stage-title"></div><div class="menu"><section class="shelf" id="shelf"></section><div class="strip tiles" data-n="${tiles.length}">${tiles.map((it) =>
      card(it, { land: true, action: "section", key: `menu-${it.Id}`, sub: "", aux: it.Id === "settings" })).join("")}</div></div>`;
    liquidWatch.disconnect();
    liquidWatch.observe($(".menu .tiles")); // its liquid glass drawn for its size
    menuBackdrop(renderGen);
    hdmiCheck(renderGen);
    syncDockCover();
    // every part's wall asked for meanwhile (only which pictures: loading them all at once held up the shelf's)
    for (const t of tiles) if (WALL_SHAPE[t.Id]) coverWall(t.Id);
    // the shelf of the tile that gets the focus is there right away, so Back to one of its titles finds it again
    // (waiting no more than a moment for a server that does not answer)
    const first = focusKey?.match(/^(?:menu|shelf)-(\w+)/)?.[1] ?? tiles[0].Id;
    await Promise.race([fillShelf(tiles.some((x) => x.Id === first) ? first : tiles[0].Id), new Promise((r) => setTimeout(r, 1500))]);
    // a key from the shelf that is not there any more (watched to the end meanwhile): its tile
    if (focusKey?.startsWith("shelf-") && !view.querySelector(`[data-k="${CSS.escape(focusKey)}"]`)) {
      view.querySelector(`[data-k="menu-${first}"]`)?.setAttribute("data-autofocus", "");
    }
  },

  async home() {
    if (!USER) await connect();
    // Back (or the tab) shows the home kept from last time at once, and asks the servers meanwhile: its five
    // requests one after another took a third of a second at home and more over a slow line, behind a spinner.
    // What came back different is put in where the page stands (see homeLatest). After a change Matinee made itself
    // (a video watched, a title marked) or one the server told of, the kept one is gone and the page waits.
    const kept = homeKept?.user === USER ? homeKept : null;
    const data = kept?.data ?? await loadHome(api);
    if (!kept) homeKept = { user: USER, data, sig: homeSig(data), checked: false };
    else if (kept.checked) kept.checked = false; // just brought in by homeLatest: the next Back asks again
    else homeLatest(renderGen, kept);
    await buildHome(data);
  },

  async item({ id, seasonId, type }) {
    // only movies and shows get a "similar" row; if the tile told us the type, start loading it right away
    if (type === "Movie" || type === "Series") similarOf(id);
    const it = await api(`/Items/${id}?userId=${USER}`);
    current.itemType = it.Type; // for Back, see back()
    const similar = ["Movie", "Series"].includes(it.Type);
    setBackdrop(backdropOf(it));
    if (it.Type === "Series") return seriesView(it, seasonId, similar);
    if (it.Type === "Season") return seriesView(await api(`/Items/${it.SeriesId}?userId=${USER}`), it.Id);
    if (it.Type === "BoxSet") return collectionView(it);

    const pos = it.UserData?.PlaybackPositionTicks || 0;
    const actions = pos > 0
      ? `<button class="btn f" data-k="resume" data-action="play" data-id="${esc(it.Id)}" data-resume="1">${ICON_PLAY}${t("resumeFrom", { time: clock(pos) })}</button>
         <button class="btn f" data-k="restart" data-action="play" data-id="${esc(it.Id)}" data-resume="0">${ICON_RESTART}${t("startOver")}</button>`
      : `<button class="btn f" data-k="play" data-action="play" data-id="${esc(it.Id)}" data-resume="0">${ICON_PLAY}${t("play")}</button>`;
    const watched = watchedButton(it) + favoriteButton(it) + trailerButton(it);
    const toSeries = it.Type === "Episode" ? `<button class="btn f" data-k="series" data-action="goto" data-id="${esc(it.SeriesId)}">${t("goToSeries")}</button>` : "";

    let html = `<div class="detail">${detailHero(it, actions + watched + toSeries)}`;
    // an episode shows its whole season, moved to this episode: the ones before and after are one key away
    const season = it.Type === "Episode" && it.SeasonId;
    if (season) html += `<section class="row"><h2>${esc(it.SeasonName || t("episodes"))}</h2><div class="strip" id="episodes"></div></section>`;
    html += castRow(it);
    if (similar) html += `<div id="similar"></div>`;
    view.innerHTML = html + "</div>";
    showDetailArt(it);
    if (similar) fillSimilar(renderGen, it.Id, t("similarMovies"));
    if (season) await loadEpisodes(it.SeriesId, it.SeasonId, it.Id);
  },

  // Someone of a cast: who they are (Jellyfin's data from TMDB), and what with them is in the library
  async person({ id, name }) {
    const [who, res] = await Promise.all([
      api(`/Items/${id}?userId=${USER}`).catch((e) => { if (e === STALE) throw e; return null; }), // only for the facts
      api(`/Items?userId=${USER}&PersonIds=${id}&Recursive=true&IncludeItemTypes=Movie,Series`
        + `&SortBy=PremiereDate,ProductionYear,SortName&SortOrder=Descending&${FIELDS}`),
    ]);
    const p = who ?? { Name: name };
    const date = (d) => new Date(d).toLocaleDateString(LANG, { day: "numeric", month: "long", year: "numeric" });
    const facts = [p.PremiereDate && t("born", { date: date(p.PremiereDate) }), p.ProductionLocations?.[0],
      p.EndDate && t("died", { date: date(p.EndDate) })].filter(Boolean);
    const src = p.ImageTags?.Primary ? img(id, "Primary", p.ImageTags.Primary, DETAIL_POSTER_W) : null;
    const movies = res.Items.filter((x) => x.Type === "Movie"), shows = res.Items.filter((x) => x.Type === "Series");
    view.innerHTML = `<div class="detail person"><section class="hero with-poster">
      <div class="poster face">${src ? `<img src="${src}" decoding="async" alt="">` : `<div class="ph">${esc(initials(p.Name))}</div>`}</div>
      <div class="info">${kicker(t("person"))}<h1 class="title">${esc(p.Name)}</h1>
      ${facts.length ? `<div class="meta">${facts.map((f) => `<span>${esc(f)}</span>`).join("")}</div>` : ""}
      ${overviewOf(p) ? `<div class="overview">${esc(overviewOf(p))}</div>` : ""}</div></section>
      ${row(t("personMovies", { name: p.Name }), movies, { prefix: "pmovies" })}
      ${row(t("personShows", { name: p.Name }), shows, { prefix: "pshows" })}
      ${res.Items.length ? "" : `<div class="hint">${esc(t("personNothing", { name: p.Name }))}</div>`}</div>`;
  },

  async library({ id, name, type, sort = "SortName", unplayed = false, genre = "" }) {
    const boxsets = type === "boxsets";
    const include = boxsets ? "BoxSet" : { movies: "Movie", tvshows: "Series" }[type] ?? "Movie,Series";
    let q = `/Items?userId=${USER}&parentId=${id}&Recursive=true&IncludeItemTypes=${include}&SortBy=${sort},SortName`
      + `&SortOrder=${sort === "SortName" ? "Ascending" : "Descending"}&${FIELDS}`;
    if (unplayed) q += "&Filters=IsUnplayed";
    if (genre) q += `&GenreIds=${genre}`;
    const cached = libCache.get(q);
    if (!genreCache.has(id)) genreCache.set(id, boxsets ? { Items: [] } : await api(`/Genres?userId=${USER}&parentId=${id}&SortBy=SortName`));
    const res = cached ? { Items: cached.items, TotalRecordCount: cached.items.length } : await api(`${q}&Limit=${LIB_FIRST}`);
    const genres = genreCache.get(id);

    const chip = (opt, val, label, on) =>
      `<div class="chip f${on ? " sel" : ""}" tabindex="-1" data-k="opt-${opt}-${val}" data-action="libopt" data-opt="${opt}" data-val="${val}">${esc(label)}</div>`;
    const sorts = SORTS.map(([v, l]) => chip("sort", v, t(l), sort === v)).join("");
    const filters = chip("unplayed", "", t("all"), !unplayed) + chip("unplayed", "1", t("unwatched"), unplayed);
    const genreChips = genres.Items.length
      ? chip("genre", "", t("allGenres"), !genre) + genres.Items.map((g) => chip("genre", g.Id, g.Name, genre === g.Id)).join("") : "";
    const bar = `<section class="row bar"><div class="strip">${sorts}<span class="sep"></span>${filters}</div></section>`
      + (genreChips ? `<section class="row bar"><div class="strip">${genreChips}</div></section>` : "");
    const grid = res.Items.length ? `<div class="grid library-grid">${res.Items.map((it) => card(it)).join("")}</div>`
      : `<div class="hint">${t("nothingFound")}</div>`;
    view.innerHTML = `<div class="grid-page">${kicker(partName("jellyfin"))}<h1>${esc(name)} <span class="count">${res.TotalRecordCount ?? res.Items.length}</span></h1>${bar}${grid}</div>`;
    // the focus on the first title, not on the sorting above (Back from a title or a chip keeps its own)
    view.querySelector(".grid .card")?.setAttribute("data-autofocus", "");
    const first = res.Items.find((i) => i.BackdropImageTags?.length);
    if (first) setBackdrop(backdropOf(first));
    if (cached?.stale) reloadLibrary(renderGen, q);
    else if (!cached && res.TotalRecordCount > res.Items.length) loadRest(renderGen, q, res.Items);
    else if (!cached) keepLibrary(q, { items: res.Items });
  },

  async search({ q = "" }) {
    titleIndex(); // for the titles a typo away, while the words are typed
    view.innerHTML = searchPage();
    showQuery(q);
    await runSearch(q);
  },

  // The library as channels (the button after the libraries on Jellyfin's start): what runs on each, the one watched
  // last focused
  async ownchannels() {
    const r = await getJson("/tv/own");
    if (!r) throw new Error(t("tvFailed"));
    tvLastLive = r.lastLive;
    view.innerHTML = `<div id="stage-art" class="beside"></div><div class="grid-page">${kicker(partName("jellyfin"))}<h1>${t("ownChannels")} <span class="count">${r.own.length}</span></h1>
      <div class="grid wide library-grid">${guideCard()}${r.own.map((c) => liveCard(c)).join("")}</div></div>`;
    const last = tvLastLive && view.querySelector(`[data-k="live-${CSS.escape(tvLastLive)}"]`);
    (last || view.querySelector(".grid [data-action=tvlive]") || view.querySelector(".grid .card"))?.setAttribute("data-autofocus", "");
    showTvNow(); // what runs comes onto the tiles afterwards
  },

  // The programme guide: a row for each channel, its programmes along the time (see move and focusEl)
  async guide() {
    const g = await getJson("/tv/guide");
    if (!g) throw new Error(t("tvFailed"));
    const start = Math.floor(g.now / 1800) * 1800 - 1800, end = g.now + 8 * 3600;
    epg = { channels: g.channels, start, now: g.now, at: g.now, x: 0 };
    const px = (s) => `${((s - start) / 60) * EPG_PX}px`;
    const times = [];
    for (let s = start; s < end; s += 1800) times.push(`<span style="left:${px(s)}">${timeOfDay(new Date(s * 1000))}</span>`);
    const rows = g.channels.map((c, n) => {
      const items = c.items.length ? c.items : [{ title: t("live"), start, end, live: true }];
      const cells = items.map((x, i) => {
        const from = Math.max(x.start, start), to = Math.min(x.end, end), on = x.start <= g.now && g.now < x.end;
        // a programme over before the guide's start or after its end: no cell (an invisible one took the focus)
        if (to <= from) return "";
        return `<div class="epg-cell f${on || x.live ? " now" : ""}" tabindex="-1" data-k="epg-${n}-${i}" data-action="guidecell" data-c="${n}" data-i="${c.items.length ? i : -1}"
          data-start="${from}" data-end="${to}" style="left:${px(from)};width:calc(${px(to)} - ${px(from)} - 0.3rem)">
          <b>${esc(x.title)}</b><small>${x.live ? "" : `${timeOfDay(new Date(x.start * 1000))}${x.sub ? ` · ${esc(x.sub)}` : ""}`}</small></div>`;
      }).join("");
      const name = c.logo ? `<img src="/tv/image?u=${encodeURIComponent(c.logo)}" decoding="async" alt="${esc(c.name)}">` : `<span>${esc(c.name)}</span>`;
      return `<div class="epg-row"><div class="epg-ch">${name}</div><div class="epg-line"><div class="epg-times">${cells}</div></div></div>`;
    }).join("");
    view.innerHTML = `<div class="epg" style="--epg-x:0px;--now-x:${px(g.now)}">${kicker(t("ownChannels"))}<h1>${t("tvGuide")}</h1>
      <div class="epg-head"><div class="epg-times">${times.join("")}</div></div>
      <div class="epg-box"><div class="epg-now"></div><div class="epg-track">${rows}</div></div></div>`;
    const first = view.querySelector(".epg-cell.now");
    first?.setAttribute("data-autofocus", "");
  },
};

const TILE_ICONS = { Search: ICON_SEARCH, Settings: ICON_SETTINGS, Jellyfin: ICON_JELLYFIN, Hdmi: ICON_HDMI, TV: ICON_TV,
  Music: ICON_MUSIC, Albums: ICON_ALBUM, Artists: ICON_ARTIST, Playlists: ICON_PLAYLIST, Shuffle: ICON_SHUFFLE,
  NowPlaying: ICON_PLAY, UpNext: ICON_PLAY, Favorites: ICON_STAR, Radio: ICON_RADIO, Books: ICON_BOOKS, Podcasts: ICON_PODCASTS, Guide: ICON_GUIDE,
  Film: ICON_FILM, Collection: ICON_COLLECTION, Book: ICON_BOOK, PodcastList: ICON_PODCAST_LINE };
const SORTS = [["SortName", "sortName"], ["DateCreated", "sortAdded"], ["PremiereDate", "sortYear"], ["CommunityRating", "sortRating"]];
const LETTERS = [..."abcdefghijklmnopqrstuvwxyz0123456789"], MARKS = [...".,-'&!"]; // a row of marks below the digits
