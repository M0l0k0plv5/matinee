"use strict";
// Matinee's page, part 10 of 18: audiobooks.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Audiobooks (Audiobookshelf) ---------- */

const BOOK_VIEWS = new Set(["books", "booklib", "book", "booksearch"]);
const BOOK_ACTIONS = new Set(["bbook", "blib", "bplay", "bchapterplay", "bfinished"]);
const BOOK_SPEEDS = [0.8, 1, 1.1, 1.25, 1.5, 1.75, 2];
let absState = null, bookProgress = new Map(), pageBook = null;
// whether there is an Audiobookshelf (the menu shows its tile) and whether it takes Matinee's API key
async function bookSession(fresh = false) {
  if (!absState || fresh) absState = await fetch("/abs/session").then((r) => r.json()).catch(() => ({ configured: false }));
  return absState;
}

// Audiobookshelf through the backend, which holds the key; like nd(), a newer render makes it STALE
async function abs(path) {
  const gen = renderGen;
  const r = await fetch(`/abs${path}`);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(t(`err_${data.error || "abs_unreachable"}`));
  if (gen !== renderGen) throw STALE;
  return data;
}

// how far each book has been heard: { progress (0–1), finished }
const loadBookProgress = () => fetch("/abs/progress").then((r) => (r.ok ? r.json() : null))
  .then((p) => { if (p) bookProgress = new Map(Object.entries(p)); }).catch(() => {});
// with the time the book last changed: a new cover is fetched at once, not the one Chromium keeps for a week
const bookCover = (b, size) => `/abs/api/items/${encodeURIComponent(b.id)}/cover?width=${size}&v=${encodeURIComponent(b.v ?? b.updatedAt ?? 0)}`;
const songCover = (song, size) => (song.podcast != null ? podCover(song.podcast) : song.book ? bookCover(song, size) : cover(song.coverArt, size));
// one title's cover, of whichever part: an album, song or playlist, a book, a podcast or one of its episodes; null for
// anything else (a film's picture is its backdrop). The main menu's shelf and the parts' starts show it alike
const titleCover = (it, size) => (it.kind === "book" ? bookCover(it, size) : it.kind === "podcast" ? (it.cover ? podCover(it.id) : null)
  : it.kind === "episode" ? podCover(it.podcast) : ["album", "song", "playlist"].includes(it.kind) ? cover(it.coverArt, size) : null);
// what Audiobookshelf still lists although its files are gone (moved, renamed): it cannot be played
const books = (items) => items.filter((li) => li && !li.isMissing && !li.isInvalid).map(bookOf);
const bookOf = (li) => ({ kind: "book", id: li.id, v: li.updatedAt, cover: !!li.media?.coverPath, title: li.media?.metadata?.title ?? "", author: li.media?.metadata?.authorName ?? "",
  addedAt: li.addedAt ?? 0 });

function bookCard(b, key) {
  const p = bookProgress.get(b.id);
  return tile({ item: b, key, action: "bbook", shape: "sq",
    art: cardImg(bookCover(b, SQUARE_W)) + (p?.finished ? `<div class="watched">✓</div>` : progressBar(Math.min(100, (p?.progress ?? 0) * 100))),
    label: b.title, sub: b.author || null });
}
// A movie's logo drawn in black or another dark color would vanish on the dark page: such a logo is shown white.
// Its pixels are looked at once it has loaded (only the ones that are drawn, by their brightness).
// A "logo" that is a picture with its own background (no transparent pixels at all, e.g. yellow letters on a black
// box) is no logo to put on the page: shown white it was a white box, so the title is written out in its place.
const logoTone = new Map(); // address → "dark", "boxed" or ""
document.addEventListener("load", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG" || !img.classList.contains("logo")) return;
  if (!logoTone.has(img.src)) {
    try {
      const c = document.createElement("canvas");
      c.width = 64;
      c.height = Math.max(1, Math.round((64 * img.naturalHeight) / img.naturalWidth));
      const ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0, c.width, c.height);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let light = 0, seen = 0, solid = 0;
      for (let i = 0; i < d.length; i += 4) {
        const a = d[i + 3] / 255;
        light += a * (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]);
        seen += a;
        if (d[i + 3] > 250) solid++;
      }
      logoTone.set(img.src, solid > 0.97 * (d.length / 4) ? "boxed" : seen > 0 && light / seen < 70 ? "dark" : "");
    } catch { logoTone.set(img.src, ""); }
  }
  const tone = logoTone.get(img.src);
  if (tone === "boxed") {
    const title = document.createElement("h1");
    title.className = "title";
    title.textContent = img.alt;
    return img.replaceWith(title);
  }
  img.classList.toggle("dark", tone === "dark");
}, true);

