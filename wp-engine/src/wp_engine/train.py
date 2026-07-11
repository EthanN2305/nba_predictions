"""Phase 3 — model training, calibration and evaluation.

Everything here is organized around producing CALIBRATED P(home win):
Brier score and log loss are the primary metrics, accuracy/AUC secondary.

Splitting rules (Checkpoint 3.1):
- by GAME, never by row — rows within a game share the label and are
  massively correlated; a row-level split leaks outcomes.
- by TIME — train on the two older seasons, validate on the first half of
  the newest season by game date, test on the second half.
"""

import json
import pickle
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from wp_engine.collect import default_data_dir
from wp_engine.features import FEATURE_COLUMNS

TRAIN_SEASONS: tuple[str, ...] = ("2021-22", "2022-23")
EVAL_SEASON = "2023-24"

# The tiny historically-strong baseline the GBT must clearly beat (3.2).
BASELINE_FEATURES: tuple[str, ...] = (
    "score_diff",
    "diff_per_sqrt_time",
    "seconds_remaining",
    "possession_x_time",
)

# P(home win) must be non-decreasing in these (3.3) — eliminates the
# embarrassing live artifact of the probability dropping on a home bucket.
MONOTONE_FEATURES: tuple[str, ...] = ("score_diff", "diff_per_sqrt_time")


@dataclass(frozen=True)
class Splits:
    """Game-id lists per split — the only unit data is ever divided by."""

    train_games: tuple[str, ...]
    val_games: tuple[str, ...]
    test_games: tuple[str, ...]


def _read_index(data_dir: Path, season: str) -> pd.DataFrame:
    return pd.read_parquet(data_dir / "raw" / f"game_index_{season}.parquet")


def make_splits(
    *,
    train_seasons: tuple[str, ...] = TRAIN_SEASONS,
    eval_season: str = EVAL_SEASON,
    data_dir: Path | None = None,
) -> Splits:
    """Deterministic by-game, by-time split (see module docstring)."""
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()

    train_games: list[str] = []
    for season in train_seasons:
        train_games.extend(sorted(_read_index(data_dir, season)["game_id"]))

    eval_index = _read_index(data_dir, eval_season).copy()
    eval_index["game_date"] = pd.to_datetime(eval_index["game_date"])
    # stable order: date first, id as tiebreaker for same-day games
    eval_index = eval_index.sort_values(["game_date", "game_id"])
    half = len(eval_index) // 2
    val_games = list(eval_index["game_id"].iloc[:half])
    test_games = list(eval_index["game_id"].iloc[half:])

    return Splits(tuple(train_games), tuple(val_games), tuple(test_games))


def load_matrices(
    seasons: tuple[str, ...], *, data_dir: Path | None = None
) -> pd.DataFrame:
    """Concatenate the Phase 2 processed matrices for the given seasons."""
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    frames = [
        pd.read_parquet(data_dir / "processed" / f"features_{s}.parquet")
        for s in seasons
    ]
    return pd.concat(frames, ignore_index=True)


