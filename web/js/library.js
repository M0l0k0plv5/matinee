"use strict";
// Matinee's page, part 12 of 18: large libraries, the typo-tolerant search and collections.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Large libraries ---------- */

// Listing a whole library takes Jellyfin seconds (517 movies: 3-13 s on a small server), 60 items well
// under one. So the first page is shown right away and the rest appended in the background. Complete
// lists are kept, so going back to a library is instant; after a change on the server the kept list is
// shown and quietly replaced by a fresh one.
const LIB_FIRST = 60, LIB_PAGE = 120, LIB_KEEP = 24;
const libCache = new Map();   // query → { items, stale }
// every sort, filter and genre is its own list: keep only the most recent ones, a TV runs for weeks
function keepLibrary(q, entry) {
  libCache.delete(q);
  libCache.set(q, entry);
  if (libCache.size > LIB_KEEP) libCache.delete(libCache.keys().next().value);
}
const genreCache = new Map(); // library id → genres

const fetchJSON = (path) => fetch("/jf" + path).then((r) => (r.ok ? r.json() : null)).catch(() => null);

// in pages, so the grid grows steadily instead of waiting for one long request
async function loadRest(gen, q, items) {
  while (true) {
    const page = await fetchJSON(`${q}&StartIndex=${items.length}&Limit=${LIB_PAGE}`);
    if (!page) return;
    items = items.concat(page.Items);
    const grid = $(".grid-page .grid");
    if (gen === renderGen && grid) {
      grid.insertAdjacentHTML("beforeend", page.Items.map((it) => card(it)).join(""));
      // the title to come back to may only arrive with a later page; unless someone moved on already
      const want = current.focusKey, el = want && lastKey < current.shownAt && grid.querySelector(`[data-k="${CSS.escape(want)}"]`);
      if (el && document.activeElement !== el) focusEl(el, false);
    }
    if (!page.Items.length || items.length >= page.TotalRecordCount) break;
  }
  keepLibrary(q, { items });
}

async function reloadLibrary(gen, q) {
  const res = await fetchJSON(q);
  if (!res) return;
  keepLibrary(q, { items: res.Items });
  const grid = $(".grid-page .grid");
  if (gen !== renderGen || !grid) return;
  const key = grid.contains(document.activeElement) && document.activeElement.dataset.k;
  grid.innerHTML = res.Items.map((it) => card(it)).join("");
  $(".grid-page .count").textContent = res.Items.length;
  const el = key && grid.querySelector(`[data-k="${CSS.escape(key)}"]`);
  if (el) focusEl(el, false);
}

/* ---------- Typo-tolerant search ---------- */

// All titles (and original titles), only the names: a few hundred small entries, but Jellyfin takes seconds to list
// them (10 s with ~600 titles on a small server). Loaded when a search opens, while the words are still being typed;
// after a change in the library the old ones serve until the new ones are there.
let titles = null, titlesFresh = false;
const loadTitles = () => fetch(`/jf/Items?userId=${USER}&Recursive=true&IncludeItemTypes=Movie,Series,BoxSet&Fields=OriginalTitle&EnableImages=false&EnableUserData=false`)
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
  .then((res) => res.Items.map((it) => ({ id: it.Id, words: [...new Set([it.Name, it.OriginalTitle].filter(Boolean).flatMap(words))] })));
function titleIndex() {
  if (!USER) return Promise.resolve([]);
  if (!titles) {
    titles = loadTitles().catch(() => { titles = null; return []; });
  } else if (!titlesFresh) {
    loadTitles().then((x) => { titles = Promise.resolve(x); }).catch(() => { titlesFresh = false; });
  }
  titlesFresh = true;
  return titles;
}
const titlesChanged = () => { titlesFresh = false; }; // a title may have been added: loaded anew when next needed

// Accents do not matter ("Zoë" = "zoe"), the German sharp s counts as ss
const words = (s) => s.toLowerCase().replace(/\u00df/g, "ss").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .split(/[^\p{L}\p{N}]+/u).filter(Boolean);

// How many typos a word may have: none for short words, they would match almost anything
const allowedTypos = (n) => (n >= 8 ? 2 : n >= 4 ? 1 : 0);

// Edit distance between the typed word and the start of a title word (the user may still be typing),
// counting a swapped pair of letters as one typo
function prefixDistance(a, b) {
  const m = a.length, n = Math.min(b.length, a.length + 2);
  let prev2 = null, prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) cur[j] = Math.min(cur[j], prev2[j - 2] + 1);
    }
    prev2 = prev;
    prev = cur;
  }
  return Math.min(...prev.slice(Math.max(1, m - 2)));
}

// Ids of titles where every typed word is close to the start of one of the title's words, best first
function fuzzyMatch(query, index) {
  const q = words(query);
  if (!q.length) return [];
  const hits = [];
  for (const { id, words: tw } of index) {
    let total = 0;
    for (const w of q) {
      const d = Math.min(...tw.map((x) => prefixDistance(w, x)));
      if (d > allowedTypos(w.length)) { total = -1; break; }
      total += d;
    }
    if (total >= 0) hits.push([total, id]);
  }
  return hits.sort((a, b) => a[0] - b[0]).map(([, id]) => id);
}

// ---------- Collection ----------

