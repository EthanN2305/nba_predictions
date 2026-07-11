# Phase 1 → Phase 2 Handoff

> Paste this file (plus `docs/00-PROJECT-OVERVIEW.md` and `docs/02-phase-feature-engineering.md`)
> at the start of the Phase 2 conversation.

## What was built

Phase 1 (historical data collection) is complete: schemas, game-index builder,
rate-limited resumable play-by-play harvester, event→GameState parser, validation
suite, and a CLI. 63 pytest tests, all green; `wp_engine` package coverage 90%.

| File | Contents |
|------|----------|
| `src/wp_engine/schemas.py` | Canonical `GameState` + `GameRecord` Pydantic v2 models |
| `src/wp_engine/collect.py` | `build_game_index`, `harvest_pbp`, `parse_game`, `parse_season`, `validate`, CLI (`python -m wp_engine.collect …`) |
| `tests/` | 6 test files (see README's Testing section) |
| `tests/fixtures/` | 3 committed real games: `0022300061` (DEN home win), `0022300062` (GSW home loss), `0022300083` (SAS OT win) + fixture `game_index.parquet` |

## ⚠️ Deviation: PlayByPlayV2 → PlayByPlayV3

The phase docs specify `nba_api...playbyplayv2`, but **the NBA stats API no longer
returns PlayByPlayV2 data** (empty JSON; nba_api issue #591, the endpoint is
deprecated in nba_api ≥1.10). Everything targets **PlayByPlayV3**:

- Clock is ISO-8601 duration (`"PT11M23.00S"`) — `collect.parse_clock` handles it and
  is directly reusable for the Phase 4 live endpoint (same format).
- Explicit `scoreHome`/`scoreAway` columns (empty strings on non-scoring events →
  forward-filled; no `"VISITOR - HOME"` string splitting).
- `actionType`/`subType` are strings (`"Made Shot"`, `"Foul"`/`"Shooting"`), not
  EVENTMSGTYPE codes.
- Steals/blocks are separate companion rows with empty `actionType` (no-ops for state).
- **V3 `actionNumber` is NOT chronological**: subs/amendments are logged late with
  earlier clocks. `parse_game` sorts events by `(period, clock desc, actionNumber)`.
  Do the same in Phase 4's live adapter if events arrive out of order.
- Timeout rows carry `teamId=0`; attribution comes from the `location` column
  (`"h"`/`"v"`). Team rebounds carry the team id in `personId`.

## Schema decisions (respect in all later phases)

- `GameState` matches the overview exactly. One row per event, state **after** the
  event resolves.
- `seconds_remaining_total`: regulation = seconds left in period + 720 × remaining
  periods; **overtime = seconds left in the current OT only** (future OTs unknowable).
  Phase 2 must add `is_overtime` handling per its docs.
- **Bonus semantics:** `home_in_bonus = (away_team_fouls_period >= 5)` — i.e. the HOME
  team shoots FTs on the next common foul. The docs' shorthand (`in_bonus = fouls >= 5`)
  was ambiguous; this is the semantically meaningful reading. The last-2-minutes bonus
  rule is NOT modeled (deferred).
- `possession`: 1 home / −1 away / 0 unknown. Inference rules are documented in
  `parse_game`'s docstring. Real-game coverage is well above the 70% requirement.
- `home_timeouts_remaining`/`away_timeouts_remaining`: decrement from 7; nullable
  Int64 — becomes `None`/NA if tracking turns inconsistent. Impute + flag in Phase 2.
- Labels: `home_win` (bool) is attached to every row of a parsed game; parse fails
  loudly (`ParseError`) if the parsed final score disagrees with the game index.

## Two bugs found via real-data validation (fixed)

The Checkpoint 1.5 real-game validation suite — and, at full scale, the actual
3-season harvest — caught two parser bugs that no synthetic test had surfaced
until real NBA data was run through the pipeline. Both are variants of the same
underlying issue: a row's `scoreHome`/`scoreAway` cannot always be trusted, even
when it looks well-formed.

1. **Stale score on administrative rows** (`0022301202`, IND vs BOS): an
   `"Instant Replay"` review row (and the following `period end` row) echoed the
   **pre-shot** score instead of the actual result of the preceding made shot.
2. **Corrupted score on an otherwise-legitimate scoring row** (`0022100016`,
   `0022100467`, `0022201225`, `0022101139` — all from 2021-22/2022-23): a
   `"Free Throw"` event very late in the game carried a score field reset to a
   tiny bogus value (e.g. `0-1` when the true running score was `115-113`).
   Restricting to scoring `actionType`s alone (fix #1) does NOT catch this,
   since the actionType here is genuinely `"Free Throw"`.

In every case the parser's score/label cross-check correctly rejected the game
rather than silently corrupting it (raised `ParseError`, logged to
`parse_failures.json`) — the resumable batch design meant no data was lost,
just deferred.

**Fix (two layers, both in `parse_game`):**
- Only trust `scoreHome`/`scoreAway` on rows where `actionType` is
  `"Made Shot"` or `"Free Throw"` (`SCORING_ACTION_TYPES`) — the only two ways
  points can change in basketball. Administrative rows (Instant Replay, period
  start/end, etc.) are never trusted for scoring.
- **Monotonicity guard:** even on a trusted scoring row, an update is only
  applied if it does not *decrease* either team's score — basketball scores
  never go down. A violating row is logged and ignored, keeping the last known
  good total.

Regression tests: `test_instant_replay_row_does_not_overwrite_score_with_stale_value`
and `test_non_monotonic_free_throw_score_is_ignored` in `tests/test_parse.py`.
After both fixes, all 4 previously-failed games across the 3-season harvest
reparsed cleanly with zero remaining failures (see Run Results below).

## Known parsing edge cases (deferred, documented)

- Jump balls: the tip recipient exists only in description text → `possession = 0`
  until the next attributable event (a handful of events per game).
- Last-2-minutes bonus rule (≥2 team fouls in final 2:00) not modeled.
- Foul classification excludes subtypes containing "Offensive"/"Technical"/"Double"
  from team-foul counts — an approximation of NBA team-foul rules.
- And-1 possession is handled via the free-throw trip logic (final made FT flips),
  not via shot+foul pairing.
- OT timeout rules (2 per OT) not modeled; counts just keep decrementing from the
  regulation allowance (goes to `None` if it would go negative).

## Data produced in this environment

- `data/raw/game_index_{season}.parquet` — 1,230 games per season (2023-24,
  2022-23, 2021-22 regular seasons; 3,690 games total).
- `data/raw/pbp/{season}/{game_id}.parquet` — raw V3 frames, 3,690 files, 131 MB total.
- `data/raw/states/{season}/{game_id}.parquet` — parsed GameState rows + `home_win`,
  3,690 files, 72 MB total.
- Run results: see the bottom of this file.

## Exact commands to reproduce

```bash
cd wp-engine
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev]"
source .venv/bin/activate
python -m wp_engine.collect all --season 2023-24    # ≈30 min at the 0.7s rate limit
python -m wp_engine.collect all --season 2022-23
python -m wp_engine.collect all --season 2021-22
python -m pytest                                     # full test suite
```

Everything is resumable: rerunning `all` skips existing files. Failed downloads land
in `data/raw/failed_{season}.json`; rerun `harvest` to retry them after deleting the
entry, or investigate `data/raw/parse_failures.json` for parse failures.

## What Phase 2 needs to know

- Input: `data/raw/states/{season}/*.parquet`, one file per game, rows already in
  chronological order, one row per raw event (NOT yet downsampled — Phase 2 owns the
  ≤1-row-per-game-second sampling policy).
- `GameRecord`-shaped `data/raw/game_index_{season}.parquet` is the source for
  pregame context (standings as-of date, rest days) — compute without future leakage.
- Timeouts are nullable (`Int64`) — impute median + `timeouts_known` flag per docs.
- The parser emits rows for non-basketball events too (subs, replay reviews); they
  carry unchanged state and will mostly disappear in the per-second downsampling.

## Run results (all 3 seasons, end-to-end in this environment)

Reproduced by running `python -m wp_engine.collect all --season {season}` for
2023-24, 2022-23, and 2021-22 (regular seasons only), followed by a re-parse
pass after the two bug fixes above landed.

| Season | Games indexed | Downloaded | Download failures | Parsed | Parse failures |
|--------|---------------|------------|--------------------|--------|-----------------|
| 2023-24 | 1,230 | 1,230 | 0 | 1,230 | 0 |
| 2022-23 | 1,230 | 1,230 | 0 | 1,230 | 0 |
| 2021-22 | 1,230 | 1,230 | 0 | 1,230 | 0 |
| **Total** | **3,690** | **3,690** | **0** | **3,690** | **0** |

Final `python -m wp_engine.collect validate` (across the full 3-season dataset):

```
games parsed: 3690
possession inferred on >70% of events: 100.0% of games
label mismatches: 0.0% (must be 0)
```

**Operational note (harvest resilience, not a code bug):** the 2021-22 harvest
took roughly 8 hours of wall-clock instead of the expected ~25 minutes, because
network connectivity degraded severely partway through (repeated
`ConnectionError`/`ReadTimeout`, almost certainly the laptop sleeping). This is
exactly the scenario the Checkpoint 1.3 retry/resumability design targets: the
harvester kept retrying with backoff, never crashed, and finished with zero
permanent failures once connectivity returned. No action needed, but if running
Phase 1 unattended overnight, consider `caffeinate` or an equivalent
sleep-prevention wrapper to keep it running at normal speed.
