"use strict";
// Matinee's page, part 17 of 18: keys, a mouse or trackpad.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

/* ---------- Input ---------- */

// Keys from keyboard and remote (USB/RF remotes send ordinary key codes)
const KEYS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
const BACK = ["Escape", "Backspace", "BrowserBack", "GoBack"];
const HOME = ["Home", "BrowserHome", "GoHome"];
const PLAY = ["MediaPlayPause", "MediaPlay"];
const MENU = ["ContextMenu", "Menu"];
const VOLUME_KEYS = { AudioVolumeUp: 5, AudioVolumeDown: -5 };
const OK = ["Enter", " ", "Select"]; // Select: the OK key of some remotes
const SHORTCUTS = {
  f: () => { const search = searchHere(); if (search && current.name !== search) navigate(search, { q: "" }); },
  ",": () => current.name !== "settings" && navigate("settings", {}),
  "[": () => back(true), // straight back, as the mouse's
  ArrowLeft: () => back(true), // as a browser's
  ...Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => [n, () => openPart(n)])), // the apps, in the menu's order
};
// ⌘+ and ⌘− (⌘= on an American keyboard): the size Matinee is drawn at, a television's or a desk's; ⌘0 as it is by itself
// here. Instead of the browser's own zoom, which changes nothing that is measured by the screen's width.
const SIZE_KEYS = { "+": "tv", "=": "tv", "-": "desk", "0": "auto" };
async function sizeKey(val) {
  const r = await fetch("/settings", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ size: val }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  useSize(val);
  toast(`${t("uiSize")}: ${t({ tv: "uiSizeTv", desk: "uiSizeDesk", auto: "effectsAuto" }[val])}`);
  render(current, document.activeElement?.dataset?.k);
}
const LONG_PRESS = 700;
const STEP_MS = 110; // a held arrow: one step this often, at the pace the pages glide
let okTimer = null, okDown = false, okLong = false, lastKey = 0, lastMove = 0, stepTimer = null;

