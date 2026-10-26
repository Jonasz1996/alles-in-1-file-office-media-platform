/* De editorpagina van de nep-DS (§3.6). Geen inline script: deze pagina draait onder de CSP
 * van de editor (Keuze 9).
 *
 * De config komt van api.js via postMessage ({fake: "init", config}). Dan een WebSocket naar
 * <prefix>/<V>/doc/<key>/c/?shardkey=<key>&EIO=4&transport=websocket. Het eerste pakket is
 * CONNECT van socket.io met het token ("token", of "session" na een eerste verbinding), dan
 * het auth-bericht, in de vorm die de spike vastlegde. Elke gebruiker typt zijn eigen tekst;
 * die van de anderen komt live binnen. Na een breuk verbindt hij opnieuw (50 keer, met een
 * kortere pauze dan de echte); na drie mislukte pogingen meldt hij onWarning -18.
 */
(function () {
  "use strict";
  var RECONNECT_MS = 500;
  var ATTEMPTS = 50;
  var WARN_AFTER = 3;

  var params = new URLSearchParams(location.search);
  var parentOrigin = params.get("parentOrigin") || location.origin;
  var base = location.pathname.slice(0, location.pathname.indexOf("/web-apps/"));

  var statusEl = document.getElementById("status");
  var textEl = document.getElementById("text");
  var saveEl = document.getElementById("save");
  var closeEl = document.getElementById("close");
  var docEl = document.getElementById("document");
  var othersEl = document.getElementById("others");

  var config = null;
  var generation = 0;
  var ws = null;
  var session = null;
  var sessionId = null;
  var sessionTimeConnect = null;
  var knownSave = -1;
  var index = -1;
  var attempts = 0;
  var warned = false;
  var opened = false;
  var stopped = true;

  function post(name, data) {
    window.parent.postMessage({ fake: "event", name: name, data: data }, parentOrigin);
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function editing() {
    return ((config && config.editorConfig) || {}).mode !== "view";
  }

  function enable() {
    var on = opened && !stopped && ws !== null && editing();
    textEl.disabled = !on;
    saveEl.disabled = !on;
  }

  function sendFrame(sock, text) {
    if (sock && sock.readyState === 1) sock.send(text);
  }

  function sendMessage(obj) {
    sendFrame(ws, "42" + JSON.stringify(["message", obj]));
  }

  function user() {
    return (config.editorConfig && config.editorConfig.user) || {};
  }

  function authData(first) {
    var doc = config.document || {};
    var ed = config.editorConfig || {};
    var u = user();
    var data = {
      type: "auth",
      docid: doc.key,
      documentCallbackUrl: ed.callbackUrl,
      token: "fghhfgsjdgfjs",
      user: { id: u.id, username: u.name, firstname: null, lastname: null, indexUser: index },
      editorType: 0,
      lastOtherSaveTime: knownSave,
      block: [],
      sessionId: sessionId,
      sessionTimeConnect: sessionTimeConnect,
      sessionTimeIdle: 0,
      documentFormatSave: 65,
      isCloseCoAuthoring: false,
    };
    if (first) {
      data.openCmd = {
        c: "open",
        id: doc.key,
        userid: u.id,
        format: doc.fileType,
        url: doc.url,
        title: doc.title,
        nobase64: true,
        outputformat: 8193,
        convertToOrigin: ".pdf.xps.oxps.djvu.ofd",
      };
    }
    data.lang = ed.lang || "en";
    data.mode = ed.mode || "edit";
    data.permissions = doc.permissions || {};
    data.encrypted = false;
    data.IsAnonymousUser = false;
    data.timezoneOffset = new Date().getTimezoneOffset();
    data.headingsColor = null;
    data.coEditingMode = "fast";
    if (first || !session) {
      data.jwtOpen = config.token;
    } else {
      data.jwtSession = session;
    }
    data.time = Math.round(performance.now());
    data.supportAuthChangesAck = true;
    return data;
  }

  function connectPacket() {
    // CONNECT draagt altijd de hele data met jwtOpen; het token zelf is "token" of "session"
    var packet = { data: authData(true) };
    if (session) packet.session = session;
    else packet.token = config.token;
    return packet;
  }

  function stop() {
    stopped = true;
    generation += 1;
    var sock = ws;
    ws = null;
    if (sock) {
      try {
        sock.close();
      } catch (e) {
        /* al dicht */
      }
    }
    enable();
  }

  function render(content) {
    docEl.textContent = "";
    (content.paragraphs || []).forEach(function (text) {
      var p = document.createElement("p");
      p.textContent = text;
      docEl.appendChild(p);
    });
    var texts = content.texts || {};
    Object.keys(texts).forEach(function (id) {
      if (id === String(user().id)) textEl.value = texts[id].text;
      else showOther(id, texts[id].name, texts[id].text);
    });
  }

  function showOther(id, name, text) {
    var el = null;
    for (var i = 0; i < othersEl.children.length; i++) {
      if (othersEl.children[i].getAttribute("data-user") === id) el = othersEl.children[i];
    }
    if (!el) {
      el = document.createElement("p");
      el.setAttribute("data-user", id);
      othersEl.appendChild(el);
    }
    el.textContent = (name || id) + ": " + text;
  }

  function load(link, gen) {
    fetch(link, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(function (content) {
        if (gen !== generation) return;
        render(content);
        opened = true;
        enable();
        setStatus("Klaar");
        post("onDocumentReady");
      })
      .catch(function () {
        if (gen !== generation) return;
        stop();
        setStatus("Het document kon niet worden geladen");
        post("onError", { errorCode: -4, errorDescription: "download" });
      });
  }

  function onMessage(sock, gen, m) {
    switch (m.type) {
      case "license":
        sendFrame(sock, "42" + JSON.stringify(["message", authData(!session)]));
        break;
      case "auth":
        if (m.result !== 1) break;
        if (m.jwt) session = m.jwt;
        sessionId = m.sessionId;
        sessionTimeConnect = m.sessionTimeConnect;
        index = m.indexUser;
        attempts = 0;
        warned = false;
        if (opened) {
          setStatus("Verbonden");
          enable();
          if (textEl.value) sendMessage({ type: "saveChanges", text: textEl.value });
        }
        break;
      case "documentOpen":
        if (m.data && m.data.status === "ok" && m.data.data) {
          load(m.data.data["Editor.bin"], gen);
        } else {
          stop();
          setStatus("Het document kon niet worden geopend");
          post("onError", { errorCode: (m.data && m.data.data) || -4, errorDescription: "open" });
        }
        break;
      case "saveChanges":
        showOther(String(m.userid), m.name, m.text);
        break;
      case "unSaveLock":
        post("onDocumentStateChange", false);
        break;
      case "forceSave":
        setStatus(m.messages && m.messages.success ? "Opgeslagen" : "Opslaan mislukt");
        break;
      case "documentSaved":
        knownSave = m.time;
        break;
      case "refreshFile":
        stop();
        setStatus("Er is intussen opgeslagen");
        post("onRequestRefreshFile");
        break;
      case "drop":
        stop();
        setStatus("De verbinding is verbroken door de server");
        break;
      default:
        break;
    }
  }

  function onFrame(sock, gen, text) {
    if (typeof text !== "string" || gen !== generation) return;
    var c = text.charAt(0);
    if (c === "0") {
      sendFrame(sock, "40" + JSON.stringify(connectPacket()));
      return;
    }
    if (c === "2") {
      sendFrame(sock, "3" + text.slice(1));
      return;
    }
    var head = text.slice(0, 2);
    if (head === "44") {
      var code = -20;
      try {
        code = JSON.parse(text.slice(2)).data.code || -20;
      } catch (e) {
        code = -20;
      }
      stop();
      setStatus("Geen toegang tot het document");
      post("onError", { errorCode: code, errorDescription: "token" });
      return;
    }
    if (head !== "42") return;
    var data;
    try {
      data = JSON.parse(text.slice(2));
    } catch (e) {
      return;
    }
    if (Array.isArray(data) && data[0] === "message" && data[1]) onMessage(sock, gen, data[1]);
  }

  function onClose(gen) {
    if (gen !== generation || stopped) return;
    ws = null;
    enable();
    attempts += 1;
    if (attempts >= WARN_AFTER && !warned) {
      warned = true;
      setStatus("Verbinding weg");
      post("onWarning", { warningCode: -18, warningDescription: "verbinding weg" });
    }
    if (attempts > ATTEMPTS) {
      stop();
      return;
    }
    setTimeout(function () {
      if (gen === generation && !stopped) connect(gen);
    }, RECONNECT_MS);
  }

  function connect(gen) {
    var key = config.document.key;
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    var url =
      scheme + "//" + location.host + base + "/doc/" + encodeURIComponent(key) +
      "/c/?shardkey=" + encodeURIComponent(key) + "&EIO=4&transport=websocket";
    var sock = new WebSocket(url);
    ws = sock;
    sock.onmessage = function (ev) {
      onFrame(sock, gen, ev.data);
    };
    sock.onclose = function () {
      if (ws === sock || ws === null) onClose(gen);
    };
  }

  function start(next) {
    stop();
    config = next;
    session = null;
    sessionId = null;
    sessionTimeConnect = null;
    knownSave = -1;
    index = -1;
    attempts = 0;
    warned = false;
    opened = false;
    stopped = false;
    textEl.value = "";
    docEl.textContent = "";
    othersEl.textContent = "";
    var custom = (config.editorConfig && config.editorConfig.customization) || {};
    closeEl.hidden = !custom.close;
    setStatus("Verbinden");
    post("onAppReady");
    connect(generation);
  }

  textEl.addEventListener("input", function () {
    sendMessage({ type: "saveChanges", text: textEl.value });
    post("onDocumentStateChange", true);
  });

  saveEl.addEventListener("click", function () {
    setStatus("Opslaan");
    sendMessage({ type: "forceSaveStart" });
  });

  closeEl.addEventListener("click", function () {
    post("onRequestClose");
  });

  window.addEventListener("message", function (e) {
    if (e.source !== window.parent) return;
    var m = e.data;
    if (m && typeof m === "object" && m.fake === "init" && m.config) start(m.config);
  });

  window.parent.postMessage({ fake: "ready" }, parentOrigin);
})();
