// Draait synchroon in <head>, vóór de eerste paint: thema en taal uit localStorage, zodat er
// nooit even het verkeerde thema te zien is. Een los bestand (geen inline script), want de CSP
// staat geen inline scripts toe. Gaat er iets mis (opslag uit), dan blijven de standaarden staan.
(function () {
  try {
    var root = document.documentElement;
    var store = null;
    try {
      store = window.localStorage;
    } catch (e) {
      store = null;
    }
    var theme = store ? store.getItem("platform-theme") : null;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");

    var locale = store ? store.getItem("platform-locale") : null;
    if (locale !== "nl" && locale !== "en") {
      var langs = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
      var first = String(langs[0] || "nl").toLowerCase();
      locale = first.indexOf("nl") === 0 ? "nl" : "en";
    }
    root.setAttribute("lang", locale);

    var dark =
      theme === "dark" ||
      (theme !== "light" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    // Elke theme-color (index.html kan er een per kleurschema hebben, met media): allemaal de
    // kleur van het gekozen thema en zonder media, anders wint die van het systeem. Dezelfde
    // waarden als --bg in tokens.css; ui/theme.ts doet hetzelfde bij het wisselen.
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    if (!metas.length) {
      var meta = document.createElement("meta");
      meta.setAttribute("name", "theme-color");
      document.head.appendChild(meta);
      metas = [meta];
    }
    for (var i = 0; i < metas.length; i++) {
      metas[i].setAttribute("content", dark ? "#121212" : "#ffffff");
      metas[i].removeAttribute("media");
    }
  } catch (e) {
    /* niets: de standaarden blijven staan */
  }
})();
