"use strict";
// Matinee's page, part 2 of 18: the pictures behind the pages: the stage, the walls of covers, the main menu's backdrop.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

// The picture at the top of the main menu and of Jellyfin's start, over the blurred one: the backdrop of what is
// focused, sharp, cross-faded. Navidrome and Audiobookshelf have only square covers: a wall of them instead
// (wall: their addresses, key: which wall). own: a film's or show's own picture on its page or Jellyfin's start,
// shown with every choice of effects; so is one title's cover or picture card (shape cover or card): what is
// focused, not decoration. The walls and a page's picture across it are effects: not with Reduced (the class plain)
let menuArtUrl = null, menuArtItem = null;
// a picture at the top that did not come (the network not up yet after switching on): asked again a few times, as
// long as the same one is still wanted
const STAGE_AGAIN = [3, 6, 12, 20, 30, 45, 60], stageTries = new Map();
function stageAgain(box, key, again) {
  const n = stageTries.get(key) ?? 0;
  if (n >= STAGE_AGAIN.length) return;
  stageTries.set(key, n + 1);
  setTimeout(() => {
    if (!box.isConnected || box.dataset.url !== key) return; // something else wanted meanwhile
    delete box.dataset.url;
    again();
  }, STAGE_AGAIN[n] * 1000);
}
async function stageArt(url, wall, shape = "square", own = false) {
  own ||= !wall?.length && (shape === "cover" || shape === "card");
  const box = $("#stage-art");
  box?.classList.toggle("own", own);
  if (!own && document.documentElement.classList.contains("plain")) { // effects Reduced or Calm: only the blurred backdrop
    if (box) { box.replaceChildren(); delete box.dataset.url; } // nor one still loading from before (see below)
    return;
  }
  const key = wall ? `wall:${url}` : shape !== "square" ? `${shape}:${url}` : url ?? "";
  if (!box || box.dataset.url === key) return;
  box.dataset.url = key;
  let pic = null;
  if (wall?.length) {
    // behind the tiles and rows: theirs first (a browser keeps six connections to Matinee's backend)
    const imgs = wall.map((src) => Object.assign(new Image(), { src, decoding: "async", fetchPriority: "low" }));
    // shown once most covers are there (or after a moment), so it does not come up piece by piece
    // (public TV's pictures, fetched from the broadcasters' pages, take longer: waited for longer)
    const slow = wall.some((u) => u.startsWith("/tv/picture"));
    const decoded = Promise.allSettled(imgs.map((i) => i.decode()));
    await Promise.race([decoded, new Promise((r) => setTimeout(r, slow ? 6000 : 1500))]);
    if (box.dataset.url !== key || !box.isConnected) return;
    if (!imgs.some((i) => i.complete && i.naturalWidth)) return stageAgain(box, key, () => stageArt(url, wall, shape, own)); // none came
    pic = wallCanvas(imgs, shape, box);
    // the ones still coming: drawn in once they are all there
    if (imgs.some((i) => !i.complete)) decoded.then(() => pic.isConnected && wallCanvas(imgs, shape, box, pic));
  } else if (url && (shape === "cover" || shape === "card")) { // one title's cover, or a video's picture as a card (style.css)
    const img = new Image();
    img.src = url;
    try { await img.decode(); } catch { return stageAgain(box, key, () => stageArt(url, wall, shape, own)); }
    pic = document.createElement("div");
    pic.className = shape === "card" ? "cover-art card16" : "cover-art";
    pic.append(img);
  } else if (url && !wall) {
    pic = new Image();
    pic.src = url;
    try { await pic.decode(); } catch { return stageAgain(box, key, () => stageArt(url, wall, shape, own)); }
  }
  if (pic) {
    if (box.dataset.url !== key || !box.isConnected) return;
    stageTries.delete(key);
    box.append(pic);
    if (pic.classList.contains("cover-art")) fitCoverArt(pic);
    void pic.offsetWidth; // commit the start state so the cross-fade runs
    pic.classList.add("on");
  }
  box.classList.toggle("walled", !!(pic && wall?.length)); // no veil over a wall (style.css)
  for (const old of [...box.children]) {
    if (old === pic) continue;
    old.classList.remove("on");
    setTimeout(() => old.remove(), 900);
  }
}

