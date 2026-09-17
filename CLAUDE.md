# CLAUDE.md

Working instructions for the `fantasy_api` project. Read this before touching anything.

## What this project is

A read-only Python service that pulls the "Life's Gr8" Yahoo fantasy football league (16 teams, IDP) through the Yahoo Fantasy Sports API, stores a full historical snapshot locally, computes statistics Yahoo does not surface, and rebuilds a static league website every day.

The website is the product. It carries four things:

1. **The Paddock.** Season-long stats rendered as carnival horse races, sixteen teams, sixteen horses, one race per category.
2. **Standings.** Actual records alongside all-play records and the luck gap between them.
3. **The Tuesday recap.** A written weekly recap, published to the site.
4. **The Blotter.** A daily-updating feed of every add, drop, and trade in the league.
5. **Draft Value.** A running board of which players are most outperforming the draft slot they were taken at.

Site regenerates daily. The recap regenerates weekly. Nothing else about the cadence differs.

Background and feature research are in `docs/IDEAS.md`. The recap's requirements are in `docs/PRD-tuesday-recap.md`. Both predate the shift to a daily site and describe the recap as an email newsletter. **The site is now the delivery surface.** Email may return later as a distribution channel for the same rendered recap, but nothing in this project sends mail today. Treat those docs as requirements for the recap's content and voice, not for its delivery.

## Non-negotiables

These exist because violating them produces failures that are expensive to detect and embarrassing to publish.

1. **This project never writes to Yahoo.** Reads only. No roster changes, no adds, no drops, no waiver claims, no trade proposals, no lineup edits, not for Kevin's team and not for anyone else's. The Yahoo client must not implement POST or PUT methods at all. Do not add them speculatively, do not add them behind a flag, and do not add them because a task would be easier with them. If a feature appears to require a write, stop and ask.

2. **The LLM never computes a number.** All statistics are computed in Python, verified, and passed to the model as a structured fact object. The model's only job is turning facts into prose. If a prompt asks the model to "figure out who had the best week," that is a bug.

3. **Snapshot raw responses before parsing.** Every Yahoo API response gets written to `data/raw/` as JSON, keyed by resource and timestamp, before any parsing happens. Yahoo changes response shapes without notice. Historical data that only exists in parsed form is data you will eventually lose.

4. **The database is the source of truth for anything comparative.** Season-long ledgers, streaks, all-play records, draft value, and trophy counts are computed from the local snapshot, never by re-querying Yahoo for prior weeks.

5. **The site is regenerated, never edited.** Every published page is a build artifact. If something on the site is wrong, the fix goes in the data or the template, never in the output. Never hand-edit anything in the build directory.

6. **A bad build never replaces a good one.** The daily job builds to a staging directory, runs validation, and only promotes on success. A failed Yahoo fetch should leave yesterday's site standing, not publish an empty one.

7. **No em dashes anywhere.** Not in code comments, not in docs, not in generated copy. This applies to prompts written for the model as well, which must instruct it accordingly.

8. **Secrets live in `.env` and never in git.** `.env.example` is committed with empty values. Never print a token, refresh token, or app secret to logs or console.

## Pipeline

Four stages, each independently runnable and independently testable.

```
ingest  ->  snapshot  ->  compute  ->  build
```

**ingest** talks to Yahoo. It is the only module allowed to make Yahoo API calls, and it only makes GET requests. It handles OAuth token refresh, retries, backoff, and writing raw JSON to disk.

**snapshot** parses raw JSON into the SQLite schema. Pure transformation, no network calls. Idempotent: re-running it over the same raw files must produce the same database state.

**compute** reads SQLite and produces the fact objects the site renders from. Pure functions, no network, no LLM, no side effects. Highest test coverage requirement in the repo, because every claim on the site traces back here.

**build** renders static pages from computed facts, generates recap prose via the Anthropic API when the week has turned over, validates the output, and promotes it.

## Cadence

**Daily**, every morning:
- Fetch transactions, rosters, player stats, and standings
- Recompute cumulative totals, races, standings, and draft value
- Rebuild and promote the site

**Weekly**, Tuesday, after Monday night is final:
- Everything the daily job does
- Plus: compute the week's matchup facts and awards, generate the recap prose, publish it as that week's recap page, and surface it on the home page

The weekly work is an addition to the daily job, not a separate pipeline. `build` decides whether a recap is due by checking whether the current week's recap already exists in the database.

**Recap review.** The recap is the only generated prose on the site and the only thing that can say something wrong about a real person. For the first three weeks of the season it does not auto-publish. The daily job writes it as a draft, Kevin reads it, and a separate command promotes it. After three consecutive weeks published without edits, auto-publish can be enabled.

## Repo layout

