/* De nep van web-apps/apps/api/documents/api.js (DS 10.0), voor tests (§3.6).
 *
 * new DocsAPI.DocEditor(id, config) vervangt het element met dat id door een iframe naar
 * <prefix>/<V>/web-apps/apps/<editor>/main/index.html. Het prefix komt uit X-Forwarded-Prefix
 * (de nep-DS vult het hier in): zonder die kop klopt het adres niet, komt de editor nooit
 * klaar, en volgt onError -4, net als bij de echte.
 *
 * Controleert config.token: aanwezig (als DS tokens eist), niet verlopen (anders -21), en de
 * getekende velden gelijk aan de config (anders -20). De handtekening zelf controleert de
 * nep-DS bij het verbinden, net als de echte.
 *
 * Events: onAppReady, onDocumentReady, onDocumentStateChange, onError, onWarning (-18 als de
 * socket wegvalt), onRequestClose, onRequestRefreshFile. Methoden: destroyEditor() en
 * refreshFile(config).
 */
(function () {
  "use strict";
  var PREFIX = __PREFIX__;
  var V = __V__;
  var JWT = __JWT__;
  var READY_MS = 10000;
  var EDITORS = { word: "documenteditor", cell: "spreadsheeteditor", slide: "presentationeditor" };

  function claimsOf(token) {
    try {
      var part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      while (part.length % 4) part += "=";
      var bytes = atob(part);
      var text = decodeURIComponent(
        Array.prototype.map
          .call(bytes, function (c) {
            return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
          })
          .join("")
      );
      return JSON.parse(text);
    } catch (e) {
      return null;
    }
  }

  function get(obj, path) {
    var value = obj;
    for (var i = 0; i < path.length; i++) {
      if (value === null || typeof value !== "object") return undefined;
      value = value[path[i]];
    }
    return value;
  }

  var SIGNED = [
    ["document", "key"],
    ["document", "url"],
    ["document", "fileType"],
    ["editorConfig", "callbackUrl"],
    ["editorConfig", "mode"],
    ["editorConfig", "user", "id"],
  ];

  function tokenProblem(config) {
    if (!JWT) return null;
    if (typeof config.token !== "string" || !config.token) return -20;
    var claims = claimsOf(config.token);
    if (!claims) return -20;
    if (typeof claims.exp !== "number") return -20;
    if (claims.exp * 1000 < Date.now()) return -21;
    for (var i = 0; i < SIGNED.length; i++) {
      if (JSON.stringify(get(claims, SIGNED[i])) !== JSON.stringify(get(config, SIGNED[i]))) {
        return -20;
      }
    }
    return null;
  }

  function plain(config) {
    var copy = {};
    for (var k in config) {
      if (Object.prototype.hasOwnProperty.call(config, k) && k !== "events") copy[k] = config[k];
    }
    return JSON.parse(JSON.stringify(copy));
  }

  function DocEditor(id, config) {
    var self = this;
    var placeholder = document.getElementById(id);
    var events = (config && config.events) || {};
    var current = config;
    var frame = null;
    var timer = null;
    var destroyed = false;

    function fire(name, data) {
      var handler = events[name];
      if (typeof handler !== "function") return;
      handler({ target: self, data: data });
    }

    function fail(code, text) {
      setTimeout(function () {
        if (!destroyed) fire("onError", { errorCode: code, errorDescription: text });
      }, 0);
    }

    function onMessage(e) {
      if (!frame || e.source !== frame.contentWindow || e.origin !== location.origin) return;
      var m = e.data;
      if (!m || typeof m !== "object") return;
      if (m.fake === "ready") {
        clearTimeout(timer);
        frame.contentWindow.postMessage({ fake: "init", config: plain(current) }, location.origin);
      } else if (m.fake === "event" && typeof m.name === "string") {
        fire(m.name, m.data);
      }
    }

    function start() {
      var problem = tokenProblem(current);
      if (problem !== null) {
        fail(problem, "token");
        return;
      }
      var dir = EDITORS[current.documentType] || EDITORS.word;
      var src =
        PREFIX + "/" + V + "/web-apps/apps/" + dir + "/main/index.html" +
        "?frameEditorId=" + encodeURIComponent(id) +
        "&parentOrigin=" + encodeURIComponent(location.origin) +
        "&lang=" + encodeURIComponent((current.editorConfig && current.editorConfig.lang) || "en");
      frame = document.createElement("iframe");
      frame.name = "frameEditor";
      frame.id = id;
      frame.src = src;
      frame.setAttribute("allowfullscreen", "");
      frame.setAttribute("allow", "autoplay; clipboard-read; clipboard-write");
      frame.style.width = current.width || "100%";
      frame.style.height = current.height || "100%";
      frame.style.border = "0";
      if (placeholder && placeholder.parentNode) {
        placeholder.parentNode.replaceChild(frame, placeholder);
      }
      timer = setTimeout(function () {
        fail(-4, "de editor kwam niet klaar");
      }, READY_MS);
    }

    window.addEventListener("message", onMessage);

    this.destroyEditor = function () {
      destroyed = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      if (frame && frame.parentNode) {
        var div = document.createElement("div");
        div.id = id;
        frame.parentNode.replaceChild(div, frame);
      }
      frame = null;
    };

    this.refreshFile = function (config) {
      var next = {};
      var k;
      for (k in current) {
        if (Object.prototype.hasOwnProperty.call(current, k)) next[k] = current[k];
      }
      for (k in config || {}) {
        if (Object.prototype.hasOwnProperty.call(config, k)) next[k] = config[k];
      }
      current = next;
      var problem = tokenProblem(current);
      if (problem !== null) {
        fail(problem, "token");
        return;
      }
      if (frame && frame.contentWindow) {
        frame.contentWindow.postMessage({ fake: "init", config: plain(current) }, location.origin);
      }
    };

    start();
  }

  window.DocsAPI = { DocEditor: DocEditor };
})();
