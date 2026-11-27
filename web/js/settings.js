"use strict";
// Matinee's page, part 4 of 18: the settings' parts, the services set up from them, the account and Bluetooth.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

// ---------- Settings ----------

let places = [];
async function runPlaceSearch(q) {
  const box = $("#results");
  if (!box) return;
  const term = q.trim(), stale = () => current.name !== "weatherplace" || (current.args?.q || "") !== q;
  if (term.length < 2) {
    box.innerHTML = `<div class="hint">${term ? t("oneMoreLetter") : t("wxHint")}</div>`;
    return;
  }
  const r = await fetch(`/weather/places?q=${encodeURIComponent(term)}`).then((x) => (x.ok ? x.json() : null)).catch(() => null);
  if (stale()) return; // typed on meanwhile
  places = r?.places ?? [];
  box.innerHTML = !r ? `<div class="hint">${t("wxFailed")}</div>`
    : !places.length ? `<div class="hint">${t("nothingFoundFor", { term: esc(term) })}</div>`
    : `<section class="tracks">${places.map((p, i) => `<div class="track f" tabindex="-1" data-k="place-${i}" data-action="wxpick" data-i="${i}">
        <span class="t">${esc(p.name)}<small>${esc(p.region)}</small></span></div>`).join("")}</section>`;
}

async function pickPlace(p) {
  const r = await fetch("/settings", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ weather: p }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  toast(t("wxSaved", { place: p.name }));
  back();
}

// ---------- Music, audiobooks and podcasts from the settings ----------

// What is typed for Navidrome, Audiobookshelf and Podfetch before Sign In, by service; the password or key only here,
// until sent
const svcDraft = { navidrome: {}, audiobookshelf: {}, podfetch: {}, jellyfin: {}, transfer: {}, everywhere: {}, wifi: {}, hdmi: {}, local: {} };
// each service's part of Matinee, its symbol, and whether it is there (with its sign-in) and its fields
const SVC_PART = { navidrome: "music", audiobookshelf: "books", podfetch: "podcasts" };
const svcIcon = (svc) => ({ navidrome: ICON_MUSIC, audiobookshelf: ICON_BOOKS, podfetch: ICON_PODCASTS }[svc]);
const svcSession = (svc, fresh) => (svc === "navidrome" ? musicSession(fresh) : svc === "podfetch" ? podSession(fresh) : bookSession(fresh));
const SVC_FIELDS = { navidrome: ["server", "username", "password"], audiobookshelf: ["server", "key"], podfetch: ["server", "username", "password"] };
const SVC_MARKS = [...".:/-_@!#&"]; // what server addresses and passwords need besides letters and digits
const SVC_HINTS = { server: "svcServerHint", username: "svcUserHint", password: "svcPasswordHint", key: "svcKeyHint" };
const SVC_SECRET = new Set(["password", "key"]);
// what a field is for, said beside it while typing (the account's move: Jellyfin's password, the other device's code)
const svcHint = (svc, field) => (svc === "transfer" ? (field === "code" ? "transferCodeHint" : "transferPasswordHint")
  : svc === "wifi" ? "wifiPasswordHint" : svc === "hdmi" ? "hdmiNameHint"
  : svc === "local" ? (field === "password" ? "localPasswordHint" : "localUserHint")
  : svc === "everywhere" && field === "server" ? "evServerHint" : SVC_HINTS[field]);
const serviceState = (sess) => t(!sess.configured ? "svcNotSet" : sess.ready ? "svcConnected" : `err_${sess.error || "svc_unknown"}`);

function serviceForm(svc, saved, sess, line, heading = true) {
  const d = svcDraft[svc];
  d.server ??= saved?.server ?? "";
  if (svc !== "audiobookshelf") d.username ??= saved?.username ?? "";
  const fields = SVC_FIELDS[svc];
  const shown = (f) => (SVC_SECRET.has(f) ? (d[f] ? "•".repeat(Math.min(d[f].length, 12)) : t(sess.ready && (svc !== "podfetch" || saved?.username) ? "svcSignedIn" : "svcEnter"))
    : d[f] || t("svcEnter"));
  const name = { server: t("server"), username: t("user"), password: t("password"), key: t("apiKey") };
  const hint = (f) => t(svc === "podfetch" && f !== "server" ? "svcPfUserHint" : SVC_HINTS[f]); // Podfetch may ask for none
  const rows = fields.map((f) => line(`svc-${f}`, name[f], esc(shown(f)), `data-action="svcfield" data-svc="${svc}" data-field="${f}"`, hint(f)))
    .join("")
    + line("svc-connect", t("signIn"), esc(serviceState(sess)), `data-action="svcconnect" data-svc="${svc}"`, t("svcConnectHint"))
    + (sess.configured ? line("svc-remove", t("svcRemove"), "", `data-action="svcremove" data-svc="${svc}"`, t("svcRemoveHint")) : "");
  const title = partName(SVC_PART[svc]);
  return `${heading ? `<h2 class="settings-part">${esc(sess.configured ? t("svcAccount") : title)}</h2>` : ""}<div class="slist">${rows}</div>`;
}

// Music, the audiobooks or the podcasts not set up, not signed in or not reachable: the same page as Jellyfin's sign-in,
// right there instead of a message (error: why); signed in, the start comes in its place (svcConnect renders it anew)
async function serviceSignIn(svc, error) {
  // set up, only not answering just now (asleep, restarting, the network away): it heals by itself
  if (/unreachable$/.test(error)) throw new Error(t(`err_${error}`));
  const [s, sess] = await Promise.all([getJson("/settings"), svcSession(svc, true)]);
  const saved = svc === "navidrome" ? s?.navidrome : svc === "podfetch" ? s?.podfetch : s?.audiobookshelf && { server: s.audiobookshelf };
  const part = SVC_PART[svc];
  const how = t(/unreachable$/.test(error) ? "svcCheckBelow" : "svcSignInBelow");
  view.innerHTML = `${settingsSide(svcIcon(svc))}<div class="grid-page settings jf-signin"><h1>${esc(partName(part))}</h1>
    <p class="jf-msg" id="jf-msg">${esc(`${t(`err_${error}`)} ${how}`)}</p>${serviceForm(svc, saved, sess, srow, false)}</div>`;
}

function svcDone() {
  const { svc, field, q = "" } = current.args;
  svcDraft[svc][field] = field === "server" || field === "username" || field === "code" ? q.trim() : q;
  const shown = back();
  // only then: a page still coming in would be written over the one with the code
  if (svc === "transfer") Promise.resolve(shown).then(() => transferNext(field));
  if (svc === "wifi") Promise.resolve(shown).then(() => wifiJoin(svcDraft.wifi.ssid, svcDraft.wifi.password));
  if (svc === "hdmi") Promise.resolve(shown).then(() => hdmiNamed(svcDraft.hdmi));
}

/* ---------- The account: to another device, or off this one ---------- */

