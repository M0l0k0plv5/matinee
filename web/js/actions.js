"use strict";
// Matinee's page, part 15 of 18: what OK does: the buttons of a title, the parts, the sign-in.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

// Begun but not finished: a movie stopped halfway, a show or collection with some of it seen
const begun = (it) => !it.UserData?.Played && ((it.UserData?.PlaybackPositionTicks || 0) > 0 || progressOf(it) > 0);

// Toggle button: shows the action it performs. Something begun can go either way: the button opens a choice of
// both (two buttons would not fit in one row); unwatched also takes back its progress.
function watchedButton(it, all = false) {
  const played = !!it.UserData?.Played;
  if (begun(it)) return `<button class="btn f" data-k="watched" data-action="watchedmenu" data-id="${esc(it.Id)}" data-name="${esc(it.Name)}" data-all="${all ? 1 : ""}">${ICON_CHECK}${t("watchedStatus")}</button>`;
  const label = t(all ? (played ? "markAllUnwatched" : "markAllWatched") : (played ? "markUnwatched" : "markWatched"));
  return `<button class="btn f" data-k="watched" data-action="watched" data-id="${esc(it.Id)}" data-played="${played ? 1 : 0}">${ICON_CHECK}${label}</button>`;
}

function watchedChoice(el) {
  const all = !!el.dataset.all, id = el.dataset.id;
  showOptions(el, el.dataset.name, [
    { label: t(all ? "markAllWatched" : "markWatched"), run: () => setPlayed(id, true) },
    { label: t(all ? "markAllUnwatched" : "markUnwatched"), run: () => setPlayed(id, false) },
  ]);
}

// Trailer: one in the library (Jellyfin's own)
function trailerButton(it) {
  if (!it.LocalTrailerCount) return "";
  return `<button class="btn f" data-k="trailer" data-action="trailer" data-id="${esc(it.Id)}">${ICON_PLAY}${t("trailer")}</button>`;
}

async function playTrailer(el) {
  const local = await fetchJSON(`/Items/${el.dataset.id}/LocalTrailers?userId=${USER}`);
  if (local?.length) play(local[0].Id, false);
}

function favoriteButton(it) {
  const on = !!it.UserData?.IsFavorite;
  return `<button class="btn f" data-k="favorite" data-action="favorite" data-id="${esc(it.Id)}" data-on="${on ? 1 : 0}">${on ? ICON_HEART : ICON_HEART_OPEN}${t(on ? "inMyList" : "addToMyList")}</button>`;
}

// "My List": Jellyfin's favorites, with OK held on a tile or the button on its page
async function setFavorite(id, on) {
  const r = await fetch("/favorite", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ itemId: id, on }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  libCache.forEach((e) => {
    e.stale = true;
    for (const it of e.items) if (it.Id === id) it.UserData = { ...it.UserData, IsFavorite: on };
  });
  forgetHome();
  toast(t(on ? "addedToMyList" : "removedFromMyList"));
  refresh();
}


// Subtitles found for a movie or an episode (views.subsearch): Jellyfin downloads them and keeps them with the video
async function getSubtitle(el) {
  if (el.dataset.busy) return;
  el.dataset.busy = "1";
  const r = await fetch("/subtitles/get", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: el.dataset.id, subtitle: el.dataset.sub }) })
    .catch(() => null); // the backend away for a moment: said, and the line can be taken again
  delete el.dataset.busy;
  if (!r?.ok) return toast(t("subsGetFailed"), 5000);
  toast(t("subsAdded"), 5000);
  back();
}

async function setPlayed(id, played) {
  const r = await fetch("/played", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ itemId: id, played }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  // show it at once in kept libraries, the fresh list from the server follows
  libCache.forEach((e) => {
    e.stale = true;
    for (const it of e.items) if (it.Id === id) it.UserData = { ...it.UserData, Played: played, PlaybackPositionTicks: 0, PlayedPercentage: played ? 100 : 0, ...(played && { UnplayedItemCount: 0 }) };
  });
  shelfCache.clear(); // Up Next and what is being watched change with it
  forgetHome();
  toast(played ? t("markedWatched") : t("markedUnwatched"));
  refresh();
}