```
fantasy_api/
  CLAUDE.md
  README.md
  .env.example
  pyproject.toml
  docs/
    PRD-tuesday-recap.md
    IDEAS.md
  src/fantasy_api/
    ingest/          # Yahoo client, OAuth, raw fetchers. GET only
    snapshot/        # raw JSON -> SQLite
    compute/         # SQLite -> facts
    build/           # facts -> static site
    cli.py
  data/
    raw/             # gitignored, raw Yahoo JSON
    league.db        # gitignored, SQLite snapshot
  site/
    templates/
    static/
    build/           # gitignored, staging output
    dist/            # gitignored, promoted output, this is what deploys
  races.toml         # race definitions, silks, track config. data not code
  tests/
    fixtures/        # anonymized real responses from prior seasons
```

## Yahoo API reference

Base URL: `https://fantasysports.yahooapis.com/fantasy/v2/`

Append `?format=json`. All requests are GET.

### Keys

- Game key changes every season. Resolve it at runtime from `/users;use_login=1/games;game_codes=nfl` rather than hardcoding.
- League key format is `{game_key}.l.{league_id}`, for example `461.l.123456`.
- Team key format is `{league_key}.t.{team_id}`, team ids are 1 through 16.
- Prior seasons are reachable through the `renew` and `renewed` fields on the league resource. This is how league history is assembled.

### Endpoints used

| Resource | Path | Cadence |
| --- | --- | --- |
| League settings and scoring | `/league/{league_key}/settings` | Once per season |
| Draft results | `/league/{league_key}/draftresults` | Once per season |
| Standings | `/league/{league_key}/standings` | Daily |
| Transactions | `/league/{league_key}/transactions` | Daily |
| All teams | `/league/{league_key}/teams` | Daily |
| Roster including bench | `/team/{team_key}/roster;week={n}` | Daily |
| Weekly scoreboard | `/league/{league_key}/scoreboard;week={n}` | Daily |
| Player stats for a week | `/league/{league_key}/players;player_keys=...;/stats;type=week;week={n}` | Daily |

Settings and draft results are fetched once and cached. Re-fetching them daily is wasted quota.

### Gotchas that will cost you a day each

- **Pending waiver claims and pending trades do not appear in a plain transactions fetch.** They are only visible to involved teams. Filter the transactions collection by type (`waiver`, `pending_trade`) to see them. Relevant because the Blotter should show pending activity, not just completed.
- **Access tokens last one hour.** Refresh tokens persist. The client must transparently refresh and write the new refresh token back to storage.
- **Rate limits are not publicly documented.** Assume they exist and are lower than you would like. Batch player queries using `player_keys` lists rather than looping. One daily pass should be well inside any reasonable limit, which is part of why the cadence is daily rather than hourly.
- **IDP position eligibility is league-specific.** Read eligible positions from league settings. Do not assume standard DL, LB, and DB buckets.
- **Stat categories are league-specific.** Never use generic fantasy point values. Pull the scoring modifiers from the settings resource and score everything with the league's real numbers. This is the whole point of the IDP features.
- **Writes exist in the API and are out of scope here.** Documented in IDEAS.md Part 1 for reference. Do not implement them.

## Data model

SQLite. Core tables:

- `leagues` (league_key, season, name, settings_json)
- `teams` (team_key, league_key, team_id, name, manager_name, silk_json)
- `players` (player_key, name, position, eligible_positions, nfl_team)
- `draft_picks` (league_key, pick_number, round, team_key, player_key, cost)
- `rosters` (league_key, week, team_key, player_key, selected_position, is_starter)
- `player_week_stats` (league_key, week, player_key, stats_json, points)
- `matchups` (league_key, week, team_a_key, team_b_key, points_a, points_b)
- `transactions` (league_key, transaction_key, type, status, timestamp, payload_json)
- `week_facts` (league_key, week, facts_json, computed_at)
- `recaps` (league_key, week, html, generated_at, published_at)
- `builds` (id, started_at, finished_at, status, promoted)

`season` is a first-class dimension everywhere. Prior seasons load into the same database, which is what makes rivalry history, draft value baselines, and backtesting possible.

## Compute layer

Every number on the site comes from here, and every metric has a test built on real fixtures from prior seasons. If a metric has no test, it does not ship.

**Per team, per week:** actual score, optimal score, bench regret, all-play record, highest and lowest scoring starter, highest scoring benched player.

**Cumulative, per team:** every race total (see `races.toml`), all-play record, luck gap, streaks.

**League wide:** week high and low, best defensive performance started, best defensive performance left unstarted anywhere, biggest overperformance and underperformance versus projection, season records in every award category.

**Draft value.** See below.

## Draft value

The question is which players are most outperforming the draft slot they were taken at. Getting this right matters because it is the most argued-about number in any league, and a naive version is easy to dismiss.

**Build an empirical baseline, not a guess.** The backfilled prior seasons give real points-scored-by-pick-number data across multiple drafts. Fit a curve of expected season points as a function of overall pick number, smoothed, and use that as the baseline. This is far more defensible than positional heuristics because it comes from Life's Gr8's own history and its own scoring settings.

