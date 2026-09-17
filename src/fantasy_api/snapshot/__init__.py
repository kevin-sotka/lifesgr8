"""
raw JSON -> SQLite.

Pure transformation: no network, no clock, no randomness. Rebuilding from the
same files gives the same database, so the whole layer is idempotent: each run
drops a season's rows and reloads them from every raw file on disk, oldest first,
so the latest copy of any resource wins (which is how stat corrections land).

Scores are computed here from raw stats and the league's own stat modifiers.
Yahoo's own per-player total is stored beside it, and verify() compares the two.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..config import DATA, RAW
from ..yahoo_shapes import items, merge, num

DB_PATH = DATA / "league.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues (
  league_key TEXT PRIMARY KEY, season INTEGER, name TEXT, num_teams INTEGER,
  current_week INTEGER, start_date TEXT, end_week INTEGER, settings_json TEXT);
CREATE TABLE IF NOT EXISTS stat_categories (
  league_key TEXT, stat_id TEXT, name TEXT, display_name TEXT, position_type TEXT,
  modifier REAL, PRIMARY KEY (league_key, stat_id));
CREATE TABLE IF NOT EXISTS roster_slots (
  league_key TEXT, position TEXT, count INTEGER, ordinal INTEGER,
  PRIMARY KEY (league_key, position));
CREATE TABLE IF NOT EXISTS teams (
  team_key TEXT PRIMARY KEY, league_key TEXT, team_id INTEGER, name TEXT,
  manager_name TEXT, is_mine INTEGER);
CREATE TABLE IF NOT EXISTS players (
  player_key TEXT PRIMARY KEY, name TEXT, position TEXT, position_type TEXT,
  eligible_positions TEXT, nfl_team TEXT, bye INTEGER);
CREATE TABLE IF NOT EXISTS draft_picks (
  league_key TEXT, pick_number INTEGER, round INTEGER, team_key TEXT, player_key TEXT,
  cost INTEGER, PRIMARY KEY (league_key, pick_number));
CREATE TABLE IF NOT EXISTS rosters (
  league_key TEXT, week INTEGER, team_key TEXT, player_key TEXT,
  selected_position TEXT, is_starter INTEGER,
  PRIMARY KEY (league_key, week, team_key, player_key));
CREATE TABLE IF NOT EXISTS player_week_stats (
  league_key TEXT, week INTEGER, player_key TEXT, stats_json TEXT,
  points REAL, yahoo_points REAL, PRIMARY KEY (league_key, week, player_key));
CREATE TABLE IF NOT EXISTS matchups (
  league_key TEXT, week INTEGER, status TEXT, is_playoffs INTEGER,
  team_a_key TEXT, team_b_key TEXT, points_a REAL, points_b REAL,
  week_start TEXT, week_end TEXT,
  PRIMARY KEY (league_key, week, team_a_key));
CREATE TABLE IF NOT EXISTS draft_history (
  league_key TEXT, season INTEGER, pick_number INTEGER, round INTEGER, player_key TEXT,
  position TEXT, position_type TEXT, season_points REAL,
  PRIMARY KEY (league_key, pick_number));
CREATE TABLE IF NOT EXISTS transactions (
  league_key TEXT, transaction_key TEXT PRIMARY KEY, type TEXT, status TEXT,
  timestamp INTEGER, payload_json TEXT);
"""

NON_STARTER_SLOTS = {"BN", "IR", "IR+", "NA"}
_STAMP = re.compile(r"(\d{8}T\d{6}\d*Z)\.json$")


# Bump when SCHEMA changes. The database is derived entirely from data/raw, so an
# old one is dropped and rebuilt rather than migrated.
SCHEMA_VERSION = 3
TABLES = ("leagues", "stat_categories", "roster_slots", "teams", "players", "draft_picks",
          "rosters", "player_week_stats", "matchups", "transactions", "draft_history")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # The whole database is under a megabyte. Sorting in memory avoids temp files
    # outside the project, which a sandboxed or read-only runner may not allow.
    conn.execute("PRAGMA temp_store = MEMORY")
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        with conn:
            for table in TABLES:
                conn.execute("DROP TABLE IF EXISTS %s" % table)
        conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    conn.executescript(SCHEMA)
    return conn


