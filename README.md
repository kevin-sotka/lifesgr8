# Life's Gr8

The league site for a sixteen-team head-to-head Yahoo fantasy football league
with custom IDP scoring. Static, mobile first, rebuilt daily, deployed to
GitHub Pages at **lifesgr8.com**.

It runs on live Life's Gr8 data from the read-only Yahoo Fantasy API. See
`docs/DATA-CONTRACT.md` for the nine files the site renders from.

## Run it

```bash
python3 tools/generate_dummy_data.py    # write a fresh demo season
python3 tools/build_site.py all         # build, validate, promote
python3 -m http.server -d site/dist 8000
```

Then open <http://localhost:8000>. No dependencies, no build toolchain, no
package manager. The site is HTML, CSS, and three script files.

Opening `site/dist/index.html` straight off disk will not work, because the
browser blocks the data files on `file://`. Serve the folder.

## What is on it

**The Card.** Home. The three things worth knowing this week, the top of the
table, the week's hardware, the tightest races, the latest recap, recent moves.

**The Paddock.** Season-long stats as carnival horse races. Nine races, sixteen
horses each, silks per manager. Horses are placed relative to the leader, who
sits at ninety percent of the rail. A week scrubber runs the whole season back.
Every race counts started players only.

**Standings.** Actual record, the all-play record it should have produced, and
the luck gap between them. Plus lineup efficiency, form sparklines, power
rankings, and playoff odds from four thousand simulated seasons.

**Draft Value.** Who beat their draft slot, against a baseline fitted from this
league's own draft and this league's own scoring. Steals, busts, weeks missed
shown separately, and the published curve so the board can be argued with.

**The Blotter.** Every add, drop, and trade, with pending waiver claims and
pending trades pinned at the top. Yahoo hides pending activity from a plain
transactions fetch, so most league sites never show it.

**The Form.** Season leaderboards under the league's real scoring, the biggest
single weeks, the best defensive week somebody started next to the best one
somebody benched, the record book, and a sixteen by sixteen head-to-head grid.

**Recaps.** The Tuesday recap, current and archive, with a visible draft state
until it has been read.

## Layout

```
lifesgr8/
  CLAUDE.md                  working instructions, read before touching anything
  races.toml                 race definitions, silks, track config. data not code
  docs/DATA-CONTRACT.md      the nine JSON files the site renders from
  tools/
    generate_dummy_data.py   writes a full demo season
    build_site.py            build, validate, promote
  site/
    templates/index.html     the shell
    static/css, static/js    one stylesheet, four scripts
    static/data/*.json       the contract. dummy today, Yahoo tomorrow
    build/                   staging, gitignored
    dist/                    promoted output, gitignored, this is what deploys
  .github/workflows/pages.yml
```

## How it deploys

Push to `main`. The workflow runs `build_site.py all` and publishes `site/dist`
to GitHub Pages. `site/CNAME` points the deployment at lifesgr8.com; set the
custom domain once in the repository's Pages settings and add the DNS records
your registrar needs.

`site/dist` is a build artifact and is not committed. **Never hand-edit anything
in it.** If something on the site is wrong, the fix goes in the data or the
template.

A build only replaces the live site if it validates. `validate` checks the data
files parse, the team counts agree across files, every race has one runner per
team and all of them are on the track, every referenced asset exists, and there
are no em dashes anywhere in the output. A failed build promotes nothing and
leaves yesterday's site standing.

## Connecting Yahoo

Life's Gr8 is connected. Credentials live in `.env` (gitignored) and the refresh
token in `data/.yahoo_tokens.json` (gitignored, owner-only). From a fresh clone:

```bash
./fantasy-api auth        # sign in to Yahoo once
./fantasy-api check       # Life's Gr8 and its prior seasons, nothing else
./fantasy-api backfill    # prior 16-team drafts, for the draft value baseline
./fantasy-api daily       # ingest, snapshot, compute, build, validate, promote
```

