"""
The weekly new-age stat headline: four modern metrics from nflverse data.

Every number is computed here, over every qualifying NFL player, so a Life's Gr8
player's rank is a real rank and not a rank among whoever happens to be rostered.
The headline sentence is assembled from finished values; nothing interprets them.

stats.toml defines the stats, their minimums, and the rotation order.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

READ_CODES = ("1", "2", "CHK", "DES", "SD")   # "0" means no read was charted

# The sample behind each rate, used to order players who share a value.
VOLUME_KEY = {"poe": "expected_per_game", "first_read": "charted_targets",
              "explosive_run": "carries", "rz_share": "opportunities"}

# ffopportunity component -> Yahoo stat id whose league modifier prices it.
COMPONENTS = [
    ("pass_completions", "pass_completions_exp", "2"),
    ("pass_yards_gained", "pass_yards_gained_exp", "4"),
    ("pass_touchdown", "pass_touchdown_exp", "5"),
    ("pass_interception", "pass_interception_exp", "6"),
    ("rush_attempt", "rush_attempt", "8"),          # volume is the opportunity itself
    ("rush_yards_gained", "rush_yards_gained_exp", "9"),
    ("rush_touchdown", "rush_touchdown_exp", "10"),
    ("receptions", "receptions_exp", "11"),
    ("rec_yards_gained", "rec_yards_gained_exp", "12"),
    ("rec_touchdown", "rec_touchdown_exp", "13"),
]
TWO_POINT = [("pass_two_point_conv", "pass_two_point_conv_exp"),
             ("rush_two_point_conv", "rush_two_point_conv_exp"),
             ("rec_two_point_conv", "rec_two_point_conv_exp")]


def league_points(row: Dict[str, float], mods: Dict[str, float], expected: bool) -> float:
    """
    One player-game scored with Life's Gr8's modifiers, from actual or expected
    components. Incompletions are attempts minus (expected) completions. Yardage
    bonuses are left out of both sides: the model has no expected distribution of
    yards to price a threshold against.
    """
    i = 1 if expected else 0
    total = sum(row.get(pair[i], 0.0) * mods.get(sid, 0.0) for *pair, sid in COMPONENTS)
    completions = row.get("pass_completions_exp" if expected else "pass_completions", 0.0)
    total += (row.get("pass_attempt", 0.0) - completions) * mods.get("3", 0.0)
    total += sum(row.get(pair[i], 0.0) for pair in TWO_POINT) * mods.get("16", 0.0)
    return total


def points_over_expected(conn: sqlite3.Connection, week: int, mods: Dict[str, float],
                         min_exp_per_game: float) -> List[Dict[str, Any]]:
    games: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for r in conn.execute("SELECT gsis_id, stats_json FROM nfl_ep_weekly WHERE week <= ?", (week,)):
        row = json.loads(r["stats_json"])
        games[r["gsis_id"]].append((league_points(row, mods, False), league_points(row, mods, True)))
    out = []
    for gsis, g in games.items():
        n = len(g)
        actual, exp = sum(a for a, _ in g), sum(e for _, e in g)
        if exp / n < min_exp_per_game:
            continue
        out.append({"gsis_id": gsis, "value": (actual - exp) / n, "games": n,
                    "detail": {"actual_per_game": round(actual / n, 2), "expected_per_game": round(exp / n, 2)}})
    return out


def first_read_share(conn: sqlite3.Connection, week: int, min_targets: int) -> List[Dict[str, Any]]:
    marks = ",".join("?" * len(READ_CODES))
    rows = conn.execute("""
        SELECT receiver_id, COUNT(*) AS targets, SUM(read_thrown = '1') AS first
        FROM nfl_plays
        WHERE week <= ? AND pass_attempt = 1 AND receiver_id IS NOT NULL AND two_point = 0
          AND read_thrown IN (%s)
        GROUP BY receiver_id""" % marks, (week,) + READ_CODES)
    return [{"gsis_id": r["receiver_id"], "value": r["first"] / r["targets"],
             "detail": {"first_read_targets": r["first"], "charted_targets": r["targets"]}}
            for r in rows if r["targets"] >= min_targets]


def explosive_run_rate(conn: sqlite3.Connection, week: int, min_carries: int,
                       explosive_yards: float) -> List[Dict[str, Any]]:
    rows = conn.execute("""
        SELECT rusher_id, COUNT(*) AS carries, SUM(yards_gained >= ?) AS explosive
        FROM nfl_plays
        WHERE week <= ? AND rush_attempt = 1 AND play_type = 'run' AND rusher_id IS NOT NULL
          AND qb_kneel = 0 AND qb_scramble = 0 AND two_point = 0
        GROUP BY rusher_id""", (explosive_yards, week))
    return [{"gsis_id": r["rusher_id"], "value": r["explosive"] / r["carries"],
             "detail": {"explosive_runs": r["explosive"], "carries": r["carries"]}}
            for r in rows if r["carries"] >= min_carries]


def red_zone_share(conn: sqlite3.Connection, week: int, min_opps: int) -> List[Dict[str, Any]]:
    player: Dict[Tuple[str, str], int] = defaultdict(int)
    team: Dict[str, int] = defaultdict(int)
    for r in conn.execute("""
        SELECT posteam, receiver_id, rusher_id, pass_attempt, rush_attempt, play_type, qb_scramble
        FROM nfl_plays
        WHERE week <= ? AND yardline_100 <= 20 AND two_point = 0 AND qb_kneel = 0 AND sack = 0""", (week,)):
        who = None
        if r["pass_attempt"] and r["receiver_id"]:
            who = r["receiver_id"]
        elif r["rush_attempt"] and r["play_type"] == "run" and not r["qb_scramble"] and r["rusher_id"]:
            who = r["rusher_id"]
        if who:
            player[(who, r["posteam"])] += 1
            team[r["posteam"]] += 1
    best: Dict[str, Dict[str, Any]] = {}
    for (gsis, posteam), n in player.items():
        if n < min_opps or not team[posteam]:
            continue
        entry = {"gsis_id": gsis, "value": n / team[posteam],
                 "detail": {"opportunities": n, "team_opportunities": team[posteam], "team": posteam}}
        if gsis not in best or entry["detail"]["opportunities"] > best[gsis]["detail"]["opportunities"]:
            best[gsis] = entry   # a traded player is judged on his larger role
    return list(best.values())


def build(conn: sqlite3.Connection, league_key: str, week: int, cfg: Dict[str, Any],
          team_of: Dict[str, int], name_of: Dict[str, Tuple[str, str, str]],
          short_name) -> Optional[Dict[str, Any]]:
    """
    team_of: yahoo player_key -> team_index (current rosters)
    name_of: yahoo player_key -> (full name, position, nfl team)
    """
    stats = cfg.get("stat", [])
    if not stats or week < 1 or not conn.execute("SELECT 1 FROM nfl_plays LIMIT 1").fetchone():
        return None
    data_week = conn.execute("SELECT MAX(week) FROM nfl_plays").fetchone()[0] or 0
    through = min(week, data_week)
    mods = {r["stat_id"]: r["modifier"] for r in conn.execute(
        "SELECT stat_id, modifier FROM stat_categories WHERE league_key=? AND modifier IS NOT NULL", (league_key,))}
    gsis_to_yahoo: Dict[str, str] = {}
    for r in conn.execute("SELECT player_key, gsis_id FROM yahoo_gsis"):
        if r["player_key"] in team_of:
            gsis_to_yahoo[r["gsis_id"]] = r["player_key"]

    boards = {}
    for sc in stats:
        sid = sc["id"]
        if sid == "poe":
            rows = points_over_expected(conn, through, mods, float(sc.get("min_expected_per_game", 6)))
        elif sid == "first_read":
            rows = first_read_share(conn, through, int(sc.get("min_targets_per_week", 4)) * through)
        elif sid == "explosive_run":
            rows = explosive_run_rate(conn, through, int(sc.get("min_carries_per_week", 8)) * through,
                                      float(sc.get("explosive_yards", 15)))
        elif sid == "rz_share":
            rows = red_zone_share(conn, through, int(sc.get("min_opportunities_per_week", 2)) * through)
        else:
            continue
        volume = VOLUME_KEY[sid]
        rows.sort(key=lambda r: (-round(r["value"], 4), -r["detail"].get(volume, 0)))
        qualifiers = len(rows)
        values = [round(r["value"], 4) for r in rows]
        league_rows = []
        for r in rows:
            v = round(r["value"], 4)
            rank = 1 + sum(1 for x in values if x > v)      # competition ranking: ties share
            tied = values.count(v) > 1
            key = gsis_to_yahoo.get(r["gsis_id"])
            if key is None:
                continue
            full, position, nfl_team = name_of[key]
            league_rows.append({
                "player_key": key, "name": full, "short": short_name(full), "position": position,
                "nfl_team": nfl_team, "team_index": team_of[key],
                "value": round(r["value"], 4), "nfl_rank": rank, "tied": tied, "detail": r["detail"],
            })
        boards[sid] = {
            "id": sid, "name": sc["name"], "short": sc["short"], "unit": sc["unit"],
            "format": sc.get("format", "number"), "blurb": sc["blurb"], "how": sc["how"],
            "source": sc["source"], "qualifiers": qualifiers,
            "leaders": league_rows[:10],
            "trailers": list(reversed(league_rows[-5:])) if len(league_rows) > 10 else [],
            "rostered_qualifiers": len(league_rows),
        }

    order = [s["id"] for s in stats if s["id"] in boards]
    if not order:
        return None
    # The week decides whose turn it is. If that stat has nobody qualified yet,
    # usually because a data file lags a day, the next stat in line takes the week
    # rather than the site running an empty headline.
    start = (week - 1) % len(order)
    ready = [order[(start + i) % len(order)] for i in range(len(order))
             if boards[order[(start + i) % len(order)]]["leaders"]]
    if not ready:
        return None
    headline_id = ready[0]
    h = boards[headline_id]
    lead = h["leaders"][0] if h["leaders"] else None
    return {
        "week": week, "data_through_week": through, "rotation": order,
        "headline": headline_id, "scheduled": order[start],
        "headline_fact": _fact(h, lead) if lead else None,
        "boards": boards,
    }


def _fmt(value: float, fmt: str) -> str:
    if fmt == "percent":
        return "%d%%" % round(value * 100)
    if fmt == "signed":
        return ("+" if value > 0 else "") + ("%.1f" % value)
    return "%.1f" % value


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


def _fact(board: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "player_key": lead["player_key"], "team_index": lead["team_index"],
        "value": lead["value"], "display": _fmt(lead["value"], board["format"]),
        "sentence": "%s leads Life's Gr8 at %s %s, %s%s among %d qualifying NFL players." % (
            lead["name"], _fmt(lead["value"], board["format"]), board["unit"],
            "tied for " if lead.get("tied") else "", _ordinal(lead["nfl_rank"]), board["qualifiers"]),
    }