document.addEventListener("keydown", (e) => {
  audioWake(); // the clicks' sound output, resting: awake by the time this key's click plays
  const el = document.activeElement?.classList.contains("f") ? document.activeElement : null;
  lastKey = Date.now();
  noteActive();
  // the TV on another input: the key only brings it back (and wakes the screensaver that came meanwhile)
  if (hdmiAway) { e.preventDefault(); wake(); hdmiBack(); return; }
  if (wake()) { e.preventDefault(); return; } // a key on the screensaver only wakes up
  // a keyboard's or a speaker's volume keys, anywhere, as in the player (a Fire TV remote's go to the TV set by
  // infrared, Matinee never sees them: there the Menu key gives the volume)
  if (VOLUME_KEYS[e.key]) { e.preventDefault(); volumeStep(VOLUME_KEYS[e.key]); return; }
  if (!reader.hidden) return readerKey(e);
  if (!options.hidden) return optionsKey(e);
  if (!volumeBox.hidden) return volumeKey(e);
  // a Mac's shortcuts (Ctrl on a PC's keyboard): ⌘F the search of the part one is in, ⌘, the settings, ⌘[ and ⌘← back,
  // ⌘1 … ⌘9 the apps, ⌘+ ⌘− ⌘0 the size
  if ((e.metaKey || e.ctrlKey) && !e.altKey && SIZE_KEYS[e.key]) {
    e.preventDefault();
    if (!e.repeat) sizeKey(SIZE_KEYS[e.key]);
    return;
  }
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && SHORTCUTS[e.key]) {
    e.preventDefault();
    if (!e.repeat) SHORTCUTS[e.key]();
    return;
  }
  if ((e.key === "ArrowUp" || e.key === "ArrowDown") && el?.closest(".nowplaying .np-buttons") && bookChapter(e.key === "ArrowDown" ? 1 : -1)) {
    e.preventDefault();
    return;
  }
  if (KEYS[e.key]) {
    e.preventDefault();
    // a held arrow: one step every STEP_MS, however often the remote or keyboard repeats. A repeat that comes early
    // is taken when its time comes rather than dropped: dropped, the pace halved wherever the repeats come a little
    // faster than that (a Mac's keyboard repeats every 90 ms or so: one step every 180 ms)
    const wait = STEP_MS - (Date.now() - lastMove);
    if (e.repeat && wait > 0) {
      const dir = KEYS[e.key];
      stepTimer ??= setTimeout(() => { stepTimer = null; arrowStep(dir); }, wait);
      return;
    }
    arrowStep(KEYS[e.key]);
    return;
  }
  if (current.name === "pin" && /^[0-9]$/.test(e.key)) { e.preventDefault(); pinKey(e.key); return; } // a remote's digits
  // Backspace takes back a digit; with none typed it is Back, as everywhere (a remote's Back may send it)
  if (current.name === "pin" && e.key === "Backspace" && pinCode) { e.preventDefault(); pinKey("del"); return; }
  // The search of the part one is in: the music's on the music's pages, Jellyfin's on Jellyfin's; everything on the menu
  const search = searchHere();
  if (e.key === "BrowserSearch") { e.preventDefault(); if (search) navigate(search, { q: "" }); return; }
  // Just start typing on a keyboard: a letter opens that search from any page
  if (e.key.length === 1 && /[\p{L}\p{N}]/u.test(e.key) && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault();
    if (typing()) typeKey(e.key);
    else if (search) navigate(search, { q: e.key });
    return;
  }
  // in a search, any other character too: dots, dashes, apostrophes …
  if (typing() && e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); typeKey(e.key); return; }
  if (e.key === " " && typing()) { e.preventDefault(); typeKey(" "); return; }
  // In search, Backspace deletes letters first and goes back once the field is empty
  if (e.key === "Backspace" && typing() && current.args?.q) { e.preventDefault(); typeKey("del"); return; }
  if (OK.includes(e.key)) e.preventDefault();
  if (e.repeat) return; // a held OK/Back must not fire repeatedly
  if (OK.includes(e.key)) {
    okDown = true;
    okLong = false;
    clearTimeout(okTimer);
    okTimer = setTimeout(() => { okLong = true; openOptions(el); }, LONG_PRESS);
  }
  else if (MENU.includes(e.key)) { e.preventDefault(); openVolume(); }
  // not trusted: from the mouse (pressKey), straight back; Jellyfin's remote control's as the remote's (remoteCommand)
  else if (BACK.includes(e.key)) { e.preventDefault(); back(!e.isTrusted && !e.remote); }
  // Home and play/pause do more when held, as a remote has few keys: Home goes straight to the main menu,
  // play/pause switches the sleep timer. A short press acts when the key comes up.
  else if (HOME.includes(e.key)) { e.preventDefault(); hold("home", mainMenu); }
  else if (PLAY.includes(e.key)) { e.preventDefault(); hold("play", cycleSleep); }
  else if (TRACK_KEYS[e.key] && musicState.active) { e.preventDefault(); musicKey(TRACK_KEYS[e.key]); }
});
// ⌘V (or Ctrl+V) in a search or a sign-in field: the text from the clipboard at the caret, on one line. A server's
// address or a user name without the spaces around it; a password exactly as copied, but for line breaks.
document.addEventListener("paste", (e) => {
  if (!typing() || !reader.hidden || !options.hidden) return;
  e.preventDefault();
  typeText(e.clipboardData?.getData("text/plain") ?? "");
});
function typeText(raw) {
  const secret = current.name === "svcentry" && SVC_SECRET.has(current.args.field);
  let text = raw.replace(/[\r\n\t]+/g, secret ? "" : " ");
  if (!secret) text = text.replace(/ {2,}/g, " ").trim();
  if (current.name === "svcentry" && !secret) text = text.replace(/ /g, "");
  if (!text) return;
  shift(false);
  typeKey(text.slice(0, 500));
}
// Jellyfin's remote control (a phone, its web app's cast menu: remote_command in backend/live_events.py). Its keys go
// through the keys' own handling as the remote's do; its text is typed into the search open, as pasted, or opens the
// search of the part one is in with it, as a letter typed does; its messages show for a while.
function remoteCommand(c) {
  if (c.key) {
    // a game controller's (menu_key_loop in backend/system.py) come pressed and let go apart, so a held OK opens the
    // options and a held arrow goes on, as a remote's; Jellyfin's as one press
    for (const type of c.type ? [c.type] : ["keydown", "keyup"]) {
      const e = new KeyboardEvent(type, { key: c.key, cancelable: true, repeat: !!c.repeat });
      e.remote = true; // Back as the remote's, not the mouse's straight one
      document.dispatchEvent(e);
    }
    return;
  }
  wake();
  noteActive();
  if (c.toast) return toast(c.toast, c.ms || 5000);
  if (typeof c.text !== "string" || !reader.hidden || !options.hidden) return;
  if (typing()) return typeText(c.text);
  const search = searchHere(), text = c.text.replace(/\s+/g, " ").trim().slice(0, 500);
  if (search && text) navigate(search, { q: text });
}
// with music: next and previous song; the remote's fast forward and rewind keys do the same (rewind goes back to
// the start of the song first, like any music player); with a book 30 seconds ahead and back. Headphones' and a
// Bluetooth speaker's buttons come as these keys too (AVRCP), stop among them
const TRACK_KEYS = { MediaTrackNext: "next", MediaTrackPrevious: "prev", MediaPause: "pause",
  MediaFastForward: "next", MediaRewind: "prev", MediaStop: "stop" };