// A cover the server cannot deliver (a file gone missing, or only Navidrome's placeholder, which the backend answers
// with 404): the title in its place instead of a broken picture
document.addEventListener("error", (e) => pictureFailed(e.target), true);
// The same for those that failed before their page was in the document (a picture Chromium knew to be missing fails
// while innerHTML still builds the page aside, and its error never reaches the document): each one put on the page,
// however it got there (a page drawn, a row filled later, the guide drawn anew), looked at as it comes, one already
// broken replaced at once, the others watched themselves
const PICTURES = ".epg-ch img, .card .art img";
function picturesWatched(root) {
  for (const img of root.matches?.(PICTURES) ? [root] : root.querySelectorAll(PICTURES)) {
    if (img.complete && img.naturalWidth === 0 && img.getAttribute("src")) pictureFailed(img);
    else {
      img.addEventListener("error", () => pictureFailed(img), { once: true });
      // a picture Chromium already knows to be missing may not say so again: decode() does, without fail
      img.decode?.().catch(() => { if (img.complete && img.naturalWidth === 0) pictureFailed(img); });
    }
  }
}
new MutationObserver((changes) => {
  for (const c of changes) for (const n of c.addedNodes) if (n.nodeType === 1) picturesWatched(n);
}).observe(document.getElementById("view"), { childList: true, subtree: true });
function pictureFailed(img) {
  if (img.tagName !== "IMG" || !img.isConnected) return; // replaced already (the document's listener and its own)
  if (img.parentElement?.classList.contains("epg-ch")) { // a channel's logo in the guide: its name
    const name = document.createElement("span");
    name.textContent = img.alt;
    return img.replaceWith(name);
  }
  if (img.tagName !== "IMG" || !img.closest(".card .art")) return;
  if (img.classList.contains("now")) return img.remove(); // a dock tile's cover of what plays: its symbol again
  const ph = document.createElement("div");
  ph.className = "ph";
  const name = img.closest(".card").querySelector(".label")?.textContent ?? "";
  ph.textContent = img.closest(".people") ? initials(name) : name; // a face: its initials, as without a picture
  const src = img.currentSrc || img.src;
  img.replaceWith(ph);
  pictureLater(img, ph, src);
}

// A picture that did not come for another reason than its server not having it (the network not up yet after
// switching on, the server busy): asked again for a few minutes, and put in the name's place once it comes. Only a
// 404 is the picture not being there.
const PICTURE_AGAIN = [0, 3, 6, 12, 20, 30, 45, 60]; // seconds before each try
async function pictureLater(img, ph, src) {
  if (!src || src.startsWith("data:")) return;
  for (const wait of PICTURE_AGAIN) {
    await new Promise((r) => setTimeout(r, wait * 1000));
    if (!ph.isConnected) return; // the page left meanwhile
    const r = await fetch(src).catch(() => null);
    if (r?.status === 404) return;
    if (!r?.ok) continue;
    const fresh = img.cloneNode();
    fresh.classList.remove("in");
    fresh.loading = "eager"; // a lazy one outside the page would never load, nor its decode() end
    fresh.src = src; // the browser has it now
    try { await fresh.decode(); } catch { continue; }
    if (!ph.isConnected) return;
    ph.replaceWith(fresh);
    requestAnimationFrame(() => fresh.classList.add("in"));
    return;
  }
}

const bookRow = (title, books, prefix) => !books.length ? ""
  : `<section class="row"><h2>${esc(title)}</h2><div class="strip">${books.map((b, i) => bookCard(b, `${prefix}-${i}`)).join("")}</div></section>`;

// A series of episodes ("Folge 212:…", "100A -…") in their order, other books by their title
const episodeNo = (title) => Number(title.match(/\d+/)?.[0] ?? NaN);
function sortBooks(books) {
  const byTitle = (a, b) => a.title.localeCompare(b.title, LANG, { numeric: true, sensitivity: "base" });
  if (books.filter((b) => !isNaN(episodeNo(b.title))).length < books.length / 2) return books.sort(byTitle);
  return books.sort((a, b) => (episodeNo(a.title) || Infinity) - (episodeNo(b.title) || Infinity) || byTitle(a, b));
}

