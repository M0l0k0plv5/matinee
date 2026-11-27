"use strict";
// Matinee's page, part 14 of 18: the options under a held OK, and the reader.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

// A cut-off description on a detail page can be focused (up from the buttons) and opened in full with OK
function readableOverview() {
  const ov = view.querySelector(".hero:is(.with-poster, .wide) .overview");
  if (!ov || ov.scrollHeight <= ov.clientHeight + 2) return;
  // cut after a whole word, with "… MORE" at the end of its last line (the line clamp cut
  // anywhere, in the middle of a word); the whole text stays for the reader
  const full = ov.dataset.full = ov.textContent, words = full.split(/\s+/);
  const fits = (n) => {
    ov.innerHTML = `${esc(words.slice(0, n).join(" ").replace(/[\s,;:.–-]+$/, ""))}… <span class="more">${t("more")}</span>`;
    return ov.scrollHeight <= ov.clientHeight + 2;
  };
  let lo = 0, hi = words.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (fits(mid)) lo = mid;
    else hi = mid - 1;
  }
  fits(lo);
  ov.classList.add("f");
  ov.tabIndex = -1;
  Object.assign(ov.dataset, { k: "overview", action: "overview" });
}

const reader = $("#reader");
function openReader(el) {
  const title = el.closest(".info")?.querySelector(".logo")?.alt ?? el.closest(".info")?.querySelector(".title")?.textContent ?? "";
  reader.innerHTML = `<div class="reader-box"><h2>${esc(title)}</h2><p>${esc(el.dataset.full ?? el.textContent)}</p></div>`;
  reader.hidden = false;
  reader.opener = el;
}
function closeReader() {
  reader.hidden = true;
  reader.opener?.isConnected && focusEl(reader.opener, false);
}
reader.addEventListener("click", closeReader);
// keys while the full text is open: OK and Back close it, up and down scroll a long text
// OK held: a small menu of what fits the focused tile (My List, watched, a star, following a channel),
// and always the way to the settings and the main menu; with music also Now Playing and the sleep timer.
// Up and down choose, OK does it, Back closes it.
const options = $("#options");
let optionItems = [], optionAt = 0;
function openOptions(el) {
  holdTipDone();
  const it = el && reg[el.dataset.r];
  const items = [];
  if (forgotten && Date.now() < forgotten.until) items.push({ label: t("undoRemove"), run: undoForget });
  const gone = forgettable(el, it);
  // to what it belongs, the same in every part and first: an episode to its show (Jellyfin's or a podcast's), a song to
  // its album and artist, an album to its artist; not where one is
  const here = (name, id) => current.name === name && (current.args?.id ?? current.args?.q) === id;
  const playing = musicState.queue?.[musicState.index];
  const ep = it?.kind === "episode" ? it : el?.id === "nowbar" && playing?.podcast ? { id: playing.id, podcast: playing.podcast, title: playing.title } : null;
  const song = (el?.dataset.action === "mtrack" && pageSongs[Number(el.dataset.i)]) || (el?.dataset.action === "mqueue" && musicState.queue?.[Number(el.dataset.i)])
    || (el?.id === "nowbar" && !playing?.podcast && !playing?.book && playing);
  const music = song && !song.radio ? { ...song, kind: "song" } : it && ["song", "album", "artist"].includes(it.kind) ? it : null;
  const goTo = (label, name, args) => { if (!here(name, args.id ?? args.q)) items.push({ label: t(label), run: () => navigate(name, args) }); };
  if (it?.Type === "Episode" && it.SeriesId) goTo("goToSeries", "item", { id: it.SeriesId });
  if (ep) goTo("goToPodcast", "podcast", { id: ep.podcast });
  if (music?.kind === "song" && music.albumId) goTo("toAlbum", "album", { id: music.albumId });
  if (["song", "album"].includes(music?.kind) && music.artistId) goTo("toArtist", "artist", { id: music.artistId });
  if (it && ["Movie", "Episode", "Series", "Season", "BoxSet"].includes(it.Type) && el.dataset.action !== "missing") {
    const fav = !!it.UserData?.IsFavorite, played = !!it.UserData?.Played;
    items.push({ label: t(fav ? "removeFromMyList" : "addToMyListLong"), run: () => setFavorite(it.Id, !fav) });
    if (!played) items.push({ label: t("markWatched"), run: () => setPlayed(it.Id, true) });
    if (played || begun(it)) items.push({ label: t("markUnwatched"), run: () => setPlayed(it.Id, false) });
    if (["Movie", "Episode"].includes(it.Type)) items.push({ label: t("findSubtitles"), run: () => navigate("subsearch", { id: it.Id, name: it.Name }) });
  }
  // an audiobook on a tile, as a movie: heard or not (a begun one either way), from the start
  if (it?.kind === "book") {
    const p = bookProgress.get(it.id), begun = !p?.finished && (p?.progress ?? 0) > 0;
    if (!p?.finished) items.push({ label: t("markHeard"), run: () => bookHeard(it.id, true, el) });
    if (p?.finished || begun) items.push({ label: t("markUnheard"), run: () => bookHeard(it.id, false, el) });
    if (begun) items.push({ label: t("startOver"), run: () => bookPlay(it.id, 0) });
  }
  // a podcast followed: no longer (asked first)
  if (it?.kind === "podcast") items.push({ label: t("unfollow"), run: () => podUnfollow(el, it) });
  // a podcast's episode: heard or not, from the start
  if (ep) {
    if ("finished" in ep) { // as a movie: one begun can be marked as heard, or as not heard (from the start again)
      if (!ep.finished) items.push({ label: t("markHeard"), run: () => podHeard(el, ep, true) });
      if (ep.finished || ep.pos > 0) items.push({ label: t("markUnheard"), run: () => podHeard(el, ep, false) });
      if (ep.pos > 0 && !ep.finished) items.push({ label: t("startOver"), run: () => podPlay(ep.id, 0) });
    }
  }
  if (el?.dataset.action === "bt" && el.dataset.paired === "1" && el.dataset.audio === "1") items.push({ label: t("btForget"), run: () => btAction(el.dataset.mac, "remove", el.dataset.name) });
  if (music) items.push({ label: t(music.starred ? "unstarIt" : "starIt"), run: () => toggleStar(music.kind, music.id, !music.starred) });
  // while music plays: a song, an album or a playlist after the playing song, or at the end of the queue
  const queueable = el?.dataset.action === "mtrack" ? { ...pageSongs[Number(el.dataset.i)], kind: "song" }
    : it && ["song", "album", "playlist"].includes(it.kind) ? it : null;
  if (queueable && musicState.active && !musicState.queue?.[musicState.index]?.radio) {
    items.push({ label: t("playNext"), run: () => musicQueue(queueable, true) });
    items.push({ label: t("addToQueue"), run: () => musicQueue(queueable, false) });
  }
  if (musicState.active) {
    if (current.name !== "nowplaying") items.push({ label: t("nowPlaying"), run: () => navigate("nowplaying", {}) });
    // with a book, up and down on the buttons go from chapter to chapter: the list is reached from here
    if (current.name === "nowplaying" && $(".np-queue .track.on") && musicState.queue?.[musicState.index]?.book) {
      items.push({ label: t("chapterList"), run: () => focusEl($(".np-queue .track.on")) });
    }
    items.push({ label: t("sleepTimer"), run: cycleSleep });
  }
  // taking it off its row last of what fits it, not first and chosen: for Continue Watching it forgets the position on
  // the server, and an OK pressed once more out of habit after the hold would have done that
  if (gone) items.push({ label: t("removeFromRow"), run: () => forgetIt(el, gone) });
  if (current.name !== "settings") items.push({ label: t("settings"), run: () => navigate("settings", { part: settingsPart() }) });
  if (current.name !== "controls") items.push({ label: t("controls"), run: () => navigate("controls") });
  if (current.name !== "menu") items.push({ label: t("mainMenu"), run: mainMenu });
  const title = it?.Type === "Episode" ? `${it.SeriesName} · ${epCode(it)}` : it?.Name ?? it?.title ?? it?.name ?? song?.title ?? "";
  showOptions(el, title, items);
}