// a movie or episode under the focus: the play key plays it, otherwise it pauses the music
const jellyfinItem = (el) => el && (el.dataset.action === "play" || ["Movie", "Episode"].includes(reg[el.dataset.r]?.Type));
const held = {}; // "home" / "play" → { long, timer } while the key is down
function hold(name, onLong) {
  clearTimeout(held[name]?.timer);
  const h = (held[name] = { long: false });
  h.timer = setTimeout(() => { h.long = true; onLong(); }, LONG_PRESS);
}
function release(name) {
  const h = held[name];
  delete held[name];
  if (!h) return false;
  clearTimeout(h.timer);
  return !h.long; // a short press
}
function arrowStep(dir) {
  clearTimeout(stepTimer);
  stepTimer = null;
  lastMove = Date.now();
  const from = document.activeElement;
  move(dir);
  if (document.activeElement !== from) tick();
}

document.addEventListener("keyup", (e) => {
  if (KEYS[e.key]) { clearTimeout(stepTimer); stepTimer = null; } // let go: no step owed after it
  const el = document.activeElement?.classList.contains("f") ? document.activeElement : null;
  if (HOME.includes(e.key) && release("home")) return goHome();
  if (PLAY.includes(e.key) && release("play")) {
    if (musicState.active && !jellyfinItem(el)) musicControl("pause");
    else if (el) quickPlay(el);
    return;
  }
  if (!OK.includes(e.key) || !okDown) return;
  okDown = false;
  clearTimeout(okTimer);
  if (!okLong && el) activate(el);
});
/* ---------- A mouse or trackpad ---------- */

