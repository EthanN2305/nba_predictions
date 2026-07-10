# NBA Live Win-Probability Engine — Project Overview

> **How to use this file:** Paste this file at the start of EVERY phase conversation with Claude.
> It gives Claude the full project context. Then paste the specific phase file (01–06) as the task.

## What We Are Building

A production-grade engine that computes **live, fluctuating win probabilities** for NBA games,
updating on every possession/event, and streams them to a real-time frontend chart.

**Three pillars:**
1. **Predictive Core** — A gradient-boosted tree classifier (LightGBM/XGBoost) trained on historical
   play-by-play logs from `nba_api.stats.endpoints.playbyplayv2`. Target: `home_team_wins` (binary),
   predicted at every game-state snapshot.
2. **Feature Engineering** — Time-decaying, clock-aware features: point differential, seconds
   remaining, period, team fouls / bonus state, possession, and rolling momentum metrics.
3. **Production Stack** — `nba_api.live` polling service → FastAPI backend → trained model
   inference → WebSocket push → React frontend with a live-updating probability chart.

## Architecture (Target End State)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Data Pipeline   │     │  Model Training   │     │  Serialized      │
│  (Phase 1)       │────▶│  (Phase 3)        │────▶│  Model + Scaler  │
│  playbyplayv2    │     │  LightGBM + calib │     │  model.pkl       │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
        │                        ▲                          │
        ▼                        │                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Feature Store   │────▶│  Feature Builder  │◀───│  FastAPI Backend │
│  (Phase 2)       │     │  (shared module)  │     │  (Phase 4)       │
│  parquet files   │     │  features.py      │     │  /ws WebSocket   │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                 ▲                          │
                        ┌────────┴────────┐                 ▼
                        │  nba_api.live    │        ┌─────────────────┐
                        │  poller (Phase 4)│        │  React Frontend  │
                        └─────────────────┘        │  (Phase 5)       │
                                                    └─────────────────┘
```

**Critical design rule:** the feature-building code (`features.py`) is a SINGLE shared module used
by both training (offline, batch) and inference (online, streaming). Training/serving skew is the
#1 failure mode of this project — never duplicate feature logic.

## Repository Layout (create in Phase 1, respect in all phases)

```
wp-engine/
├── data/
│   ├── raw/                  # raw play-by-play JSON/parquet per game
│   ├── processed/            # feature matrices (parquet)
│   └── models/               # model.pkl, calibrator.pkl, feature_meta.json
├── src/
│   ├── wp_engine/
│   │   ├── __init__.py
│   │   ├── collect.py        # Phase 1: historical data harvesting
│   │   ├── features.py       # Phase 2: SHARED feature builder (offline + online)
│   │   ├── train.py          # Phase 3: training, calibration, evaluation
│   │   ├── live.py           # Phase 4: nba_api.live poller → GameState
│   │   ├── inference.py      # Phase 4: model loading + predict_proba wrapper
│   │   └── schemas.py        # Pydantic models shared across phases
│   └── api/
│       └── main.py           # Phase 4: FastAPI app + WebSocket endpoints
├── frontend/                 # Phase 5: React + chart
├── tests/
├── pyproject.toml
└── README.md
```

## Global Conventions (all phases must follow)

- **Python 3.11+**, managed with `pyproject.toml` (use `uv` or `pip install -e .`).
- **Libraries:** `nba_api`, `pandas`, `pyarrow`, `lightgbm`, `scikit-learn`, `fastapi`,
  `uvicorn`, `websockets`, `pydantic v2`, `httpx`. Frontend: React + Recharts (or lightweight-charts).
- **Data format:** parquet everywhere (raw and processed). One file per game in raw,
  partitioned by season in processed.
- **Time convention:** all clock features expressed as `seconds_remaining_total`
  (seconds left in regulation; overtime handled explicitly — see Phase 2).
- **Target convention:** always predict **P(home team wins)**. The frontend flips display
  per user preference.
- **Rate limiting:** `nba_api` stats endpoints are unofficially rate-limited. Every harvesting
  loop must sleep 0.6–1.0s between calls, retry with exponential backoff, and checkpoint
  progress so it can resume after a crash.
- **Type hints + docstrings** on every public function. Pydantic models for anything crossing
  a process boundary.
- **Testing:** every phase ships pytest tests for its deliverables.

## The Canonical GameState Schema

This Pydantic model is the contract between EVERY component. Define it in Phase 1
(`schemas.py`), never change field names afterward without updating all phases:

```python
class GameState(BaseModel):
    game_id: str
    period: int                      # 1-4, 5+ = OT
    seconds_remaining_period: float
    seconds_remaining_total: float   # regulation-normalized, see Phase 2
    home_score: int
    away_score: int
    score_diff: int                  # home - away
    home_team_fouls_period: int
    away_team_fouls_period: int
    home_in_bonus: bool
    away_in_bonus: bool
    possession: int                  # 1 = home, -1 = away, 0 = unknown/dead ball
    home_timeouts_remaining: int | None
    away_timeouts_remaining: int | None
    event_num: int                   # ordering key within the game
```

## Phase Map

| Phase | File | Deliverable | Depends on |
|-------|------|-------------|------------|
| 1 | `01-phase-data-collection.md` | Historical PBP harvester + raw dataset + schemas | — |
| 2 | `02-phase-feature-engineering.md` | Shared `features.py` + processed feature matrices | 1 |
| 3 | `03-phase-model-training.md` | Trained, calibrated model + evaluation report | 2 |
| 4 | `04-phase-backend-live.md` | Live poller + FastAPI + WebSocket inference service | 2, 3 |
| 5 | `05-phase-frontend.md` | React live win-probability chart | 4 |
| 6 | `06-phase-hardening.md` | Tests, simulation/replay harness, deployment | all |

## How to Prompt Each Phase

For each phase, start a fresh conversation and paste:
1. This overview file.
2. The phase file.
3. The **handoff notes** produced at the end of the previous phase (each phase prompt
   instructs Claude to write `HANDOFF.md` summarizing what was built, file paths,
   schema decisions, and any deviations).

If a phase is too large for one session, the phase files contain internal checkpoints —
ask Claude to complete checkpoints sequentially and verify each before moving on.
