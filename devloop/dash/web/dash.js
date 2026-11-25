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
    fillDrawer();
  }

  // Drawer: one shell outside #app-body. Its content is cloned from hidden sources that
  // ride inside every fragment, so an open drawer follows each poll.
  var lastFocus = null;
  var NO_SLICE = "This slice is no longer active.";

  function hashTarget() {
    var m = /^#slice=([A-Za-z0-9._:-]+)$/.exec(location.hash);
    return m ? { kind: "slice", key: m[1] } : null;
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
  function fillDrawer() {
    var drawer = $("drawer");
    var target = hashTarget();
    if (!target) {
      drawer.hidden = true;
      document.body.classList.remove("drawer-open");
      return;
    }
    var body = $("drawer-body");
    var keep = body.scrollTop;
    var source = document.getElementById("ds-" + target.key);
    var parts = [];
    if (source) {
      for (var i = 0; i < source.childNodes.length; i++) {
        parts.push(source.childNodes[i].cloneNode(true));
      }
    } else {
      parts.push(cardFallback(target.key) || note(NO_SLICE));
    }
    $("drawer-title").textContent = target.key;
    body.replaceChildren.apply(body, parts);
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

  function showStale() {
    $("stale").hidden = !(failures >= 2 || Date.now() - lastGood > STALE_MS);
  }

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
