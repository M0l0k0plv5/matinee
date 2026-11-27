"use strict";
// Matinee's page, part 6 of 18: the letters, Jellyfin's search and the search of everything.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

// ---------- Search ----------

// Letter grid on the left, results on the right: for Jellyfin as well as the music, the books and the podcasts
const typing = () => ["search", "allsearch", "musicsearch", "booksearch", "podsearch", "weatherplace", "svcentry"].includes(current.name);
// The page a letter typed on the current page opens: the search of its part, null where none fits
function searchHere() {
  if (typing()) return null;
  if (current.name === "menu") return "allsearch"; // the main menu: everything
  if (MUSIC_VIEWS.has(current.name)) return "musicsearch";
  if (BOOK_VIEWS.has(current.name)) return "booksearch";
  if (POD_VIEWS.has(current.name)) return "podsearch";
  return USER ? "search" : null;
}
// Each search names what it looks through above its words, all the time; a part's search has a way below its letters
// to the search of everything with the same words (where more than one app is set up)
const SEARCH_PART = { search: "jellyfin", musicsearch: "music", booksearch: "books", podsearch: "podcasts" };
const searchScope = () => (current.name === "allsearch" ? t("allApps") : SEARCH_PART[current.name] ? partName(SEARCH_PART[current.name]) : null);
const appsHere = () => [USER, ndSession?.configured, absState?.configured, pfState?.configured].filter(Boolean).length;
const nothingFoundHere = (term) => `<div class="hint">${t("nothingFoundIn", { term: esc(term), part: esc(searchScope()) })}</div>`;

function searchPage(marks = MARKS) {
  shifted = false;
  caret = null;
  const scope = searchScope();
  const wider = SEARCH_PART[current.name] && appsHere() > 1
    ? `<div class="actions search-wider"><button class="btn f" data-k="search-all" data-action="searchall">${esc(t("searchAllApps"))}</button></div>` : "";
  const keys = LETTERS.map((l, i) => `<div class="key f" tabindex="-1" data-k="key-${l}"${i ? "" : " data-autofocus"} data-action="type" data-val="${l}">${l}</div>`).join("")
    + marks.map((m, i) => `<div class="key mark f" tabindex="-1" data-k="key-mark${i}" data-action="type" data-val="${esc(m)}">${esc(m)}</div>`).join("")
    + `<div class="key wide f" tabindex="-1" data-k="key-shift" data-action="type" data-val="shift">⇧</div>`
    + `<div class="key wide f" tabindex="-1" data-k="key-space" data-action="type" data-val=" ">${t("space")}</div>`
    + `<div class="key wide f" tabindex="-1" data-k="key-del" data-action="type" data-val="del">⌫</div>`;
  return `<div class="search"><div class="search-left">${scope ? kicker(scope) : ""}<div class="query f" tabindex="-1" data-k="query" id="query"></div><div class="keys">${keys}</div>${wider}</div>
    <div class="search-results grid-page" id="results"></div></div>`;
}

function showQuery(q) {
  const placeholder = current.name === "svcentry" ? t(svcHint(current.args.svc, current.args.field))
    : current.name === "weatherplace" ? t("wxPlaceholder") : current.name === "allsearch" ? t("searchAllPlaceholder")
    : t("searchPart", { part: partName(SEARCH_PART[current.name] ?? "jellyfin") });
  if (current.name === "svcentry" && SVC_SECRET.has(current.args.field)) q = "•".repeat(q.length); // a password is not shown
  const at = Math.min(caret ?? q.length, q.length);
  $("#query").innerHTML = q ? `${esc(q.slice(0, at))}<span class="caret${at < q.length ? " mid" : ""}"></span>${esc(q.slice(at))}`
    : `<span class="hint">${placeholder}</span>`;
}

// Up from the letters is the text itself: left and right move the caret in it, to change a letter in the middle.
// null is the end, where it goes again with every new search.
let caret = null;
function moveCaret(dir) {
  const q = current.args?.q || "", at = Math.min(caret ?? q.length, q.length) + (dir === "left" ? -1 : 1);
  if (at < 0 || at > q.length) return;
  caret = at === q.length ? null : at;
  showQuery(q);
}

// ⇧ on the letter grid: the next letter is a capital, like on a phone
let searchTimer, shifted = false;
function shift(on) {
  shifted = on;
  $(".keys")?.classList.toggle("shifted", on);
}

