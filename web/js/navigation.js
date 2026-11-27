"use strict";
// Matinee's page, part 13 of 18: rendering, focus, moving and the help.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Navigation ---------- */

// A page that could not be loaded (e.g. Jellyfin briefly busy) tries again by itself, or at once with OK.
// Without this the home screen could get stuck: there Back and Home have nowhere to go.
let RETRY_AFTER = 10000; // shortened in the tests
let retryTimer, retryRender = null;

// The page built again where it stands (a change on the server, a title marked): it takes a while, and the old one
// stays on the screen meanwhile. A step taken on it then is where the focus goes, not back to the tile focused when
// the rebuild began (moving along "More Like This", a change from Jellyfin threw the focus back to an earlier tile).
let focusedKey = null; // the key of what was focused last in the page
view.addEventListener("focusin", (e) => { focusedKey = e.target.dataset?.k ?? null; });

async function render(entry, focusKey, scrollTop = 0) {
  const gen = ++renderGen, again = entry === current, began = Date.now(), focusedThen = focusedKey;
  renderAbort.abort(STALE); // what the page before still asks for (see api)
  renderAbort = new AbortController();
  const offsets = entry.offsets; // where its rows and the page stood (keepOffsets), once
  delete entry.offsets;
  clearTimeout(retryTimer);
  // anything slower than a moment gets a spinner, so a key press never looks ignored
  clearTimeout(busyTimer);
  busyTimer = setTimeout(() => document.body.classList.add("busy"), 300);
  current = entry;
  document.body.dataset.view = entry.name; // e.g. no mouse's back button on the main menu
  reg = [];
  delete view.dataset.liveHero;
  delete view.dataset.plainHero;
  clearTimeout(heroTimer); // a picture for the page before's focus, not to come after it
  try {
    await views[entry.name](entry.args || {}, focusKey);
    if (USER) loadBadges(); // at most every few minutes; counts that change go onto the page as it stands
  } catch (e) {
    if (e === STALE || gen !== renderGen) return;
    if (e === SIGNED_OUT) { // the sign-in in its place until the backend is signed in again, then this page anew
      try { await connect(); } catch { return; } // gone on meanwhile
      if (gen !== renderGen) return;
      entry.offsets = offsets;
      return render(entry, focusKey, scrollTop);
    }
    view.classList.remove("still");
    clearTimeout(busyTimer);
    document.body.classList.remove("busy");
    // what happened in plain words, that it heals by itself; below it why, where that was written for people (a
    // service not reachable). A technical reason only goes to the log: on the TV it read "502 /Shows/NextUp?userId=…"
    const why = e?.name === "Error" && !e.technical ? e.message : "";
    if (!why) console.warn("Page not loaded:", e?.message ?? e);
    // a page of music, audiobooks or podcasts whose server did not answer: its symbol above, and its settings a
    // button away (the server's address, the sign-in), not only the wait
    const svc = !why || SIMPLE ? null : MUSIC_VIEWS.has(entry.name) ? ["navidrome", ICON_MUSIC] : BOOK_VIEWS.has(entry.name)
      ? ["books", ICON_BOOKS] : POD_VIEWS.has(entry.name) ? ["podcasts", ICON_PODCASTS] : null;
    const wasOn = document.activeElement?.dataset?.k; // the page failed before, retried by itself: its button kept
    view.innerHTML = `<div class="loading"><div class="failed">${svc ? `<div class="failed-icon">${svc[1]}</div>` : ""}
      <h2 class="failed-title">${esc(t("failedTitle"))}</h2>${why ? `<p class="failed-why">${esc(why)}</p>` : ""}
      <p class="failed-again">${esc(t("failedAgain"))}</p>
      <div class="actions"><button class="btn f" data-k="retry" data-action="retry">${t("retry")}</button>${svc
        ? `<button class="btn f" data-k="failed-settings" data-action="settingspart" data-val="${svc[0]}">${t("settings")}</button>` : ""}</div></div></div>`;
    focusEl((wasOn === "failed-settings" && $('[data-k="failed-settings"]')) || $('[data-k="retry"]'), false);
    retryRender = () => render(entry, focusKey, scrollTop);
    retryTimer = setTimeout(retryRender, RETRY_AFTER);
    return;
  }
  if (gen !== renderGen) return paintedOver();
  // a page that moves itself (rows, grids, long pages): the view does not scroll (a class, not :has(), which
  // made every change inside the page cost a look through all of it)
  view.classList.toggle("still", !!view.querySelector(".grid-page, .moving"));
  clearTimeout(busyTimer);
  document.body.classList.remove("busy");
  const moved = again && lastKey >= began && focusedKey && focusedKey !== focusedThen;
  if (moved) focusKey = focusedKey;
  entry.focusKey = focusKey;
  entry.shownAt = Date.now();
  view.scrollTop = scrollTop;
  readableOverview();
  // a page with nothing to pick but its bar: the part shown there
  const el = (focusKey && view.querySelector(`[data-k="${CSS.escape(focusKey)}"]`)) || view.querySelector("[data-autofocus]")
    || view.querySelector(".f:not(.overview):not(.tab)") || view.querySelector(".tabbar .tab.sel");
  heroAtOnce = true;
  try {
    if (el) focusEl(el, false);
  } finally {
    heroAtOnce = false; // never left on: every later step would load what it passes
  }
  if (el && offsets && !moved) putOffsets(offsets, el); // taken before the step: the focus has put it in view
  shown = { gen, node: view.firstElementChild };
}

