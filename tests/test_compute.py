"""
Tests for compute/ and the scoring in snapshot/, on real Life's Gr8 fixtures.

tests/fixtures/lifesgr8_2026_week1.json holds the league's real 2026 scoring
settings, its real roster slots, week 1's sixteen team scores, and a handful of
NFL stat lines with Yahoo's own point totals. No team or manager names.

Run with: python3 -m unittest discover -s tests
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fantasy_api import compute  # noqa: E402
from fantasy_api.snapshot import score  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures" / "lifesgr8_2026_week1.json").read_text())
BONUSES = {k: [tuple(t) for t in v] for k, v in FIX["bonuses"].items()}


class Scoring(unittest.TestCase):
    def test_every_fixture_player_matches_yahoo(self):
        for p in FIX["players"]:
            with self.subTest(player=p["name"], case=p["why"]):
                self.assertAlmostEqual(score(p["stats"], FIX["modifiers"], BONUSES),
                                       p["yahoo_points"], places=2)

    def test_yardage_bonuses_stack(self):
        henry = next(p for p in FIX["players"] if p["name"] == "Derrick Henry")
        without = score(henry["stats"], FIX["modifiers"], {})
        self.assertAlmostEqual(score(henry["stats"], FIX["modifiers"], BONUSES) - without, 5.0)

    def test_bonus_threshold_is_inclusive(self):
        rush = {"9": 75.0}
        self.assertAlmostEqual(score(rush, FIX["modifiers"], BONUSES), 75 * 0.05 + 1)
        self.assertAlmostEqual(score({"9": 74.0}, FIX["modifiers"], BONUSES), 74 * 0.05)


class Lineups(unittest.TestCase):
    SLOTS = [s for s in FIX["roster_slots"] if s not in ("BN", "IR")]

    def test_flex_does_not_steal_a_strict_slot(self):
        players = [
            ("rb1", ["RB", "W/R/T"], 30.0), ("rb2", ["RB", "W/R/T"], 20.0),
            ("rb3", ["RB", "W/R/T"], 15.0), ("wr1", ["WR", "W/R/T"], 10.0),
            ("wr2", ["WR", "W/R/T"], 9.0),
        ]
        total, chosen = compute.optimal_lineup(self.SLOTS, players)
        self.assertEqual(set(chosen), {"rb1", "rb2", "rb3", "wr1", "wr2"})
        self.assertAlmostEqual(total, 84.0)

    def test_idp_slots_take_the_two_best_defenders(self):
        players = [("lb", ["D"], 14.5), ("s", ["D"], 9.0), ("cb", ["D"], 3.0),
                   ("def", ["DEF"], 25.0)]
        total, chosen = compute.optimal_lineup(self.SLOTS, players)
        self.assertEqual(set(chosen), {"lb", "s", "def"})
        self.assertAlmostEqual(total, 48.5)

    def test_empty_slots_are_left_empty_rather_than_filled_illegally(self):
        total, chosen = compute.optimal_lineup(self.SLOTS, [("k", ["K"], 7.0)])
        self.assertEqual(chosen, ["k"])


class AllPlay(unittest.TestCase):
    def test_week1_real_scores(self):
        scores = dict(enumerate(FIX["week1_team_scores"]))
        ap = compute.all_play(scores)
        self.assertEqual(ap[0], (15, 0, 0))                        # the week high
        self.assertEqual(ap[len(scores) - 1], (0, 15, 0))          # the week low
        self.assertEqual(sum(w for w, _, _ in ap.values()), sum(l for _, l, _ in ap.values()))

    def test_ties_count_as_ties(self):
        self.assertEqual(compute.all_play({0: 100.0, 1: 100.0, 2: 90.0})[0], (1, 0, 1))


class Positions(unittest.TestCase):
    def test_leader_sits_at_ninety_percent(self):
        pos = compute.race_positions([193.02, 145.56, 65.25], "high")
        self.assertEqual(pos[0], 0.9)
        self.assertAlmostEqual(pos[1], round(0.9 * 145.56 / 193.02, 4))

    def test_floor_and_all_zero_race(self):
        self.assertEqual(compute.race_positions([0, 0, 0], "high"), [0.08, 0.08, 0.08])
        self.assertEqual(min(compute.race_positions([100, 1], "high")), 0.08)

    def test_low_direction_puts_the_smallest_value_in_front(self):
        pos = compute.race_positions([44.6, 87.5, 10.0], "low")
        self.assertEqual(pos.index(max(pos)), 2)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_javascript_mirror_agrees(self):
        """charts.js re-derives positions for the week scrubber. It must match exactly."""
        cases = [([193.02, 145.56, 65.25, 0.0], "high"), ([44.6, 87.5, 10.0], "low"),
                 ([0, 0, 0], "high"), ([1, 1, 2], "high")]
        script = """
          global.window = {LG: {}};
          require(%r);
          const C = window.LG.chart;
          console.log(JSON.stringify(%s.map(([v, d]) => C.positions(v, d, {leader_scale: 0.9, position_floor: 0.08}))));
        """ % (str(ROOT / "site/static/js/charts.js"), json.dumps(cases))
        js = json.loads(subprocess.check_output(["node", "-e", script]).decode())
        for (values, direction), got in zip(cases, js):
            want = compute.race_positions(values, direction)
            self.assertEqual([round(x, 4) for x in got], want)


class DraftCurve(unittest.TestCase):
    def test_non_increasing_and_immune_to_a_pick_one_outlier(self):
        # Pick one scoring 32 (a real 2024 injury) must not drag the head of the curve down,
        # and one monster season must not throw it to several hundred.
        pts = [(1, 32.4), (2, 325.35), (3, 210.0), (4, 190.0)] + \
              [(p, 200.0 - p * 0.6) for p in range(5, 257)]
        knots = compute.fit_draft_curve(pts, bin_size=16)
        ys = [y for _, y in knots]
        self.assertEqual(ys, sorted(ys, reverse=True))
        self.assertLess(compute.curve_value(knots, 1), 260)
        self.assertGreater(compute.curve_value(knots, 1), 150)

    def test_flat_beyond_the_fitted_range(self):
        knots = [(10.0, 200.0), (100.0, 100.0)]
        self.assertEqual(compute.curve_value(knots, 1), 200.0)
        self.assertEqual(compute.curve_value(knots, 999), 100.0)
        self.assertEqual(compute.curve_value(knots, 55), 150.0)


class PlayoffOdds(unittest.TestCase):
    @staticmethod
    def _round_robin(n, weeks):
        ids, rounds = list(range(n)), []
        for _ in range(n - 1):
            rounds.append([(ids[i], ids[n - 1 - i]) for i in range(n // 2)])
            ids = [ids[0], ids[-1]] + ids[1:-1]
        return [rounds[w % len(rounds)] for w in range(weeks)]

    def _run(self, scores=None, seed=8):
        teams = list(range(16))
        scores = scores or FIX["week1_team_scores"]
        records = {t: {"wins": 1 if t < 8 else 0, "ties": 0, "pf": scores[t]} for t in teams}
        weekly = {t: [scores[t]] for t in teams}
        remaining = self._round_robin(16, 12)
        return compute.playoff_odds(teams, records, weekly, remaining, spots=8, sims=2000, seed=seed)

    def test_schedule_helper_is_legal(self):
        for week in self._round_robin(16, 12):
            played = [t for pair in week for t in pair]
            self.assertEqual(sorted(played), list(range(16)))

    def test_odds_account_for_exactly_eight_spots(self):
        self.assertAlmostEqual(sum(self._run().values()), 800.0, delta=0.5)

    def test_seeded_and_reproducible(self):
        self.assertEqual(self._run(), self._run())

    def test_one_week_does_not_decide_a_season(self):
        # The week 1 low scorer, 0-1 with 65 points, is nowhere near eliminated.
        self.assertGreater(self._run()[15], 2.0)

    def test_identical_teams_are_a_coin_flip(self):
        odds = compute.playoff_odds(list(range(16)),
                                    {t: {"wins": 0, "ties": 0, "pf": 110.0} for t in range(16)},
                                    {t: [110.0] for t in range(16)}, self._round_robin(16, 13),
                                    spots=8, sims=3000)
        for t, pct in odds.items():
            self.assertAlmostEqual(pct, 50.0, delta=6.0)


class Codes(unittest.TestCase):
    def test_codes_are_unique_and_never_an_nfl_team(self):
        taken = set()
        for name in ["densmen", "Den Mothers", "Dennis Menace", "sisu", "e-mans revenge", "Long Howie"]:
            code = compute.team_code(name, taken)
            self.assertNotIn(code, compute.NFL_CODES)
            self.assertNotIn(code, taken)
            self.assertEqual(len(code), 3)
            taken.add(code)


class Jockeys(unittest.TestCase):
    def test_short_names(self):
        cases = {"Derrick Henry": "D. Henry", "Kenneth Walker III": "K. Walker",
                 "Amon-Ra St. Brown": "A. St. Brown", "Michael Pittman Jr.": "M. Pittman",
                 "Josh Allen": "J. Allen", "Travis Kelce": "T. Kelce"}
        for full, short in cases.items():
            self.assertEqual(compute.short_name(full), short)
        self.assertEqual(compute.short_name("Seahawks", "DT"), "Seahawks")
        self.assertEqual(compute.short_name(None), "?")

    def test_jockeys_rank_by_contribution_and_count_the_rest(self):
        class P(dict):
            pass
        season = type("S", (), {"players": {k: {"name": n, "position_type": "O"} for k, n in
                                            [("a", "Derrick Henry"), ("b", "Ashton Jeanty"),
                                             ("c", "Jadarian Price"), ("d", "Jonathon Brooks")]}})()
        j = compute._jockeys({"a": 144, "b": 102, "c": 52, "d": 2, "z": 0}, season)
        self.assertEqual([x["name"] for x in j["top"]], ["D. Henry", "A. Jeanty", "J. Price"])
        self.assertEqual(j["more"], 1)


class CorrectionWindow(unittest.TestCase):
    def test_a_finished_week_is_not_final_until_corrections_are_in(self):
        from datetime import date
        from fantasy_api.corrections import is_final
        week1_end = "2026-09-14"                       # Monday night of week 1
        self.assertFalse(is_final(week1_end, date(2026, 9, 16)))   # Wednesday: corrections pending
        self.assertFalse(is_final(week1_end, date(2026, 9, 18)))
        self.assertTrue(is_final(week1_end, date(2026, 9, 19)))
        self.assertFalse(is_final(None, date(2027, 1, 1)))
        self.assertFalse(is_final("not a date", date(2027, 1, 1)))


@unittest.skipUnless((ROOT / "data" / "league.db").exists(), "no local snapshot")
class AgainstYahoo(unittest.TestCase):
    """Integration checks on the local snapshot."""

    def test_counting_race_jockeys_add_up_to_the_lane_total(self):
        from fantasy_api import snapshot
        conn = snapshot.connect()
        key = conn.execute("SELECT league_key FROM leagues ORDER BY season DESC LIMIT 1").fetchone()[0]
        season = compute.Season(conn, key)
        tw = compute.team_weeks(season)
        baseline = compute.draft_baseline(conn, bin_size=season.n)
        totals, _, jockeys, _ = compute.race_totals(season, tw, baseline)
        for stat in ("rush_yards", "pass_yards", "sacks", "solo_tackles", "defense_points"):
            for ti in range(season.n):
                j = jockeys[ti].get(stat)
                if not j or j["more"]:
                    continue  # only lanes where every contributor is listed can be summed
                with self.subTest(stat=stat, team=ti):
                    self.assertAlmostEqual(sum(x["value"] for x in j["top"]), totals[ti][stat], places=1)

    def test_standings_match_yahoo(self):
        import glob
        from fantasy_api import snapshot
        from fantasy_api.yahoo_shapes import items, merge
        conn = snapshot.connect()
        key = conn.execute("SELECT league_key FROM leagues ORDER BY season DESC LIMIT 1").fetchone()[0]
        season = compute.Season(conn, key)
        st = compute.standings(season, compute.team_weeks(season), {"season": {}, "playoffs": {}})
        files = sorted(glob.glob(str(ROOT / "data" / "raw" / ("league_%s_standings" % key) / "*.json")))
        if not files:
            self.skipTest("no standings snapshot")
        raw = json.loads(Path(files[-1]).read_text())
        yahoo = {}
        for e in items(raw["fantasy_content"]["league"][1]["standings"][0]["teams"]):
            t = merge(e["team"][0])
            ts = merge(e["team"][1:])["team_standings"]
            yahoo[t["team_key"]] = (int(ts["outcome_totals"]["wins"]), int(ts["outcome_totals"]["losses"]),
                                    round(float(ts["points_for"]), 2))
        for row in st["rows"]:
            team_key = season.teams[row["team_index"]]["team_key"]
            self.assertEqual((row["wins"], row["losses"], round(row["pf"], 2)), yahoo[team_key])


if __name__ == "__main__":
    unittest.main()