function typeKey(val) {
  if (val === "shift") return shift(!shifted);
  if (shifted && val.length === 1) { val = val.toUpperCase(); shift(false); }
  let q = current.args?.q || "", at = Math.min(caret ?? q.length, q.length);
  if (val === "del") {
    if (!at) return;
    q = q.slice(0, at - 1) + q.slice(at);
    at--;
  } else {
    if (val === " " && (!at || q[at - 1] === " " || q[at] === " ")) return; // no space first, never two
    q = q.slice(0, at) + val + q.slice(at);
    at += val.length;
  }
  caret = at === q.length ? null : at;
  current.args = { ...current.args, q };
  showQuery(q);
  clearTimeout(searchTimer);
  if (q.trim()) { // the suggestions go with the first letter, not after the pause
    searchRun++;
    suggestRun++;
    const box = $("#results");
    if (box?.querySelector(".suggested")) box.innerHTML = "";
  }
  // a search waits until typing pauses
  if (current.name === "svcentry") return; // nothing to look up: Done keeps it
  searchTimer = current.name === "musicsearch" ? setTimeout(() => runMusicSearch(q), 300)
    : current.name === "booksearch" ? setTimeout(() => runBookSearch(q), 300)
    : current.name === "podsearch" ? setTimeout(() => runPodSearch(q), 200)
    : current.name === "weatherplace" ? setTimeout(() => runPlaceSearch(q), 400)
    : current.name === "allsearch" ? setTimeout(() => runAllSearch(q), 500)
    : setTimeout(() => runSearch(q), 300);
}

// Movies, shows and collections; Jellyfin only finds exact spellings: with few hits, also titles a typo away
// The titles a typo away wait for the index at most TITLES_WAIT ms; still loading, the search is done without them
// and "pending" says when they could be had (runSearch then searches again)
const TITLES_WAIT = 3000;
async function jellyfinSearch(term, limit) {
  const items = (await api(`/Items?userId=${USER}&searchTerm=${encodeURIComponent(term)}&Recursive=true&IncludeItemTypes=Movie,Series,BoxSet&Limit=${limit}&${FIELDS}`)).Items;
  let close = [];
  if (items.length < 10 && term.length >= 3) {
    const seen = new Set(items.map((it) => it.Id));
    const index = titleIndex();
    const known = await Promise.race([index, new Promise((r) => setTimeout(() => r(null), TITLES_WAIT))]);
    if (!known) return { items, close, pending: index };
    const ids = fuzzyMatch(term, known).filter((id) => !seen.has(id)).slice(0, 40);
    if (ids.length) {
      const extra = await api(`/Items?userId=${USER}&Ids=${ids.join(",")}&${FIELDS}`);
      const byId = new Map(extra.Items.map((it) => [it.Id, it]));
      close = ids.map((id) => byId.get(id)).filter(Boolean);
    }
  }
  return { items, close };
}

let searchRun = 0; // each run of the search counted: an answer for an older one is not shown
async function runSearch(q) {
  const box = $("#results");
  if (!box) return;
  const term = q.trim(), run = ++searchRun;
  if (!term) return searchSuggestions(box, t("searchHint"));
  if (term.length < 2) {
    box.innerHTML = `<div class="hint">${t("oneMoreLetter")}</div>`;
    return;
  }
  let items, close, pending;
  try {
    ({ items, close, pending } = await jellyfinSearch(term, 60));
  } catch (e) {
    if (e === STALE) return;
    if (current.name !== "search" || (current.args?.q || "") !== q) return;
    if (e === SIGNED_OUT) return render(current); // the sign-in in its place, then the search again (see render)
    // e.g. the server is briefly away: say so in plain words (a technical reason only to the log) instead of
    // leaving old results that no longer match
    if (e?.technical) console.warn("Search failed:", e.message);
    box.innerHTML = `<div class="hint">${e?.technical ? t("searchFailed") : t("error", { message: esc(e?.message ?? e) })}</div>`;
    return;
  }
  if (current.name !== "search" || (current.args?.q || "") !== q) return; // the user kept typing in the meantime
  // the titles a typo away once they are there, if the words are still the same and nothing found is being looked at
  // (only if they came: the index failing to load would otherwise be asked for again and again)
  pending?.then((known) => {
    if (known?.length && run === searchRun && current.name === "search" && (current.args?.q || "") === q && !box.contains(document.activeElement)) runSearch(q);
  });
  if (!items.length && !close.length) {
    box.innerHTML = nothingFoundHere(term);
    return;
  }
  const hint = !items.length ? `<div class="hint small">${t("closeMatches", { term: esc(term) })}</div>` : "";
  box.innerHTML = `${hint}<div class="grid">${[...items, ...close].map((it) => card(it)).join("")}</div>`;
}

