"""
Build, validate, and promote the Life's Gr8 static site.

Three commands, in the order the daily job runs them:

    python3 tools/build_site.py build      # site/ sources  -> site/build/
    python3 tools/build_site.py validate   # check site/build/ before it goes live
    python3 tools/build_site.py promote    # site/build/ -> site/dist/
    python3 tools/build_site.py all        # all three, stopping at the first failure

A bad build never replaces a good one. `promote` refuses to run unless
`validate` passes, so a failed Yahoo fetch leaves yesterday's site standing
rather than publishing an empty one.

This is the standalone version. When the Python package lands it moves into
src/fantasy_api/build/ behind `fantasy-api build|validate|promote` and the
checks below come with it unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
SRC_TEMPLATES = SITE / "templates"
SRC_STATIC = SITE / "static"
BUILD = SITE / "build"
DIST = SITE / "dist"

REQUIRED_DATA = ["league", "standings", "races", "season", "draft",
                 "blotter", "leaders", "players", "recaps", "newage"]

# Files copied to the site root rather than under static/.
ROOT_FILES = ["CNAME", ".nojekyll"]

# Written as an escape so this file does not itself violate the rule it checks.
EM_DASH = "\u2014"


class Failure(Exception):
    pass


def log(msg):
    print(msg)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    index = SRC_TEMPLATES / "index.html"
    if not index.exists():
        raise Failure("missing %s" % index)
    shutil.copy2(index, BUILD / "index.html")
    shutil.copytree(SRC_STATIC, BUILD / "static")

    for name in ROOT_FILES:
        src = SITE / name
        if src.exists():
            shutil.copy2(src, BUILD / name)
        elif name == ".nojekyll":
            # GitHub Pages otherwise runs the output through Jekyll, which drops
            # any directory beginning with an underscore.
            (BUILD / ".nojekyll").write_text("")

    stamped = stamp_assets()

    files = sum(1 for _ in BUILD.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in BUILD.rglob("*") if f.is_file())
    log("build   staged %d files, %.1f KB, %d assets versioned -> %s"
        % (files, size / 1024, stamped, BUILD))


ASSET_REF = re.compile(r'(?P<attr>href|src)="(?P<path>static/[^"?]+\.(?:css|js))"')


def stamp_assets():
    """
    Append a short content hash to every stylesheet and script the shell loads.

    The site rebuilds every morning. Without this, a returning visitor can get
    yesterday's cached JavaScript alongside today's freshly fetched JSON, and the
    failure that produces is both intermittent and very hard to read. The hash
    only changes when the file's bytes change, so an unchanged asset stays
    cached.
    """
    index = BUILD / "index.html"
    html = index.read_text()
    count = [0]

    def replace(match):
        rel = match.group("path")
        target = BUILD / rel
        if not target.exists():
            return match.group(0)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:10]
        count[0] += 1
        return '%s="%s?v=%s"' % (match.group("attr"), rel, digest)

    index.write_text(ASSET_REF.sub(replace, html))
    return count[0]


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def validate():
    problems = []
    checked = [0]

    def check(condition, message):
        checked[0] += 1
        if not condition:
            problems.append(message)

    check(BUILD.exists(), "no staged build; run build first")
    if problems:
        raise Failure("\n".join(problems))

    index = BUILD / "index.html"
    check(index.exists(), "index.html missing from the build")
    html = index.read_text() if index.exists() else ""
    check(len(html) > 1500, "index.html is suspiciously small")

    # Every asset the shell references must exist in the build.
    for marker in ('href="static/', 'src="static/'):
        start = 0
        while True:
            i = html.find(marker, start)
            if i < 0:
                break
            j = html.index('"', i + len(marker))
            rel = html[i + len(marker) - len("static/"):j]
            rel = rel.split("?")[0]   # drop the build's cache-busting stamp
            check((BUILD / rel).exists(), "referenced asset missing: %s" % rel)
            start = j

    data_dir = BUILD / "static" / "data"
    payload = {}
    for name in REQUIRED_DATA:
        path = data_dir / ("%s.json" % name)
        if not path.exists():
            problems.append("data file missing: %s.json" % name)
            continue
        try:
            payload[name] = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append("%s.json is not valid JSON: %s" % (name, exc))

    if "league" in payload:
        league = payload["league"]
        n = league.get("num_teams")
        check(n == len(league.get("teams", [])),
              "league.json says %s teams but lists %d" % (n, len(league.get("teams", []))))
        check(n and n > 0, "league.json has no teams")
        for t in league.get("teams", []):
            check(bool(t.get("code")), "team %s has no three-letter code" % t.get("name"))
            # Team names only, anywhere on the site. Manager names never ship.
            check("manager" not in t, "team %s carries a manager field" % t.get("name"))
            check(bool(t.get("silk", {}).get("light")) and bool(t.get("silk", {}).get("dark")),
                  "team %s is missing a silk colour for one of the two modes" % t.get("name"))
        try:
            datetime.fromisoformat(league.get("built_at", ""))
        except ValueError:
            problems.append("league.json built_at is not a parseable timestamp")

        if "standings" in payload:
            rows = payload["standings"].get("rows", [])
            check(len(rows) == n, "standings has %d rows for %s teams" % (len(rows), n))
            for r in rows:
                games = r["wins"] + r["losses"] + r["ties"]
                check(games > 0, "a standings row has no games played")

        if "races" in payload:
            races = payload["races"].get("races", [])
            check(len(races) > 0, "no races defined")
            for race in races:
                check(len(race.get("runners", [])) == n,
                      "race %s has %d runners, expected %s"
                      % (race.get("id"), len(race.get("runners", [])), n))
                for run in race.get("runners", []):
                    p = run.get("position", 0)
                    check(0 < p <= 1.0,
                          "race %s put %s off the track at position %s"
                          % (race.get("id"), run.get("team_index"), p))

        if "season" in payload and payload["season"].get("purse"):
            purse = payload["season"]["purse"]
            for wk in purse["weeks"]:
                check(len(wk["paid"]) >= purse["places"],
                      "week %s pays %d places, expected at least %d"
                      % (wk["week"], len(wk["paid"]), purse["places"]))
                for p in wk["paid"]:
                    check(0 <= p["team_index"] < n, "the purse credits an unknown team")
                    check(1 <= p["place"] <= purse["places"],
                          "the purse has a place outside 1 to %d" % purse["places"])
            weeks_paid = sum(len(wk["paid"]) for wk in purse["weeks"])
            check(sum(r["weeks_in_the_money"] for r in purse["tally"]) == weeks_paid,
                  "the purse tally does not match the weekly rows")
            money = sum(p["amount"] for wk in purse["weeks"] for p in wk["paid"])
            check(abs(sum(r["money"] for r in purse["tally"]) - money) < 0.01,
                  "the purse winnings do not match what the weeks paid")
            check(abs(purse["paid_so_far"] - money) < 0.01,
                  "the purse total does not match what the weeks paid")

        if "newage" in payload and payload["newage"].get("headline"):
            na = payload["newage"]
            check(na["headline"] in na.get("boards", {}), "headline stat has no board")
            check(len(na.get("rotation", [])) == len(na.get("boards", {})), "rotation and boards disagree")
            for sid, board in na.get("boards", {}).items():
                for row in board.get("leaders", []):
                    check(0 <= row["team_index"] < n, "new-age board %s credits an unknown team" % sid)
                    if board.get("format") == "percent":
                        check(0 <= row["value"] <= 1, "new-age %s has a share outside 0 to 1" % sid)

        if "draft" in payload and league.get("source") == "yahoo":
            # An empty baseline prices every pick at zero and publishes raw points as
            # value over slot, which looks plausible and is wrong.
            base = payload["draft"].get("baseline", {})
            check(base.get("historical_picks", 0) > 0,
                  "draft value baseline has no historical picks; run ./fantasy-api backfill")

        if "recaps" in payload:
            # Zero recaps is legitimate: a drafted recap stays off the site until it has
            # been read. Anything that is listed must be published and complete.
            for r in payload["recaps"].get("recaps", []):
                check(r.get("status") == "published", "recap for week %s is not published" % r.get("week"))
                check(bool(r.get("paragraphs")), "recap for week %s has no body" % r.get("week"))

    # House rule, enforced rather than remembered.
    for path in list(BUILD.rglob("*.html")) + list(BUILD.rglob("*.json")) + \
                list(BUILD.rglob("*.js")) + list(BUILD.rglob("*.css")):
        text = path.read_text(errors="ignore")
        if EM_DASH in text:
            line = text[:text.index(EM_DASH)].count("\n") + 1
            problems.append("em dash in %s line %d"
                            % (path.relative_to(BUILD), line))

    if problems:
        raise Failure("\n".join("  - %s" % p for p in problems))
    log("validate  passed %d checks across %d data files"
        % (checked[0], len(payload)))


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def promote():
    if not BUILD.exists():
        raise Failure("nothing staged to promote")
    previous = SITE / "dist.previous"
    if previous.exists():
        shutil.rmtree(previous)
    if DIST.exists():
        DIST.rename(previous)
    try:
        shutil.copytree(BUILD, DIST)
    except Exception:
        # Put the good site back rather than leaving nothing served.
        if DIST.exists():
            shutil.rmtree(DIST)
        if previous.exists():
            previous.rename(DIST)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    log("promote  %s is live" % DIST)


COMMANDS = {"build": build, "validate": validate, "promote": promote}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "all"
    order = ["build", "validate", "promote"] if cmd == "all" else [cmd]
    if any(c not in COMMANDS for c in order):
        print(__doc__)
        return 2
    for c in order:
        try:
            COMMANDS[c]()
        except Failure as exc:
            print("\n%s FAILED\n%s" % (c, exc), file=sys.stderr)
            print("\nNothing was promoted. The site already published stays up.",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
