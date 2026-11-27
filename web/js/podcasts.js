"use strict";
// Matinee's page, part 11 of 18: podcasts.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Podcasts (Podfetch) ---------- */

const POD_VIEWS = new Set(["podcasts", "podcast", "podsearch", "podcat"]);
const POD_ACTIONS = new Set(["ppodcast", "pplay", "pcat", "punfollow", "pfollow"]);
let pfState = null;
// whether there is a Podfetch (the menu shows its tile then) and whether it lets Matinee in
async function podSession(fresh = false) {
  if (!pfState || fresh) pfState = await fetch("/pf/session").then((r) => r.json()).catch(() => ({ configured: false }));
  return pfState;
}

// Podfetch through the backend, which signs in; like abs(), a newer render makes it STALE
async function pf(path) {
  const gen = renderGen;
  const r = await fetch(`/pf${path}`);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(t(`err_${data.error || "pf_unreachable"}`));
  if (gen !== renderGen) throw STALE;
  return data;
}

// The start's lists as shown last: Podfetch takes a second or two for what was heard, so the start shows the kept
// ones at once and asks behind them (podLatest), as Jellyfin's home. Forgotten when Matinee itself changes them (an
// episode played or marked, a podcast followed or not), so the page then waits for what is so now.
let podKept = null; // { data: [{podcasts}, recent], sig, checked }
const loadPods = () => Promise.all([pf("/podcasts"), pf("/recent")]);
const forgetPods = () => (podKept = null);
function podLatest(gen, kept) {
  loadPods().then((data) => {
    if (podKept !== kept) return; // a change meanwhile: the next start asks anyway
    const sig = JSON.stringify(data);
    if (sig === kept.sig) return;
    podKept = { data, sig, checked: true };
    const put = () => {
      if (gen !== renderGen || podKept?.data !== data) return; // gone on: the next start has it without asking
      const busy = 3000 - (Date.now() - lastKey);
      if (busy > 0 || polling) return setTimeout(put, Math.max(busy, 1000)); // not under a key being pressed
      refresh();
    };
    put();
  }).catch((e) => {
    if (e === STALE || podKept !== kept) return; // the page gone on: the next start asks again
    forgetPods(); // Podfetch away, or it wants a sign-in: the page says so, as when opened without anything kept
    if (gen === renderGen) refresh();
  });
}

// a podcast's picture (an episode shows its podcast's), fetched by the backend
const podCover = (id) => `/pf/cover/${encodeURIComponent(id)}`;
const podOf = (p) => ({ ...p, kind: "podcast" });
// the podcasts heard first, the last one first (as Recently Played in music), then the others by name
const byHeard = (podcasts, heard = []) => {
  const at = (p) => (heard.includes(p.id) ? heard.indexOf(p.id) : heard.length);
  return [...podcasts].sort((a, b) => at(a) - at(b));
};
const episodeOf = (e) => ({ ...e, kind: "episode" });
// how far an episode has been heard, 0–100
const heardPct = (e) => (e.finished ? 100 : e.duration ? Math.min(100, (e.pos / e.duration) * 100) : 0);

const podcastCard = (p, key) => tile({ item: podOf(p), key, action: "ppodcast", shape: "sq",
  art: cardImg(p.cover ? podCover(p.id) : null, p.name), label: p.name, sub: p.author || null });
// an episode on a row: its podcast's picture, how far it was heard; OK plays it (from where it was left)
const episodeCard = (e, key) => tile({ item: episodeOf(e), key, action: "pplay", shape: "sq",
  art: cardImg(podCover(e.podcast), e.title) + (e.finished ? `<div class="watched">✓</div>` : progressBar(heardPct(e))),
  label: e.title, sub: e.show || calendarDay(e.date) });
// The categories a podcast's feed gives (the directory's names, which Podfetch keeps), in the language shown where it has them
const catName = (c) => POD_CATEGORIES[LANG]?.[c] || c;
const sameCat = (a, b) => a.toLocaleLowerCase("en") === b.toLocaleLowerCase("en");
// the podcasts' categories, the one with the most podcasts first, each once however a feed spells it
function podCategories(podcasts) {
  const found = new Map();
  for (const p of podcasts) {
    for (const c of p.categories ?? []) {
      const k = c.toLocaleLowerCase("en"), had = found.get(k);
      found.set(k, { c: had?.c ?? c, n: (had?.n ?? 0) + 1 });
    }
  }
  return [...found.values()].sort((a, b) => b.n - a.n || catName(a.c).localeCompare(catName(b.c), LANG)).map((x) => x.c);
}

