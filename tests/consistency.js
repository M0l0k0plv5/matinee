// What tests/test_consistency.py looks for on each page: see there.
(() => {
  const out = [];
  const vis = (e) => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth && cs.visibility !== "hidden" && cs.opacity !== "0"; };
  const name = (e) => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (typeof e.className === "string" && e.className.trim() ? "." + e.className.trim().split(/\s+/).join(".") : "");
  const path = (e) => { const p = []; for (let x = e; x && x !== document.body && p.length < 3; x = x.parentElement) p.unshift(name(x)); return p.join(" > "); };
  const root = parseFloat(getComputedStyle(document.documentElement).fontSize);
  // the type scale (tokens.css), times the setting "textsize"; 5rem stands outside it
  const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--t-scale")) || 1;
  const sizes = [5 / scale, 4.5, 3.2, 2.4, 1.6, 1.35, 1.15, 1.12, 1, 0.86].map((r) => r * root * scale);
  const onScale = (px) => sizes.some((s) => Math.abs(s - px) < 0.15);
  const texts = [...document.querySelectorAll("body *")].filter((e) => !e.closest("svg") && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && vis(e));
  for (const e of texts) {
    const cs = getComputedStyle(e), fs = parseFloat(cs.fontSize).toFixed(1), txt = e.textContent.trim().slice(0, 30);
    if (!onScale(parseFloat(cs.fontSize))) out.push(["size", fs + "px", path(e), txt]);
    if (/^H[1-6]$/.test(e.tagName) && cs.fontWeight === "700") out.push(["bold-heading", path(e), txt]);
    if (/times|serif$/i.test(cs.fontFamily) && !/sans/i.test(cs.fontFamily)) out.push(["font", cs.fontFamily, path(e)]);
    const clamp = cs.webkitLineClamp && cs.webkitLineClamp !== "none";
    if (e.scrollWidth > e.clientWidth + 2 && cs.overflowX !== "visible" && cs.textOverflow !== "ellipsis" && !clamp) out.push(["cut-x", path(e), txt]);
    if (e.scrollHeight > e.clientHeight + 3 && cs.overflowY === "hidden" && !clamp && e.clientHeight > 0) out.push(["cut-y", path(e), txt]);
    const r = e.getBoundingClientRect();
    if (r.right > innerWidth + 1 && !e.closest(".strip, .rows-track, .tabbar")) out.push(["off-screen", path(e), txt]);
  }
  for (const img of document.querySelectorAll("img")) if (vis(img) && img.complete && img.naturalWidth === 0) out.push(["broken-img", path(img), img.src.slice(0, 80)]);
  return out;
})()