// A page that came too late (Back pressed while a part's start was still loading) has put itself over the one shown
// since, the main menu: Matinee took it for the menu, with nowhere to go back to, and Back did nothing. The one that
// counts is built again; one that came too late after the next had not yet stood is drawn over by it anyway.
let shown = null; // the page that stood last (render): its turn and what it put into the view
function paintedOver() {
  if (shown?.gen === renderGen && view.firstElementChild !== shown.node) render(current, current.focusKey);
}

// Back (and a page built again where it is: a title marked, the return from a video) shows the page as it was left:
// each row moved as far as it was and the focused tile at the same height on the screen, not only the same tile
// focused. A row is known by its first tile. The page is put by the tile, not by how far it was moved: in a grid the
// tiles far above are laid out at an estimated height, which is not the same after the page is built again. Where
// the focused tile would be out of sight (the page changed meanwhile), it stays as the focus put it.
const movedBy = (n) => -(new DOMMatrix(getComputedStyle(n).transform).m42 || 0);
// how far down in the page a tile is laid out, without its own lift (the focus scales it)
function topIn(el, box) {
  let y = 0;
  for (let n = el; n && n !== box; n = n.offsetParent) y += n.offsetTop;
  return y;
}
function keepOffsets() {
  const strips = [...view.querySelectorAll(".strip")].filter((s) => s.slid != null && s.firstElementChild?.dataset.k)
    .map((s) => [s.firstElementChild.dataset.k, s.slid]);
  const page = view.querySelector(".rows-track, .grid-page, .moving"), el = document.activeElement;
  return { strips, page: page && page.contains(el) ? [page.className, topIn(el, page) - movedBy(page)] : null };
}
function putOffsets({ strips, page }, el) {
  const inView = () => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; };
  for (const [k, x] of strips) {
    const strip = view.querySelector(`.strip > [data-k="${CSS.escape(k)}"]`)?.parentElement;
    if (!strip || strip.slid === x) continue;
    const was = strip.slid;
    strip.slid = x;
    slide(strip, `translate3d(${-x}px,0,0)`, false);
    if (strip.contains(el) && !inView()) { strip.slid = was; slide(strip, `translate3d(${-was}px,0,0)`, false); }
  }
  const box = page && view.querySelector(".rows-track, .grid-page, .moving");
  if (!box || box.className !== page[0] || !box.contains(el)) return;
  const place = () => {
    const y = topIn(el, box) - page[1];
    if (Math.abs(y - movedBy(box)) < 1) return;
    const before = box.style.transform;
    slide(box, `translate3d(0,${-y}px,0)`, false);
    if (!inView()) slide(box, before, false);
  };
  place();
  // the tiles that came into sight are drawn at their real height only now: once more, unless a key moved on
  const at = Date.now();
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (document.activeElement === el && lastKey < at && box.isConnected) place();
  }));
}


function navigate(name, args) {
  stack.push({ ...current, focusKey: document.activeElement?.dataset?.k, scrollTop: view.scrollTop, offsets: keepOffsets() });
  render({ name, args });
}

// Back from a movie, show or collection leads to its library, on that title, however it was reached
// (home, search, "More Like This", several titles in a row); the next Back goes home. Opened straight
// from its library, that library comes back as it was, with its sorting and filters.
// Everywhere else (e.g. from an episode) Back is one page back.
// Back is always one page back, onto what was opened there. From further down a part's start
// it first goes up to the bar at its top; from there to the main menu.
// With the remote, Back on a page with tabs goes up to them first; with the mouse it goes straight back (the pointer
// would take the focus off the tab again at its next move, and Back would never get further)
// On the main menu, Back from its shelf goes down into the dock, to the tile the shelf belongs to.
function back(pointer = false) {
  if (document.activeElement?.id === "nowbar") return leaveNowbar();
  const tile = document.activeElement?.closest("#shelf") && view.querySelector(`[data-k="menu-${$("#shelf").dataset.for}"]`);
  if (tile) return focusEl(tile);
  const tab = $(".tabbar .tab.sel");
  if (tab && !pointer && !document.activeElement?.closest(".tabbar")) return focusEl(tab);
  const prev = stack.pop();
  // nowhere to go back to, though not on the main menu (a page that went wrong somehow): the main menu
  if (!prev) return current?.name !== "menu" || !view.querySelector(".menu") ? mainMenu() : undefined;
  return render(prev, prev.focusKey, prev.scrollTop); // done once that page stands
}

