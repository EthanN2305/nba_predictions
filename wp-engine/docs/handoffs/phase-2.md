# Phase 2 → Phase 3 Handoff

> Paste this file (plus `docs/00-PROJECT-OVERVIEW.md` and `docs/03-phase-model-training.md`)
> at the start of the Phase 3 conversation. The Phase 1 handoff is archived at
> `docs/handoffs/phase-1.md`.

## What was built

Phase 2 (shared feature engineering) is complete: `src/wp_engine/features.py` is
the SINGLE feature module used by offline training and (later) live inference,
plus materialized training matrices for all 3 seasons. 127 pytest tests, all
green (65 Phase 1 + 62 Phase 2).

| File | Contents |
|------|----------|
| `src/wp_engine/features.py` | `FeatureBuilder` (stateful incremental), `build_offline`, `PregameContext` + `build_pregame_context`, `build_game_matrix`, `build_season`, `check_parity`, `sanity_table`, CLI (`python -m wp_engine.features {build,parity,sanity} --season …`) |
| `data/processed/features_{season}.parquet` | training matrices (see row counts below) |
| `data/models/feature_meta.json` | ordered feature list, dtypes, imputation values, sampling & OT policy, code hash — **Phase 4 must read this, never hard-code columns** |
| `tests/test_features.py`, `tests/test_pregame.py`, `tests/test_offline_parity.py`, `tests/test_features_cli.py` | Phase 2 test suite (see README Testing section) |

## The anti-skew design (why parity is trivially guaranteed)

`build_offline()` deliberately iterates the SAME `FeatureBuilder.update()` that
Phase 4 will call on live events — feature logic exists in exactly one place
(the overview's "never duplicate feature logic" rule). The parity tests
(`tests/test_offline_parity.py` + `python -m wp_engine.features parity`, run on
20 sampled games per season, all passing) pin this contract so any future
vectorized reimplementation must stay bit-identical.

## Final feature list (30 columns, all float64, order = `FEATURE_COLUMNS`)

Core clock & score (2.1): `score_diff`, `seconds_remaining`, `period`,
`is_overtime`, `diff_per_sqrt_time` (= diff/√(sec+1)), `score_total`,
`lead_changes_so_far` (leader flips only; ties don't count),
`time_since_lead_change` (game-clock secs, capped 1200; time since tip if none
yet), `largest_lead_home`, `largest_lead_away`.

Game situation (2.2): `possession` (1/−1/0), `possession_x_time`
(= poss/√(sec+1)), `home_in_bonus`, `away_in_bonus`, `foul_diff_period`,
`home_timeouts_remaining`, `away_timeouts_remaining` (None → **5.0**, the
empirical median), `timeouts_known` (0/1 flag), `is_clutch` (last 5:00 of Q4 or
any OT, |diff| ≤ 5).

Rolling momentum (2.3), all over trailing **game-clock** windows (deque of
(elapsed, delta) tuples inside `FeatureBuilder` — reuse live in Phase 4):
`run_last_120s`, `run_last_300s`, `scoring_rate_home_300s`,
`scoring_rate_away_300s` (pts / 5 min, fixed denominator),
`fouls_last_300s_diff` (from period-counter deltas; period reset handled),
`momentum_ewm` (decayed sum of per-event diff changes, halflife 90 game-secs:
`m ← m·0.5^(Δt/90) + Δdiff`).

Pregame (2.4): `pregame_win_pct_home/away/diff`, `rest_days_home/away`
(capped 7; opener = 7). Defaults when context absent: win% 0.5, rest 2.0.

## Decisions & deviations

- **OT handling:** `seconds_remaining` = regulation seconds left; in OT it is
  seconds left in the *current* OT only, with `is_overtime = 1` (future OTs are
  unknowable live). Matches Phase 1's `seconds_remaining_total` convention.
- **Elapsed game clock** for windows: `game_elapsed_seconds()` — regulation
  periods 720s, OT 300s. Monotonic within a game.
- **⚠️ Deviation — `turnovers_last_300s_diff` dropped:** `GameState` carries no
  turnover signal, and the Golden Rule forbids features the live GameState
  stream can't produce. Adding turnovers would mean extending the Phase 1
  schema + reparse + a matching Phase 4 live adapter field — deferred.
- **Window boundary:** an event exactly `window` seconds old is EXCLUDED
  (strictly-newer-than comparison). Pinned by test.
- **Sampling policy:** features are computed on the FULL event stream (running
  counts must see every event), then downsampled to ≤1 row per game-clock
  second per game, keeping the last event of each second.
- **Leakage guards:** pregame context uses strictly-earlier dates only
  (same-day games excluded); the no-leakage test asserts truncated-prefix
  features are bit-identical to full-game rows.

## Matrix stats (input to Phase 3)

| Season | Games | Rows | home_win row rate |
|--------|-------|------|-------------------|
| 2023-24 | 1,230 | 395,238 | 0.545 |
| 2022-23 | 1,230 | 395,269 | 0.582 |
| 2021-22 | 1,230 | 396,603 | 0.544 |
| **Total** | **3,690** | **1,186,110** | — |

Columns = 30 features + `game_id` (str), `event_num` (int, per-game ordering
key), `home_win` (bool label, constant within a game). ~321 rows per game.

Sanity fan chart (`python -m wp_engine.features sanity --season 2023-24`)
looks textbook: home win rate is monotonic in score-diff bucket within every
time bucket, and fans out toward 0/1 as seconds remaining → 0 (e.g. +10–20
with <5:00 left → 0.999; −5–2 → 0.155).

## What Phase 3 must know

- **Split by game_id, never by row** (rows within a game share a label and are
  massively correlated). `event_num` orders rows within a game; `game_id`
  prefix encodes season (00223… = 2023-24, 00222… = 2022-23, 00221… = 2021-22).
- Read the feature list from `data/models/feature_meta.json` (or
  `features.FEATURE_COLUMNS`) — never hard-code column names.
- `feature_meta.json` is REWRITTEN on every `build` run with the current code
  hash (`code_version`) — Phase 3 should append model info to it, not clobber it.
- Monotonic constraints for LightGBM: `score_diff` and `diff_per_sqrt_time`
  should be non-decreasing → P(home win). Column order comes from
  `FEATURE_COLUMNS`.
- No NaNs anywhere in the matrices (enforced by tests); everything float64
  except the 3 extra columns.
- Home-court advantage in this data: 54.5–58.2% home win rate by season —
  the naive baseline for Checkpoint 3.2.

## Exact commands to reproduce

```bash
cd wp-engine && source .venv/bin/activate
python -m wp_engine.features build  --season 2023-24   # ≈40s per season
python -m wp_engine.features build  --season 2022-23
python -m wp_engine.features build  --season 2021-22
python -m wp_engine.features parity --season 2023-24   # 20-game skew check
python -m wp_engine.features sanity --season 2023-24   # fan-chart table
python -m pytest                                        # full suite (127 tests)
```