**Metric:** `value_over_slot = actual_points_to_date - expected_points_for_pick(pick_number, weeks_elapsed)`

Scale the expectation by weeks elapsed so the board is meaningful in Week 3 and not just in December.

**Required refinements, in priority order:**

1. **Position-adjusted baseline.** A pick 40 quarterback and a pick 40 linebacker have different expected outputs. Fit per position group, falling back to the global curve where a position has too few historical picks to fit.
2. **Undrafted players.** Waiver pickups have no pick number. Assign them a baseline equal to expected points at one slot past the last pick of the draft, which correctly makes a productive waiver add look like an enormous win. This overlaps with the transaction ROI idea and they should share code.
3. **Keepers and auction costs.** If Life's Gr8 uses keepers or auction, pick number is not the right input. Check league settings before assuming a snake draft. The `cost` field on draft results carries auction values when present.
4. **Games missed.** A player who tore an ACL in Week 2 looks like the worst value in league history. That is technically true and not interesting. Show availability alongside the number rather than adjusting it away, so the board distinguishes a bust from an injury.

**Two boards on the site:** biggest steals and biggest busts, each showing player, pick number, actual points, expected points, and the difference. Credit the drafting team, because the point of the board is bragging rights.

The same baseline feeds a Paddock race, "The Claiming Stakes," which ranks teams by total value over slot across their whole roster.

## Site

Static. No backend, no authentication, no database at runtime. The build emits JSON and HTML, and it deploys as files.

**Pages:**
- Home: current standings, the tightest two or three races, the latest recap, recent transactions
- The Paddock: all races, full detail
- Standings: actual, all-play, and luck gap
- Recaps: current and archive
- The Blotter: full transaction feed with pending activity
- Draft Value: steals and busts

**Rules:**
- Mobile first. Sixteen lanes on a phone is the hard constraint that governs the whole design.
- Races count **started players only**, never full rosters. This is a league rules decision, not an implementation detail, and it must not be quietly changed.
- Race definitions, silks, and track geometry live in `races.toml`. Adding or retiring a race is a config change.
- Track position is leader-relative by default: `0.90 * (team_total / leader_total)` with a floor near `0.08`. Min-max normalization sits behind a flag and is not the default, because it exaggerates gaps that are not there.
- Silks are assigned once per manager and used consistently everywhere a team is depicted.
- Every page shows when it was last built. A stale site should be visibly stale rather than quietly wrong.
- Week 1 ships static standings only. Race animation is a fast follow in Weeks 2 through 4. Do not let the scrubber delay the launch.

Full race spec, including the race card and stakes names, is Part 7 of `docs/IDEAS.md`.

## Commands

```
fantasy-api auth                          # one-time OAuth flow, stores refresh token
fantasy-api backfill --season 2023        # pull a full prior season into the snapshot
fantasy-api ingest [--week N]             # fetch from Yahoo, defaults to current week
fantasy-api compute [--week N]            # build and store facts
fantasy-api recap --week N [--draft]      # generate recap prose, draft by default
fantasy-api publish-recap --week N        # promote a drafted recap to the site
fantasy-api build                         # render the static site to site/build
fantasy-api validate                      # check a staged build before promotion
fantasy-api promote                       # move site/build to site/dist
fantasy-api daily                         # the whole chain, this is what cron runs
```

`daily` runs ingest, compute, build, validate, and promote in sequence, and stops at the first failure without promoting. It also generates the week's recap as a draft when one is due.

## Conventions

- Python 3.12, `uv` for dependency management.
- Type hints everywhere. Fact objects are dataclasses or Pydantic models, never bare dicts.
- Tests for `compute/` are mandatory and use real fixtures from prior seasons, not synthetic data.
- Logging over print. Never log token values.
- Every Yahoo fetch function returns raw parsed JSON. Interpretation happens in `snapshot/`.
- The daily job must be safe to run twice in one day.

## Voice rules for recap prose

The narration prompt must enforce all of the following:

- Dry, observational, occasionally sharp. A beat writer who has watched this league for years, not a hype man. Avoid the "behold, ye fantasy warriors" register that every open-source recap bot defaults to. It is funny once and exhausting by Week 4.
- Full sentences and natural storytelling. No punchy sentence fragments, no salesy staccato.
- No em dashes.
- Never invent a statistic, a player, a nickname, or a piece of league history. Only the facts passed in the prompt.
- Never mock a manager for something outside the game. Losing badly is fair material. Nothing else is.
- Two to four sentences per matchup. Brevity is what makes it readable at sixteen teams.

## Out of scope

Do not build these without being asked, even if they seem like natural extensions:

- Any write operation to Yahoo, for any team, under any framing
- Manager logins or any authenticated experience
- Email sending
- Live in-game score updates or any polling faster than daily
- Anything in `docs/IDEAS.md` outside Part 7 and the draft value work described above
