# NBA Live Win-Probability Engine (`wp-engine`)

A production-grade engine that computes **live, fluctuating win probabilities** for NBA
games, updating on every possession/event, and streams them to a real-time frontend chart.

Built in six phases (see [`../docs/`](../docs/) for the full plans):

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1 | Historical play-by-play harvester + raw `GameState` dataset + schemas | ✅ done |
| 2 | Shared `features.py` (offline + online) + processed feature matrices | ✅ done |
| 3 | Trained, calibrated LightGBM model + evaluation report | ✅ done |
| 4 | Live poller + FastAPI + WebSocket inference service | ✅ done |
| 5 | React live win-probability chart | ⏳ next |
| 6 | Hardening: e2e replay regression suite, skew guards, Docker | — |

## Setup

```bash
cd wp-engine
uv venv --python 3.12 .venv          # or: python3.12 -m venv .venv
uv pip install -p .venv/bin/python -e ".[dev]"   # or: .venv/bin/pip install -e ".[dev]"
```

## Phase 1 pipeline (data collection)

```bash
source .venv/bin/activate

# everything for one season (index → harvest → parse → validate):
python -m wp_engine.collect all --season 2023-24

# or step by step:
python -m wp_engine.collect index    --season 2023-24   # game list + home_win labels
python -m wp_engine.collect harvest  --season 2023-24   # raw play-by-play (rate-limited, resumable)
python -m wp_engine.collect parse    --season 2023-24   # events → GameState rows
python -m wp_engine.collect validate                    # data-quality summary
```

The harvester sleeps 0.7s between requests, retries with exponential backoff
(2/4/8/16s), and is fully resumable — rerun the same command after a crash and it
skips finished games. Failures are recorded in `data/raw/failed_{season}.json` and
`data/raw/parse_failures.json`.

Data layout:

```
data/
├── raw/
│   ├── game_index_{season}.parquet   # one row per game + home_win label
│   ├── pbp/{season}/{game_id}.parquet     # raw PlayByPlayV3, untouched
│   └── states/{season}/{game_id}.parquet  # parsed GameState rows + label
├── processed/                        # Phase 2 feature matrices
└── models/                           # Phase 3 model.pkl / calibrator.pkl / feature_meta.json
```