let trTimer = null;
const post = (url, body = {}) => fetch(url, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) })
  .then((r) => r.json().then((d) => (r.ok ? d : { error: d?.error || "service_unreachable" }))).catch(() => null);
const clockTime = (sec) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;

// Matinee starts again with the new state of things: the page once the new backend answers (its secret is new)
function reloadWhenBack() {
  const again = () => fetch("/alive", { method: "POST", headers: JSON_HEADERS, body: "{}" })
    .then((r) => (r.status === 403 ? location.reload() : setTimeout(again, 1000))).catch(() => setTimeout(again, 1000));
  setTimeout(again, 2500);
}

async function transferLook() {
  const r = await post("/transfer/devices");
  const list = $("#tr-list"), msg = $("#tr-msg");
  if (!list || !msg) return;
  const devices = r?.devices ?? [];
  msg.textContent = !r || r.error ? t(`err_${r?.error || "service_unreachable"}`) : t(devices.length ? "transferPick" : "transferNone");
  list.innerHTML = `<div class="slist">${devices.map((d, i) => srow(`tr-${i}`, d.name, "", `data-action="transferpick" data-ip="${esc(d.ip)}" data-name="${esc(d.name)}"`,
    t("transferCodeHint"))).join("")}${srow("tr-again", t("transferAgain"), "", `data-action="transferlook"`, t("transferOutHint"))}</div>`;
  if (!view.contains(document.activeElement) || !document.activeElement.classList.contains("f")) focusEl(list.querySelector(".srow"), false);
}

// The code the other device shows, then (with Jellyfin signed in) Jellyfin's password to sign it in, then sent
async function transferNext(field) {
  if (field === "code") {
    if (!/^\d{6}$/.test(svcDraft.transfer.code ?? "")) return toast(t("err_transfer_code"), 4000);
    const s = await getJson("/settings");
    if (s?.jellyfinReady) return navigate("svcentry", { svc: "transfer", field: "password", q: "" });
  }
  const { ip, code, password } = svcDraft.transfer;
  delete svcDraft.transfer.code;
  delete svcDraft.transfer.password;
  toast(t("transferSending"), 20000);
  const r = await post("/transfer/send", { ip, code, ...(password == null ? {} : { password }) });
  if (!r || r.error) return toast(t(`err_${r?.error || "transfer_unreachable"}`), 5000);
  toast(t("transferDone", { name: r.name }), 5000);
  if (current.name === "transferout") render(current);
}

function signOut(el) {
  showOptions(el, t("signOutAsk"), [{ label: t("signOut"), run: async () => {
    const r = await post("/signout");
    if (!r || r.error) return toast(t("saveFailed"), 4000);
    forgetShelves();
    try { sessionStorage.removeItem("kino-everywhere"); } catch {} // whose sign-in it was: theirs no more
    toast(t("signedOut"), 30000);
    reloadWhenBack();
  } }, { label: t("cancel") }]);
}

async function svcSend(svc, body) {
  const r = await fetch("/service", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ service: svc, ...body }) }).catch(() => null);
  const data = await r?.json().catch(() => ({}));
  if (!r?.ok) return toast(t(`err_${data?.error || "service_unreachable"}`), 5000), false;
  delete svcDraft[svc].password;
  delete svcDraft[svc].key;
  await Promise.all([musicSession(true), bookSession(true), podSession(true)]);
  shelfCache.clear();
  forgetShelves();
  return true;
}

async function jfSignIn() {
  const d = svcDraft.jellyfin;
  if (!d.server) return toast(t("svcNeedServer"), 4000);
  if (!d.username) return toast(t("svcNeedUser"), 4000);
  toast(t("svcConnecting"), 10000);
  const r = await fetch("/jellyfin/setup", { method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ server: d.server, username: d.username, password: d.password ?? "" }) }).catch(() => null);
  const data = await r?.json().catch(() => ({}));
  if (!r?.ok) return toast(t(`err_${data?.error || "service_unreachable"}`), 5000);
  delete d.password;
  forgetShelves();
  toast(t("svcDone", { name: partName("jellyfin") }));
  if (current.name === "settings") { // perhaps another server: everything built anew for it
    try { sessionStorage.setItem("kino-open", "settings:movies"); } catch {}
    return location.reload();
  }
  shelfCache.clear();
  render(current);
}

async function svcConnect(svc) {
  if (svc === "jellyfin") return jfSignIn();
  const d = svcDraft[svc], secret = svc === "audiobookshelf" ? "key" : "password";
  if (!d.server) return toast(t("svcNeedServer"), 4000);
  if (svc === "podfetch") { // a user name and password, or neither for a Podfetch without sign-in
    if (d.username && !d.password) return toast(t("svcNeedPassword"), 4000);
    if (d.password && !d.username) return toast(t("svcNeedUser"), 4000);
    toast(t("svcConnecting"), 10000);
    if (await svcSend(svc, { server: d.server, username: d.username ?? "", password: d.password ?? "" })) {
      toast(t("svcDone", { name: partName("podcasts") }));
      if (current.name === "menu" || current.name === "settings") render(current, "svc-connect");
      else render(current);
    }
    return;
  }
  if (svc === "navidrome" && !d.username) return toast(t("svcNeedUser"), 4000);
  if (!d[secret]) return toast(t(svc === "navidrome" ? "svcNeedPassword" : "svcNeedKey"), 4000);
  toast(t("svcConnecting"), 10000);
  if (await svcSend(svc, { server: d.server, username: d.username, [secret]: d[secret] })) {
    toast(t("svcDone", { name: partName(SVC_PART[svc]) }));
    render(current, "svc-connect");
  }
}

// Settings → Account → Sign In Everywhere (backend/services.py: sign_in_everywhere): one user name and password for every
// service set up here, for another person on their own TV computer; below, how it went at each. Kept for the page
// after Matinee has started anew (another Jellyfin user builds everything anew). Jellyfin not set up yet: its server
// first, filled in with the first one found on the network
let everywhereDone = null;
function everywhereForm(line, jellyfinServer) {
  const d = svcDraft.everywhere;
  if (!everywhereDone) try { everywhereDone = JSON.parse(sessionStorage.getItem("kino-everywhere")); } catch {}
  const shown = (f) => (f === "password" ? (d.password ? "•".repeat(Math.min(d.password.length, 12)) : t("svcEnter")) : d[f] || t("svcEnter"));
  const name = { server: t("server"), username: t("user"), password: t("password") };
  const hint = { server: "evServerHint", username: SVC_HINTS.username, password: SVC_HINTS.password };
  const fields = jellyfinServer ? ["username", "password"] : ["server", "username", "password"];
  if (jellyfinServer) delete d.server;
  else if (d.server === undefined) everywhereLook();
  const rows = fields.map((f) => line(`ev-${f}`, name[f], esc(shown(f)), `data-action="svcfield" data-svc="everywhere" data-field="${f}"`,
    t(hint[f]))).join("") + line("ev-signin", t("signIn"), "", `data-action="everywhere"`, t("everywhereHow"));
  const said = { ok: "evOk", rejected: "evRejected", unreachable: "evUnreachable", not_set_up: "evNotSetUp" };
  const done = everywhereDone?.results;
  const result = done ? `<h2 class="settings-part">${esc(t("everywhereResult", { user: everywhereDone.user }))}</h2><div class="slist">${
    [["jellyfin", "jellyfin"], ["navidrome", "music"], ["audiobookshelf", "books"], ["podfetch", "podcasts"]].map(([svc, part]) =>
      line(`ev-${svc}`, partName(part), `${done[svc] === "ok" ? "✓ " : ""}${esc(t(said[done[svc]] ?? "evUnreachable"))}`, "", t("everywhereHow"))).join("")}</div>` : "";
  return `<h2 class="settings-part">${esc(t("signInEverywhere"))}</h2><div class="slist">${rows}</div>${result}`;
}

