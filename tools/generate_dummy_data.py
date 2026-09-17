"""
Generate a complete, internally consistent dummy season for Life's Gr8.

Why this exists
---------------
The site is the product, and the site cannot be designed against three rows of
hand-typed numbers. This script produces a full sixteen-team IDP season whose
every derived figure is computed the same way the real `compute/` layer will
compute it: all-play records come from actual weekly scores, luck gap comes from
all-play, playoff odds come from a Monte Carlo over the remaining schedule, and
draft value comes from a fitted expected-points-by-pick curve.

That makes it a fixture and a specification at the same time. When the Yahoo
pipeline lands, `compute/` must emit JSON matching what this writes to
site/static/data/. Nothing on the front end needs to change.

No third-party dependencies. Deterministic: same seed, same season, every run.

Usage:
    python3 tools/generate_dummy_data.py [--weeks 11] [--seed 8]
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "static" / "data"

SEASON = 2026
REGULAR_SEASON_WEEKS = 14
PLAYOFF_TEAMS = 6

# Week 1 of the 2026 NFL season, for stamping transaction and matchup dates.
WEEK1_MONDAY = datetime(2026, 9, 7, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# League identity
# ---------------------------------------------------------------------------

# (team name, manager, 3-letter code). The codes are the accessibility channel:
# sixteen teams cannot be told apart by color alone, so every silk in the UI is
# rendered beside its code, always.
TEAMS = [
    ("Sod Squad",             "Kevin",   "SOD"),
    ("Blitzkrieg Bop",        "Marcus",  "BLZ"),
    ("Tackling Dummies",      "Dana",    "TKL"),
    ("Red Zone Rhapsody",     "Pete",    "RZR"),
    ("The Nickel Package",    "Aaron",   "NIK"),
    ("Hail Mary Hooligans",   "Jules",   "HMH"),
    ("Linebacker Lullaby",    "Sam",     "LBL"),
    ("Pylon Pushers",         "Theo",    "PYL"),
    ("Cover Two Cowboys",     "Reggie",  "CV2"),
    ("Screen Pass Syndicate", "Nina",    "SPS"),
    ("Third and Long Shots",  "Omar",    "TLS"),
    ("Gap Discipline",        "Wes",     "GAP"),
    ("Punt God Disciples",    "Cass",    "PGD"),
    ("Holding Penalty Co.",   "Ivan",    "HLD"),
    ("Sunday Scaries",        "Bryn",    "SCR"),
    ("Two Minute Warning",    "Lou",     "TMW"),
]

# Eight validated categorical hues (dataviz reference palette), each used twice
# and separated by silk pattern. Composite encoding, not sixteen invented hues.
SILK_HUES = [
    ("blue",    "#2a78d6", "#3987e5"),
    ("orange",  "#eb6834", "#d95926"),
    ("aqua",    "#1baf7a", "#199e70"),
    ("yellow",  "#eda100", "#c98500"),
    ("magenta", "#e87ba4", "#d55181"),
    ("green",   "#008300", "#008300"),
    ("violet",  "#4a3aa7", "#9085e9"),
    ("red",     "#e34948", "#e66767"),
]
SILK_PATTERNS = ["solid", "hoops"]

POSITIONS = {
    "QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "K": 1,
    "DL": 2, "LB": 3, "DB": 2,
}
STARTER_COUNT = sum(POSITIONS.values())  # 16
BENCH_COUNT = 6
ROSTER_SIZE = STARTER_COUNT + BENCH_COUNT  # 22

# Position mix drafted, in rough draft-board order of scarcity.
DRAFT_MIX = ["RB"] * 5 + ["WR"] * 6 + ["QB"] * 2 + ["TE"] * 2 + \
            ["LB"] * 4 + ["DL"] * 2 + ["DB"] * 2 + ["K"] * 1

FIRST_NAMES = [
    "Jalen", "Trey", "Deshaun", "Kenneth", "Brock", "Amari", "Tyreek", "Micah",
    "Roquan", "Fred", "Nick", "Budda", "Derwin", "Quay", "Zaire", "Bijan",
    "Puka", "Garrett", "Devon", "Jaxon", "Rome", "Sam", "Bo", "Drake",
    "Cooper", "Malik", "Xavier", "Jordan", "Marvin", "Tank", "Kayvon", "Will",
    "Foyesade", "Bobby", "Zack", "Ernest", "Antoine", "Jessie", "Riq", "Cole",
    "Hollywood", "Rashee", "Tee", "Chase", "Kyren", "Breece", "Jahmyr", "Travis",
]
LAST_NAMES = [
    "Hendricks", "Okonkwo", "Vasquez", "Bellamy", "Rutledge", "Okafor", "Doyle",
    "Marchetti", "Ferro", "Halloran", "Saldana", "Brightwell", "Kemper", "Nwosu",
    "Ainsworth", "Castille", "Drummond", "Pettigrew", "Lassiter", "Whitlock",
    "Ozuna", "Brannigan", "Sowell", "Teague", "Fennimore", "Achebe", "Kowalczyk",
    "Delacroix", "Redfield", "Stancill", "Varela", "Ahmadi", "Boudreaux", "Quill",
    "Mancuso", "Iverson", "Garrity", "Tolliver", "Ashford", "Winterbourne",
    "Petrossian", "Calloway", "Nakamura", "Ruvalcaba", "Eastwood", "Strand",
    "Beaumont", "Kirkland", "Odhiambo", "Salvatore", "Renfro", "Bianchi",
]
NFL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
]

# Per-position weekly scoring shape (mean, sd) under this league's IDP settings.
# Tackle-heavy: 1.5 solo / 1.0 assist / 4.0 sack, which is why LB is a premium
# position here and why three of them start.
POS_SHAPE = {
    "QB": (19.5, 6.8), "RB": (12.4, 7.2), "WR": (11.8, 7.0), "TE": (8.9, 5.6),
    "K":  (8.2, 3.6),  "DL": (9.6, 5.0),  "LB": (14.1, 5.4), "DB": (10.7, 4.8),
}

BYE_WEEKS = {t: 5 + (i % 9) for i, t in enumerate(NFL_TEAMS)}


# ---------------------------------------------------------------------------
# Scoring settings
#
# These are the league's real modifiers. Nothing in this file ever uses a
# generic fantasy point value: stat lines are generated first, then scored with
# the numbers below. That is the same order the real pipeline works in, and it
# is the whole reason the IDP races mean anything.
# ---------------------------------------------------------------------------

SCORING = {
    "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -2.0,
    "rush_yd": 0.10, "rush_td": 6.0,
    "rec": 0.5, "rec_yd": 0.10, "rec_td": 6.0,
    "fum_lost": -2.0,
    "fg_0_39": 3.0, "fg_40_49": 4.0, "fg_50_plus": 5.0, "xp": 1.0, "fg_miss": -1.0,
    "solo": 1.5, "assist": 0.75, "sack": 4.0, "tfl": 1.0,
    "def_int": 5.0, "pass_def": 1.5, "ff": 4.0, "fr": 3.0,
    "def_td": 6.0, "safety": 4.0,
}

STAT_LABELS = {
    "pass_yd": "Pass Yds", "pass_td": "Pass TD", "pass_int": "INT Thrown",
    "rush_yd": "Rush Yds", "rush_td": "Rush TD", "rec": "Rec",
    "rec_yd": "Rec Yds", "rec_td": "Rec TD", "fum_lost": "Fum Lost",
    "solo": "Solo", "assist": "Ast", "sack": "Sack", "tfl": "TFL",
    "def_int": "INT", "pass_def": "PD", "ff": "FF", "fr": "FR",
    "def_td": "Def TD", "safety": "Safety",
    "fg_0_39": "FG 0-39", "fg_40_49": "FG 40-49", "fg_50_plus": "FG 50+",
    "xp": "XP", "fg_miss": "FG Miss",
}


def score_line(line):
    """Score a raw stat line with the league's own modifiers. Never a generic value."""
    return round(sum(SCORING.get(k, 0.0) * v for k, v in line.items()), 2)