// Rows are moved with transforms instead of scrolling: runs on the GPU and stays smooth
function scrollStrip(el, smooth = true) {
  const strip = el.closest(".strip");
  if (!strip || strip.matches(".menu .tiles")) return; // the main menu's tiles stand still, all of them in view
  const pad = parseFloat(getComputedStyle(strip).paddingLeft);
  // scrollWidth leaves out the right padding of a flex row: measure up to the last tile plus the same margin,
  // so the last tile keeps its distance from the screen edge like the first one
  const last = strip.lastElementChild;
  const end = last.offsetLeft - strip.offsetLeft + last.offsetWidth + pad;
  const max = Math.max(0, end - strip.clientWidth);
  // the focus goes along the tiles in view; a step beyond the edge turns a page: that tile comes first (going left:
  // last), so the row moves once a screenful. Moved only as far as needed, it moved on every key at the edge, which
  // was restless; pinned to the left, it moved on every key always and the tile's own motion was lost
  const left = el.offsetLeft - strip.offsetLeft - pad;
  const right = el.offsetLeft - strip.offsetLeft + el.offsetWidth + pad - strip.clientWidth;
  const now = strip.slid ?? 0;
  const x = Math.min(max, Math.max(0, left < now ? right : right > now ? left : now));
  strip.slid = x;
  slide(strip, `translate3d(${-x}px,0,0)`, smooth);
}

// The queue on Now Playing moves inside its box like the pages do: the song at `at` of the box's height. The last one
// stops above the box's padding and the foot a TV may cut off, not at the screen's very edge.
function slideQueue(row, smooth, at = 0.5) {
  const track = row?.closest(".np-queue-track"), box = track?.parentElement;
  if (!track) return;
  const foot = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--edge-bottom")) / 100 * innerHeight;
  const keep = Math.max(parseFloat(getComputedStyle(box).paddingBottom) || 0, foot - (innerHeight - box.getBoundingClientRect().bottom));
  const max = Math.max(0, track.scrollHeight - (box.clientHeight - keep));
  const y = Math.min(max, Math.max(0, row.offsetTop + row.offsetHeight / 2 - box.clientHeight * at));
  slide(track, `translate3d(0,${-y}px,0)`, smooth);
}

function slide(el, transform, smooth) {
  if (!smooth) el.style.transition = "none";
  el.style.transform = transform;
  if (!smooth) { el.offsetWidth; el.style.transition = ""; }
}

// A grid page or a detail page moves with transforms (smoother than scrolling, which starts over with every key): the
// focused grid row, setting or song centered; near the top (the sort bar, the first settings) the page stays at its start
// The screen's top edge (at top in the page) through a row of a grid would leave a strip of its names and years:
// the page moves on past that row or back to show it whole, the edge in the middle of a gap, whichever is the
// shorter way that keeps the focused tile in view with room for its lift, above the foot a TV may cut off, and that
// the page can go (up to most). Its tiles are the grid's children.
function clearCutRow(grid, page, top, el, most) {
  const tiles = grid?.children, card = el.closest(".card");
  if (!tiles?.length || !card || top <= 0) return top;
  let cols = 1;
  while (cols < tiles.length && tiles[cols].offsetTop === tiles[0].offsetTop) cols++;
  let base = 0; // the grid's top in the page: its tiles are placed within it
  for (let n = grid; n && n !== page; n = n.offsetParent) base += n.offsetTop;
  for (let i = 0; i < tiles.length; i += cols) {
    const t = base + tiles[i].offsetTop, b = t + tiles[i].offsetHeight, next = tiles[i + cols];
    if (t >= top) return top; // the edge in a gap
    if (b <= top) continue;
    const gap = next ? base + next.offsetTop - b : 0;
    const css = getComputedStyle(document.documentElement);
    const lift = parseFloat(css.getPropertyValue("--focus-scale")) || 1.1;
    const ft = base + card.offsetTop, fb = ft + card.offsetHeight, room = card.offsetHeight * (lift - 1) / 2 + 2; // its growth
    const foot = (parseFloat(css.getPropertyValue("--edge-bottom")) || 0) / 100 * innerHeight;
    const ways = [b + gap / 2, Math.max(0, t - gap / 2)] // on past it, back to show it
      .filter((y) => y <= most && ft - y >= room && y + innerHeight - foot - fb >= room);
    return ways.sort((p, q) => Math.abs(p - top) - Math.abs(q - top))[0] ?? top;
  }
  return top;
}