**Only Life's Gr8 is ever read.** The client refuses any request naming another
league on the account, and the account-wide league list needs an explicit
`check --discover`. Tests enforce both.

**Scoring is verified, not assumed.** `snapshot` recomputes every player's points
from raw stats and the league's modifiers, including the stacking yardage bonuses,
and compares each one to Yahoo's own total. A disagreement in a final week stops
the build. For four days after a week ends, NFL stat corrections can make Yahoo
briefly disagree with itself, so those weeks warn and get refetched instead.

**The weekly stat headline** uses a second read-only source: public nflverse
play-by-play, FTN charting, and ffopportunity expected points, downloaded from
GitHub only when upstream changes (`./fantasy-api nflverse`, also part of
`daily`). Four stats rotate one a week, defined in `stats.toml`. If nflverse is
unreachable the site still builds; the headline uses the last files on disk.

**The Purse** is the commissioner's weekly high scores, which he pays out at the end
of the season. The top four each week are worth $40, $20, $10 and $5, set in
`league.toml`. This week's four show on The Card, the running tally and the week by
week log live on Standings, and Doc names them in every recap.

**Rules Yahoo does not carry** (the commissioner runs matchups and the playoff
tree by hand) live in `league.toml`. It currently says top 8 make a winners
bracket in weeks 14 to 17. Replace it when the real rules arrive.

## Deploying

`.github/workflows/pages.yml` runs every morning at **5:00am Pacific**: tests, then
`./fantasy-api daily`, then deploys `site/dist` to GitHub Pages. Cron is UTC and
Pacific shifts with daylight time, so two schedules are registered and a gate keeps
the one that is 5am in Los Angeles that day. It also runs on every push to `main`,
and by hand from the Actions tab.

**Tuesdays**, the same run writes Doc's recap of the finished week and opens a pull
request with the full text in its description. **Merging the pull request publishes
the recap.** Edit the file on the branch first if anything is off. Every number in a
recap is checked against the week's facts before the pull request opens, along with
em dashes, years, manager names, and a paragraph per matchup; a draft that fails
twice is not opened at all. After three clean weeks, `auto_publish = true` in
`league.toml` skips the pull request. To write one on another day, run the workflow
by hand with "recap" ticked, or locally: `./fantasy-api recap`.

Repository settings the workflow needs:

- secrets `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REFRESH_TOKEN`
- secret `ANTHROPIC_API_KEY` (recap only; without it the recap step skips)
- secret `DOC_VOICE`, the contents of `docs/doc_voice.md`, which is private and not in git
- variable `LEAGUE_ID` (33009 for 2026)
- Pages set to GitHub Actions, custom domain lifesgr8.com
- Actions allowed to create pull requests

Tests: `python3 -m unittest discover -s tests`.

## Demo data

`tools/generate_dummy_data.py` still writes a full fake season, which is useful
for working on the design mid-week. It refuses to overwrite real Yahoo data
unless passed `--overwrite-real-data`. The demo banner shows only when
`league.json` says `"source": "dummy"`.

The one thing to keep in sync: the track positioning formula exists in both
`tools/generate_dummy_data.py` and `site/static/js/charts.js`, because the week
scrubber re-derives positions in the browser. `docs/DATA-CONTRACT.md` says so
again where it matters.

**This project never writes to Yahoo.** Reads only, GET only, for any team, under
any framing.

## Design notes

Sixteen teams is the constraint that governs everything. Sixteen lanes have to
work on a phone, and sixteen entities cannot be told apart by colour, so identity
is carried on three channels at once: eight hues that pass the colourblind
separation and contrast gates in both light and dark mode, two silk patterns, and
a three-letter code rendered beside every silk without exception. Every race lane
also prints its own value, so nothing on the site depends on hue to be read.

Light and dark are both selected, not flipped. The dark palette is the same eight
hues stepped for the dark surface and validated against it separately.
