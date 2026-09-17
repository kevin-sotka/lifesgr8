"""
SQLite -> the nine JSON files the site renders.

Pure functions over the snapshot database: no network, no LLM, and no randomness
except the playoff simulation, which is seeded so a rebuild is reproducible.
docs/DATA-CONTRACT.md is the specification for every file written here.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..config import DATA, LEAGUE_TOML, RACES_TOML, ROOT, read_toml
from . import newage

OUT = ROOT / "site" / "static" / "data"
SILKS_FILE = DATA / "silks.json"

SILK_HUES = [
    ("blue", "#2a78d6", "#3987e5"), ("orange", "#eb6834", "#d95926"),
    ("aqua", "#1baf7a", "#199e70"), ("yellow", "#eda100", "#c98500"),
    ("magenta", "#e87ba4", "#d55181"), ("green", "#008300", "#008300"),
    ("violet", "#4a3aa7", "#9085e9"), ("red", "#e34948", "#e66767"),
]
SILK_PATTERNS = ["solid", "hoops"]

NFL_GAMES = 17          # a full season of games, for scaling season baselines
IDP_SLOT = "D"          # Life's Gr8's two generic individual defender slots
DEF_SLOT = "DEF"
NON_STARTER = {"BN", "IR", "IR+", "NA"}

# Yahoo stat ids this league scores.
S = {
    "pass_yd": "4", "pass_td": "5", "rush_yd": "9", "rush_td": "10", "rec": "11",
    "rec_yd": "12", "rec_td": "13", "ret_td": "15", "fum_ret_td": "57",
    "solo": "38", "assist": "39", "sack": "40", "def_int": "41", "ff": "42", "fr": "43",
    "def_td": "44", "pass_def": "46", "tfl": "65", "team_def_td": "35", "team_ret_td": "49",
}
TD_IDS = [S[k] for k in ("pass_td", "rush_td", "rec_td", "ret_td", "fum_ret_td", "def_td",
                         "team_def_td", "team_ret_td")]


# =============================================================================
# small helpers
# =============================================================================

def rows(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> List[sqlite3.Row]:
    return list(conn.execute(sql, args))


def stats_of(row: Any) -> Dict[str, float]:
    return json.loads(row["stats_json"]) if row and row["stats_json"] else {}


NFL_CODES = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
             "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
             "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS", "WSH", "JAC", "OAK", "SD",
             "STL", "LA", "DEF"}


def team_code(name: str, taken: set) -> str:
    """
    A three-letter code, stable and unique, which the UI prints beside every silk.
    NFL abbreviations are off limits: player rows print those right next to it.
    """
    taken = taken | NFL_CODES
    words = ["".join(ch for ch in w if ch.isalnum()) for w in name.split()]
    words = [w for w in words if w] or ["TEAM"]
    if len(words) >= 3:
        base = "".join(w[0] for w in words[:3])
    elif len(words) == 2:
        base = words[0][0] + words[1][:2]
    else:
        base = words[0][:3]
    base = (base.upper() + "XXX")[:3]
    code, n = base, 0
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"
    while code in taken:
        code = base[:2] + alphabet[n % len(alphabet)]
        n += 1
    return code


NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def short_name(full: Optional[str], position_type: Optional[str] = None) -> str:
    """
    "Derrick Henry" -> "D. Henry", "Kenneth Walker III" -> "K. Walker",
    "Amon-Ra St. Brown" -> "A. St. Brown". Team defenses keep their name.
    """
    if not full:
        return "?"
    if position_type == "DT":
        return full
    parts = [p for p in full.split() if p.lower() not in NAME_SUFFIXES]
    if len(parts) < 2:
        return full
    return "%s. %s" % (parts[0][0], " ".join(parts[1:]))


def position_group(position: Optional[str], position_type: Optional[str]) -> str:
    if position_type == "DP":
        return "IDP"
    if position_type == "DT":
        return "DEF"
    if position_type == "K":
        return "K"
    return (position or "").split(",")[0] or "O"


# =============================================================================
# lineups
# =============================================================================

def lineup_slots(conn: sqlite3.Connection, league_key: str) -> List[str]:
    slots = []
    for r in rows(conn, "SELECT position, count FROM roster_slots WHERE league_key=? ORDER BY ordinal",
                  (league_key,)):
        if r["position"] not in NON_STARTER:
            slots += [r["position"]] * r["count"]
    return slots


def optimal_lineup(slots: Sequence[str], players: Sequence[Tuple[str, List[str], float]]
                   ) -> Tuple[float, List[str]]:
    """
    Best legal lineup from (player_key, eligible_positions, points).

    Single-position slots fill first and multi-position flex slots last, so a flex
    never takes a player a stricter slot needs. With one flex slot, as Life's Gr8
    has, that greedy order is optimal.
    """
    ordered = sorted(slots, key=lambda s: ("/" in s, s))
    ranked = sorted(players, key=lambda p: -p[2])
    used: set = set()
    total = 0.0
    chosen = []
    for slot in ordered:
        for key, eligible, pts in ranked:
            if key not in used and slot in eligible:
                used.add(key)
                chosen.append(key)
                total += pts
                break
    return round(total, 2), chosen


# =============================================================================
# draft baseline
# =============================================================================

def fit_draft_curve(points: Iterable[Tuple[int, float]], bin_size: int = 16
                    ) -> List[Tuple[float, float]]:
    """
    Expected season points by overall pick: median production per region of the
    board, forced non-increasing, interpolated between region centers.

    Medians, not a fitted power law. A power law in log-log space sends the
    expectation for pick one to several hundred points, and then every early pick
    who merely had a good season publishes as a historic bust.
    """
    raw = sorted(((float(x), float(y)) for x, y in points if x and x > 0 and y is not None),
                 key=lambda t: t[0])
    if not raw:
        return [(1.0, 0.0)]
    if len(raw) < 4:
        return [(1.0, sum(y for _, y in raw) / len(raw))]
    knots: List[Tuple[float, float]] = []
    for i in range(0, len(raw), bin_size):
        chunk = raw[i:i + bin_size]
        if len(chunk) < max(2, bin_size // 3) and knots:
            chunk = raw[max(0, i - bin_size):i + len(chunk)]
            knots.pop()
        xs = sorted(x for x, _ in chunk)
        ys = sorted(y for _, y in chunk)
        knots.append((xs[len(xs) // 2], ys[len(ys) // 2]))
    i = 1
    while i < len(knots):
        if knots[i][1] > knots[i - 1][1]:
            (x0, y0), (x1, y1) = knots[i - 1], knots[i]
            knots[i - 1:i + 1] = [((x0 + x1) / 2.0, (y0 + y1) / 2.0)]
            i = max(1, i - 1)
        else:
            i += 1
    return knots


def curve_value(knots: Sequence[Tuple[float, float]], pick: float) -> float:
    x = max(1.0, float(pick))
    if x <= knots[0][0]:
        return knots[0][1]
    if x >= knots[-1][0]:
        return knots[-1][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / ((x1 - x0) or 1.0)
    return knots[-1][1]


# =============================================================================
# the paddock
# =============================================================================

def race_positions(values: Sequence[float], direction: str,
                   leader_scale: float = 0.90, floor: float = 0.08) -> List[float]:
    """
    Leader-relative track position. Mirrored exactly by LG.chart.positions() in
    site/static/js/charts.js; change both or neither.
    """
    if direction == "low":
        worst = max(values) if values else 1.0
        basis = [worst - v + 0.02 * worst for v in values]
    else:
        basis = list(values)
    leader = max(basis) if basis else 0.0
    leader = leader or 1.0
    return [round(max(floor, leader_scale * (b / leader)), 4) for b in basis]


# =============================================================================
# standings
# =============================================================================

def current_streak(results: Sequence[str]) -> str:
    if not results:
        return "-"
    last, n = results[-1], 0
    for r in reversed(results):
        if r != last:
            break
        n += 1
    return "%s%d" % (last, n)


def all_play(week_scores: Dict[int, float]) -> Dict[int, Tuple[int, int, int]]:
    """Each team against every other team's score that week: (wins, losses, ties)."""
    out = {}
    for t, pts in week_scores.items():
        others = [p for o, p in week_scores.items() if o != t]
        out[t] = (sum(1 for p in others if pts > p), sum(1 for p in others if pts < p),
                  sum(1 for p in others if pts == p))
    return out