# --- reading raw files ----------------------------------------------------------

def _folder(raw: Path, path: str) -> Path:
    return raw / re.sub(r"[^A-Za-z0-9._=-]+", "_", path).strip("_")[:160]


def _files(folder: Path) -> List[Path]:
    """Oldest first, so later snapshots overwrite earlier ones."""
    return sorted(folder.glob("*.json"), key=lambda p: (_STAMP.search(p.name) or p).__str__())


def _load_all(raw: Path, path: str) -> Iterator[Any]:
    for f in _files(_folder(raw, path)):
        try:
            yield json.loads(f.read_text())
        except ValueError:
            continue  # an unparseable snapshot is kept on disk, just not loaded


def _latest(raw: Path, path: str) -> Optional[Any]:
    latest = None
    for doc in _load_all(raw, path):
        latest = doc
    return latest


def _league_part(doc: Any, index: int) -> Any:
    league = (doc or {}).get("fantasy_content", {}).get("league", [])
    return league[index] if len(league) > index else {}


# --- parsers ----------------------------------------------------------------------

def parse_player(fragments: Any) -> Dict[str, Any]:
    p = merge(fragments)
    bye = p.get("bye_weeks", {})
    return {
        "player_key": p.get("player_key"),
        "name": (p.get("name") or {}).get("full") or p.get("name"),
        "position": p.get("display_position") or p.get("primary_position"),
        "position_type": p.get("position_type"),
        "eligible_positions": [e.get("position") for e in p.get("eligible_positions", [])
                               if isinstance(e, dict)],
        "nfl_team": (p.get("editorial_team_abbr") or "").upper(),
        "bye": int(num(bye.get("week"), 0)) if isinstance(bye, dict) else 0,
    }


Bonuses = Dict[str, List[Tuple[float, float]]]


def score(stats: Dict[str, float], modifiers: Dict[str, float],
          bonuses: Optional[Bonuses] = None) -> float:
    """
    The league's real modifiers, applied to raw stats. Never a generic value.

    Bonuses stack: Life's Gr8 pays +1 at 75 rushing yards and +4 more at 125, so
    a 144-yard game collects both. Verified against Yahoo's own totals, not assumed.
    """
    total = sum(v * modifiers[sid] for sid, v in stats.items() if sid in modifiers)
    for sid, tiers in (bonuses or {}).items():
        value = stats.get(sid, 0.0)
        total += sum(points for target, points in tiers if value >= target)
    return round(total, 2)


def _load_settings(conn: sqlite3.Connection, raw: Path, league_key: str):
    doc = _latest(raw, "league/%s/settings" % league_key)
    if doc is None:
        raise FileNotFoundError("No settings snapshot for %s. Run ingest first." % league_key)
    meta = merge(_league_part(doc, 0))
    settings = _league_part(doc, 1).get("settings", [{}])[0]
    conn.execute("INSERT OR REPLACE INTO leagues VALUES (?,?,?,?,?,?,?,?)", (
        league_key, int(num(meta.get("season"))), meta.get("name"), int(num(meta.get("num_teams"))),
        int(num(meta.get("current_week"))), meta.get("start_date"), int(num(meta.get("end_week"))),
        json.dumps({k: v for k, v in settings.items()
                    if k not in ("stat_categories", "stat_modifiers", "roster_positions")})))

    modifiers = {str(m["stat"]["stat_id"]): num(m["stat"]["value"])
                 for m in settings.get("stat_modifiers", {}).get("stats", [])}
    bonuses: Bonuses = {}
    for m in settings.get("stat_modifiers", {}).get("stats", []):
        tiers = [(num(b["bonus"]["target"]), num(b["bonus"]["points"]))
                 for b in m["stat"].get("bonuses", []) if "bonus" in b]
        if tiers:
            bonuses[str(m["stat"]["stat_id"])] = tiers
    for c in settings.get("stat_categories", {}).get("stats", []):
        s = c["stat"]
        conn.execute("INSERT OR REPLACE INTO stat_categories VALUES (?,?,?,?,?,?)", (
            league_key, str(s["stat_id"]), s.get("name"), s.get("display_name"),
            s.get("position_type"), modifiers.get(str(s["stat_id"]))))
    for n, r in enumerate(settings.get("roster_positions", [])):
        rp = r["roster_position"]
        conn.execute("INSERT OR REPLACE INTO roster_slots VALUES (?,?,?,?)",
                     (league_key, rp["position"], int(num(rp["count"])), n))
    return modifiers, bonuses