function slidePage(el, smooth, again = true) {
  const page = el.closest(".grid-page, .moving");
  // offsetTop: the heading's top margin moves the page down (it collapses through), its bottom with it
  const max = Math.max(0, page.offsetTop + page.scrollHeight - innerHeight);
  // where it is on the screen now, plus how far the page is moved already
  const moved = -(new DOMMatrix(getComputedStyle(page).transform).m42 || 0), r = el.getBoundingClientRect();
  let y = Math.min(max, Math.max(0, moved + r.top + r.height / 2 - innerHeight / 2));
  // in the row with the page's last focusable element: all the way down, so what follows it (the versions
  // under the settings) shows too
  const focusable = page.querySelectorAll(".f"), row = el.closest(".row");
  if (!el.closest(".grid") && row && row.contains(focusable[focusable.length - 1])) y = max;
  if (el.closest(".hero")) y = 0; // the buttons of an album: its cover and title in full
  y = Math.min(max, clearCutRow(el.closest(".grid"), page, y - page.offsetTop, el, max - page.offsetTop) + page.offsetTop);
  slide(page, `translate3d(0,${-y}px,0)`, smooth);
  // tiles never seen are laid out at an estimated height, the rows slid past are drawn at their real one: arrived
  // after a long way (back to the 50th video), the focused tile is set right once more
  if (!smooth || !again) return;
  const arrived = (e) => {
    if (e.target !== page) return;
    page.removeEventListener("transitionend", arrived);
    if (document.activeElement !== el) return;
    const now = el.getBoundingClientRect(), mid = (b) => b.top + b.height / 2; // grown since, around its middle
    if (Math.abs(mid(now) - (mid(r) - (y - moved))) > 2) slidePage(el, true, false);
  };
  page.addEventListener("transitionend", arrived);
}

// How far a page's rows can move up: until the last row ends at the bottom (with room below it); the space above
// the track (the music page's padding) is part of what is seen
// The height the rows show in: their box, less what the music's bar covers at its foot
function rowsSeen(box) {
  const bar = document.body.classList.contains("with-nowbar") ? $("#nowbar") : null;
  const covered = bar && !bar.hidden ? Math.max(0, box.getBoundingClientRect().bottom - bar.getBoundingClientRect().top) : 0;
  return box.clientHeight - parseFloat(getComputedStyle(box).paddingTop) - covered;
}
function trackEnd(track) {
  const box = track.parentElement, last = track.lastElementChild;
  if (!last) return 0;
  const seen = rowsSeen(box);
  const root = getComputedStyle(document.documentElement);
  const foot = parseFloat(root.getPropertyValue("--edge-bottom")) / 100 * innerHeight; // in vh
  return Math.max(0, last.offsetTop + last.offsetHeight + Math.max(parseFloat(root.fontSize) * 1.5, foot) - seen);
}

const FOCUS_GROUPS = [".strip", ".chips", ".actions", ".grid", ".keys"];
function focusEl(el, smooth = true) {
  el.focus({ preventScroll: true });
  warmSimilar(el);
  // each group it is in keeps it: coming back into the group from above or below lands on it again (see move)
  for (const group of FOCUS_GROUPS.map((g) => el.closest(g))) if (group) group.lastFocus = el;
  scrollStrip(el, smooth);
  const behavior = smooth ? "smooth" : "instant";
  const track = el.closest(".rows-track");
  if (track) {
    const r = el.closest(".row");
    // the focused row at the top, but near the end only as far as the last row still ends at the bottom
    let y = Math.min(r.offsetTop, trackEnd(track));
    const seen = rowsSeen(track.parentElement);
    const foot = parseFloat(getComputedStyle(document.documentElement).fontSize) * 1.5;
    // no row cut through its titles at the top (the bottom half of them showed): one only partly moved out comes in
    // wholly if the focused row still fits below it; near the end, where the last row stands at the bottom, one mostly
    // in view stays so (its titles whole, its pictures cut), rather than the last rows standing at the top over empty
    // room; otherwise it goes out wholly
    const cut = [...track.children].find((row) => row.offsetTop < y && row.offsetTop + row.offsetHeight > y);
    if (cut && r.offsetTop + r.offsetHeight + foot <= cut.offsetTop + seen) y = cut.offsetTop;
    else if (cut && !(y < r.offsetTop && cut.offsetTop + cut.offsetHeight - y >= 0.4 * cut.offsetHeight)) {
      y = Math.min(r.offsetTop, cut.offsetTop + cut.offsetHeight);
    }
    // a row taller than the room (many buttons in lines): far enough for the focused one's
    // line to show, rather than its top staying at the top and the lower lines out of sight
    const top = el.getBoundingClientRect().top - track.getBoundingClientRect().top;
    if (top + el.offsetHeight + foot > y + seen) y = top + el.offsetHeight + foot - seen;
    slide(track, `translate3d(0,${-y}px,0)`, smooth); // .rows-track is the offsetParent of the rows
  } else if (el.closest(".epg")) epgScroll(el, smooth);
  else if (el.closest(".hero") && !el.closest(".moving")) view.scrollTo({ top: 0, behavior });
  else if (el.closest(".grid-page, .moving")) slidePage(el, smooth);
  else if (el.closest(".np-queue")) slideQueue(el, smooth);
  else if (el.closest(".tracks")) el.scrollIntoView({ block: "center", behavior }); // songs elsewhere
  else el.closest(".row, .chips")?.scrollIntoView({ block: "nearest", behavior });
  onFocus(el);
}