def playoff_odds(teams: Sequence[int], records: Dict[int, Dict[str, float]],
                 weekly: Dict[int, List[float]], remaining: Sequence[Sequence[Tuple[int, int]]],
                 spots: int, sims: int = 4000, seed: int = 8) -> Dict[int, float]:
    """
    Monte Carlo over the remaining regular season, on the commissioner's schedule.

    Early in a season one or two weeks say little about a team, so each team's mean
    is shrunk toward the league mean by three weeks' worth of weight, and its spread
    falls back to the league-wide spread until it has three weeks of its own.
    """
    rng = random.Random(seed)
    league_pts = [p for pts in weekly.values() for p in pts]
    league_mean = sum(league_pts) / len(league_pts) if league_pts else 100.0
    league_sd = (math.sqrt(sum((p - league_mean) ** 2 for p in league_pts) / len(league_pts))
                 if len(league_pts) > 1 else 20.0) or 20.0
    prior = 3.0
    mean, sd = {}, {}
    for t in teams:
        pts = weekly.get(t, [])
        n = len(pts)
        m = sum(pts) / n if n else league_mean
        mean[t] = (n * m + prior * league_mean) / (n + prior)
        own = math.sqrt(sum((p - m) ** 2 for p in pts) / n) if n > 2 else league_sd
        sd[t] = max(8.0, own)

    made = defaultdict(int)
    for _ in range(sims):
        wins = {t: records[t]["wins"] + 0.5 * records[t]["ties"] for t in teams}
        pf = {t: records[t]["pf"] for t in teams}
        for pairs in remaining:
            for a, b in pairs:
                sa, sb = rng.gauss(mean[a], sd[a]), rng.gauss(mean[b], sd[b])
                pf[a] += sa
                pf[b] += sb
                if sa > sb:
                    wins[a] += 1
                else:
                    wins[b] += 1
        for t in sorted(teams, key=lambda x: (-wins[x], -pf[x]))[:spots]:
            made[t] += 1
    return {t: round(100.0 * made[t] / sims, 1) for t in teams}


# =============================================================================
# assembling the season from the database
# =============================================================================