// Jellyfin's server for the form above, looked for once on the network; one typed in meanwhile stays
async function everywhereLook() {
  svcDraft.everywhere.server = "";
  const found = await getJson("/jellyfin/discover");
  const address = found?.servers?.[0]?.address;
  if (!address || svcDraft.everywhere.server) return;
  svcDraft.everywhere.server = address;
  const val = $('[data-k="ev-server"] .sval');
  if (val) val.textContent = address;
}

async function signInEverywhere() {
  const d = svcDraft.everywhere;
  if (!d.username) return toast(t("svcNeedUser"), 4000);
  toast(t("svcConnecting"), 20000);
  const r = await fetch("/signin/everywhere", { method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ username: d.username, password: d.password ?? "", ...(d.server ? { server: d.server } : {}) }) }).catch(() => null);
  const data = await r?.json().catch(() => ({}));
  if (!r?.ok) return toast(t(`err_${data?.error || "service_unreachable"}`), 5000);
  delete d.password;
  const said = Object.values(data.results);
  toast(t("everywhereDone", { n: said.filter((x) => x === "ok").length, total: said.filter((x) => x !== "not_set_up").length }), 5000);
  everywhereDone = { user: d.username, results: data.results };
  if (said.includes("ok")) forgetShelves(); // perhaps another person's shelves now
  try { sessionStorage.setItem("kino-everywhere", JSON.stringify(everywhereDone)); } catch {}
  if (data.results.jellyfin === "ok") { // perhaps another user there: everything built anew for them
    try { sessionStorage.setItem("kino-open", "settings:everywhere"); } catch {}
    return location.reload();
  }
  await Promise.all([musicSession(true), bookSession(true), podSession(true)]);
  shelfCache.clear();
  render(current, "ev-signin");
}

async function svcRemove(svc) {
  if (await svcSend(svc, { remove: true })) {
    svcDraft[svc] = {};
    toast(t("removed"));
    render(current, "svc-server");
  }
}

// which settings the options menu leads to: those of the area one is in, in the main menu the general ones
// The settings' pages: on the left the symbol of the part, large, and below it what the focused
// line means (#set-aside, filled by onFocus), standing still while the list on the right moves
const settingsSide = (icon, hint = "") =>
  `<aside class="settings-side"><div class="settings-icon">${icon}</div><div class="settings-aside" id="set-aside">${esc(hint)}</div></aside>`;