// One search for everything (the main menu's Search): each part's own search at once, what it found each in its own
// place, in the dock's order, each shown as it comes; a part not set up is left out, one not answering too. Two lines
// of each at most (the music's artists first, then albums, then songs), so one part cannot push the others off the
// page; "More in …" goes on in that part's own search with the same words.
const ALL_LIMIT = { cards: 8, wide: 6 };
const PART_SEARCH = { jellyfin: "search", music: "musicsearch", books: "booksearch", podcasts: "podsearch" };
Object.assign(views, {
  async allsearch({ q = "" } = {}) {
    titleIndex(); // for the titles a typo away, while the words are typed
    view.innerHTML = searchPage();
    showQuery(q);
    await Promise.all([musicSession(), bookSession(), podSession()]);
    runAllSearch(q);
  },
});
let allRun = 0;
async function runAllSearch(q) {
  const box = $("#results");
  if (!box) return;
  const term = q.trim(), low = term.toLocaleLowerCase(LANG), run = ++allRun;
  const stale = () => run !== allRun || current.name !== "allsearch" || (current.args?.q || "") !== q;
  if (term.length < 2) {
    clearTimeout(busyTimer); // a search still running is for words since deleted: its ring would turn on and on
    document.body.classList.remove("busy");
    if (!term) return searchSuggestions(box, t("searchAllHint"));
    box.innerHTML = `<div class="hint">${t("oneMoreLetter")}</div>`;
    return;
  }
  const has = (...texts) => texts.some((x) => (x ?? "").toLocaleLowerCase(LANG).includes(low));
  // each part: [id, name, find, wide]; find gives the cards' HTML (or a function giving it, run once still wanted)
  const parts = [
    USER && ["jellyfin", partName("jellyfin"), async () => {
      const { items, close } = await jellyfinSearch(term, 18);
      return [...items, ...close].map((it) => card(it));
    }],
    ndSession?.configured && ["music", partName("music"), async () => {
      const r = (await nd("search3", { query: term, artistCount: 6, albumCount: 12, songCount: 12 })).searchResult3 ?? {};
      const albums = r.album ?? [];
      // a single's song beside its album looks the same twice: the album stands for it
      const songs = (r.song ?? []).filter((x) => !albums.some((a) => a.id === x.albumId && a.name === x.title));
      return () => { // the songs play from this page's list
        pageSongs = songs;
        return [...(r.artist ?? []).slice(0, 4).map((x) => musicCard(x, "artist", `all-artist-${x.id}`)),
          ...albums.map((x) => musicCard(x, "album", `all-album-${x.id}`)), ...songs.map((x) => musicCard(x, "song", `all-song-${x.id}`))];
      };
    }],
    absState?.configured && ["books", partName("books"), async () => {
      return sortBooks(await absSearch(term, 12, 12)).map((b) => bookCard(b, `all-book-${b.id}`));
    }],
    pfState?.configured && ["podcasts", partName("podcasts"), async () => {
      const [r, recent] = await Promise.all([getJson("/pf/podcasts"), getJson("/pf/recent")]);
      return [...(r?.podcasts ?? []).filter((x) => has(x.name, x.author)).map((x) => podcastCard(x, `all-pod-${x.id}`)),
        ...(recent?.latest ?? []).filter((e) => has(e.title)).map((e) => episodeCard(e, `all-ep-${e.id}`))];
    }],
  ].filter(Boolean);
  box.innerHTML = parts.map(([id]) => `<section class="all-part" id="all-${id}"></section>`).join("")
    + `<div class="hint" id="all-none" hidden>${t("nothingFoundFor", { term: esc(term) })}</div>`;
  clearTimeout(busyTimer);
  busyTimer = setTimeout(() => document.body.classList.add("busy"), 300);
  let any = false;
  await Promise.allSettled(parts.map(async ([id, name, find, wide]) => {
    let cards = await find().catch(() => []);
    if (stale()) return;
    if (typeof cards === "function") cards = cards();
    if (!cards.length) return;
    any = true;
    const limit = wide ? ALL_LIMIT.wide : ALL_LIMIT.cards;
    const more = cards.length > limit
      ? `<div class="actions all-more"><button class="btn f" data-k="all-more-${id}" data-action="allmore" data-val="${id}">${esc(t("moreIn", { part: name }))}</button></div>` : "";
    const part = $(`#all-${id}`);
    if (part) part.innerHTML = `<h2 class="results-title">${esc(name)}</h2><div class="grid${wide ? " wide" : ""}">${cards.slice(0, limit).join("")}</div>${more}`;
  }));
  if (stale()) return;
  clearTimeout(busyTimer);
  document.body.classList.remove("busy");
  if (!any) $("#all-none")?.removeAttribute("hidden");
}

