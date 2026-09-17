/* =============================================================================
   Life's Gr8 - views
   One function per section. Each returns a DocumentFragment; the router swaps
   them into #view. Nothing here fetches, and nothing here computes a statistic
   that the data files did not already carry.
   ========================================================================== */
(function (LG) {
  "use strict";
  var el = LG.el, V = LG.views = {};

  function byIndex(rows) {
    var m = [];
    rows.forEach(function (r) { m[r.team_index] = r; });
    return m;
  }
  function sortedStandings() {
    return LG.data.standings.rows.slice().sort(function (a, b) {
      return (b.wins - a.wins) || (b.pf - a.pf);
    });
  }

  /* =========================================================== THE CARD === */
  V.card = function () {
    var lg = LG.data.league, st = sortedStandings(), rows = byIndex(LG.data.standings.rows);
    var races = LG.data.races.races, weeks = LG.data.season.weeks;
    var latest = weeks[weeks.length - 1], recap = LG.data.recaps.recaps[0];
    var out = document.createDocumentFragment();

    /* --- hero ----------------------------------------------------------- */
    var lead = st[0];
    out.appendChild(el("section.card.hero", el("div.hero-inner",
      el("div.hero-eyebrow", { text: lg.name + " · " + lg.season + " season" }),
      el("h1", { text: "Through Week " + lg.completed_week }),
      el("p", { text: lg.num_teams + " teams, two IDP slots, a team defense, and a "
        + "scoring system with its own opinions about incompletions. Every number below "
        + "is computed from the league's own settings and checked against Yahoo." }),
      el("div.hero-stats",
        el("div.hero-stat", el("b", { text: LG.team(lead.team_index).code }),
          el("span", { text: "Top of the table" })),
        el("div.hero-stat", el("b", { text: LG.fmt(latest.awards.gold_cup.points) }),
          el("span", { text: "Week " + latest.week + " high" })),
        el("div.hero-stat", el("b", { text: lg.num_teams }),
          el("span", { text: "Teams running" })),
        el("div.hero-stat", el("b", { text: races.length }),
          el("span", { text: "Races on the card" }))))));

    /* --- storylines ------------------------------------------------------
       The three things anyone opening this page actually wants to know.
       Each story has a condition, because a luck gap after one week and a
       "streak" of one win are noise dressed up as news. */
    var rows = LG.data.standings.rows;
    var done = lg.completed_week;
    var candidates = [];

    /* "Robbed" means scoring well and losing anyway. A team that is last in
       points is not unlucky, it is bad, however sour its luck gap looks, so the
       search is restricted to the top half of the scoring table. */
    var byPf = rows.slice().sort(function (a, b) { return b.pf - a.pf; });
    var unlucky = byPf.slice(0, Math.ceil(byPf.length / 2))
      .sort(function (a, b) { return a.luck - b.luck; })[0];
    if (done >= 3 && unlucky && unlucky.luck <= -1) {
      var pfRank = byPf.indexOf(unlucky) + 1;
      candidates.push([LG.frag(LG.teamName(unlucky.team_index, "full"), " is being robbed"),
        LG.record(unlucky) + " while scoring " +
        (pfRank === 1 ? "more points than anybody else in the league"
                      : "the " + LG.ord(pfRank) + "-most points in the league") +
        ". The all-play record says this team has earned " + LG.fmt(unlucky.expected_wins, 1) +
        " wins and it has " + (unlucky.wins + unlucky.ties * 0.5) + ", a gap of " +
        LG.signed(unlucky.luck) + ". Nobody scoring this well has less to show for it."]);
    }

    var gold = latest.awards.gold_cup, sacko = latest.awards.sacko;
    candidates.push([LG.frag(LG.teamName(gold.team_index, "full"), " owned Week " + latest.week),
      LG.fmt(gold.points) + " points, the most in the league. " + LG.team(sacko.team_index).name +
      " finished at the other end with " + LG.fmt(sacko.points) + ", a spread of " +
      LG.fmt(gold.points - sacko.points) + "."]);

    var tight = races[0];
    var weeksLeft = (lg.regular_season_weeks - done) + " regular season weeks left";
    var top = tight.runners.filter(function (r) { return r.place === 1 || r.value === tight.runners[tight.leader].value; });
    if (tight.gap === 0) {
      candidates.push([tight.name + " is dead level",
        top.length + " teams share the lead at " + LG.fmt(tight.runners[tight.leader].value, 0) + " " +
        LG.unit(tight.runners[tight.leader].value, tight.unit) + " apiece, with " + weeksLeft + "."]);
    } else {
      candidates.push([tight.name + " is a photo finish",
        LG.frag(LG.teamName(tight.leader, "full"), " leads by " + LG.fmt(tight.gap) + " " +
          tight.unit + ", " + LG.pct(tight.gap_pct) + " of its own total. That is the tightest " +
          "race on the card with " + weeksLeft + ".")]);
    }

    var hot = rows.slice().sort(function (a, b) {
      var sa = a.streak[0] === "W" ? parseInt(a.streak.slice(1), 10) : 0;
      var sb = b.streak[0] === "W" ? parseInt(b.streak.slice(1), 10) : 0;
      return sb - sa || b.last3_ppg - a.last3_ppg;
    })[0];
    var steal = (LG.data.draft.steals || [])[0];
    if (hot && hot.streak[0] === "W" && parseInt(hot.streak.slice(1), 10) >= 3) {
      candidates.push([LG.frag(LG.teamName(hot.team_index, "full"), " is on a run"),
        hot.streak.slice(1) + " straight wins and " + LG.fmt(hot.last3_ppg) +
        " points a week over the last three. Playoff odds are now " + LG.pct(hot.playoff_pct) + "."]);
    } else if (steal) {
      candidates.push([steal.name + " is the early steal",
        LG.frag((steal.undrafted ? "Undrafted, " : "Pick " + steal.pick_number + ", ") +
          LG.fmt(steal.points, 1) + " points, " + LG.fmt(steal.value_over_slot, 1) +
          " above what that slot usually produces by now. Credit to ",
          LG.teamName(steal.team_index, "full"), ".")]);
    }

    var stories = LG.card("Needs your attention", { flush: true, eyebrow: "The lead" });
    candidates.slice(0, 3).forEach(function (c, i) {
      stories.body.appendChild(el("div.story",
        el("div.story-mark", { text: String(i + 1) }),
        el("div", null, el("h4", null, c[0]), el("p", null, c[1]))));
    });
    out.appendChild(stories);

    /* --- the purse and the stat of the week -------------------------------- */
    var pc = V.purseCard();
    if (pc) out.appendChild(pc);
    var na = LG.data.newage;
    if (na && na.headline && na.boards[na.headline]) out.appendChild(V.statOfWeek(na));

    /* --- two column: standings snapshot + awards ------------------------- */
    var grid = el("div.grid.side");

    var snap = LG.card("The table", {
      eyebrow: "Standings", flush: true,
      aside: el("a.chip", { href: "#/standings", text: "Full standings" })
    });
    snap.body.appendChild(LG.table([
      { key: "rank", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
      { key: "team", label: "Team", sort: false, value: function (r) { return LG.team(r.team_index).name; },
        cell: function (r) { return LG.teamName(r.team_index); } },
      { key: "rec", label: "Rec", sort: false, cell: LG.record },
      { key: "pf", label: "PF", cell: function (r) { return LG.fmt(r.pf, 0); } },
      { key: "luck", label: "Luck", title: "Wins above or below what the all-play record earned",
        cell: function (r) { return LG.signed(r.luck, 1); },
        cls: function (r) { return r.luck > 0.5 ? "pos-good" : r.luck < -0.5 ? "pos-bad" : ""; } },
      { key: "playoff_pct", label: "PO%", title: "Odds of a top " + LG.data.league.playoff_teams + " finish, 4,000 simulated seasons",
        cell: function (r) { return LG.fmt(r.playoff_pct, 0) + "%"; } }
    ], st.slice(0, LG.data.league.playoff_teams + 2), { sort: "rank", cut: LG.data.league.playoff_teams }));
    snap.body.appendChild(el("p.sub", { style: "padding:10px 16px 0",
      text: "The dashed line is the playoff cut: top " + LG.data.league.playoff_teams + " make the winners bracket. Odds come from four thousand simulations of the commissioner's remaining schedule." }));
    grid.appendChild(snap);

    var aw = LG.card("Week " + latest.week + " hardware", { eyebrow: "Awards", flush: true });
    var box = el("div.awards");
    [["gold_cup", "pts"], ["sacko", "pts"], ["blowout", "margin"],
     ["nailbiter", "margin"], ["regret", "pts"], ["trench", "pts"]].forEach(function (pair) {
      var a = latest.awards[pair[0]];
      if (!a) return;
      var value = a.points !== undefined ? a.points : a.margin;
      box.appendChild(el("div.award",
        el("div.award-label", { text: a.label }),
        el("div.award-team", LG.silk(a.team_index), LG.team(a.team_index).code,
          el("span.tmgr", { text: LG.team(a.team_index).name })),
        el("div.award-val.num", { text: LG.fmt(value) + " " + pair[1] }),
        el("div.award-note", { text: a.player_key ? LG.player(a.player_key).name : a.note })));
    });
    aw.body.appendChild(box);
    grid.appendChild(aw);
    out.appendChild(grid);

    /* --- three tightest races ------------------------------------------- */
    var rc = LG.card("Tightest on the card", {
      eyebrow: "The Paddock", flush: true,
      sub: "Ranked by how little separates the leader from second.",
      aside: el("a.chip", { href: "#/paddock", text: "All " + races.length + " races" })
    });
    races.slice(0, 3).forEach(function (race) {
      var vals = race.runners.map(function (r) { return r.value; });
      var sec = el("div.race",
        el("div.race-head",
          el("div", null,
            el("h3.race-title", { text: race.name }),
            el("p.race-blurb", { text: race.blurb })),
          el("div.race-gap",
            el("b.num", { text: LG.fmt(race.gap) }),
            el("span", { text: "lead, " + race.unit }))),
        LG.chart.track(race, vals, { track: LG.data.races.track, jockeys: V.jockeysFor(race, 0) }));
      rc.body.appendChild(sec);
    });
    out.appendChild(rc);

    /* --- recap teaser + blotter strip ------------------------------------ */
    var bottom = el("div.grid.two");
    if (recap) {
      var rp = LG.card(recap.headline, {
        eyebrow: "The Tuesday recap",
        aside: el("a.chip", { href: "#/recaps", text: "Read all" })
      });
      rp.body.appendChild(el("div.recap-body",
        el("p.lede", { text: recap.lede }),
        el("p", { text: recap.paragraphs[0] })));
      bottom.appendChild(rp);
    } else {
      bottom.appendChild(V.noRecap());
    }

    var bl = LG.card("Latest moves", {
      eyebrow: "The Blotter", flush: true,
      aside: el("a.chip", { href: "#/blotter", text: "Full feed" })
    });
    LG.data.blotter.transactions.slice(0, 6).forEach(function (t) {
      bl.body.appendChild(V.txnRow(t, true));
    });
    bottom.appendChild(bl);
    out.appendChild(bottom);

    return out;
  };

  /* Jockeys indexed by team: the season list, or the list as it stood after a
     given week when the scrubber is in use. */
  V.jockeysFor = function (race, week) {
    if (week && race.split_jockeys && race.split_jockeys[week - 1]) return race.split_jockeys[week - 1];
    var byTeam = [];
    (race.runners || []).forEach(function (r) { byTeam[r.team_index] = r.jockeys; });
    return byTeam.some(Boolean) ? byTeam : null;
  };

  /* ========================================================= THE PADDOCK == */
  V.paddock = function () {
    var out = document.createDocumentFragment();
    var races = LG.data.races.races, weeks = LG.data.league.completed_week;
    var mode = LG.state.paddockWeek === undefined ? 0 : LG.state.paddockWeek; // 0 = season

    out.appendChild(el("div.card",
      el("div.card-head", el("div", null,
        el("div.eyebrow", { text: "The Paddock" }),
        el("h2", { text: LG.data.league.num_teams + " horses, " + races.length + " races" }),
        el("p.sub", { text: "Every race counts started players only. A linebacker who "
          + "put up fourteen tackles on your bench did not run in this race. Horses are "
          + "placed relative to the leader, who sits at ninety percent of the rail." })))));

    var scrubWrap = el("div.card");
    var head = el("div.card-head", el("div", null,
      el("div.eyebrow", { text: "Replay" }),
      el("h3", { text: "Run the season back" })),
      el("button.btn", { type: "button", text: "Re-run the races", onclick: function () {
        document.querySelectorAll(".track").forEach(function (t) {
          LG.chart.resetTrack(t);
          setTimeout(function () { LG.chart.runTrack(t); }, 60);
        });
      } }));
    var output = el("output", { text: mode ? "Week " + mode : "Season" });
    var range = el("input", {
      type: "range", min: "0", max: String(weeks), value: String(mode),
      "aria-label": "Show cumulative totals through a given week"
    });
    range.addEventListener("input", function () {
      var v = Number(range.value);
      output.textContent = v ? "Week " + v : "Season";
      LG.state.paddockWeek = v;
      repositionAll(v);
    });
    scrubWrap.appendChild(head);
    scrubWrap.appendChild(el("div.scrub",
      el("label", { text: "Through" }), range, output));
    scrubWrap.appendChild(el("div.card-body",
      el("p.sub", { text: "Slide to watch the field change shape week by week. "
        + "Zero shows the full season to date." })));
    out.appendChild(scrubWrap);

    races.forEach(function (race) {
      var card = LG.card(race.name, {
        small: false, flush: true,
        eyebrow: race.direction === "low" ? "Low score wins" : "Stakes race",
        sub: race.blurb,
        aside: el("div.race-gap",
          el("b.num", { text: LG.fmt(race.gap) }),
          el("span", { text: "lead, " + race.unit }))
      });
      var vals = valuesFor(race, mode);
      var track = LG.chart.track(race, vals, { track: LG.data.races.track, jockeys: V.jockeysFor(race, mode) });
      track.dataset.raceId = race.id;
      card.body.appendChild(track);
      card.body.appendChild(el("p.legend", { style: "padding:10px 16px 0" },
        el("span", null, "Leader: ", LG.teamName(race.leader, "full", "sm")),
        el("span", { text: "Second is " + LG.fmt(race.gap) + " " + race.unit + " back" })));
      out.appendChild(card);
    });

    function valuesFor(race, week) {
      if (!week) return race.runners.map(function (r) { return r.value; });
      return race.splits[week - 1];
    }
    function repositionAll(week) {
      races.forEach(function (race) {
        var track = document.querySelector('.track[data-race-id="' + race.id + '"]');
        if (!track) return;
        var fresh = LG.chart.track(race, valuesFor(race, week),
          { track: LG.data.races.track, jockeys: V.jockeysFor(race, week) });
        fresh.dataset.raceId = race.id;
        track.replaceWith(fresh);
        LG.chart.runTrack(fresh);
      });
    }

    return out;
  };

  /* ============================================================ PURSE ===
     The commissioner pays the weekly high scores at the end of the season, so the
     board is cumulative: this week's money on The Card, the running tally and the
     week by week log on Standings. */
  V.purseOrdinal = function (place) { return LG.ord(place); };

  V.money = function (amount) {
    var p = LG.data.season.purse;
    return (p.currency || "$") + LG.fmt(amount, amount % 1 ? 2 : 0);
  };

  V.purseWeek = function (paid, compact) {
    var list = el("ol.purse-list");
    paid.forEach(function (p) {
      list.appendChild(el("li",
        el("span.purse-place.num", { text: V.purseOrdinal(p.place) + (p.tied ? "=" : "") }),
        el("span.purse-team", null, LG.teamName(p.team_index, compact ? undefined : "full")),
        el("span.purse-pts.num", { text: LG.fmt(p.points, 2) }),
        el("span.purse-money.num", { text: V.money(p.amount) })));
    });
    return list;
  };

  V.purseCard = function () {
    var purse = LG.data.season.purse;
    if (!purse || !purse.weeks.length) return null;
    var latest = purse.weeks[0];
    var card = LG.card("In the money, Week " + latest.week, {
      eyebrow: "The Purse", flush: true,
      sub: "Top " + purse.places + " scores every week, worth "
         + purse.amounts.map(V.money).join(", ") + ". The commissioner pays it out at the "
         + "end of the season.",
      aside: el("a.chip", { href: "#/standings", text: "Season tally" })
    });
    card.body.appendChild(el("div", { style: "padding:0 16px" }, V.purseWeek(latest.paid, true)));
    return card;
  };

  /* ========================================================= NEW-AGE ===
     One modern stat headlines each week and the four rotate. Every value and rank
     arrives computed; these functions only format and lay it out. */
  V.naValue = function (board, v) {
    if (board.format === "percent") return Math.round(v * 100) + "%";
    if (board.format === "signed") return LG.signed(v, 1);
    return LG.fmt(v, 1);
  };

  V.naDetail = function (id, d) {
    if (id === "poe") return LG.fmt(d.actual_per_game, 1) + " actual, " + LG.fmt(d.expected_per_game, 1) + " expected";
    if (id === "first_read") return d.first_read_targets + " of " + d.charted_targets + " charted targets";
    if (id === "explosive_run") return d.explosive_runs + " of " + d.carries + " carries";
    if (id === "rz_share") return d.opportunities + " of " + d.team_opportunities + " team looks";
    return "";
  };

  V.naRank = function (row) { return (row.tied ? "T-" : "") + row.nfl_rank; };

  V.naBar = function (board, rows, row) {
    if (board.format === "signed") {
      var max = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.value); })) || 1;
      // Two-sided only when the rows actually straddle zero; otherwise half the bar is dead space.
      var mixed = rows.some(function (r) { return r.value < 0; }) && rows.some(function (r) { return r.value > 0; });
      if (!mixed) return LG.chart.meter(Math.abs(row.value), max, V.naValue(board, row.value));
      return LG.chart.diverging(row.value, max, board.unit);
    }
    return LG.chart.meter(row.value * 100, 100, V.naValue(board, row.value));
  };

  V.newAgeTable = function (board, rows) {
    return LG.table([
      { key: "nfl_rank", label: "NFL", title: "Rank among every qualifying NFL player", sort: false,
        cls: "rk", cell: V.naRank },
      { key: "name", label: "Player", sort: false, cell: function (r) {
          return el("span", null, el("em", { text: r.name }), " ",
            el("span.ppos", { text: r.position + " \u00B7 " + r.nfl_team })); } },
      { key: "team", label: "Roster", sort: false, cell: function (r) { return LG.teamName(r.team_index); } },
      { key: "bar", label: "", sort: false, cell: function (r) { return V.naBar(board, rows, r); } },
      { key: "value", label: board.short, sort: false, cell: function (r) { return V.naValue(board, r.value); } },
      { key: "detail", label: "Sample", sort: false, cls: "ppos", cell: function (r) { return V.naDetail(board.id, r.detail); } }
    ], rows, { sort: "nfl_rank", dir: 1 });
  };

  V.newAgeFooter = function (board, na) {
    return el("p.sub", { style: "padding:12px 16px 0" },
      board.how + " Through Week " + na.data_through_week + ", " + board.qualifiers +
      " NFL players qualified, " + board.rostered_qualifiers + " of them on Life\u2019s Gr8 rosters. Source: " +
      board.source + ".");
  };

  V.statOfWeek = function (na) {
    var board = na.boards[na.headline], fact = na.headline_fact;
    var card = LG.card(board.name, {
      eyebrow: "Stat of the week \u00B7 Week " + na.week,
      sub: board.blurb,
      aside: el("a.chip", { href: "#/form", text: "All four boards" })
    });
    if (fact) {
      card.body.appendChild(el("div.sotw",
        el("div.sotw-num", null,
          el("b.num", { text: fact.display }),
          el("span", { text: board.unit })),
        el("div.sotw-who", null,
          LG.teamName(fact.team_index, undefined, "lg"),
          el("p", { text: fact.sentence }))));
    }
    var list = el("ol.sotw-list");
    board.leaders.slice(0, 5).forEach(function (r) {
      list.appendChild(el("li", null,
        el("span.sotw-rank.num", { text: V.naRank(r) }),
        el("span.sotw-name", null, LG.silk(r.team_index, "sm"), " ", el("span.tcode", { text: LG.team(r.team_index).code }),
          " ", el("em", { text: r.short }), el("span.ppos", { text: " " + r.position + " \u00B7 " + LG.team(r.team_index).name })),
        el("span.sotw-bar", null, V.naBar(board, board.leaders.slice(0, 5), r)),
        el("span.sotw-val.num", { text: V.naValue(board, r.value) })));
    });
    card.body.appendChild(list);
    var rot = el("div.sotw-rotation");
    na.rotation.forEach(function (id) {
      rot.appendChild(el("span.chip" + (id === na.headline ? ".live" : ""), { text: na.boards[id].short }));
    });
    card.body.appendChild(el("div.sotw-foot", null,
      el("span.ppos", { text: "Rotation, one a week, repeating:" }), rot));
    card.body.appendChild(el("p.sub", { style: "margin-top:10px", text: "Through Week " + na.data_through_week +
      ". Ranked against all " + board.qualifiers + " qualifying NFL players. Source: " + board.source + "." }));
    return card;
  };

  /* ========================================================== STANDINGS == */
  V.standings = function () {
    var out = document.createDocumentFragment();
    var rows = LG.data.standings.rows, st = sortedStandings();
    var maxLuck = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.luck); }));

    var main = LG.card("Standings", {
      eyebrow: "Week " + LG.data.standings.week, flush: true,
      sub: "Actual record, the all-play record it should have produced, and the gap "
         + "between them. Tap any column header to sort."
    });
    main.body.appendChild(LG.table([
      { key: "rank", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
      { key: "team", label: "Team", value: function (r) { return LG.team(r.team_index).name; },
        asc: true, cell: function (r) { return LG.teamName(r.team_index); } },
      { key: "wins", label: "Rec", sort: false, cell: LG.record },
      { key: "pf", label: "PF", cell: function (r) { return LG.fmt(r.pf, 1); } },
      { key: "pa", label: "PA", cell: function (r) { return LG.fmt(r.pa, 1); } },
      { key: "ppg", label: "PPG", cell: function (r) { return LG.fmt(r.ppg, 1); } },
      { key: "all_play_pct", label: "All-play", title: "Record against the whole league every week",
        cell: function (r) { return r.all_play_wins + "-" + r.all_play_losses; } },
      { key: "expected_wins", label: "xW", title: "Wins the all-play record earned",
        cell: function (r) { return LG.fmt(r.expected_wins, 1); } },
      { key: "luck", label: "Luck", title: "Actual wins minus expected wins",
        cell: function (r) { return LG.signed(r.luck, 1); },
        cls: function (r) { return r.luck > 0.5 ? "pos-good" : r.luck < -0.5 ? "pos-bad" : ""; } },
      { key: "efficiency", label: "Eff%", title: "Points scored as a share of the best lineup available",
        cell: function (r) { return LG.fmt(r.efficiency, 1); } },
      { key: "streak", label: "Run", sort: false, cell: function (r) { return r.streak; } },
      { key: "form", label: "Form", sort: false, cell: function (r) {
          return LG.chart.spark(r.weekly.map(function (w) { return w.points; })); } },
      { key: "playoff_pct", label: "PO%", title: "Odds of a top " + LG.data.league.playoff_teams + " finish, 4,000 simulations",
        cell: function (r) { return LG.fmt(r.playoff_pct, 1) + "%"; } }
    ], st, { sort: "rank", cut: LG.data.league.playoff_teams }));
    out.appendChild(main);

    /* --- luck gap -------------------------------------------------------- */
    var luck = LG.card("The luck gap", {
      eyebrow: "Wins above expectation", flush: true,
      sub: "A fantasy record is half scoring and half schedule. All-play removes the "
         + "schedule: it scores every team against all fifteen opponents it did not play, "
         + "every week. The bar is how many wins a team has above or below what its "
         + "scoring actually earned."
    });
    var lt = LG.table([
      { key: "team", label: "Team", value: function (r) { return LG.team(r.team_index).name; },
        asc: true, cell: function (r) { return LG.teamName(r.team_index); } },
      { key: "luck", label: "Gap", cell: function (r) { return LG.signed(r.luck, 2); },
        cls: function (r) { return r.luck > 0.5 ? "pos-good" : r.luck < -0.5 ? "pos-bad" : ""; } },
      { key: "chart", label: "Lucky →", sort: false, cell: function (r) {
          return LG.chart.diverging(r.luck, maxLuck, "wins"); } },
      { key: "wins", label: "Actual", sort: false, cell: LG.record },
      { key: "expected_wins", label: "Deserved", sort: false, cell: function (r) {
          var g = r.wins + r.losses + r.ties;
          return LG.fmt(r.expected_wins, 1) + "-" + LG.fmt(g - r.expected_wins, 1); } }
    ], rows, { sort: "luck" });
    luck.body.appendChild(lt);
    luck.body.appendChild(LG.chart.legend([
      { color: "var(--div-pos)", label: "More wins than the scoring earned" },
      { color: "var(--div-neg)", label: "Fewer wins than the scoring earned" }
    ]));
    out.appendChild(luck);

    /* --- the purse -------------------------------------------------------- */
    var purse = LG.data.standings.purse || LG.data.season.purse;
    if (purse && purse.weeks.length) {
      var pc = LG.card("The Purse", {
        eyebrow: "Weekly high scores", flush: true,
        sub: "The commissioner tracks the top " + purse.places + " scores every week: "
           + purse.amounts.map(function (a, i) { return V.purseOrdinal(i + 1) + " " + V.money(a); }).join(", ")
           + ". Paid out at the end of the season. A tie at the cut pays everyone tied. "
           + V.money(purse.paid_so_far) + " claimed so far."
      });
      pc.body.appendChild(LG.table([
        { key: "rk", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
        { key: "team", label: "Team", sort: false, cell: function (r) { return LG.teamName(r.team_index); } },
        { key: "weeks_in_the_money", label: "In the money",
          cell: function (r) { return r.weeks_in_the_money; } },
        { key: "first", label: "1st", value: function (r) { return r.places[0]; },
          cell: function (r) { return r.places[0] || "\u2013"; } },
        { key: "second", label: "2nd", value: function (r) { return r.places[1]; },
          cell: function (r) { return r.places[1] || "\u2013"; } },
        { key: "third", label: "3rd", value: function (r) { return r.places[2]; },
          cell: function (r) { return r.places[2] || "\u2013"; } },
        { key: "fourth", label: "4th", value: function (r) { return r.places[3]; },
          cell: function (r) { return r.places[3] || "\u2013"; } },
        { key: "points", label: "Points in the money",
          cell: function (r) { return LG.fmt(r.points, 1); } },
        { key: "money", label: "Won", cell: function (r) { return V.money(r.money); } }
      ], purse.tally.filter(function (r) { return r.weeks_in_the_money > 0; }),
         { sort: "money" }));

      var log = el("div.purse-log");
      purse.weeks.forEach(function (w) {
        log.appendChild(el("div.purse-week",
          el("div.day-head", { text: "Week " + w.week }),
          V.purseWeek(w.paid)));
      });
      pc.body.appendChild(log);
      out.appendChild(pc);
    }

    /* --- power rankings -------------------------------------------------- */
    var pw = LG.card("Power rankings", {
      eyebrow: "Half all-play, a third scoring, the rest recent form", flush: true,
      sub: "Deliberately not the record. The record is already above."
    });
    pw.body.appendChild(LG.table([
      { key: "power_rank", label: "#", asc: true, cls: "rk",
        cell: function (r) { return r.power_rank; } },
      { key: "team", label: "Team", sort: false, cell: function (r) { return LG.teamName(r.team_index); } },
      { key: "wins", label: "Rec", sort: false, cell: LG.record },
      { key: "last3_ppg", label: "Last 3", cell: function (r) { return LG.fmt(r.last3_ppg, 1); } },
      { key: "all_play_pct", label: "All-play%", cell: function (r) { return LG.pct(r.all_play_pct * 100); } },
      { key: "playoff_pct", label: "Playoffs", sort: false, cell: function (r) {
          return LG.chart.meter(r.playoff_pct, 100, LG.pct(r.playoff_pct)); } },
      { key: "po", label: "", sort: false, cell: function (r) { return LG.pct(r.playoff_pct); } }
    ], rows, { sort: "power_rank", dir: 1 }));
    out.appendChild(pw);

    return out;
  };
  /* ======================================================== DRAFT VALUE == */
  V.draft = function () {
    var out = document.createDocumentFragment();
    var d = LG.data.draft;
    var maxPts = Math.max.apply(null, d.all.map(function (e) {
      return Math.max(e.points, e.expected); }));

    out.appendChild(el("div.card", el("div.card-head", el("div", null,
      el("div.eyebrow", { text: "Draft value" }),
      el("h2", { text: "Who beat their slot" }),
      el("p.sub", { text: "Expected production is not a guess: it is the " + d.baseline.method
        + ". Undrafted players are priced one slot past the end of the draft, which is "
        + "why a good waiver add reads as an enormous win. Early in the season every "
        + "number here is small and one big game moves it a long way." })))));

    function board(title, eyebrow, list, tone) {
      var card = LG.card(title, { eyebrow: eyebrow, flush: true });
      card.body.appendChild(LG.table([
        { key: "rk", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
        { key: "name", label: "Player", asc: true, cell: function (r) {
            return el("span.tname",
              el("span", null, el("em", { text: r.name }), " ",
                el("span.ppos", { text: r.position + " · " + r.nfl_team }))); } },
        { key: "team", label: "Roster", sort: false, cell: function (r) {
            return LG.teamName(r.team_index); } },
        { key: "pick_number", label: "Pick", cell: function (r) {
            return r.undrafted ? el("span.chip", { text: "UDFA" }) : "#" + r.pick_number; } },
        { key: "points", label: "Actual", cell: function (r) { return LG.fmt(r.points, 1); } },
        { key: "expected", label: "Expected", cell: function (r) { return LG.fmt(r.expected, 1); } },
        { key: "chart", label: "", sort: false, cell: function (r) {
            return LG.chart.dumbbell(r.expected, r.points, maxPts); } },
        { key: "value_over_slot", label: "Value", cell: function (r) {
            return LG.signed(r.value_over_slot, 1); },
          cls: function () { return tone; } },
        { key: "missed", label: "Missed", title: "Weeks unavailable. Shown, never adjusted away.",
          cell: function (r) { return r.missed ? r.missed + "w" : "\u2013"; } }
      ], list, { sort: "value_over_slot", dir: tone === "pos-good" ? -1 : 1 }));
      return card;
    }

    out.appendChild(board("Biggest steals", "The board that wins arguments",
      d.steals.slice(0, 15), "pos-good"));

    var busts = board("Biggest busts", "Handle with care", d.busts.slice(0, 15), "pos-bad");
    busts.body.appendChild(el("p.sub", { style: "padding:12px 16px 0",
      text: "A player who tore a knee in Week 2 is technically the worst value in league "
        + "history and that is not interesting. Weeks missed sits in its own column so the "
        + "board can tell a bust apart from an injury rather than quietly adjusting one "
        + "into the other." }));
    out.appendChild(busts);

    /* --- per team -------------------------------------------------------- */
    var tt = d.team_totals.slice().sort(function (a, b) {
      return b.value_over_slot - a.value_over_slot; });
    var maxAbs = Math.max.apply(null, tt.map(function (t) {
      return Math.abs(t.value_over_slot); }));
    var team = LG.card("Value over slot, by team", {
      eyebrow: "The Claiming Stakes", flush: true,
      sub: "Every rostered player's value over slot, added up. This is the same number "
         + "that runs The Claiming Stakes in The Paddock."
    });
    team.body.appendChild(LG.table([
      { key: "rk", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
      { key: "team", label: "Team", sort: false, cell: function (r) { return LG.teamName(r.team_index); } },
      { key: "value_over_slot", label: "Value", cell: function (r) {
          return LG.signed(r.value_over_slot, 1); },
        cls: function (r) { return r.value_over_slot > 0 ? "pos-good" : "pos-bad"; } },
      { key: "chart", label: "Above the board →", sort: false, cell: function (r) {
          return LG.chart.diverging(r.value_over_slot, maxAbs, "points"); } }
    ], tt, { sort: "value_over_slot" }));
    team.body.appendChild(LG.chart.legend([
      { color: "var(--div-pos)", label: "Roster is beating its draft slots" },
      { color: "var(--div-neg)", label: "Roster is behind its draft slots" }
    ]));
    out.appendChild(team);

    /* --- the baseline itself --------------------------------------------- */
    var base = LG.card("The baseline", { eyebrow: "Show your work", flush: true,
      sub: "Expected season points by region of the draft board, fitted from this "
         + "league's own results. Published so the board can be argued with." });
    base.body.appendChild(LG.table([
      { key: "pick", label: "Around pick", asc: true, sort: false,
        cell: function (r) { return "#" + LG.fmt(r[0], 0); } },
      { key: "exp", label: "Expected points to date", sort: false,
        cell: function (r) { return LG.fmt(r[1], 1); } }
    ], d.baseline.global, { sort: "pick", dir: 1 }));
    out.appendChild(base);

    return out;
  };

  /* =========================================================== BLOTTER === */
  V.txnRow = function (t, compact) {
    var pending = t.status === "pending";
    var row = el("div.txn" + (pending ? ".pending" : ""));
    var head = el("div.txn-head");
    t.teams.forEach(function (ti, n) {
      if (n) head.appendChild(el("span.tmgr", { text: "↔" }));
      head.appendChild(LG.teamName(ti));
    });
    if (pending) head.appendChild(el("span.chip.live", { text: t.type === "pending_trade" ? "Pending trade" : "Pending claim" }));
    else if (t.type === "trade") head.appendChild(el("span.chip", { text: "Trade" }));
    row.appendChild(head);
    row.appendChild(el("div.txn-time", { text: LG.dateTime(t.timestamp) }));

    var line = el("div.txn-line");
    if (t.sends) {
      t.sends.forEach(function (s) {
        line.appendChild(el("span.mv",
          el("span.mv-out", { text: LG.team(s.team_index).code }),
          el("span", { text: "→" }), LG.playerLabel(s.player_key)));
      });
    } else {
      if (t.added) line.appendChild(el("span.mv",
        el("span.mv-in", { text: "+" }), LG.playerLabel(t.added),
        t.faab ? el("span.ppos", { text: "$" + t.faab }) : null));
      if (t.dropped) line.appendChild(el("span.mv",
        el("span.mv-out", { text: "−" }), LG.playerLabel(t.dropped)));
    }
    if (!compact && t.note) line.appendChild(el("span.ppos", { text: t.note }));
    row.appendChild(line);
    return row;
  };

  V.blotter = function () {
    var out = document.createDocumentFragment();
    var all = LG.data.blotter.transactions;
    var filter = LG.state.blotterFilter || "all";
    var teamFilter = LG.state.blotterTeam === undefined ? -1 : LG.state.blotterTeam;

    out.appendChild(el("div.card", el("div.card-head", el("div", null,
      el("div.eyebrow", { text: "The Blotter" }),
      el("h2", { text: "Every add, drop, and trade" }),
      el("p.sub", { text: "Updated daily. Pending claims and trades are pinned at the top "
        + "when there are any, but Yahoo only reveals pending activity to the teams "
        + "involved, so this feed can only show them for the team whose Yahoo login "
        + "builds the site." })))));

    var card = LG.card("Feed", { flush: true, eyebrow: all.length + " moves this season" });

    card.body.appendChild(el("div", { style: "padding:0 16px" },
      LG.toggleRow([
        { value: "all", label: "Everything" },
        { value: "pending", label: "Pending" },
        { value: "add", label: "Adds" },
        { value: "drop", label: "Drops" },
        { value: "trade", label: "Trades" }
      ], filter, function (v) { LG.state.blotterFilter = v; LG.render(); })));

    var teamOpts = [{ value: -1, label: "All teams" }].concat(
      LG.data.league.teams.map(function (t) { return { value: t.team_index, label: t.code }; }));
    card.body.appendChild(el("div", { style: "padding:0 16px" },
      LG.toggleRow(teamOpts, teamFilter, function (v) { LG.state.blotterTeam = v; LG.render(); })));

    var shown = all.filter(function (t) {
      if (teamFilter >= 0 && t.teams.indexOf(teamFilter) < 0) return false;
      if (filter === "all") return true;
      if (filter === "pending") return t.status === "pending";
      if (filter === "trade") return t.type === "trade" || t.type === "pending_trade";
      if (filter === "add") return !!t.added;
      if (filter === "drop") return !!t.dropped;
      return true;
    });

    if (!shown.length) {
      card.body.appendChild(el("p.empty", { text: "Nothing matches that filter." }));
    } else {
      var day = null;
      shown.slice(0, 180).forEach(function (t) {
        var k = LG.dayKey(t.timestamp);
        if (k !== day) { day = k; card.body.appendChild(el("div.day-head", { text: k })); }
        card.body.appendChild(V.txnRow(t));
      });
      if (shown.length > 180) {
        card.body.appendChild(el("p.empty", {
          text: "Showing the most recent 180 of " + shown.length + " moves." }));
      }
    }
    out.appendChild(card);
    return out;
  };

  /* ============================================================ RECAPS === */
  V.noRecap = function () {
    var lg = LG.data.league;
    var card = LG.card("The first recap is on its way", { eyebrow: "The Tuesday recap" });
    card.body.appendChild(el("p.sub", { text: "Doc files his recap every Tuesday morning once "
      + "Monday night is final. Week " + Math.max(1, lg.completed_week) + " is on the editor's desk." }));
    return card;
  };

  V.recaps = function () {
    var out = document.createDocumentFragment();
    var recaps = LG.data.recaps.recaps;
    if (!recaps.length) {
      out.appendChild(V.noRecap());
      return out;
    }
    var week = LG.state.recapWeek || recaps[0].week;
    var r = recaps.filter(function (x) { return x.week === week; })[0] || recaps[0];

    var card = LG.card(r.headline, {
      eyebrow: "The Tuesday recap · Week " + r.week,
      aside: el("span.chip" + (r.status === "published" ? "" : ".live"),
        { text: r.status === "published" ? "Published" : "Draft, awaiting review" })
    });
    var body = el("div.recap-body", el("p.lede", { text: r.lede }));
    r.paragraphs.forEach(function (p) { body.appendChild(el("p", { text: p })); });
    if (r.purse) body.appendChild(el("p.recap-purse", { text: r.purse }));
    if (r.sign_off) body.appendChild(el("p.recap-signoff", { text: r.sign_off }));
    card.body.appendChild(body);
    card.body.appendChild(el("p.sub", { style: "margin-top:18px",
      text: "Filed by Doc, " + LG.dateTime(r.generated_at) + ". Every number in it comes "
        + "straight off the league ledger." }));
    out.appendChild(card);

    var arch = LG.card("Archive", { flush: true, eyebrow: recaps.length + " weeks" });
    recaps.forEach(function (x) {
      arch.body.appendChild(el("button.archive-item", {
        type: "button",
        "aria-current": x.week === week ? "true" : null,
        onclick: function () { LG.state.recapWeek = x.week; LG.render(); window.scrollTo(0, 0); }
      }, el("b", { text: "Wk " + x.week }), el("span", { text: x.headline })));
    });
    out.appendChild(arch);
    return out;
  };

  /* ============================================================== FORM === */
  V.form = function () {
    var out = document.createDocumentFragment();
    var L = LG.data.leaders;
    var board = LG.state.leaderBoard || "points";

    out.appendChild(el("div.card", el("div.card-head", el("div", null,
      el("div.eyebrow", { text: "The Form" }),
      el("h2", { text: "Leaders, records, and grudges" }),
      el("p.sub", { text: "Season leaderboards under this league's scoring, the best and "
        + "worst single weeks anyone has had, and the sixteen by sixteen grid of who has "
        + "beaten whom." })))));

    var BOARDS = [
      { value: "points", label: "Points", col: "points", dp: 1 },
      { value: "sacks", label: "Sacks", col: "sack", dp: 0 },
      { value: "tackles", label: "Tackles", col: "tackles", dp: 0 },
      { value: "takeaways", label: "Takeaways", col: "takeaways", dp: 0 },
      { value: "touchdowns", label: "TDs", col: "touchdowns", dp: 0 },
      { value: "pass_yd", label: "Pass yds", col: "pass_yd", dp: 0 },
      { value: "rush_yd", label: "Rush yds", col: "rush_yd", dp: 0 },
      { value: "rec_yd", label: "Rec yds", col: "rec_yd", dp: 0 }
    ];
    var cfg = BOARDS.filter(function (b) { return b.value === board; })[0] || BOARDS[0];

    var lb = LG.card("Season leaders", { flush: true, eyebrow: "Through week " + L.week });
    lb.body.appendChild(el("div", { style: "padding:0 16px" },
      LG.toggleRow(BOARDS, board, function (v) { LG.state.leaderBoard = v; LG.render(); })));
    lb.body.appendChild(LG.table([
      { key: "rk", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
      { key: "name", label: "Player", sort: false, cell: function (r) {
          return el("span", null, el("em", { text: r.name }), " ",
            el("span.ppos", { text: r.position + " · " + r.nfl_team })); } },
      { key: "team", label: "Roster", sort: false, cell: function (r) {
          return LG.teamName(r.team_index); } },
      { key: cfg.col, label: cfg.label, cell: function (r) { return LG.fmt(r[cfg.col], cfg.dp); } }
    ].concat(cfg.col === "points" ? [] : [
      // Fantasy points sit beside the counting stat, except on the board that is
      // already showing fantasy points.
      { key: "points", label: "Pts", cell: function (r) { return LG.fmt(r.points, 1); } }
    ]), L.season[board] || L.season.points, { sort: cfg.col }));
    out.appendChild(lb);

    /* --- the new-age boards ------------------------------------------------ */
    var na = LG.data.newage;
    if (na && na.rotation && na.rotation.length) {
      var pick = LG.state.newAgeBoard || na.headline || na.rotation[0];
      var nb = na.boards[pick];
      var nc = LG.card(nb.name, { flush: true, eyebrow: "New-age boards",
        sub: nb.blurb, aside: pick === na.headline ? el("span.chip.live", { text: "This week's headline" }) : null });
      nc.body.appendChild(el("div", { style: "padding:0 16px" },
        LG.toggleRow(na.rotation.map(function (id) { return { value: id, label: na.boards[id].short }; }),
          pick, function (v) { LG.state.newAgeBoard = v; LG.render(); })));
      nc.body.appendChild(nb.leaders.length ? V.newAgeTable(nb, nb.leaders)
        : el("p.empty", { text: "Nobody on a Life\u2019s Gr8 roster qualifies for this one yet." }));
      if (nb.trailers && nb.trailers.length) {
        nc.body.appendChild(el("div.day-head", { text: nb.format === "signed" ? "Due for a bounce" : "The other end" }));
        nc.body.appendChild(V.newAgeTable(nb, nb.trailers));
      }
      nc.body.appendChild(V.newAgeFooter(nb, na));
      out.appendChild(nc);
    }

    /* --- IDP started versus benched -------------------------------------- */
    var two = el("div.grid.two");
    function idpCard(title, eyebrow, list, sub) {
      var c = LG.card(title, { flush: true, eyebrow: eyebrow, sub: sub });
      c.body.appendChild(LG.table([
        { key: "week", label: "Wk", sort: false, cls: "rk", cell: function (r) { return r.week; } },
        { key: "name", label: "Player", sort: false, cell: function (r) {
            return el("span", null, el("em", { text: r.name }), " ",
              el("span.ppos", { text: r.position })); } },
        { key: "team", label: "Roster", sort: false, cell: function (r) {
            return LG.teamName(r.team_index); } },
        { key: "points", label: "Pts", cell: function (r) { return LG.fmt(r.points, 1); } }
      ], list, { sort: "points" }));
      return c;
    }
    two.appendChild(idpCard("Best defensive weeks started", "The Trench Report",
      L.idp_week_started, "The reason this league plays IDP."));
    two.appendChild(idpCard("Best defensive weeks benched", "The other Trench Report",
      L.idp_week_benched, "Same performances. Nobody started them."));
    out.appendChild(two);

    /* --- single week records --------------------------------------------- */
    var sw = LG.card("Biggest single weeks", { flush: true,
      eyebrow: "Any position", sub: "The fifteen loudest individual weeks of the season." });
    sw.body.appendChild(LG.table([
      { key: "rk", label: "#", sort: false, cls: "rk", cell: function (r, i) { return i + 1; } },
      { key: "week", label: "Wk", cell: function (r) { return r.week; } },
      { key: "name", label: "Player", sort: false, cell: function (r) {
          return el("span", null, el("em", { text: r.name }), " ",
            el("span.ppos", { text: r.position })); } },
      { key: "team", label: "Roster", sort: false, cell: function (r) {
          return LG.teamName(r.team_index); } },
      { key: "started", label: "Started", sort: false, cell: function (r) {
          return r.started ? el("span.chip.good", { text: "Yes" })
                           : el("span.chip.bad", { text: "Benched" }); } },
      { key: "points", label: "Pts", cell: function (r) { return LG.fmt(r.points, 1); } }
    ], L.single_week, { sort: "points" }));
    out.appendChild(sw);

    /* --- record book ------------------------------------------------------ */
    var rows = LG.data.standings.rows, weeks = LG.data.season.weeks;
    var allTeamWeeks = LG.data.season.team_weeks;
    var hi = allTeamWeeks.slice().sort(function (a, b) { return b.points - a.points; })[0];
    var lo = allTeamWeeks.slice().sort(function (a, b) { return a.points - b.points; })[0];
    var reg = allTeamWeeks.slice().sort(function (a, b) { return b.regret - a.regret; })[0];
    var allM = [];
    weeks.forEach(function (w) { w.matchups.forEach(function (m) {
      allM.push({ week: w.week, m: m }); }); });
    var big = allM.slice().sort(function (a, b) { return b.m.margin - a.m.margin; })[0];
    var thin = allM.slice().sort(function (a, b) { return a.m.margin - b.m.margin; })[0];

    var rec = LG.card("The record book", { flush: true, eyebrow: LG.data.league.season + " season" });
    var recs = el("div.awards");
    [
      ["Highest week", hi.team_index, LG.fmt(hi.points) + " pts", "Week " + hi.week],
      ["Lowest week", lo.team_index, LG.fmt(lo.points) + " pts", "Week " + lo.week],
      ["Widest margin", big.m.winner, LG.fmt(big.m.margin) + " pts", "Week " + big.week],
      ["Closest game", thin.m.winner, LG.fmt(thin.m.margin) + " pts", "Week " + thin.week],
      ["Most benched", reg.team_index, LG.fmt(reg.regret) + " pts", "Week " + reg.week],
      ["Best lineup rate", rows.slice().sort(function (a, b) {
        return b.efficiency - a.efficiency; })[0].team_index,
        LG.fmt(rows.slice().sort(function (a, b) {
          return b.efficiency - a.efficiency; })[0].efficiency, 1) + "%", "Season"]
    ].forEach(function (r) {
      recs.appendChild(el("div.award",
        el("div.award-label", { text: r[0] }),
        el("div.award-team", LG.silk(r[1]), LG.team(r[1]).code,
          el("span.tmgr", { text: LG.team(r[1]).name })),
        el("div.award-val.num", { text: r[2] }),
        el("div.award-note", { text: r[3] })));
    });
    rec.body.appendChild(recs);
    out.appendChild(rec);

    /* --- head to head ----------------------------------------------------- */
    var h = LG.card("Head to head", { flush: true, eyebrow: "Who has beaten whom",
      sub: "Read across a row: that team's result against each opponent. Colour is the "
         + "margin, the letter is the result, and a blank means they have not met." });
    h.body.appendChild(el("div", { style: "padding:0 16px" }, LG.chart.h2hGrid(LG.data.standings.head_to_head)));
    h.body.appendChild(LG.chart.legend([
      { color: "var(--div-pos)", label: "Won, deeper is a wider margin" },
      { color: "var(--div-neg)", label: "Lost, deeper is a wider margin" }
    ]));
    out.appendChild(h);

    return out;
  };
})(window.LG);