function refresh() {
  current.offsets = keepOffsets();
  render(current, document.activeElement?.dataset?.k, view.scrollTop);
}

async function activate(el) {
  el.classList.add("pressed"); // gives way for a moment (style.css)
  setTimeout(() => el.classList.remove("pressed"), 140);
  const a = el.dataset.action;
  const it = reg[el.dataset.r];
  // OK on a tile of the page just left, while the next is being built: its item is gone, the press goes nowhere
  if (el.dataset.r !== undefined && !it) return;
  if (a === "open") navigate("item", { id: it.Id, type: it.Type });
  else if (a === "section") openSection(it.Id);
  else if (a === "nowbar") navigate("nowplaying", {});
  else if (a === "tab") switchSection(el.dataset.val);
  else if (a === "partsearch") navigate(PART_SEARCH[el.dataset.val], { q: "" });
  else if (a === "settingstile") navigate("settings", { part: settingsPart() });
  else if (a === "controls") navigate("controls");
  else if (a === "legal") navigate("legal");
  else if (a === "support") navigate("support");
  else if (a === "supportsend") supportSend(el);
  else if (a === "welcomenext") welcomeNext(el.dataset.val);
  else if (a === "localsetup") localSetup();
  else if (a === "share") navigate("share");
  else if (a === "diagnose") navigate("diagnose");
  else if (a === "welcomelang") welcomeLang(el.dataset.val);
  else if (a === "welcomedone") welcomeDone();
  else if (a === "welcome") navigate("welcome", { step: "lang" });
  else if (a === "simple") navigate("pin", { mode: el.dataset.val });
  else if (a === "pinkey") pinKey(el.dataset.val);
  else if (a === "tvtoggle") toggleTvChannel(el);
  else if (a === "update") softwareUpdate(el);
  else if (a === "updatenotes") openUpdateNotes(el);
  else if (a === "missing") toast(t("notInLibraryHint"), 4000);
  else if (a === "person") navigate("person", { id: el.dataset.id, name: el.dataset.name });
  else if (a === "searchall") navigate("allsearch", { q: (current.args?.q || "").trim() });
  else if (a === "allmore") navigate(PART_SEARCH[el.dataset.val], { q: (current.args?.q || "").trim() });
  else if (a === "tvlive") startPlayback("/tv/play", { live: el.dataset.id });
  else if (a === "sublangopt") { current.args = { ...current.args, lang: el.dataset.val }; render(current, el.dataset.k); }
  else if (a === "subget") getSubtitle(el);
  else if (a === "ownchannels") navigate("ownchannels");
  else if (a === "ownguide") navigate("guide");
  else if (a === "guidecell") playGuide(el);
  else if (a === "settingspart") navigate("settings", { part: el.dataset.val });
  else if (a === "svcfield") navigate("svcentry", { svc: el.dataset.svc, field: el.dataset.field, q: svcDraft[el.dataset.svc][el.dataset.field] ?? "" });
  else if (a === "svcdone") svcDone();
  else if (a === "svcconnect") svcConnect(el.dataset.svc);
  else if (a === "everywhere") signInEverywhere();
  else if (a === "svcremove") svcRemove(el.dataset.svc);
  else if (a === "choose") chooseSetting(el);
  else if (a === "libcheck") current.name === "libcheck" ? render(current, "lc-again") : navigate("libcheck", {});
  else if (a === "libfix") lcAsk(el);
  else if (a === "libfixall") lcAskAll(el);
  else if (a === "volumeopen") openVolume();
  else if (a === "system") systemAction(el, el.dataset.val);
  else if (a === "volume") changeVolume(Number(el.dataset.val));
  else if (a === "bluetooth") navigate("bluetooth", {});
  else if (a === "wifi") navigate("wifi", {});
  else if (a === "hdmi") navigate("hdmi", {});
  else if (a === "hdmiadd") hdmiAdd(el);
  else if (a === "hdmiinput") hdmiInput(el);
  else if (a === "wifiscan") { toast(t("wifiLooking"), 3000); wifiRefresh(true); }
  else if (a === "wifinet") wifiPick(el);
  else if (a === "jfpick") { svcDraft.jellyfin.server = el.dataset.url; render(current, "jf-username"); }
  else if (a === "transferout") navigate("transferout", {});
  else if (a === "transferin") navigate("transferin", {});
  else if (a === "transferlook") { $("#tr-msg").textContent = t("transferLooking"); transferLook(); }
  else if (a === "transferpick") { svcDraft.transfer.ip = el.dataset.ip; navigate("svcentry", { svc: "transfer", field: "code", q: "" }); }
  else if (a === "transferend") back();
  else if (a === "signout") signOut(el);
  else if (a === "unhide") unhideAll();
  else if (a === "wxplace") navigate("weatherplace", { q: "" });
  else if (a === "wxpick") pickPlace(places[Number(el.dataset.i)]);
  else if (a === "btscan") btScan();
  // a remote, keyboard or controller is how Matinee is used: connected, it is not disconnected or forgotten from here;
  // one not connected (a controller switched off and on again that did not come back by itself) is connected
  else if (a === "bt" && el.dataset.paired === "1" && el.dataset.audio !== "1" && el.dataset.on === "1") toast(t("btKeep", { name: el.dataset.name }), 4000);
  else if (a === "bt") btAction(el.dataset.mac, el.dataset.on === "1" ? "disconnect" : "connect", el.dataset.name, el.dataset.paired === "1");
  else if (a === "library") navigate("library", { id: it.Id, name: it.Name, type: it.CollectionType });
  else if (a === "type") typeKey(el.dataset.val);
  else if (a === "libopt") {
    const v = el.dataset.opt === "unplayed" ? !!el.dataset.val : el.dataset.val;
    current.args = { ...current.args, [el.dataset.opt]: v };
    render(current, el.dataset.k);
  }
  else if (a === "goto") navigate("item", { id: el.dataset.id });
  else if (a === "season") { clearTimeout(seasonTimer); loadEpisodes(view.dataset.series, el.dataset.id); }
  else if (a === "play") play(el.dataset.id ?? it.Id, el.dataset.resume !== "0");
  else if (a === "watched") setPlayed(el.dataset.id, el.dataset.played !== "1");
  else if (a === "watchedmenu") watchedChoice(el);
  else if (a === "favorite") setFavorite(el.dataset.id, el.dataset.on !== "1");
  else if (a === "trailer") playTrailer(el);
  else if (a === "overview") openReader(el);
  else if (a === "retry") retryRender?.();
  else if (BOOK_ACTIONS.has(a)) bookAction(a, el, it);
  else if (POD_ACTIONS.has(a)) podAction(a, el, it);
  else musicAction(a, el, it);
}