class Season:
    """Everything compute needs, read once from SQLite."""

    def __init__(self, conn: sqlite3.Connection, league_key: str) -> None:
        self.conn, self.key = conn, league_key
        self.league = rows(conn, "SELECT * FROM leagues WHERE league_key=?", (league_key,))[0]
        self.teams = rows(conn, "SELECT * FROM teams WHERE league_key=? ORDER BY team_id", (league_key,))
        self.idx = {t["team_key"]: i for i, t in enumerate(self.teams)}
        self.n = len(self.teams)
        self.slots = lineup_slots(conn, league_key)
        self.players = {r["player_key"]: r for r in rows(conn, "SELECT * FROM players")}
        self.eligible = {k: json.loads(p["eligible_positions"] or "[]") for k, p in self.players.items()}

        self.matchups = defaultdict(list)
        for m in rows(conn, "SELECT * FROM matchups WHERE league_key=? ORDER BY week", (league_key,)):
            self.matchups[m["week"]].append(m)
        self.completed = [w for w in sorted(self.matchups)
                          if self.matchups[w] and all(m["status"] == "postevent" for m in self.matchups[w])]
        self.completed_week = self.completed[-1] if self.completed else 0
        self.current_week = int(self.league["current_week"] or (self.completed_week + 1))

        self.roster = defaultdict(lambda: defaultdict(list))   # week -> team_index -> rows
        for r in rows(conn, "SELECT * FROM rosters WHERE league_key=?", (league_key,)):
            if r["team_key"] in self.idx:
                self.roster[r["week"]][self.idx[r["team_key"]]].append(r)
        self.stats = {}                                         # (week, player) -> row
        for r in rows(conn, "SELECT * FROM player_week_stats WHERE league_key=?", (league_key,)):
            self.stats[(r["week"], r["player_key"])] = r
        latest = max(self.roster) if self.roster else 0
        self.on_team = {r["player_key"]: ti for ti, rs in self.roster.get(latest, {}).items() for r in rs}

    def points(self, week: int, player_key: str) -> float:
        row = self.stats.get((week, player_key))
        return row["points"] if row else 0.0

    def line(self, week: int, player_key: str) -> Dict[str, float]:
        return stats_of(self.stats.get((week, player_key)))

    def team_points(self, week: int) -> Dict[int, float]:
        out = {}
        for m in self.matchups[week]:
            out[self.idx[m["team_a_key"]]] = m["points_a"]
            out[self.idx[m["team_b_key"]]] = m["points_b"]
        return out


def team_weeks(season: Season) -> List[Dict[str, Any]]:
    """Per team, per completed week: actual, optimal, regret, and the notable players."""
    out = []
    for week in season.completed:
        official = season.team_points(week)
        for ti in range(season.n):
            roster = season.roster[week].get(ti, [])
            starters = [r for r in roster if r["is_starter"]]
            bench = [r for r in roster if not r["is_starter"]]
            pts = lambda r: season.points(week, r["player_key"])
            started_sum = round(sum(pts(r) for r in starters), 2)
            optimal, best_keys = optimal_lineup(season.slots, [
                (r["player_key"], season.eligible.get(r["player_key"], []), pts(r)) for r in roster])
            idp = [r for r in starters if r["selected_position"] == IDP_SLOT]

            def ref(r):
                return {"player_key": r["player_key"], "points": pts(r)} if r is not None else None

            out.append({
                "week": week, "team_index": ti,
                "points": official.get(ti, started_sum),
                "optimal": max(optimal, started_sum),
                "regret": round(max(0.0, optimal - started_sum), 2),
                # The benched players who belonged in the best lineup, which is who the
                # regret was. Starters displaced by them are implied.
                "regret_players": sorted(
                    ({"player_key": k, "points": season.points(week, k)} for k in best_keys
                     if k not in {r["player_key"] for r in starters} and season.points(week, k) > 0),
                    key=lambda e: -e["points"]),
                "best_starter": ref(max(starters, key=pts) if starters else None),
                "worst_starter": ref(min(starters, key=pts) if starters else None),
                "best_bench": ref(max(bench, key=pts) if bench else None),
                "best_idp": ref(max(idp, key=pts) if idp else None),
            })
    return out


