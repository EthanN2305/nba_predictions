# wp-engine — Architecture

> The document a new contributor reads first. Consolidates the six phase
> handoffs (originals preserved in [`handoffs/`](handoffs/)).

## What this is

A production-grade engine that computes live, per-event win probabilities
for NBA games and streams them to a React chart over WebSockets. Built in
six phases (plans in [`../../docs/`](../../docs/)); every phase shipped
TDD-first pytest/Vitest coverage — **202 backend tests + 12 frontend tests**.

```
                   HISTORICAL (offline)                       LIVE / REPLAY (online)
┌──────────────────────────────────────────────┐   ┌────────────────────────────────────┐
│ collect.py (Phase 1)                         │   │ live.py poller/adapter (Phase 4)   │
│ stats.nba.com PlayByPlayV3 → GameState rows  │   │ cdn.nba.com liveData → GameState   │
│ data/raw/{pbp,states}/{season}/…             │   │ replay.py: parsed games → same path│
└──────────────┬───────────────────────────────┘   └────────────────┬───────────────────┘
               ▼                                                    ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ features.py (Phase 2) — THE shared module. FeatureBuilder.update(state) is the ONLY   │
│ feature code path; build_offline() literally iterates it (skew impossible by design). │
│ 30 features; contract = FEATURE_COLUMNS ⇔ data/models/feature_meta.json               │
└──────────────┬───────────────────────────────────────────────────┬───────────────────┘
               ▼ data/processed/features_{season}.parquet          ▼ per event
┌──────────────────────────────────────────────┐   ┌────────────────────────────────────┐
│ train.py (Phase 3)                           │   │ inference.LivePredictor (Phase 4)  │
│ split by game & time → baselines → monotone  │──▶│ load_predictor() → wp_home         │
│ LightGBM → OOF calibration → evaluation      │   │ → WinProbUpdate                    │
│ data/models/model.pkl + report               │   └────────────────┬───────────────────┘
└──────────────────────────────────────────────┘                    ▼
                                                   ┌────────────────────────────────────┐
                                                   │ hub.GameHub → api/main.py FastAPI  │
                                                   │ REST + WS /ws/games/{id} (Phase 4) │
                                                   │ → frontend/ React chart (Phase 5)  │
                                                   └────────────────────────────────────┘
```

## Module map

| Module | Phase | Role |
|--------|-------|------|
| `src/wp_engine/schemas.py` | 1 | `GameState` (the universal contract), `GameRecord`, `WinProbUpdate` |
| `src/wp_engine/collect.py` | 1 | game index, rate-limited resumable harvester, V3 parser, validation CLI |
| `src/wp_engine/features.py` | 2 | `FeatureBuilder` / `build_offline` / `PregameContext`, matrix build CLI, parity + sanity |
| `src/wp_engine/train.py` | 3 | splits, baselines, monotone LightGBM, calibration, `load_predictor()`, report |
| `src/wp_engine/live.py` | 4 | `LiveGameAdapter` (liveData→GameState), CDN fetchers + request semaphore |
| `src/wp_engine/inference.py` | 4 | `LivePredictor`: state → features → wp → `WinProbUpdate` |
| `src/wp_engine/poller.py` | 4 | `GamePoller` (fault-tolerant), `GameDirectory` (scoreboard watcher) |
| `src/wp_engine/hub.py` | 4 | in-memory pub/sub + bounded history + control frames |
| `src/wp_engine/replay.py` | 4 | historical games through the live pipeline (CLI + `POST /replay/{id}`) |
| `src/api/main.py` | 4/6 | FastAPI app factory, WS contract, backpressure, graceful shutdown |
| `src/wp_engine/config.py` | 6 | `Settings` (env `WP_*`), JSON logging |
| `src/wp_engine/monitor.py` | 6 | realized-Brier drift monitor + retraining recommendation |
| `frontend/` | 5 | Vite/React/TS chart app (see `frontend/README.md`) |

## Load-bearing design decisions

1. **One feature code path.** `build_offline()` iterates the same
   `FeatureBuilder` the live service calls. The parity test + golden
   trajectory pin this. Never vectorize without keeping them green.
2. **`GameState` is the universal interface.** Historical parser, live
   adapter and replay all emit it; everything downstream is source-agnostic.
3. **Split by game and by time, never by row.** Rows within a game share a
   label; row-level splits inflate every metric.
4. **Monotone constraints** on `score_diff`/`diff_per_sqrt_time` — the
   probability can never drop when the home team scores.
5. **Calibration is the product.** Brier/log-loss primary; the raw LightGBM
   beat isotonic/Platt on held-out test (0.1565 / 0.4663 / AUC 0.857) so the
   shipped calibrator is identity — the plumbing supports swapping it.
6. **Replay is a first-class path**, not a test hack: same predictor, hub
   and wire format, flagged `is_replay`.
7. **In-memory hub ⇒ exactly one uvicorn worker.** Scale-out path (if ever
   needed): Redis pub/sub behind the same `GameHub` API.

## Key deviations from the original phase docs

- **PlayByPlayV2 → V3** (Phase 1): the stats API stopped serving V2
  (nba_api #591). V3's ISO clock matches the live feed — shared parser.
- **`turnovers_last_300s_diff` dropped** (Phase 2): `GameState` carries no
  turnover signal; the Golden Rule forbids features the live stream can't
  produce.
- **OT time** (Phases 1/2): `seconds_remaining` = current-OT seconds only +
  `is_overtime` flag; future OTs are unknowable live.
- **Raw > calibrated** (Phase 3): isotonic/Platt fit on GroupKFold OOF
  predictions but lost on test Brier; shipped raw with the swap plumbing.
- **cdn.nba.com Akamai-blocked** from the dev network (Phase 4): live
  adapter built against the documented liveData schema with synthesized
  fixtures; `WP_ENABLE_LIVE=1` is code-complete but must be verified from an
  unblocked network. stats.nba.com (historical) works fine.

## The data

3,690 games (2021-22 → 2023-24 regular seasons), 100% harvested and parsed,
zero label mismatches. Feature matrices: 1,186,110 rows (≤1 row per
game-clock second). Home win rate 54.5–58.2% by season.

## Guard rails (Phase 6)

- `tests/e2e/test_replay_pipeline.py`: 6 diverse real games through the full
  WS pipeline — event counts, final-probability correctness, ≤25 pp
  smoothness outside the last 2 minutes, schema-valid frames.
- `tests/e2e/test_golden.py`: DEN–LAL trajectory reproduced within 1e-6
  against the pinned committed model (`tests/fixtures/models/`) —
  regenerate ONLY consciously via `scripts/build_golden.py`.
- `tests/test_skew_guard.py` + a `load_predictor` startup assertion: code ⇔
  meta ⇔ model column agreement, fails loudly.
- `python -m wp_engine.monitor`: realized Brier vs benchmark; >20% relative
  degradation → retraining recommendation.

## Operations

Config via env (`WP_*`, see `config.Settings`). JSON structured logs.
WS backpressure (slow clients dropped after `WP_WS_SEND_TIMEOUT`). Graceful
shutdown broadcasts `{"type":"server_closing"}`. Global semaphore caps
concurrent NBA requests at `WP_MAX_CONCURRENT_NBA_REQUESTS` (default 3).
Deployment: single small VM, one uvicorn worker (see README).