const episodeRow = (title, eps, prefix) => !eps.length ? ""
  : `<section class="row"><h2>${esc(title)}</h2><div class="strip">${eps.map((e, i) => episodeCard(e, `${prefix}-${i}`)).join("")}</div></section>`;

Object.assign(views, {
  // Start of the podcasts: what is begun, the newest episodes of all, then the podcasts
  async podcasts() {
    const kept = podKept;
    // with lists kept, Podfetch is not asked first whether it lets Matinee in (that reads all its podcasts): what it says
    // to the lists asked behind them shows it
    const s = await podSession(!kept);
    if (!s.ready) return serviceSignIn("podfetch", s.configured ? s.error || "pf_unreachable" : "pf_not_configured");
    const [{ podcasts: all }, recent] = kept?.data ?? await loadPods();
    if (!kept) podKept = { data: [{ podcasts: all }, recent], sig: JSON.stringify([{ podcasts: all }, recent]), checked: false };
    else if (kept.checked) kept.checked = false; // just brought in by podLatest: the next start asks again
    else podLatest(renderGen, kept);
    const podcasts = byHeard(all, recent.heard);
    // at the top, as the libraries on Jellyfin's start: all podcasts; their categories on that page, as a library's genres
    const allChip = podcasts.length ? { Name: t("allPodcasts"), icon: "PodcastList" } : null;
    let html = allChip ? libChips([libChip(allChip, "pcat-all", "pcat")]) : "";
    html += episodeRow(t("continueListening"), recent.begun, "listen");
    html += episodeRow(t("newEpisodes"), recent.latest.slice(0, 30), "newep");
    html += podcasts.length ? `<section class="row"><h2>${t("podcasts")}</h2><div class="strip">${podcasts.map((p, i) => podcastCard(p, `pod-${i}`)).join("")}</div></section>`
      : `<div class="hint">${t("noPodcasts")}</div>`;
    const first = recent.begun[0] ?? recent.latest[0];
    view.innerHTML = `${await tabBar("podcasts")}<div id="stage-art" class="beside"></div><div class="home music-home"><section class="hero" id="hero">${chipHero(allChip ?? { Name: partName("podcasts") })}</section><div class="rows"><div class="rows-track">${html}</div></div></div>`;
    view.dataset.plainHero = "1";
    if (first) setBackdrop(podCover(first.podcast));
    coverWall("podcasts").then((urls) => {
      homeWall = { key: "podcasts", urls };
      if (current.name === "podcasts" && urls.length) stageArt("podcasts", urls);
    });
  },

  // The podcasts of a category (or all of them), as a library with its genres: the categories above the grid
  async podcat({ cat = "" }) {
    const [{ podcasts }, { heard = [] }] = await Promise.all([pf("/podcasts"), pf("/recent").catch(() => ({}))]);
    const cats = podCategories(podcasts);
    const shown = cat ? podcasts.filter((p) => (p.categories ?? []).some((c) => sameCat(c, cat))) : podcasts;
    const chip = (val, label, key) => `<div class="chip f${sameCat(val, cat) ? " sel" : ""}" tabindex="-1" data-k="${key}" data-action="pcat" data-cat="${esc(val)}">${esc(label)}</div>`;
    const bar = `<section class="row bar"><div class="strip">${chip("", t("allPodcasts"), "pc-all")}${cats.map((c, i) => chip(c, catName(c), `pc-${i}`)).join("")}</div></section>`;
    const grid = shown.length ? `<div class="grid library-grid">${shown.map((p) => podcastCard(p, `pod-${p.id}`)).join("")}</div>`
      : `<div class="hint">${t("nothingFound")}</div>`;
    view.innerHTML = `<div class="grid-page">${kicker(partName("podcasts"))}<h1>${esc(cat ? catName(cat) : t("allPodcasts"))} <span class="count">${shown.length}</span></h1>${bar}${grid}</div>`;
    // in the order of their names, to be found; the focus on the one heard last (else the first), not on the bar
    const last = shown.find((p) => p.id === heard[0]) ?? shown.find((p) => heard.includes(p.id));
    (view.querySelector(`.grid [data-k="pod-${CSS.escape(last?.id ?? "")}"]`) ?? view.querySelector(".grid .card"))?.setAttribute("data-autofocus", "");
  },

  async podsearch({ q = "" }) {
    view.innerHTML = searchPage();
    showQuery(q);
    await runPodSearch(q);
  },

  // A podcast: its picture, who makes it, what it is about, the newest episode, then all its episodes
  async podcast({ id }) {
    const { podcast: p, episodes } = await pf(`/podcast/${encodeURIComponent(id)}`);
    const begun = episodes.find((e) => e.pos > 0 && !e.finished);
    const facts = [p.author && `<b>${esc(p.author)}</b>`, esc(episodes.length === 1 ? t("episodeOne") : t("episodesN", { n: episodes.length })),
      p.categories?.length && esc(p.categories.slice(0, 3).map(catName).join(", "))].filter(Boolean);
    const actions = (begun ? `<button class="btn f" data-k="presume" data-action="pplay" data-id="${esc(begun.id)}">${ICON_PLAY}${esc(t("resumeEpisode"))}</button>` : "")
      + (episodes[0] && episodes[0] !== begun ? `<button class="btn f" data-k="pnewest" data-action="pplay" data-id="${esc(episodes[0].id)}"${episodes[0].finished ? ' data-at="0"' : ""}>${begun ? "" : ICON_PLAY}${t("newestEpisode")}</button>` : "")
      + `<button class="btn f" data-k="punfollow" data-action="punfollow" data-id="${esc(p.id)}" data-name="${esc(p.name)}">${t("unfollow")}</button>`;
    const about = plainText(p.about);
    const row = (e, i) => {
      const left = e.pos > 0 && !e.finished && e.duration ? t("timeLeft", { time: duration((e.duration - e.pos) * TICKS) }) : "";
      return `<div class="track f${e.finished ? " heard" : ""}" tabindex="-1" data-k="ep-${i}" data-r="${reg.push(episodeOf({ ...e, podcast: p.id, show: p.name })) - 1}" data-action="pplay" data-id="${esc(e.id)}"${e.finished ? ' data-at="0"' : ""}>
        <span class="no">${esc(calendarDay(e.date))}</span><span class="t">${esc(e.title)}</span>
        <span class="d">${e.finished ? "✓" : esc(left || (e.duration ? clock(e.duration * TICKS) : ""))}</span></div>`;
    };
    view.innerHTML = `<div class="detail music-detail moving podcast-detail"><section class="hero with-poster">
        <div class="poster sq">${cardImg(p.cover ? podCover(p.id) : null, p.name)}</div>
        <div class="info">${kicker(t("kindPodcast"))}<h1 class="title">${esc(p.name)}</h1><div class="meta">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
          ${about ? `<div class="overview">${esc(about)}</div>` : ""}<div class="actions">${actions}</div></div></section>
      ${episodes.length ? `<section class="tracks"><h2>${t("episodes")}</h2>${episodes.map(row).join("")}</section>` : `<div class="hint">${t("pfNoEpisodesYet")}</div>`}</div>`;
    if (p.cover) setBackdrop(podCover(p.id));
  },
});