// A start page's top keeps below the bar: where a long title and its text are taller than its room, the text gives up
// lines, from the last, and then the title its second, rather than the top going up under the bar (whenever the top
// is filled anew)
function fitHero(hero) {
  if (!hero.firstElementChild || !hero.closest(".home")) return;
  const text = hero.querySelector(":scope > .overview"), title = hero.querySelector(":scope > .kicker + .title");
  for (const el of [text, title]) el?.style.removeProperty("-webkit-line-clamp");
  if (text) text.hidden = false;
  const room = () => hero.firstElementChild.getBoundingClientRect().top >= hero.getBoundingClientRect().top + parseFloat(getComputedStyle(hero).paddingTop) - 1;
  if (text) {
    for (let lines = parseInt(getComputedStyle(text).webkitLineClamp, 10) - 1; !room() && lines >= 0; lines--) {
      if (lines) text.style.setProperty("-webkit-line-clamp", String(lines));
      else text.hidden = true;
    }
  }
  if (title && !room()) title.style.setProperty("-webkit-line-clamp", "1"); // last: the title in one line
}
new MutationObserver((changes) => {
  const heroes = new Set();
  for (const c of changes) {
    if (c.target.id === "hero") heroes.add(c.target);
    for (const n of c.addedNodes) if (n.nodeType === 1) n.querySelectorAll?.("#hero").forEach((h) => heroes.add(h));
  }
  heroes.forEach(fitHero);
}).observe(view, { childList: true, subtree: true });
addEventListener("resize", () => { const hero = $("#hero"); if (hero) fitHero(hero); });

let heroTimer, seasonTimer;
// How long the focus rests on a tile before the top tells of it: going on quickly, nothing in between is loaded
const HERO_WAIT = 140;
// A page's first focus tells about it above at once: after the wait, the picture the page put there while it was
// built (Jellyfin's start: the film begun, before its first button's wall) came up for a moment first
let heroAtOnce = false;
const heroLater = (fn) => (heroAtOnce ? void fn() : setTimeout(fn, HERO_WAIT));
function onFocus(el) {
  const aside = $("#set-aside");
  if (aside && el.dataset.hint !== undefined) aside.textContent = el.dataset.hint;
  if (aside) sideIcon(el);
  dropTip(el);
  // not on a face of the cast: holding OK there has nothing of its own to offer
  if (el.classList.contains("card") && !el.closest(".menu .tiles") && el.dataset.action !== "person") holdTip();
  if (aside) el.dataset.k === "set-effects" ? fxPreview(fxNow()) : aside.parentElement.classList.remove("fx");
  const it = reg[el.dataset.r];
  if (view.dataset.plainHero && it) {
    clearTimeout(heroTimer);
    heroTimer = heroLater(() => {
      const hero = $("#hero");
      if (hero) hero.innerHTML = el.classList.contains("lib") ? chipHero(it) : plainHero(...plainHeroOf(it));
      // a part: the wall of what is in it; one title: its cover alone, large
      if (el.classList.contains("lib")) tileWall(it);
      else if (titleCover(it, COVER_W)) stageArt(titleCover(it, COVER_W), null, "cover");
    });
  }
  if (view.dataset.liveHero && it && el.dataset.action === "open") {
    clearTimeout(heroTimer);
    heroTimer = heroLater(() => {
      const hero = $("#hero");
      if (hero) hero.innerHTML = heroInner(it);
      setBackdrop(backdropOf(it));
      stageArt(backdropOf(it), null, "card", true); // one title: its picture as a card, as everywhere on the starts
    });
  }
  // the libraries row (and search, settings, one's own channels): not the movie last looked at above it
  if (view.dataset.liveHero && it && (el.dataset.action === "library" || el.dataset.action === "ownchannels")) {
    clearTimeout(heroTimer);
    heroTimer = heroLater(() => {
      const hero = $("#hero");
      if (hero) hero.innerHTML = chipHero(it);
      // a part, not a title: never one title's picture for it as a picture, only the wall of the library's newest
      // posters, as a Navidrome tile's covers, or nothing where too few; behind everything, blurred past knowing, the
      // colours of the one of them that pops most, as the music's start has its cover's (and the effects Calm,
      // without the wall, not a black page)
      const newest = (libWalls.get(it.Id) ?? []).filter((x) => x && backdropOf(x));
      const pop = newest.length ? poppingOf(newest, 1) : null; // the very best: the same each time
      if (pop) setBackdrop(backdropOf(pop), MENU_SOFT);
      else clearBackdrop();
      if (!pageWall(`lib-${it.Id}`, (libWalls.get(it.Id) ?? []).map(posterOf), "poster")) stageArt(null);
    });
  }
  if (current.name === "menu" && el.dataset.action === "section") {
    fitDockLabel(el);
    clearTimeout(shelfTimer);
    shelfTimer = setTimeout(() => fillShelf(reg[el.dataset.r]?.Id), HERO_WAIT);
    const id = reg[el.dataset.r]?.Id;
    if (WALL_SHAPE[id]) menuWall(id, el);
    else if (id === "next") nextArt(el);
    else if (id === "settings") deviceStage(el);
    else { stageArt(null); stageTitle(null); }
  }
  // a title on the shelf: its picture beside, as a card or its cover; behind the glass the menu's own picture stays
  // (each title's behind, blurred, changed it at every step and left the last one there, often a dull one)
  if (current.name === "menu" && it && el.closest("#shelf")) {
    clearTimeout(heroTimer);
    heroTimer = heroLater(() => {
      const own = titleCover(it, 1920);
      const bg = own ?? (it.Id ? backdropOf(it) : null);
      if (own) stageArt(titleCover(it, COVER_W), null, "cover"); // an album, a book, a podcast: its cover alone
      else if (it.Id && bg) stageArt(menuArtUrl = bg, null, "card");
      if (it.Id && bg) menuArtItem = it;
      stageTitle(it);
    });
  }
  if (el.dataset.action === "season" && !el.classList.contains("sel")) {
    clearTimeout(seasonTimer);
    seasonTimer = setTimeout(() => loadEpisodes(view.dataset.series, el.dataset.id), 300);
  }
}

