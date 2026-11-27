"use strict";
// Matinee's page, part 8 of 18: one's own channels (the Jellyfin library as television).
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- One's own channels: the Jellyfin library as television (tv.py) ---------- */

// The channel watched last (live id): its tile focused when the channels are shown again
let tvLastLive = null;

// The programme guide (see views.guide): its channels, where its time starts, the time the focus is at (up and down
// keep to it) and how far the time is moved to the left
let epg = null;
const EPG_PX = 7; // a minute's width
function epgScroll(el, smooth) {
  const box = el.closest(".epg-box"), track = box.querySelector(".epg-track"), row = el.closest(".epg-row");
  const y = Math.max(0, Math.min(row.offsetTop - box.clientHeight * 0.3, track.scrollHeight - box.clientHeight));
  slide(track, `translate3d(0,${-y}px,0)`, smooth);
  const line = row.querySelector(".epg-line").clientWidth, left = el.offsetLeft, width = Math.min(el.offsetWidth, line * 0.6);
  if (left < epg.x) epg.x = Math.max(0, left - 30 * EPG_PX);
  else if (left + width > epg.x + line) epg.x = left + width - line * 0.75;
  el.closest(".epg").style.setProperty("--epg-x", `${-epg.x}px`);
}

// Up and down in the guide: the programme of the channel above or below that runs at the time the focus is at
function epgMove(cur, dir) {
  if (dir === "left" || dir === "right") {
    const next = dir === "left" ? cur.previousElementSibling : cur.nextElementSibling;
    if (next) {
      const from = Number(next.dataset.start);
      epg.at = from <= epg.now && epg.now < Number(next.dataset.end) ? epg.now : from; // the one running: from now
      focusEl(next);
    }
    return;
  }
  const row = cur.closest(".epg-row")[dir === "up" ? "previousElementSibling" : "nextElementSibling"];
  const cells = [...(row?.querySelectorAll(".epg-cell") ?? [])];
  const hit = cells.find((c) => Number(c.dataset.start) <= epg.at && epg.at < Number(c.dataset.end))
    ?? cells.reduce((a, b) => (!a || Math.abs(Number(b.dataset.start) - epg.at) < Math.abs(Number(a.dataset.start) - epg.at) ? b : a), null);
  if (hit) focusEl(hit);
}

// OK in the guide: what runs now live, what is to come: the film or episode itself
function playGuide(el) {
  const c = epg.channels[Number(el.dataset.c)];
  const x = c.items[Number(el.dataset.i)];
  return x ? playScheduled(c.id, x) : startPlayback("/tv/play", { live: c.id });
}

// A channel live: its name on the tile, below it what runs now (filled in by showTvNow)
function liveCard(c, key = `live-${c.id}`) {
  return tile({ item: { ...c, live: true }, key, action: "tvlive", shape: "land", attrs: ` data-id="${esc(c.id)}"`,
    artClass: "channel", art: `${cardImg(null, c.name)}<div class="badge time status live">${t("live")}</div>`, label: c.name, sub: "" });
}

// What runs on the channels now (live id → {title, sub, about, start, end, picture, next}), from their programmes:
// on the live tiles with a bar for how far it is. Looked at again every minute.
let tvNow = new Map(), tvNowTimer = null;
async function showTvNow() {
  clearTimeout(tvNowTimer);
  const now = await getJson("/tv/now");
  if (now) tvNow = new Map(Object.entries(now));
  const cards = view.querySelectorAll("[data-action=tvlive]");
  for (const card of cards) {
    const x = tvNow.get(card.dataset.id);
    const sub = card.querySelector(".sub"), art = card.querySelector(".art");
    if (sub) sub.textContent = x?.title ?? "";
    art.querySelector(".progress")?.remove();
    const pct = x && Math.min(100, Math.max(0, (100 * (Date.now() / 1000 - x.start)) / (x.end - x.start)));
    if (x) art.insertAdjacentHTML("beforeend", `<div class="progress"><i style="width:${pct}%"></i></div>`);
    const pic = x?.picture; // from Jellyfin (/jf/…)
    if (!pic) { // none for what runs now: not the one of the programme before
      art.querySelector(".now-pic")?.remove();
      art.classList.remove("showing");
    }
    if (pic && art.querySelector(".now-pic")?.getAttribute("src") !== pic) {
      art.querySelector(".now-pic")?.remove();
      art.insertAdjacentHTML("afterbegin", `<img class="now-pic" src="${pic}" decoding="async" alt="">`);
      // the channel's name moves to the corner only once there is a picture: one that cannot be had leaves it as it was
      const img = art.querySelector(".now-pic");
      img.addEventListener("load", () => art.classList.add("showing"), { once: true });
      img.addEventListener("error", () => { img.remove(); art.classList.remove("showing"); }, { once: true });
    }
  }
  if (cards.length) tvNowTimer = setTimeout(() => cards[0].isConnected && showTvNow(), 60000);
}

// The tile in front of the channels that opens the programme guide
const guideCard = () => tile({ item: { Id: "guide", Name: t("tvGuide") }, key: "own-guide", action: "ownguide", shape: "land",
  artClass: "channel", art: `<div class="ph icon">${ICON_GUIDE}</div>`, label: t("tvGuide") });

// A programme of the guide: live now, else the film or episode itself, whenever it runs
function playScheduled(live, x) {
  const now = Date.now() / 1000;
  if (x.start <= now && now < x.end) return startPlayback("/tv/play", { live });
  if (x.jf) return play(x.jf, true);
  return toast(t("tvRunsAt", { time: timeOfDay(new Date(x.start * 1000)) }), 4000);
}
