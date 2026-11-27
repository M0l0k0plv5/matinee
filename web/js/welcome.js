/* ---------- The first start: language, network, Jellyfin, remote, in that order ---------- */

// A new device (no Jellyfin server yet, never through this) starts here instead of the main menu: each step one
// page, Back to the one before, every step but the language can be left for later. Settings → System runs it again.
const WELCOME_STEPS = ["lang", "net", "jellyfin", "remote", "done"];
let welcomeInfo = null; // what the device has (Wi-Fi, Bluetooth), asked once per run

async function welcomeNext(step) {
  welcomeInfo ??= await getJson("/settings");
  let i = WELCOME_STEPS.indexOf(step) + 1;
  // nothing to do on a step this device has no part for: no Bluetooth, no remote to pair
  while (WELCOME_STEPS[i] === "remote" && !welcomeInfo?.bluetooth) i++;
  navigate("welcome", { step: WELCOME_STEPS[i] });
}

async function welcomeLang(lang) {
  if (lang === LANG) return welcomeNext("lang");
  const r = await fetch("/settings", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ language: lang }) }).catch(() => null);
  if (!r?.ok) return toast(t("saveFailed"), 4000);
  // the whole UI is built in one language: again, on the next step
  try { sessionStorage.setItem("kino-open", "welcome:net"); } catch {}
  location.reload();
}

async function welcomeDone() {
  await fetch("/welcome/done", { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => {});
  stack = [];
  render({ name: "menu" }, "menu-jellyfin");
}

Object.assign(views, {
  async welcome({ step = "lang" } = {}) {
    welcomeInfo = step === "lang" ? null : welcomeInfo;
    const n = WELCOME_STEPS.indexOf(step) + 1;
    const dots = `<div class="welcome-steps">${WELCOME_STEPS.map((_, i) => `<i${i < n ? ' class="on"' : ""}></i>`).join("")}</div>`;
    const next = (label = t("welcomeNext"), hint = "") => srow("welcome-next", label, "", `data-action="welcomenext" data-val="${step}"`, hint);
    const page = (icon, title, text, rows) => {
      view.innerHTML = `${settingsSide(icon, text)}<div class="grid-page settings welcome" data-step="${step}">${dots}<h1>${esc(title)}</h1>
        <p class="jf-msg" id="jf-msg">${esc(text)}</p>${rows}</div>`;
    };
    if (step === "lang") {
      // both languages at once: whoever reads neither yet finds theirs
      return page(KINO_LOGO, "Willkommen bei Matinee · Welcome to Matinee", t("welcomeLangHint"), `<div class="slist">${[["de", "Deutsch"], ["en", "English"]]
        .map(([v, name]) => srow(`welcome-${v}`, name, v === LANG ? "✓" : "", `data-action="welcomelang" data-val="${v}"${v === LANG ? " data-autofocus" : ""}`)).join("")}</div>`);
    }
    if (step === "net") {
      const [net, wifi] = await Promise.all([getJson("/network"), getJson("/wifi")]);
      const ssid = wifi?.networks?.find((x) => x.active)?.ssid;
      const state = net?.up ? (ssid ? t("welcomeOnWifi", { name: ssid }) : t("welcomeOnline")) : t("err_no_network");
      return page(ICON_WIFI, t("welcomeNet"), state, `<div class="slist">
        ${wifi?.available ? srow("welcome-wifi", t("wifi"), esc(ssid ?? t("wifiNotConnected")), `data-action="wifi"${net?.up ? "" : " data-autofocus"}`, t("wifiHint")) : ""}
        ${next(net?.up ? t("welcomeNext") : t("welcomeLater"))}</div>`);
    }
    if (step === "jellyfin") {
      const s = await fetch("/session").then((r) => r.json()).catch(() => ({}));
      if (s.userId) { // signed in (just now, or before): on to the next step by itself
        USER = s.userId;
        return page(ICON_JELLYFIN, partName("jellyfin"), t("svcDone", { name: partName("jellyfin") }),
          `<div class="slist">${next(t("welcomeNext"), "")}</div>`);
      }
      page(ICON_JELLYFIN, partName("jellyfin"), t("welcomeJfHint"), `${jfForm(s)}<div id="jf-found"></div><div class="slist">
        ${srow("jf-transfer", t("transferIn"), "", `data-action="transferin"`, t("transferInHint"))}${next(t("welcomeLater"), t("welcomeLaterHint"))}</div>`);
      return void jfFound(renderGen);
    }
    if (step === "remote") {
      return page(G.remote, t("welcomeRemote"), t("welcomeRemoteHint"), `<div class="slist">
        ${srow("welcome-pair", t("setRemotes"), "", `data-action="bluetooth"`, t("btHint"))}${next()}</div>`);
    }
    page(KINO_LOGO, t("welcomeReady"), t("welcomeReadyHint"), `<div class="slist">${srow("welcome-start", t("welcomeStart"), "", 'data-action="welcomedone" data-autofocus')}</div>`);
  },
});
