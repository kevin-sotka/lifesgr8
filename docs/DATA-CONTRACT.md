# The data contract

The site is static. It loads nine JSON files from `site/static/data/` and renders
everything from them. It has no backend, no database at runtime, and no idea
where the numbers came from.

That is the whole integration surface (ten files, with `newage.json`). `src/fantasy_api/compute/` writes these
nine files from the snapshot database. `tools/generate_dummy_data.py` writes the
same shapes from a fake season, for design work.

```
ingest -> snapshot -> compute -> [these nine files] -> build
```

## The rule that matters most

**Nothing on the site computes a statistic.** Every number rendered is read
straight out of these files. All-play records, luck gaps, playoff odds, race
positions, and value over slot are computed in Python, tested in Python, and
arrive here finished. The one exception is documented below under `races.json`,
and it exists for a specific reason.

`team_index` is the join key everywhere: an integer from 0 to 15, stable for a
season, indexing into `league.teams`. Player references are Yahoo `player_key`
strings and resolve through `players.json`.

---

## league.json

Identity, settings, and the sixteen teams. Loaded first; everything else assumes it.

| Field | Notes |
| --- | --- |
| `league_key`, `name`, `season`, `num_teams` | From the league resource. |
| `current_week`, `completed_week` | `completed_week` is what the site reports on. |
| `regular_season_weeks`, `playoff_teams`, `playoff_weeks` | From `league.toml`, because the commissioner runs playoffs outside Yahoo. Drives the cut line and the simulations. |
| `roster_slots` | Ordered list of starting slots, for example `["QB","RB","RB",...]`. |
| `scoring` | The league's real modifiers, keyed by stat. Never a generic value. |
| `stat_labels` | Display names for stat keys. |
| `teams[]` | `team_index`, `team_key`, `team_id`, `name`, `manager`, `code`, `silk`. |
| `built_at` | ISO 8601. Rendered in the footer so a stale site is visibly stale. |
| `source` | `"yahoo"` for real data, `"dummy"` for the demo season, which shows a banner. |

### silk

```json
{ "hue": "blue", "light": "#2a78d6", "dark": "#3987e5", "pattern": "solid" }
```

Sixteen teams cannot be told apart by colour, so a silk carries three channels:
eight validated hues, two patterns (`solid` or `hoops`), and the three-letter
`code`, which the UI renders beside every silk without exception. `light` and
`dark` are different steps of the same hue, each validated against the surface
it actually renders on. Assign silks once per manager and never reassign them:
colour follows the team, never its rank.

---

## standings.json

`rows[]`, one per team, plus `head_to_head`.

Actual record (`wins`, `losses`, `ties`, `pf`, `pa`, `ppg`, `high`, `low`), the
all-play record (`all_play_wins`, `all_play_losses`, `all_play_pct`), and the gap
between them (`expected_wins`, `luck`). Also `efficiency` (points as a share of
the best available lineup), `streak`, `results[]`, `last3_ppg`, `power_rank`,
`power_score`, `playoff_pct`, `bye_pct`, and `weekly[]` for the sparkline.

**All-play**: each week, score every team against all fifteen it did not play.
Over eleven weeks that is 165 games. `expected_wins = all_play_pct * games` and
`luck = actual_wins - expected_wins`.

**head_to_head**: a 16x16 array. `grid[i][j]` is `{w, l, pf, pa}` for team `i`
against team `j`, or `null` if they have not met.

---

## races.json

`track` carries the positioning config read from `races.toml`. `races[]` carries
one entry per race with `id`, `name`, `blurb`, `stat`, `unit`, `direction`
(`high` or `low`), `runners[]`, `leader`, `gap`, `gap_pct`, and `splits`.

`runners[]` gives each team its `value`, its `place`, its `jockeys`, and its `position`.

`jockeys` is `{"top": [{"name": "D. Henry", "value": 144}, ...], "more": 1}`: the
three started players who contributed most to that lane's total, and how many
others contributed. `split_jockeys[w][t]` is the compact per-week version,
`{"names": [...], "more": n}`, for the scrubber. The Regret Steeplechase lists the
benched players who belonged in the best lineup; The Claiming Stakes lists the
rostered players furthest above their draft slot.

`position` is a
fraction of the track computed leader-relative as
`max(floor, leader_scale * value / leader_value)`.

**Races count started players only.** The IDP races (sacks, solo tackles,
takeaways) count the two `D` slots; team defense runs separately in The Fortress
Stakes. A linebacker who posted fourteen tackles on
a bench did not run in that race. This is a league rules decision and must not be
quietly changed.

**The one client-side computation.** `splits[w][t]` is team `t`'s cumulative
total through week `w + 1`, and the week scrubber re-derives positions from it in
the browser. The formula lives in two places as a result: `race_positions()` in
the generator and `LG.chart.positions()` in `site/static/js/charts.js`. They must
stay identical, because a scrubbed week has to land the horses exactly where the
season view would have put them. If the positioning rule changes, change both.

---

## season.json

`weeks[]` carries per-week `matchups[]` and `awards`. Awards are `gold_cup`,
`sacko`, `blowout`, `nailbiter`, `regret`, and `trench`, each naming a
`team_index` and a value, some naming a `player_key`.

`team_weeks[]` is one row per team per week: `points`, `optimal`, `regret`, and
the `best_starter` / `worst_starter` / `best_bench` / `best_idp` references that
feed the record book.

---

## draft.json

`baseline` publishes the expected-points curve so the board can be argued with:

```json
{ "method": "...", "global": [[17.0, 190.6], [41, 172.3], ...],
  "by_position": { "LB": [[...]] }, "undrafted_slot": 257 }
```