// The tab bar: at the top of each part's start the parts side by side, up from the first row (and with Back).
// OK on one changes to it in place (Back from the bar still leads to the main menu), with the focus staying on it.
// While music plays, the bar has a tab more: Now Playing, reached from any start with up or Back
const NOW_TAB = () => `<div class="tab f now" tabindex="-1" data-k="tab-nowplaying" data-action="nowbar">${esc(t("nowPlaying"))}</div>`;
function syncNowTab() {
  const nav = $(".tabbar");
  if (!nav) return;
  const tab = nav.querySelector("[data-k=tab-nowplaying]");
  if (musicState.active && !tab) nav.querySelector("[data-k=tab-settings]")?.insertAdjacentHTML("beforebegin", NOW_TAB());
  if (!musicState.active && !musicState.unknown && tab) {
    if (document.activeElement === tab) focusEl(nav.querySelector(".tab.sel") ?? nav.querySelector(".tab"));
    tab.remove();
  }
}

// the apps set up here, in the main menu's order: the tabs on their starts, ⌘1 … ⌘9
async function partsHere() {
  const [nd, ab, pfs] = await Promise.all([musicSession(), bookSession(), podSession()]);
  return ["jellyfin", ...(nd.configured ? ["music"] : []), ...(ab.configured ? ["books"] : []), ...(pfs.configured ? ["podcasts"] : [])];
}
async function tabBar(here) {
  const parts = (await partsHere()).map((id) => [id, partName(id)]);
  // its search at the left end, its settings at the right, the same on every start however long its rows are
  return `<nav class="tabbar"><div class="tab icon f" tabindex="-1" data-k="tab-search" data-action="partsearch" data-val="${here}" aria-label="${esc(t("search"))}">${ICON_SEARCH}</div>${parts.map(([id, name]) =>
    `<div class="tab f${id === here ? " sel" : ""}" tabindex="-1" data-k="tab-${id}" data-action="tab" data-val="${id}">${esc(name)}</div>`).join("")}${musicState.active ? NOW_TAB() : ""}<div class="tab icon f" tabindex="-1" data-k="tab-settings" data-action="settingstile" aria-label="${esc(t("settings"))}">${ICON_SETTINGS}</div></nav>`;
}
// ⌘1 … ⌘9 (Ctrl on a PC's keyboard): the n-th app's start from any page, as if opened from the main menu (Back
// goes to the menu, on its tile)
async function openPart(n) {
  const id = (await partsHere())[n - 1];
  if (!id || !pageFree()) return;
  stack = [{ name: "menu", focusKey: `menu-${id}` }];
  lastSection = null; // there even from further down in that app
  switchSection(id);
}
function switchSection(id) {
  if (id === lastSection) return;
  lastSection = id;
  fetch("/section", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id }) }).catch(() => {});
  const name = { music: "music", books: "books", podcasts: "podcasts" }[id] ?? "home";
  render({ name, args: {} }, `tab-${id}`);
}