// One title's cover or picture card at the top right ends above what stands below it, the menu's shelf or a start's
// rows, with a gap. They are measured by the screen's width, the card by its height: on a screen taller than a TV's (a
// laptop's 16:10) the shelf reached up over it. Its room goes to style.css as --room; the shelf's and rows' places as
// laid out, without their gliding (offsetTop leaves transforms out).
function fitCoverArt(pic = [...document.querySelectorAll("#stage-art .cover-art")].pop()) {
  if (!pic) return;
  const below = view.querySelector(".menu .shelf > :first-child, .home .rows");
  if (!below) return pic.style.removeProperty("--room");
  let top = 0;
  for (let el = below; el; el = el.offsetParent) top += el.offsetTop;
  const gap = 1.25 * parseFloat(getComputedStyle(document.documentElement).fontSize); // --sp-5
  pic.style.setProperty("--room", `${Math.max(0, top - pic.offsetTop - gap)}px`);
}
addEventListener("resize", () => fitCoverArt());

// A covers' wall drawn once into a canvas, blurred there: gliding, it is one finished picture the graphics chip only
// moves, where 66 pictures under a blur were blurred anew for every frame. Laid out as a slanted grid (style.css):
// square covers; wide: videos' pictures (16:9) and poster: posters (2:3), in their own numbers of columns. A cover
// the server cannot deliver is left out, the others move up.
const WALL_COLUMNS = { square: 11, poster: 13, wide: 7 }, WALL_ASPECT = { square: 1, poster: 2 / 3, wide: 16 / 9 };
function wallCanvas(imgs, shape, box, canvas = document.createElement("canvas")) {
  const vw = innerWidth / 100, vh = innerHeight / 100, n = WALL_COLUMNS[shape] ?? 11, gap = 0.7 * vw;
  const w = box.clientWidth + 12 * vw, cw = (w - (n - 1) * gap) / n, ch = cw / (WALL_ASPECT[shape] ?? 1);
  const ok = imgs.filter((i) => i.complete && i.naturalWidth);
  const rows = Math.ceil(ok.length / n), h = Math.max(rows * ch + (rows - 1) * gap, 1);
  const css = getComputedStyle(document.documentElement);
  const blur = parseFloat(css.getPropertyValue("--wall-blur")) || 0;
  const radius = parseFloat(css.getPropertyValue("--r-sm")) * parseFloat(css.fontSize) || 0;
  canvas.width = Math.round(w);
  canvas.height = Math.round(h);
  canvas.className = `wall ${shape}${canvas.classList.contains("on") ? " on" : ""}`;
  // turned about the middle of the screen's part of it, as the grid it replaces (12vh above and below the box)
  canvas.style.cssText = `width:${w}px;height:${h}px;transform-origin:50% ${(box.clientHeight + 24 * vh) / 2}px`;
  const ctx = canvas.getContext("2d");
  ctx.filter = blur ? `blur(${blur}px)` : "none";
  ok.forEach((img, i) => {
    const x = (i % n) * (cw + gap), y = Math.floor(i / n) * (ch + gap);
    // cut to fill its place, as object-fit: cover
    const scale = Math.max(cw / img.naturalWidth, ch / img.naturalHeight), sw = cw / scale, sh = ch / scale;
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, cw, ch, radius);
    ctx.clip();
    ctx.drawImage(img, (img.naturalWidth - sw) / 2, (img.naturalHeight - sh) / 2, sw, sh, x, y, cw, ch);
    ctx.restore();
  });
  return canvas;
}

// The top of the main menu: the name of what its picture shows, as its logo where Jellyfin has one
function stageTitle(it) {
  const box = $("#stage-title");
  if (!box) return;
  const name = it ? (it.Type === "Episode" ? it.SeriesName : it.Name ?? it.title ?? it.name ?? "") : "";
  const logo = it?.Id && logoOf(it);
  const html = logo ? `<img class="logo" src="${logo}" decoding="async" alt="${esc(name)}">`
    : name ? `<h1 class="title">${esc(name)}</h1>` : "";
  if (box.dataset.html === html) return;
  box.dataset.html = html;
  box.innerHTML = html;
  box.classList.remove("shown");
  void box.offsetWidth; // fade in again for each
  box.classList.add("shown");
}

