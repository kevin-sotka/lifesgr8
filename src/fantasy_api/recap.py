"""
The Tuesday recap, in Doc's voice.

Three parts, kept separate on purpose:

1. facts()    Python assembles a finished fact object from the snapshot database.
              Every number the recap may use is in here, already computed.
2. write()    Claude turns those facts into prose. It is handed facts, never raw data,
              and never asked to work anything out.
3. check()    The prose is rejected if it breaks a rule that can be checked by
              machine: an em dash, a number that is not in the facts, a manager's
              name, a missing matchup. One retry with the problems listed, then fail.

Doc's character sheet (docs/doc_voice.md) is private. It is gitignored, read from
disk locally, and from the DOC_VOICE secret in CI. It never ships to the site.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .compute import Season, team_weeks
from .config import DATA, LEAGUE_TOML, ROOT, read_toml

RECAP_DIR = DATA / "recaps"
VOICE_FILE = ROOT / "docs" / "doc_voice.md"


# =============================================================================
# 1. facts
# =============================================================================

def facts(conn: sqlite3.Connection, league_key: str, week: int,
          newage: Optional[Dict[str, Any]] = None,
          purse: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    season = Season(conn, league_key)
    if week not in season.completed:
        raise ValueError("Week %d is not complete yet" % week)
    tw = {(r["week"], r["team_index"]): r for r in team_weeks(season)}
    name = lambda i: season.teams[i]["name"]

    def player(ref):
        if not ref:
            return None
        p = season.players[ref["player_key"]]
        return {"name": p["name"], "position": p["position"], "points": round(ref["points"], 2)}

    # Records as they stood after this week, from this week and earlier only.
    rec = {i: [0, 0, 0] for i in range(season.n)}
    streak: Dict[int, List[str]] = {i: [] for i in range(season.n)}
    for w in season.completed:
        if w > week:
            break
        for m in season.matchups[w]:
            a, b = season.idx[m["team_a_key"]], season.idx[m["team_b_key"]]
            for me, pm, po in ((a, m["points_a"], m["points_b"]), (b, m["points_b"], m["points_a"])):
                r = "W" if pm > po else "L" if pm < po else "T"
                rec[me]["WLT".index(r)] += 1
                streak[me].append(r)

    def record(i):
        w, l, t = rec[i]
        return "%d-%d%s" % (w, l, "-%d" % t if t else "")

    def run(i):
        s = streak[i]
        n = 0
        for r in reversed(s):
            if r != s[-1]:
                break
            n += 1
        return {"result": {"W": "won", "L": "lost", "T": "tied"}[s[-1]], "games": n} if s else None

    scores = season.team_points(week)
    margins = []
    matchups = []
    for m in sorted(season.matchups[week], key=lambda m: -abs(m["points_a"] - m["points_b"])):
        a, b = season.idx[m["team_a_key"]], season.idx[m["team_b_key"]]
        if m["points_b"] > m["points_a"]:
            a, b = b, a
        margin = round(abs(m["points_a"] - m["points_b"]), 2)
        margins.append(margin)

        def side(i):
            row = tw[(week, i)]
            return {
                "team": name(i), "points": round(scores[i], 2), "record_after": record(i),
                "streak_after": run(i),
                "top_starter": player(row["best_starter"]),
                "worst_starter": player(row["worst_starter"]),
                # These two are different numbers and the difference matters: starting the
                # benched player would have benched somebody else, so the lineup cost is
                # usually smaller than the benched player's own score.
                "points_a_better_lineup_would_have_added": round(row["regret"], 2),
                "best_player_left_on_the_bench": player(row["best_bench"]),
            }

        matchups.append({"winner": side(a), "loser": side(b), "margin": margin,
                         "tied": m["points_a"] == m["points_b"]})
    matchups[0]["widest_margin_of_week"] = True
    matchups[-1]["closest_margin_of_week"] = True

    hi = max(scores, key=scores.get)
    lo = min(scores, key=scores.get)
    idp = max((tw[(week, i)] for i in range(season.n) if tw[(week, i)]["best_idp"]),
              key=lambda r: r["best_idp"]["points"], default=None)
    regret = max((tw[(week, i)] for i in range(season.n)), key=lambda r: r["regret"])

    unbeaten = [name(i) for i in range(season.n) if rec[i][1] == 0 and rec[i][0] > 0]
    winless = [name(i) for i in range(season.n) if rec[i][0] == 0 and rec[i][1] > 0]

    rules = read_toml(LEAGUE_TOML)
    ends = int(rules.get("season", {}).get("regular_season_end_week", 13))
    out = {
        "league": season.league["name"], "season": season.league["season"], "week": week,
        "regular_season_ends_after_week": ends,
        "weeks_left_in_regular_season": max(0, ends - week),
        "teams_that_make_the_playoffs": int(rules.get("playoffs", {}).get("teams", 8)),
        "week_high": {"team": name(hi), "points": round(scores[hi], 2)},
        "week_low": {"team": name(lo), "points": round(scores[lo], 2)},
        "best_idp_performance": ({"team": name(idp["team_index"]), **player(idp["best_idp"])}
                                 if idp else None),
        "worst_lineup_of_the_week": {"team": name(regret["team_index"]),
                                     "points_a_better_lineup_would_have_added": round(regret["regret"], 2),
                                     "best_player_left_on_the_bench": player(regret["best_bench"])},
        "unbeaten_teams": unbeaten, "winless_teams": winless,
        "matchups": matchups,
    }
    if purse:
        this_week = next((w for w in purse["weeks"] if w["week"] == week), None)
        if this_week:
            counts = {r["team_index"]: r for r in purse["tally"]}
            out["in_the_money_this_week"] = {
                "how_it_works": "The commissioner pays the top %d scores each week, %s, at the "
                                "end of the season."
                                % (purse["places"],
                                   ", ".join("%s%g for %s" % (purse.get("currency", "$"), a, n)
                                             for a, n in zip(purse["amounts"],
                                                             ("1st", "2nd", "3rd", "4th")))),
                "currency": purse.get("currency", "$"),
                "paid": [{"place": p["place"], "team": name(p["team_index"]),
                          "points": p["points"], "wins": p["amount"],
                          "tied_at_this_place": p["tied"],
                          "weeks_in_the_money_so_far": counts[p["team_index"]]["weeks_in_the_money"]}
                         for p in this_week["paid"]],
            }
    if newage and newage.get("headline_fact") and newage.get("week") == week:
        board = newage["boards"][newage["headline"]]
        lead = board["leaders"][0]
        out["stat_of_the_week"] = {
            "stat": board["name"], "what_it_measures": board["blurb"],
            "leader": lead["name"], "team": name(lead["team_index"]),
            "value": newage["headline_fact"]["display"] + " " + board["unit"],
            "nfl_rank": lead["nfl_rank"], "qualifying_nfl_players": board["qualifiers"],
        }
    return out


# =============================================================================
# 2. write
# =============================================================================

RULES = """You are writing the weekly recap for Life's Gr8, a sixteen-team fantasy football \
league, published on the league's public website. You are Doc: the character sheet below \
describes him. Use his personality, his frontier dialect, and his humor. The sheet was \
written for a different job, narrating a business portfolio, so ignore its portfolio \
lexicon, its response modes, and anything about Kevin's projects. Here Doc is a \
frontier-town newspaperman calling the week's races.

