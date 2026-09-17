"""
Tests for the weekly new-age stats. Scoring checks use real week 1 ffopportunity
rows; the play-level stats use small hand-built plays in the real nflverse schema,
because each rule (kneels out, scrambles out, read code 0 out) needs its own case.
"""

import json
import math
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fantasy_api.compute import newage, short_name  # noqa: E402
from fantasy_api.snapshot import nflverse as nfl_snap  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures" / "lifesgr8_2026_week1.json").read_text())
MODS = FIX["modifiers"]


def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(nfl_snap.SCHEMA)
    conn.execute("CREATE TABLE stat_categories (league_key TEXT, stat_id TEXT, modifier REAL)")
    for sid, m in MODS.items():
        conn.execute("INSERT INTO stat_categories VALUES ('L', ?, ?)", (sid, m))
    return conn


_n = [0]


def play(conn, **kw):
    _n[0] += 1
    row = dict(game_id="G1", play_id=str(_n[0]), week=1, posteam="BAL", play_type="pass",
               pass_attempt=0, rush_attempt=0, receiver_id=None, rusher_id=None, yardline_100=50,
               yards_gained=0, qb_kneel=0, qb_scramble=0, two_point=0, sack=0, read_thrown=None,
               is_motion=0)
    row.update(kw)
    conn.execute("INSERT INTO nfl_plays VALUES (%s)" % ",".join("?" * 17), [row[k] for k in (
        "game_id", "play_id", "week", "posteam", "play_type", "pass_attempt", "rush_attempt",
        "receiver_id", "rusher_id", "yardline_100", "yards_gained", "qb_kneel", "qb_scramble",
        "two_point", "sack", "read_thrown", "is_motion")])


class PointsOverExpected(unittest.TestCase):
    def test_actual_components_reproduce_yahoo_minus_bonuses(self):
        henry = FIX["nflverse"]["ep_rows"]["Derrick Henry"]
        self.assertAlmostEqual(newage.league_points(henry, MODS, expected=False),
                               FIX["nflverse"]["yahoo_minus_bonuses"]["Derrick Henry"], places=2)

    def test_expected_uses_league_modifiers_not_ppr(self):
        henry = FIX["nflverse"]["ep_rows"]["Derrick Henry"]
        exp = newage.league_points(henry, MODS, expected=True)
        # 99.08 rush yds x .05 + 1.40 TD x 6 + 24 carries x .2 + .85 rec x .4 + 6.59 rec yds x .05 + .04 TD x 6
        self.assertAlmostEqual(exp, 99.08 * .05 + 1.40 * 6 + 24 * .2 + .85 * .4 + 6.59 * .05 + .04 * 6, places=1)

    def test_quarterback_incompletions_are_priced(self):
        allen = FIX["nflverse"]["ep_rows"]["Josh Allen"]
        actual = newage.league_points(allen, MODS, expected=False)
        yahoo = FIX["nflverse"]["josh_allen_yahoo"]
        bonus = 5.0 if yahoo["stats"]["4"] >= 300 else 0.0
        # nflverse and Yahoo chart a handful of QB plays differently (scrambles, sacks),
        # so the two sources agree to within a point, not to the penny.
        self.assertLess(abs(actual - (yahoo["points"] - bonus)), 1.0)


class FirstRead(unittest.TestCase):
    def test_counts_only_charted_read_codes(self):
        c = db()
        for code in ("1", "1", "1", "2", "CHK", "DES", "0", "0"):
            play(c, pass_attempt=1, receiver_id="WR1", read_thrown=code)
        play(c, pass_attempt=1, receiver_id="WR1", read_thrown="1", two_point=1)   # two-point: out
        rows = newage.first_read_share(c, 1, min_targets=1)
        self.assertEqual(rows[0]["detail"], {"first_read_targets": 3, "charted_targets": 6})
        self.assertAlmostEqual(rows[0]["value"], 0.5)

    def test_minimum_targets(self):
        c = db()
        for _ in range(5):
            play(c, pass_attempt=1, receiver_id="WR1", read_thrown="1")
        self.assertEqual(newage.first_read_share(c, 1, min_targets=6), [])


class ExplosiveRuns(unittest.TestCase):
    def test_designed_runs_only_and_fifteen_is_explosive(self):
        c = db()
        for yards in (15, 14, 40, 3):
            play(c, play_type="run", rush_attempt=1, rusher_id="RB1", yards_gained=yards)
        play(c, play_type="run", rush_attempt=1, rusher_id="RB1", yards_gained=30, qb_scramble=1)
        play(c, play_type="run", rush_attempt=1, rusher_id="RB1", yards_gained=-1, qb_kneel=1)
        rows = newage.explosive_run_rate(c, 1, min_carries=1, explosive_yards=15)
        self.assertEqual(rows[0]["detail"], {"explosive_runs": 2, "carries": 4})


