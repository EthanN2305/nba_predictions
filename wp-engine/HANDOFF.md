# Phase 3 → Phase 4 Handoff

> Paste this file (plus `docs/00-PROJECT-OVERVIEW.md`, `docs/04-phase-backend-live.md`
> and `docs/handoffs/phase-2.md`) at the start of the Phase 4 conversation.
> Earlier handoffs: `docs/handoffs/phase-1.md`, `docs/handoffs/phase-2.md`.

## What was built

Phase 3 (training, calibration, evaluation) is complete: `src/wp_engine/train.py`
runnable as `python -m wp_engine.train all`, persisted artifacts, and
`reports/evaluation.md`. 143 pytest tests, all green.

| File | Contents |
|------|----------|
| `src/wp_engine/train.py` | `make_splits`, baselines, `fit_gbt`/`tune_gbt`, OOF calibration, `save_artifacts`, **`load_predictor()`**, `write_report`, `run_all`, CLI |
| `data/models/model.pkl` | LightGBM Booster (pickled), 204 trees, monotone-constrained |
| `data/models/calibrator.pkl` | `{"method": "raw", "model": None}` — see calibration note |
| `data/models/feature_meta.json` | feature contract + `"model"` section (metrics, params, num_trees) |
| `reports/evaluation.md` + `reports/figures/` | metrics table, reliability diagram, Brier by phase, 6 trajectory plots, importance table |
| `tests/test_splits.py`, `tests/test_model.py`, `tests/test_latency.py` | Phase 3 suite |

## The predictor API contract (the ONLY thing Phase 4 calls)

```python
from wp_engine.train import load_predictor
predict = load_predictor()            # loads data/models/* once — do this at startup
probs = predict(frame)                # frame: pd.DataFrame containing at least the
                                      # 30 FEATURE_COLUMNS (any order, extras ignored)
                                      # → np.ndarray of calibrated P(home win)
```

- Raises `ValueError("feature frame is missing columns: […]")` on missing features.
- Single-row latency: **< 1 ms** measured (test asserts < 10 ms) — plenty of
  headroom for per-event live inference.
- Feed it exactly what `FeatureBuilder.update()` returns
  (`pd.DataFrame([features_dict])`).

## Final metrics (held-out test = 2nd half of 2023-24 by date, split by game)

| model | Brier ↓ | log loss ↓ | AUC ↑ |
|-------|---------|-----------|-------|
| naive (train home rate 0.563) | 0.2533 | 0.6997 | 0.500 |
| logistic (4 features) | 0.1681 | 0.4921 | 0.832 |
| **GBT raw (shipped)** | **0.1565** | **0.4663** | **0.857** |
| GBT + isotonic | 0.1569 | 0.4676 | 0.857 |
| GBT + Platt | 0.1584 | 0.4786 | 0.857 |

Brier by phase: Q1 0.215 → Q2 0.188 → Q3 0.140 → Q4(>5min) 0.102; clutch 0.177.

## Chosen hyperparameters

`learning_rate=0.03, num_leaves=31, min_child_samples=50, feature_fraction=0.7,
lambda_l2=1.0, n_estimators=204` (early-stopping best iteration on the val
half-season; 30-trial random search; monotone +1 on `score_diff` and
`diff_per_sqrt_time`, tuned by validation log loss).

## Calibration note (deviation-ish)

Isotonic and Platt were fit on GroupKFold(5) out-of-fold predictions over
train+val as specified — but the RAW model beat both on test Brier (LightGBM
trained on ~980k rows with logloss objective is already well calibrated), so
`calibrator.pkl` stores `method="raw"`, `model=None` and `load_predictor`
applies identity. The reliability diagram shows mild overconfidence in the
0.4–0.8 band; revisit after Phase 6 if it matters. `apply_calibrator` handles
isotonic/Platt/None uniformly, so swapping later is a one-line retrain.

## What Phase 4 must know

- Load once at startup: `predict = load_predictor()`; per event build features
  with the SAME `FeatureBuilder` (`features.py`) — never re-implement.
- Sort live events like Phase 1 did: V3 `actionNumber` is NOT chronological —
  sort by `(period, clock desc, actionNumber)` before feeding `FeatureBuilder`.
- Live clock format is identical to historical V3 (`PT11M23.00S`):
  `collect.parse_clock` is directly reusable.
- **⚠️ Environment blocker discovered:** `cdn.nba.com` (ALL `nba_api.live`
  endpoints + todaysScoreboard) returns Akamai **403 Access Denied** from this
  network, while `stats.nba.com` works fine. Live-payload fixtures could not be
  captured here. Phase 4 must therefore: (1) code the live adapter defensively
  against the documented liveData schema (`extra="ignore"`, fallbacks), (2)
  make replay mode (Checkpoint 4.4) the primary dev/demo path, and (3) verify
  against a real live payload from a non-blocked network before production use.
- `feature_meta.json` now has a `"model"` section — the skew guard in Phase 6
  should compare its `feature_columns` against both `FEATURE_COLUMNS` and the
  booster's `feature_name()`.

## Exact commands to reproduce

```bash
cd wp-engine && source .venv/bin/activate
brew install libomp                      # macOS only, once
python -m wp_engine.train all --trials 30   # ≈15 min end to end
python -m pytest                            # 143 tests
```
