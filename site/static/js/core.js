/* =============================================================================
   Life's Gr8 - shared helpers
   Every view is built from these. No framework, no build step, no runtime
   dependency: the site is files, and files are what GitHub Pages serves.
   ========================================================================== */
window.LG = window.LG || {};

(function (LG) {
  "use strict";

  /* --- tiny DOM builder --------------------------------------------------
     el("div.card", {id:"x"}, child, child) - the class shorthand keeps the view
     code readable, which matters a lot more here than any framework would. */
  LG.el = function el(spec, attrs) {
    var parts = String(spec).split(".");
    var node = document.createElement(parts.shift() || "div");
    if (parts.length) node.className = parts.join(" ");

    // arguments[1] is the attribute bag only when it is a plain object. A node,
    // an array, a string, or an explicit null all mean "no attributes, this is
    // already a child" - except null, which still occupies the slot.
    var first = 1;
    var isAttrs = attrs !== null && typeof attrs === "object" &&
                  !attrs.nodeType && !Array.isArray(attrs);
    if (isAttrs) {
      for (var k in attrs) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "text") node.textContent = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v === true ? "" : v);
      }
      first = 2;
    } else if (attrs === null || attrs === undefined) {
      first = 2;
    }

    for (var i = first; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  };

  function append(parent, c) {
    if (c === null || c === undefined || c === false || c === "") return;
    if (Array.isArray(c)) { c.forEach(function (x) { append(parent, x); }); return; }
    parent.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  }

  LG.frag = function () {
    var f = document.createDocumentFragment();
    for (var i = 0; i < arguments.length; i++) append(f, arguments[i]);
    return f;
  };

  /* --- numbers ----------------------------------------------------------- */
  LG.fmt = function (n, dp) {
    if (n === null || n === undefined || isNaN(n)) return "\u2013";
    var d = dp === undefined ? (Math.abs(n) >= 100 ? 0 : 1) : dp;
    return Number(n).toLocaleString("en-US", {
      minimumFractionDigits: d, maximumFractionDigits: d
    });
  };
  LG.signed = function (n, dp) {
    var s = LG.fmt(Math.abs(n), dp === undefined ? 2 : dp);
    return (n > 0 ? "+" : n < 0 ? "−" : "±") + s;
  };
  /* "1 sacks" reads like a typo on a page people screenshot for the group chat. */
  LG.unit = function (n, unit) {
    return Math.abs(n) === 1 && /[a-z]s$/.test(unit) ? unit.slice(0, -1) : unit;
  };
  LG.pct = function (n, dp) { return LG.fmt(n, dp === undefined ? 1 : dp) + "%"; };
  LG.record = function (r) {
    return r.wins + "-" + r.losses + (r.ties ? "-" + r.ties : "");
  };
  LG.ord = function (n) {
    var s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  };

  /* --- dates ------------------------------------------------------------- */
  var DTF = { month: "short", day: "numeric" };
  LG.date = function (iso) {
    return new Date(iso).toLocaleDateString("en-US", DTF);
  };
  LG.dateTime = function (iso) {
    var d = new Date(iso);
    return d.toLocaleDateString("en-US", DTF) + ", " +
           d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  };
  LG.dayKey = function (iso) {
    return new Date(iso).toLocaleDateString("en-US", {
      weekday: "long", month: "long", day: "numeric"
    });
  };

  /* --- teams and silks ---------------------------------------------------
     Sixteen teams cannot be distinguished by hue alone, so nothing here ever
     renders a silk without its three-letter code beside it. */
  LG.team = function (i) { return LG.data.league.teams[i]; };

  LG.silk = function (i, size) {
    var t = LG.team(i);
    return LG.el("span.silk" + (size ? "." + size : ""), {
      "data-pattern": t.silk.pattern,
      style: "--silk:" + LG.hue(i),
      "aria-hidden": "true"
    });
  };

  /* Resolve the silk hue for the mode the page is actually rendering in. The
     light and dark values are different steps of the same eight hues, each
     validated against its own surface. */
  LG.hue = function (i) {
    var t = LG.team(i);
    return LG.isDark() ? t.silk.dark : t.silk.light;
  };

  LG.isDark = function () {
    var set = document.documentElement.getAttribute("data-theme");
    if (set === "dark") return true;
    if (set === "light") return false;
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  };

  /* mode: "code" | "full" | "both" (default) */
  LG.teamName = function (i, mode, size) {
    var t = LG.team(i);
    var kids = [LG.silk(i, size)];
    if (mode !== "full") kids.push(LG.el("span.tcode", { text: t.code }));
    if (mode !== "code") kids.push(LG.el("span.tfull", { text: t.name }));
    return LG.el.apply(null, ["span.tname", { title: t.name }].concat(kids));
  };

  LG.player = function (key) {
    return LG.data.players.players[key] || { name: "Unknown", position: "", nfl_team: "" };
  };
  LG.playerLabel = function (key) {
    var p = LG.player(key);
    return LG.frag(
      LG.el("em", { text: p.name }),
      LG.el("span.ppos", { text: p.position + (p.nfl_team ? " · " + p.nfl_team : "") })
    );
  };

  /* --- card scaffold ------------------------------------------------------ */
  LG.card = function (title, opts) {
    opts = opts || {};
    var head = LG.el("div.card-head",
      LG.el("div", null,
        opts.eyebrow ? LG.el("div.eyebrow", { text: opts.eyebrow }) : null,
        LG.el(opts.small ? "h3" : "h2", { text: title }),
        opts.sub ? LG.el("p.sub", { text: opts.sub }) : null),
      opts.aside || null);
    var body = LG.el("div.card-body" + (opts.flush ? ".flush" : ""));
    var card = LG.el("div.card" + (opts.cls ? "." + opts.cls : ""), head, body);
    card.body = body;
    return card;
  };

  /* A row of mutually exclusive filter buttons. Returns the container; the
     caller gets the selected value through onPick. */
  LG.toggleRow = function (options, current, onPick) {
    var row = LG.el("div.btnrow");
    options.forEach(function (o) {
      var b = LG.el("button.btn", {
        type: "button",
        "aria-pressed": String(o.value === current),
        text: o.label,
        onclick: function () { onPick(o.value); }
      });
      row.appendChild(b);
    });
    return row;
  };

  /* --- sortable table -----------------------------------------------------
     Columns declare how to render and how to sort; sorting never repaints a
     team's silk, because colour follows the team and not its rank. */
  LG.table = function (columns, rows, opts) {
    opts = opts || {};
    var state = { key: opts.sort || columns[0].key, dir: opts.dir || -1 };

    var thead = LG.el("thead"), tbody = LG.el("tbody");
    var table = LG.el("table.data", thead, tbody);

    function head() {
      thead.textContent = "";
      var tr = LG.el("tr");
      columns.forEach(function (c) {
        var sortable = c.sort !== false;
        var th = LG.el("th" + (sortable ? ".sortable" : ""), {
          scope: "col", text: c.label, title: c.title || c.label,
          "aria-sort": state.key === c.key ? (state.dir < 0 ? "descending" : "ascending") : "none"
        });
        if (sortable) {
          th.tabIndex = 0;
          var go = function () {
            if (state.key === c.key) state.dir = -state.dir;
            else { state.key = c.key; state.dir = c.asc ? 1 : -1; }
            head(); body();
          };
          th.addEventListener("click", go);
          th.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
          });
        }
        tr.appendChild(th);
      });
      thead.appendChild(tr);
    }

    function body() {
      tbody.textContent = "";
      var col = columns.filter(function (c) { return c.key === state.key; })[0] || columns[0];
      var sorted = rows.slice().sort(function (a, b) {
        var va = col.value ? col.value(a) : a[col.key];
        var vb = col.value ? col.value(b) : b[col.key];
        if (typeof va === "string") return state.dir * va.localeCompare(vb);
        return state.dir * ((va || 0) - (vb || 0));
      });
      sorted.forEach(function (r, i) {
        var tr = LG.el("tr");
        if (opts.cut && opts.cut === i + 1) tr.className = "cut";
        columns.forEach(function (c) {
          var td = LG.el("td");
          var v = c.cell ? c.cell(r, i) : (c.value ? c.value(r) : r[c.key]);
          if (v && v.nodeType) td.appendChild(v);
          else td.textContent = v === null || v === undefined ? "\u2013" : v;
          if (c.cls) td.className = typeof c.cls === "function" ? c.cls(r) : c.cls;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    head(); body();
    return LG.el("div.table-scroll", table);
  };
})(window.LG);