// The wall above a tile of the music's or the audiobooks' start: its own covers (addresses), the wall of all as long
// as it has none of its own; cross-faded as the focus goes from tile to tile, as Jellyfin's pictures are
let homeWall = null; // {key, urls}: the wall of all, for the tiles without covers of their own
// A detail page's wall behind its head (an album: the artist's albums, a playlist: its songs' covers, an artist: its
// albums), gliding like Jellyfin's pictures; none with too few covers, as a handful repeated looks like a pattern
function pageWall(key, ids, shape = "square") {
  // an address (Jellyfin's at a wall tile's width, not the screen's), or a cover's id
  let urls = [...new Set(ids.filter(Boolean))].slice(0, WALL_UNIQUE).map((id) => (/^(\/|https?:)/.test(id) ? id.replace(/maxWidth=\d+/, `maxWidth=${tileWidth(0.15)}`)
    : cover(id, tileWidth(0.1))));
  if (urls.length < 6) return false;
  while (urls.length < WALL_SIZE) urls = [...urls, ...urls].slice(0, WALL_SIZE);
  stageArt(key, urls, shape);
  return true;
}

function tileWall(it) {
  const own = (it?.wall ?? []).filter(Boolean);
  if (own.length < 6) return homeWall && stageArt(homeWall.key, homeWall.urls); // a handful repeated looks like a pattern
  let urls = [...new Set(own)].slice(0, WALL_UNIQUE);
  while (urls.length < WALL_SIZE) urls = [...urls, ...urls].slice(0, WALL_SIZE);
  stageArt(`${current.name}-${it.Id}`, urls);
}

// The main menu's top for each part: a gliding wall of its pictures, as on the music's start (posters of Jellyfin,
// covers of the music, the books and the podcasts); nothing where too few, never one title
const WALL_SHAPE = { jellyfin: "poster", music: "square", books: "square", podcasts: "square" };
function menuWall(id, el) {
  coverWall(id).then((urls) => {
    if (document.activeElement !== el) return;
    stageTitle(null);
    stageArt(urls.length ? id : null, urls.length ? urls : null, WALL_SHAPE[id]); // too few for a wall: nothing, not one title
  });
}

// Up Next's tile: a gliding wall of what was begun, as the other tiles' walls (one title large, then the same as a
// card on the shelf, was the same picture twice in two ways); too few for a wall: nothing
function nextArt(el) {
  stageTitle(null);
  shelfData("next").then((d) => {
    if (document.activeElement !== el) return;
    const items = d?.items ?? [];
    const picture = (x) => (x.kind === "abs" ? bookCover(x.it, tileWidth(0.1)) : x.kind === "pf" ? podCover(x.it.podcast) : landOf(x.it));
    const urls = [...new Set(items.map(picture).filter(Boolean))];
    if (urls.length >= 6) return stageArt("next", wallOrder(urls, WALL_COLUMNS.wide), "wide");
    stageArt(null); // too few for a wall: Up Next is a row, not its first title
  });
}