// The setting "effects": in place of the symbol a small main menu, as each choice shows it (the one set while its
// line has the focus, then each one reached in its choices); a picture of the main menu's, or a gradient
const fxNow = () => {
  const c = document.documentElement.classList;
  return c.contains("light") ? "light" : c.contains("plain") ? "calm" : "full";
};
// What each key does: the lines are the keys, what one means in full stands beside them as a setting's hint does
const CTL_BROWSING = [["arrows", "Arrows"], ["ok", "Ok"], ["hold", "Hold"], ["back", "Back"], ["home", "Home"], ["play", "Play"], ["menu", "Menu"], ["off", "Off"]];
const CTL_PLAYING = [["seek", "Seek"], ["updown", "UpDown"], ["info", "Info"], ["pause", "Pause"], ["stop", "Stop"]];
const CTL_PAD = [["pmove", "PMove"], ["pab", "PAB"], ["pxy", "PXY"], ["pmenu", "PMenu"], ["phome", "PHome"], ["plbrb", "PLbRb"], ["pltrt", "PLtRt"]];
const CTL_KEYBOARD = [["ksearch", "KSearch"], ["kback", "KBack"], ["kapps", "KApps"], ["ksize", "KSize"], ["ksettings", "KSettings"]];
const MOD_KEY = /Mac/.test(navigator.platform) ? "⌘" : "Ctrl"; // the shortcuts' key: a Mac's command key, a PC's Ctrl
Object.assign(views, {
  async controls() {
    const row = ([k, key]) => `<div class="srow f" tabindex="-1" data-k="ctl-${k}" data-hint="${esc(t(`ctl${key}Hint`))}"><span class="sname">${esc(t(`ctl${key}`, { mod: MOD_KEY }))}</span><span class="sval">${esc(t(`ctl${key}Do`))}</span></div>`;
    view.innerHTML = `${settingsSide(G.remote, t("controlsHint"))}<div class="grid-page settings controls"><h1>${esc(t("controls"))}</h1>
      <h2 class="settings-part">${esc(t("ctlBrowsing"))}</h2><div class="slist">${CTL_BROWSING.map(row).join("")}</div>
      <h2 class="settings-part">${esc(t("ctlPlaying"))}</h2><div class="slist">${CTL_PLAYING.map(row).join("")}</div>
      <h2 class="settings-part">${esc(t("ctlPad"))}</h2><div class="slist">${CTL_PAD.map(row).join("")}</div>
      <h2 class="settings-part">${esc(t("ctlKeyboard"))}</h2><div class="slist">${CTL_KEYBOARD.map(row).join("")}</div></div>`;
  },
  // Licenses and privacy: what Matinee is made of and under which terms, and every place it reaches out to, with whether
  // it does so on this device now (docs/privacy.md says the same at length)
  async legal() {
    const s = (await getJson("/settings")) ?? {};
    const row = (k, value) => `<div class="srow f" tabindex="-1" data-k="legal-${k}" data-hint="${esc(t(`legal_${k}Hint`))}"><span class="sname">${esc(t(`legal_${k}`))}</span><span class="sval">${esc(value)}</span></div>`;
    const onOff = (on) => t(on ? "on" : "off");
    const lic = [["kino", "FSL-1.1-MIT"], ["font", "SIL OFL 1.1"], ["jellyfin", "CC BY-SA 4.0"], ["system", t("legalTheirOwn")]];
    const net = [["servers", t("legalYours")], ["updates", s.updates ? t(s.updatecheck === "manual" ? "updateLookManual" : "updateLookAuto") : t("off")], ["weather", onOff(!!s.weather)], ["libcheck", "TMDB"], ["radio", t("legalStations")], ["time", "ptb.de"],
      ["netcheck", "example.com"], ["discover", t("legalLocal")], ["tailscale", onOff(s.tailscale?.state === "Running")],
      ...(s.support ? [["support", t("legalOnRequest")]] : []), ["nothing", ""]];
    view.innerHTML = `${settingsSide(G.seal, t("legalHint"))}<div class="grid-page settings legal"><h1>${esc(t("legal"))}</h1>
      <h2 class="settings-part">${esc(t("legalLicenses"))}</h2><div class="slist">${lic.map(([k, v]) => row(k, v)).join("")}</div>
      <h2 class="settings-part">${esc(t("legalPrivacy"))}</h2><div class="slist">${net.map(([k, v]) => row(k, v)).join("")}</div></div>`;
  },
  // Status (System): every check of backend/diagnose.py as a line, a dot for how it is; asked anew each time
  async diagnose() {
    const d = await getJson("/diagnose"); // a few seconds at most (the servers asked at once): the spinner meanwhile
    const when = (vars) => Object.fromEntries(Object.entries(vars ?? {}).map(([k, v]) => [k, k === "at" || k === "until" ? dayTime(v) : v]));
    const row = (l) => srow(`diag-${l.k}`, t(`diag_${l.k}`), `${l.state === "info" ? "" : `<i class="diag-dot ${esc(l.state)}"></i>`}${esc(l.say ? t(l.say, when(l.vars)) : l.v)}`,
      'data-action="none"');
    view.innerHTML = `${settingsSide(G.gauge, t("diagnoseHint"))}<div class="grid-page settings diagnose"><h1>${esc(t("diagnose"))}</h1>
      ${d?.lines ? `<div class="slist">${d.lines.map(row).join("")}</div>` : `<p class="jf-msg">${esc(t("saveFailed"))}</p>`}</div>`;
  },
  // How films get onto this computer: its Samba share, from Windows and from a Mac (deploy/server.sh)
  async share() {
    const l = await getJson("/jellyfin/local");
    const s = l?.share;
    const row = (k, name, value, hint = "") => srow(`share-${k}`, name, esc(value), 'data-action="none"', hint);
    view.innerHTML = `${settingsSide(G.film, t("shareHint"))}<div class="grid-page settings share"><h1>${esc(t("share"))}</h1>
      ${s ? `<p class="jf-msg">${esc(t("shareHow", { folders: l.folders.join(", ") }))}</p><div class="slist">
        ${row("win", "Windows", `\\\\${s.host}\\${s.name}`, t("shareWinHint"))}
        ${row("mac", "Mac", `smb://${s.host}/${s.name}`, t("shareMacHint"))}
        ${row("user", t("user"), s.user)}${row("password", t("password"), s.password)}</div>`
        : `<p class="jf-msg">${esc(t("shareNone"))}</p>`}</div>`;
  },
  // A report to support, only when asked for here: what goes into it first, then its code to read out on the phone
  async support() {
    view.innerHTML = `${settingsSide(G.question, t("supportHint"))}<div class="grid-page settings support"><h1>${esc(t("support"))}</h1>
      <p class="jf-msg" id="sup-msg">${esc(t("supportWhat"))}</p><div id="sup-code"></div>
      <div class="actions"><button class="btn f" data-k="sup-send" data-action="supportsend" data-autofocus>${t("supportSend")}</button></div></div>`;
  },
  // The code of the simple settings: chosen twice to turn them on, entered to turn them off. A pad of digits as a
  // phone's, or a remote's own digit keys; the fourth digit goes on by itself
  async pin({ mode = "on", step = 0 } = {}) {
    pinCode = "";
    const title = mode === "off" ? "pinEnter" : step ? "pinAgain" : "pinNew";
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "del"].map((d) => (d ? `<div class="key f" tabindex="-1" data-k="pin-${d}"
      data-action="pinkey" data-val="${d}"${d === "1" ? " data-autofocus" : ""}>${d === "del" ? "⌫" : d}</div>` : "<div></div>")).join("");
    view.innerHTML = `${settingsSide(G.lock, t(`${title}Hint`))}<div class="grid-page settings pin-page"><h1>${esc(t(title))}</h1>
      <div class="pin-dots" id="pin-dots">${"<i></i>".repeat(4)}</div><p class="pin-msg" id="pin-msg"></p><div class="pin-pad">${keys}</div></div>`;
  },
});
let pinCode = "", pinFirst = "";
const pinDots = () => $("#pin-dots")?.querySelectorAll("i").forEach((dot, i) => dot.classList.toggle("on", i < pinCode.length));
const pinMsg = (text) => { pinCode = ""; pinDots(); const m = $("#pin-msg"); if (m) m.textContent = text; };
async function pinKey(d) {
  if (current.name !== "pin") return;
  if (d === "del") pinCode = pinCode.slice(0, -1);
  else if (pinCode.length < 4) pinCode += d;
  pinDots();
  if (pinCode.length < 4) return;
  const { mode, step = 0 } = current.args ?? {}, code = pinCode;
  if (mode === "on" && !step) { // chosen: once more, to be sure of it
    pinFirst = code;
    current.args = { mode, step: 1 };
    return render(current);
  }
  if (mode === "on" && code !== pinFirst) {
    current.args = { mode };
    await render(current);
    return pinMsg(t("pinMismatch"));
  }
  pinCode = ""; // a key pressed while the backend checks: a new code from the start
  const r = await fetch("/simple", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ on: mode === "on", pin: code }) }).catch(() => null);
  if (current.name !== "pin") return;
  if (r?.status === 403) return pinMsg(t("pinWrong"));
  if (!r?.ok) return pinMsg(t("saveFailed"));
  SIMPLE = mode === "on";
  toast(t(SIMPLE ? "simpleIsOn" : "simpleIsOff"));
  back(); // to the settings, built anew with them
}

// The options under a held OK are found by no one who does not know of them: a tip on the focused tile's picture the
// first few times a tile has the focus (once a page, after a moment there), until they have been opened once. On the
// tile, not at the screen's foot or in a corner: there it lay over another row, a page's heading or the bar of parts,
// and it is the tile it tells of
const TIP_KEY = "kino-tip-hold", TIP_TIMES = 6;
let tipGen = -1, tipTimer;
function holdTip() {
  if (tipGen === renderGen) return;
  tipGen = renderGen;
  let n;
  try { n = Number(localStorage.getItem(TIP_KEY) ?? 0); } catch { return; }
  if (!(n >= 0 && n < TIP_TIMES)) return;
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => {
    if (!document.activeElement?.classList.contains("card") || !options.hidden) return;
    try { localStorage.setItem(TIP_KEY, String(n + 1)); } catch {}
    const art = document.activeElement.querySelector(".art") ?? document.activeElement;
    // the pointer in use: what the mouse does for it (a held click is a click)
    const how = document.body.classList.contains("pointer") ? "tipRight" : "tipHold";
    art.insertAdjacentHTML("beforeend", `<div class="tip" role="status">${esc(t(how))}</div>`);
    const tip = art.lastElementChild;
    requestAnimationFrame(() => tip.classList.add("on"));
    tipTimer = setTimeout(dropTip, 4000);
  }, 1500);
}
// gone after a while, and at once when the focus leaves its tile
function dropTip(el) {
  const tip = $(".card .tip");
  if (!tip || el?.contains(tip)) return;
  clearTimeout(tipTimer);
  tip.classList.remove("on");
  setTimeout(() => tip.remove(), 400);
}
function holdTipDone() {
  clearTimeout(tipTimer);
  dropTip();
  try { localStorage.setItem(TIP_KEY, "-1"); } catch {}
}