// Before a letter is typed, every search shows suggestions of its app instead of an empty half, another choice each
// time: movies and shows not watched yet, albums, audiobooks not begun, new episodes not heard. Its hint is there until
// they come, and stays if they do not (the server away); the search of everything shows one row of each app's
const SUGGEST_ROW = 4; // a row of cards on the search's right
let suggestRun = 0; // each run counted: suggestions for an older one (letters typed since) are not shown
const SUGGEST = {
  search: async (n) => (await api(`/Items?userId=${USER}&Recursive=true&IncludeItemTypes=Movie,Series&Filters=IsUnplayed&SortBy=Random&Limit=${n}&${FIELDS}`))
    .Items.map((it) => card(it)),
  musicsearch: async (n) => ((await nd("getAlbumList2", { type: "random", size: n })).albumList2.album ?? []).filter(showAlbum)
    .map((x) => musicCard(x, "album", `sug-album-${x.id}`)),
  booksearch: async (n) => {
    // a page of 60 at random from each library, not all of it: its size asked first (one entry)
    const libs = (await abs("/api/libraries")).libraries ?? [];
    const page = async (l) => {
      const at = `/api/libraries/${encodeURIComponent(l.id)}/items?minified=1&limit=`;
      const total = (await abs(`${at}1`)).total ?? 0;
      return abs(`${at}60&page=${Math.floor(Math.random() * Math.max(1, Math.ceil(total / 60)))}`);
    };
    const [lists] = await Promise.all([Promise.all(libs.map(page)), loadBookProgress()]);
    const seen = new Set(); // a book in two libraries once
    return shuffled(books(lists.flatMap((r) => r.results ?? [])).filter((b) => !seen.has(b.id) && seen.add(b.id)
      && !bookProgress.get(b.id)?.progress && !bookProgress.get(b.id)?.finished))
      .slice(0, n).map((b) => bookCard(b, `sug-book-${b.id}`));
  },
  podsearch: async (n) => ((await getJson("/pf/recent"))?.latest ?? []).filter((e) => !e.finished && !(e.pos > 0))
    .slice(0, n).map((e) => episodeCard(e, `sug-ep-${e.id}`)),
};
// Kept with the page (its args, so in the stack too): Back, or the words deleted again, shows the same ones, and the
// focus goes back onto the one it was on; a search opened anew chooses again
async function searchSuggestions(box, hint) {
  const here = current.name, run = ++suggestRun, entry = current;
  if (entry.args?.suggested) return void (box.innerHTML = entry.args.suggested);
  box.innerHTML = `<div class="hint">${hint}</div>`;
  const still = () => run === suggestRun && current === entry && !(current.args?.q || "").trim();
  const keep = () => { entry.args = { ...entry.args, suggested: box.innerHTML }; };
  if (here === "allsearch") { // a few of each app set up, in the dock's order, each under its name as it comes
    const parts = [USER && ["jellyfin", "search"], ndSession?.configured && ["music", "musicsearch"],
      absState?.configured && ["books", "booksearch"], pfState?.configured && ["podcasts", "podsearch"]].filter(Boolean);
    let first = true;
    await Promise.allSettled(parts.map(async ([id, from]) => {
      const cards = await SUGGEST[from](SUGGEST_ROW).catch(() => []);
      if (!still() || !cards.length) return;
      if (first) box.innerHTML = parts.map(([p]) => `<section class="all-part suggested" id="sug-${p}"></section>`).join("");
      first = false;
      const at = $(`#sug-${id}`);
      if (at) at.innerHTML = `<h2 class="results-title">${esc(partName(id))}</h2><div class="grid">${cards.slice(0, SUGGEST_ROW).join("")}</div>`;
    }));
    if (still() && !first) keep();
    return;
  }
  const cards = await SUGGEST[here](18).catch(() => []);
  if (!still() || !cards.length) return;
  box.innerHTML = `<h2 class="results-title suggested">${t("suggestions")}</h2><div class="grid">${cards.join("")}</div>`;
  keep();
}
