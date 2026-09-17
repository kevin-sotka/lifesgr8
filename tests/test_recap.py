"""
Tests for the Tuesday recap. Real Week 1 facts; a fake Claude client, so nothing here
touches the network or spends money.
"""

import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fantasy_api import recap  # noqa: E402

FACTS = json.loads((ROOT / "tests" / "fixtures" / "recap_facts_2026_week1.json").read_text())
# Made-up manager names, except "Justin", which is also part of a real team name in the
# fixture and so must be allowed when it appears as that team.
MANAGERS = ["Rusty", "Clementine", "Ezekiel", "Justin"]


def good_draft():
    paras = []
    for m in FACTS["matchups"]:
        paras.append("Well now, %s put up %s and sent %s home with %s, pardner." % (
            m["winner"]["team"], m["winner"]["points"], m["loser"]["team"], m["loser"]["points"]))
    return {"headline": "Week 1: sisu Rides High, e-mans revenge Eats Dust",
            "lede": "Hold up there. sisu hung %s on the board and nobody else came close."
                    % FACTS["week_high"]["points"],
            "paragraphs": paras,
            "sign_off": "Saddle up, pardners. Same time next week."}


class FakeClient:
    """Stands in for anthropic.Anthropic(); returns queued drafts and records requests."""

    def __init__(self, *drafts, stop_reason="end_turn"):
        self.drafts, self.requests, self.stop_reason = list(drafts), [], stop_reason
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        text = json.dumps(self.drafts.pop(0))
        return types.SimpleNamespace(
            stop_reason=self.stop_reason, stop_details=None, model=kw["model"],
            content=[types.SimpleNamespace(type="text", text=text)])


class Checks(unittest.TestCase):
    def test_a_faithful_draft_passes(self):
        self.assertEqual(recap.check(good_draft(), FACTS, MANAGERS), [])

    def test_em_and_en_dashes_are_rejected(self):
        d = good_draft()
        d["lede"] += " Whoa — rein 'em in."
        self.assertTrue(any("dash" in p for p in recap.check(d, FACTS, MANAGERS)))

    def test_invented_numbers_are_rejected_rounded_ones_allowed(self):
        d = good_draft()
        d["paragraphs"][0] += " Henry went for 36.4 and hauled in 777 yards of gold dust."
        problems = recap.check(d, FACTS, MANAGERS)
        self.assertTrue(any("777" in p and "not in the facts" in p for p in problems), problems)
        self.assertFalse(any("36.4" in p for p in problems))   # 36.35 rounded is fine

    def test_years_are_rejected_even_when_digits_match_a_score(self):
        for story in (" Same as a fella I knew in '24.", " Ain't seen that since 2019."):
            d = good_draft()
            d["lede"] += story
            self.assertTrue(any("years" in p for p in recap.check(d, FACTS, MANAGERS)), story)
        d = good_draft()
        d["lede"] += " The 2026 season is off and runnin'."
        self.assertEqual(recap.check(d, FACTS, MANAGERS), [])

    def test_every_matchup_needs_both_teams_and_its_own_paragraph(self):
        d = good_draft()
        d["paragraphs"] = d["paragraphs"][:-1]
        self.assertTrue(any("entries" in p for p in recap.check(d, FACTS, MANAGERS)))

    def test_a_closing_line_belongs_in_sign_off_not_in_paragraphs(self):
        d = good_draft()
        d["paragraphs"].append("That's the week, pardners. Mind the waiver wire.")
        problems = recap.check(d, FACTS, MANAGERS)
        self.assertTrue(any("sign_off" in p for p in problems), problems)

    def test_the_sign_off_obeys_the_same_rules(self):
        d = good_draft()
        d["sign_off"] = "Ain't seen a week like it since 2011 \u2014 not once."
        problems = recap.check(d, FACTS, MANAGERS)
        self.assertTrue(any("dash" in p for p in problems), problems)
        self.assertTrue(any("years" in p for p in problems), problems)
        d = good_draft()
        loser = FACTS["matchups"][2]["loser"]["team"]
        d["paragraphs"][2] = d["paragraphs"][2].replace(loser, "the other fellas")
        self.assertTrue(any(loser in p for p in recap.check(d, FACTS, MANAGERS)))

    def test_manager_names_are_rejected_unless_they_are_team_names(self):
        d = good_draft()
        d["lede"] += " Rusty should be proud."
        self.assertTrue(any("Rusty" in p for p in recap.check(d, FACTS, MANAGERS)))
        d = good_draft()
        d["lede"] += " Justin's Bad Team had a rough one."    # a real team name
        self.assertFalse(any("Justin" in p for p in recap.check(d, FACTS, MANAGERS)))


class Generate(unittest.TestCase):
    def test_the_schema_pins_the_paragraph_count_to_the_matchups(self):
        client = FakeClient(good_draft())
        recap.write(FACTS, "voice", "claude-opus-5", client)
        schema = client.requests[0]["output_config"]["format"]["schema"]["properties"]["paragraphs"]
        self.assertEqual((schema["minItems"], schema["maxItems"]),
                         (len(FACTS["matchups"]), len(FACTS["matchups"])))

    def test_request_shape(self):
        client = FakeClient(good_draft())
        recap.write(FACTS, "Doc is a frontier guru.", "claude-opus-5", client)
        req = client.requests[0]
        self.assertEqual(req["model"], "claude-opus-5")
        self.assertEqual(req["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", req["betas"])
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Doc is a frontier guru.", req["system"])
        self.assertIn('"week": 1', req["messages"][0]["content"])

    def test_rejected_draft_gets_one_retry_with_the_problems_listed(self):
        bad = good_draft()
        bad["lede"] += " — and that's a fact."
        client = FakeClient(bad, good_draft())
        draft = recap.write(FACTS, "voice", "claude-opus-5", client)
        problems = recap.check(draft, FACTS, MANAGERS)
        self.assertTrue(problems)
        fixed = recap.write(FACTS, "voice", "claude-opus-5", client, feedback=problems)
        self.assertEqual(recap.check(fixed, FACTS, MANAGERS), [])
        self.assertIn("rejected", client.requests[1]["messages"][0]["content"])

    def test_refusal_raises_instead_of_publishing(self):
        client = FakeClient(good_draft(), stop_reason="refusal")
        with self.assertRaises(RuntimeError):
            recap.write(FACTS, "voice", "claude-opus-5", client)

    def test_voice_sheet_must_exist(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"DOC_VOICE_PROMPT": ""}), \
                mock.patch.object(recap, "VOICE_FILE", ROOT / "does-not-exist.md"):
            with self.assertRaises(RuntimeError):
                recap.load_voice()


if __name__ == "__main__":
    unittest.main()