// The search of the podcasts: their names and who makes them, and the titles of the newest episodes
async function runPodSearch(q) {
  const box = $("#results");
  if (!box) return;
  const term = q.trim().toLocaleLowerCase(LANG), stale = () => current.name !== "podsearch" || (current.args?.q || "") !== q;
  if (term.length < 2) {
    if (!term) return searchSuggestions(box, t("podHint"));
    box.innerHTML = `<div class="hint">${t("oneMoreLetter")}</div>`;
    return;
  }
  const [r, recent] = await Promise.all([getJson("/pf/podcasts"), getJson("/pf/recent")]);
  if (stale()) return; // typed on meanwhile
  const has = (...texts) => texts.some((x) => (x ?? "").toLocaleLowerCase(LANG).includes(term));
  const pods = (r?.podcasts ?? []).filter((p) => has(p.name, p.author));
  const eps = (recent?.latest ?? []).filter((e) => has(e.title));
  // each kind under its heading, as every other search
  const part = (title, cards) => (cards.length ? `<h2 class="results-title">${title}</h2><div class="grid">${cards.join("")}</div>` : "");
  box.innerHTML = pods.length || eps.length ? part(partName("podcasts"), pods.map((p) => podcastCard(p, `found-${p.id}`)))
    + part(t("episodes"), eps.map((e) => episodeCard(e, `found-ep-${e.id}`)))
    : nothingFoundHere(q.trim());
  // then new ones to follow, from the podcast directory through Podfetch: once typing pauses, as it takes a second
  clearTimeout(podFindTimer);
  if (term.length < 3) return;
  podFindTimer = setTimeout(async () => {
    const found = await getJson(`/pf/find?q=${encodeURIComponent(q.trim())}`);
    const fresh = (found?.podcasts ?? []).filter((p) => !p.following);
    if (stale() || !fresh.length) return;
    const html = `<h2 class="results-title">${t("pfToFollow")}</h2><div class="grid">${fresh.map((p) => tile({ item: { ...p, kind: "newpodcast" },
      key: `new-${p.id}`, action: "pfollow", shape: "sq", art: cardImg(p.art, p.name), label: p.name, sub: p.author || p.genre || null })).join("")}</div>`;
    if (box.querySelector(":scope > .grid")) box.insertAdjacentHTML("beforeend", html);
    else box.innerHTML = html; // in place of "nothing found"
  }, 500);
}
let podFindTimer;

