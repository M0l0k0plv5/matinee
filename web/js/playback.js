"use strict";
// Matinee's page, part 16 of 18: playback.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Playback ---------- */

let polling = false;
const play = (itemId, resume) => startPlayback("/play", { itemId, resume });

// From pressing play until the player is in front, and on until its first picture: just black (the curtain)
const curtain = $("#curtain");
// what is starting, with a spinner, once it takes longer than a moment (a quick start shows only black)
let curtainTimer;
function curtainTitle(el) {
  const it = el && reg[el.dataset.r];
  const name = it?.Type === "Episode" ? `${it.SeriesName} · ${it.Name}` : it?.Name ?? it?.title ?? it?.name
    ?? el?.querySelector?.(".label")?.textContent ?? $("#hero .title")?.textContent ?? "";
  curtain.innerHTML = `<div class="curtain-box"><div class="curtain-title">${esc(name)}</div><div class="curtain-wait">${esc(t("starting"))}</div></div>`;
  clearTimeout(curtainTimer);
  curtain.classList.remove("slow");
  curtainTimer = setTimeout(() => curtain.classList.contains("on") && curtain.classList.add("slow"), 1000);
}
let statusTick = null; // while a player is in front: back in Matinee's window, look at once whether it has closed
addEventListener("focus", () => statusTick?.());
async function startPlayback(path, body) {
  shelfCache.clear(); // what is being watched changes with it
  forgetHome();
  curtain.classList.add("on");
  curtainTitle(document.activeElement);
  try {
    const r = await fetch(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  } catch (e) {
    // also when the backend is not reachable at all (e.g. restarting): say so, never just stay black; in plain words,
    // the reason (an exception's text from the backend) only in the log
    curtain.classList.remove("on", "slow");
    console.warn("Playback not started:", e?.message ?? e);
    return toast(t(e?.message === "tv_failed" ? "tvPlayFailed" : e?.message === "age_limit" ? "ageLimitPlay" : "playFailed"), 5000);
  }
  // the player is in front now, with its own turning ring until the picture (--force-window): from here on the
  // curtain is only black, so the end (the backend still reporting it to Jellyfin) does not say "Starting…"
  clearTimeout(curtainTimer);
  curtain.classList.remove("slow");
  followPlayback();
}

// While a player is in front: the page black behind it, and back once it has closed. Also for one another device
// started here (Continue on, take_handoff in backend/handoff.py)
function followPlayback() {
  if (polling) return;
  curtain.classList.add("on");
  polling = true;
  let seen = false, timer = null;
  const started = Date.now();
  const tick = async () => {
    clearTimeout(timer);
    const s = await fetch("/status").then((r) => r.json()).catch(() => ({}));
    if (!polling) return; // the focus came back and another look has ended it already
    if (s.playing) seen = true;
    if ((seen && !s.playing) || (!seen && Date.now() - started > 15000)) {
      polling = false;
      statusTick = null;
      readSinkVolume(); // turned up or down for the film: the clicks follow (sinkFactor)
      wake(); // a whole movie without a key: the screensaver starts counting only now
      curtain.classList.remove("on", "slow"); // the page comes out of the black
      if (s.error) toast(t({ connection: "connLost", tv: "tvPlayFailed", crashed: "playStopped", unreachable: "playerUnreachable" }[s.error] ?? "playFailed"), 6000);
      // the page is there as it was; wait briefly so Jellyfin has processed the stop, then quietly update the
      // progress shown
      if (current.name === "ownchannels" && s.itemId?.startsWith("tv:live-")) {
        // zapped through the channels: back on the one watched last
        const id = s.itemId.slice(8);
        setTimeout(() => { current.offsets = keepOffsets(); render(current, `live-${id}`, view.scrollTop); }, 800);
      } else setTimeout(refresh, 800);
      return;
    }
    timer = setTimeout(tick, seen ? 700 : 1500);
  };
  statusTick = tick;
  tick();
}

let toastTimer;
function toast(msg, ms = 2500) {
  const box = $("#toast");
  box.textContent = msg;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), ms);
}

// every picture that has arrived fades in (style.css); load does not bubble, so it is caught on the way down
document.addEventListener("load", (e) => {
  if (e.target.tagName !== "IMG") return;
  e.target.classList.add("in");
}, true);
// A title's logo or a poster beside a page's text the server does not have after all: the name written out in the
// logo's place (the browser drew its broken-picture sign with the name beside it), the poster left out. A tile's
// picture gets its name (books.js).
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG") return;
  if (img.classList.contains("logo") && img.alt) {
    const title = document.createElement("h1");
    title.className = "title";
    title.textContent = img.alt;
    return img.replaceWith(title);
  }
  img.closest(".poster")?.remove();
}, true);
// One the browser has already (a page built again: Back, a title marked as watched, the return from a video) is there
// at once: faded in anew, every picture on the screen went dark and came back
new MutationObserver((changes) => {
  for (const change of changes) {
    for (const node of change.addedNodes) {
      if (node.nodeType !== 1) continue;
      for (const img of node.tagName === "IMG" ? [node] : node.getElementsByTagName("img")) {
        if (!img.complete || !img.naturalWidth || img.classList.contains("in")) continue;
        img.style.transition = "none";
        img.classList.add("in");
      }
    }
  }
}).observe(document.body, { childList: true, subtree: true });