// The covers of a wall: random albums, the books added last; kept for a while, so going along the tiles is quick
const WALL_SIZE = 66, WALL_UNIQUE = 24, wallCache = new Map();
// A wall of fewer pictures than places: each place takes one from a shuffled round of them, never the one beside it, above
// it or above it at a slant (the wall is slanted: those are neighbours too). The list repeated as it was put the same
// picture in a column wherever its length met the number of columns (seven begun on Up Next's seven: stripes).
function wallOrder(urls, cols) {
  const out = [];
  let round = [];
  for (let i = 0; i < WALL_SIZE && urls.length; i++) {
    const col = i % cols, near = new Set([col > 0 && out[i - 1], out[i - cols], col > 0 && out[i - cols - 1], col < cols - 1 && out[i - cols + 1]]);
    if (!round.length) round = shuffled(urls);
    let k = round.findIndex((u) => !near.has(u));
    if (k < 0) { // none of this round's rest fits: one from a fresh round, at worst any but the one beside it
      const fits = shuffled(urls).find((u) => !near.has(u)) ?? urls.find((u) => u !== out[i - 1]) ?? urls[0];
      out.push(fits);
      continue;
    }
    out.push(round.splice(k, 1)[0]);
  }
  return out;
}
async function coverWall(id) {
  const hit = wallCache.get(id);
  if (hit && Date.now() - hit.at < 10 * 60000) return hit.urls;
  const size = tileWidth(0.1);
  let urls = [];
  if (id === "music") {
    const r = await getJson(`/nd/getAlbumList2?type=random&size=${WALL_SIZE}`);
    urls = (r?.albumList2?.album ?? []).map((a) => cover(a.coverArt, size)).filter(Boolean);
  } else if (id === "jellyfin") { // posters of the library, at random
    const s = await getJson("/session");
    const r = s?.userId && await getJson(`/jf/Items?userId=${s.userId}&Recursive=true&IncludeItemTypes=Movie,Series&ImageTypes=Primary`
      + `&SortBy=Random&Limit=${WALL_SIZE}&${FIELDS}`);
    urls = (r?.Items ?? []).map(posterOf).filter(Boolean).map((u) => u.replace(/maxWidth=\d+/, `maxWidth=${size}`));
  } else if (id === "podcasts") { // the podcasts' pictures
    const r = await getJson("/pf/podcasts");
    urls = shuffled((r?.podcasts ?? []).filter((p) => p.cover)).map((p) => podCover(p.id));
  } else {
    const libs = (await getJson("/abs/api/libraries"))?.libraries ?? [];
    const lists = await Promise.all(libs.map((l) => getJson(`/abs/api/libraries/${l.id}/items?limit=${WALL_SIZE}&sort=addedAt&desc=1&minified=1`)));
    urls = shuffled(books(lists.flatMap((x) => x?.results ?? [])).filter((b) => b.cover)).slice(0, WALL_SIZE).map((b) => bookCover(b, size));
  }
  if (urls.length < 6) urls = []; // a handful repeated looks like a pattern: the part's picture instead
  urls = urls.slice(0, WALL_UNIQUE); // repeated across the slanted wall it does not show, and loads a third as much
  // a small library fills the wall all the same, no picture beside itself
  if (urls.length < WALL_SIZE) urls = wallOrder(urls, WALL_COLUMNS[WALL_SHAPE[id]] ?? WALL_COLUMNS.square);
  if (urls.length) wallCache.set(id, { at: Date.now(), urls }); // none: asked again next time (the network just after switching on)
  return urls;
}

