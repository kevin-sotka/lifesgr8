/* =============================================================================
   Life's Gr8 - chart components

   Every mark here follows the same rules: thin geometry, a direct label so no
   value is carried by colour alone, a recessive rail rather than a heavy axis,
   and a hover layer for the detail that does not fit on screen.
   ========================================================================== */
(function (LG) {
  "use strict";
  var C = LG.chart = {};

  /* --- track position -----------------------------------------------------
     The JavaScript mirror of race_positions() in the generator. It lives in two
     places because the scrubber repositions horses client side from the weekly
     splits, and a scrubbed week has to land the horses exactly where the season
     view would have put them. */
  C.positions = function (values, direction, track) {
    var scale = (track && track.leader_scale) || 0.90;
    var floor = (track && track.position_floor) || 0.08;
    var basis;
    if (direction === "low") {
      var worst = Math.max.apply(null, values) || 1;
      basis = values.map(function (v) { return worst - v + 0.02 * worst; });
    } else {
      basis = values.slice();
    }
    var leader = Math.max.apply(null, basis) || 1;
    return basis.map(function (b) {
      return Math.max(floor, scale * (b / leader));
    });
  };

  /* --- the paddock --------------------------------------------------------
     Sixteen lanes. The horse is the mark, the trailing dust is its bar, and the
     value is printed at the rail so the lane never depends on hue to be read. */
  C.track = function (race, values, opts) {
    opts = opts || {};
    var pos = C.positions(values, race.direction, opts.track);
    var order = values.map(function (v, i) { return i; }).sort(function (a, b) {
      return race.direction === "low" ? values[a] - values[b] : values[b] - values[a];
    });

    var wrap = LG.el("div.track");
    wrap.appendChild(LG.el("span.finish", {
      "aria-hidden": "true",
      style: "left:calc(" + (opts.rail || 52) + "px + " +
             ((opts.track && opts.track.leader_scale) || 0.9) * 100 + "% - " +
             ((opts.rail || 52) + 70) + "px)"
    }));

    order.forEach(function (ti, place) {
      var hue = LG.hue(ti), t = LG.team(ti);
      var val = values[ti];
      var jockeys = C.jockeyLine(opts.jockeys && opts.jockeys[ti], race.unit);
      var lane = LG.el("div.lane" + (place === 0 ? ".is-leader" : "") +
                       (opts.jockeys ? ".has-jockeys" : ""), {
        "data-pattern": t.silk.pattern,
        style: "--silk:" + hue + ";--i:" + place,
        title: t.name + " · " + LG.ord(place + 1) + " · " +
               LG.fmt(val, val % 1 ? 1 : 0) + " " + LG.unit(val, race.unit) +
               (jockeys ? "\n" + jockeys.detail : "")
      });

      lane.appendChild(LG.el("div.lane-id",
        LG.el("span.lane-place.num", { text: place + 1 }),
        LG.silk(ti),
        LG.el("span.tcode", { text: t.code })));

      var prog = LG.el("div.lane-prog", { style: "--p:0" });
      prog.appendChild(LG.el("span.lane-dust", { "aria-hidden": "true" }));
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "lane-runner");
      svg.setAttribute("viewBox", "0 0 68 44");
      svg.setAttribute("aria-hidden", "true");
      var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", "#horse");
      svg.appendChild(use);
      prog.appendChild(svg);
      prog.dataset.target = pos[ti];

      lane.appendChild(LG.el("div.lane-track", LG.el("span.lane-rail"), prog));
      lane.appendChild(LG.el("div.lane-val", { text: LG.fmt(val, val % 1 ? 1 : 0) }));
      if (opts.jockeys) {
        // Like the jockey line on a race card: who actually rode this total in.
        lane.appendChild(LG.el("div.lane-jockeys", null, LG.el("span.jk", null,
          jockeys ? jockeys.names : "\u00A0",
          jockeys && jockeys.more ? LG.el("span.lane-more", { text: "+" + jockeys.more }) : null)));
      }
      wrap.appendChild(lane);
    });

    return wrap;
  };

  /* The players behind one lane's total. Accepts the season form, with values for
     the hover detail, or the compact per-week form the scrubber uses. */
  C.jockeyLine = function (entry, unit) {
    if (!entry) return null;
    var names = entry.names || (entry.top || []).map(function (j) { return j.name; });
    if (!names.length) return null;
    var detail = entry.top
      ? entry.top.map(function (j) {
          return j.name + " " + LG.fmt(j.value, j.value % 1 ? 1 : 0) + " " + LG.unit(j.value, unit);
        }).join(", ") + (entry.more ? ", and " + entry.more + " more" : "")
      : names.join(", ") + (entry.more ? ", and " + entry.more + " more" : "");
    return { names: names.join(", "), more: entry.more || 0, detail: detail };
  };

  /* Send the horses. Called when a race scrolls into view, and again whenever
     the scrubber moves. */
  C.runTrack = function (trackEl) {
    var lanes = trackEl.querySelectorAll(".lane-prog");
    // Force the starting style to be computed so the transition has something to
    // run from. requestAnimationFrame does the same job and was the first
    // attempt, but it does not fire in a background tab, which left a scrubbed
    // race standing at the gate until the tab happened to be looked at.
    void trackEl.offsetWidth;
    for (var i = 0; i < lanes.length; i++) {
      lanes[i].style.setProperty("--p", lanes[i].dataset.target);
    }
  };

  C.resetTrack = function (trackEl) {
    var lanes = trackEl.querySelectorAll(".lane-prog");
    for (var i = 0; i < lanes.length; i++) lanes[i].style.setProperty("--p", 0);
  };

  /* --- single-hue magnitude meter (playoff odds, efficiency) -------------- */
  C.meter = function (value, max, label) {
    var w = Math.max(0, Math.min(1, value / (max || 100))) * 100;
    return LG.el("div.dv",
      LG.el("div.meter", { title: label || "" }, LG.el("i", { style: "width:" + w + "%" })));
  };

  /* --- diverging bar (luck gap) -------------------------------------------
     Two poles and a neutral midpoint, never a rainbow. Positive means a team
     has more wins than its scoring earned. */
  C.diverging = function (value, absMax, unit) {
    var half = Math.min(1, Math.abs(value) / (absMax || 1)) * 50;
    var bar = LG.el("div.divbar", {
      title: LG.signed(value) + " " + (unit || "")
    }, LG.el("span.zero", { "aria-hidden": "true" }));
    bar.appendChild(LG.el("i" + (value >= 0 ? ".pos" : ".neg"), {
      style: "width:" + half + "%"
    }));
    return bar;
  };

  /* --- dumbbell (expected versus actual) ---------------------------------- */
  C.dumbbell = function (expected, actual, max) {
    var m = max || Math.max(expected, actual) || 1;
    var ex = Math.min(100, (expected / m) * 100);
    var ac = Math.min(100, (actual / m) * 100);
    var lo = Math.min(ex, ac), hi = Math.max(ex, ac);
    return LG.el("div.dumbbell", {
      title: "Expected " + LG.fmt(expected, 0) + ", actual " + LG.fmt(actual, 0)
    },
      LG.el("span.bar", { style: "left:" + lo + "%;width:" + (hi - lo) + "%" }),
      LG.el("span.pt.exp", { style: "left:" + ex + "%" }),
      LG.el("span.pt.act", { style: "left:" + ac + "%" }));
  };

  C.legend = function (items) {
    var row = LG.el("p.legend");
    items.forEach(function (it) {
      row.appendChild(LG.el("span", null,
        LG.el("i", { style: "background:" + it.color }), it.label));
    });
    return row;
  };

  /* --- weekly score sparkline --------------------------------------------
     One series, so no legend: the row it sits in names it. */
  C.spark = function (points, w, h) {
    w = w || 92; h = h || 22;
    var lo = Math.min.apply(null, points), hi = Math.max.apply(null, points);
    var span = (hi - lo) || 1;
    var step = points.length > 1 ? w / (points.length - 1) : w;
    var d = points.map(function (p, i) {
      return (i ? "L" : "M") + (i * step).toFixed(1) + " " +
             (h - 2 - ((p - lo) / span) * (h - 4)).toFixed(1);
    }).join(" ");
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.setAttribute("aria-hidden", "true");
    svg.style.display = "block";
    var path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("opacity", ".62");
    svg.appendChild(path);
    var last = document.createElementNS(ns, "circle");
    last.setAttribute("cx", ((points.length - 1) * step).toFixed(1));
    last.setAttribute("cy", (h - 2 - ((points[points.length - 1] - lo) / span) * (h - 4)).toFixed(1));
    last.setAttribute("r", "2.6");
    last.setAttribute("fill", "currentColor");
    svg.appendChild(last);
    return svg;
  };

  /* --- head to head grid --------------------------------------------------
     Coloured by margin on a diverging scale, labelled W or L so the result is
     never carried by colour alone. Blank means the two have not met. */
  C.h2hGrid = function (grid) {
    var teams = LG.data.league.teams;
    var maxMargin = 1;
    grid.forEach(function (row) {
      row.forEach(function (c) {
        if (c) maxMargin = Math.max(maxMargin, Math.abs(c.pf - c.pa));
      });
    });

    var table = LG.el("table.h2h");
    var head = LG.el("tr", LG.el("th.rowh", { scope: "col" }));
    teams.forEach(function (t) {
      head.appendChild(LG.el("th.colh", { scope: "col" }, LG.el("span", { text: t.code })));
    });
    table.appendChild(LG.el("thead", head));

    var body = LG.el("tbody");
    teams.forEach(function (t, i) {
      var tr = LG.el("tr", LG.el("th.rowh", { scope: "row" },
        LG.el("span.tname", LG.silk(i, "sm"), LG.el("span.tcode", { text: t.code }))));
      teams.forEach(function (o, j) {
        if (i === j) { tr.appendChild(LG.el("td.cell.self", { text: "•" })); return; }
        var c = grid[i][j];
        if (!c) {
          tr.appendChild(LG.el("td.cell", {
            title: t.name + " has not played " + o.name + " this season"
          }));
          return;
        }
        var margin = c.pf - c.pa;
        var k = Math.min(1, Math.abs(margin) / maxMargin);
        var hue = margin >= 0 ? "var(--div-pos)" : "var(--div-neg)";
        tr.appendChild(LG.el("td.cell", {
          style: "background:color-mix(in srgb," + hue + " " +
                 (12 + k * 62).toFixed(0) + "%, var(--surface))",
          text: c.w > c.l ? "W" : c.l > c.w ? "L" : "T",
          title: t.name + " vs " + o.name + ": " + c.w + "-" + c.l +
                 ", " + LG.fmt(c.pf) + " to " + LG.fmt(c.pa)
        }));
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    return LG.el("div.table-scroll", table);
  };
})(window.LG);