// A dock tile's name under it, centred, but inside the dock: a long one under a tile at its end ("Einstellungen" under
// the narrow settings tile, with large text all the more) went out over its edge
function fitDockLabel(el) {
  const label = el.querySelector(".label"), dock = el.parentElement;
  if (!label?.textContent) return;
  label.style.translate = "";
  const text = document.createRange();
  text.selectNodeContents(label);
  const t = text.getBoundingClientRect(), d = dock.getBoundingClientRect(), a = el.getBoundingClientRect();
  const room = parseFloat(getComputedStyle(dock).paddingRight) / 2; // half the dock's own margin stays free
  // a name wider than its box runs out to the right of it: centred under the tile again first
  let shift = (a.left + a.right) / 2 - (t.left + t.right) / 2;
  shift = Math.min(shift, d.right - room - t.right);
  shift = Math.max(shift, d.left + room - t.left);
  if (Math.abs(shift) >= 1) label.style.translate = `${Math.round(shift)}px 0`;
}

// Spatial navigation: next focusable element in the arrow direction
// The way back: the opposite arrow right after a step goes back where it came from, as a page that maps each way
// would, where the nearest thing that way is another one of the same group (a wide key below two narrow ones, a
// grid's shorter last row, the tab shown rather than the one left from). Along the bar or a row the arrow goes on.
const OPPOSITE = { up: "down", down: "up", left: "right", right: "left" };
const WAY_GROUPS = ".strip, .chips, .actions, .grid, .keys, .tabbar, .tiles, .slist, .search-left";
let wayBack = null; // { from, to, dir } of the last step
function move(dir) {
  const cur = document.activeElement, back = wayBack;
  let to = null;
  wayBack = null;
  const from = back?.to === cur && back.dir === OPPOSITE[dir] && back.from?.isConnected && back.from.offsetWidth && back.from;
  findMove(dir, (el) => (to = el), !!from);
  if (document.activeElement !== cur) return; // a step of its own (into the music's bar, the guide, the caret)
  // and where the search finds nothing at all: the page still gliding from the step, what was there is not yet
  // (and across a search's two halves, the letters and the results: back onto the one left from)
  const across = (a, b) => a.closest("#results") && b.closest(".search-left");
  if (from && from !== to && (!to || to.closest(WAY_GROUPS) === from.closest(WAY_GROUPS) || across(from, cur) || across(cur, from))) to = from;
  if (!to) return;
  focusEl(to);
  if (to !== cur && cur?.classList.contains("f")) wayBack = { from: cur, to, dir }; // from something focused only
}

