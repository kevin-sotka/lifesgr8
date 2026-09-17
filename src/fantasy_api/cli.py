"""
fantasy-api: the command line.

    ./fantasy-api auth              one-time Yahoo authorization
    ./fantasy-api check             prove access works and list Life's Gr8's seasons
    ./fantasy-api ingest [--week N] snapshot today's league data to data/raw/
    ./fantasy-api backfill          prior Life's Gr8 drafts, for the draft baseline
    ./fantasy-api snapshot          data/raw/ -> data/league.db, checked against Yahoo
    ./fantasy-api nflverse          public NFL play-by-play for the weekly stat headline
    ./fantasy-api compute           data/league.db -> site/static/data/*.json
    ./fantasy-api daily             all of the above, then build, validate, promote
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
import logging
import sys
import urllib.parse
import webbrowser
from typing import Any, Dict, List, Optional

from .config import INGEST_STATE, LEAGUE_TOML, RAW, ROOT, ConfigError, load_settings, read_toml
from .corrections import is_final
from .ingest import auth, fetch
from .ingest.client import PermissionProblem, YahooClient, YahooError

log = logging.getLogger("fantasy_api")

PERMISSION_HELP = """
Yahoo accepted the login but refused the Fantasy Sports read. That is the exact
problem Yahoo's email describes. Check, on https://developer.yahoo.com/apps/ :
  1. The app lists "Fantasy Sports" under API Permissions, set to Read.
  2. The Client ID in .env belongs to THAT app, not the older one.
  3. That new Client ID was submitted on Yahoo's application confirmation form.