function showOptions(el, title, items, at = 0) {
  optionItems = items;
  optionAt = at;
  options.firstElementChild.innerHTML = (title ? `<h2>${esc(title)}</h2>` : "")
    + items.map((x, i) => `<div class="opt${x.on ? " on" : ""}" data-i="${i}">${esc(x.label)}</div>`).join("");
  showOption();
  options.hidden = false;
  options.opener = el;
}

function showOption() {
  options.querySelectorAll(".opt").forEach((o, i) => o.classList.toggle("sel", i === optionAt));
  optionItems[optionAt]?.preview?.(); // the effects: shown beside it as each is reached
}

function closeOptions() {
  options.hidden = true;
  options.opener?.isConnected && focusEl(options.opener, false);
}

function optionsKey(e) {
  e.preventDefault();
  const was = optionAt;
  if (e.key === "ArrowDown") optionAt = Math.min(optionItems.length - 1, optionAt + 1);
  else if (e.key === "ArrowUp") optionAt = Math.max(0, optionAt - 1);
  if (optionAt !== was) tick(optionItems[optionAt].sound); // the sounds to choose from: each heard as it is reached
  else if (OK.includes(e.key)) {
    if (e.repeat) return;
    const item = optionItems[optionAt];
    closeOptions();
    item?.run?.(); // Cancel: nothing to run
    return;
  }
  else if (BACK.includes(e.key) || MENU.includes(e.key) || HOME.includes(e.key)) return closeOptions();
  showOption();
}
options.addEventListener("click", (e) => {
  const o = e.target.closest(".opt");
  if (!o) return closeOptions();
  optionAt = Number(o.dataset.i);
  const item = optionItems[optionAt];
  closeOptions();
  item?.run?.();
});

function readerKey(e) {
  e.preventDefault();
  const box = reader.firstElementChild, behavior = document.documentElement.classList.contains("less-motion") ? "instant" : "smooth";
  if (e.key === "ArrowDown") box.scrollBy({ top: box.clientHeight * 0.6, behavior });
  else if (e.key === "ArrowUp") box.scrollBy({ top: -box.clientHeight * 0.6, behavior });
  else if (OK.includes(e.key) || BACK.includes(e.key) || HOME.includes(e.key)) closeReader();
}