def standings(season: Season, tw: List[Dict[str, Any]], rules: Dict[str, Any]) -> Dict[str, Any]:
    n = season.n
    rec = {i: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0, "ap_w": 0, "ap_l": 0,
               "ap_t": 0, "results": [], "weekly": [], "optimal": 0.0, "regret": 0.0}
           for i in range(n)}
    grid: List[List[Optional[Dict[str, Any]]]] = [[None] * n for _ in range(n)]

    for week in season.completed:
        scores = season.team_points(week)
        for t, (w, l, tie) in all_play(scores).items():
            rec[t]["ap_w"] += w; rec[t]["ap_l"] += l; rec[t]["ap_t"] += tie
        for m in season.matchups[week]:
            a, b = season.idx[m["team_a_key"]], season.idx[m["team_b_key"]]
            for me, them, mine, theirs in ((a, b, m["points_a"], m["points_b"]),
                                           (b, a, m["points_b"], m["points_a"])):
                r = rec[me]
                r["pf"] += mine; r["pa"] += theirs
                result = "W" if mine > theirs else "L" if mine < theirs else "T"
                r[{"W": "wins", "L": "losses", "T": "ties"}[result]] += 1
                r["results"].append(result)
                r["weekly"].append({"week": week, "points": mine, "opponent": them, "opp_points": theirs})
                cell = grid[me][them] or {"w": 0, "l": 0, "pf": 0.0, "pa": 0.0}
                cell["w"] += result == "W"; cell["l"] += result == "L"
                cell["pf"] = round(cell["pf"] + mine, 2); cell["pa"] = round(cell["pa"] + theirs, 2)
                grid[me][them] = cell
    for row in tw:
        rec[row["team_index"]]["optimal"] += row["optimal"]
        rec[row["team_index"]]["regret"] += row["regret"]

    reg_end = int(rules.get("season", {}).get("regular_season_end_week", 13))
    spots = int(rules.get("playoffs", {}).get("teams", 8))
    remaining = []
    for week in range(season.completed_week + 1, reg_end + 1):
        remaining.append([(season.idx[m["team_a_key"]], season.idx[m["team_b_key"]])
                          for m in season.matchups.get(week, [])])
    odds = playoff_odds(list(range(n)), rec,
                        {t: [w["points"] for w in rec[t]["weekly"]] for t in rec}, remaining, spots)

    out_rows = []
    for t in range(n):
        r = rec[t]
        games = r["wins"] + r["losses"] + r["ties"]
        ap_games = r["ap_w"] + r["ap_l"] + r["ap_t"]
        ap_pct = (r["ap_w"] + 0.5 * r["ap_t"]) / ap_games if ap_games else 0.0
        pts = [w["points"] for w in r["weekly"]]
        last3 = pts[-3:]
        out_rows.append({
            "team_index": t, "wins": r["wins"], "losses": r["losses"], "ties": r["ties"],
            "pf": round(r["pf"], 2), "pa": round(r["pa"], 2),
            "ppg": round(r["pf"] / games, 2) if games else 0.0,
            "high": round(max(pts), 2) if pts else 0.0, "low": round(min(pts), 2) if pts else 0.0,
            "all_play_wins": r["ap_w"], "all_play_losses": r["ap_l"], "all_play_pct": round(ap_pct, 4),
            "expected_wins": round(ap_pct * games, 2),
            "luck": round(r["wins"] + 0.5 * r["ties"] - ap_pct * games, 2),
            "streak": current_streak(r["results"]), "results": r["results"],
            "optimal_total": round(r["optimal"], 2), "regret_total": round(r["regret"], 2),
            "efficiency": round(100 * r["pf"] / r["optimal"], 1) if r["optimal"] else 0.0,
            "last3_ppg": round(sum(last3) / len(last3), 2) if last3 else 0.0,
            "weekly": r["weekly"], "playoff_pct": odds[t],
        })

    def z(vals):
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1.0
        return [(v - m) / sd for v in vals]
    zap, zpf, zl3 = (z([r[k] for r in out_rows]) for k in ("all_play_pct", "ppg", "last3_ppg"))
    power = sorted(range(n), key=lambda i: -(0.5 * zap[i] + 0.3 * zpf[i] + 0.2 * zl3[i]))
    for rank, t in enumerate(power, 1):
        out_rows[t]["power_rank"] = rank
        out_rows[t]["power_score"] = round(0.5 * zap[t] + 0.3 * zpf[t] + 0.2 * zl3[t], 3)
    return {"week": season.completed_week, "rows": out_rows, "head_to_head": grid}


# =============================================================================
# draft value
# =============================================================================

