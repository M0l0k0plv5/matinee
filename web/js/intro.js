// Run before the rest of the page: the intro only once per start of Chromium, not again on each reload
try { if (sessionStorage.getItem("kino-started")) document.getElementById("intro").remove(); } catch {}
