"""
nflverse files -> SQLite, joined to Yahoo players.

Pure transformation of the latest downloaded copy of each file. Only the columns
the four headline stats need are kept, and only regular season plays.

Matching a Yahoo player to an NFL GSIS id tries the DynastyProcess id map first,
then falls back to normalized name plus position, and team when a name is shared.
The method used is stored with every match so a bad one can be traced.
"""

from __future__ import annotations

import csv
import gzip
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from ..ingest.nflverse import latest_local

SCHEMA = """
CREATE TABLE IF NOT EXISTS nfl_plays (
  game_id TEXT, play_id TEXT, week INTEGER, posteam TEXT, play_type TEXT,
  pass_attempt INTEGER, rush_attempt INTEGER, receiver_id TEXT, rusher_id TEXT,
  yardline_100 REAL, yards_gained REAL, qb_kneel INTEGER, qb_scramble INTEGER,
  two_point INTEGER, sack INTEGER, read_thrown TEXT, is_motion INTEGER,
  PRIMARY KEY (game_id, play_id));
CREATE TABLE IF NOT EXISTS nfl_ep_weekly (
  week INTEGER, gsis_id TEXT, posteam TEXT, position TEXT, stats_json TEXT,
  PRIMARY KEY (week, gsis_id, posteam));
CREATE TABLE IF NOT EXISTS yahoo_gsis (
  player_key TEXT PRIMARY KEY, gsis_id TEXT, method TEXT);
CREATE TABLE IF NOT EXISTS nflverse_files (source TEXT PRIMARY KEY, file TEXT);
"""

EP_COLUMNS = [
    "pass_attempt", "pass_completions", "pass_completions_exp", "pass_yards_gained",
    "pass_yards_gained_exp", "pass_touchdown", "pass_touchdown_exp", "pass_interception",
    "pass_interception_exp", "pass_two_point_conv", "pass_two_point_conv_exp",
    "rush_attempt", "rush_yards_gained", "rush_yards_gained_exp", "rush_touchdown",
    "rush_touchdown_exp", "rush_two_point_conv", "rush_two_point_conv_exp",
    "rec_attempt", "receptions", "receptions_exp", "rec_yards_gained", "rec_yards_gained_exp",
    "rec_touchdown", "rec_touchdown_exp", "rec_two_point_conv", "rec_two_point_conv_exp",
]

# Yahoo and nflverse disagree on a few franchise codes.
TEAM_ALIASES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LA"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
OFFENSE = {"QB", "RB", "WR", "TE", "FB"}


def norm_name(name: Optional[str]) -> str:
    words = re.sub(r"[^a-z ]", "", (name or "").lower().replace("-", " ")).split()
    return "".join(w for w in words if w not in SUFFIXES)


def _rows(path: Optional[Path]) -> Iterator[Dict[str, str]]:
    if path is None:
        return iter(())
    handle = gzip.open(path, "rt", newline="") if path.name.endswith(".gz") else open(path, newline="")
    return csv.DictReader(handle)


def _i(v: str) -> int:
    return 1 if v in ("1", "TRUE", "true", "1.0") else 0


def _f(v: str) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _blank(v: Optional[str]) -> Optional[str]:
    return None if v in (None, "", "NA") else v


def load(conn: sqlite3.Connection, raw_players: List[sqlite3.Row]) -> Dict[str, int]:
    conn.executescript(SCHEMA)
    for t in ("nfl_plays", "nfl_ep_weekly", "yahoo_gsis", "nflverse_files"):
        conn.execute("DELETE FROM %s" % t)
    files = {s: latest_local(s) for s in ("pbp", "ftn", "ep_weekly", "players", "ids")}
    for s, p in files.items():
        conn.execute("INSERT INTO nflverse_files VALUES (?,?)", (s, p.name if p else None))

    ftn = {}
    for r in _rows(files["ftn"]):
        ftn[(r["nflverse_game_id"], r["nflverse_play_id"].split(".")[0])] = (
            r.get("read_thrown"), _i(r.get("is_motion", "")))

    plays = 0
    for r in _rows(files["pbp"]):
        if r.get("season_type") != "REG" or r.get("play_type") not in ("pass", "run"):
            continue
        read, motion = ftn.get((r["game_id"], r["play_id"]), (None, None))
        conn.execute("INSERT OR REPLACE INTO nfl_plays VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["game_id"], r["play_id"], int(r["week"]), r["posteam"], r["play_type"],
            _i(r["pass_attempt"]), _i(r["rush_attempt"]), _blank(r["receiver_player_id"]),
            _blank(r["rusher_player_id"]), _f(r["yardline_100"]), _f(r["yards_gained"]),
            _i(r["qb_kneel"]), _i(r["qb_scramble"]), _i(r["two_point_attempt"]), _i(r["sack"]),
            read, motion))
        plays += 1

    import json
    ep = 0
    for r in _rows(files["ep_weekly"]):
        conn.execute("INSERT OR REPLACE INTO nfl_ep_weekly VALUES (?,?,?,?,?)", (
            int(r["week"]), r["player_id"], r["posteam"], r["position"],
            json.dumps({c: (_f(r.get(c)) or 0.0) for c in EP_COLUMNS})))
        ep += 1

    # --- identity -------------------------------------------------------------------
    by_yahoo = {}
    for r in _rows(files["ids"]):
        if _blank(r.get("yahoo_id")) and _blank(r.get("gsis_id")):
            by_yahoo[r["yahoo_id"].split(".")[0]] = r["gsis_id"]
    by_name: Dict[str, List[Dict[str, str]]] = {}
    for r in _rows(files["players"]):
        if _blank(r.get("gsis_id")) and r.get("position") in OFFENSE:
            by_name.setdefault(norm_name(r.get("display_name")), []).append(r)

    counts = {"yahoo_id": 0, "name": 0, "unmatched": 0}
    for p in raw_players:
        if p["position_type"] != "O":
            continue
        yahoo_id = p["player_key"].split(".p.")[-1]
        gsis, method = by_yahoo.get(yahoo_id), "yahoo_id"
        if not gsis:
            method = "name"
            positions = set((p["position"] or "").split(","))
            cands = [c for c in by_name.get(norm_name(p["name"]), []) if c.get("position") in positions]
            if len(cands) > 1:
                team = TEAM_ALIASES.get(p["nfl_team"], p["nfl_team"])
                cands = [c for c in cands if c.get("latest_team") == team]
            gsis = cands[0]["gsis_id"] if len(cands) == 1 else None
        if gsis:
            conn.execute("INSERT OR REPLACE INTO yahoo_gsis VALUES (?,?,?)", (p["player_key"], gsis, method))
            counts[method] += 1
        else:
            counts["unmatched"] += 1
    return {"plays": plays, "ep_rows": ep, **counts}