function openSection(id) {
  if (id === "settings") return navigate("settings", {});
  if (id === "search") return navigate("allsearch", { q: "" });
  if (id?.startsWith("hdmi")) return hdmiGo(hdmiTiles[+id.slice(4)]);
  if (id === "next") { // its titles are the shelf above it: OK goes to the first
    const first = $("#shelf .card");
    if (first) focusEl(first);
    return;
  }
  lastSection = id;
  fetch("/section", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id }) }).catch(() => {});
  if (id === "music") navigate("music");
  else if (id === "books") navigate("books");
  else if (id === "podcasts") navigate("podcasts");
  else navigate("home");
}

// Jellyfin may not be set up yet (a fresh install), not signed in (signed out by the server) or not reachable (at boot
// the network is often missing): say why, with the server, user name and password to sign in right here, and carry
// on by itself once the backend is signed in
async function connect() {
  const gen = renderGen;
  let shown = null;
  for (;;) {
    let msg, saved = {};
    try {
      const r = await fetch("/session");
      const d = await r.json();
      if (r.ok) { USER = d.userId; return; }
      msg = t(d.network === false ? "err_no_network" : `err_${d.error}`, { detail: d.detail });
      saved = d;
    } catch {
      msg = t("waitingBackend");
    }
    if (gen !== renderGen) throw STALE;
    // waiting for its own backend, or switched on for the first time (no server yet): Matinee's logo beside it; a server
    // that does not answer or signed it out: Jellyfin's
    const side = !saved.error || saved.error === "setup" ? KINO_LOGO : ICON_JELLYFIN;
    const box = $(".jf-signin")?.parentElement.querySelector(".settings-icon");
    if (box && box.dataset.side !== side) { box.innerHTML = box.base = side; box.dataset.side = side; delete box.dataset.icon; }
    if (shown === null) {
      view.innerHTML = `${settingsSide(side)}<div class="grid-page settings jf-signin"><h1>${esc(partName("jellyfin"))}</h1>
        <p class="jf-msg" id="jf-msg"></p>${jfForm(saved)}<div id="jf-found"></div>${saved.error === "setup"
          ? `<div class="slist">${srow("jf-transfer", t("transferIn"), "", `data-action="transferin"`, t("transferInHint"))}</div>` : ""}</div>`;
      $(".settings-icon").dataset.side = side;
      jfFound(gen);
      clearTimeout(busyTimer);
      document.body.classList.remove("busy");
      // back from typing a field: on that line again (the page itself is still waiting, so not by render's focus)
      focusEl(view.querySelector(`[data-k="${CSS.escape(current.focusKey ?? "")}"]`) ?? view.querySelector(".srow"), false);
    }
    if (msg !== shown) $("#jf-msg").textContent = shown = msg;
    await new Promise((res) => setTimeout(res, 2000));
    if (gen !== renderGen) throw STALE;
  }
}