function findMove(dir, go, wayBackThere = false) {
  const cur = document.activeElement;
  if (!cur?.classList.contains("f")) {
    const first = view.querySelector(".f");
    return first && go(first);
  }
  if (cur.id === "nowbar") { // the music's bar at the bottom: up goes back to where the focus came from
    if (dir === "up") leaveNowbar();
    return;
  }
  if (cur.closest(".epg")) return epgMove(cur, dir);
  if (cur.id === "query" && (dir === "left" || dir === "right")) return moveCaret(dir);
  // left from the first tile of a start's row: up into the bar, on the part being shown (search and settings at its ends)
  if (dir === "left" && !cur.closest(".tabbar") && cur.closest(".strip, .chips") && !cur.previousElementSibling?.classList.contains("f")) {
    const tab = $(".tabbar .tab.sel");
    if (tab) return go(tab);
  }
  // the bar at the top: nothing above it (a row moved up past it is not there to go to); below it the page's first
  // row, on what was left there last, else its first
  if (cur.closest(".tabbar") && (dir === "up" || dir === "down")) {
    if (dir === "up") return;
    const first = [...view.querySelectorAll(".f")].find((e) => !e.closest(".tabbar") && e.offsetWidth);
    const group = first?.closest(".chips, .strip");
    const to = group?.lastFocus?.isConnected ? group.lastFocus : group?.matches(".strip") ? rowEntry(group) : first;
    if (to) return go(to);
  }
  // down from the main menu's shelf: the tile it belongs to, not the one nearest below (the dock is narrower)
  if (dir === "down" && cur.closest("#shelf")) {
    const tile = view.querySelector(`[data-k="menu-${$("#shelf").dataset.for}"]`);
    if (tile) return go(tile);
  }
  // the description is wide, its middle would point at a later button: down goes to the first one
  if (cur.dataset.k === "overview" && dir === "down") {
    const btn = cur.closest(".info")?.querySelector(".actions .f");
    if (btn) return go(btn);
  }
  // the main menu's tiles go round: left on the first is the last, right on the last the first
  if ((dir === "left" || dir === "right") && cur.parentElement.matches(".menu .tiles")) {
    const tiles = cur.parentElement.children, end = dir === "left" ? tiles[0] : tiles[tiles.length - 1];
    if (cur === end) return go(dir === "left" ? tiles[tiles.length - 1] : tiles[0]);
  }
  // from the letters right into the results: the first one found, the row of channels above it is one key up
  if (dir === "right" && cur.closest(".search-left")) {
    const lastKeyInRow = !cur.nextElementSibling || cur.nextElementSibling.offsetTop > cur.offsetTop; // not the rect: the focused key is scaled up
    const first = lastKeyInRow && view.querySelector("#results .grid .card");
    if (first) return go(first);
  }
  // up or down out of a group of results: the group just above or below it (past its heading, e.g. the channels
  // found, or the next row of programmes), not the letters beside them, which may be nearer. Down from the last
  // group stays there; up from the first goes on to the bar at the top.
  if ((dir === "up" || dir === "down") && cur.closest("#results .grid") && !gridNeighbor(cur, dir)) {
    const step = dir === "up" ? "previousElementSibling" : "nextElementSibling";
    let other = cur.closest(".grid")[step];
    while (other && !other.querySelector(".f")) other = other[step];
    if (!other) { // the last of its part (the search of everything: each app's in a section of its own): the next part's
      const groups = [...view.querySelectorAll("#results .grid, #results .actions, #results .strip")].filter((g) => g.querySelector(".f"));
      other = groups[groups.indexOf(cur.closest(".grid")) + (dir === "up" ? -1 : 1)];
    }
    const group = [...(other?.querySelectorAll(".f") ?? [])];
    if (dir === "up") group.reverse(); // from below: its last row first
    const x = cur.getBoundingClientRect().left, top = group[0]?.getBoundingClientRect().top;
    const row = group.filter((el) => Math.abs(el.getBoundingClientRect().top - top) < 2);
    const near = row.sort((a, b) => Math.abs(a.getBoundingClientRect().left - x) - Math.abs(b.getBoundingClientRect().left - x))[0];
    if (near) return go(near);
    if (dir === "down") return;
  }
  const next = gridNeighbor(cur, dir);
  if (next) return go(next);
  if (dir === "down" && cur.closest(".search-left .keys") && ![...cur.parentElement.children].some((k) => k.offsetTop > cur.offsetTop)) {
    const wider = view.querySelector(".search-wider .f"); // below the letters only their own button, never the results beside
    return wider ? go(wider) : undefined;
  }
  if (dir === "right" && cur.closest("#results .grid")) return; // a group of results' last one: its end stays put, not up into a longer row above
  // along a row: the tile beside it, as the search below would find, without measuring every element of the page
  // (some 600 on a start: 12 ms a key, twice that on a slow machine); at the row's end the search goes on as before
  if ((dir === "left" || dir === "right") && cur.parentElement?.classList.contains("strip")) {
    const side = dir === "right" ? cur.nextElementSibling : cur.previousElementSibling;
    if (side?.classList.contains("f") && side.offsetTop === cur.offsetTop && side.offsetWidth) return go(side);
  }
  const r = cur.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const horizontal = dir === "left" || dir === "right";
  const cands = [], loose = [];
  // a search's two halves, the letters and the results: up and down stay in the half they are in (left and right cross)
  const half = !horizontal && (cur.closest(".search-left") ? ".search-left" : cur.closest("#results") ? "#results" : null);
  for (const el of view.querySelectorAll(".f")) {
    if (el === cur || (half && !el.closest(half))) continue;
    const e = el.getBoundingClientRect();
    if (!e.width) continue;
    const ex = e.left + e.width / 2, ey = e.top + e.height / 2;
    let main, cross;
    if (dir === "left") { if (e.right > r.left + 2) continue; main = cx - ex; cross = Math.abs(ey - cy); }
    if (dir === "right") { if (e.left < r.right - 2) continue; main = ex - cx; cross = Math.abs(ey - cy); }
    if (dir === "up") { if (e.bottom > r.top + 2) continue; main = cy - ey; cross = Math.abs(ex - cx); }
    if (dir === "down") { if (e.top < r.bottom - 2) continue; main = ey - cy; cross = Math.abs(ex - cx); }
    if (horizontal && cross > r.height / 2) { loose.push({ el, main, cross }); continue; } // sideways: same row first
    cands.push({ el, main, cross, left: e.left });
  }
  if (!cands.length && horizontal && loose.length && cur.closest(".search")) {
    // nothing in the same row: from the letter grid to the search results, the nearest element. Elsewhere a row's end
    // is its end: on into another row (or up into the bar) was never where one meant to go
    return go(loose.reduce((a, b) => (b.main + b.cross < a.main + a.cross ? b : a)).el);
  }
  if (!cands.length) {
    // below a page's last row: the bar of the music playing, if it shows; not while the way back is there (the page
    // still gliding from the step up finds nothing below yet: move goes back the way it came instead)
    if (dir === "down" && !wayBackThere) toNowbar(cur);
    return;
  }
  let best;
  if (horizontal) {
    best = cands.reduce((a, b) => (b.main < a.main ? b : a));
  } else {
    // first the next row, then the element closest in x
    const minMain = Math.min(...cands.map((c) => c.main));
    const band = cands.filter((c) => c.main < minMain + 40);
    best = band.reduce((a, b) => (b.cross < a.cross ? b : a));
    const strip = best.el.closest(".strip");
    if (strip && strip !== cur.closest(".strip")) return go(rowEntry(strip));
    // up into the bar at the top: onto the part being shown, not onto the magnifier the first row's left is under
    const bar = best.el.closest(".tabbar");
    if (bar && !cur.closest(".tabbar")) return go(bar.querySelector(".tab.sel") ?? best.el);
    // up to a page's buttons (from its seasons, its cast): the one used last, else the first (Play), not the one
    // that happens to stand above
    const actions = best.el.closest(".actions");
    if (actions && actions !== cur.closest(".actions")) return go(actions.lastFocus?.isConnected ? actions.lastFocus : actions.querySelector(".f"));
    // into a row of buttons, a grid or the letters from above or below: where one left it (the way back lands
    // where one came from); buttons never left: the one chosen (the season whose episodes are shown)
    // Buttons in several lines (more than one line holds): only one in the line entered, else the way back from the
    // line beyond would not lead back
    const group = best.el.closest(".chips, .grid, .keys");
    if (group && group !== cur.closest(".chips, .grid, .keys")) {
      const top = (el) => el.getBoundingClientRect().top;
      const inLine = (el) => el && (!group.matches(".chips") || Math.abs(top(el) - top(best.el)) < 8);
      const kept = group.lastFocus?.isConnected ? group.lastFocus : group.querySelector(".chip.sel");
      return go(inLine(kept) ? kept : best.el);
    }
  }
  go(best.el);
}