class RedZone(unittest.TestCase):
    def test_share_of_team_looks_inside_the_twenty(self):
        c = db()
        play(c, pass_attempt=1, receiver_id="WR1", yardline_100=12)
        play(c, pass_attempt=1, receiver_id="TE1", yardline_100=5)
        play(c, play_type="run", rush_attempt=1, rusher_id="RB1", yardline_100=3)
        play(c, play_type="run", rush_attempt=1, rusher_id="RB1", yardline_100=1)
        play(c, pass_attempt=1, receiver_id="WR1", yardline_100=35)                 # outside: out
        play(c, pass_attempt=1, sack=1, yardline_100=10)                             # sack: out
        play(c, play_type="run", rush_attempt=1, rusher_id="QB1", yardline_100=8, qb_scramble=1)
        rows = {r["gsis_id"]: r for r in newage.red_zone_share(c, 1, min_opps=1)}
        self.assertAlmostEqual(rows["RB1"]["value"], 0.5)
        self.assertEqual(rows["RB1"]["detail"]["team_opportunities"], 4)
        self.assertNotIn("QB1", rows)

    def test_traded_player_is_judged_on_his_larger_role(self):
        c = db()
        play(c, pass_attempt=1, receiver_id="WR9", yardline_100=10, posteam="NYJ")
        for _ in range(3):
            play(c, pass_attempt=1, receiver_id="WR9", yardline_100=10, posteam="DAL", week=2)
        play(c, pass_attempt=1, receiver_id="WR2", yardline_100=10, posteam="DAL", week=2)
        rows = newage.red_zone_share(c, 2, min_opps=1)
        self.assertEqual(rows[0]["detail"]["team"], "DAL")


class BoardsAndRotation(unittest.TestCase):
    CFG = {"stat": [
        {"id": "poe", "name": "Points Over Expected", "short": "POE/G", "unit": "pts per game",
         "format": "signed", "blurb": "", "how": "", "source": "", "min_expected_per_game": 0},
        {"id": "first_read", "name": "First Read Share", "short": "1st", "unit": "of targets",
         "format": "percent", "blurb": "", "how": "", "source": "", "min_targets_per_week": 1},
    ]}

    def _board(self, week):
        c = db()
        c.execute("CREATE TABLE IF NOT EXISTS players (player_key TEXT)")
        for gsis, first, total in (("A", 4, 4), ("B", 2, 2), ("C", 1, 2)):
            for i in range(total):
                play(c, pass_attempt=1, receiver_id=gsis, read_thrown="1" if i < first else "2", week=1)
        for key, gsis in (("y.a", "A"), ("y.b", "B"), ("y.c", "C")):
            c.execute("INSERT INTO yahoo_gsis VALUES (?,?,?)", (key, gsis, "yahoo_id"))
        return newage.build(c, "L", week, self.CFG, team_of={"y.a": 0, "y.b": 1, "y.c": 2},
                            name_of={"y.a": ("Al Pha", "WR", "BAL"), "y.b": ("Bea Tee", "WR", "BAL"),
                                     "y.c": ("Cee Dee", "WR", "BAL")}, short_name=short_name)

    def test_ties_share_a_rank_and_volume_breaks_the_order(self):
        board = self._board(1)["boards"]["first_read"]
        ranks = [(r["name"], r["nfl_rank"], r["tied"]) for r in board["leaders"]]
        self.assertEqual(ranks, [("Al Pha", 1, True), ("Bea Tee", 1, True), ("Cee Dee", 3, False)])

    def test_rotation_follows_the_week_and_repeats(self):
        self.assertEqual([self._board(w)["scheduled"] for w in (1, 2, 3, 4)],
                         ["poe", "first_read", "poe", "first_read"])

    def test_a_stat_with_no_data_yet_hands_the_week_to_the_next(self):
        board = self._board(1)            # poe is scheduled, but no expected-points rows exist
        self.assertEqual(board["scheduled"], "poe")
        self.assertEqual(board["headline"], "first_read")
        self.assertEqual(board["boards"]["poe"]["leaders"], [])

    def test_headline_sentence_says_tied(self):
        fact = self._board(1)["headline_fact"]
        self.assertIn("tied for 1st among 3 qualifying NFL players", fact["sentence"])


class Identity(unittest.TestCase):
    def test_name_normalization(self):
        self.assertEqual(nfl_snap.norm_name("Harold Fannin Jr."), nfl_snap.norm_name("Harold Fannin"))
        self.assertEqual(nfl_snap.norm_name("Tre' Harris"), nfl_snap.norm_name("Tre Harris"))
        self.assertEqual(nfl_snap.norm_name("Oronde Gadsden II"), nfl_snap.norm_name("Oronde Gadsden"))
        self.assertEqual(nfl_snap.norm_name("Jacory Croskey-Merritt"), "jacorycroskeymerritt")

    def test_yahoo_and_nflverse_team_codes(self):
        self.assertEqual(nfl_snap.TEAM_ALIASES["LAR"], "LA")


class FetcherScope(unittest.TestCase):
    def test_only_nflverse_release_and_id_map_urls(self):
        from fantasy_api.ingest import nflverse
        calls = []

        def opener(req, timeout):
            calls.append(req.get_method())
            raise AssertionError("should not be reached")

        for bad in ("https://api.github.com/repos/someone/else/releases/tags/x",
                    "https://example.com/play_by_play_2026.csv.gz",
                    "https://raw.githubusercontent.com/someone/else/main/x.csv"):
            with self.assertRaises(nflverse.NflverseError):
                nflverse._get(bad, opener)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