function fxPreview(mode) {
  const side = $(".settings-side");
  if (!side) return;
  let box = side.querySelector(".fx-preview");
  if (!box) {
    side.insertAdjacentHTML("afterbegin", `<div class="fx-preview" aria-hidden="true"><div class="fx-bg"></div><div class="fx-art"></div>
      <div class="fx-row"><i></i><i></i><i></i></div><div class="fx-dock"><i></i><i></i><i></i><i></i><i></i></div></div>`);
    box = side.firstElementChild;
    const pic = menuArtUrl || bgUrl || [...libNewest.values()].map((it) => it && backdropOf(it)).find(Boolean);
    if (pic) box.style.setProperty("--fx-pic", `url(${JSON.stringify(pic)})`);
  }
  box.dataset.fx = mode === "auto" ? (weakDevice() ? "light" : "full") : mode;
  side.classList.add("fx");
}

// One of one's own channels shown or left out (the settings of Jellyfin)
async function toggleTvChannel(el) {
  const s = await getJson("/settings");
  if (!s) return toast(t("saveFailed"), 4000);
  const off = new Set(s.tvoff ?? []), id = el.dataset.id;
  if (off.has(id)) off.delete(id); else off.add(id);
  const r = await fetch("/settings", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ tvoff: [...off] }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  if (id.startsWith("own-")) forgetHome(); // the library's channels all left out: their button goes
  render(current, el.dataset.k);
}

// The simple settings (SIMPLE): only these parts, the general page without the apps' and the account
let SIMPLE = false;
const SIMPLE_PARTS = new Set(["general", "basics", "saver", "av", "subs", "system", "update"]);
function settingsPart() {
  if (current.name === "menu" || SIMPLE) return "general";
  return { jellyfin: "movies", music: "navidrome" }[lastSection] ?? "general"; // books: none of their own
}

async function unhideAll() {
  const r = await fetch("/unhide", { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  hidden = { nextup: [], albums: [], books: [] };
  shelfCache.clear();
  forgetHome();
  toast(t("unhidden"));
  await render(current);
  const sel = view.querySelector('[data-opt="screensaver"].sel'); // near where the button was
  if (sel) focusEl(sel, false);
}

// OK on a line of the settings: its choices, the one set ticked and chosen, as the options are
let settingChoices = {};
// what a setting is set to; the audio sync: for the sound output in use
const settingValue = (s, opt) => (opt === "audiosync" ? String(s.audiosync?.ms ?? 0) : String(s[opt]));
async function chooseSetting(el) {
  const { name, opts } = settingChoices[el.dataset.opt] ?? {};
  if (!opts) return;
  const s = await fetch("/settings").then((r) => r.json()).catch(() => ({}));
  const now = settingValue(s, el.dataset.opt);
  const value = (v) => (el.dataset.opt === "audiosync" ? { device: s.audiosync?.id, ms: v } : v);
  showOptions(el, name, opts.map(([v, l]) => ({ label: l, on: v === now, sound: el.dataset.opt === "soundkind" ? v : undefined,
    preview: el.dataset.opt === "effects" ? () => fxPreview(v) : undefined,
    run: () => v !== now && changeSetting(el.dataset.opt, value(v), el.dataset.k) })),
    Math.max(0, opts.findIndex(([v]) => v === now)));
}

async function changeSetting(opt, val, key) {
  const r = await fetch("/settings", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ [opt]: val }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  if (opt === "language") {
    forgetShelves(); // their titles are in the old one
    // the whole UI is built in one language: reload, and come back to this page
    try { sessionStorage.setItem("kino-open", `settings:${current.args?.part ?? "general"}`); } catch {}
    return location.reload();
  }
  if (opt === "screensaver") useSaver(val);
  if (opt === "spoilers") SPOILERS = val === "show";
  if (opt === "effects") useEffects(val);
  if (opt === "textsize") useTextSize(val);
  if (opt === "motion") useMotion(val);
  if (opt === "size") useSize(val);
  if (opt === "badges") { BADGE_MODE = val; if (val !== "off") await loadBadges(true); }
  if (opt === "sounds") { SOUNDS = SOUND_LEVELS[val] ?? 0; tick(); } // heard at once, at the level chosen
  if (opt === "soundkind") { SOUND_KIND = val; tick(); }
  toast(t("saved"));
  render(current, key);
}

// Restart or shut down: a remote is pressed by accident easily, so only a second OK within a few seconds does it
// the TV computer's sound (also a Bluetooth speaker's), in steps of 5 %; the page stays as it is, focus and all
async function changeVolume(step) {
  const r = await fetch("/volume", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ step }) }).catch(() => null);
  const v = r?.ok ? (await r.json()).volume : null;
  if (v == null) return toast(t("saveFailed"), 4000);
  const now = $("#vol-now");
  if (now) now.textContent = `${v} %`;
  showVolume(v);
}

// The menu key: the volume, as a bar in the corner. Up and right louder, down and left quieter; it goes away
// three seconds after the last key, or with Back, OK or the menu key. (The menu of the focused tile: OK held.)
const volumeBox = $("#volume");
let volumeTimer = null;
function showVolume(v) {
  sinkVolume = v; // the clicks follow it (sinkFactor)
  volumeBox.querySelector("h2").textContent = t("volume");
  volumeBox.querySelector("i").style.transform = `scaleX(${v / 100})`;
  volumeBox.querySelector("span").textContent = `${v} %`;
  clearTimeout(volumeTimer);
  if (!volumeBox.hidden) volumeTimer = setTimeout(closeVolume, 3000);
}
async function openVolume() {
  const r = await fetch("/volume", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ step: 0 }) }).catch(() => null);
  const v = r?.ok ? (await r.json()).volume : null;
  if (v == null) return toast(t("noVolume"), 4000);
  volumeBox.hidden = false;
  showVolume(v);
}
// a volume key: one step and the bar shown; on a desktop nothing (the desktop changes its own volume)
async function volumeStep(step) {
  const r = await fetch("/volume", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ step }) }).catch(() => null);
  const v = r?.ok ? (await r.json()).volume : null;
  if (v == null) return;
  volumeBox.hidden = false;
  showVolume(v);
}
function closeVolume() {
  clearTimeout(volumeTimer);
  volumeBox.hidden = true;
}
function volumeKey(e) {
  e.preventDefault();
  const step = { ArrowUp: 5, ArrowRight: 5, ArrowDown: -5, ArrowLeft: -5 }[e.key];
  if (step) changeVolume(step);
  else if (!e.repeat && (BACK.includes(e.key) || MENU.includes(e.key) || OK.includes(e.key) || HOME.includes(e.key))) closeVolume();
}