Hard rules. A recap that breaks one is thrown out.

1. Use only the facts in the user message. Every number you write must appear in those \
facts. You may round fantasy points to one decimal. Do not write years, ages, or any other \
number, even in a joke or a story ("a fella I knew in '24" is out).
2. Never invent a statistic, a player, a nickname, a quote, a rivalry, or any league \
history. If it is not in the facts, it did not happen.
2b. Numbers belong to the game you are writing about. Use a matchup's own numbers in its \
own paragraph. "points_a_better_lineup_would_have_added" is what a better lineup was worth, \
and it is smaller than "best_player_left_on_the_bench" because starting that player would \
have benched someone else. Do not mix the two into one figure.
3. Refer to teams only by their team names, exactly as written in the facts. Name no real \
people except the NFL players listed in the facts.
4. Losing badly, benching points, and bad lineups are fair game. Nothing outside the game is.
5. Never use an em dash or an en dash. Use commas, periods, or colons.
5b. Write every number as a numeral, taken from the facts. Do not spell numbers out: \
"12 weeks", never "twelve weeks". If you want to say how much of the season is left, the \
facts carry it.
6. Stay in character. Never mention AI, prompts, models, data feeds, or how the recap was made.
7. "paragraphs" holds exactly one paragraph per matchup, in the order given, two to four \
sentences each, naming both teams. Put nothing else in that list.
7b. "purse" is one or two sentences on the teams in the money this week, from \
"in_the_money_this_week". Name every team on that list, in order, and say what each \
scored. This is the commissioner's payout, so it is worth a little ceremony.
8. "sign_off" is for a closing line to the league, one or two sentences. Leave it as an \
empty string if the week does not want one. Any parting words go here, never in "paragraphs".
9. Headline: one line, under fourteen words. Lede: two or three sentences on the week as a \
whole.