def _load_teams(conn, raw, league_key):
    doc = _latest(raw, "league/%s/teams" % league_key)
    for entry in items(_league_part(doc, 1).get("teams", {})):
        t = merge(entry["team"])
        managers = [m.get("manager", {}) for m in t.get("managers", [])]
        conn.execute("INSERT OR REPLACE INTO teams VALUES (?,?,?,?,?,?)", (
            t["team_key"], league_key, int(num(t.get("team_id"))), t.get("name"),
            " & ".join(m.get("nickname", "") for m in managers if m.get("nickname")),
            1 if str(t.get("is_owned_by_current_login")) == "1" else 0))


def _upsert_player(conn, p):
    if not p.get("player_key"):
        return
    conn.execute("""INSERT INTO players VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(player_key) DO UPDATE SET
          name=COALESCE(excluded.name, name), position=COALESCE(excluded.position, position),
          position_type=COALESCE(excluded.position_type, position_type),
          eligible_positions=CASE WHEN excluded.eligible_positions='[]'
                                  THEN eligible_positions ELSE excluded.eligible_positions END,
          nfl_team=CASE WHEN excluded.nfl_team='' THEN nfl_team ELSE excluded.nfl_team END,
          bye=CASE WHEN excluded.bye=0 THEN bye ELSE excluded.bye END""", (
        p["player_key"], p["name"], p["position"], p["position_type"],
        json.dumps(p["eligible_positions"]), p["nfl_team"], p["bye"]))


def _load_draft(conn, raw, league_key):
    doc = _latest(raw, "league/%s/draftresults" % league_key)
    for d in items(_league_part(doc, 1).get("draft_results", {})):
        r = d.get("draft_result", {})
        if r.get("player_key"):
            conn.execute("INSERT OR REPLACE INTO draft_picks VALUES (?,?,?,?,?,?)", (
                league_key, int(r["pick"]), int(r["round"]), r["team_key"], r["player_key"],
                int(r["cost"]) if r.get("cost") not in (None, "") else None))


def _weeks_on_disk(raw: Path, prefix: str) -> List[int]:
    weeks = set()
    for folder in raw.glob(prefix + "*"):
        m = re.search(r"week[=_](\d+)$", folder.name)
        if m:
            weeks.add(int(m.group(1)))
    return sorted(weeks)


def _load_rosters(conn, raw, league_key):
    team_keys = [r["team_key"] for r in conn.execute(
        "SELECT team_key FROM teams WHERE league_key=?", (league_key,))]
    for team_key in team_keys:
        for week in _weeks_on_disk(raw, _folder(raw, "team/%s/roster;week=" % team_key).name):
            doc = _latest(raw, "team/%s/roster;week=%d" % (team_key, week))
            team = (doc or {}).get("fantasy_content", {}).get("team", [])
            if len(team) < 2:
                continue
            conn.execute("DELETE FROM rosters WHERE league_key=? AND week=? AND team_key=?",
                         (league_key, week, team_key))
            for entry in items(team[1]["roster"]["0"]["players"]):
                parts = entry["player"]
                player = parse_player(parts[0])
                slot = merge(merge(parts[1:]).get("selected_position", [])).get("position", "BN")
                _upsert_player(conn, player)
                conn.execute("INSERT OR REPLACE INTO rosters VALUES (?,?,?,?,?,?)", (
                    league_key, week, team_key, player["player_key"], slot,
                    0 if slot in NON_STARTER_SLOTS else 1))