async function collectionView(col) {
  const res = await api(`/Items?userId=${USER}&parentId=${col.Id}&SortBy=PremiereDate,ProductionYear,SortName&${FIELDS}`);
  const next = res.Items.find((i) => !i.UserData?.Played);
  let play = "";
  if (next) {
    const pos = next.UserData?.PlaybackPositionTicks || 0;
    play = `<button class="btn f" data-k="play" data-action="play" data-id="${esc(next.Id)}" data-resume="1">${ICON_PLAY}${pos ? t("resume") : t("play")}: ${esc(next.Name)}</button>`;
  }
  view.innerHTML = `<div class="detail">${detailHero({ ...col, ChildCount: res.Items.length }, play + watchedButton(col, true) + favoriteButton(col))}
    ${row(t("collectionMovies"), res.Items, { prefix: "col" })}</div>`;
  showDetailArt(col);
  if (!backdropOf(col)) { // without a picture of its own: its movies' posters as a gliding wall
    view.insertAdjacentHTML("afterbegin", `<div id="stage-art" class="beside"></div>`);
    pageWall(`col-${col.Id}`, res.Items.map(posterOf), "poster");
  }
}

async function seriesView(series, seasonId, similar = false) {
  // with badges on, a season with no episode in the library is shown too, dimmed (its episodes all missing), when
  // some of them have aired: where the missing ones of the poster's 10/20 are
  const missing = BADGE_MODE !== "off";
  const [seasons, nextUp] = await Promise.all([
    api(`/Shows/${series.Id}/Seasons?userId=${USER}${missing ? "" : "&isMissing=false"}`),
    api(`/Shows/NextUp?userId=${USER}&seriesId=${series.Id}&Limit=1&${FIELDS}`),
  ]);
  seasons.Items = seasons.Items.filter((s) => s.IndexNumber !== 0 // specials stay out of the way
    && (!isMissing(s) || BADGES.seasons[s.Id]?.[1] > 0));
  const next = nextUp.Items[0];
  const selected = seasonId ?? next?.SeasonId ?? (seasons.Items.find((s) => !isMissing(s)) ?? seasons.Items[0])?.Id;

  let play = "";
  if (next) {
    const pos = next.UserData?.PlaybackPositionTicks || 0;
    play = `<button class="btn f" data-k="play" data-action="play" data-id="${esc(next.Id)}" data-resume="1">${ICON_PLAY}${pos ? t("resume") : t("play")} ${esc(epCode(next))}</button>`;
  }
  const chips = seasons.Items.map((s) => `<div class="chip f${s.Id === selected ? " sel" : ""}${isMissing(s) ? " missing" : ""}" tabindex="-1" data-k="season-${esc(s.Id)}" data-action="season" data-id="${esc(s.Id)}">${esc(s.Name)}${countBadge(BADGES.seasons[s.Id], true)}</div>`).join("");

  const watched = watchedButton(series, true) + favoriteButton(series) + trailerButton(series);
  view.innerHTML = `<div class="detail">${detailHero({ ...series, ChildCount: seasons.Items.filter((x) => !isMissing(x)).length }, play + watched)}
    <div class="chips">${chips}</div><section class="row"><div class="strip" id="episodes"></div></section>
    ${castRow(series)}${similar ? `<div id="similar"></div>` : ""}</div>`;
  showDetailArt(series);
  view.dataset.series = series.Id;
  if (similar) fillSimilar(renderGen, series.Id, t("similarShows"));
  await loadEpisodes(series.Id, selected, next?.Id);
}

const isMissing = (e) => e.LocationType === "Virtual";
let episodesAsked = 0; // the season asked for last (loadEpisodes)
async function loadEpisodes(seriesId, seasonId, scrollTo) {
  if (current?.name === "item") current.args = { ...current.args, seasonId }; // keep the season across refreshes
  // with badges on: the placeholders of missing episodes that have aired as well, dimmed, in their place
  const missing = BADGE_MODE !== "off";
  const asked = ++episodesAsked;
  const eps = await api(`/Shows/${seriesId}/Episodes?userId=${USER}&seasonId=${seasonId}${missing ? "" : "&isMissing=false"}&Fields=Overview,PremiereDate&EnableImageTypes=Primary,Thumb,Backdrop&ImageTypeLimit=1`);
  const strip = $("#episodes");
  // another season chosen meanwhile: its episodes, not these come late over them (a long season answers slower)
  if (!strip || asked !== episodesAsked) return;
  // Jellyfin places specials between the episodes of a season; only an opened special shows its own season
  eps.Items = eps.Items.filter((e) => e.ParentIndexNumber !== 0 || e.SeasonId === seasonId);
  eps.Items = eps.Items.filter((e) => !isMissing(e) || (e.PremiereDate && Date.parse(e.PremiereDate) <= Date.now()));
  strip.innerHTML = eps.Items.map((e) =>
    card(e, { land: true, key: `ep-${e.Id}`, action: isMissing(e) ? "missing" : "play", label: `${e.IndexNumber ?? ""}. ${e.Name}`,
      sub: isMissing(e) ? t("notInLibrary") : e.RunTimeTicks ? duration(e.RunTimeTicks) : "" })
      .replace('<div class="card', `<div class="card ep${isMissing(e) ? " missing" : ""}`)
      .replace(/<\/div>\s*$/, `${overviewOf(e) ? `<div class="ep-overview">${esc(overviewOf(e))}</div>` : ""}</div>`)
  ).join("");
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("sel", c.dataset.id === seasonId));
  const target = scrollTo && strip.querySelector(`[data-k="ep-${scrollTo}"]`);
  if (target) {
    scrollStrip(target, false);
    strip.lastFocus = target; // entering the row lands on this episode
  }
}