Then run ./fantasy-api auth again, since a token issued to the old app stays useless.
"""


def _extract_code(text: str) -> str:
    text = text.strip()
    if "code=" in text:
        return urllib.parse.parse_qs(urllib.parse.urlparse(text).query).get("code", [""])[0]
    return text


def cmd_auth(args: argparse.Namespace) -> int:
    settings = load_settings()
    url = auth.authorize_url(settings)
    print("\n1. Sign in to Yahoo and approve access at:\n\n   %s\n" % url)
    if not args.no_browser:
        webbrowser.open(url)
    if settings.redirect_uri == "oob":
        print("2. Yahoo will show a short code. Paste it here.")
    else:
        print("2. Your browser will land on %s (the page may fail to load, that is fine)."
              % settings.redirect_uri)
        print("   Copy the whole address from the address bar and paste it here.")
    code = _extract_code(input("\n   Code or URL: "))
    if not code:
        print("No code found.", file=sys.stderr)
        return 1
    auth.exchange_code(settings, code)
    print("\nAuthorized. Tokens saved to data/ (gitignored, owner-only permissions).")
    print("Next: ./fantasy-api check")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings()

    if args.discover or not settings.league_id:
        # Opt-in only: this is the one request that sees every league on the account.
        client = YahooClient(settings, allow_discovery=True)
        try:
            leagues = fetch.my_nfl_leagues(client)
        except PermissionProblem as exc:
            print(str(exc), file=sys.stderr)
            print(PERMISSION_HELP, file=sys.stderr)
            return 2
        print("\nNFL leagues on this account (--discover):\n")
        for lg in leagues:
            print("  %s  %-28s %s" % (lg["season"], lg["name"], lg["league_key"]))
        print("\nSet LEAGUE_ID in .env to the number after '.l.' for this season's Life's Gr8.")
        return 0

    client = YahooClient(settings)
    try:
        league_key = _league_key(client, settings.league_id)
        chain = fetch.season_chain(client, league_key)
    except PermissionProblem as exc:
        print(str(exc), file=sys.stderr)
        print(PERMISSION_HELP, file=sys.stderr)
        return 2
    except (YahooError, ConfigError) as exc:
        print("check failed: %s" % exc, file=sys.stderr)
        return 1

    print("\nFantasy Sports access works. %s and its prior seasons:\n" % chain[0]["name"])
    for lg in chain:
        print("  %s  %-16s %s teams" % (lg["season"], lg["league_key"], lg["num_teams"]))
    return 0


def _league_key(client: YahooClient, league_id: Optional[str]) -> str:
    if not league_id:
        raise ConfigError("LEAGUE_ID is not set in .env. Run ./fantasy-api check --discover to find it.")
    key = "%s.l.%s" % (fetch.current_game_key(client), league_id)
    client.allow_league(key)
    return key


def _already_snapshotted(path: str) -> bool:
    from .ingest.client import _snapshot_name
    folder = RAW / _snapshot_name(path)
    return folder.exists() and any(folder.glob("*.json"))


def _my_team_key(teams_raw: Any) -> Optional[str]:
    for entry in fetch._collection(teams_raw, "teams"):
        team = fetch._flatten(entry.get("team", entry))
        if str(team.get("is_owned_by_current_login")) == "1":
            return team.get("team_key")
    return None


def _player_keys(roster_raw: Any) -> List[str]:
    keys = []
    for entry in fetch._collection(roster_raw, "players"):
        key = fetch._flatten(entry.get("player", entry)).get("player_key")
        if key:
            keys.append(key)
    return keys


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(INGEST_STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    INGEST_STATE.parent.mkdir(parents=True, exist_ok=True)
    INGEST_STATE.write_text(json.dumps(state, indent=1, sort_keys=True))


def cmd_ingest(args: argparse.Namespace) -> int:
    """
    Daily fetch. Safe to run twice in a day: everything lands as a new timestamped
    snapshot and snapshot/ takes the latest copy of each resource.

    Completed weeks are fetched once (rosters as they were set, final stats) and
    remembered in data/ingest_state.json. The current week is refetched every run.
    On a fresh machine, such as a CI runner, the state file is absent and every
    completed week is fetched again, which is slower but always correct.
    """
    settings = load_settings()
    client = YahooClient(settings)
    rules = read_toml(LEAGUE_TOML)
    reg_end = int(rules.get("season", {}).get("regular_season_end_week", 13))
    calls_before = client.request_count
    try:
        league_key = _league_key(client, settings.league_id)
        state = _load_state()
        # A week is skipped only if it was recorded as final with its end date and
        # that date is still past the correction window. Anything else, including
        # state written by an older version of this command, gets refetched.
        recorded = state.get(league_key, {}).get("final_weeks", {})
        today = date.today()
        final_ends = {int(w): end for w, end in recorded.items() if is_final(end, today)}
        done = set(final_ends)

        # Settings and draft results are fetched once per season and cached.
        settings_path = "league/%s/settings" % league_key
        if args.refresh_static or not _already_snapshotted(settings_path):
            fetch.settings(client, league_key)
        draft_path = "league/%s/draftresults" % league_key
        draft_raw = (fetch.draft_results(client, league_key)
                     if args.refresh_static or not _already_snapshotted(draft_path)
                     else _latest_raw(draft_path))
        drafted = fetch.draft_player_keys(draft_raw)

        league_raw = client.get("league/%s" % league_key)
        current = args.week or fetch.current_week(league_raw)
        if not current:
            raise YahooError("Could not determine the current week; pass --week N")

        fetch.standings(client, league_key)
        teams_raw = fetch.teams(client, league_key)
        fetch.transactions(client, league_key)
        mine = _my_team_key(teams_raw)
        if mine:
            fetch.pending_transactions(client, league_key, mine)

        # Scoreboards for the whole regular season: past weeks give results, future
        # weeks give the schedule the commish has set so far, for the playoff odds.
        statuses: Dict[int, Any] = {}
        for week in range(1, max(current, reg_end) + 1):
            if week in done:
                continue
            statuses[week] = fetch.scoreboard_status(fetch.scoreboard(client, league_key, week))

        fetched_weeks = []
        for week in range(1, current + 1):
            if week in done:
                continue
            rostered: Dict[str, None] = {}
            for team_key in fetch.team_keys(teams_raw):
                for key in _player_keys(fetch.roster(client, team_key, week)):
                    rostered[key] = None
            # Drafted players are included even after they are dropped, because a
            # bust who was cut in Week 3 still belongs on the draft value board.
            wanted = list(rostered) + [k for k in drafted if k not in rostered]
            fetch.player_week_stats(client, league_key, wanted, week)
            fetched_weeks.append(week)
            week_statuses, week_end = statuses.get(week, ([], None))
            # Finished is not final. Keep refetching until stat corrections are in.
            if (week_statuses and all(s == "postevent" for s in week_statuses)
                    and is_final(week_end, today)):
                done.add(week)
                final_ends[week] = week_end

        state[league_key] = {"final_weeks": {str(w): final_ends[w] for w in sorted(done)},
                             "current_week": current}
        _save_state(state)
    except PermissionProblem as exc:
        print(str(exc), file=sys.stderr)
        print(PERMISSION_HELP, file=sys.stderr)
        return 2
    except (YahooError, ConfigError, auth.AuthError) as exc:
        print("ingest failed: %s" % exc, file=sys.stderr)
        return 1

    print("Ingested %s: week(s) %s refreshed, final weeks %s, %d Yahoo requests."
          % (league_key, fetched_weeks or "none", sorted(done) or "none",
             client.request_count - calls_before))
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """
    Prior Life's Gr8 drafts and what those picks went on to score, for the draft
    value baseline. Seasons are reached only through the league's own renew chain,
    and only seasons with the current team count are used, because pick 40 in a
    twelve-team draft is not pick 40 in a sixteen-team one.

    A finished season never changes, so each one is fetched once.
    """
    settings = load_settings()
    client = YahooClient(settings)
    try:
        league_key = _league_key(client, settings.league_id)
        chain = fetch.season_chain(client, league_key)
        size = chain[0]["num_teams"]
        wanted = [l for l in chain[1:] if l["num_teams"] == size]
        if args.seasons:
            wanted = wanted[: args.seasons]
        for lg in wanted:
            key = lg["league_key"]
            season_path = "league/%s/player_stats/season" % key
            if _already_snapshotted(season_path) and not args.refresh:
                print("  %s %s already backfilled" % (lg["season"], key))
                continue
            draft = fetch.draft_results(client, key)
            fetch.settings(client, key)
            picks = fetch.draft_player_keys(draft)
            fetch.player_season_stats(client, key, picks)
            print("  %s %s: %d picks" % (lg["season"], key, len(picks)))
    except PermissionProblem as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (YahooError, ConfigError, auth.AuthError) as exc:
        print("backfill failed: %s" % exc, file=sys.stderr)
        return 1
    print("Backfilled %d prior %d-team seasons in %d Yahoo requests."
          % (len(wanted), size, client.request_count))
    return 0


def _latest_raw(path: str) -> Any:
    from .ingest.client import _snapshot_name
    files = sorted((RAW / _snapshot_name(path)).glob("*.json"))
    return json.loads(files[-1].read_text()) if files else {}


def _current_league_key() -> str:
    """The league key ingest last resolved, so snapshot and compute need no network."""
    state = _load_state()
    settings = load_settings()
    keys = [k for k in state if k.endswith(".l.%s" % settings.league_id)]
    if not keys:
        raise ConfigError("No ingested league found. Run ./fantasy-api ingest first.")
    return sorted(keys)[-1]


def cmd_snapshot(args: argparse.Namespace) -> int:
    from . import snapshot
    league_key = _current_league_key()
    conn = snapshot.build(league_key)
    problems = snapshot.verify(conn, league_key)
    ends = snapshot.week_ends(conn, league_key)
    today = date.today()
    hard = 0
    for week in sorted(problems):
        final = is_final(ends.get(week), today)
        label = "FAIL" if final else "warn"
        print("%s week %d: %d scores disagree with Yahoo%s" % (
            label, week, len(problems[week]),
            "" if final else " (inside the stat correction window, will refetch)"))
        for line in problems[week][: (10 if args.verbose else 3)]:
            print("     " + line)
        hard += len(problems[week]) if final else 0
    counts = {t: conn.execute("SELECT COUNT(*) FROM %s WHERE league_key=?" % t,
                              (league_key,)).fetchone()[0]
              for t in ("teams", "draft_picks", "rosters", "player_week_stats", "matchups", "transactions")}
    conn.close()  # compute opens its own connection; never leave two writers on one file
    print("snapshot %s -> data/league.db  %s" % (league_key, counts))
    if hard:
        print("Scoring does not reproduce Yahoo for a final week. Not safe to build.", file=sys.stderr)
        return 1
    return 0


def cmd_compute(args: argparse.Namespace) -> int:
    from . import compute, snapshot
    league_key = _current_league_key()
    conn = snapshot.connect()
    try:
        sizes = compute.run(conn, league_key)
    finally:
        conn.close()
    total = sum(sizes.values())
    print("compute %s -> site/static/data/  %s  (%.0f KB)"
          % (league_key, ", ".join("%s %.0fK" % (k, v / 1024) for k, v in sizes.items()), total / 1024))
    return 0


def cmd_recap(args: argparse.Namespace) -> int:
    """Write the week's recap in Doc's voice, if it has not been written yet."""
    from . import recap, snapshot
    league_key = _current_league_key()
    conn = snapshot.connect()
    newage_path = ROOT / "site" / "static" / "data" / "newage.json"
    newage = json.loads(newage_path.read_text()) if newage_path.exists() else None
    if args.facts:
        from .compute import Season
        week = args.week or Season(conn, league_key).completed_week
        print(json.dumps(recap.facts(conn, league_key, week, newage), indent=1))
        return 0
    try:
        path = recap.generate(conn, league_key, week=args.week, newage=newage, force=args.force)
    except Exception as exc:
        print("recap failed: %s" % exc, file=sys.stderr)
        return 1
    finally:
        conn.close()
    if path is None:
        print("recap: nothing to write (week not complete, or already written)")
        return 0
    rel = path.relative_to(ROOT)
    print("recap written: %s" % rel)
    # A line CI can read to know what to open a pull request for.
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write("recap_path=%s\n" % rel)
    return 0


def cmd_nflverse(args: argparse.Namespace) -> int:
    """Public play-by-play, FTN charting, and expected points, for the stat headline."""
    from .ingest import nflverse
    season = args.season or int(date.today().year if date.today().month >= 3 else date.today().year - 1)
    try:
        results = nflverse.fetch_all(season)
    except Exception as exc:  # network, rate limit, or a file not published yet
        print("nflverse fetch failed: %s" % exc, file=sys.stderr)
        return 1
    print("nflverse %d: %s" % (season, ", ".join(
        "%s %s" % (k, "updated" if new else "unchanged") for k, (_, new) in results.items())))
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    """
    The whole chain, which is what the schedule runs: ingest, nflverse, snapshot
    (verified against Yahoo), compute, then build, validate, and promote.

    Each stage runs as its own process, exactly as it would by hand. Network
    stages and database stages never share a process, a stage's failure is its
    own exit code, and a failure before promote leaves yesterday's site standing.
    nflverse is the one stage allowed to fail: it only feeds the stat headline.
    """
    import subprocess

    def run(*stage: str) -> int:
        cmd = [sys.executable, "-m", "fantasy_api.cli"] + (["-v"] if args.verbose else []) + list(stage)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        return subprocess.call(cmd, env=env, cwd=str(ROOT))

    ingest = ["ingest"] + (["--week", str(args.week)] if args.week else []) + \
             (["--refresh-static"] if args.refresh_static else [])
    if run(*ingest):
        print("daily stopped at ingest. Nothing was promoted.", file=sys.stderr)
        return 1
    if run("nflverse"):
        print("continuing with the nflverse files already on disk", file=sys.stderr)
    for stage in ("snapshot", "compute"):
        code = run(stage)
        if code:
            print("daily stopped at %s. Nothing was promoted." % stage, file=sys.stderr)
            return code
    return subprocess.call([sys.executable, str(ROOT / "tools" / "build_site.py"), "all"], cwd=str(ROOT))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="fantasy-api", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="one-time Yahoo authorization")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("check", help="verify access to Life's Gr8 and list its seasons")
    p.add_argument("--discover", action="store_true",
                   help="list every NFL league on the account, to find LEAGUE_ID")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("backfill", help="prior Life's Gr8 drafts, for the draft value baseline")
    p.add_argument("--seasons", type=int, help="limit to the N most recent prior seasons")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("snapshot", help="raw JSON -> data/league.db, verified against Yahoo")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("daily", help="ingest, snapshot, compute, build, validate, promote")
    p.add_argument("--week", type=int)
    p.add_argument("--refresh-static", action="store_true")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("recap", help="write the week's recap in Doc's voice")
    p.add_argument("--week", type=int)
    p.add_argument("--force", action="store_true", help="rewrite even if the recap exists")
    p.add_argument("--facts", action="store_true", help="print the fact object and stop")
    p.set_defaults(func=cmd_recap)

    p = sub.add_parser("nflverse", help="download public play-by-play for the stat headline")
    p.add_argument("--season", type=int)
    p.set_defaults(func=cmd_nflverse)

    p = sub.add_parser("compute", help="data/league.db -> the site's JSON files")
    p.set_defaults(func=cmd_compute)

    p = sub.add_parser("ingest", help="snapshot league data to data/raw/")
    p.add_argument("--week", type=int)
    p.add_argument("--refresh-static", action="store_true",
                   help="refetch settings and draft results, normally cached per season")
    p.set_defaults(func=cmd_ingest)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (ConfigError, auth.AuthError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