const plainText = (html) => new DOMParser().parseFromString(html ?? "", "text/html").body.textContent.trim();

Object.assign(views, {
  // Start of the audiobooks: the libraries, what is begun, what is new in each
  async books() {
    const s = await bookSession(true);
    if (!s.ready) return serviceSignIn("audiobookshelf", s.configured ? s.error || "abs_unreachable" : "abs_not_configured");
    const [libs, begun] = await Promise.all([abs("/api/libraries").then((r) => (r.libraries ?? []).map((l) => ({ ...l, name: libName(l.name, "books") }))),
      abs("/api/me/items-in-progress").then((r) => books(r.libraryItems ?? [])), loadBookProgress(), loadHidden()]);
    const latest = await Promise.all(libs.map((l) =>
      abs(`/api/libraries/${l.id}/items?limit=20&sort=addedAt&desc=1&minified=1`).then((r) => books(r.results ?? []))));
    // a book playing leads the way back to it; music has its own tab at the top and its own start
    const playing = musicState.active && musicState.queue?.[musicState.index];
    const tiles = [...(playing?.book ? [[{ Id: "nowplaying", Name: t("nowPlaying"), Type: "NowPlaying" },
        "msection"]] : []),
      ...libs.map((l, i) => [{ Id: l.id, Name: l.name, Type: "Books", icon: "Book",
          wall: latest[i].filter((b) => b.cover).map((b) => bookCover(b, tileWidth(0.1))) }, "blib"])];
    let html = tiles.length ? libChips(tiles.map(([x, action], i) => libChip(x, `bsec-${i}`, action))) : "";
    html += bookRow(t("continueListening"), begun.filter(showBook), "listen");
    libs.forEach((l, i) => { html += bookRow(t("newIn", { name: l.name }), latest[i], `new${i}`); });
    view.innerHTML = `${await tabBar("books")}<div id="stage-art" class="beside"></div><div class="home music-home"><section class="hero" id="hero">${chipHero(tiles[0]?.[0] ?? { Name: partName("books") })}</section><div class="rows"><div class="rows-track">${html}</div></div></div>`;
    view.dataset.plainHero = "1";
    const first = begun[0] ?? latest.flat()[0];
    if (first) setBackdrop(bookCover(first, 1920));
    coverWall("books").then((urls) => {
      homeWall = { key: "books", urls };
      if (current.name === "books") tileWall(reg[document.activeElement?.dataset.r]);
    });
  },

  async booksearch({ q = "" }) {
    view.innerHTML = searchPage();
    showQuery(q);
    await runBookSearch(q);
  },

  // A whole library, its episodes in order
  async booklib({ id, name }) {
    const [r] = await Promise.all([abs(`/api/libraries/${encodeURIComponent(id)}/items?minified=1&limit=5000`), loadBookProgress()]);
    const all = sortBooks(books(r.results ?? []));
    view.innerHTML = `<div class="grid-page">${kicker(partName("books"))}<h1>${esc(name)} <span class="count">${all.length}</span></h1>
      ${all.length ? `<div class="grid">${all.map((b) => bookCard(b, `book-${b.id}`)).join("")}</div>` : `<div class="hint">${t("nothingFound")}</div>`}</div>`;
    if (all[0]) setBackdrop(bookCover(all[0], 1920));
  },

  // A book: cover, who wrote and reads it, carry on or from the start, heard or not, then its chapters
  async book({ id }) {
    const li = await abs(`/api/items/${encodeURIComponent(id)}?expanded=1&include=progress`);
    const m = li.media ?? {}, md = m.metadata ?? {}, p = li.userMediaProgress;
    const chapters = m.chapters ?? [];
    pageBook = { id: li.id, chapters };
    const begun = p && !p.isFinished && p.currentTime > 0;
    const facts = [md.authorName && `<b>${esc(md.authorName)}</b>`, md.narratorName && esc(t("readBy", { names: md.narratorName })),
      /^\d{4}$/.test(md.publishedYear ?? "") && md.publishedYear, m.duration && duration(m.duration * TICKS), // a free text field: only a year
      begun && esc(t("timeLeft", { time: duration((m.duration - p.currentTime) * TICKS) })), p?.isFinished && t("heard")].filter(Boolean);
    const actions = (begun ? `<button class="btn f" data-k="bplay" data-action="bplay" data-id="${esc(li.id)}">${ICON_PLAY}${t("resume")}</button>
        <button class="btn f" data-k="bstart" data-action="bplay" data-id="${esc(li.id)}" data-at="0">${t("startOver")}</button>`
      : `<button class="btn f" data-k="bplay" data-action="bplay" data-id="${esc(li.id)}"${p?.isFinished ? ' data-at="0"' : ""}>${ICON_PLAY}${t("play")}</button>`)
      + `<button class="btn f" data-k="bfinished" data-action="bfinished" data-id="${esc(li.id)}" data-on="${p?.isFinished ? 0 : 1}">${ICON_CHECK}${t(p?.isFinished ? "markUnheard" : "markHeard")}</button>`
      + (begun ? `<button class="btn f" data-k="bunheard" data-action="bfinished" data-id="${esc(li.id)}" data-on="0">${t("markUnheard")}</button>` : ""); // as a movie: begun, either
    const about = plainText(md.description);
    view.innerHTML = `<div class="detail music-detail moving"><section class="hero with-poster">
        <div class="poster sq"><img src="${bookCover(li, COVER_W)}" decoding="async" alt=""></div>
        <div class="info">${kicker(t("kindAudiobook"))}<h1 class="title">${esc(md.title ?? "")}</h1><div class="meta">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
          ${about ? `<div class="overview">${esc(about)}</div>` : ""}<div class="actions">${actions}</div></div></section>
      ${chapters.length > 1 ? `<section class="tracks"><h2>${t("chapters")}</h2>${chapters.map((c, i) => `<div class="track f" tabindex="-1" data-k="chapter-${i}" data-action="bchapterplay" data-i="${i}">
        <span class="no">${i + 1}</span><span class="t">${esc(c.title)}</span><span class="d">${clock((c.end - c.start) * TICKS)}</span></div>`).join("")}</section>` : ""}</div>`;
    setBackdrop(bookCover(li, 1920));
  },
});

