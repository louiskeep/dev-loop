/* Polls /fragment and swaps the page body with parsed nodes. No framework. */
(function () {
  "use strict";
  var POLL_MS = 4000;
  var STALE_MS = 60000;
  var skew = 0; // server clock minus local clock, from each Date header
  var etag = null;
  var failures = 0;
  var lastGood = Date.now();
  var busy = false;

  function $(id) { return document.getElementById(id); }
  function now() { return Date.now() + skew; }

  function store(key, value) {
    try {
      if (value === undefined) { return localStorage.getItem(key); }
      localStorage.setItem(key, value);
    } catch (err) { /* private mode or blocked: the page still works */ }
    return null;
  }

  // Theme: follow the system unless the viewer picks one.
  function applyTheme(name) {
    if (name) { document.documentElement.setAttribute("data-theme", name); }
  }
  applyTheme(store("dash-theme"));
  $("theme").addEventListener("click", function () {
    var cur = document.documentElement.getAttribute("data-theme");
    var dark = cur ? cur === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    applyTheme(next);
    store("dash-theme", next);
  });

  // Relative times, driven by the server's clock.
  function relative(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) { return ""; }
    var s = Math.max(0, Math.floor((now() - t) / 1000));
    if (s < 60) { return "just now"; }
    if (s < 3600) { return Math.floor(s / 60) + "m ago"; }
    if (s < 86400) { return Math.floor(s / 3600) + "h ago"; }
    return Math.floor(s / 86400) + "d ago";
  }
  function tickTimes() {
    var els = document.querySelectorAll("time[datetime]");
    for (var i = 0; i < els.length; i++) {
      var text = relative(els[i].getAttribute("datetime"));
      if (text && els[i].textContent !== text) { els[i].textContent = text; }
    }
  }

  // Filters live in the query string so a view can be shared.
  function readFilters() {
    var p = new URLSearchParams(location.search);
    $("f-q").value = p.get("q") || "";
    $("f-risk").value = p.get("risk") || "";
    $("f-attn").checked = p.get("attn") === "1";
  }
  function applyFilters() {
    var q = $("f-q").value.trim().toLowerCase();
    var risk = $("f-risk").value;
    var attn = $("f-attn").checked;
    var cards = document.querySelectorAll("[data-card]");
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var show = (!q || c.getAttribute("data-text").indexOf(q) >= 0) &&
        (!risk || c.getAttribute("data-risk") === risk) &&
        (!attn || c.getAttribute("data-attn") === "1");
      c.hidden = !show;
    }
  }
  function saveFilters() {
    var p = new URLSearchParams();
    if ($("f-q").value) { p.set("q", $("f-q").value); }
    if ($("f-risk").value) { p.set("risk", $("f-risk").value); }
    if ($("f-attn").checked) { p.set("attn", "1"); }
    var qs = p.toString();
    try { history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "")); }
    catch (err) { /* ignore */ }
    applyFilters();
  }
  ["f-q", "f-risk", "f-attn"].forEach(function (id) {
    $(id).addEventListener("input", saveFilters);
    $(id).addEventListener("change", saveFilters);
  });
  document.addEventListener("keydown", function (ev) {
    var tag = (ev.target && ev.target.tagName) || "";
    if (ev.key === "/" && tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA") {
      ev.preventDefault();
      $("f-q").focus();
    }
  });

  // Swap the body for the new fragment, keeping open details and the scroll position.
  function swap(text) {
    var doc = new DOMParser().parseFromString(text, "text/html");
    var fresh = doc.getElementById("app-body");
    var current = $("app-body");
    if (!fresh || !current) { return; }
    // Same content as the page already shows: keep every node (forms, scroll, focus).
    if (fresh.getAttribute("data-version") === current.getAttribute("data-version")) { return; }
    var open = {};
    var details = current.querySelectorAll("details[id]");
    for (var i = 0; i < details.length; i++) { open[details[i].id] = details[i].open; }
    var y = window.scrollY;
    var incoming = document.importNode(fresh, true);
    var next = incoming.querySelectorAll("details[id]");
    for (var j = 0; j < next.length; j++) {
      if (open.hasOwnProperty(next[j].id)) { next[j].open = open[next[j].id]; }
    }
    current.replaceWith(incoming);
    window.scrollTo(0, y);
    applyFilters();
    tickTimes();
    showControlMessage();
    fillDrawer();
  }

  // Drawer: one shell outside #app-body. Its content is cloned from hidden sources that
  // ride inside every fragment, so an open drawer follows each poll.
  var lastFocus = null;
  var NO_SLICE = "This slice is no longer active.";
  var NO_QUESTION = "This question was answered.";
  var shown = null; // {kind, key, version} of the question drawer content on screen
  var changed = null; // a newer version of the open question, waiting for the owner
  var lastSaid = ""; // the server's message for the last answer, shown once the drawer empties

  function hashTarget() {
    var m = /^#(slice|q)=([A-Za-z0-9._:-]+)$/.exec(location.hash);
    return m ? { kind: m[1], key: m[2] } : null;
  }
  function note(text) {
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = text;
    return p;
  }
  function cardFallback(key) {
    var cards = document.querySelectorAll("[data-card-key]");
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].getAttribute("data-card-key") === key) {
        var list = cards[i].querySelector(".cl > ul");
        var box = document.createElement("div");
        var h = document.createElement("h3");
        h.textContent = "Checklist";
        box.appendChild(h);
        box.appendChild(list ? list.cloneNode(true) : note("No checklist for this slice."));
        return box;
      }
    }
    return null;
  }
  function cloneChildren(source) {
    var parts = [];
    for (var i = 0; i < source.childNodes.length; i++) {
      parts.push(source.childNodes[i].cloneNode(true));
    }
    return parts;
  }
  function draftOf(body) {
    var ta = body.querySelector("textarea");
    return ta ? ta.value : "";
  }
  function setSaveEnabled(body, on) {
    // An old form would post an old version, so none of its buttons stay live.
    var forms = body.querySelectorAll("form.qform button");
    for (var i = 0; i < forms.length; i++) { forms[i].disabled = !on; }
  }
  function showChanged(body, source) {
    if (changed && changed.version === source.getAttribute("data-version")) { return; }
    changed = { version: source.getAttribute("data-version") };
    var bar = document.createElement("div");
    bar.className = "qchanged";
    bar.setAttribute("role", "alert");
    var msg = document.createElement("p");
    msg.textContent = "This question changed while you were typing. Read the new version; your text is kept.";
    var use = document.createElement("button");
    use.type = "button";
    use.className = "use-new";
    use.textContent = "Use my text with the new version";
    bar.appendChild(msg);
    bar.appendChild(use);
    body.insertBefore(bar, body.firstChild);
    setSaveEnabled(body, false);
  }
  function fillQuestion(target, body) {
    var source = document.getElementById("qs-" + target.key);
    if (!source) {
      shown = null;
      changed = null;
      body.replaceChildren(note(lastSaid ? lastSaid + " " + NO_QUESTION : NO_QUESTION));
      return;
    }
    var version = source.getAttribute("data-version");
    if (shown && shown.key === target.key) {
      if (shown.version === version) { return; } // same version: keep the draft as typed
      if (draftOf(body)) { showChanged(body, source); return; }
    }
    shown = { kind: "q", key: target.key, version: version };
    changed = null;
    body.replaceChildren.apply(body, cloneChildren(source));
  }
  function useNewVersion(body) {
    var draft = draftOf(body);
    var source = document.getElementById("qs-" + shown.key);
    if (!source) { return; }
    shown = { kind: "q", key: shown.key, version: source.getAttribute("data-version") };
    changed = null;
    body.replaceChildren.apply(body, cloneChildren(source));
    var ta = body.querySelector("textarea");
    if (ta) { ta.value = draft; }
  }
  function fillDrawer() {
    var drawer = $("drawer");
    var target = hashTarget();
    if (!target) {
      drawer.hidden = true;
      shown = null;
      changed = null;
      document.body.classList.remove("drawer-open");
      return;
    }
    var body = $("drawer-body");
    var keep = body.scrollTop;
    if (target.kind === "q") {
      if (shown && shown.key !== target.key) { shown = null; changed = null; }
      fillQuestion(target, body);
    } else {
      shown = null;
      var source = document.getElementById("ds-" + target.key);
      body.replaceChildren.apply(
        body,
        source ? cloneChildren(source) : [cardFallback(target.key) || note(NO_SLICE)]
      );
    }
    $("drawer-title").textContent = target.key;
    body.scrollTop = keep;
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
  }
  function openDrawer(kind, key) {
    lastFocus = document.activeElement;
    location.hash = kind + "=" + key;
  }
  function closeDrawer() {
    if (location.hash) {
      try { history.replaceState(null, "", location.pathname + location.search); }
      catch (err) { location.hash = ""; }
    }
    fillDrawer();
    if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
    lastFocus = null;
  }
  $("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !$("drawer").hidden) { closeDrawer(); }
  });
  window.addEventListener("hashchange", function () {
    fillDrawer();
    if (!$("drawer").hidden) { $("drawer-close").focus(); }
  });
  document.addEventListener("click", function (ev) {
    var from = ev.target.closest ? ev.target : null;
    if (!from) { return; }
    var link = from.closest("a[href^='#slice=']");
    if (link) { lastFocus = link; return; }
    var btn = from.closest("[data-open-kind]");
    if (btn) {
      openDrawer(btn.getAttribute("data-open-kind"), btn.getAttribute("data-open-key"));
    }
  });

  // Write actions: every form posts through fetch and shows the server's own message.
  function messageBox(form) {
    return form.closest("#drawer-body") ? document.querySelector("#drawer-body .qmsg") :
      document.querySelector(".cmsg");
  }
  var ctlSaid = { text: "", at: 0 };
  function showControlMessage() {
    var box = document.querySelector(".cmsg");
    if (box) { box.textContent = Date.now() - ctlSaid.at < 8000 ? ctlSaid.text : ""; }
  }
  function say(form, text) {
    if (!form.closest("#drawer-body")) { ctlSaid = { text: text, at: Date.now() }; }
    var box = messageBox(form);
    if (box) { box.textContent = text; }
  }
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.classList || !(form.classList.contains("qform") || form.classList.contains("cform"))) { return; }
    ev.preventDefault();
    var ta = form.querySelector("textarea");
    if (ta && !ta.value.trim()) { say(form, "Write an answer first."); return; }
    var buttons = form.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }
    var body = new URLSearchParams(new FormData(form)).toString();
    fetch(form.action, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
      body: body,
      cache: "no-store"
    }).then(function (res) {
      return res.json().catch(function () { return { ok: false, message: "Something went wrong. Reload to see what was saved." }; })
        .then(function (data) { return { ok: res.ok, data: data }; });
    }).then(function (out) {
      say(form, out.data.message || "");
      lastSaid = out.ok && form.classList.contains("qform") ? out.data.message || "" : "";
      if (out.ok && ta) { ta.value = ""; }
      for (var j = 0; j < buttons.length; j++) { buttons[j].disabled = false; }
      refresh();
    }).catch(function () {
      say(form, "Could not reach the dashboard.");
      for (var j = 0; j < buttons.length; j++) { buttons[j].disabled = false; }
    });
  });
  document.addEventListener("click", function (ev) {
    var from = ev.target.closest ? ev.target : null;
    if (!from) { return; }
    var use = from.closest(".use-new");
    if (use) { useNewVersion($("drawer-body")); return; }
    var copy = from.closest("button.copy");
    if (!copy) { return; }
    var text = copy.getAttribute("data-copy");
    var box = copy.closest("#drawer-body").querySelector(".qmsg");
    if (window.isSecureContext && navigator.clipboard) {
      navigator.clipboard.writeText(text).then(function () { box.textContent = "Copied."; },
        function () { box.textContent = "Could not copy."; });
      return;
    }
    // A LAN address over http has no clipboard API: show the text selected instead.
    var area = copy.parentNode.querySelector("textarea.copybox");
    if (!area) {
      area = document.createElement("textarea");
      area.className = "copybox";
      area.readOnly = true;
      area.rows = 4;
      copy.parentNode.appendChild(area);
    }
    area.value = text;
    area.focus();
    area.select();
    box.textContent = "Press Ctrl+C to copy.";
  });

  function showStale() {
    $("stale").hidden = !(failures >= 2 || Date.now() - lastGood > STALE_MS);
  }

  function refresh() { busy = false; poll(); }

  function poll() {
    if (document.hidden || busy) { return; }
    busy = true;
    var headers = etag ? { "If-None-Match": etag } : {};
    fetch("/fragment", { headers: headers, cache: "no-store" }).then(function (res) {
      var date = Date.parse(res.headers.get("Date") || "");
      if (!isNaN(date)) { skew = date - Date.now(); }
      if (res.status === 304) { return null; }
      if (!res.ok) { throw new Error("bad status"); }
      etag = res.headers.get("ETag");
      return res.text();
    }).then(function (text) {
      if (text !== null) { swap(text); }
      failures = 0;
      lastGood = Date.now();
    }).catch(function () {
      failures += 1;
    }).then(function () {
      busy = false;
      showStale();
    });
  }

  function start() {
    readFilters();
    applyFilters();
    tickTimes();
    fillDrawer();
    setInterval(poll, POLL_MS);
    setInterval(tickTimes, 30000);
    setInterval(showStale, 5000);
    window.addEventListener("focus", poll);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { poll(); }
    });
  }
  start();
}());