// The main menu's picture is one that pops: strong colours and light against dark, as a red-lit face on black, not a
// grey street. Of sixty at random, each is judged by its BlurHash (Jellyfin sends one with every picture: a few
// dozen characters, its colours as a coarse sketch), so none has to be loaded for it; one of the best few is taken,
// once, and kept from then on: the menu does not change its picture each time one comes back to it. Without
// BlurHashes (an older server) the first, as before.
const POP_CHOICE = 60, POP_FEW = 3;
const B83 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~";
const b83 = (str) => [...str].reduce((v, c) => v * 83 + B83.indexOf(c), 0);
const toLinear = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const toSrgb = (v) => { v = Math.max(0, Math.min(1, v)); return 255 * (v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055); };
// the sketch's pixels (w × h, sRGB 0…255), or null for a hash that is not one
function blurPixels(hash, w = 8, h = 5) {
  if (!hash || hash.length < 6) return null;
  const size = b83(hash[0]), nx = (size % 9) + 1, ny = Math.floor(size / 9) + 1;
  if (hash.length !== 4 + 2 * nx * ny) return null;
  const max = (b83(hash[1]) + 1) / 166, dc = b83(hash.slice(2, 6));
  const colors = [[toLinear(dc >> 16), toLinear((dc >> 8) & 255), toLinear(dc & 255)]];
  const ac = (q) => Math.sign(q - 9) * ((q - 9) / 9) ** 2 * max;
  for (let i = 1; i < nx * ny; i++) {
    const v = b83(hash.slice(4 + i * 2, 6 + i * 2));
    colors.push([ac(Math.floor(v / 361)), ac(Math.floor(v / 19) % 19), ac(v % 19)]);
  }
  const px = [];
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const c = [0, 0, 0];
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const f = Math.cos((Math.PI * x * i) / w) * Math.cos((Math.PI * y * j) / h), k = colors[j * nx + i];
      c[0] += k[0] * f; c[1] += k[1] * f; c[2] += k[2] * f;
    }
    px.push(c.map(toSrgb));
  }
  return px;
}
// how much a picture pops, as a red-lit face on black: one strong colour (its brightest colours' strength, and how much
// they are of one hue) glowing out of the dark (a good share of it near black). Many colours in patches, or no dark,
// make the blurred backdrop a muddy blotch, however colourful the picture itself
function popScore(hash) {
  const px = blurPixels(hash, 16, 9);
  if (!px) return -1;
  const light = px.map(([r, g, b]) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255);
  const chroma = px.map((c) => (Math.max(...c) - Math.min(...c)) / 255);
  const strong = [...chroma].sort((a, b) => a - b)[Math.floor(0.9 * (chroma.length - 1))];
  const dark = light.filter((l) => l < 0.12).length / light.length;
  let x = 0, y = 0, sum = 0; // the hues of its colourful parts, weighed by their strength: how much they agree
  px.forEach(([r, g, b], i) => {
    const c = chroma[i], max = Math.max(r, g, b);
    if (c < 0.25) return;
    const d = c * 255, h = (Math.PI / 3) * (max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4);
    x += Math.cos(h) * c; y += Math.sin(h) * c; sum += c;
  });
  return strong * Math.min(dark / 0.45, 1) * (sum ? Math.hypot(x, y) / sum : 0);
}
function poppingOf(items, few = POP_FEW) {
  const scored = items.map((it) => ({ it, score: popScore(Object.values(it.ImageBlurHashes?.Backdrop ?? {})[0]) }))
    .filter((x) => x.score >= 0).sort((a, b) => b.score - a.score);
  if (!scored.length) return items[0];
  return scored[Math.floor(Math.random() * Math.min(few, scored.length))].it;
}

// Behind the main menu's glass: the cover of the music playing, otherwise a picture from the library
let menuPick = null; // the menu's picture, chosen once
let MENU_PICTURE = null; // or the one set in config.json (menupicture: a Jellyfin title's id), always that one
async function menuBackdrop(gen) {
  const song = musicState.active && musicState.queue?.[musicState.index];
  if (song && songCover(song, 1)) return setBackdrop(songCover(song, 1920), MENU_SOFT);
  if (MENU_PICTURE) {
    const url = `/jf/Items/${encodeURIComponent(MENU_PICTURE)}/Images/Backdrop?maxWidth=1920&quality=90`;
    menuArtUrl ||= url;
    return setBackdrop(url, MENU_SOFT);
  }
  if (menuPick) return setBackdrop(backdropOf(menuPick), MENU_SOFT);
  const s = await fetch("/session").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!s || gen !== renderGen) return;
  const r = await fetch(`/jf/Items?userId=${s.userId}&Recursive=true&IncludeItemTypes=Movie,Series&ImageTypes=Backdrop`
    + `&SortBy=Random&Limit=${POP_CHOICE}&EnableImageTypes=Backdrop&ImageTypeLimit=1`).then((x) => (x.ok ? x.json() : null)).catch(() => null);
  const it = poppingOf(r?.Items ?? []);
  if (it) menuPick = it;
  if (it && gen === renderGen) {
    setBackdrop(backdropOf(it), MENU_SOFT);
    if (!menuArtUrl) [menuArtUrl, menuArtItem] = [backdropOf(it), it];
    if (document.activeElement?.dataset.k === "menu-jellyfin") menuWall("jellyfin", document.activeElement);
  }
}