// The pointer shows once the mouse moves and hides again after a while still or at a key, as on a TV nothing but
// the focus is wanted. What it is over takes the focus, without the page moving (it would slip away under it); a
// click is OK, a right click opens the options of what it is on (as OK held; elsewhere it goes back), the mouse's back
// button goes back; the wheel and a two-finger swipe move the page and the row under the pointer as far as the
// fingers go, as a computer's pages do (over a box lying on the page, they step as the arrows do); over a row that
// goes on beyond the screen, ‹ and › at its ends turn a page of it. Each goes through the keys' own handling, so the
// menus, the volume, the reader and the screensaver answer to it as they do to the remote.
const POINTER_IDLE = 3000, POINTER_IDLE_OVER = 10000; // the pointer hides when still; over something to click, later
// Moving over a tile focuses it at once (its ring), but what the page tells of it (the picture and title at the top)
// only once the pointer rests on it a moment: swept across the screen, nothing in between changes the page
const HOVER_WAIT = 220;
let hoverTimer = null, pointerAt = null;
let pointerTimer, wheelSum = 0, wheelAxis = "", wheelAt = 0, wheelStep = 0;
let wheelLast = 0, wheelFalling = 0, wheelCoasting = false, wheelStepped = false;
const pointerGone = () => { clearTimeout(pointerTimer); document.body.classList.remove("pointer"); };
function pointerSeen(over) {
  document.body.classList.add("pointer");
  clearTimeout(pointerTimer);
  pointerTimer = setTimeout(pointerGone, over?.closest?.(".f, button, .opt") ? POINTER_IDLE_OVER : POINTER_IDLE);
}
// the tile under the pointer focused without moving the page; the page told of it once the pointer rests there
function hoverFocus(el) {
  if (!el || el === document.activeElement) return;
  el.focus({ preventScroll: true });
  for (const group of FOCUS_GROUPS.map((g) => el.closest(g))) if (group) group.lastFocus = el;
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => { if (document.activeElement === el) onFocus(el); }, HOVER_WAIT);
}
const pressKey = (key) => document.dispatchEvent(new KeyboardEvent("keydown", { key, cancelable: true }));
// nothing lies over the page: no options, reader, volume or screensaver
const pageFree = () => options.hidden && reader.hidden && volumeBox.hidden && saver.hidden;
// the focusable element under the pointer: on the page or the music's bar, nothing while a box lies over them
const pointed = (target) => {
  const el = target?.closest?.(".f");
  return el && (view.contains(el) || el.id === "nowbar") && pageFree() ? el : null;
};