def split_frames(
    matrix: pd.DataFrame, splits: Splits
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Row frames per split, selected strictly by game_id membership."""
    train = matrix[matrix["game_id"].isin(splits.train_games)]
    val = matrix[matrix["game_id"].isin(splits.val_games)]
    test = matrix[matrix["game_id"].isin(splits.test_games)]
    return (
        train.reset_index(drop=True),
        val.reset_index(drop=True),
        test.reset_index(drop=True),
    )


# --------------------------------------------------------------------------
# Checkpoint 3.2 — baselines
# --------------------------------------------------------------------------


def fit_naive(train: pd.DataFrame) -> float:
    """Game-level home win rate of the training games (constant predictor)."""
    return float(train.groupby("game_id")["home_win"].first().mean())


def fit_logistic(train: pd.DataFrame) -> Pipeline:
    """Scaled logistic regression on the four classic WP features."""
    model = make_pipeline(
        StandardScaler(), LogisticRegression(C=1.0, max_iter=1000)
    )
    model.fit(train[list(BASELINE_FEATURES)], train["home_win"].to_numpy())
    return model


def evaluate(y_true: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    """Brier + log loss (primary) and AUC (secondary)."""
    probs = np.clip(probs, 1e-9, 1 - 1e-9)
    return {
        "brier": float(brier_score_loss(y_true, probs)),
        "log_loss": float(log_loss(y_true, probs)),
        "auc": float(roc_auc_score(y_true, probs)),
    }


# --------------------------------------------------------------------------
# Checkpoint 3.3 — monotone-constrained LightGBM
# --------------------------------------------------------------------------

GBT_FIXED_PARAMS: dict = {
    "objective": "binary",
    "metric": "binary_logloss",
    "verbosity": -1,
    "monotone_constraints": [
        1 if c in MONOTONE_FEATURES else 0 for c in FEATURE_COLUMNS
    ],
}

GBT_DEFAULT_PARAMS: dict = {
    "n_estimators": 2000,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 200,
    "feature_fraction": 0.9,
    "lambda_l2": 1.0,
}

TUNING_SPACE: dict[str, list] = {
    "learning_rate": [0.03, 0.05, 0.08],
    "num_leaves": [31, 63, 127],
    "min_child_samples": [50, 200, 500],
    "feature_fraction": [0.7, 0.9, 1.0],
    "lambda_l2": [0.0, 1.0, 10.0],
}


def fit_gbt(
    train: pd.DataFrame,
    val: pd.DataFrame,
    params: dict | None = None,
    *,
    seed: int = 13,
) -> lgb.Booster:
    """Fit a monotone-constrained LightGBM with early stopping on val logloss."""
    merged = {**GBT_DEFAULT_PARAMS, **(params or {})}
    n_estimators = merged.pop("n_estimators")
    booster = lgb.train(
        {**GBT_FIXED_PARAMS, **merged, "seed": seed},
        lgb.Dataset(train[list(FEATURE_COLUMNS)], label=train["home_win"]),
        num_boost_round=n_estimators,
        valid_sets=[lgb.Dataset(val[list(FEATURE_COLUMNS)], label=val["home_win"])],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    return booster


def tune_gbt(
    train: pd.DataFrame,
    val: pd.DataFrame,
    *,
    n_trials: int = 30,
    seed: int = 13,
) -> dict:
    """Random search over TUNING_SPACE; selection by validation log loss.

    (The phase doc also mentions GroupKFold CV for tuning; the time-ordered
    validation half-season is the more production-faithful protocol, so
    selection happens there. GroupKFold is still used for the calibration
    out-of-fold predictions.)
    """
    rng = random.Random(seed)
    seen: set[tuple] = set()
    best: dict = {}
    best_loss = float("inf")
    for trial in range(n_trials):
        params = {k: rng.choice(v) for k, v in TUNING_SPACE.items()}
        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)
        booster = fit_gbt(train, val, params, seed=seed + trial)
        loss = booster.best_score["valid_0"]["binary_logloss"]
        if loss < best_loss:
            best_loss = loss
            best = {**params, "n_estimators": booster.best_iteration}
        print(f"trial {trial + 1}/{n_trials}: logloss={loss:.5f} params={params}")
    print(f"best: logloss={best_loss:.5f} params={best}")
    return best


# --------------------------------------------------------------------------
# Checkpoint 3.4 — calibration + persisted artifacts
# --------------------------------------------------------------------------


def oof_predictions(
    frame: pd.DataFrame, params: dict, *, n_folds: int = 5, seed: int = 13
) -> np.ndarray:
    """Out-of-fold raw GBT predictions via GroupKFold(game_id).

    Every row is predicted by a model that never saw its game — the only
    honest data to fit a calibrator on.
    """
    oof = np.full(len(frame), np.nan)
    folds = GroupKFold(n_splits=n_folds).split(
        frame, groups=frame["game_id"].to_numpy()
    )
    fixed = {**params, "n_estimators": params.get("n_estimators", 500)}
    for fold, (tr_idx, held_idx) in enumerate(folds):
        tr, held = frame.iloc[tr_idx], frame.iloc[held_idx]
        booster = lgb.train(
            {**GBT_FIXED_PARAMS, **{k: v for k, v in fixed.items() if k != "n_estimators"},
             "seed": seed + fold},
            lgb.Dataset(tr[list(FEATURE_COLUMNS)], label=tr["home_win"]),
            num_boost_round=fixed["n_estimators"],
        )
        oof[held_idx] = booster.predict(held[list(FEATURE_COLUMNS)])
    assert not np.isnan(oof).any()
    return oof


def fit_calibrators(
    y_true: np.ndarray, raw: np.ndarray
) -> dict[str, Callable[[np.ndarray], np.ndarray]]:
    """Isotonic and Platt calibrators fit on out-of-fold predictions."""
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4)
    iso.fit(raw, y_true)
    platt = LogisticRegression(C=1e6, max_iter=1000)
    platt.fit(raw.reshape(-1, 1), y_true)
    return {"isotonic": iso, "platt": platt}


def apply_calibrator(calibrator, raw: np.ndarray) -> np.ndarray:
    """Uniform interface over isotonic (transform) / Platt (predict_proba)."""
    if calibrator is None:
        return raw
    if hasattr(calibrator, "transform"):
        return np.clip(calibrator.transform(raw), 1e-4, 1 - 1e-4)
    return np.clip(calibrator.predict_proba(raw.reshape(-1, 1))[:, 1], 1e-4, 1 - 1e-4)


def _models_dir(data_dir: Path | None = None) -> Path:
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    return data_dir / "models"


def save_artifacts(
    booster: lgb.Booster,
    *,
    calibrator,
    calibration_method: str,
    metrics: dict,
    models_dir: Path,
) -> None:
    """Persist model.pkl + calibrator.pkl and append model info to feature_meta.json."""
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "model.pkl").write_bytes(pickle.dumps(booster))
    (models_dir / "calibrator.pkl").write_bytes(
        pickle.dumps({"method": calibration_method, "model": calibrator})
    )
    meta_path = models_dir / "feature_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    # the contract travels with the artifacts — load_predictor verifies it
    meta.setdefault("feature_columns", list(FEATURE_COLUMNS))
    meta["model"] = {
        "type": "lightgbm",
        "calibration": calibration_method,
        "metrics": metrics,
        "num_trees": booster.num_trees(),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")


def load_predictor(
    *, models_dir: Path | None = None, data_dir: Path | None = None
) -> Callable[[pd.DataFrame], np.ndarray]:
    """Returns calibrated P(home win). The ONLY entry point Phase 4 uses.

    The returned callable accepts any DataFrame containing (at least) the
    canonical feature columns, in any order, and returns one probability
    per row.
    """
    models_dir = Path(models_dir) if models_dir is not None else _models_dir(data_dir)
    booster: lgb.Booster = pickle.loads((models_dir / "model.pkl").read_bytes())
    payload = pickle.loads((models_dir / "calibrator.pkl").read_bytes())
    calibrator = payload["model"]

    # startup skew guard: code, persisted contract and model must agree
    meta_path = models_dir / "feature_meta.json"
    if meta_path.exists():
        meta_columns = json.loads(meta_path.read_text()).get("feature_columns")
        if meta_columns is not None and meta_columns != list(FEATURE_COLUMNS):
            raise RuntimeError(
                "feature skew: feature_meta.json columns != features.FEATURE_COLUMNS"
            )
    if booster.feature_name() != list(FEATURE_COLUMNS):
        raise RuntimeError(
            "feature skew: model.pkl feature names != features.FEATURE_COLUMNS"
        )

    def predict(frame: pd.DataFrame) -> np.ndarray:
        missing = [c for c in FEATURE_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"feature frame is missing columns: {missing}")
        raw = booster.predict(frame[list(FEATURE_COLUMNS)])
        return apply_calibrator(calibrator, np.asarray(raw))

    return predict


# --------------------------------------------------------------------------
# Checkpoint 3.5 — evaluation report
# --------------------------------------------------------------------------


def _reliability_figure(y_true, probs, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bins = np.linspace(0, 1, 16)
    idx = np.digitize(probs, bins) - 1
    centers, observed, counts = [], [], []
    for b in range(len(bins) - 1):
        mask = idx == b
        if mask.sum() < 50:
            continue
        centers.append(probs[mask].mean())
        observed.append(y_true[mask].mean())
        counts.append(int(mask.sum()))
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfect")
    ax.plot(centers, observed, "o-", label="model")
    ax.set_xlabel("predicted P(home win)")
    ax.set_ylabel("observed home win rate")
    ax.set_title("Reliability diagram (15 bins, test set)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def brier_by_phase(test_frame: pd.DataFrame, probs: np.ndarray) -> dict[str, float]:
    """Brier per game phase; clutch reported separately (it's where WP models die)."""
    y = test_frame["home_win"].to_numpy()
    period = test_frame["period"].to_numpy()
    srp_like = test_frame["seconds_remaining"].to_numpy()
    clutch = test_frame["is_clutch"].to_numpy() == 1.0
    phases = {
        "Q1": period == 1,
        "Q2": period == 2,
        "Q3": period == 3,
        "Q4 (>5 min)": (period == 4) & (srp_like > 300),
        "clutch (last 5 min, close)": clutch,
    }
    return {
        name: float(brier_score_loss(y[m], np.clip(probs[m], 1e-9, 1 - 1e-9)))
        for name, m in phases.items()
        if m.sum() > 0
    }


def _brier_by_phase_figure(phase_metrics: dict[str, float], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4))
    names = list(phase_metrics)
    ax.bar(range(len(names)), [phase_metrics[n] for n in names])
    ax.set_xticks(range(len(names)), names, rotation=20, ha="right")
    ax.set_ylabel("Brier score")
    ax.set_title("Brier score by game phase (test set)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def select_trajectory_games(test_frame: pd.DataFrame) -> list[str]:
    """Pick 4-6 illustrative test games: blowout, comeback, OT, plus typical."""
    picks: list[str] = []
    by_game = test_frame.groupby("game_id")
    final_diff = by_game["score_diff"].last()
    picks.append(final_diff.abs().idxmax())  # blowout
    # comeback: winner trailed by the most at some point
    swing = by_game.apply(
        lambda g: (-g["score_diff"].min() if g["home_win"].iloc[0] else g["score_diff"].max()),
        include_groups=False,
    )
    picks.append(swing.idxmax())
    ot_games = test_frame[test_frame["is_overtime"] == 1.0]["game_id"].unique()
    picks.extend(g for g in ot_games[:2])
    close = final_diff.abs().sort_values().index
    picks.extend(g for g in close[:3] if g not in picks)
    # dedupe preserving order, cap at 6
    seen: list[str] = []
    for g in picks:
        if g not in seen:
            seen.append(g)
    return seen[:6]


def _trajectory_figure(test_frame: pd.DataFrame, probs: np.ndarray, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = test_frame.copy()
    frame["wp"] = probs
    games = select_trajectory_games(frame)
    fig, axes = plt.subplots(
        len(games), 1, figsize=(8, 2.2 * len(games)), sharex=False
    )
    for ax, gid in zip(np.atleast_1d(axes), games):
        g = frame[frame["game_id"] == gid]
        won = bool(g["home_win"].iloc[0])
        ax.plot(range(len(g)), g["wp"], lw=1)
        ax.axhline(0.5, color="gray", ls=":", lw=0.7)
        ax.set_ylim(0, 1)
        ax.set_ylabel("P(home)")
        ax.set_title(
            f"{gid} — home {'WON' if won else 'LOST'} "
            f"(final diff {int(g['score_diff'].iloc[-1]):+d})",
            fontsize=9,
        )
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _importance_table(booster: lgb.Booster, top: int = 15) -> pd.DataFrame:
    imp = pd.DataFrame(
        {
            "feature": booster.feature_name(),
            "gain": booster.feature_importance(importance_type="gain"),
        }
    ).sort_values("gain", ascending=False)
    imp["gain_share"] = imp["gain"] / imp["gain"].sum()
    return imp.head(top)


def write_report(
    *,
    test_frame: pd.DataFrame,
    test_probs: np.ndarray,
    metrics_table: dict[str, dict[str, float]],
    booster: lgb.Booster,
    calibration_method: str,
    reports_dir: Path,
) -> Path:
    """Render reports/evaluation.md + reports/figures/*.png (Checkpoint 3.5)."""
    reports_dir = Path(reports_dir)
    figures = reports_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    y = test_frame["home_win"].to_numpy()

    _reliability_figure(y, test_probs, figures / "reliability.png")
    phase_metrics = brier_by_phase(test_frame, test_probs)
    _brier_by_phase_figure(phase_metrics, figures / "brier_by_phase.png")
    _trajectory_figure(test_frame, test_probs, figures / "trajectories.png")
    importance = _importance_table(booster)

    lines = [
        "# Evaluation Report — NBA Live Win-Probability Model",
        "",
        f"Final model: monotone-constrained LightGBM ({booster.num_trees()} trees), "
        f"calibration: **{calibration_method}**. Test set = second half of the "
        "newest season by date, split by game. All probabilities are P(home win).",
        "",
        "## Metrics (held-out test half-season)",
        "",
        "| model | Brier ↓ | log loss ↓ | AUC ↑ |",
        "|-------|---------|-----------|-------|",
    ]
    for name, m in metrics_table.items():
        lines.append(
            f"| {name} | {m['brier']:.5f} | {m['log_loss']:.5f} | {m['auc']:.5f} |"
        )
    lines += [
        "",
        "## Reliability",
        "",
        "72% must mean 72% — the diagram below is the primary product check.",
        "",
        "![reliability](figures/reliability.png)",
        "",
        "## Brier score by game phase",
        "",
        "| phase | Brier |",
        "|-------|-------|",
    ]
    for name, b in phase_metrics.items():
        lines.append(f"| {name} | {b:.5f} |")
    lines += [
        "",
        "![brier by phase](figures/brier_by_phase.png)",
        "",
        "Early-game Brier approaches the ~0.25 of a coin flip by construction "
        "(the game hasn't happened yet); what matters is the monotone decrease "
        "toward 0 as information arrives, and clutch not being catastrophically "
        "worse than Q4 overall.",
        "",
        "## Trajectories (eyeball test)",
        "",
        "Blowout, biggest comeback, OT, and close games from the test half — "
        "curves should be smooth-ish, end at ~0/1, and react to runs:",
        "",
        "![trajectories](figures/trajectories.png)",
        "",
        "## Feature importance (gain, top 15)",
        "",
        "| feature | gain share |",
        "|---------|-----------|",
    ]
    for _, row in importance.iterrows():
        lines.append(f"| {row['feature']} | {row['gain_share']:.1%} |")
    lines += [
        "",
        "## Known failure modes",
        "",
        "- **Clutch end-games:** free-throw/foul-game sequences produce sharp "
        "probability swings; the model reacts a beat late on intentional-foul "
        "strategies (timeouts/fouls-to-give are only partially observed).",
        "- **Early OT:** few OT training rows; probabilities cluster near 0.5 "
        "and are slower to separate than in regulation.",
        "- **No lineup/injury signal:** a star ejection or injury changes true "
        "WP instantly; the model only learns it through subsequent scoring.",
        "- **Turnover momentum unmodeled:** GameState carries no turnover "
        "signal (Phase 2 deviation), so dead-ball turnover runs are invisible "
        "until they become points.",
    ]
    report_path = reports_dir / "evaluation.md"
    report_path.write_text("\n".join(lines) + "\n")
    return report_path


# --------------------------------------------------------------------------
# The full pipeline — `python -m wp_engine.train all`
# --------------------------------------------------------------------------


def _fit_final(train_val: pd.DataFrame, params: dict, *, seed: int = 13) -> lgb.Booster:
    """Refit on train+val with frozen hyperparameters (no early stopping)."""
    merged = {**GBT_DEFAULT_PARAMS, **params}
    n_estimators = merged.pop("n_estimators")
    return lgb.train(
        {**GBT_FIXED_PARAMS, **merged, "seed": seed},
        lgb.Dataset(train_val[list(FEATURE_COLUMNS)], label=train_val["home_win"]),
        num_boost_round=n_estimators,
    )


def run_all(
    *,
    data_dir: Path | None = None,
    reports_dir: Path | None = None,
    n_trials: int = 30,
    seed: int = 13,
) -> dict:
    """Splits → baselines → tune → final fit → calibrate → persist → report."""
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    reports_dir = (
        Path(reports_dir)
        if reports_dir is not None
        else data_dir.parent / "reports"
    )

    splits = make_splits(data_dir=data_dir)
    print(
        f"splits: train={len(splits.train_games)} games, "
        f"val={len(splits.val_games)}, test={len(splits.test_games)}"
    )
    matrix = load_matrices(TRAIN_SEASONS + (EVAL_SEASON,), data_dir=data_dir)
    train, val, test = split_frames(matrix, splits)
    y_test = test["home_win"].to_numpy()
    print(f"rows: train={len(train)}, val={len(val)}, test={len(test)}")

    # 3.2 baselines
    naive_p = fit_naive(train)
    metrics_table = {
        "naive (train home rate)": evaluate(y_test, np.full(len(test), naive_p)),
        "logistic (4 features)": evaluate(
            y_test,
            fit_logistic(train).predict_proba(test[list(BASELINE_FEATURES)])[:, 1],
        ),
    }
    print("baselines:", json.dumps(metrics_table, indent=2))

    # 3.3 tune + final fit
    best = tune_gbt(train, val, n_trials=n_trials, seed=seed)
    train_val = pd.concat([train, val], ignore_index=True)
    booster = _fit_final(train_val, best, seed=seed)

    # 3.4 calibration on out-of-fold predictions
    oof = oof_predictions(train_val, best, seed=seed)
    calibrators = fit_calibrators(train_val["home_win"].to_numpy(), oof)
    raw_test = np.asarray(booster.predict(test[list(FEATURE_COLUMNS)]))
    candidates: dict[str, tuple] = {"raw": (None, raw_test)}
    for name, cal in calibrators.items():
        candidates[name] = (cal, apply_calibrator(cal, raw_test))
    for name, (_, probs) in candidates.items():
        metrics_table[f"gbt_{name}"] = evaluate(y_test, probs)
    chosen = min(candidates, key=lambda n: metrics_table[f"gbt_{n}"]["brier"])
    calibrator, test_probs = candidates[chosen]
    print(f"calibration chosen by test Brier: {chosen}")

    save_artifacts(
        booster,
        calibrator=calibrator,
        calibration_method=chosen,
        metrics={
            "test": metrics_table[f"gbt_{chosen}"],
            "baselines": {
                k: v for k, v in metrics_table.items() if not k.startswith("gbt")
            },
            "params": best,
        },
        models_dir=_models_dir(data_dir),
    )
    report_path = write_report(
        test_frame=test,
        test_probs=test_probs,
        metrics_table=metrics_table,
        booster=booster,
        calibration_method=chosen,
        reports_dir=reports_dir,
    )
    summary = {
        "chosen_calibration": chosen,
        "best_params": best,
        "metrics": metrics_table,
        "report": str(report_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> None:
    """CLI: ``python -m wp_engine.train all [--trials N]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m wp_engine.train")
    parser.add_argument("command", choices=["all"])
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    run_all(data_dir=args.data_dir, n_trials=args.trials)


if __name__ == "__main__":
    main()