def _load_stats(conn, raw, league_key, modifiers, bonuses):
    prefix = _folder(raw, "league/%s/player_stats/week_" % league_key).name
    for week in _weeks_on_disk(raw, prefix):
        for doc in _load_all(raw, "league/%s/player_stats/week_%d" % (league_key, week)):
            for entry in items(_league_part(doc, 1).get("players", {})):
                parts = entry["player"]
                player = parse_player(parts[0])
                extra = merge(parts[1:])
                stats = {str(s["stat"]["stat_id"]): num(s["stat"]["value"])
                         for s in extra.get("player_stats", {}).get("stats", [])}
                yahoo = extra.get("player_points", {}).get("total")
                _upsert_player(conn, player)
                conn.execute("INSERT OR REPLACE INTO player_week_stats VALUES (?,?,?,?,?,?)", (
                    league_key, week, player["player_key"],
                    json.dumps({k: v for k, v in stats.items() if v}),
                    score(stats, modifiers, bonuses), num(yahoo) if yahoo is not None else None))


def _load_matchups(conn, raw, league_key):
    prefix = _folder(raw, "league/%s/scoreboard;week=" % league_key).name
    for week in _weeks_on_disk(raw, prefix):
        doc = _latest(raw, "league/%s/scoreboard;week=%d" % (league_key, week))
        board = _league_part(doc, 1).get("scoreboard", {}).get("0", {}).get("matchups", {})
        conn.execute("DELETE FROM matchups WHERE league_key=? AND week=?", (league_key, week))
        for m in items(board):
            mt = m["matchup"]
            sides = []
            for t in items(mt["0"]["teams"]):
                info = merge(t["team"][0])
                pts = merge(t["team"][1:]).get("team_points", {}).get("total")
                sides.append((info["team_key"], num(pts)))
            if len(sides) == 2:
                conn.execute("INSERT OR REPLACE INTO matchups VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    league_key, week, mt.get("status"), int(num(mt.get("is_playoffs"))),
                    sides[0][0], sides[1][0], sides[0][1], sides[1][1],
                    mt.get("week_start"), mt.get("week_end")))


def _transaction_rows(doc: Any) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for entry in items(_league_part(doc, 1).get("transactions", {})):
        parts = entry.get("transaction", [])
        head = merge(parts[:1])
        moves = []
        for p in items(merge(parts[1:]).get("players", {})):
            player = parse_player(p["player"][0])
            data = merge(p["player"][1:]).get("transaction_data", {})
            data = data[0] if isinstance(data, list) else data
            moves.append({"player": player, "move": data})
        head["moves"] = moves
        yield head.get("transaction_key"), head


def _load_transactions(conn, raw, league_key):
    paths = ["league/%s/transactions" % league_key]
    paths += [f.name for f in raw.glob(_folder(raw, "league/%s/transactions;types=" % league_key).name + "*")]
    for path in paths:
        folder = raw / path if (raw / path).is_dir() else _folder(raw, path)
        docs = [json.loads(f.read_text()) for f in _files(folder)]
        if not docs:
            continue
        for key, t in _transaction_rows(docs[-1]):
            if not key:
                continue
            for mv in t["moves"]:
                _upsert_player(conn, mv["player"])
            conn.execute("INSERT OR REPLACE INTO transactions VALUES (?,?,?,?,?,?)", (
                league_key, key, t.get("type"), t.get("status"),
                int(num(t.get("timestamp"))), json.dumps(t)))


def _load_history(conn, raw, current_league_key):
    """
    Prior seasons' drafts joined to what each pick scored that season, in that
    season's own league scoring. This is the draft value baseline's only input.
    """
    conn.execute("DELETE FROM draft_history")
    for folder in raw.glob("league_*_player_stats_season"):
        league_key = folder.name[len("league_"):-len("_player_stats_season")]
        if league_key == current_league_key:
            continue
        season_points: Dict[str, float] = {}
        positions: Dict[str, Tuple[str, str]] = {}
        for doc in _load_all(raw, "league/%s/player_stats/season" % league_key):
            for entry in items(_league_part(doc, 1).get("players", {})):
                player = parse_player(entry["player"][0])
                total = merge(entry["player"][1:]).get("player_points", {}).get("total")
                season_points[player["player_key"]] = num(total)
                positions[player["player_key"]] = (player["position"], player["position_type"])
        draft = _latest(raw, "league/%s/draftresults" % league_key)
        meta = merge(_league_part(_latest(raw, "league/%s/settings" % league_key), 0))
        for d in items(_league_part(draft, 1).get("draft_results", {})):
            r = d.get("draft_result", {})
            key = r.get("player_key")
            if not key or key not in season_points:
                continue
            pos, ptype = positions.get(key, (None, None))
            conn.execute("INSERT OR REPLACE INTO draft_history VALUES (?,?,?,?,?,?,?,?)", (
                league_key, int(num(meta.get("season"))), int(r["pick"]), int(r["round"]), key,
                pos, ptype, season_points[key]))