// ‹ and › at the ends of the row the pointer is over, where it goes on beyond the screen: a page of it further, as
// the arrow keys turn one at its edge (scrollStrip), the focus on the first tile of that page (going back: the last).
// The page itself stays where it is, as with the pointer's focus.
const stripPrev = $("#stripprev"), stripNext = $("#stripnext");
stripPrev.setAttribute("aria-label", t("rowPrev"));
stripNext.setAttribute("aria-label", t("rowNext"));
let navStrip = null;
function stripEnds(strip) { // how far it is moved, how far it can be, and the room at its ends (as scrollStrip)
  const pad = parseFloat(getComputedStyle(strip).paddingLeft), last = strip.lastElementChild;
  const end = last.offsetLeft - strip.offsetLeft + last.offsetWidth + pad;
  return { now: strip.slid ?? 0, max: Math.max(0, end - strip.clientWidth), pad };
}
function showStripNav(strip) {
  navStrip = strip?.lastElementChild && !strip.matches(".menu .tiles") && view.contains(strip) && pageFree() ? strip : null;
  const ends = navStrip && stripEnds(navStrip), r = navStrip?.getBoundingClientRect();
  for (const [btn, on] of [[stripPrev, ends && ends.now > 1], [stripNext, ends && ends.now < ends.max - 1]]) {
    btn.classList.toggle("on", !!on);
    if (on) btn.style.top = `${(r.top + r.bottom) / 2}px`;
  }
}
function stripPage(dir) {
  const strip = navStrip;
  if (!strip?.isConnected) return showStripNav(null);
  const { now, pad } = stripEnds(strip), at = (el) => el.offsetLeft - strip.offsetLeft - pad;
  const tiles = [...strip.children].filter((el) => el.classList.contains("f") && el.offsetWidth);
  const el = dir > 0 ? tiles.find((el) => at(el) + el.offsetWidth + 2 * pad - strip.clientWidth > now + 1)
    : tiles.findLast((el) => at(el) < now - 1);
  if (!el) return;
  el.focus({ preventScroll: true });
  strip.lastFocus = el;
  scrollStrip(el);
  onFocus(el);
  tick();
  showStripNav(strip);
}
for (const [btn, dir] of [[stripPrev, -1], [stripNext, 1]]) {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    stripPage(dir);
  });
}
document.addEventListener("keydown", (e) => { if (e.isTrusted) pointerGone(); }, true);
document.addEventListener("mousemove", (e) => {
  pointerAt = { x: e.clientX, y: e.clientY };
  if (!e.movementX && !e.movementY) return; // the page moved under a still mouse
  pointerSeen(e.target);
  noteActive();
  if (wake()) return;
  // over ‹ or › itself: the row they belong to still
  showStripNav(e.target.closest?.(".stripnav") ? navStrip : e.target.closest?.(".strip"));
  const opt = !options.hidden && e.target.closest?.(".opt");
  if (opt && Number(opt.dataset.i) !== optionAt) {
    optionAt = Number(opt.dataset.i);
    showOption();
  }
  hoverFocus(pointed(e.target));
  fullTitle(e.target);
});
// a label cut short (… or a clamped line) tells all of itself where the pointer rests on it
function fullTitle(el) {
  if (!(el instanceof HTMLElement) || el.title || el.children.length) return;
  if (el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1) return;
  const text = el.textContent.trim();
  if (text) el.title = text;
}
document.addEventListener("mousedown", (e) => {
  pointerSeen(e.target);
  if (e.button === 3 || e.button === 4) e.preventDefault(); // not the browser's own back and forward
  // a click held no longer opens the options (a slow click on a trackpad did): the right click does
});
document.addEventListener("mouseup", (e) => {
  if (e.button === 3) { e.preventDefault(); pressKey("Escape"); }
});
document.addEventListener("click", (e) => {
  showStripNav(null); // the page may change: they come again as the mouse moves
  if (!saver.hidden || !volumeBox.hidden) return pressKey("Escape"); // either closes, as with any key
  const el = pointed(e.target);
  if (!el) return;
  focusEl(el);
  activate(el);
});
// the back button that shows with the pointer: Back, as the remote's
const mouseBack = $("#mouseback");
mouseBack.setAttribute("aria-label", t("back"));
mouseBack.addEventListener("click", (e) => {
  e.stopPropagation();
  pressKey("Escape");
});
document.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  // a right click is Back; the Menu key brings one too (Chromium on Linux, button 0): that key is the volume
  // (keydown), which this would close again at once
  if (e.button !== 2) return;
  const el = pointed(e.target);
  if (el && pageFree()) { focusEl(el, false); openOptions(el); }
  else pressKey("Escape");
});
// The wheel and a swipe over the page (nothing lying over it): the page, or sideways the row, under the pointer moves
// as far as the fingers go (a trackpad's coasting too), a mouse's notch gliding there; once it stands, the tile under
// the pointer has the focus (a long grid goes on loading from there). False where it steps instead: the main menu,
// the programme guide, the keyboard of a search, a box over the page, a page that does not move. "own" where a box
// scrolls by itself (a film's page, a list of songs): the browser's own scrolling, not held back (a mouse's quick
// notches, each a smooth scroll of ours, began anew every few ms and the page stood the first tenth of a second).
const WHEEL_LINE = 40, WHEEL_SETTLE = 140; // a line of a wheel that counts lines, in pixels; ms until it stands
let wheelSettle = null;
const wheeling = new Set();
function wheelMove(el, transform, glide) {
  if (!glide) { el.classList.add("wheeling"); wheeling.add(el); } // following the fingers: no glide behind them
  el.style.transform = transform;
}
const movedY = (el) => -(parseFloat(/translate3d\([^,]+,\s*(-?[\d.]+)px/.exec(el.style.transform)?.[1]) || 0);
function moveEnd(box) {
  if (box.matches(".rows-track")) return trackEnd(box);
  if (box.matches(".np-queue-track")) return Math.max(0, box.scrollHeight - box.parentElement.clientHeight);
  return Math.max(0, box.offsetTop + box.scrollHeight - innerHeight); // a grid or detail page, as slidePage
}
function scrollable(el) { // the nearest box that scrolls by itself (a list of songs), the page's own view last
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const y = getComputedStyle(n).overflowY;
    if ((y === "auto" || y === "scroll") && n.scrollHeight > n.clientHeight + 1) return n;
  }
  return null;
}
function freeScroll(e) {
  const t = e.target;
  if (!pageFree() || current.name === "menu" || !view.contains(t) || t.closest?.(".epg, .keys")) return false;
  const unit = e.deltaMode === 1 ? WHEEL_LINE : e.deltaMode === 2 ? innerHeight : 1;
  let dx = e.deltaX * unit, dy = e.deltaY * unit;
  if (e.shiftKey && !dx) [dx, dy] = [dy, 0]; // a mouse's wheel with shift: sideways
  const glide = Math.max(Math.abs(dx), Math.abs(dy)) >= 50; // a notch, not a trackpad's small steps
  let native = false;
  if (Math.abs(dx) > Math.abs(dy)) {
    const strip = t.closest?.(".strip");
    if (strip && !strip.matches(".menu .tiles") && strip.lastElementChild) {
      const { now, max } = stripEnds(strip), x = Math.round(Math.min(max, Math.max(0, now + dx)));
      if (x !== now) { strip.slid = x; wheelMove(strip, `translate3d(${-x}px,0,0)`, glide); }
      showStripNav(strip);
    }
  } else {
    const box = t.closest?.(".np-queue-track, .rows-track, .grid-page, .moving") ?? view.querySelector(".rows-track, .grid-page, .moving");
    if (box) {
      const now = movedY(box), y = Math.round(Math.min(moveEnd(box), Math.max(0, now + dy)));
      if (y !== now) wheelMove(box, `translate3d(0,${-y}px,0)`, glide);
    } else {
      if (!scrollable(t)) return false;
      native = true;
    }
    showStripNav(t.closest?.(".strip"));
  }
  clearTimeout(wheelSettle);
  wheelSettle = setTimeout(() => {
    for (const el of wheeling) el.classList.remove("wheeling");
    wheeling.clear();
    if (pointerAt) hoverFocus(pointed(document.elementFromPoint(pointerAt.x, pointerAt.y)));
  }, WHEEL_SETTLE);
  return native ? "own" : true;
}

