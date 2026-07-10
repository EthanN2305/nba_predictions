# Phase 2 — Feature Engineering (Shared Offline/Online Module)

> **Prompt to Claude:** You are building Phase 2 of the NBA Live Win-Probability Engine.
> `00-PROJECT-OVERVIEW.md` and the Phase 1 `HANDOFF.md` are pasted above. Phase 1 produced
> per-game parquet files of `GameState` rows. Your job is to build `src/wp_engine/features.py` —
> the SINGLE feature-building module used by both training and live inference — and to
> materialize the training feature matrix.

## The Golden Rule

Every feature must be computable from **a chronological sequence of GameState objects for
one game, up to the current event, plus static pregame metadata**. No look-ahead. No
column that couldn't be produced live at that moment. Design the API around this:

```python
class FeatureBuilder:
    """Stateful, incremental feature computer for one game."""
    def __init__(self, pregame: PregameContext): ...
    def update(self, state: GameState) -> dict[str, float]:
        """Consume the next event, return the full feature vector for this moment."""

def build_offline(states: pd.DataFrame, pregame: PregameContext) -> pd.DataFrame:
    """Vectorized-ish offline path: MUST produce identical output to iterating
    FeatureBuilder.update() row by row. Enforced by a parity test."""
```

The **parity test** (offline vs incremental producing identical matrices on 20 sampled games)
is a required deliverable — it is what guarantees no training/serving skew.

## Checkpoint 2.1 — Core Clock & Score Features

| Feature | Definition | Notes |
|---|---|---|
| `score_diff` | home − away | raw |
| `seconds_remaining` | regulation seconds left; **overtime handling:** in OT, use `seconds_remaining_period` of the current OT and add feature `is_overtime` (0/1) + set `seconds_remaining = ot_seconds_remaining` | Document the choice clearly |
| `period` | 1–4, 5+ | |
| `diff_per_sqrt_time` | `score_diff / sqrt(seconds_remaining + 1)` | The classic WP interaction — a 5-pt lead with 30s left ≫ with 30 min left |
| `score_total` | home + away | pace proxy |
| `lead_changes_so_far` | running count | |
| `time_since_lead_change` | seconds | cap at 1200 |
| `largest_lead_home` / `largest_lead_away` | running max | |

## Checkpoint 2.2 — Game-Situation Features

- `possession` (1 / −1 / 0) and `possession_x_time`: possession value scaled by
  `1/sqrt(seconds_remaining+1)` — possession matters enormously late.
- `home_in_bonus`, `away_in_bonus`, `foul_diff_period`.
- `home_timeouts_remaining`, `away_timeouts_remaining` (impute median if None, add
  `timeouts_known` flag).
- `is_clutch`: last 5 min of 4th/OT AND |diff| ≤ 5.

## Checkpoint 2.3 — Rolling Momentum Features

All computed over trailing windows of **game clock time**, not event counts:
- `run_last_120s`: home points − away points over the trailing 120 seconds. Same for 300s.
- `scoring_rate_home_300s`, `scoring_rate_away_300s` (points per minute).
- `turnovers_last_300s_diff`, `fouls_last_300s_diff`.
- `momentum_ewm`: exponentially-weighted mean of per-event score changes (halflife ≈ 90
  game-seconds). Implement with an explicit decay formula so the incremental path is exact.

Keep the window bookkeeping inside `FeatureBuilder` with a deque of (timestamp, event)
tuples — this same code runs live in Phase 4.

## Checkpoint 2.4 — Pregame Context (small, optional but valuable)

`PregameContext` model: home/away team season win% entering the game, rest days, and a
simple Elo or net-rating differential. Build these from the Phase 1 game index only
(compute standings as-of game date — no leakage from future games). If time is short,
ship with just `pregame_win_pct_diff` and leave hooks for the rest.

## Checkpoint 2.5 — Materialize the Training Matrix

`python -m wp_engine.features build --season 2023-24`:
- Iterate all parsed games, run `build_offline`, attach label `home_win` and `game_id`,
  and write `data/processed/features_{season}.parquet`.
- **Row sampling policy:** play-by-play emits many rows per second of game time
  (substitutions, etc.). Downsample to at most one row per game-clock second per game
  (keep the last event at each second) to reduce redundancy; document this.
- Emit `data/models/feature_meta.json`: ordered feature list, dtypes, imputation values,
  and the code version hash. Inference in Phase 4 MUST read this file rather than
  hard-coding column names.

## Checkpoint 2.6 — Tests & Sanity Analysis

- The offline/incremental **parity test** (bit-identical on sampled games).
- No-leakage test: recompute features for a truncated game prefix and assert equality with
  the full-game matrix's corresponding rows.
- A short exploratory notebook/script printing sanity curves: average empirical home win
  rate bucketed by (`score_diff`, time bucket) — should look like the familiar WP fan chart.

## Deliverables & Handoff

1. `features.py` with `FeatureBuilder`, `build_offline`, `PregameContext`.
2. Processed feature matrices for all harvested seasons + `feature_meta.json`.
3. Passing tests, including parity and no-leakage.
4. `HANDOFF.md`: final feature list with one-line definitions, sampling policy, OT handling
   decision, matrix row counts, class balance, and anything Phase 3 must know.