# ---------------------------------------------------------------------------
# Small statistical helpers (no numpy, no scipy - this must run anywhere)
# ---------------------------------------------------------------------------

def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:  # normal approximation, plenty good at these rates
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    ell, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= ell:
            return k - 1


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fit_draft_curve(points, bin_size=16):
    """
    Empirical expected-points-by-pick baseline, from the league's own draft.

    Method: bin picks into regions of the board, take each region's MEDIAN season
    production, force the sequence non-increasing, and interpolate linearly
    between region centers. Outside the fitted range the curve is held flat.

    A power law was tried first and rejected. Fitted in log-log space it has to
    go somewhere as the pick number approaches one, and where it goes is a
    nine-hundred-point expectation for the first overall pick. Every early pick
    who merely had a good season then publishes as a historic bust, which is the
    exact failure mode that gets a draft-value board laughed out of a league
    chat. Medians plus interpolation cannot do that: the expectation for pick one
    is the median of what picks one through sixteen actually did.
    """
    raw = sorted(((x, y) for x, y in points if x and x > 0 and y >= 0), key=lambda t: t[0])
    if len(raw) < 4:
        avg = (sum(y for _, y in raw) / len(raw)) if raw else 1.0
        return [(1.0, avg)]

    knots = []
    for i in range(0, len(raw), bin_size):
        chunk = raw[i:i + bin_size]
        if len(chunk) < max(2, bin_size // 3) and knots:
            # Too thin to trust on its own; fold it into the previous region.
            chunk = raw[max(0, i - bin_size):i + len(chunk)]
            knots.pop()
        xs = sorted(x for x, _ in chunk)
        ys = sorted(y for _, y in chunk)
        knots.append((xs[len(xs) // 2], ys[len(ys) // 2]))

    # Pool adjacent violators: later regions of the board must not be expected to
    # out-produce earlier ones, however a single noisy round happened to land.
    i = 1
    while i < len(knots):
        if knots[i][1] > knots[i - 1][1]:
            x0, y0 = knots[i - 1]
            x1, y1 = knots[i]
            knots[i - 1:i + 1] = [((x0 + x1) / 2.0, (y0 + y1) / 2.0)]
            i = max(1, i - 1)
        else:
            i += 1
    return knots


def curve_value(knots, pick):
    """Expected season points at an overall pick number, held flat past both ends."""
    x = float(max(1, pick))
    if x <= knots[0][0]:
        return knots[0][1]
    if x >= knots[-1][0]:
        return knots[-1][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x0 <= x <= x1:
            span = (x1 - x0) or 1.0
            return y0 + (y1 - y0) * (x - x0) / span
    return knots[-1][1]



# ---------------------------------------------------------------------------
# Player pool
# ---------------------------------------------------------------------------

# How many of each position exist in the drafted-plus-waiver universe.
POOL_SIZE = {"QB": 34, "RB": 66, "WR": 84, "TE": 34, "K": 20,
             "DL": 52, "LB": 62, "DB": 56}


def build_player_pool(rng):
    """
    Create the universe of players, each with a latent talent factor.

    Talent decays with rank inside its position, which is what makes a fitted
    draft curve meaningful later: the draft roughly follows talent, so points by
    pick number really does slope downward, and a player who beats the slope
    really is a steal.
    """
    used_names = set()
    players = []
    pid = 0
    for pos, count in POOL_SIZE.items():
        for rank in range(count):
            while True:
                name = "%s %s" % (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
                if name not in used_names:
                    used_names.add(name)
                    break
            # Talent factor: ~1.55 at the top of a position, ~0.55 at the bottom,
            # with real noise so the board is not a straight line.
            decay = 1.55 * math.exp(-2.05 * (rank / max(1, count - 1)))
            talent = clamp(decay * rng.lognormvariate(0.0, 0.17), 0.30, 1.85)
            nfl = rng.choice(NFL_TEAMS)
            pid += 1
            players.append({
                "player_key": "461.p.%d" % (1000 + pid),
                "name": name,
                "position": pos,
                "nfl_team": nfl,
                "bye": BYE_WEEKS[nfl],
                "talent": talent,
                "pos_rank": rank,
                # Durability: a handful of players will miss real time.
                "fragility": rng.random(),
            })
    # Sort the board the way a consensus ranking would: by expected weekly output.
    for p in players:
        p["adp_score"] = p["talent"] * POS_SHAPE[p["position"]][0]
    players.sort(key=lambda p: -p["adp_score"])
    return players


def run_draft(rng, players):
    """
    Sixteen rounds, snake order, sixteen teams. Teams take roughly the best
    player available with positional need and a human amount of noise.
    """
    board = list(players)
    need = [dict(QB=2, RB=5, WR=6, TE=2, K=1, DL=2, LB=4, DB=2) for _ in TEAMS]
    picks = []
    pick_no = 0
    for rnd in range(1, 17):
        order = range(len(TEAMS)) if rnd % 2 == 1 else reversed(range(len(TEAMS)))
        for ti in order:
            pick_no += 1
            # Reach window: managers do not pick strictly off the top of the board.
            window = board[: min(len(board), 9)]
            wanted = [p for p in window if need[ti].get(p["position"], 0) > 0]
            candidates = wanted or window
            weights = [1.0 / (1 + i * 0.55) for i in range(len(candidates))]
            choice = rng.choices(candidates, weights=weights, k=1)[0]
            board.remove(choice)
            need[ti][choice["position"]] = max(0, need[ti].get(choice["position"], 0) - 1)
            picks.append({
                "pick_number": pick_no,
                "round": rnd,
                "team_index": ti,
                "player_key": choice["player_key"],
            })
            choice["drafted_by"] = ti
            choice["pick_number"] = pick_no
            choice["round"] = rnd
    return picks, board  # board is now the undrafted / waiver pool


MIN_NEED = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DL": 2, "LB": 3, "DB": 2}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}


def assemble_rosters(rng, players, picks, waiver_pool):
    rosters = [[] for _ in TEAMS]
    for pk in picks:
        player = next(p for p in players if p["player_key"] == pk["player_key"])
        rosters[pk["team_index"]].append(player)

    pool = list(waiver_pool)
    # Pass one: satisfy the minimum legal lineup at every position.
    for ti, roster in enumerate(rosters):
        for pos, need in MIN_NEED.items():
            have = sum(1 for p in roster if p["position"] == pos)
            while have < need:
                cand = next((p for p in pool if p["position"] == pos), None)
                if cand is None:
                    break
                pool.remove(cand)
                cand["drafted_by"] = None
                cand["pick_number"] = None
                cand["acquired"] = "waiver"
                roster.append(cand)
                have += 1
    # Pass two: best available until every roster is full.
    ti = 0
    while any(len(r) < ROSTER_SIZE for r in rosters) and pool:
        roster = rosters[ti % len(TEAMS)]
        if len(roster) < ROSTER_SIZE:
            cand = pool.pop(0)
            cand["drafted_by"] = None
            cand["pick_number"] = None
            cand["acquired"] = "waiver"
            roster.append(cand)
        ti += 1
    return rosters


# ---------------------------------------------------------------------------
# Weekly stat lines
# ---------------------------------------------------------------------------

def stat_line(rng, player, form):
    """
    Generate one player-week of raw counting stats.

    `form` is a per-week multiplier: real players run hot and cold, and a site
    whose every player posts their average every week is a site nobody checks.
    """
    pos = player["position"]
    # Talent and weekly form multiply, but not without a ceiling. Uncapped, a
    # high-talent linebacker on a hot week draws twenty-plus solo tackles and
    # posts a score no defender has ever posted.
    t = clamp(player["talent"] * form, 0.05, 2.30)
    line = {}

    if pos == "QB":
        att = max(12, int(rng.gauss(33, 6)))
        ypa = clamp(rng.gauss(7.1, 1.5) * (0.85 + 0.25 * t), 3.0, 12.5)
        line["pass_yd"] = int(att * ypa)
        line["pass_td"] = poisson(rng, 1.45 * t)
        line["pass_int"] = poisson(rng, 0.72 / max(0.5, t) ** 0.5)
        if rng.random() < 0.55:
            line["rush_yd"] = max(0, int(rng.gauss(18 * t, 16)))
            if rng.random() < 0.13 * t:
                line["rush_td"] = 1
    elif pos == "RB":
        carries = max(2, int(rng.gauss(13 * t, 5)))
        line["rush_yd"] = max(-4, int(carries * clamp(rng.gauss(4.3, 1.3), 1.2, 9.0)))
        line["rush_td"] = poisson(rng, 0.44 * t)
        rec = poisson(rng, 2.4 * t)
        if rec:
            line["rec"] = rec
            line["rec_yd"] = max(0, int(rec * clamp(rng.gauss(7.6, 2.6), 1.0, 22.0)))
            if rng.random() < 0.09 * t:
                line["rec_td"] = 1
        if rng.random() < 0.035:
            line["fum_lost"] = 1
    elif pos in ("WR", "TE"):
        base = 5.6 if pos == "WR" else 4.4
        rec = poisson(rng, base * t)
        line["rec"] = rec
        ypr = clamp(rng.gauss(12.4 if pos == "WR" else 10.8, 3.4), 2.0, 28.0)
        line["rec_yd"] = max(0, int(rec * ypr))
        line["rec_td"] = poisson(rng, (0.42 if pos == "WR" else 0.34) * t)
        if rng.random() < 0.06 * t:
            line["rush_yd"] = max(0, int(rng.gauss(9, 7)))
    elif pos == "K":
        atts = poisson(rng, 2.3 * (0.7 + 0.5 * t))
        for _ in range(atts):
            r = rng.random()
            made = rng.random() < (0.88 if r < 0.5 else 0.74 if r < 0.82 else 0.61)
            bucket = "fg_0_39" if r < 0.5 else "fg_40_49" if r < 0.82 else "fg_50_plus"
            key = bucket if made else "fg_miss"
            line[key] = line.get(key, 0) + 1
        line["xp"] = poisson(rng, 2.4)
    elif pos == "DL":
        line["solo"] = poisson(rng, 2.3 * t)
        line["assist"] = poisson(rng, 1.5 * t)
        line["sack"] = poisson(rng, 0.55 * t)
        line["tfl"] = poisson(rng, 0.7 * t)
        if rng.random() < 0.07 * t:
            line["ff"] = 1
        if rng.random() < 0.05:
            line["fr"] = 1
        if rng.random() < 0.10 * t:
            line["pass_def"] = 1
    elif pos == "LB":
        line["solo"] = poisson(rng, 5.3 * t)
        line["assist"] = poisson(rng, 2.6 * t)
        line["sack"] = poisson(rng, 0.30 * t)
        line["tfl"] = poisson(rng, 0.85 * t)
        if rng.random() < 0.08 * t:
            line["ff"] = 1
        if rng.random() < 0.05:
            line["fr"] = 1
        line["pass_def"] = poisson(rng, 0.45 * t)
        if rng.random() < 0.035 * t:
            line["def_int"] = 1
    elif pos == "DB":
        line["solo"] = poisson(rng, 4.1 * t)
        line["assist"] = poisson(rng, 1.5 * t)
        line["sack"] = poisson(rng, 0.09 * t)
        line["tfl"] = poisson(rng, 0.32 * t)
        line["pass_def"] = poisson(rng, 0.85 * t)
        if rng.random() < 0.10 * t:
            line["def_int"] = 1
        if rng.random() < 0.05 * t:
            line["ff"] = 1

    if line.get("def_int") and rng.random() < 0.12:
        line["def_td"] = 1
    return {k: v for k, v in line.items() if v}


# ---------------------------------------------------------------------------
# Lineups
# ---------------------------------------------------------------------------

SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K",
         "DL", "DL", "LB", "LB", "LB", "DB", "DB"]


def choose_lineup(roster, value_of):
    """
    Fill the sixteen starting slots greedily by `value_of`, hardest slot first.

    Called twice per team per week: once with the manager's projection (what they
    actually started) and once with the week's real points (what they should have
    started). The gap between the two is bench regret, and bench regret is the
    most-read number on any league site ever built.
    """
    remaining = sorted(roster, key=lambda p: -value_of(p))
    chosen, used = [], set()
    # Strict positions before FLEX, or FLEX steals a player a real slot needs.
    for slot in [s for s in SLOTS if s != "FLEX"]:
        pick = next((p for p in remaining
                     if p["player_key"] not in used and p["position"] == slot), None)
        if pick:
            used.add(pick["player_key"])
            chosen.append((slot, pick))
    for _ in range(SLOTS.count("FLEX")):
        pick = next((p for p in remaining
                     if p["player_key"] not in used and p["position"] in FLEX_ELIGIBLE), None)
        if pick:
            used.add(pick["player_key"])
            chosen.append(("FLEX", pick))
    return chosen, used


def build_schedule(n_teams, n_weeks):
    """Circle-method round robin. Sixteen teams gives fifteen unique rounds."""
    ids = list(range(n_teams))
    rounds = []
    for _ in range(n_teams - 1):
        half = n_teams // 2
        pairs = [(ids[i], ids[n_teams - 1 - i]) for i in range(half)]
        rounds.append(pairs)
        ids = [ids[0]] + [ids[-1]] + ids[1:-1]
    return [rounds[w % len(rounds)] for w in range(n_weeks)]


# ---------------------------------------------------------------------------
# Season simulation
# ---------------------------------------------------------------------------

def simulate_season(rng, rosters, weeks_played):
    """
    Run every week for every player, set lineups, and score matchups.

    Returns per-week team results plus a season-long per-player ledger.
    """
    # Manager skill drives how closely the lineup they set tracks the lineup they
    # should have set. It is the only per-manager knob in the whole simulation.
    skill = [clamp(rng.gauss(0.62, 0.17), 0.22, 0.94) for _ in TEAMS]

    # Injuries: a few players lose a stretch of the season outright.
    injuries = {}
    for roster in rosters:
        for p in roster:
            if p["fragility"] > 0.90:
                start = rng.randint(2, max(2, REGULAR_SEASON_WEEKS - 3))
                injuries[p["player_key"]] = (start, start + rng.randint(2, 7))

    player_log = {}   # player_key -> {week: {"line":..., "points":..., "started":bool}}
    week_rows = []    # per week, per team

    for week in range(1, weeks_played + 1):
        weekly_points, weekly_lines = {}, {}
        for roster in rosters:
            for p in roster:
                key = p["player_key"]
                out_injured = key in injuries and injuries[key][0] <= week <= injuries[key][1]
                if p["bye"] == week or out_injured:
                    weekly_points[key] = 0.0
                    weekly_lines[key] = {}
                    player_log.setdefault(key, {})[week] = {
                        "points": 0.0, "line": {},
                        "status": "BYE" if p["bye"] == week else "OUT",
                    }
                    continue
                form = clamp(rng.lognormvariate(0.0, 0.42), 0.15, 2.9)
                line = stat_line(rng, p, form)
                pts = score_line(line)
                weekly_points[key] = pts
                weekly_lines[key] = line
                player_log.setdefault(key, {})[week] = {
                    "points": pts, "line": line, "status": "OK",
                }

        for ti, roster in enumerate(rosters):
            # What the manager projected, and therefore what they started.
            noise_sd = 6.5 * (1.0 - skill[ti]) + 1.2

            def projected(p, _sd=noise_sd):
                base = p["talent"] * POS_SHAPE[p["position"]][0]
                key = p["player_key"]
                unavailable = (p["bye"] == week or
                               (key in injuries and injuries[key][0] <= week <= injuries[key][1]))
                if unavailable:
                    # Most managers notice a bye. Not all of them, not every week.
                    return -1.0 if rng.random() < 0.93 else base * 0.35
                return base + rng.gauss(0, _sd)

            started, started_keys = choose_lineup(roster, projected)
            optimal, _ = choose_lineup(roster, lambda p: weekly_points[p["player_key"]])

            actual = round(sum(weekly_points[p["player_key"]] for _, p in started), 2)
            optimal_pts = round(sum(weekly_points[p["player_key"]] for _, p in optimal), 2)
            bench = [p for p in roster if p["player_key"] not in started_keys]

            for _, p in started:
                player_log[p["player_key"]][week]["started"] = True
            for p in bench:
                player_log[p["player_key"]][week].setdefault("started", False)

            best_starter = max(started, key=lambda sp: weekly_points[sp[1]["player_key"]])
            worst_starter = min(started, key=lambda sp: weekly_points[sp[1]["player_key"]])
            best_bench = max(bench, key=lambda p: weekly_points[p["player_key"]]) if bench else None
            idp_started = [sp for sp in started if sp[1]["position"] in ("DL", "LB", "DB")]
            best_idp = max(idp_started, key=lambda sp: weekly_points[sp[1]["player_key"]])

            week_rows.append({
                "week": week, "team_index": ti,
                "points": actual, "optimal": optimal_pts,
                "regret": round(optimal_pts - actual, 2),
                "starters": [{"slot": s, "player_key": p["player_key"],
                              "points": weekly_points[p["player_key"]]} for s, p in started],
                "bench": [{"player_key": p["player_key"],
                           "points": weekly_points[p["player_key"]]} for p in bench],
                "best_starter": {"player_key": best_starter[1]["player_key"],
                                 "points": weekly_points[best_starter[1]["player_key"]]},
                "worst_starter": {"player_key": worst_starter[1]["player_key"],
                                  "points": weekly_points[worst_starter[1]["player_key"]]},
                "best_bench": ({"player_key": best_bench["player_key"],
                                "points": weekly_points[best_bench["player_key"]]}
                               if best_bench else None),
                "best_idp": {"player_key": best_idp[1]["player_key"],
                             "points": weekly_points[best_idp[1]["player_key"]]},
            })

    return week_rows, player_log, skill, injuries


# ---------------------------------------------------------------------------
# Derived league tables
# ---------------------------------------------------------------------------

def compute_standings(week_rows, schedule, weeks_played):
    """
    Actual record, all-play record, and the luck gap between them.

    All-play is the honest version of a fantasy record: every week, a team is
    scored against all fifteen opponents it did not play. Expected wins is the
    all-play win rate applied to games played, and luck gap is the difference.
    A team at +2.0 has two more wins than its scoring has earned.
    """
    n = len(TEAMS)
    rows = [{"team_index": i, "wins": 0, "losses": 0, "ties": 0,
             "pf": 0.0, "pa": 0.0, "ap_w": 0, "ap_l": 0,
             "weekly": [], "optimal_total": 0.0, "regret_total": 0.0,
             "results": []} for i in range(n)]

    by_week = {}
    for r in week_rows:
        by_week.setdefault(r["week"], {})[r["team_index"]] = r

    for week in range(1, weeks_played + 1):
        wk = by_week[week]
        scores = sorted((wk[i]["points"], i) for i in range(n))
        for rank, (_pts, ti) in enumerate(scores):
            rows[ti]["ap_w"] += rank
            rows[ti]["ap_l"] += (n - 1 - rank)
        for a, b in schedule[week - 1]:
            pa, pb = wk[a]["points"], wk[b]["points"]
            rows[a]["pf"] += pa; rows[a]["pa"] += pb
            rows[b]["pf"] += pb; rows[b]["pa"] += pa
            if pa > pb:
                rows[a]["wins"] += 1; rows[b]["losses"] += 1
                rows[a]["results"].append("W"); rows[b]["results"].append("L")
            elif pb > pa:
                rows[b]["wins"] += 1; rows[a]["losses"] += 1
                rows[b]["results"].append("W"); rows[a]["results"].append("L")
            else:
                rows[a]["ties"] += 1; rows[b]["ties"] += 1
                rows[a]["results"].append("T"); rows[b]["results"].append("T")
            rows[a]["weekly"].append({"week": week, "points": pa, "opponent": b, "opp_points": pb})
            rows[b]["weekly"].append({"week": week, "points": pb, "opponent": a, "opp_points": pa})
        for ti in range(n):
            rows[ti]["optimal_total"] += wk[ti]["optimal"]
            rows[ti]["regret_total"] += wk[ti]["regret"]

    for r in rows:
        games = r["wins"] + r["losses"] + r["ties"]
        ap_games = r["ap_w"] + r["ap_l"]
        r["ap_pct"] = round(r["ap_w"] / ap_games, 4) if ap_games else 0.0
        r["expected_wins"] = round(r["ap_pct"] * games, 2)
        r["luck"] = round(r["wins"] + 0.5 * r["ties"] - r["expected_wins"], 2)
        r["pf"] = round(r["pf"], 2)
        r["pa"] = round(r["pa"], 2)
        r["ppg"] = round(r["pf"] / games, 2) if games else 0.0
        r["optimal_total"] = round(r["optimal_total"], 2)
        r["regret_total"] = round(r["regret_total"], 2)
        r["efficiency"] = round(100 * r["pf"] / r["optimal_total"], 1) if r["optimal_total"] else 0.0
        r["streak"] = current_streak(r["results"])
        r["high"] = round(max(w["points"] for w in r["weekly"]), 2)
        r["low"] = round(min(w["points"] for w in r["weekly"]), 2)
        last3 = [w["points"] for w in r["weekly"]][-3:]
        r["last3_ppg"] = round(sum(last3) / len(last3), 2)
    return rows


def current_streak(results):
    if not results:
        return "-"
    last, count = results[-1], 0
    for r in reversed(results):
        if r != last:
            break
        count += 1
    return "%s%d" % (last, count)


def compute_power(rows, weeks_played):
    """
    Power ranking: half all-play, a third scoring, the rest recent form.

    Deliberately not a record-based ranking. Record is already on the standings
    page; a power ranking that just reprints it is not worth the pixels.
    """
    def z(vals):
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1.0
        return [(v - m) / sd for v in vals]

    zap = z([r["ap_pct"] for r in rows])
    zpf = z([r["ppg"] for r in rows])
    zl3 = z([r["last3_ppg"] for r in rows])
    scored = [(0.50 * zap[i] + 0.30 * zpf[i] + 0.20 * zl3[i], i) for i in range(len(rows))]
    scored.sort(key=lambda t: -t[0])
    return [{"team_index": ti, "rank": n + 1, "score": round(s, 3)}
            for n, (s, ti) in enumerate(scored)]


def playoff_odds(rng, rows, schedule, weeks_played, sims=4000):
    """
    Monte Carlo over the remaining schedule.

    Each team's future weeks are drawn from its own observed scoring distribution,
    so a boom-or-bust team keeps its variance instead of being flattened to a mean.
    """
    n = len(TEAMS)
    means, sds = [], []
    for r in rows:
        pts = [w["points"] for w in r["weekly"]]
        m = sum(pts) / len(pts)
        sd = math.sqrt(sum((p - m) ** 2 for p in pts) / len(pts)) or 12.0
        means.append(m); sds.append(max(8.0, sd))

    remaining = schedule[weeks_played:REGULAR_SEASON_WEEKS]
    made = [0] * n
    byes = [0] * n
    for _ in range(sims):
        wins = [r["wins"] + 0.5 * r["ties"] for r in rows]
        pf = [r["pf"] for r in rows]
        for pairs in remaining:
            for a, b in pairs:
                sa = max(30.0, rng.gauss(means[a], sds[a]))
                sb = max(30.0, rng.gauss(means[b], sds[b]))
                pf[a] += sa; pf[b] += sb
                if sa > sb:
                    wins[a] += 1
                else:
                    wins[b] += 1
        order = sorted(range(n), key=lambda i: (-wins[i], -pf[i]))
        for seed, ti in enumerate(order):
            if seed < PLAYOFF_TEAMS:
                made[ti] += 1
            if seed < 2:
                byes[ti] += 1
    return [{"team_index": i,
             "playoff_pct": round(100.0 * made[i] / sims, 1),
             "bye_pct": round(100.0 * byes[i] / sims, 1)} for i in range(n)]


# ---------------------------------------------------------------------------
# The Paddock
# ---------------------------------------------------------------------------

def compute_race_totals(week_rows, player_log, weeks_played):
    """
    Accumulate every race category from STARTED PLAYERS ONLY.

    This is a league rules decision, not an implementation detail. A team does not
    get credit for a linebacker who put up fourteen tackles on its bench.
    """
    n = len(TEAMS)
    totals = [{"points_for": 0.0, "sacks": 0.0, "solo_tackles": 0, "takeaways": 0,
               "pass_yards": 0, "rush_yards": 0, "touchdowns": 0,
               "bench_regret": 0.0} for _ in range(n)]
    by_week = [[dict(t) for t in totals] for _ in range(weeks_played + 1)]

    for row in week_rows:
        ti, wk = row["team_index"], row["week"]
        t = totals[ti]
        t["points_for"] += row["points"]
        t["bench_regret"] += row["regret"]
        for s in row["starters"]:
            line = player_log[s["player_key"]][wk]["line"]
            t["sacks"] += line.get("sack", 0)
            t["solo_tackles"] += line.get("solo", 0)
            t["takeaways"] += line.get("def_int", 0) + line.get("ff", 0) + line.get("fr", 0)
            t["pass_yards"] += line.get("pass_yd", 0)
            t["rush_yards"] += line.get("rush_yd", 0)
            t["touchdowns"] += (line.get("pass_td", 0) + line.get("rush_td", 0) +
                                line.get("rec_td", 0) + line.get("def_td", 0))
        by_week[wk][ti] = {k: (round(v, 2) if isinstance(v, float) else v)
                           for k, v in totals[ti].items()}

    for t in totals:
        t["points_for"] = round(t["points_for"], 2)
        t["bench_regret"] = round(t["bench_regret"], 2)
    return totals, by_week


def race_positions(values, direction, leader_scale=0.90, floor=0.08):
    """
    Leader-relative track position, per CLAUDE.md.

    Min-max normalization is deliberately not the default: it stretches a field
    that finished within four points of each other across the whole track and
    makes a tight race look like a rout.
    """
    if direction == "low":
        # Fewer is better. Invert around the worst so the best horse still leads.
        worst = max(values) if values else 1.0
        basis = [worst - v + (0.02 * worst) for v in values]
    else:
        basis = list(values)
    leader = max(basis) or 1.0
    return [round(max(floor, leader_scale * (b / leader)), 4) for b in basis]


# ---------------------------------------------------------------------------
# Draft value
# ---------------------------------------------------------------------------

def compute_draft_value(players_by_key, rosters, player_log, weeks_played):
    """
    Value over slot, against an empirically fitted baseline.

    The curve is fitted from this league's own draft and this league's own
    scoring, which is the only version of this metric that survives an argument.
    Undrafted players are priced one slot past the end of the draft, which
    correctly makes a productive waiver add look enormous.
    """
    last_pick = 16 * len(TEAMS)
    ledger = []
    for ti, roster in enumerate(rosters):
        for p in roster:
            log = player_log.get(p["player_key"], {})
            pts = round(sum(w["points"] for w in log.values()), 2)
            played = sum(1 for w in log.values() if w["status"] == "OK")
            ledger.append({
                "player_key": p["player_key"], "name": p["name"],
                "position": p["position"], "nfl_team": p["nfl_team"],
                "team_index": ti,
                "pick_number": p.get("pick_number"),
                "round": p.get("round"),
                "points": pts, "games": played,
                "missed": weeks_played - played,
            })

    drafted = [e for e in ledger if e["pick_number"]]
    global_fit = fit_draft_curve([(e["pick_number"], e["points"]) for e in drafted],
                                 bin_size=len(TEAMS))

    pos_fits = {}
    for pos in POOL_SIZE:
        sample = [(e["pick_number"], e["points"]) for e in drafted if e["position"] == pos]
        # Fall back to the global curve where a position has too few picks to fit.
        pos_fits[pos] = fit_draft_curve(sample, bin_size=8) if len(sample) >= 24 else global_fit

    for e in ledger:
        fit = pos_fits.get(e["position"], global_fit)
        slot = e["pick_number"] or (last_pick + 1)
        expected = max(0.5, curve_value(fit, slot))
        if e["pick_number"] is None:
            # One slot past the end of the draft, which is what makes a productive
            # waiver add read as the enormous win it actually was.
            expected *= 0.95
        e["expected"] = round(expected, 2)
        e["value_over_slot"] = round(e["points"] - expected, 2)
        e["undrafted"] = e["pick_number"] is None
    return ledger, global_fit, pos_fits


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

AGG_KEYS = ["sack", "solo", "assist", "tfl", "pass_def", "def_int", "ff", "fr",
            "pass_yd", "rush_yd", "rec_yd", "rec"]


def compute_leaders(rosters, player_log, week_rows, weeks_played):
    """
    Season leaderboards, single-week highs, and the two numbers every IDP league
    argues about: the best defensive week somebody started, and the best
    defensive week somebody left sitting on a bench.
    """
    started = set()
    for r in week_rows:
        for s_ in r["starters"]:
            started.add((s_["player_key"], r["week"]))

    rows, best_weeks = [], []
    for ti, roster in enumerate(rosters):
        for p in roster:
            log = player_log.get(p["player_key"], {})
            agg = {k: 0 for k in AGG_KEYS}
            tds = pts = 0.0
            for wk, entry in log.items():
                for k in AGG_KEYS:
                    agg[k] += entry["line"].get(k, 0)
                tds += (entry["line"].get("pass_td", 0) + entry["line"].get("rush_td", 0) +
                        entry["line"].get("rec_td", 0) + entry["line"].get("def_td", 0))
                pts += entry["points"]
                if entry["points"] > 0:
                    best_weeks.append({
                        "player_key": p["player_key"], "name": p["name"],
                        "position": p["position"], "team_index": ti, "week": wk,
                        "points": entry["points"], "line": entry["line"],
                        "started": (p["player_key"], wk) in started,
                    })
            rows.append({
                "player_key": p["player_key"], "name": p["name"],
                "position": p["position"], "nfl_team": p["nfl_team"],
                "team_index": ti, "points": round(pts, 2), "touchdowns": int(tds),
                "tackles": agg["solo"] + agg["assist"],
                "takeaways": agg["def_int"] + agg["ff"] + agg["fr"],
                **{k: round(v, 2) if isinstance(v, float) else v for k, v in agg.items()},
            })

    def top(key, n=12, positions=None):
        pool = [r for r in rows if positions is None or r["position"] in positions]
        return sorted(pool, key=lambda r: -r[key])[:n]

    idp_weeks = [b for b in best_weeks if b["position"] in ("DL", "LB", "DB")]
    idp_weeks.sort(key=lambda b: -b["points"])

    return {
        "week": weeks_played,
        "season": {
            "points": top("points"),
            "sacks": top("sack", positions=("DL", "LB", "DB")),
            "tackles": top("tackles", positions=("DL", "LB", "DB")),
            "takeaways": top("takeaways", positions=("DL", "LB", "DB")),
            "touchdowns": top("touchdowns", positions=("QB", "RB", "WR", "TE")),
            "rush_yd": top("rush_yd", positions=("RB", "QB", "WR")),
            "rec_yd": top("rec_yd", positions=("WR", "TE", "RB")),
            "pass_yd": top("pass_yd", positions=("QB",)),
        },
        "single_week": sorted(best_weeks, key=lambda b: -b["points"])[:15],
        "idp_week_started": [b for b in idp_weeks if b["started"]][:10],
        "idp_week_benched": [b for b in idp_weeks if not b["started"]][:10],
        # Deliberately no full-pool dump here. The boards above are already
        # sliced, and this file loads on every page view on a phone.
    }



# ---------------------------------------------------------------------------
# The Blotter
# ---------------------------------------------------------------------------

TXN_NOTES = [
    "Waiver claim processed", "Free agent pickup", "Dropped to waivers",
    "Claim awarded on priority", "Blocked, lost priority",
]


def generate_transactions(rng, rosters, players_by_key, weeks_played):
    """A season's worth of adds, drops, and trades, plus live pending activity."""
    txns = []
    pool = [p for p in players_by_key.values()]
    tid = 0
    for week in range(1, weeks_played + 1):
        base = WEEK1_MONDAY + timedelta(days=7 * (week - 1))
        count = rng.randint(6, 16)
        for _ in range(count):
            tid += 1
            ti = rng.randrange(len(TEAMS))
            when = base + timedelta(days=rng.randint(1, 6), hours=rng.randint(6, 23),
                                    minutes=rng.randrange(60))
            kind = rng.choices(["add_drop", "add", "drop", "trade"],
                               weights=[52, 22, 16, 10], k=1)[0]
            if kind == "trade":
                tj = rng.choice([x for x in range(len(TEAMS)) if x != ti])
                give = [rng.choice(rosters[ti]) for _ in range(rng.randint(1, 2))]
                get = [rng.choice(rosters[tj]) for _ in range(rng.randint(1, 2))]
                txns.append({
                    "id": "t.%d" % tid, "type": "trade", "status": "successful",
                    "week": week, "timestamp": when.isoformat(),
                    "teams": [ti, tj],
                    "sends": [{"team_index": ti, "player_key": p["player_key"]} for p in give] +
                             [{"team_index": tj, "player_key": p["player_key"]} for p in get],
                })
            else:
                added = rng.choice(pool) if kind in ("add_drop", "add") else None
                dropped = rng.choice(rosters[ti]) if kind in ("add_drop", "drop") else None
                txns.append({
                    "id": "t.%d" % tid, "type": "add/drop" if kind == "add_drop" else kind,
                    "status": "successful", "week": week, "timestamp": when.isoformat(),
                    "teams": [ti],
                    "added": added["player_key"] if added else None,
                    "dropped": dropped["player_key"] if dropped else None,
                    "source": rng.choice(["waivers", "freeagents"]),
                    "faab": rng.choice([0, 0, 1, 3, 5, 8, 12, 17, 24, 39]) if added else None,
                    "note": rng.choice(TXN_NOTES),
                })

    # Pending activity. Yahoo hides this from a plain transactions fetch, which is
    # exactly why the Blotter showing it is worth something.
    pending_at = WEEK1_MONDAY + timedelta(days=7 * weeks_played + 1, hours=9)
    for i in range(4):
        tid += 1
        ti = rng.randrange(len(TEAMS))
        txns.append({
            "id": "t.%d" % tid, "type": "waiver", "status": "pending",
            "week": weeks_played + 1, "timestamp": (pending_at + timedelta(hours=i)).isoformat(),
            "teams": [ti],
            "added": rng.choice(pool)["player_key"],
            "dropped": rng.choice(rosters[ti])["player_key"],
            "source": "waivers", "faab": rng.choice([2, 7, 15, 31]),
            "note": "Clears Wednesday 3:00am ET",
        })
    for i in range(2):
        tid += 1
        ti = rng.randrange(len(TEAMS))
        tj = rng.choice([x for x in range(len(TEAMS)) if x != ti])
        txns.append({
            "id": "t.%d" % tid, "type": "pending_trade", "status": "pending",
            "week": weeks_played + 1,
            "timestamp": (pending_at + timedelta(hours=4 + i)).isoformat(),
            "teams": [ti, tj],
            "sends": [{"team_index": ti, "player_key": rng.choice(rosters[ti])["player_key"]},
                      {"team_index": tj, "player_key": rng.choice(rosters[tj])["player_key"]}],
            "note": "Awaiting league review",
        })

    txns.sort(key=lambda t: t["timestamp"], reverse=True)
    return txns


# ---------------------------------------------------------------------------
# races.toml
#
# Minimal reader for the shape races.toml actually has: scalar keys, [table],
# and [[array of tables]]. Python 3.11+ has tomllib; this keeps the generator
# runnable on the interpreter that happens to be installed.
# ---------------------------------------------------------------------------

def read_races_toml(path):
    doc, current = {}, None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        if line.startswith("[["):
            name = line[2:-2].strip()
            doc.setdefault(name, [])
            current = {}
            doc[name].append(current)
        elif line.startswith("["):
            name = line[1:-1].strip()
            current = doc.setdefault(name, {})
        elif "=" in line and current is not None:
            k, v = (x.strip() for x in line.split("=", 1))
            if v.startswith('"'):
                current[k] = v.strip('"')
            elif v in ("true", "false"):
                current[k] = v == "true"
            else:
                current[k] = float(v) if "." in v else int(v)
    return doc


# ---------------------------------------------------------------------------
# Weekly awards and matchups
# ---------------------------------------------------------------------------

def build_weeks(week_rows, schedule, weeks_played, player_log, players_by_key):
    by_week = {}
    for r in week_rows:
        by_week.setdefault(r["week"], {})[r["team_index"]] = r

    out = []
    for week in range(1, weeks_played + 1):
        wk = by_week[week]
        matchups = []
        for a, b in schedule[week - 1]:
            pa, pb = wk[a]["points"], wk[b]["points"]
            matchups.append({
                "team_a": a, "team_b": b, "points_a": pa, "points_b": pb,
                "margin": round(abs(pa - pb), 2),
                "winner": a if pa > pb else (b if pb > pa else None),
            })
        scores = sorted(((wk[i]["points"], i) for i in range(len(TEAMS))), reverse=True)
        blowout = max(matchups, key=lambda m: m["margin"])
        nailbiter = min(matchups, key=lambda m: m["margin"])
        regret = max(range(len(TEAMS)), key=lambda i: wk[i]["regret"])
        idp = max(range(len(TEAMS)), key=lambda i: wk[i]["best_idp"]["points"])
        idp_key = wk[idp]["best_idp"]["player_key"]

        out.append({
            "week": week,
            "date": (WEEK1_MONDAY + timedelta(days=7 * (week - 1))).date().isoformat(),
            "matchups": matchups,
            "awards": {
                "gold_cup": {"team_index": scores[0][1], "points": scores[0][0],
                             "label": "The Gold Cup", "note": "Week high"},
                "sacko": {"team_index": scores[-1][1], "points": scores[-1][0],
                          "label": "The Sacko", "note": "Week low"},
                "blowout": {"label": "The Woodshed", "note": "Widest margin",
                            "team_index": blowout["winner"],
                            "loser_index": blowout["team_b"] if blowout["winner"] == blowout["team_a"] else blowout["team_a"],
                            "margin": blowout["margin"]},
                "nailbiter": {"label": "The Photo Finish", "note": "Closest game",
                              "team_index": nailbiter["winner"], "margin": nailbiter["margin"]},
                "regret": {"label": "The Regret Award", "note": "Most points benched",
                           "team_index": regret, "points": wk[regret]["regret"],
                           "player_key": wk[regret]["best_bench"]["player_key"] if wk[regret]["best_bench"] else None},
                "trench": {"label": "The Trench Trophy", "note": "Top IDP performance",
                           "team_index": idp, "player_key": idp_key,
                           "points": wk[idp]["best_idp"]["points"]},
            },
        })
    return out


def head_to_head(standings_rows, weeks_played):
    """Sixteen by sixteen grid of who has beaten whom this season."""
    n = len(TEAMS)
    grid = [[None] * n for _ in range(n)]
    for r in standings_rows:
        i = r["team_index"]
        for w in r["weekly"]:
            j = w["opponent"]
            cell = grid[i][j] or {"w": 0, "l": 0, "pf": 0.0, "pa": 0.0}
            if w["points"] > w["opp_points"]:
                cell["w"] += 1
            elif w["points"] < w["opp_points"]:
                cell["l"] += 1
            cell["pf"] = round(cell["pf"] + w["points"], 2)
            cell["pa"] = round(cell["pa"] + w["opp_points"], 2)
            grid[i][j] = cell
    return grid


# ---------------------------------------------------------------------------
# Recap prose
#
# In the real pipeline this is the one place a language model gets used, and it
# is handed a finished fact object rather than a pile of numbers to interpret.
# Here the same fact object drives a small templater, so the dummy recap can
# never contradict the dummy data. Swapping the templater for the Anthropic call
# changes nothing else.
#
# Voice rules, enforced here and in the eventual prompt: dry and observational,
# full sentences, no em dashes, nothing invented, and nothing about a manager
# that is not about the game.
# ---------------------------------------------------------------------------

BLOWOUT = [
    "{w} put {wp} on the board and {l} never got close, finishing {m} behind.",
    "{w} handled {l} by {m}, and it was settled well before the late games kicked off.",
    "There was no second act here. {w} scored {wp}, {l} scored {lp}, and the rest was arithmetic.",
    "{l} scored {lp}, which would have won a different matchup. It did not win this one, because {w} scored {wp}.",
    "{w} beat {l} by {m}. There is not a great deal to add about a game decided by that much.",
    "{w} reached {wp} and {l} stopped at {lp}. The second half was a formality.",
]
CLOSE = [
    "{w} beat {l} by {m}, which is the kind of margin that gets recounted twice.",
    "{w} and {l} traded the lead into Monday night, and {w} came out {m} ahead.",
    "{m} separated {w} from {l}. Neither manager will describe this week as relaxing.",
    "{w} won this by {m}. Any one of nine players on either roster could have flipped it.",
    "{w} finished at {wp} and {l} at {lp}, which is close enough that both managers watched the last drive.",
]
NORMAL = [
    "{w} took care of {l}, {wp} to {lp}.",
    "{w} scored {wp}, and that was enough against {l}, who managed {lp}.",
    "{l} posted {lp} and ran into {w} at {wp}.",
    "{w} led this one most of the way and closed it out {wp} to {lp}.",
    "{w} got {wp} out of its lineup. {l} got {lp} out of its own, and that was the game.",
]
BENCH_NOTE = [
    " {t} left {p} points on the bench, which would have changed the result.",
    " {t} benched {p} points worth of production, and the margin was smaller than that.",
]
TREND = [
    " {w} has now won {n} in a row.",
    " That is {n} straight for {w}.",
]


def write_recap(rng, week_obj, standings_rows, player_log, players_by_key, week_rows):
    wk = {r["team_index"]: r for r in week_rows if r["week"] == week_obj["week"]}
    name = lambda i: TEAMS[i][0]
    aw = week_obj["awards"]

    lede = (
        "Week %d is in the books. %s led the league at %s points and %s brought up "
        "the rear at %s, a spread of %s. The best single defensive performance of "
        "the weekend belonged to %s, and %s left %s points sitting on its bench."
    ) % (
        week_obj["week"],
        name(aw["gold_cup"]["team_index"]), fmt(aw["gold_cup"]["points"]),
        name(aw["sacko"]["team_index"]), fmt(aw["sacko"]["points"]),
        fmt(round(aw["gold_cup"]["points"] - aw["sacko"]["points"], 2)),
        name(aw["trench"]["team_index"]),
        name(aw["regret"]["team_index"]), fmt(aw["regret"]["points"]),
    )

    paras = []
    for m in sorted(week_obj["matchups"], key=lambda x: -x["margin"]):
        if m["winner"] is None:
            paras.append("%s and %s tied at %s, which the league constitution does not"
                         " appear to address." % (name(m["team_a"]), name(m["team_b"]),
                                                  fmt(m["points_a"])))
            continue
        w = m["winner"]
        l = m["team_b"] if w == m["team_a"] else m["team_a"]
        wp = m["points_a"] if w == m["team_a"] else m["points_b"]
        lp = m["points_b"] if w == m["team_a"] else m["points_a"]
        bank = BLOWOUT if m["margin"] > 34 else CLOSE if m["margin"] < 6 else NORMAL
        text = rng.choice(bank).format(
            w=name(w), l=name(l), wp=fmt(wp), lp=fmt(lp), m=fmt(m["margin"]))

        loser_regret = wk[l]["regret"]
        if loser_regret > m["margin"] and rng.random() < 0.7:
            text += rng.choice(BENCH_NOTE).format(t=name(l), p=fmt(loser_regret))
        else:
            streak = current_streak([r for r in standings_rows[w]["results"]][:week_obj["week"]])
            if streak.startswith("W") and int(streak[1:]) >= 3 and rng.random() < 0.5:
                text += rng.choice(TREND).format(w=name(w), n=streak[1:])
        paras.append(text)

    return {
        "week": week_obj["week"],
        "headline": recap_headline(week_obj, name),
        "lede": lede,
        "paragraphs": paras,
        "status": "published",
        "generated_at": (WEEK1_MONDAY + timedelta(days=7 * (week_obj["week"] - 1) + 1,
                                                  hours=7)).isoformat(),
    }


def recap_headline(week_obj, name):
    aw = week_obj["awards"]
    # Constructed so it stays grammatical no matter what a manager named the team.
    return "Week %d: %s on Top, %s on the Bottom" % (
        week_obj["week"], name(aw["gold_cup"]["team_index"]), name(aw["sacko"]["team_index"]))


def fmt(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def dump(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, indent=1, sort_keys=False))
    return "%-16s %7.1f KB" % (name, path.stat().st_size / 1024)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weeks", type=int, default=11,
                    help="weeks of the season already played (default 11)")
    ap.add_argument("--seed", type=int, default=8)
    ap.add_argument("--overwrite-real-data", action="store_true",
                    help="required when site/static/data holds real Yahoo data")
    args = ap.parse_args()
    existing = OUT / "league.json"
    if existing.exists() and not args.overwrite_real_data:
        try:
            if json.loads(existing.read_text()).get("source") == "yahoo":
                raise SystemExit("site/static/data holds real Yahoo data. Pass "
                                 "--overwrite-real-data to replace it with demo data.")
        except ValueError:
            pass
    weeks_played = clamp(args.weeks, 1, REGULAR_SEASON_WEEKS)
    rng = random.Random(args.seed)

    players = build_player_pool(rng)
    picks, waiver_pool = run_draft(rng, players)
    rosters = assemble_rosters(rng, players, picks, waiver_pool)
    players_by_key = {p["player_key"]: p for r in rosters for p in r}

    week_rows, player_log, skill, injuries = simulate_season(rng, rosters, weeks_played)
    schedule = build_schedule(len(TEAMS), REGULAR_SEASON_WEEKS)
    standings = compute_standings(week_rows, schedule, weeks_played)
    power = compute_power(standings, weeks_played)
    odds = playoff_odds(rng, standings, schedule, weeks_played)
    totals, totals_by_week = compute_race_totals(week_rows, player_log, weeks_played)
    ledger, global_fit, pos_fits = compute_draft_value(
        players_by_key, rosters, player_log, weeks_played)
    txns = generate_transactions(rng, rosters, players_by_key, weeks_played)
    weeks = build_weeks(week_rows, schedule, weeks_played, player_log, players_by_key)
    grid = head_to_head(standings, weeks_played)

    # Fold roster-wide draft value into the race totals so The Claiming Stakes runs.
    for e in ledger:
        totals[e["team_index"]].setdefault("value_over_slot", 0.0)
        totals[e["team_index"]]["value_over_slot"] += e["value_over_slot"]
    for t in totals:
        t["value_over_slot"] = round(t["value_over_slot"], 2)

    built_at = WEEK1_MONDAY + timedelta(days=7 * (weeks_played - 1) + 1, hours=7, minutes=12)

    # --- league.json -------------------------------------------------------
    teams_out = []
    for i, (name, manager, code) in enumerate(TEAMS):
        hue_name, light, dark = SILK_HUES[i % len(SILK_HUES)]
        teams_out.append({
            "team_index": i,
            "team_key": "461.l.884422.t.%d" % (i + 1),
            "team_id": i + 1,
            "name": name, "code": code,
            "silk": {
                "hue": hue_name, "light": light, "dark": dark,
                # Sixteen teams cannot be told apart by hue alone. Pattern is the
                # second channel and the three-letter code is the third.
                "pattern": SILK_PATTERNS[i // len(SILK_HUES)],
            },
            "manager_skill": round(skill[i], 3),
        })

    races_cfg = read_races_toml(ROOT / "races.toml")
    print(dump("league.json", {
        "league_key": "461.l.884422",
        "name": "Life's Gr8",
        "season": SEASON,
        "num_teams": len(TEAMS),
        "scoring_type": "head-to-head, custom IDP",
        "current_week": weeks_played + 1,
        "completed_week": weeks_played,
        "regular_season_weeks": REGULAR_SEASON_WEEKS,
        "playoff_teams": PLAYOFF_TEAMS,
        "roster_slots": SLOTS,
        "bench_slots": BENCH_COUNT,
        "scoring": SCORING,
        "stat_labels": STAT_LABELS,
        "teams": teams_out,
        "built_at": built_at.isoformat(),
        "source": "dummy",
        "notice": ("Demo data. Every figure on this site was generated by "
                   "tools/generate_dummy_data.py and is not a real result."),
    }))

    # --- standings.json ----------------------------------------------------
    odds_by = {o["team_index"]: o for o in odds}
    power_by = {p["team_index"]: p for p in power}
    print(dump("standings.json", {
        "week": weeks_played,
        "rows": [{
            "team_index": r["team_index"],
            "wins": r["wins"], "losses": r["losses"], "ties": r["ties"],
            "pf": r["pf"], "pa": r["pa"], "ppg": r["ppg"],
            "high": r["high"], "low": r["low"],
            "all_play_wins": r["ap_w"], "all_play_losses": r["ap_l"],
            "all_play_pct": r["ap_pct"],
            "expected_wins": r["expected_wins"], "luck": r["luck"],
            "streak": r["streak"], "results": r["results"],
            "optimal_total": r["optimal_total"], "regret_total": r["regret_total"],
            "efficiency": r["efficiency"], "last3_ppg": r["last3_ppg"],
            "weekly": r["weekly"],
            "power_rank": power_by[r["team_index"]]["rank"],
            "power_score": power_by[r["team_index"]]["score"],
            "playoff_pct": odds_by[r["team_index"]]["playoff_pct"],
            "bye_pct": odds_by[r["team_index"]]["bye_pct"],
        } for r in standings],
        "head_to_head": grid,
    }))

    # --- races.json --------------------------------------------------------
    races_out = []
    track = races_cfg.get("track", {})
    for rc in races_cfg.get("race", []):
        stat = rc["stat"]
        values = [t.get(stat, 0) for t in totals]
        pos = race_positions(values, rc.get("direction", "high"),
                             track.get("leader_scale", 0.90),
                             track.get("position_floor", 0.08))
        order = sorted(range(len(TEAMS)), key=lambda i: values[i],
                       reverse=rc.get("direction", "high") == "high")
        runners = [{"team_index": i, "value": values[i], "position": pos[i],
                    "place": order.index(i) + 1} for i in range(len(TEAMS))]
        leader, second = order[0], order[1]
        gap = abs(values[leader] - values[second])
        lead_pct = (gap / values[leader] * 100) if values[leader] else 0
        races_out.append({
            "id": rc["id"], "name": rc["name"], "blurb": rc["blurb"],
            "stat": stat, "unit": rc["unit"], "direction": rc.get("direction", "high"),
            "featured": rc.get("featured", False),
            "runners": runners,
            "leader": leader, "gap": round(gap, 2), "gap_pct": round(lead_pct, 2),
            "splits": [[round(w[i].get(stat, 0), 2) for i in range(len(TEAMS))]
                       for w in totals_by_week[1:weeks_played + 1]],
        })
    races_out.sort(key=lambda r: r["gap_pct"])
    print(dump("races.json", {"week": weeks_played, "track": track, "races": races_out}))

    # --- season.json -------------------------------------------------------
    print(dump("season.json", {
        "week": weeks_played,
        "weeks": weeks,
        "team_weeks": [{
            "week": r["week"], "team_index": r["team_index"],
            "points": r["points"], "optimal": r["optimal"], "regret": r["regret"],
            "best_starter": r["best_starter"], "worst_starter": r["worst_starter"],
            "best_bench": r["best_bench"], "best_idp": r["best_idp"],
        } for r in week_rows],
    }))

    # --- draft.json --------------------------------------------------------
    by_value = sorted(ledger, key=lambda e: -e["value_over_slot"])
    team_value = [0.0] * len(TEAMS)
    for e in ledger:
        team_value[e["team_index"]] += e["value_over_slot"]
    print(dump("draft.json", {
        "week": weeks_played,
        "weeks_elapsed": weeks_played,
        "baseline": {
            "method": ("binned median production by region of the draft board, "
                       "forced non-increasing, interpolated linearly between region "
                       "centers and held flat outside the fitted range"),
            "global": [[round(x, 1), round(y, 2)] for x, y in global_fit],
            "by_position": {k: [[round(x, 1), round(y, 2)] for x, y in v]
                            for k, v in pos_fits.items()},
            "undrafted_slot": 16 * len(TEAMS) + 1,
        },
        "steals": by_value[:25],
        "busts": list(reversed(by_value[-25:])),
        "team_totals": [{"team_index": i, "value_over_slot": round(team_value[i], 2)}
                        for i in range(len(TEAMS))],
        "all": ledger,
    }))

    # --- blotter.json ------------------------------------------------------
    print(dump("blotter.json", {"week": weeks_played, "transactions": txns}))

    # --- leaders.json ------------------------------------------------------
    print(dump("leaders.json", compute_leaders(rosters, player_log, week_rows, weeks_played)))

    # --- players.json ------------------------------------------------------
    print(dump("players.json", {
        "players": {p["player_key"]: {
            "name": p["name"], "position": p["position"], "nfl_team": p["nfl_team"],
            "bye": p["bye"], "team_index": ti,
            "pick_number": p.get("pick_number"), "round": p.get("round"),
        } for ti, roster in enumerate(rosters) for p in roster}
    }))

    # --- recaps.json -------------------------------------------------------
    recaps = [write_recap(rng, w, standings, player_log, players_by_key, week_rows)
              for w in weeks]
    print(dump("recaps.json", {"week": weeks_played, "recaps": list(reversed(recaps))}))

    print("\nSeason: %d weeks played, %d teams, %d players rostered."
          % (weeks_played, len(TEAMS), len(players_by_key)))
    lead = max(standings, key=lambda r: (r["wins"], r["pf"]))
    print("Leader: %s (%d-%d, %.2f PF, luck %+0.2f)"
          % (TEAMS[lead["team_index"]][0], lead["wins"], lead["losses"],
             lead["pf"], lead["luck"]))


if __name__ == "__main__":
    main()