// Entering a row from above or below: the tile last focused there, otherwise the first visible one
function rowEntry(strip) {
  if (strip.lastFocus?.isConnected) return strip.lastFocus;
  const x = -(new DOMMatrix(getComputedStyle(strip).transform).m41 || 0);
  const tiles = [...strip.querySelectorAll(".f")];
  return tiles.find((t) => t.offsetLeft - strip.offsetLeft >= x) ?? tiles[0];
}

// Inside a grid the neighbor follows from row and column, so large libraries do not
// have to measure every tile on each key press (matters on slow machines).
function gridNeighbor(cur, dir) {
  const grid = cur.closest(".grid");
  if (!grid) return null;
  const cards = grid.children;
  const i = Array.prototype.indexOf.call(cards, cur);
  const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").length;
  // right at the end of a row goes on to the first tile of the next one, left at its start back to the last one
  // of the row above; only the search results' left edge leads out of the grid, to the letters
  const out = !(i % cols) && (!i || grid.closest(".search-results"));
  const j = { left: out ? -1 : i - 1, right: i + 1 < cards.length ? i + 1 : -1, up: i - cols,
              down: i + cols < cards.length ? i + cols : Math.floor(i / cols) < Math.floor((cards.length - 1) / cols) ? cards.length - 1 : -1 }[dir];
  return j >= 0 ? cards[j] : null; // at the edge: fall back to the geometric search (e.g. up to the filter bar)
}