def draft_baseline(conn: sqlite3.Connection, bin_size: int) -> Dict[str, Any]:
    hist = rows(conn, "SELECT * FROM draft_history")
    global_fit = fit_draft_curve(((h["pick_number"], h["season_points"]) for h in hist), bin_size)
    by_group: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    for h in hist:
        by_group[position_group(h["position"], h["position_type"])].append(
            (h["pick_number"], h["season_points"]))
    # A position with too few historical picks to fit falls back to the global curve.
    fits = {g: (fit_draft_curve(pts, max(6, bin_size // 2)) if len(pts) >= 24 else global_fit)
            for g, pts in by_group.items()}
    return {"global": global_fit, "by_group": fits,
            "seasons": sorted({h["season"] for h in hist}), "picks": len(hist)}


def draft_ledger(season: Season, baseline: Dict[str, Any], through_week: int,
                 roster_week: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Value over slot for every drafted player and every undrafted player on a roster.
    Weeks missed are reported beside the number and never used to adjust it.
    """
    picks = rows(season.conn, "SELECT * FROM draft_picks WHERE league_key=?", (season.key,))
    last_pick = max((p["pick_number"] for p in picks), default=season.n * 16)
    weeks = [w for w in season.completed if w <= through_week]
    scale = min(1.0, len(weeks) / float(NFL_GAMES))
    week_for_roster = roster_week if roster_week is not None else max(season.roster or [0])
    rostered = {r["player_key"]: ti for ti, rs in season.roster.get(week_for_roster, {}).items() for r in rs}

    entries: Dict[str, Dict[str, Any]] = {}
    for p in picks:
        entries[p["player_key"]] = {"pick_number": p["pick_number"], "round": p["round"],
                                    "team_index": season.idx.get(p["team_key"])}
    for key, ti in rostered.items():
        entries.setdefault(key, {"pick_number": None, "round": None, "team_index": ti})

    out = []
    for key, e in entries.items():
        player = season.players.get(key)
        if player is None or e["team_index"] is None:
            continue
        group = position_group(player["position"], player["position_type"])
        fit = baseline["by_group"].get(group, baseline["global"])
        slot = e["pick_number"] or (last_pick + 1)
        expected = curve_value(fit, slot) * scale
        if e["pick_number"] is None:
            expected *= 0.95  # one slot past the end of the draft
        pts = round(sum(season.points(w, key) for w in weeks), 2)
        played = sum(1 for w in weeks if season.line(w, key))
        bye_in_window = 1 if player["bye"] and player["bye"] in weeks else 0
        out.append({
            "player_key": key, "name": player["name"], "position": player["position"],
            "nfl_team": player["nfl_team"], "team_index": e["team_index"],
            "current_team_index": rostered.get(key),
            "pick_number": e["pick_number"], "round": e["round"],
            "points": pts, "games": played, "missed": max(0, len(weeks) - played - bye_in_window),
            "expected": round(expected, 2), "value_over_slot": round(pts - expected, 2),
            "undrafted": e["pick_number"] is None,
        })
    return out


# =============================================================================
# races
# =============================================================================

JOCKEYS_SHOWN = 3
EMPTY_JOCKEYS: Dict[str, Any] = {"top": [], "more": 0}


def _jockeys(contrib: Dict[str, float], season: "Season") -> Dict[str, Any]:
    """The top contributors to one team's race total, plus how many others chipped in."""
    ranked = sorted(((k, v) for k, v in contrib.items() if abs(v) > 1e-9), key=lambda kv: -kv[1])
    top = [{"name": short_name(season.players[k]["name"], season.players[k]["position_type"]),
            "value": round(v, 2)} for k, v in ranked[:JOCKEYS_SHOWN]]
    return {"top": top, "more": max(0, len(ranked) - JOCKEYS_SHOWN)}


def race_totals(season: Season, tw: List[Dict[str, Any]], baseline: Dict[str, Any]):
    """
    Cumulative race stats per team, STARTED PLAYERS ONLY, plus a split per week.

    Every total also records which players produced it, so a lane can name its
    jockeys. Contributions are summed per player across the weeks they started for
    that team, which is why a player traded mid-season rides for both stables.
    """
    by_week = {(r["week"], r["team_index"]): r for r in tw}
    totals = [defaultdict(float) for _ in range(season.n)]
    contrib = [defaultdict(lambda: defaultdict(float)) for _ in range(season.n)]
    splits, split_jockeys = [], []
    for week in season.completed:
        official = season.team_points(week)
        for ti in range(season.n):
            t, c = totals[ti], contrib[ti]
            t["points_for"] += official.get(ti, 0.0)
            row = by_week.get((week, ti))
            if row:
                t["bench_regret"] += row["regret"]
                for rp in row["regret_players"]:
                    c["bench_regret"][rp["player_key"]] += rp["points"]
            for r in season.roster[week].get(ti, []):
                if not r["is_starter"]:
                    continue
                key, slot = r["player_key"], r["selected_position"]
                line = season.line(week, key)

                def add(stat, value):
                    if value:
                        t[stat] += value
                        c[stat][key] += value

                c["points_for"][key] += season.points(week, key)
                add("pass_yards", line.get(S["pass_yd"], 0))
                add("rush_yards", line.get(S["rush_yd"], 0))
                add("touchdowns", sum(line.get(i, 0) for i in TD_IDS))
                if slot == IDP_SLOT:
                    add("sacks", line.get(S["sack"], 0))
                    add("solo_tackles", line.get(S["solo"], 0))
                    add("takeaways", sum(line.get(S[k], 0) for k in ("def_int", "ff", "fr")))
                if slot == DEF_SLOT:
                    add("defense_points", season.points(week, key))
        vos = defaultdict(float)
        vos_contrib = [defaultdict(float) for _ in range(season.n)]
        for e in draft_ledger(season, baseline, week, roster_week=week):
            if e["current_team_index"] is not None:
                vos[e["current_team_index"]] += e["value_over_slot"]
                vos_contrib[e["current_team_index"]][e["player_key"]] = e["value_over_slot"]
        for ti in range(season.n):
            totals[ti]["value_over_slot"] = vos[ti]
            contrib[ti]["value_over_slot"] = vos_contrib[ti]
        splits.append([{k: round(v, 2) for k, v in t.items()} for t in totals])
        split_jockeys.append([{stat: _jockeys(c[stat], season) for stat in c} for c in contrib])
    final = [{k: round(v, 2) for k, v in t.items()} for t in totals]
    jockeys = split_jockeys[-1] if split_jockeys else [{} for _ in range(season.n)]
    return final, splits, jockeys, split_jockeys


def races(season: Season, totals, splits, cfg: Dict[str, Any], jockeys=None,
          split_jockeys=None) -> Dict[str, Any]:
    track = cfg.get("track", {})
    out = []
    for rc in cfg.get("race", []):
        stat, direction = rc["stat"], rc.get("direction", "high")
        values = [t.get(stat, 0.0) for t in totals]
        pos = race_positions(values, direction, track.get("leader_scale", 0.9),
                             track.get("position_floor", 0.08))
        order = sorted(range(season.n), key=lambda i: values[i], reverse=(direction == "high"))
        leader = order[0]
        gap = abs(values[order[0]] - values[order[1]]) if season.n > 1 else 0.0
        out.append({
            "id": rc["id"], "name": rc["name"], "blurb": rc["blurb"], "stat": stat,
            "unit": rc["unit"], "direction": direction, "featured": rc.get("featured", False),
            "runners": [{"team_index": i, "value": values[i], "position": pos[i],
                         "place": order.index(i) + 1,
                         "jockeys": (jockeys or [{}] * season.n)[i].get(stat, EMPTY_JOCKEYS)}
                        for i in range(season.n)],
            "leader": leader, "gap": round(gap, 2),
            "gap_pct": round(100.0 * gap / abs(values[leader]), 2) if values[leader] else 100.0,
            "splits": [[w[i].get(stat, 0.0) for i in range(season.n)] for w in splits],
            # Names only for past weeks: values would repeat the whole race table
            # thirteen times over by December, for a tooltip on the scrubber.
            "split_jockeys": [[{"names": [j["name"] for j in w[i].get(stat, EMPTY_JOCKEYS)["top"]],
                                "more": w[i].get(stat, EMPTY_JOCKEYS)["more"]}
                               for i in range(season.n)] for w in (split_jockeys or [])],
        })
    out.sort(key=lambda r: r["gap_pct"])
    return {"week": season.completed_week, "track": track, "races": out}


# =============================================================================
# the purse
# =============================================================================

def purse(season: Season, places: int, amounts: Optional[Dict[int, float]] = None,
          currency: str = "$") -> Dict[str, Any]:
    """
    The weekly high scores the commissioner pays out at the end of the season.

    Ranks are competition ranks, so a tie at the cut pays everyone tied: two teams
    level on 4th means five teams in the money that week. The tally counts each
    finishing place separately, because 1st four times is not 4th four times.
    """
    amounts = amounts or {}
    weeks_out, tally = [], {i: {"team_index": i, "places": [0] * places, "weeks_in_the_money": 0,
                                "points": 0.0, "money": 0.0} for i in range(season.n)}
    for week in season.completed:
        scores = season.team_points(week)
        ordered = sorted(range(season.n), key=lambda i: -scores[i])
        paid = []
        for ti in ordered:
            rank = 1 + sum(1 for o in range(season.n) if scores[o] > scores[ti])
            if rank > places:
                continue
            # A tie at the cut pays every tied team that place's amount, which is how the
            # commissioner has always run it.
            money = amounts.get(rank, 0.0)
            paid.append({"team_index": ti, "points": round(scores[ti], 2), "place": rank,
                         "amount": money,
                         "tied": sum(1 for o in range(season.n) if scores[o] == scores[ti]) > 1})
            tally[ti]["places"][rank - 1] += 1
            tally[ti]["weeks_in_the_money"] += 1
            tally[ti]["points"] = round(tally[ti]["points"] + scores[ti], 2)
            tally[ti]["money"] = round(tally[ti]["money"] + money, 2)
        weeks_out.append({"week": week, "paid": paid})

    rows = sorted(tally.values(), key=lambda r: (-r["money"], -r["weeks_in_the_money"], -r["points"]))
    return {"places": places, "currency": currency,
            "amounts": [amounts.get(p, 0.0) for p in range(1, places + 1)],
            "paid_so_far": round(sum(r["money"] for r in rows), 2),
            "weeks": list(reversed(weeks_out)), "tally": rows}


# =============================================================================
# weeks and awards
# =============================================================================

def weeks(season: Season, tw: List[Dict[str, Any]], purse_cfg: Optional[Dict[str, Any]] = None
          ) -> Dict[str, Any]:
    by = {(r["week"], r["team_index"]): r for r in tw}
    out = []
    for week in season.completed:
        ms = []
        for m in season.matchups[week]:
            a, b = season.idx[m["team_a_key"]], season.idx[m["team_b_key"]]
            ms.append({"team_a": a, "team_b": b, "points_a": m["points_a"], "points_b": m["points_b"],
                       "margin": round(abs(m["points_a"] - m["points_b"]), 2),
                       "winner": a if m["points_a"] > m["points_b"] else b if m["points_b"] > m["points_a"] else None})
        scores = season.team_points(week)
        hi = max(scores, key=scores.get)
        lo = min(scores, key=scores.get)
        decided = [m for m in ms if m["winner"] is not None]
        blow = max(decided, key=lambda m: m["margin"]) if decided else None
        close = min(decided, key=lambda m: m["margin"]) if decided else None
        reg = max(range(season.n), key=lambda i: by[(week, i)]["regret"])
        idp_rows = [by[(week, i)] for i in range(season.n) if by[(week, i)]["best_idp"]]
        trench = max(idp_rows, key=lambda r: r["best_idp"]["points"]) if idp_rows else None
        awards = {
            "gold_cup": {"label": "The Gold Cup", "note": "Week high", "team_index": hi, "points": scores[hi]},
            "sacko": {"label": "The Sacko", "note": "Week low", "team_index": lo, "points": scores[lo]},
        }
        if blow:
            awards["blowout"] = {"label": "The Woodshed", "note": "Widest margin",
                                 "team_index": blow["winner"], "margin": blow["margin"]}
            awards["nailbiter"] = {"label": "The Photo Finish", "note": "Closest game",
                                   "team_index": close["winner"], "margin": close["margin"]}
        best_bench = by[(week, reg)]["best_bench"]
        awards["regret"] = {"label": "The Regret Award", "note": "Most points benched",
                            "team_index": reg, "points": by[(week, reg)]["regret"],
                            "player_key": best_bench["player_key"] if best_bench else None}
        if trench:
            awards["trench"] = {"label": "The Trench Trophy", "note": "Top IDP performance",
                                "team_index": trench["team_index"],
                                "player_key": trench["best_idp"]["player_key"],
                                "points": trench["best_idp"]["points"]}
        start = season.matchups[week][0]["week_start"]
        out.append({"week": week, "date": start, "matchups": ms, "awards": awards})
    cfg = purse_cfg or {}
    places = int(cfg.get("places", 4))
    amounts = {p: float(cfg.get("place_%d" % p, 0)) for p in range(1, places + 1)}
    return {"week": season.completed_week, "weeks": out, "team_weeks": tw,
            "purse": purse(season, places, amounts, cfg.get("currency", "$"))}


# =============================================================================
# the blotter
# =============================================================================

def blotter(season: Season) -> Dict[str, Any]:
    spans = [(w, ms[0]["week_start"], ms[0]["week_end"]) for w, ms in season.matchups.items()
             if ms and ms[0]["week_start"]]

    def week_of(ts: int) -> int:
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        for w, start, end in spans:
            if start <= day <= end:
                return w
        return season.current_week

    out = []
    for r in rows(season.conn, "SELECT * FROM transactions WHERE league_key=? ORDER BY timestamp DESC",
                  (season.key,)):
        t = json.loads(r["payload_json"])
        moves = t.get("moves", [])
        teams, added, dropped, sends, source = [], None, None, [], None
        for mv in moves:
            data, key = mv["move"], mv["player"]["player_key"]
            src, dst = data.get("source_team_key"), data.get("destination_team_key")
            if r["type"] in ("trade", "pending_trade"):
                if src in season.idx:
                    sends.append({"team_index": season.idx[src], "player_key": key})
            elif data.get("type") == "add":
                added, source = key, data.get("source_type")
            elif data.get("type") == "drop":
                dropped = key
            for tk in (dst, src):
                if tk in season.idx and season.idx[tk] not in teams:
                    teams.append(season.idx[tk])
        if not teams:
            continue
        row = {"id": r["transaction_key"], "type": r["type"], "status": r["status"],
               "week": week_of(r["timestamp"]),
               "timestamp": datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).isoformat(),
               "teams": teams}
        if sends:
            row["sends"] = sends
        else:
            row.update({"added": added, "dropped": dropped, "source": source, "faab": None})
        if r["type"] == "commish":
            row["note"] = "Commissioner move"
        out.append(row)
    return {"week": season.completed_week, "transactions": out}


# =============================================================================
# leaders
# =============================================================================

def leaders(season: Season) -> Dict[str, Any]:
    agg: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    single, idp_started, idp_benched = [], [], []
    for week in season.completed:
        slot_of = {r["player_key"]: r["selected_position"]
                   for rs in season.roster[week].values() for r in rs}
        team_of = {r["player_key"]: ti for ti, rs in season.roster[week].items() for r in rs}
        for (w, key), row in season.stats.items():
            if w != week or key not in team_of:
                continue
            line, pts = stats_of(row), row["points"]
            a = agg[key]
            a["points"] += pts
            for name in ("sack", "solo", "assist", "pass_yd", "rush_yd", "rec_yd"):
                a[name] += line.get(S[name], 0)
            a["takeaways"] += sum(line.get(S[k], 0) for k in ("def_int", "ff", "fr"))
            a["touchdowns"] += sum(line.get(i, 0) for i in TD_IDS)
            slot = slot_of.get(key, "BN")
            player = season.players[key]
            if pts > 0:
                entry = {"player_key": key, "name": player["name"], "position": player["position"],
                         "team_index": team_of[key], "week": week, "points": pts, "line": line,
                         "started": slot not in NON_STARTER}
                single.append(entry)
                if player["position_type"] == "DP":
                    (idp_started if slot == IDP_SLOT else idp_benched if slot in NON_STARTER else []).append(entry)

    table = []
    for key, a in agg.items():
        if key not in season.on_team:
            continue
        p = season.players[key]
        table.append({"player_key": key, "name": p["name"], "position": p["position"],
                      "nfl_team": p["nfl_team"], "team_index": season.on_team[key],
                      "points": round(a["points"], 2), "sack": a["sack"], "solo": a["solo"],
                      "assist": a["assist"], "tackles": a["solo"] + a["assist"],
                      "takeaways": a["takeaways"], "touchdowns": a["touchdowns"],
                      "pass_yd": a["pass_yd"], "rush_yd": a["rush_yd"], "rec_yd": a["rec_yd"]})

    def top(k, n=12, types=None):
        pool = [r for r in table if r[k] > 0 and
                (types is None or season.players[r["player_key"]]["position_type"] in types)]
        return sorted(pool, key=lambda r: -r[k])[:n]

    idp = ("DP",)
    return {
        "week": season.completed_week,
        "season": {"points": top("points"), "sacks": top("sack", types=idp),
                   "tackles": top("tackles", types=idp), "takeaways": top("takeaways", types=idp),
                   "touchdowns": top("touchdowns", types=("O",)), "pass_yd": top("pass_yd"),
                   "rush_yd": top("rush_yd"), "rec_yd": top("rec_yd")},
        "single_week": sorted(single, key=lambda e: -e["points"])[:15],
        "idp_week_started": sorted(idp_started, key=lambda e: -e["points"])[:10],
        "idp_week_benched": sorted(idp_benched, key=lambda e: -e["points"])[:10],
    }


# =============================================================================
# identity, league, recaps
# =============================================================================

def assign_silks(season: Season, path: Path = SILKS_FILE) -> Dict[str, Dict[str, Any]]:
    """
    Silks and codes are assigned once per team and kept in data/silks.json, which is
    committed, so a team keeps its colours all season and in every CI build. Keyed by
    Yahoo team id, never by manager: the site names teams only, and this file lives in
    a public repository.
    """
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = {}
    used = {(v["hue"], v["pattern"]) for v in saved.values()}
    codes = {v["code"] for v in saved.values()}
    combos = [(h, p) for p in range(len(SILK_PATTERNS)) for h in range(len(SILK_HUES))]
    changed = False
    out = {}
    for t in season.teams:
        ident = "team:%s" % t["team_id"]
        if ident not in saved:
            hue, pattern = next((c for c in combos if c not in used), combos[t["team_id"] % len(combos)])
            used.add((hue, pattern))
            code = team_code(t["name"], codes)
            codes.add(code)
            saved[ident] = {"hue": hue, "pattern": pattern, "code": code}
            changed = True
        out[t["team_key"]] = saved[ident]
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, indent=1, sort_keys=True))
    return out


def league_json(season: Season, rules: Dict[str, Any], silks: Dict[str, Dict[str, Any]],
                built_at: str) -> Dict[str, Any]:
    cats = rows(season.conn, "SELECT * FROM stat_categories WHERE league_key=? AND modifier IS NOT NULL",
                (season.key,))
    teams = []
    for i, t in enumerate(season.teams):
        s = silks[t["team_key"]]
        name, light, dark = SILK_HUES[s["hue"]]
        teams.append({"team_index": i, "team_key": t["team_key"], "team_id": t["team_id"],
                      "name": t["name"], "code": s["code"],
                      "silk": {"hue": name, "light": light, "dark": dark,
                               "pattern": SILK_PATTERNS[s["pattern"]]}})
    return {
        "league_key": season.key, "name": season.league["name"], "season": season.league["season"],
        "num_teams": season.n, "scoring_type": "head-to-head, custom IDP",
        "current_week": season.current_week, "completed_week": season.completed_week,
        "regular_season_weeks": int(rules.get("season", {}).get("regular_season_end_week", 13)),
        "playoff_teams": int(rules.get("playoffs", {}).get("teams", 8)),
        "playoff_weeks": [rules.get("playoffs", {}).get("start_week"),
                          rules.get("playoffs", {}).get("end_week")],
        "roster_slots": season.slots,
        "scoring": {c["name"]: c["modifier"] for c in cats},
        "teams": teams, "built_at": built_at, "source": "yahoo",
    }


def players_json(season: Season) -> Dict[str, Any]:
    picks = {p["player_key"]: p for p in rows(season.conn, "SELECT * FROM draft_picks WHERE league_key=?",
                                               (season.key,))}
    return {"players": {k: {"name": p["name"], "position": p["position"], "nfl_team": p["nfl_team"],
                            "bye": p["bye"], "team_index": season.on_team.get(k),
                            "pick_number": picks[k]["pick_number"] if k in picks else None,
                            "round": picks[k]["round"] if k in picks else None}
                        for k, p in season.players.items()}}


def recaps_json(season: Season, recap_dir: Path) -> Dict[str, Any]:
    """Published recaps only. A draft stays off the site until it has been read."""
    out = []
    for f in sorted(recap_dir.glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError:
            continue
        if r.get("league_key") == season.key and r.get("status") == "published":
            out.append(r)
    out.sort(key=lambda r: -r["week"])
    return {"week": season.completed_week, "recaps": out}


# =============================================================================
# entry point
# =============================================================================

def run(conn: sqlite3.Connection, league_key: str, out_dir: Path = OUT,
        now: Optional[datetime] = None) -> Dict[str, int]:
    rules = read_toml(LEAGUE_TOML)
    race_cfg = read_toml(RACES_TOML)
    season = Season(conn, league_key)
    built_at = (now or datetime.now(timezone.utc)).isoformat()

    tw = team_weeks(season)
    baseline = draft_baseline(conn, bin_size=season.n)
    totals, splits, jockeys, split_jockeys = race_totals(season, tw, baseline)
    ledger = draft_ledger(season, baseline, season.completed_week)
    by_value = sorted(ledger, key=lambda e: -e["value_over_slot"])
    team_vos = defaultdict(float)
    for e in ledger:
        if e["current_team_index"] is not None:
            team_vos[e["current_team_index"]] += e["value_over_slot"]

    payloads = {
        "league": league_json(season, rules, assign_silks(season), built_at),
        "standings": standings(season, tw, rules),
        "races": races(season, totals, splits, race_cfg, jockeys, split_jockeys),
        "season": weeks(season, tw, rules.get("purse", {})),
        "draft": {
            "week": season.completed_week, "weeks_elapsed": len(season.completed),
            "baseline": {
                "method": ("median season points by region of the draft board across %d prior "
                           "Life's Gr8 drafts (%s), forced non-increasing, interpolated between "
                           "regions, scaled by weeks played out of %d"
                           % (len(baseline["seasons"]), ", ".join(map(str, baseline["seasons"])), NFL_GAMES)),
                "global": [[round(x, 1), round(y, 2)] for x, y in baseline["global"]],
                "by_position": {g: [[round(x, 1), round(y, 2)] for x, y in k]
                                for g, k in baseline["by_group"].items()},
                "seasons": baseline["seasons"], "historical_picks": baseline["picks"],
                "undrafted_slot": max((e["pick_number"] or 0 for e in ledger), default=0) + 1,
            },
            "steals": by_value[:25], "busts": list(reversed(by_value[-25:])),
            "team_totals": [{"team_index": i, "value_over_slot": round(team_vos[i], 2)}
                            for i in range(season.n)],
            "all": ledger,
        },
        "blotter": blotter(season),
        "leaders": leaders(season),
        "players": players_json(season),
        "recaps": recaps_json(season, DATA / "recaps"),
        "newage": newage.build(
            conn, league_key, season.completed_week, read_toml(ROOT / "stats.toml"),
            team_of=season.on_team,
            name_of={k: (p["name"], p["position"], p["nfl_team"]) for k, p in season.players.items()},
            short_name=short_name) or {"week": season.completed_week, "headline": None, "boards": {}},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, payload in payloads.items():
        text = json.dumps(payload, separators=(",", ":"))
        (out_dir / ("%s.json" % name)).write_text(text)
        sizes[name] = len(text)
    return sizes