def build(league_key: str, raw: Path = RAW, db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = connect(db_path)
    with conn:
        for table in ("stat_categories", "roster_slots", "teams", "draft_picks", "rosters",
                      "player_week_stats", "matchups", "transactions"):
            conn.execute("DELETE FROM %s WHERE league_key=?" % table, (league_key,))
        modifiers, bonuses = _load_settings(conn, raw, league_key)
        _load_teams(conn, raw, league_key)
        _load_draft(conn, raw, league_key)
        _load_rosters(conn, raw, league_key)
        _load_stats(conn, raw, league_key, modifiers, bonuses)
        _load_matchups(conn, raw, league_key)
        _load_transactions(conn, raw, league_key)
        _load_history(conn, raw, league_key)
        # nflverse feeds only the weekly stat headline. With no files on disk the
        # tables load empty and the headline sits out; nothing else depends on it.
        from . import nflverse
        nflverse.load(conn, list(conn.execute("SELECT * FROM players")))
    return conn


def verify(conn: sqlite3.Connection, league_key: str,
           tolerance: float = 0.011) -> Dict[int, List[str]]:
    """
    Every computed player score must match Yahoo's own figure, and every completed
    team score must equal the sum of its starters. Returns problems by week.

    A mismatch almost always means the scoring settings were read wrong, which is
    how the stacking yardage bonuses were found. The one legitimate exception is
    the NFL stat correction window: for a few days after a week ends Yahoo can
    show corrected stats beside an uncorrected point total. The caller decides
    how much a week's age excuses; this function only reports.
    """
    problems: Dict[int, List[str]] = {}
    for r in conn.execute("""SELECT s.week, s.player_key, p.name, s.points, s.yahoo_points
                             FROM player_week_stats s LEFT JOIN players p USING (player_key)
                             WHERE s.league_key=? AND s.yahoo_points IS NOT NULL
                               AND ABS(s.points - s.yahoo_points) > ?""", (league_key, tolerance)):
        problems.setdefault(r["week"], []).append(
            "%s (%s): computed %.2f, Yahoo %.2f"
            % (r["name"], r["player_key"], r["points"], r["yahoo_points"]))
    for r in conn.execute("""
        SELECT m.week, t.team_key, t.pts AS yahoo, (
          SELECT ROUND(SUM(s.points), 2) FROM rosters r JOIN player_week_stats s
            ON s.league_key=r.league_key AND s.week=r.week AND s.player_key=r.player_key
          WHERE r.league_key=m.league_key AND r.week=m.week AND r.team_key=t.team_key
            AND r.is_starter=1) AS computed
        FROM matchups m JOIN (
          SELECT league_key, week, team_a_key AS team_key, points_a AS pts FROM matchups
          UNION ALL SELECT league_key, week, team_b_key, points_b FROM matchups) t
          ON t.league_key=m.league_key AND t.week=m.week
             AND t.team_key IN (m.team_a_key, m.team_b_key)
        WHERE m.league_key=? AND m.status='postevent'
        GROUP BY m.week, t.team_key""", (league_key,)):
        if r["computed"] is None or abs(r["computed"] - r["yahoo"]) > 0.05:
            problems.setdefault(r["week"], []).append(
                "team %s: starters sum to %s, Yahoo scored %.2f"
                % (r["team_key"], r["computed"], r["yahoo"]))
    return problems


def week_ends(conn: sqlite3.Connection, league_key: str) -> Dict[int, str]:
    return {r["week"]: r["week_end"] for r in conn.execute(
        "SELECT week, MAX(week_end) AS week_end FROM matchups WHERE league_key=? GROUP BY week",
        (league_key,))}