// A search of Audiobookshelf in all its libraries: the books found by their titles, and the books of the series and
// authors found (its search lists an author by itself: their books asked for, perAuthor of each at most)
async function absSearch(term, limit, perAuthor) {
  const libs = (await abs("/api/libraries")).libraries ?? [];
  const found = await Promise.all(libs.map((l) => abs(`/api/libraries/${l.id}/search?q=${encodeURIComponent(term)}&limit=${limit}`)));
  const authors = [...new Map(found.flatMap((x) => x.authors ?? []).map((a) => [a.id, a])).values()].slice(0, 4);
  const byAuthor = await Promise.all(authors.map((a) => abs(`/api/authors/${a.id}?include=items`)
    .then((r) => (r.libraryItems ?? []).slice(0, perAuthor))
    .catch((e) => { if (e === STALE) throw e; return []; }))); // the titles found are shown all the same
  const seen = new Set();
  return books([...found.flatMap((x) => (x.book ?? []).map((b) => b.libraryItem)),
    ...found.flatMap((x) => (x.series ?? []).flatMap((s) => s.books ?? [])), ...byAuthor.flat()]
    .filter((li) => li && !seen.has(li.id) && seen.add(li.id)));
}

// The search of the audiobooks: titles, series, authors and readers in all libraries
async function runBookSearch(q) {
  const box = $("#results");
  if (!box) return;
  const term = q.trim(), stale = () => current.name !== "booksearch" || (current.args?.q || "") !== q;
  if (term.length < 2) {
    if (!term) return searchSuggestions(box, t("booksHint"));
    box.innerHTML = `<div class="hint">${t("oneMoreLetter")}</div>`;
    return;
  }
  let hits;
  try {
    hits = await absSearch(term, 40, 200);
  } catch (e) {
    if (e === STALE) return;
    if (!stale()) box.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
    return;
  }
  if (stale()) return; // typed on meanwhile
  box.innerHTML = hits.length ? `<h2 class="results-title">${partName("books")}</h2><div class="grid">${sortBooks(hits).map((b) => bookCard(b, `found-${b.id}`)).join("")}</div>`
    : nothingFoundHere(term);
}