// Below the sign-in: the Jellyfin servers found on the network (OK puts one's address in), and Wi-Fi where Matinee can
// set it up (a TV computer without a network yet). Looked for once a page, after it is there: a second or two.
async function jfFound(gen) {
  const [found, wifi, local] = await Promise.all([getJson("/jellyfin/discover"), getJson("/wifi"), getJson("/jellyfin/local")]);
  const box = $("#jf-found");
  if (gen !== renderGen || !box) return;
  // Jellyfin on this computer, not set up yet (deploy/server.sh): made ready from here, with its first user
  const d = svcDraft.local;
  const mine = local?.there && local.fresh ? `<h2 class="results-title">${t("localTitle")}</h2><div class="slist">${
    srow("local-username", t("user"), esc(d.username || t("svcEnter")), 'data-action="svcfield" data-svc="local" data-field="username"', t("localUserHint"))
    + srow("local-password", t("password"), esc(d.password ? "•".repeat(Math.min(d.password.length, 12)) : t("svcEnter")),
      'data-action="svcfield" data-svc="local" data-field="password"', t("localPasswordHint"))
    + srow("local-create", t("localCreate"), "", 'data-action="localsetup"', t("localCreateHint"))}</div>` : "";
  const servers = (found?.servers ?? []).filter((x) => x.address !== svcDraft.jellyfin.server);
  box.innerHTML = mine + (servers.length ? `<h2 class="results-title">${t("jfFound")}</h2><div class="slist">${servers.map((x, i) =>
    srow(`jf-found-${i}`, x.name, esc(x.address), `data-action="jfpick" data-url="${esc(x.address)}"`, t("jfFoundHint"))).join("")}</div>` : "")
    + (wifi?.available ? `<div class="slist">${srow("jf-wifi", t("wifi"), esc(wifi.networks.find((n) => n.active)?.ssid ?? t("wifiNotConnected")),
      `data-action="wifi"`, t("wifiHint"))}</div>` : "");
}

// A line as on the settings' pages
const srow = (key, name, value, attrs, hint = "") =>
  `<div class="srow f" tabindex="-1" data-k="${esc(key)}" ${attrs} data-hint="${esc(hint)}"><span class="sname">${esc(name)}</span><span class="sval">${value}</span></div>`;

// Jellyfin's server, user name and password, and Sign In: on the sign-in page and in Jellyfin's settings (signedIn),
// with the same words and the state on the right as music's and the audiobooks'
function jfForm(saved = {}, signedIn = false) {
  const d = svcDraft.jellyfin;
  d.server ??= saved.server ?? "";
  d.username ??= saved.username ?? "";
  const shown = (f) => (f === "password" ? (d.password ? "•".repeat(Math.min(d.password.length, 12)) : t(signedIn ? "svcSignedIn" : "svcEnter"))
    : d[f] || t("svcEnter"));
  const name = { server: t("server"), username: t("user"), password: t("password") };
  return `<div class="slist">${["server", "username", "password"].map((f) =>
    srow(`jf-${f}`, name[f], esc(shown(f)), `data-action="svcfield" data-svc="jellyfin" data-field="${f}"`, t(SVC_HINTS[f]))).join("")}
    ${srow("jf-signin", t("signIn"), signedIn ? esc(t("svcConnected")) : "", `data-action="svcconnect" data-svc="jellyfin"`, t("jfSignInHint"))}</div>`;
}