// Elsewhere a notch of a wheel is one step; a trackpad's many small ones add up to one, at the pace of a held arrow key. A
// trackpad swiped and let go goes on by itself for a second or two (the coasting a Mac adds, ever smaller steps): that
// is no swipe of its own, so the focus does not run on far past where the fingers left it
const WHEEL_GAP = 150; // ms without a step: a new swipe
document.addEventListener("wheel", (e) => {
  pointerSeen(e.target);
  pointerAt = { x: e.clientX, y: e.clientY };
  // a trackpad's pinch (and Ctrl with the wheel): the browser's zoom of the whole page, which Settings → Size does
  // instead; not a scroll either
  if (e.ctrlKey) return e.preventDefault();
  const free = freeScroll(e);
  if (free !== "own") e.preventDefault();
  if (free) return;
  const axis = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? "x" : "y", now = Date.now();
  const delta = axis === "x" ? e.deltaX : e.deltaY, size = Math.abs(delta);
  if (axis !== wheelAxis || now - wheelAt > WHEEL_GAP) { // a new swipe or notch
    wheelSum = 0;
    wheelFalling = 0;
    wheelCoasting = wheelStepped = false;
  } else if (size < wheelLast) {
    // smaller and smaller after a step: the coasting (four in a row, as no hand slows that evenly)
    if (++wheelFalling >= 4 && wheelStepped) wheelCoasting = true;
  } else if (size > wheelLast * 1.3 + 1) { // larger again: the fingers swipe once more
    wheelFalling = 0;
    wheelCoasting = false;
  }
  wheelAxis = axis;
  wheelAt = now;
  wheelLast = size;
  if (wheelCoasting) return;
  wheelSum += delta;
  if (Math.abs(wheelSum) < 40 || now - wheelStep < 110) return;
  wheelStep = now;
  wheelStepped = true;
  pressKey(axis === "x" ? (wheelSum > 0 ? "ArrowRight" : "ArrowLeft") : (wheelSum > 0 ? "ArrowDown" : "ArrowUp"));
  wheelSum = 0;
  showStripNav(axis === "x" ? navStrip : null); // sideways the row stays where it is; up and down it moves away
}, { passive: false });
