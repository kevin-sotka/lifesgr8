/* =============================================================================
   Life's Gr8 - boot, routing, and theme

   The site is a single page with hash routes, which means GitHub Pages needs no
   rewrite rules and every section is still a link somebody can paste into the
   league chat.
   ========================================================================== */
(function (LG) {
  "use strict";
  var el = LG.el;

  var ROUTES = [
    { path: "/",          label: "The Card",    view: "card" },
    { path: "/paddock",   label: "The Paddock", view: "paddock" },
    { path: "/standings", label: "Standings",   view: "standings" },
    { path: "/draft",     label: "Draft Value", view: "draft" },
    { path: "/blotter",   label: "The Blotter", view: "blotter" },
    { path: "/form",      label: "The Form",    view: "form" },
    { path: "/recaps",    label: "Recaps",      view: "recaps" }
  ];

  var FILES = ["league", "standings", "races", "season", "draft",
               "blotter", "leaders", "players", "recaps", "newage"];

  LG.state = {};
  LG.data = null;

  /* --- theme --------------------------------------------------------------
     Silk hues are written into inline styles, and the light and dark steps are
     different colours, so a theme change has to re-render rather than just
     restyle. */
  var THEMES = ["auto", "light", "dark"];

  function readTheme() {
    try { return localStorage.getItem("lg8-theme") || "auto"; } catch (e) { return "auto"; }
  }
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("lg8-theme", t); } catch (e) { /* private window */ }
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.querySelector(".theme-toggle-label").textContent =
        t === "auto" ? "Auto" : t === "dark" ? "Dark" : "Light";
      btn.setAttribute("title", "Theme: " + t + ". Click to change.");
    }
  }

  function initTheme() {
    applyTheme(readTheme());
    var btn = document.getElementById("theme-toggle");
    btn.addEventListener("click", function () {
      var next = THEMES[(THEMES.indexOf(readTheme()) + 1) % THEMES.length];
      applyTheme(next);
      if (LG.data) LG.render();
    });
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onChange = function () { if (readTheme() === "auto" && LG.data) LG.render(); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  /* --- routing ------------------------------------------------------------ */
  function currentRoute() {
    var h = (location.hash || "#/").replace(/^#/, "");
    h = h.split("?")[0] || "/";
    return ROUTES.filter(function (r) { return r.path === h; })[0] || ROUTES[0];
  }

  function buildTabs() {
    var wrap = document.getElementById("tabs");
    wrap.textContent = "";
    ROUTES.forEach(function (r) {
      wrap.appendChild(el("a.tab", {
        href: "#" + r.path, text: r.label,
        "aria-current": r.path === currentRoute().path ? "page" : null
      }));
    });
  }

  /* --- render -------------------------------------------------------------
     A full re-render on every route change and every state change. With this
     much data it still lands well inside a frame, and it removes a whole class
     of stale-view bugs that a hand-rolled diff would have introduced. */
  LG.render = function () {
    var route = currentRoute();
    var host = document.getElementById("view");
    buildTabs();

    var fn = LG.views[route.view];
    host.textContent = "";
    try {
      host.appendChild(fn());
    } catch (err) {
      host.appendChild(el("div.fail",
        el("h2", { text: "That section failed to render" }),
        el("p", { text: String(err && err.message || err) }),
        el("p", { text: "The rest of the site is fine. Reload, or pick another tab." })));
      if (window.console) console.error(err);
    }

    document.title = (route.path === "/" ? "Life’s Gr8" : route.label + " · Life’s Gr8")
      + " · " + LG.data.league.season;
    armTracks();
  };

  /* Horses leave the gate when their race scrolls into view, not on load.
     Sixteen lanes animating off screen is work nobody sees. */
  function armTracks() {
    var tracks = document.querySelectorAll(".track");
    if (!tracks.length) return;
    if (!("IntersectionObserver" in window)) {
      tracks.forEach(function (t) { LG.chart.runTrack(t); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        LG.chart.runTrack(e.target);
        io.unobserve(e.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
    tracks.forEach(function (t) { io.observe(t); });
  }

  /* --- boot --------------------------------------------------------------- */
  function load() {
    return Promise.all(FILES.map(function (name) {
      return fetch("static/data/" + name + ".json", { cache: "no-cache" })
        .then(function (res) {
          if (!res.ok) throw new Error(name + ".json returned " + res.status);
          return res.json();
        })
        .then(function (json) { return [name, json]; });
    })).then(function (pairs) {
      var out = {};
      pairs.forEach(function (p) { out[p[0]] = p[1]; });
      return out;
    });
  }

  function fail(err) {
    document.getElementById("view").innerHTML = "";
    document.getElementById("view").appendChild(el("div.fail",
      el("h2", { text: "The data did not load" }),
      el("p", { text: String(err && err.message || err) }),
      el("p", null, "If you are opening this file directly from disk, the browser is "
        + "blocking the data files. Serve the folder instead: ",
        el("code", { text: "python3 -m http.server -d site/dist 8000" }))));
  }

  function start() {
    initTheme();
    load().then(function (data) {
      LG.data = data;

      var banner = document.getElementById("demo-banner");
      if (data.league.source === "dummy") banner.hidden = false;

      document.getElementById("brand-sub").textContent =
        data.league.season + " · week " + data.league.current_week;
      var built = new Date(data.league.built_at);
      var stamp = document.getElementById("built-at");
      stamp.textContent = built.toLocaleString("en-US", {
        weekday: "short", month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit"
      });
      stamp.setAttribute("datetime", data.league.built_at);

      window.addEventListener("hashchange", function () {
        LG.state.recapWeek = null;
        LG.render();
        document.getElementById("main").focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: "instant" });
      });
      LG.render();
    }).catch(fail);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else { start(); }
})(window.LG);