// Follow a podcast found in the podcast directory, asked first; Podfetch adds it and fetches its episodes, its page opens
function podFollow(el, p) {
  const run = async () => {
    toast(t("pfFollowing"), 15000);
    forgetPods();
    const r = await fetch("/pf/follow", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: p.id }) }).catch(() => null);
    const data = await r?.json().catch(() => ({}));
    if (!r?.ok) return toast(t(`err_${data?.error || "pf_unreachable"}`), 5000);
    shelfCache.delete("podcasts");
    toast(t("following", { name: p.name }));
    if (data.id) navigate("podcast", { id: data.id });
  };
  showOptions(el, p.name, [{ label: t("follow"), run }, { label: t("cancel") }]);
}

async function podAction(a, el, it) {
  if (a === "ppodcast") navigate("podcast", { id: it.id });
  else if (a === "pplay") podPlay(el.dataset.id ?? it.id, el.dataset.at != null ? Number(el.dataset.at) : undefined);
  else if (a === "punfollow") podUnfollow(el);
  else if (a === "pfollow") podFollow(el, it);
  else if (a === "pcat") {
    const cat = el.dataset.cat ?? it?.cat ?? "";
    if (current.name !== "podcat") return navigate("podcat", { cat });
    current.args = { ...current.args, cat };
    render(current, el.dataset.k);
  }
}

// An episode marked as heard (Podfetch is told its whole length) or as not heard (from the start again)
async function podHeard(el, ep, on) {
  forgetPods();
  const r = await fetch("/pf/finished", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: ep.id, on, duration: ep.duration }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  shelfCache.delete("podcasts");
  shelfCache.delete("next");
  toast(t(on ? "markedHeard" : "markedUnheard"));
  render(current, el?.dataset.k);
}

// Unfollow a podcast, asked first: Podfetch forgets it, its episodes and where they were heard; what it downloaded
// stays on its disk or goes too. Keeping them is offered first, as nothing of it can be undone.
// From its page (then back from it) or, OK held, from its tile (the page stays, without it)
function podUnfollow(el, { id, name } = el.dataset) {
  const run = (files) => async () => {
    forgetPods();
    const r = await fetch("/pf/unfollow", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id, files }) }).catch(() => null);
    const data = await r?.json().catch(() => ({}));
    if (!r?.ok) return toast(t(`err_${data?.error || "pf_unreachable"}`), 5000);
    shelfCache.delete("podcasts");
    shelfCache.delete("next");
    toast(t("unfollowed", { name }));
    if (current.name === "podcast") back();
    else render(current);
  };
  showOptions(el, t("pfUnfollowAsk", { name }), [{ label: t("pfUnfollowKeep"), run: run(false) },
    { label: t("pfUnfollowDelete"), run: run(true) }, { label: t("cancel") }]);
}

// An episode plays in the music's player, as a book does: in the background, faster or slower, with the sleep timer
async function podPlay(id, at) {
  forgetPods();
  const r = await fetch("/pf/play", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id, at }) }).catch(() => null);
  if (!r?.ok) return toast(t("playFailed", { error: r?.status ?? "–" }), 5000);
  shelfCache.delete("podcasts");
  shelfCache.delete("next");
  musicState = { active: true, index: 0, queue: [], paused: false };
  await musicPoll();
  if (current.name !== "nowplaying") navigate("nowplaying", {});
}
