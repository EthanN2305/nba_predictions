# Phase 3 — Model Training, Calibration & Evaluation

> **Prompt to Claude:** You are building Phase 3 of the NBA Live Win-Probability Engine.
> `00-PROJECT-OVERVIEW.md` and the Phase 2 `HANDOFF.md` are pasted above. Phase 2 produced
> `data/processed/features_{season}.parquet` and `feature_meta.json`. Train a calibrated
> classifier that outputs P(home win) for any game state.

## Why Calibration Is the Whole Point

A win-probability engine is judged on whether "72%" *means* 72%. Accuracy/AUC are secondary.
The primary metrics are **Brier score** and **log loss**, evaluated with a reliability diagram.
Everything in this phase is organized around producing calibrated probabilities.

## Checkpoint 3.1 — Correct Data Splitting

- **Split by game, never by row.** Rows within a game are massively correlated; random
  row splits leak the outcome and inflate every metric.
- **Split by time:** train on the two older seasons, validate on the first half of the most
  recent season, test on the second half. Also run a `GroupKFold(groups=game_id)` CV on the
  training set for hyperparameter tuning.
- Write the split logic as `train.make_splits()` with a fixed seed and log game counts.

## Checkpoint 3.2 — Baselines First

Before any gradient boosting, implement and evaluate two baselines:
1. **Naive:** P = pregame_win_pct-based constant (or 0.5 + home-court prior ≈ 0.57 at tip).
2. **Logistic regression** on just (`score_diff`, `diff_per_sqrt_time`, `seconds_remaining`,
   `possession_x_time`). This tiny model is historically very strong at this task and is
   the bar the GBT must clearly beat.

Record baseline Brier/log-loss in the final report table.

## Checkpoint 3.3 — Gradient-Boosted Model

- Use **LightGBM** (`LGBMClassifier`) with `objective="binary"`.
- Tune with a modest search (Optuna or a small grid) over: `num_leaves`, `min_child_samples`,
  `learning_rate`, `n_estimators` (with early stopping on validation log loss),
  `feature_fraction`, `lambda_l2`. ~30–50 trials max; this problem doesn't need more.
- **Monotonic constraints** (LightGBM `monotone_constraints`): P(home win) must be
  non-decreasing in `score_diff` and `diff_per_sqrt_time`. This eliminates embarrassing
  live artifacts (probability dropping when the home team scores).
- Train the final model on train+validation once hyperparameters are frozen.

## Checkpoint 3.4 — Probability Calibration

- Fit **isotonic regression** on out-of-fold predictions (never on data the model trained on).
- Compare raw vs isotonic vs Platt (sigmoid) on the test set; keep the best by Brier score.
- Persist BOTH artifacts: `data/models/model.pkl` (LightGBM Booster) and
  `data/models/calibrator.pkl`, plus update `feature_meta.json` with model version,
  training date, and metrics. Provide a single loader:
  ```python
  def load_predictor() -> Callable[[pd.DataFrame], np.ndarray]:
      """Returns calibrated P(home win). The ONLY entry point Phase 4 uses."""
  ```

## Checkpoint 3.5 — Evaluation Report

Produce `reports/evaluation.md` containing:
- Metrics table (Brier, log loss, AUC) for naive / logistic / GBT-raw / GBT-calibrated,
  on the held-out test season half.
- **Reliability diagram** (10–20 bins) for the final model.
- **Brier score by game phase:** Q1, Q2, Q3, Q4 (>5 min), clutch (last 5 min close games).
  Clutch is where models usually fall apart — report it separately.
- **Trajectory plots** for 4–6 test games: predicted WP over game time vs actual outcome,
  including one blowout, one comeback, and one OT game. Eyeball test: curves should be
  smooth-ish, end at ~0/1, and react sensibly to runs.
- Feature importance (gain) top-15 with brief commentary.
- Known failure modes section.

## Checkpoint 3.6 — Latency & Robustness Checks

- Assert single-row inference (build features dict → predict) completes in **< 10 ms** on
  this machine; Phase 4 needs headroom.
- Test the predictor against edge states: tip-off, end of regulation tie, 40-pt blowout,
  missing timeouts (imputation path), OT states. No NaNs, no probabilities outside (0,1).

## Deliverables & Handoff

1. `train.py` runnable as `python -m wp_engine.train all`.
2. Persisted `model.pkl`, `calibrator.pkl`, updated `feature_meta.json`, `load_predictor()`.
3. `reports/evaluation.md` with all plots saved under `reports/figures/`.
4. Tests: split-by-game enforcement, monotonicity spot checks, predictor edge cases.
5. `HANDOFF.md`: final metrics, chosen hyperparameters, calibration method, model file
   paths, inference latency, and the exact predictor API contract for Phase 4.