async function bookAction(a, el, it) {
  if (a === "bbook") navigate("book", { id: it.id });
  else if (a === "blib") navigate("booklib", { id: it.Id, name: it.Name });
  else if (a === "bplay") bookPlay(el.dataset.id, el.dataset.at != null ? Number(el.dataset.at) : undefined);
  else if (a === "bchapterplay" && pageBook) {
    const start = pageBook.chapters[Number(el.dataset.i)]?.start ?? 0;
    const song = musicState.active && musicState.queue?.[musicState.index];
    if (song?.book && song.id === pageBook.id) { // this book plays already: only to the chapter
      await musicControl("seek", start);
      navigate("nowplaying", {});
    } else bookPlay(pageBook.id, start);
  }
  else if (a === "bfinished") bookHeard(el.dataset.id, el.dataset.on === "1");
}

// A book marked as heard, or as not heard (from the start again): from its page or, OK held, from its tile
async function bookHeard(id, on, el) {
  const r = await fetch("/abs/finished", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id, on }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  bookProgress.set(id, { ...bookProgress.get(id), finished: on, progress: on ? 1 : 0 });
  shelfCache.delete("books");
  shelfCache.delete("next");
  toast(t(on ? "markedHeard" : "markedUnheard"));
  if (el) render(current, el.dataset.k);
  else refresh();
}

// A book plays in the music's player: in the background, with the bar at the bottom and the sleep timer
async function bookPlay(id, at) {
  const r = await fetch("/abs/play", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id, at }) }).catch(() => null);
  if (!r?.ok) return toast(t("playFailed", { error: r?.status ?? "–" }), 5000);
  shelfCache.delete("books");
  musicState = { active: true, index: 0, queue: [], paused: false };
  await musicPoll(); // the book is known before "Now Playing" shows it, so its buttons can take the focus
  if (current.name !== "nowplaying") navigate("nowplaying", {});
}

// "Now Playing" with a book: its chapters instead of the queue, the one playing marked and its name above the title
function showChapters(np, q, song, s, focused) {
  const chapters = song.chapters ?? [];
  const i = Math.max(0, chapters.findLastIndex((c) => c.start <= (s.pos ?? 0) + 0.5));
  np.querySelector(".np-album").textContent = chapters.length > 1 ? chapters[i].title : "";
  if (q.dataset.songs !== `book-${song.id}`) {
    q.dataset.songs = `book-${song.id}`;
    q.dataset.index = i;
    q.innerHTML = `<div class="np-queue-track">${chapters.map((c, n) => `<div class="track f${n === i ? " on" : ""}" tabindex="-1" data-k="chapter-${n}" data-action="bchapter" data-i="${n}">
      <span class="no">${n + 1}</span><span class="t">${esc(c.title)}</span><span class="d">${clock((c.end - c.start) * TICKS)}</span></div>`).join("")}</div>`;
    const el = focused && q.querySelector(`[data-k="${focused.dataset.k}"]`);
    if (el) focusEl(el, false);
    else slideQueue(q.querySelector(".track.on"), false, 0.15);
  } else if (q.dataset.index !== String(i)) {
    q.dataset.index = i;
    q.querySelectorAll(".track.on").forEach((r) => r.classList.remove("on"));
    const on = q.querySelector(`[data-k="chapter-${i}"]`);
    on?.classList.add("on");
    if (!focused) slideQueue(on, true, 0.15);
  }
}

// "Now Playing" with a book, on its buttons: down goes to the next chapter, up to the start of this one (a few
// seconds in, else the one before), like a music player's skip keys. False when no book with chapters plays.
function bookChapter(step) {
  const song = musicState.active && musicState.queue?.[musicState.index];
  const chapters = song?.book ? song.chapters ?? [] : [];
  if (chapters.length < 2) return false;
  const pos = musicState.pos ?? 0;
  const i = Math.max(0, chapters.findLastIndex((c) => c.start <= pos + 0.5));
  const to = step > 0 ? i + 1 : pos - chapters[i].start > 3 ? i : i - 1;
  if (to >= chapters.length) return toast(t("lastChapter")), true;
  const c = chapters[Math.max(0, to)];
  musicState.pos = c.start; // the mark moves at once, the player follows
  musicControl("seek", c.start);
  toast(c.title || t("chapterN", { n: Math.max(0, to) + 1 }));
  return true;
}

// The speed of a book, one step further with each press (and kept for the next book)
function nextSpeed() {
  const now = BOOK_SPEEDS.indexOf(musicState.speed ?? 1);
  const speed = BOOK_SPEEDS[(now + 1) % BOOK_SPEEDS.length];
  musicState.speed = speed;
  toast(t("speed", { x: speed }));
  musicControl("speed", speed);
}
