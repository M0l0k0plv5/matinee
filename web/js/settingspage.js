"use strict";
// Matinee's page, part 5 of 18: the settings page itself, and the pages it opens: a service's field, the transfer of
// an account, the place for the weather, the library check, subtitles, HDMI, Wi-Fi and Bluetooth.
// The parts are classic scripts loaded in order (index.html): they share one global scope, as one file would.

Object.assign(views, {
  // Settings: from the main menu what concerns everything; from Jellyfin, the music or the audiobooks only theirs
  // The settings: a list, each line its name and on the right what it is set to. OK opens the choice (or
  // does what the line says); what a line means stands beside the list while it has the focus.
  async settings({ part: which = "general" } = {}) {
    const [s, ndS, absS, pfS] = await Promise.all([fetch("/settings").then((r) => r.json()), musicSession(true), bookSession(true), podSession(true)]);
    SIMPLE = !!s.simple;
    if (SIMPLE && !SIMPLE_PARTS.has(which)) which = "general"; // the apps' own, the account: not with the simple settings
    settingChoices = {};
    const line = (key, name, value, attrs, hint = "") =>
      `<div class="srow f" tabindex="-1" data-k="${esc(key)}" ${attrs} data-hint="${esc(hint)}"><span class="sname">${esc(name)}</span><span class="sval">${value}</span></div>`;
    const choice = (opt, name, opts, hint) => {
      settingChoices[opt] = { name, opts };
      const now = opts.find(([v]) => v === settingValue(s, opt))?.[1] ?? "–";
      return line(`set-${opt}`, name, esc(now), `data-action="choose" data-opt="${opt}"`, hint);
    };
    // the age limit: the highest age shown (backend/age_limit.py)
    const ages = [["off", t("off")], ["0", t("ageAll")], ...["6", "12", "16"].map((n) => [n, t("ageUpTo", { n })])];
    const quality = [["auto", t("qualityAuto")], ["original", t("qualityOriginal")],
      ...["20", "8", "4", "2"].map((n) => [n, t("mbit", { n })])];
    const saverOpts = [["off", t("off")], ...["5", "10", "30"].map((n) => [n, t("minutes", { m: n })])];
    const askOpts = [["off", t("off")], ...["2", "3", "5"].map((n) => [n, t("afterEpisodes", { n })])];
    const audioOpts = [["auto", t("audioAuto")], ["de", "Deutsch"], ["en", "English"], ["fr", "Français"], ["es", "Español"], ["nl", "Nederlands"], ["original", t("audioOriginal")]];
    const subsOpts = [["off", t("subsOff")], ["auto", t("subsAuto")], ["always", t("subsAlways")]];
    const sublangOpts = [["auto", t("sublangAuto")], ["de", "Deutsch"], ["en", "English"], ["fr", "Français"], ["es", "Español"], ["nl", "Nederlands"]];
    // only on the TV computer (in cage), where Matinee has the screen to itself
    const displayOpts = s.displayModes?.length && [["auto", t("displayAuto")], ["native", t("displayNative")],
      ...s.displayModes.map((m) => [m, m.replace("x", "×").replace("@", " · ") + " Hz"])];
    const part = (title, body) => (body.trim() ? `${title ? `<h2 class="settings-part">${esc(title)}</h2>` : ""}<div class="slist">${body}</div>` : "");
    const about = (rows) => `<dl class="about">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v ?? "–")}</dd>`).join("")}</dl>`;
    const toGeneral = `<div class="slist">${line("set-general", t("allSettings"), "", `data-action="settingspart" data-val="general"`, t("allSettingsHint"))}</div>`;
    // a line that opens a part of the settings (val: its part), showing what is set there in short
    const open = (val, name, summary, hint, action = "settingspart") =>
      line(`part-${val}`, name, esc(summary), `data-action="${action}" data-val="${val}"`, hint);
    const now = (opt, opts) => opts.find(([v]) => v === settingValue(s, opt))?.[1] ?? "";
    // the sound output in use (a Bluetooth speaker by its name): its sound earlier by so much
    const syncOpts = [["0", t("off")], ...["50", "100", "150", "200", "250", "300"].map((n) => [n, t("audioSyncEarlier", { n })])];
    const syncName = s.audiosync?.id === "default" ? t("audioSyncHere") : s.audiosync?.name || t("audioSyncHere");
    const parts = {
      // the parts, each with what matters most in it: what is changed most often first, the account and the system
      // (signing out, switching off) last, the apps' own below; screensaver, subtitles and software updates a page
      // further down, as there
      general: `${part("", `
      ${open("basics", t("setGeneral"), now("language", LANG_CHOICES), t("setGeneralHint"))}
      ${open("av", t("setAv"), now("audio", audioOpts), t("setAvHint"))}
      ${s.wifi ? line("set-wifi", t("wifi"), "", `data-action="wifi"`, t("wifiHint")) : ""}
      ${s.hdmi?.available ? line("set-hdmi", t("hdmi"), esc(s.hdmi.inputs.map((x) => x.name).join(", ") || t("hdmiNone")), `data-action="hdmi"`, t("hdmiHint")) : ""}
      ${s.bluetooth ? line("set-bluetooth", t("setRemotes"), "", `data-action="bluetooth"`, t("btHint")) : ""}
      ${line("set-controls", t("controls"), "", `data-action="controls"`, t("controlsHint"))}
      ${SIMPLE ? "" : line("set-phoneremote", t("phoneRemote"), esc(t(s.phoneRemote?.on ? "on" : "off")), `data-action="phoneremote"`, t("phoneRemoteHint"))}
      ${SIMPLE ? "" : open("account", t("setAccount"), s.username ?? "", t("setAccountHint"))}
      ${open("system", t("setSystem"), "", t("setSystemHint"))}`)}
      ${SIMPLE ? "" : part(t("setApps"), `
      ${open("movies", partName("jellyfin"), s.jellyfinReady ? t("svcConnected") : "", t("setAppsHint"))}
      ${open("navidrome", partName("music"), serviceState(ndS), t("svcHint"))}
      ${open("books", partName("books"), serviceState(absS), t("svcHint"))}
      ${open("podcasts", partName("podcasts"), serviceState(pfS), t("svcHint"))}`)}`, // the apps in the order of the main menu and the tabs
      // the account: who is signed in and the devices sharing it, then moving it to another device, last handing this
      // one to someone else (their sign-ins, or everything of the one before off it)
      account: part(s.jellyfinReady && s.username ? t("accSignedInAs", { user: s.username }) : t("setAccount"),
        s.jellyfinReady ? choice("sync", t("syncSetting"), [["on", t("on")], ["off", t("off")]], syncHint(s)) : "")
        + (s.sync === "on" && s.devices?.length ? part(t("accDevices"), deviceLines(s.devices, line)) : "")
        + part(t("accMove"), line("acc-out", t("transferOut"), "", `data-action="transferout"`, t("transferOutHint"))
          + line("acc-in", t("transferIn"), "", `data-action="transferin"`, t("transferInHint")))
        + part(t("accOther"), open("everywhere", t("signInEverywhere"), "", t("signInEverywhereHint"))
          + line("acc-signout", t("signOut"), "", `data-action="signout"`, t("signOutHint"))),
      basics: `${part(t("setGeneral"), `
      ${choice("language", t("language"), LANG_CHOICES, t("languageHint"))}
      ${choice("startwith", t("startWith"), [["menu", t("startMenu")], ["last", t("startLast")], ["live", t("startLive")]], t("startWithHint"))}
      ${choice("effects", t("effects"), [["auto", t("effectsAuto")], ["full", t("effectsFull")], ["calm", t("effectsCalm")], ["light", t("effectsLight")]],
        t("effectsHint", { now: t({ full: "effectsFull", calm: "effectsCalm", light: "effectsLight" }[fxNow()]) }))}
      ${choice("size", t("uiSize"), [["auto", t("effectsAuto")], ["tv", t("uiSizeTv")], ["desk", t("uiSizeDesk")]],
        t("uiSizeHint", { now: t(SIZE_AUTO === "desk" ? "uiSizeDesk" : "uiSizeTv") }))}
      ${choice("textsize", t("textSize"), [["normal", t("textNormal")], ["large", t("textLarge")]], t("textSizeHint"))}
      ${choice("motion", t("motion"), [["full", t("textNormal")], ["less", t("motionLess")]], t("motionHint"))}
      ${choice("sounds", t("sounds"), [["off", t("off")], ["low", t("soundsLow")], ["medium", t("soundsMedium")], ["high", t("soundsHigh")]], t("soundsHint"))}
      ${s.sounds !== "off" ? choice("soundkind", t("soundKind"), ["click", "tap", "wood", "glass", "soft"].map((k) => [k, t(`sound_${k}`)]), t("soundKindHint")) : ""}
      ${open("saver", t("screensaver"), now("screensaver", saverOpts), t("screensaverHint"))}
      ${s.system ? choice("autooff", t("autoOff"), [["off", t("off")], ...["2", "4", "8"].map((n) => [n, t("autoOffAfter", { n })])], t("autoOffHint")) : ""}
      ${s.system ? choice("sleepend", t("sleepEndSetting"), [["stop", t("sleepStop")], ["poweroff", t("sleepPoweroff")]], t("sleepHint")) : ""}
      ${s.hidden ? line("set-unhide", t("hiddenItems"), esc(t("unhide", { n: s.hidden })), `data-action="unhide"`, t("unhideHint")) : ""}`)}`,
      saver: `${part(t("screensaver"), `
      ${choice("screensaver", t("saverStart"), saverOpts, t("screensaverHint"))}
      ${s.weather ? choice("saverweather", t("saverWeather"), [["on", t("saverWeatherOn")], ["off", t("off")]], t("saverWeatherHint")) : ""}
      ${line("set-wxplace", t("wxPlaceRow"), esc(s.weather ?? t("wxChoose")), `data-action="wxplace"`, t("saverWeatherHint"))}`)}`,
      av: `${part(t("setAv"), `
      ${choice("audio", t("audioLang"), audioOpts, t("audioHint"))}
      ${open("subs", t("subtitles"), now("subtitles", subsOpts), t("subtitlesHint"))}
      ${displayOpts ? choice("display", t("display"), displayOpts, t("displayHint")) : ""}
      ${s.audiosync ? choice("audiosync", t("audioSync"), syncOpts, t("audioSyncHint", { name: syncName })) : ""}
      ${s.volume != null ? line("set-volume", t("volume"), `<span id="vol-now">${s.volume} %</span>`, `data-action="volumeopen"`, t("volumeHint")) : ""}`)}`,
      subs: `${part(t("subtitles"), `
      ${choice("subtitles", t("subsShow"), subsOpts, t("subtitlesHint"))}
      ${choice("sublang", t("sublang"), sublangOpts, t("sublangHint"))}
      ${choice("subsize", t("subSize"), [["s", t("subSizeS")], ["m", t("subSizeM")], ["l", t("subSizeL")], ["xl", t("subSizeXL")]], t("subSizeHint"))}
      ${choice("substyle", t("subStyle"), [["outline", t("subStyleOutline")], ["box", t("subStyleBox")]], t("subStyleHint"))}`)}`,
      // its heading also where there is nothing to do (a desktop: no updates, no restart), Matinee's version under it
      system: `${part(t("setSystem"), `
      ${s.updates ? open("update", t("softwareUpdates"), s.update?.behind ? t("updateReady") : "", t("updateHint")) : ""}
      ${s.system ? ["reboot", "poweroff"].map((v) => line(`sys-${v}`, t(v), "", `data-action="system" data-val="${v}"`, t(`${v}Hint`))).join("") : ""}
      ${SIMPLE ? line("set-simple", t("simpleOff"), "", `data-action="simple" data-val="off"`, t("simpleOffHint"))
        : line("set-simple", t("simple"), esc(t("off")), `data-action="simple" data-val="on"`, t("simpleHint"))}
      ${SIMPLE ? line("set-maxage", t("ageLimit"), esc(ages.find(([v]) => v === String(s.maxage))?.[1] ?? t("off")), "", t("ageLimitLocked"))
        : choice("maxage", t("ageLimit"), ages, t("ageLimitHint"))}
      ${SIMPLE ? "" : line("set-welcome", t("welcomeAgain"), "", `data-action="welcome"`, t("welcomeAgainHint"))}
      ${s.support ? line("set-support", t("support"), "", `data-action="support"`, t("supportHint")) : ""}
      ${line("set-diagnose", t("diagnose"), "", `data-action="diagnose"`, t("diagnoseHint"))}
      ${line("set-legal", t("legal"), "", `data-action="legal"`, t("legalHint"))}`)}
      ${about([["Matinee", s.version], ...(s.timeSynced == null ? [] : [[t("timeSync"), t(s.timeSynced ? "timeSynced" : "timeNotSynced")]])])}
      ${SIMPLE ? "" : tailscaleAbout(s.tailscale, about)}`,
      // the last look (the main menu's, at most half an hour old): what it found at once, a second OK installs it
      update: s.updates ? part(t("softwareUpdates"), line("set-update", t("softwareUpdate"), esc(s.update?.behind ? updateLabel(s.update) : t("updateCheck")),
        `data-action="update" data-install="${s.updateInstall ? 1 : 0}"${s.update?.behind ? ' data-ready="1"' : ""}`, t("updateHint"))
        // the TV at night; a desktop once Matinee was not used for a while
        + choice("updatecheck", t("updateLook"), [["auto", t("updateLookAuto")], ["manual", t("updateLookManual")]], t("updateLookHint"))
        + (s.autoUpdates && s.updatecheck !== "manual" ? choice("autoupdate", t("autoUpdate"), [["off", t("off")], ["on", t("on")]], t(s.autoUpdatesNight ? "autoUpdateHint" : "autoUpdateHintDesktop")) : "")
        // the patch notes: OK opens them (kept for openUpdateNotes)
        + ((updateSeen = s.update ?? null, updateRecent = s.updateRecent ?? []).length || s.update?.behind
          ? line("set-notes", t("updateNotes"), esc(updateNotesValue(s.update, updateRecent)), 'data-action="updatenotes"', t("updateNotesHint")) : ""))
        + `<div id="update-info">${updateInfo(s.update)}</div>` : "",
      movies: `${part(partName("jellyfin"), `
      ${choice("quality", t("quality"), quality, t("qualityHint"))}
      ${choice("ask", t("askStill"), askOpts, t("askHint"))}
      ${choice("spoilers", t("spoilers"), [["show", t("spoilersShow")], ["hide", t("spoilersHide")]], t("spoilersHint"))}
      ${choice("badges", t("badges"), [["all", t("badgesAll")], ["missing", t("badgesMissing")], ["off", t("off")]], t("badgesHint"))}
      ${choice("skipintro", t("skipIntro"), [["off", t("off")], ["offer", t("skipOffer")], ["auto", t("skipAuto")]], t("skipIntroHint"))}
      ${choice("moviesound", t("movieSound"), [["level", t("movieSoundLevel")], ["original", t("movieSoundOriginal")]], t("movieSoundHint"))}
      ${displayOpts ? choice("framerate", t("framerate"), [["off", t("off")], ["on", t("framerateOn")]], t("framerateHint")) : ""}`)}
      ${part(t("lcPart"), (s.share ? line("set-share", t("share"), "", `data-action="share"`, t("shareHint")) : "")
        + line("set-libcheck", t("libCheck"), "", `data-action="libcheck"`, t("libCheckHint")))}
      ${s.ownchannels?.length ? part(t("ownChannels"), s.ownchannels.map((c) => line(`tvch-${c.id}`, c.name, esc(t(s.tvoff?.includes(c.id) ? "chHidden" : "chShown")),
        `data-action="tvtoggle" data-id="${esc(c.id)}"`, t("ownChannelsHint"))).join("")) : ""}
      <h2 class="settings-part">${esc(t("svcAccount"))}</h2>${jfForm({ server: s.server, username: s.username }, s.jellyfinReady)}
      ${about([["Jellyfin", s.serverVersion]])}`,
      // Navidrome, Audiobookshelf and Podfetch: set up here, while Matinee runs (server, user name, password or API key)
      navidrome: (ndS.configured ? part(partName("music"), choice("scrobble", t("scrobble"), [["on", t("scrobbleOn")], ["off", t("off")]], t("scrobbleHint"))) : "")
        + serviceForm("navidrome", s.navidrome, ndS, line),
      books: serviceForm("audiobookshelf", s.audiobookshelf && { server: s.audiobookshelf }, absS, line),
      podcasts: serviceForm("podfetch", s.podfetch, pfS, line),
      everywhere: everywhereForm(line, s.server),
    };
    const icon = { movies: ICON_JELLYFIN, navidrome: ICON_MUSIC, books: ICON_BOOKS, podcasts: ICON_PODCASTS, av: ICON_PLAY }[which] ?? ICON_SETTINGS;
    view.innerHTML = `${settingsSide(icon)}<div class="grid-page settings" data-part="${parts[which] ? which : "general"}"><h1>${t("settings")}</h1>
      ${parts[which] || parts.general}${["movies", "navidrome", "books", "podcasts"].includes(which) ? toGeneral : ""}</div>`; // an app's part, opened from the app: a way to all the others
  },

  // Bluetooth: speakers and headphones; it looks for devices nearby as soon as it opens
  // A server, user name, password or API key for a service, typed on the letter grid (or a keyboard); Done keeps it
  async svcentry({ svc, field, q = "" }) {
    view.innerHTML = searchPage(SVC_MARKS).replace('<div class="search">', '<div class="search entry">');
    showQuery(q);
    $("#results").innerHTML = `<div class="hint">${t(svcHint(svc, field))}</div>
      <div class="actions"><button class="btn f" data-k="svc-done" data-action="svcdone">${t("done")}</button></div>`;
  },

  // Waiting for another device's account: the code to type there, until it comes (Matinee then starts again); Back
  // stops waiting
  async transferin() {
    const r = await post("/transfer/wait");
    const body = !r || r.error ? `<p class="jf-msg">${esc(t(`err_${r?.error || "service_unreachable"}`))}</p>`
      : `<p class="jf-msg">${esc(t("transferInHow", { name: r.name }))}</p><div class="transfer-code">${esc(r.code.replace(/^(\d{3})(\d{3})$/, "$1 $2"))}</div>
        <p class="jf-msg" id="tr-msg">${esc(t("transferWaiting", { time: clockTime(r.seconds) }))}</p>`;
    view.innerHTML = `${settingsSide(ICON_SETTINGS, t("transferInHint"))}<div class="grid-page settings transfer"><h1>${esc(t("transferIn"))}</h1>
      ${body}<div class="actions"><button class="btn f" data-k="tr-end" data-action="transferend">${t("cancel")}</button></div></div>`;
    if (!r || r.error) return;
    const entry = current;
    clearInterval(trTimer);
    trTimer = setInterval(async () => {
      if (current !== entry) {
        clearInterval(trTimer);
        return void post("/transfer/cancel");
      }
      const st = await post("/transfer/state");
      const msg = $("#tr-msg");
      if (!st || !msg || current !== entry) return;
      if (st.waiting) return void (msg.textContent = t("transferWaiting", { time: clockTime(st.seconds) }));
      clearInterval(trTimer);
      if (st.from) {
        msg.textContent = t("transferCame", { name: st.from });
        return reloadWhenBack();
      }
      msg.textContent = t("transferOver");
      const end = $('[data-k="tr-end"]');
      if (end) end.textContent = t("done");
    }, 1000);
  },

  // This device's account to another one: the tailnet's devices waiting for one, then the code it shows
  async transferout() {
    view.innerHTML = `${settingsSide(ICON_SETTINGS, t("transferOutHint"))}<div class="grid-page settings transfer"><h1>${esc(t("transferOut"))}</h1>
      <p class="jf-msg" id="tr-msg">${esc(t("transferLooking"))}</p><div id="tr-list"></div></div>`;
    await transferLook();
  },

  // The place of the weather under the screensaver's clock: typed like a search, then chosen from the places found
  async weatherplace({ q = "" }) {
    view.innerHTML = searchPage();
    showQuery(q);
    await runPlaceSearch(q);
  },

  // Films Jellyfin took for others, or with a still for a poster: looked through anew each time, each with what
  // would fix it; fixed only after asking (lcAsk), one or all
  async libcheck() {
    view.innerHTML = `${settingsSide(ICON_JELLYFIN, t("libCheckHint"))}<div class="grid-page settings libcheck" data-part="libcheck">
      <h1>${esc(t("libCheck"))}</h1><p class="jf-msg" id="lc-msg">${esc(t("lcScanning"))}</p><div id="lc-body"></div></div>`;
    lcShow(await fetch("/library/check", { method: "POST", headers: JSON_HEADERS, body: "{}" }).then((r) => r.json()).catch(() => null));
    const entry = current;
    clearInterval(lcTimer);
    lcTimer = setInterval(() => (current === entry ? lcRefresh() : clearInterval(lcTimer)), 1000);
  },

  // Subtitles for a movie or an episode from the internet, through Jellyfin (backend/library.py, subtitle_search): in the
  // subtitle language of the settings, or the other one; OK on one has Jellyfin download it
  async subsearch({ id, name, lang = "" }) {
    view.innerHTML = `${settingsSide(G.captions, t("findSubtitlesHint"))}<div class="grid-page settings"><h1>${esc(name)}</h1>
      <div id="sublist"><div class="hint">${t("subsLooking")}</div></div></div>`;
    const r = await fetch(`/subtitles/search?id=${encodeURIComponent(id)}&lang=${encodeURIComponent(lang)}`);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || t("subsFailed"));
    const chips = [["de", "Deutsch"], ["en", "English"], ["fr", "Français"], ["es", "Español"], ["nl", "Nederlands"]].map(([v, label]) =>
      `<div class="chip f${data.lang === v ? " sel" : ""}" tabindex="-1" data-k="sublang-${v}" data-action="sublangopt" data-val="${v}">${label}</div>`).join("");
    const rows = data.items.map((s, i) => srow(`sub-${i}`, s.name || s.provider,
      esc([s.match && t("subsMatch"), s.forced && t("subsForced"), s.hearing && "SDH", s.format.toUpperCase(), s.provider,
        s.downloads != null && `${s.downloads} ↓`].filter(Boolean).join(" · ")),
      `data-action="subget" data-id="${esc(id)}" data-sub="${esc(s.id)}"`, t("subsGetHint"))).join("");
    $("#sublist").innerHTML = `<div class="chips">${chips}</div>${rows ? `<div class="slist">${rows}</div>`
      : `<div class="hint">${t("subsNone")}</div>`}`;
  },

  async bluetooth() {
    view.innerHTML = `${settingsSide(ICON_BLUETOOTH, t("btHint"))}<div class="grid-page settings bluetooth"><h1>Bluetooth</h1>
      <div class="slist">${srow("btscan", t("btScan"), "", `data-action="btscan"`, t("btHint"))}</div>
      <div id="btlist"></div></div>`;
    await btScan();
    const entry = current;
    clearInterval(btTimer);
    btTimer = setInterval(() => (current === entry ? btRefresh() : clearInterval(btTimer)), 2000);
  },

  // Wi-Fi: the networks nearby, the one in use first; OK joins one (asking for its password where it needs one)
  async wifi() {
    view.innerHTML = `${settingsSide(ICON_WIFI, t("wifiHint"))}<div class="grid-page settings wifi"><h1>${t("wifi")}</h1>
      <div class="page-actions"><button class="btn f" data-k="wifiscan" data-action="wifiscan">${t("btScan")}</button></div>
      <div id="wifilist"><div class="hint">${t("wifiLooking")}</div></div></div>`;
    await wifiRefresh();
  },

  // The TV's other inputs (hdmi.py): each a tile on the main menu that switches the TV to it
  async hdmi(args, focusKey) {
    const d = await getJson("/hdmi");
    hdmiInputs = d?.inputs ?? [];
    const rows = hdmiInputs.map((x, i) => srow(`hdmi-${i}`, x.name, esc(t("hdmiPort", { n: x.port })), `data-action="hdmiinput" data-i="${i}"`, t("hdmiInputHint")))
      .join("") + (hdmiInputs.length < HDMI_MAX ? srow("hdmi-add", t("hdmiAdd"), "", `data-action="hdmiadd"`, t("hdmiAddHint")) : "");
    view.innerHTML = `${settingsSide(ICON_HDMI, t("hdmiHint"))}<div class="grid-page settings hdmi"><h1>${t("hdmi")}</h1>
      ${d?.available ? "" : `<p class="jf-msg">${t("hdmiNoAdapter")}</p>`}<div class="slist">${rows}</div></div>`;
    if (focusKey) view.querySelector(`[data-k="${CSS.escape(focusKey)}"]`)?.setAttribute("data-autofocus", "");
  },

});