> **Note — PlayByPlayV2 vs V3:** the phase docs reference `PlayByPlayV2`, but the NBA
> stats API no longer serves V2 data ([nba_api issue #591](https://github.com/swar/nba_api/issues/591)).
> Phase 1 harvests and parses **PlayByPlayV3** (ISO-8601 clocks, explicit
> `scoreHome`/`scoreAway`, `actionType` strings). Bonus: V3's clock format matches the
> live endpoint used in Phase 4, so the clock parser is shared.

## Phase 2 pipeline (feature engineering)

`src/wp_engine/features.py` is the **single shared feature module** used by both
training and live inference — `build_offline()` literally iterates the same
`FeatureBuilder.update()` that Phase 4 calls on live events, so training/serving
skew is impossible by construction.

```bash
python -m wp_engine.features build  --season 2023-24   # → data/processed/features_{season}.parquet + feature_meta.json
python -m wp_engine.features parity --season 2023-24   # offline vs incremental on 20 sampled games
python -m wp_engine.features sanity --season 2023-24   # empirical WP fan-chart table
```

30 features (see `FEATURE_COLUMNS` / `data/models/feature_meta.json`): core
clock/score (`diff_per_sqrt_time`, lead changes, largest leads), game situation
(possession×time, bonus, timeouts + `timeouts_known` imputation flag, clutch),
rolling momentum over trailing *game-clock* windows (`run_last_120s/300s`,
scoring rates, foul diff, `momentum_ewm` with 90s halflife), and leakage-free
pregame context (win% as-of date, rest days). Matrices are downsampled to at
most one row per game-clock second (features are computed on the full stream
first). OT convention: `seconds_remaining` = current-OT seconds with
`is_overtime = 1`. Deviation: `turnovers_last_300s_diff` was dropped —
`GameState` carries no turnover signal (see `HANDOFF.md`).

## Phase 3 pipeline (model training)

> macOS prerequisite: `brew install libomp` (LightGBM needs OpenMP).

```bash
python -m wp_engine.train all --trials 30   # splits → baselines → tune → calibrate → report (~15 min)
```

Produces `data/models/model.pkl` + `calibrator.pkl` (+ model section in
`feature_meta.json`) and `reports/evaluation.md` with reliability, Brier-by-phase
and trajectory figures. Split: train = 2021-22 + 2022-23, validation = first half
of 2023-24 by date, test = second half — **always by game, never by row**. Final
model: monotone-constrained LightGBM (P(home win) non-decreasing in `score_diff`,
`diff_per_sqrt_time`). Test metrics: **Brier 0.1565, log loss 0.4663, AUC 0.857**
(naive 0.2533, 4-feature logistic 0.1681). Raw model beat isotonic/Platt on test
Brier, so no calibrator is applied. Phase 4 consumes only
`train.load_predictor()`.

## Phase 4 pipeline (live backend)

```bash
uvicorn api.main:app                                        # the service
python -m wp_engine.replay --game-id 0022300061 --speed 60  # serve + replay a game
python -m wp_engine.replay --game-id 0022300061 --print --speed 0  # pipeline → stdout
curl -X POST 'localhost:8000/replay/0022300061?speed=60'    # replay into a running server
```

REST: `GET /healthz`, `GET /games`, `GET /games/{id}/history`,
`POST /replay/{id}?speed=`. WebSocket `/ws/games/{id}`: snapshot frame on
connect, then per-event update frames (contract documented in `api/main.py`
docstring and `HANDOFF.md`). Env: `WP_DATA_DIR`, `WP_CORS_ORIGINS`,
`WP_ENABLE_LIVE=1` (start the scoreboard-driven live pollers).

> **⚠️ Live-feed caveat:** `cdn.nba.com` (all `nba_api.live` endpoints) was
> Akamai-blocked from the development network, so the live adapter is built
> against the documented liveData payload shape + synthesized fixtures, and
> **replay mode is the primary dev/demo path**. Verify against a real live
> payload before relying on `WP_ENABLE_LIVE=1` (see `HANDOFF.md`).

## Testing — every phase ships pytest coverage

**The whole suite must be green before a phase is considered done.** Run it from
`wp-engine/`:

```bash
.venv/bin/python -m pytest                 # everything
.venv/bin/python -m pytest -m "not network"  # skip tests that hit the live NBA API
.venv/bin/python -m pytest --cov=wp_engine --cov-report=term-missing  # with coverage
```

### Phase 1 — data collection ✅ (implemented, 65 tests, 90% coverage)

Harvested and parsed all 3 target seasons end-to-end in this environment:
3,690/3,690 games downloaded and parsed with **zero** remaining failures, 100%
possession coverage, 0% label mismatches. See `HANDOFF.md` for the full run
report and two real-data parser bugs found and fixed along the way.

| Test file | What it proves |
|-----------|----------------|
| `tests/test_schemas.py` | `GameState`/`GameRecord` validate correctly (possession ∈ {1,−1,0}, non-negative scores, JSON round-trip) |
| `tests/test_game_index.py` | Team rows collapse to one game row; home/away resolved from MATCHUP; `home_win` label correct; parquet written |
| `tests/test_harvest.py` | Resumability (existing files skipped), 0.7s rate-limit sleeps, 2/4/8/16s exponential backoff, failures recorded to `failed_{season}.json` without killing the run |
| `tests/test_parse.py` | Clock parsing (ISO durations), OT time handling, score forward-fill, possession inference rules, team-foul/bonus tracking with per-period reset, timeout tracking with None fallback, chronological reordering of out-of-order events |
| `tests/test_parse_season.py` | Batch parsing survives malformed games; failures land in `parse_failures.json`; already-parsed games skipped |
| `tests/test_collect.py` | **End-to-end on 3 real committed games** (regulation win, home loss, OT): final `score_diff` sign matches `home_win`; clock monotonically non-increasing; scores non-decreasing; fouls reset every period; possession inferred on >70% of events; `validate` CLI reports mismatches |

```bash
.venv/bin/python -m pytest tests/test_schemas.py tests/test_game_index.py \
    tests/test_harvest.py tests/test_parse.py tests/test_parse_season.py tests/test_collect.py
```

### Phase 2 — feature engineering ✅ (implemented, 62 tests)

All three seasons materialized: 1,186,110 rows / 3,690 games (see `HANDOFF.md`
for row counts and class balance). `python -m wp_engine.features parity` passed
on 20 sampled games in each season.

| Test file | What it proves |
|-----------|----------------|
| `tests/test_features.py` | Every feature's definition: clock/score interactions (`diff_per_sqrt_time`), lead-change counting and game-clock timing, largest leads, bonus/foul-diff, timeout imputation + flag, clutch definition, trailing-window runs/scoring rates over **game-clock** time (incl. exact window-boundary exclusion and period-reset foul deltas), exact `momentum_ewm` halflife decay |
| `tests/test_pregame.py` | Win% uses only strictly-earlier dates (no leakage), rest days from previous game capped at 7, season-opener neutral defaults, pregame values flow into the feature vector |
| `tests/test_offline_parity.py` | **Offline vs incremental parity** — `build_offline()` bit-identical to iterating `FeatureBuilder.update()` on the 3 real fixture games (incl. OT), and **no-leakage**: truncated-prefix features equal the full-game matrix rows |
| `tests/test_features_cli.py` | Downsampling keeps the last event per game-clock second and runs on the full stream first; matrix columns = `FEATURE_COLUMNS` + id/event/label; `feature_meta.json` contents (ordered columns, dtypes, imputation values, code hash); season-opener leakage check on the built matrix; `check_parity`; sanity-table probabilities |

```bash
.venv/bin/python -m pytest tests/test_features.py tests/test_pregame.py \
    tests/test_offline_parity.py tests/test_features_cli.py
```

### Phase 3 — model training ✅ (implemented, 16 tests)

| Test file | What it proves |
|-----------|----------------|
| `tests/test_splits.py` | Splits are by game and by time, never by row: no game_id in two splits, train = the two older seasons, eval season halved by **date** (not id), deterministic |
| `tests/test_model.py` | Naive baseline is game-level (not row-level) home win rate; logistic uses the 4 classic features and beats chance; `evaluate` returns Brier/log-loss/AUC; **monotonicity** (P(home win) non-decreasing in `score_diff` sweep); artifact save/load round-trip via `load_predictor` (valid probabilities, missing-column rejection, edge states incl. imputed timeouts); report generator writes `evaluation.md` + ≥3 figures |
| `tests/test_latency.py` | Against the REAL trained artifacts (skipped on fresh clones): single-row inference < 10 ms; edge states (tip-off, tie at 0:00, ±40 blowout, OT, imputed timeouts) give no NaNs, probs in (0,1), blowout > 0.95; tip-off reflects home-court advantage |

```bash
.venv/bin/python -m pytest tests/test_splits.py tests/test_model.py tests/test_latency.py
```

### Phase 4 — live backend ✅ (implemented, 37 tests)

The ISO clock parser was already unit-tested in Phase 1 (`tests/test_parse.py`)
and is reused verbatim for the live feed.

| Test file | What it proves |
|-----------|----------------|
| `tests/test_live_adapter.py` | liveData actions → GameState: display clock (Q/OT), score trusted only on made scoring actions + monotonic guard, foul counting excl. offensive/technical with period reset, bonus, timeout decrement, explicit-possession mapping, **out-of-order sort + dedup/amendment (same `actionNumber` → latest wins)**, malformed events skipped without crashing |
| `tests/test_inference.py` | `LivePredictor`: GameState → valid `WinProbUpdate`; features stay stateful across events (a late run raises wp); OT clock display; `is_replay` flag |
| `tests/test_poller.py` | `GamePoller.step` emits exactly one update per new event (idempotent on cumulative payloads, amendments don't re-emit); 3s→30s backoff when the feed goes quiet; 5 consecutive fetch failures → degraded (never crashes, recovers); `GameHub` history bounds + pub/sub; `GameDirectory` starts live games once and stops finished ones |
| `tests/test_ws.py` | Full REST + WebSocket contract with `TestClient`: healthz, empty history, then a REAL fixture game replayed through the app — snapshot frame, per-event update frames (`is_replay`, valid wp), `replay_finished` control frame, DEN home win ends > 0.9, history backfill, `/games` meta, 404 on unknown replay |

```bash
.venv/bin/python -m pytest tests/test_live_adapter.py tests/test_inference.py \
    tests/test_poller.py tests/test_ws.py
```

### Phase 5 — frontend (planned)

Vitest (not pytest — TypeScript): clock→x-axis transform and socket-reducer component
tests, plus `npm run build` with zero TS errors.

```bash
cd frontend && npm test && npm run build
```

### Phase 6 — hardening (planned)

Required by [`docs/06-phase-hardening.md`](../docs/06-phase-hardening.md):

- `tests/e2e/test_replay_pipeline.py` — replay 5 diverse historical games through the
  full poller→features→model→hub pipeline with a real WebSocket client; final
  `wp_home` > 0.95 for home wins (< 0.05 for losses); no >25 pp single-event jumps
  outside the final 2 minutes; all frames validate against `WinProbUpdate`.
- `tests/e2e/test_golden.py` — committed WP trajectory of one game reproduced within
  1e-6 (catches silent feature drift).
- `tests/test_skew_guard.py` — `feature_meta.json` == `FeatureBuilder` output columns ==
  model's expected columns.

```bash
.venv/bin/python -m pytest tests/e2e/ tests/test_skew_guard.py
```

## Conventions

- Python 3.11+ (developed on 3.12), `pydantic` v2 models for anything crossing a
  process boundary, parquet everywhere.
- All probabilities are **P(home team wins)**; the frontend flips display per user
  preference.
- The canonical `GameState` schema lives in `src/wp_engine/schemas.py` — field names
  never change without updating every phase.