Each pair is `[pick_number, expected_season_points]`, fitted from prior Life's
Gr8 drafts with the current team count (2022 to 2025 for sixteen teams), using
each season's own league scoring. Expected points to date are that value times
weeks played over 17. Expected value at an
arbitrary pick is linear interpolation between the knots, held flat outside the
fitted range.

**The curve is binned medians, not a power law.** A power law fitted in log-log
space has to go somewhere as the pick number approaches one, and where it goes is
a nine-hundred-point expectation for the first overall pick. Every early pick who
merely had a good season then publishes as a historic bust. Median production per
region of the board cannot do that: the expectation for pick one is the median of
what picks one through sixteen actually did.

`steals`, `busts`, and `all` carry per-player rows with `pick_number` (null when
undrafted), `points`, `expected`, `value_over_slot`, `games`, and `missed`.
`missed` is displayed and never used to adjust `value_over_slot`, so the board
can tell a bust apart from a torn knee instead of quietly turning one into the
other. `team_totals` feeds The Claiming Stakes.

---

## blotter.json

`transactions[]`, newest first. Each has `id`, `type`, `status`, `week`,
`timestamp`, and `teams[]`. Adds and drops carry `added`, `dropped`, `source`,
and `faab` (always null: Life's Gr8 uses rolling waiver priority). Commissioner
moves carry `note`. Trades carry `sends[]` of `{team_index, player_key}`, read as
"this team sends this player".

**Pending activity is the point.** `status: "pending"` rows pin to the top.
Yahoo does not return pending waiver claims or pending trades from a plain
transactions fetch: filter the collection by type (`waiver`, `pending_trade`) to
get them. Most league sites never show them, which is exactly why this one does.

**One real limit.** Yahoo only returns pending activity for teams the authorized
account manages, so the Blotter can show pending claims and trades for Kevin's
team and nobody else's.

---

## leaders.json

`season` holds one array per category (`points`, `sacks`, `tackles`,
`takeaways`, `touchdowns`, `pass_yd`, `rush_yd`, `rec_yd`). `single_week` holds
the biggest individual weeks with a `started` flag. `idp_week_started` and
`idp_week_benched` are the pair this league argues about: the best defensive week
somebody started, and the best defensive week somebody left on a bench.

---

## newage.json

The weekly stat headline, from nflverse data (play-by-play, FTN charting) and the
ffopportunity expected points model. `stats.toml` defines the four stats, their
minimums, and the rotation.

| Field | Notes |
| --- | --- |
| `rotation` | Stat ids in order. `scheduled` is `rotation[(week - 1) % 4]`. |
| `headline` | The stat actually headlining. Normally `scheduled`; if that stat has no qualifier yet (a data file lagging), the next stat in line takes the week. |
| `headline_fact` | `{player_key, team_index, value, display, sentence}`, assembled from finished values. |
| `data_through_week` | The last NFL week present in the downloaded play-by-play. |
| `boards[id]` | `name`, `short`, `unit`, `format` (`percent` or `signed`), `blurb`, `how`, `source`, `qualifiers` (all NFL players who met the minimum), `rostered_qualifiers`, `leaders`, `trailers`. |

Each board row: `player_key`, `name`, `short`, `position`, `nfl_team`,
`team_index`, `value` (a 0 to 1 share, or points per game), `nfl_rank` among all
qualifying NFL players, `tied`, and `detail` (the sample behind the value).
Ranks are competition ranks: equal values share a rank, and larger samples are
listed first.

**Points Over Expected** rescales ffopportunity's components with Life's Gr8's
modifiers on both sides, yardage bonuses excluded from both. **First Read Share**
counts targets with FTN read code `1` over targets with any read code (`0` means
not charted). **Explosive Run Rate** is designed carries of 15+ yards. **Red Zone
Opportunity Share** is targets plus designed carries at or inside the 20 over the
team's total.

Yahoo players are joined to NFL GSIS ids through the DynastyProcess id map, then
by normalized name plus position (and team when a name is shared). The method is
stored per player in `yahoo_gsis`.

## players.json

`players` is an object keyed by `player_key`, each value `{name, position,
nfl_team, bye, team_index, pick_number, round}`. Every other file refers to
players by key and resolves through this one.

---

## recaps.json

`recaps[]`, newest first, each with `week`, `headline`, `lede`, `paragraphs[]`,
`status`, and `generated_at`. Only published recaps are listed, read from
`data/recaps/`, so an empty list is normal early in a season and the site shows
a holding card.

`status` is `"draft"` or `"published"`. The site renders a draft with a visible
"awaiting review" chip. For the first three weeks of a season the recap does not
auto-publish: the daily job writes it as a draft, it gets read, and
`publish-recap` promotes it. After three consecutive weeks published without
edits, auto-publish can be turned on.

The recap is the only generated prose on the site and the only thing that can say
something wrong about a real person. In the dummy data it is composed by a small
templater from the same finished fact object the model will receive, which is why
the demo recap can never contradict the demo data. Swapping the templater for the
Anthropic call changes nothing else in the pipeline.

---

## Validation

`tools/build_site.py validate` refuses to promote a build that fails any of
these, so a bad build never replaces a good one:

- every data file present and parseable
- team count matches `num_teams`, and matches the standings row count
- every team has a three-letter code and a silk colour for both modes
- every race has one runner per team, all on the track (`0 < position <= 1`)
- every standings row has at least one game played
- every asset referenced by `index.html` exists in the build
- `built_at` parses as a timestamp
- every listed recap is published and has a body
- **no em dashes anywhere in the output**, which is a house rule and is therefore
  enforced rather than remembered