--- Doc's character sheet ---
"""

def schema_for(fact: Dict[str, Any]) -> Dict[str, Any]:
    """
    The response schema, shaped to this week's actual matchups.

    One required field per game rather than a list. Telling Doc "one paragraph per
    matchup" in the prompt was not enough (he kept adding an intro or a closing line
    to the list), and a list cannot be pinned to a length here: structured outputs
    only accept minItems of 0 or 1. A field per game fixes the count, names the two
    teams in the field's own description, and leaves sign_off for parting words.
    """
    games = {}
    for n, m in enumerate(fact["matchups"], 1):
        games["game_%d" % n] = {
            "type": "string",
            "description": "Two to four sentences on %s against %s. Name both teams."
                           % (m["winner"]["team"], m["loser"]["team"]),
        }
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "One line, under fourteen words."},
            "lede": {"type": "string", "description": "Two or three sentences on the week."},
            "sign_off": {"type": "string",
                         "description": "A closing line to the league, or an empty string."},
            "purse": {"type": "string",
                      "description": "One or two sentences naming every team in the money this "
                                     "week, in order, with what each scored. Empty string if "
                                     "the facts carry no purse."},
            **games,
        },
        "required": ["headline", "lede", "sign_off", "purse"] + list(games),
        "additionalProperties": False,
    }


def normalize(payload: Dict[str, Any], matchups: int) -> Dict[str, Any]:
    """Turn the per-game fields back into the ordered list the site renders."""
    if "paragraphs" in payload:
        return payload
    return {
        "headline": payload["headline"], "lede": payload["lede"],
        "sign_off": payload.get("sign_off", ""), "purse": payload.get("purse", ""),
        "paragraphs": [payload.get("game_%d" % n, "") for n in range(1, matchups + 1)],
    }


def load_voice() -> str:
    text = os.environ.get("DOC_VOICE_PROMPT") or (VOICE_FILE.read_text() if VOICE_FILE.exists() else "")
    if not text.strip():
        raise RuntimeError("Doc's voice sheet is missing: docs/doc_voice.md locally, DOC_VOICE in CI.")
    return text


def write(fact: Dict[str, Any], voice: str, model: str, client: Any = None,
          feedback: Optional[List[str]] = None) -> Dict[str, Any]:
    if client is None:
        import anthropic  # only the recap needs the SDK; the rest of the pipeline is stdlib
        client = anthropic.Anthropic()
    user = "The facts for Week %d, as JSON:\n\n%s" % (fact["week"], json.dumps(fact, indent=1))
    if feedback:
        user += ("\n\nA previous draft was rejected for these reasons. Fix every one:\n- "
                 + "\n- ".join(feedback))
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high",
                       "format": {"type": "json_schema", "schema": schema_for(fact)}},
        system=RULES + voice,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The recap request was declined: %s" % (response.stop_details,))
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The recap was cut off at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    draft = normalize(json.loads(text), len(fact["matchups"]))
    draft["model"] = response.model
    return draft


# =============================================================================
# 3. check
# =============================================================================

NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")


def allowed_numbers(fact: Any) -> set:
    """Every number that appears anywhere in the facts, in the forms a writer might use."""
    found: set = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, bool):
            return
        elif isinstance(v, (int, float)):
            add(float(v))
        elif isinstance(v, str):
            for tok in NUMBER.findall(v):
                add(float(tok))

    def add(x):
        found.update({_norm(x), _norm(round(x, 1)), _norm(round(x))})

    walk(fact)
    return found


def _norm(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def check(draft: Dict[str, Any], fact: Dict[str, Any], forbidden_names: List[str]) -> List[str]:
    problems = []
    text_parts = ([draft.get("headline", ""), draft.get("lede", ""), draft.get("sign_off", ""),
                   draft.get("purse", "")] + list(draft.get("paragraphs", [])))
    text = "\n".join(text_parts)
    if "—" in text or "–" in text:
        problems.append("It uses an em dash or en dash. Use commas, periods, or colons.")
    if len(draft.get("paragraphs", [])) != len(fact["matchups"]):
        problems.append(
            "\"paragraphs\" has %d entries; there are %d matchups, so it needs exactly %d. "
            "A closing line to the league goes in \"sign_off\", not in \"paragraphs\"."
            % (len(draft.get("paragraphs", [])), len(fact["matchups"]), len(fact["matchups"]))),
    # Numbers are checked against the facts for the game being written about, not the
    # whole week. Otherwise one matchup's score vouches for a wrong number in another.
    week_wide = {k: v for k, v in fact.items() if k != "matchups"}
    shared = allowed_numbers(week_wide)
    everywhere = allowed_numbers(fact)
    for label, part, allowed in (
            [("the headline", draft.get("headline", ""), everywhere),
             ("the lede", draft.get("lede", ""), everywhere),
             ("the sign off", draft.get("sign_off", ""), everywhere),
             ("the purse line", draft.get("purse", ""), everywhere)]
            + [("matchup paragraph %d" % (i + 1), para,
                shared | allowed_numbers(fact["matchups"][i]))
               for i, para in enumerate(draft.get("paragraphs", [])[:len(fact["matchups"])])]):
        bad = sorted({tok for tok in NUMBER.findall(part) if _norm(float(tok)) not in allowed})
        if bad:
            problems.append("%s uses numbers that do not belong to it: %s."
                            % (label[0].upper() + label[1:], ", ".join(bad)))
    # Rounding means a point total can vouch for almost any small whole number, so
    # years get their own check: "in '24" or "back in 2019" are never in the facts.
    spelled = sorted(set(w.lower() for w in re.findall(
        r"\b(?:[Tt]en|[Ee]leven|[Tt]welve|[Tt]hirteen|[Ff]ourteen|[Ff]ifteen|[Ss]ixteen|"
        r"[Ss]eventeen|[Ee]ighteen|[Nn]ineteen|[Tt]wenty|[Tt]hirty|[Ff]orty|[Ff]ifty|"
        r"[Ss]ixty|[Ss]eventy|[Ee]ighty|[Nn]inety|[Hh]undred|[Tt]housand)\b", text)))
    if spelled:
        # A spelled-out number is never checked against the facts, so it is the one place
        # an invented figure can hide. "Fifteen weeks of trail left" got through once.
        problems.append("It spells numbers out instead of using numerals: %s." % ", ".join(spelled))
    years = sorted(set(re.findall(r"['\u2018\u2019](\d{2})\b", text)) |
                   {y for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text) if int(y) != fact["season"]})
    if years:
        problems.append("It mentions years, which are not in the facts: %s." % ", ".join(years))
    for i, (para, m) in enumerate(zip(draft.get("paragraphs", []), fact["matchups"])):
        for side in ("winner", "loser"):
            if m[side]["team"] not in para:
                problems.append("Matchup paragraph %d does not name %s." % (i + 1, m[side]["team"]))
    fact_text = json.dumps(fact)
    for n in forbidden_names:
        # A manager's name is allowed only where it is also a team or player name in the facts.
        if n and re.search(r"\b%s\b" % re.escape(n), text) and n not in fact_text:
            problems.append("It names a real person who is not an NFL player in the facts: %s." % n)
    money = (fact.get("in_the_money_this_week") or {}).get("paid", [])
    missing = [p["team"] for p in money if p["team"] not in draft.get("purse", "")]
    if missing:
        problems.append("The purse line does not name every team in the money: %s."
                        % ", ".join(missing))
    if len(draft.get("headline", "").split()) >= 14:
        problems.append("The headline is fourteen words or longer.")
    return problems


# =============================================================================
# entry point
# =============================================================================

def recap_path(season: int, week: int) -> Path:
    return RECAP_DIR / ("%d-week-%02d.json" % (season, week))


def generate(conn: sqlite3.Connection, league_key: str, week: Optional[int] = None,
             client: Any = None, newage: Optional[Dict[str, Any]] = None,
             purse: Optional[Dict[str, Any]] = None,
             voice: Optional[str] = None, force: bool = False,
             now: Optional[Callable[[], datetime]] = None) -> Optional[Path]:
    """Write this week's recap file if it does not exist. Returns its path, or None."""
    season = Season(conn, league_key)
    week = week or season.completed_week
    if not week:
        return None
    path = recap_path(int(season.league["season"]), week)
    if path.exists() and not force:
        return None

    cfg = read_toml(LEAGUE_TOML).get("recaps", {})
    model = cfg.get("model", "claude-opus-5")
    fact = facts(conn, league_key, week, newage, purse)
    voice = voice if voice is not None else load_voice()
    managers = [r["manager_name"] for r in conn.execute(
        "SELECT manager_name FROM teams WHERE league_key=?", (league_key,)) if r["manager_name"]]

    draft = write(fact, voice, model, client)
    problems = check(draft, fact, managers)
    if problems:
        draft = write(fact, voice, model, client, feedback=problems)
        problems = check(draft, fact, managers)
    if problems:
        raise RuntimeError("Recap for week %d failed its checks twice:\n- %s" % (week, "\n- ".join(problems)))

    record = {
        "league_key": league_key, "season": int(season.league["season"]), "week": week,
        "headline": draft["headline"], "lede": draft["lede"], "paragraphs": draft["paragraphs"],
        "sign_off": (draft.get("sign_off") or "").strip(),
        "purse": (draft.get("purse") or "").strip(),
        "status": "published", "voice": "Doc", "model": draft.get("model", model),
        "generated_at": (now() if now else datetime.now(timezone.utc)).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1) + "\n")
    return path