// Restart or shut down: asked first, as the update is
function systemAction(el, action) {
  const run = async () => {
    const r = await fetch("/system", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ action }) }).catch(() => null);
    toast(r?.ok ? t(action === "reboot" ? "rebooting" : "poweringOff") : t("saveFailed"), 10000);
  };
  showOptions(el, t(action === "reboot" ? "confirmReboot" : "confirmPoweroff"), [{ label: t(action), run }, { label: t("cancel") }]);
}

// Software update (System): OK looks for one, a second OK asks whether to install it now;
// Matinee then starts again (the page reloads once the new backend answers, see alive)
const updateCount = (r) => (r.behind === 1 ? t("updateChangesOne") : t("updateChanges", { n: r.behind }));
const updateLabel = (r) => `${t("updateAvailable")} · ${updateCount(r)}`;
// below the line, what the last look found: when the version here and the newest were made, and when it was looked
let updateSeen = null, updateRecent = []; // the settings' update (the last look) and updateRecent (the last changes installed)
function updateInfo(r) {
  const rows = r ? [[t("updateInstalled"), [r.version, r.installed && dayTime(r.installed)].filter(Boolean).join(" · ")],
    ...(r.behind && r.at ? [[t("updateNewest"), dayTime(r.at)]] : []),
    ...(r.checked ? [[t("updateChecked"), dayTime(r.checked)]] : [])].filter(([, v]) => v) : [];
  return rows.length ? `<dl class="about">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : "";
}
// The patch notes, a line of their own ("Changes"): OK opens them in the reader, up and down scroll. What the update
// brings (the commits' own words, newest first), then what the last versions brought.
const changeList = (list, more) => `<ul class="update-changes">${list.map((c) =>
  `<li><time>${esc(dayTime(c.at))}</time>${esc(c.s)}</li>`).join("")}${more ? `<li><time></time>${esc(t("updateMore", { n: more }))}</li>` : ""}</ul>`;
function updateNotes(r, recent) {
  const fresh = r?.behind && r.changes?.length ? r.changes : [];
  return (fresh.length ? `<h3 class="update-new">${esc(t("updateWhatsNew"))} <span>${esc(updateCount(r))}</span></h3>`
    + changeList(fresh, Math.max(0, r.behind - fresh.length)) : "")
    + (recent?.length ? `<h3 class="update-new">${esc(t("updateRecent"))}</h3>${changeList(recent)}` : "");
}
// the line's value: what an update brings, else when the last change came
const updateNotesValue = (r, recent) => (r?.behind ? `${t("updateWhatsNew")} · ${updateCount(r)}` : recent?.length ? dayTime(recent[0].at) : "");
function openUpdateNotes(el) {
  reader.innerHTML = `<div class="reader-box update-notes"><h2>${esc(t("updateNotes"))}</h2>${updateNotes(updateSeen, updateRecent)}</div>`;
  reader.hidden = false;
  reader.opener = el;
}

async function softwareUpdate(el) {
  const val = el.querySelector(".sval");
  if (el.dataset.ready && el.dataset.install !== "1") return toast(t("updateByGit"), 6000); // a desktop's working copy
  if (el.dataset.ready) {
    return showOptions(el, t("updateConfirm"), [{ label: t("updateNow"), run: installUpdate }, { label: t("cancel") }]);
  }
  val.textContent = t("updateChecking");
  const r = await fetch("/update/check", { method: "POST", headers: JSON_HEADERS, body: "{}" }).then((x) => x.json()).catch(() => null);
  const info = $("#update-info");
  if (info && r) info.innerHTML = updateInfo(r);
  if (r && !r.error) {
    updateSeen = r;
    const notes = $("[data-k=set-notes] .sval");
    if (notes) notes.textContent = updateNotesValue(r, updateRecent);
  }
  if (!r || r.error) {
    val.textContent = t("updateCheck");
    return toast(t("updateFailed"), 5000);
  }
  if (!r.behind) return void (val.textContent = t("updateCurrent"));
  el.dataset.ready = "1";
  val.textContent = updateLabel(r);
}

async function installUpdate() {
  const r = await fetch("/update/install", { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => null);
  toast(t(r?.ok ? "updateInstalling" : "updateFailed"), r?.ok ? 30000 : 5000);
}

// ---------- Bluetooth ----------

let lcTimer = null, lcState = null;
const lcRefresh = async () => lcShow(await getJson("/library/check"));
// TheMovieDb's pictures come in their full size: a small one for a line
const lcPicture = (url) => (url ? String(url).replace("/t/p/original/", "/t/p/w185/") : "");
function lcShow(d) {
  const msg = $("#lc-msg"), body = $("#lc-body");
  if (!d || !msg || !body) return;
  lcState = d;
  const items = d.items ?? [], ready = items.filter((e) => e.state === "ready" || e.state === "failed");
  const busy = d.state === "scanning" || d.state === "looking";
  msg.textContent = d.state === "error" ? t(`lc_${d.error}`) : d.state === "scanning" ? t("lcScanning")
    : !items.length ? t("lcAllFine") : t(items.length === 1 ? "lcFoundOne" : "lcFound", { n: items.length }) + (busy ? ` ${t("lcLooking")}` : "");
  const film = (pic, name, year) => `<span class="lc-film">${pic ? `<img src="${esc(pic)}" alt="" decoding="async">` : "<i></i>"}`
    + `<span><b>${esc(name ?? "")}</b><small>${esc(year ?? "")}</small></span></span>`;
  const reason = (e) => t(`lc_${e.kind}`, { id: e.hints?.tmdb ?? e.hints?.imdb ?? "", year: e.hints?.year ?? "" });
  const row = (e) => {
    const p = e.proposal, fixed = e.state === "fixed";
    const now = film(fixed && p?.image ? lcPicture(p.image) : `/jf/Items/${e.id}/Images/Primary?maxWidth=120&quality=90`,
      fixed && p?.name ? p.name : e.name, fixed && p ? p.year : e.year);
    const then = fixed ? "" : p ? `<span class="lc-arrow">→</span>${film(lcPicture(p.image), p.name, p.year)}` : "";
    return `<div class="srow f lc" tabindex="-1" data-k="lc-${esc(e.id)}" data-action="libfix" data-id="${esc(e.id)}"
      data-hint="${esc(`${reason(e)}\n${e.file}`)}">${now}${then}<span class="sval">${esc(t(`lcState_${e.state}`))}</span></div>`;
  };
  const tvdb = items.some((e) => e.tvdb && (e.kind === "wrong" || e.kind === "year"));
  const html = (busy ? "" : `<div class="slist">${ready.length > 1 ? srow("lc-all", t("lcFixAll", { n: ready.length }), "", `data-action="libfixall"`, t("lcFixAllHint")) : ""}`
      + srow("lc-again", t("lcAgain"), "", `data-action="libcheck"`, t("libCheckHint")) + "</div>")
    + (tvdb ? `<p class="hint lc-tip">${esc(t("lcTvdbTip"))}</p>` : "")
    + (items.length ? `<div class="slist lc-list">${items.map(row).join("")}</div>` : "");
  if (html === body.dataset.html) return; // each second: only when something changed
  body.dataset.html = html;
  const k = document.activeElement?.dataset?.k;
  body.innerHTML = html;
  const again = (k && view.querySelector(`[data-k="${CSS.escape(k)}"]`)) || (document.activeElement === document.body || !view.contains(document.activeElement)
    ? view.querySelector("#lc-body .srow") : null);
  if (again && again !== document.activeElement) focusEl(again, false);
}

function lcAsk(el) {
  const e = lcState?.items.find((x) => x.id === el.dataset.id);
  if (!e || !(e.state === "ready" || e.state === "failed")) return;
  const p = e.proposal, label = (name, year) => (year ? `${name} (${year})` : name);
  showOptions(el, e.kind === "poster" ? t("lcAskPoster", { name: e.name }) : t("lcAsk", { old: label(e.name, e.year), new: label(p.name, p.year) }),
    [{ label: t("lcFixNow"), run: () => lcFix([e.id]) }, { label: t("cancel") }]);
}

function lcAskAll(el) {
  const ids = (lcState?.items ?? []).filter((e) => e.state === "ready" || e.state === "failed").map((e) => e.id);
  if (ids.length) showOptions(el, t("lcAskAll", { n: ids.length }), [{ label: t("lcFixNow"), run: () => lcFix(ids) }, { label: t("cancel") }]);
}

async function lcFix(ids) {
  const r = await fetch("/library/fix", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ ids }) }).catch(() => null);
  if (!r?.ok) return toast(t("lcFailed"), 5000);
  shelfCache.clear(); // other titles and pictures on the rows
  lcRefresh();
}

let btTimer = null, btBusy = false;
// a headset's class says nothing: speakers with a microphone for calls tell it too (e.g. Magnat's Prime One), so it is
// called neither
const BT_KIND = { "audio-headphones": "btHeadphones", "audio-card": "btSpeaker",
  "input-keyboard": "btInput", "input-mouse": "btInput", "input-gaming": "btInput" };

/* ---------- Jellyfin on this computer (local_server.py) ---------- */

let localBusy = false;
async function localSetup() {
  const d = svcDraft.local;
  if (!d.username) return toast(t("svcNeedUser"), 4000);
  if ((d.password ?? "").length < 4) return toast(t("err_local_password"), 5000);
  if (localBusy) return;
  localBusy = true;
  toast(t("localWorking"), 30000);
  try {
    const r = await post("/jellyfin/local/setup", { username: d.username, password: d.password });
    if (!r || r.error) return toast(t(`err_${r?.error || "local_failed"}`), 6000);
    svcDraft.local = {};
    forgetShelves();
    toast(t("svcDone", { name: partName("jellyfin") }));
    if (current.name !== "welcome") return location.reload(); // the sign-in page: Jellyfin's start, all of it anew
    render(current);
  } finally {
    localBusy = false;
  }
}

/* ---------- A report to support (support.py) ---------- */

let supportBusy = false;
async function supportSend(el) {
  if (supportBusy) return;
  supportBusy = true;
  const msg = $("#sup-msg");
  if (msg) msg.textContent = t("supportSending");
  try {
    const r = await post("/support/send");
    if (current.name !== "support") return;
    if (!r || r.error) return void ($("#sup-msg").textContent = t(`err_${r?.error || "support_failed"}`));
    $("#sup-msg").textContent = t("supportSent");
    $("#sup-code").innerHTML = `<div class="transfer-code">${esc(r.code)}</div>`;
    el.remove(); // sent: once is enough (Back and in again for another)
  } finally {
    supportBusy = false;
  }
}

/* ---------- The TV's other inputs (hdmi.py, through HDMI-CEC) ---------- */

const HDMI_MAX = 6, HDMI_PORTS = [1, 2, 3, 4];
let hdmiInputs = [], hdmiTiles = [], hdmiAway = false;

// The main menu's tiles for them: drawn with the ones known, without waiting; asked after it is there, and the menu
// drawn anew where they changed (set up on another page, an adapter plugged in)
async function hdmiCheck(gen) {
  const d = await getJson("/hdmi");
  const now = d?.available ? d.inputs : [];
  if (!d || JSON.stringify(now) === JSON.stringify(hdmiTiles)) return;
  hdmiTiles = now;
  if (gen === renderGen && current.name === "menu") render(current, document.activeElement?.dataset?.k);
}

// A tile on the main menu: the TV to that input. Matinee's music paused meanwhile; the next key on Matinee's remote brings
// the TV back (see input.js)
async function hdmiGo(input) {
  if (!input) return;
  if (musicState.active && !musicState.paused) musicControl("pause");
  const r = await post("/hdmi/switch", { port: input.port });
  if (!r || r.error) return toast(t(`err_${r?.error || "hdmi_failed"}`), 5000);
  hdmiAway = true;
  toast(t("hdmiSwitched", { name: input.name }), 4000);
}

async function hdmiBack() {
  hdmiAway = false;
  const r = await post("/hdmi/back");
  if (!r || r.error) toast(t(`err_${r?.error || "hdmi_failed"}`), 5000);
}

async function hdmiSave(inputs, focusKey) {
  const r = await post("/hdmi/inputs", { inputs });
  if (!r || r.error) return toast(t("err_hdmi_failed"), 5000);
  hdmiInputs = r.inputs;
  if (current.name === "hdmi") render(current, focusKey);
}

// Adding one: its port first (those not taken), then its name on the letter grid
function hdmiAdd(el, i = null) {
  const taken = new Set(hdmiInputs.filter((_, j) => j !== i).map((x) => x.port));
  showOptions(el, t("hdmiWhichPort"), HDMI_PORTS.filter((n) => !taken.has(n)).map((n) => ({
    label: t("hdmiPort", { n }),
    run: () => {
      if (i !== null) return hdmiSave(hdmiInputs.map((x, j) => (j === i ? { ...x, port: n } : x)), `hdmi-${i}`);
      svcDraft.hdmi = { port: n };
      navigate("svcentry", { svc: "hdmi", field: "name", q: "" });
    },
  })));
}

function hdmiNamed({ i, port, name = "" }) {
  name = name.trim().slice(0, 30);
  if (!name) return;
  if (i != null) return hdmiSave(hdmiInputs.map((x, j) => (j === i ? { ...x, name } : x)), `hdmi-${i}`);
  hdmiSave([...hdmiInputs, { name, port }], `hdmi-${hdmiInputs.length}`);
}

function hdmiInput(el) {
  const i = +el.dataset.i, x = hdmiInputs[i];
  showOptions(el, x.name, [
    { label: t("hdmiSwitchNow"), run: () => hdmiGo(x) },
    { label: t("hdmiRename"), run: () => { svcDraft.hdmi = { i }; navigate("svcentry", { svc: "hdmi", field: "name", q: x.name }); } },
    { label: t("hdmiChangePort"), run: () => hdmiAdd(el, i) },
    { label: t("hdmiRemove"), run: () => hdmiSave(hdmiInputs.filter((_, j) => j !== i), "hdmi-add") },
  ]);
}

/* ---------- Wi-Fi (network.py, through NetworkManager) ---------- */

let wifiBusy = false;
async function wifiRefresh(rescan = false) {
  const d = await getJson(rescan ? "/wifi?rescan=1" : "/wifi");
  const box = $("#wifilist");
  if (!box || !d) return;
  const bars = (n) => "▂▄▆█".slice(0, Math.max(1, Math.ceil(n / 25)));
  const chip = (x) => `<div class="chip f${x.active ? " sel" : ""}" tabindex="-1" data-k="wifi-${esc(x.ssid)}" data-action="wifinet"
    data-ssid="${esc(x.ssid)}" data-on="${x.active ? 1 : 0}" data-known="${x.known ? 1 : 0}" data-secure="${x.secure ? 1 : 0}">${esc(x.ssid)}<span class="chip-sub">${
    [x.active ? t("btConnected") : x.known ? t("wifiKnown") : "", `${bars(x.signal)}${x.secure ? " 🔒" : ""}`].filter(Boolean).join(" · ")}</span></div>`;
  const k = document.activeElement?.dataset?.k;
  box.innerHTML = d.networks.length ? `<div class="recent">${d.networks.map(chip).join("")}</div>` : `<div class="hint">${t("wifiNone")}</div>`;
  const again = k && view.querySelector(`[data-k="${CSS.escape(k)}"]`);
  if (again) focusEl(again, false);
}

// OK on a network: the one in use or one known offers what can be done with it; another one is joined, with its
// password first where it has one
function wifiPick(el) {
  const ssid = el.dataset.ssid;
  if (el.dataset.on === "1" || el.dataset.known === "1") {
    return showOptions(el, ssid, [
      ...(el.dataset.on === "1" ? [] : [{ label: t("wifiJoin"), run: () => wifiJoin(ssid) }]),
      { label: t("wifiForget"), run: () => wifiForget(ssid) }]);
  }
  if (el.dataset.secure !== "1") return wifiJoin(ssid);
  svcDraft.wifi = { ssid };
  navigate("svcentry", { svc: "wifi", field: "password", q: "" });
}

async function wifiJoin(ssid, password) {
  if (wifiBusy || !ssid) return;
  wifiBusy = true;
  delete svcDraft.wifi.password;
  toast(t("wifiJoining", { name: ssid }), 60000);
  try {
    const r = await post("/wifi/connect", password == null ? { ssid } : { ssid, password });
    toast(r && !r.error ? t("wifiJoined", { name: ssid }) : t(`err_${r?.error || "wifi_failed"}`), 5000);
  } finally {
    wifiBusy = false;
  }
  if (current.name === "wifi") wifiRefresh();
}

async function wifiForget(ssid) {
  await post("/wifi/forget", { ssid });
  toast(t("wifiForgotten", { name: ssid }), 4000);
  if (current.name === "wifi") wifiRefresh();
}

async function btScan() {
  await fetch("/bt/scan", { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => {});
  await btRefresh();
}

async function btRefresh(d) {
  d ??= await fetch("/bt/devices").then((r) => r.json()).catch(() => null);
  const box = $("#btlist");
  if (!box || !d) return;
  const scan = $("[data-k=btscan] .sname");
  if (scan) scan.textContent = t(d.scanning ? "btScanning" : "btScan");
  // a device as a line of the settings: its name, and on the right its kind and how it is; beside the list what is
  // wrong with one that does not answer, else what the page is for
  const row = (x) => {
    const kind = BT_KIND[x.icon] ? `${t(BT_KIND[x.icon])} · ` : "";
    const stateText = x.connected ? t("btConnected") : x.unreachable ? t("btUnreachable") : x.paired ? t("btNotConnected") : t("btNew");
    const hint = x.unreachable ? t("btUnreachableHint", { name: x.name }) : t("btHint");
    return `<div class="srow f${x.connected ? " on" : ""}" tabindex="-1" data-k="bt-${esc(x.mac)}" data-action="bt" data-mac="${esc(x.mac)}"
      data-name="${esc(x.name)}" data-on="${x.connected ? 1 : 0}" data-paired="${x.paired ? 1 : 0}" data-audio="${x.icon.startsWith("audio") ? 1 : 0}"
      data-hint="${esc(hint)}"><span class="sname">${esc(x.name)}</span><span class="sval">${kind}${stateText}</span></div>`;
  };
  const mine = d.devices.filter((x) => x.paired), near = d.devices.filter((x) => !x.paired);
  // one that does not answer: most likely another device has it (a speaker takes one at a time)
  const down = mine.find((x) => x.unreachable);
  const html = (mine.length ? `<h2 class="settings-part">${t("btMine")}</h2><div class="slist">${mine.map(row).join("")}</div>` : "")
    + (down ? `<p class="hint bt-down">${esc(t("btUnreachableHint", { name: down.name }))}</p>` : "")
    + `<h2 class="settings-part">${t("btNearby")}</h2>`
    + (near.length ? `<div class="slist">${near.map(row).join("")}</div>` : `<p class="hint">${t(d.scanning ? "btLooking" : "btNoneNearby")}</p>`);
  if (html === box.dataset.html) return; // every two seconds: only when something changed
  box.dataset.html = html;
  const k = document.activeElement?.dataset?.k;
  box.innerHTML = html;
  const again = k && view.querySelector(`[data-k="${CSS.escape(k)}"]`);
  if (again && again !== document.activeElement) focusEl(again, false);
}

async function btAction(mac, action, name, paired = false) {
  if (btBusy) return;
  btBusy = true;
  toast(t({ connect: "btConnecting", disconnect: "btDisconnecting", remove: "btRemoving" }[action], { name }), 30000);
  try {
    const r = await fetch("/bt/action", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ mac, action }) }).catch(() => null);
    const res = await r?.json().catch(() => null);
    toast(r?.ok ? t({ connect: "btConnectedTo", disconnect: "btDisconnected", remove: "btRemoved" }[action], { name })
      // paired before: not a pairing mode it lacks, mostly another device that has it (a phone, a computer)
      : t(paired && action === "connect" ? "btFailedPaired" : "btFailed", { name }), paired ? 8000 : 4000);
    if (res && current.name === "bluetooth") btRefresh();
  } finally { btBusy = false; }
}

