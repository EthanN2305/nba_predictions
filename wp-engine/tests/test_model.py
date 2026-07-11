"""Checkpoints 3.2-3.4 — baselines, monotone-constrained GBT, calibration
artifacts and the ``load_predictor`` contract.

Runs on small synthetic matrices so the suite stays fast; checks against the
real trained artifacts live in tests/test_latency.py (skipped if absent).
"""

import numpy as np
import pandas as pd
import pytest

from wp_engine.features import FEATURE_COLUMNS
from wp_engine.train import (
    BASELINE_FEATURES,
    evaluate,
    fit_gbt,
    fit_logistic,
    fit_naive,
    load_predictor,
    save_artifacts,
)

RNG = np.random.default_rng(13)


def synthetic_matrix(n_games: int = 60, rows_per_game: int = 40) -> pd.DataFrame:
    """Synthetic training matrix: outcome driven by late score_diff."""
    frames = []
    for g in range(n_games):
        home_win = g % 2 == 0
        drift = 1.0 if home_win else -1.0
        seconds = np.linspace(2880, 0, rows_per_game)
        diff = np.cumsum(RNG.normal(drift * 0.5, 2.0, rows_per_game)).round()
        df = pd.DataFrame(0.0, index=range(rows_per_game), columns=list(FEATURE_COLUMNS))
        df["score_diff"] = diff
        df["seconds_remaining"] = seconds
        df["diff_per_sqrt_time"] = diff / np.sqrt(seconds + 1)
        df["period"] = np.ceil((2880 - seconds + 1) / 720).clip(1, 4)
        df["possession_x_time"] = RNG.choice([-1, 0, 1], rows_per_game) / np.sqrt(seconds + 1)
        df["game_id"] = f"00223{g:05d}"
        df["event_num"] = range(rows_per_game)
        df["home_win"] = home_win
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def matrix() -> pd.DataFrame:
    return synthetic_matrix()


class TestBaselines:
    def test_naive_is_game_level_home_win_rate(self):
        # 1 winning game with many rows + 3 losing games with few rows:
        # row-level rate would be 0.7, game-level must be 0.25
        frames = []
        for g, (win, rows) in enumerate([(True, 21), (False, 3), (False, 3), (False, 3)]):
            df = pd.DataFrame(0.0, index=range(rows), columns=list(FEATURE_COLUMNS))
            df["game_id"] = str(g)
            df["home_win"] = win
            frames.append(df)
        assert fit_naive(pd.concat(frames, ignore_index=True)) == 0.25

    def test_logistic_uses_four_features_and_beats_chance(self, matrix):
        model = fit_logistic(matrix)
        probs = model.predict_proba(matrix[list(BASELINE_FEATURES)])[:, 1]
        assert ((probs > 0) & (probs < 1)).all()
        metrics = evaluate(matrix["home_win"].to_numpy(), probs)
        assert metrics["brier"] < 0.25  # clearly better than coin-flip

    def test_evaluate_returns_brier_logloss_auc(self, matrix):
        y = matrix["home_win"].to_numpy()
        metrics = evaluate(y, np.full(len(y), 0.5))
        assert metrics["brier"] == pytest.approx(0.25)
        assert metrics["log_loss"] == pytest.approx(np.log(2), rel=1e-3)
        assert metrics["auc"] == pytest.approx(0.5)


class TestGBT:
    def test_probability_monotone_in_score_diff(self, matrix):
        booster = fit_gbt(matrix, matrix, {"n_estimators": 30}, seed=13)
        probe = pd.DataFrame(0.0, index=range(41), columns=list(FEATURE_COLUMNS))
        probe["score_diff"] = np.linspace(-20, 20, 41)
        probe["seconds_remaining"] = 600.0
        probe["diff_per_sqrt_time"] = probe["score_diff"] / np.sqrt(601.0)
        preds = booster.predict(probe[list(FEATURE_COLUMNS)])
        assert (np.diff(preds) >= -1e-12).all(), "P(home win) dropped as score_diff rose"

    def test_gbt_beats_chance_on_train(self, matrix):
        booster = fit_gbt(matrix, matrix, {"n_estimators": 30}, seed=13)
        preds = booster.predict(matrix[list(FEATURE_COLUMNS)])
        assert evaluate(matrix["home_win"].to_numpy(), preds)["brier"] < 0.2


@pytest.fixture(scope="module")
def models_dir(tmp_path_factory, matrix):
    """Tiny trained artifacts persisted to a temp models dir."""
    out = tmp_path_factory.mktemp("models")
    booster = fit_gbt(matrix, matrix, {"n_estimators": 30}, seed=13)
    raw = booster.predict(matrix[list(FEATURE_COLUMNS)])
    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    iso.fit(raw, matrix["home_win"].to_numpy())
    save_artifacts(
        booster,
        calibrator=iso,
        calibration_method="isotonic",
        metrics={"test_brier": 0.1},
        models_dir=out,
    )
    return out


class TestPredictorArtifacts:
    def test_roundtrip_produces_valid_probabilities(self, models_dir, matrix):
        predict = load_predictor(models_dir=models_dir)
        probs = predict(matrix[list(FEATURE_COLUMNS)].head(100))
        assert probs.shape == (100,)
        assert not np.isnan(probs).any()
        assert ((probs > 0) & (probs < 1)).all()

    def test_predictor_accepts_extra_columns_but_requires_features(
        self, models_dir, matrix
    ):
        predict = load_predictor(models_dir=models_dir)
        with_extras = matrix.head(5)  # includes game_id/event_num/home_win
        assert predict(with_extras).shape == (5,)
        with pytest.raises(ValueError, match="missing"):
            predict(matrix[["score_diff"]].head(5))

    def test_edge_states_no_nans_probs_in_open_interval(self, models_dir):
        predict = load_predictor(models_dir=models_dir)
        tipoff = {c: 0.0 for c in FEATURE_COLUMNS}
        tipoff.update(seconds_remaining=2880.0, period=1.0, home_timeouts_remaining=7.0,
                      away_timeouts_remaining=7.0, timeouts_known=1.0,
                      pregame_win_pct_home=0.5, pregame_win_pct_away=0.5,
                      rest_days_home=2.0, rest_days_away=2.0)
        tie_end = dict(tipoff, seconds_remaining=0.0, period=4.0, is_clutch=1.0)
        blowout = dict(tipoff, score_diff=40.0, seconds_remaining=600.0, period=4.0,
                       diff_per_sqrt_time=40.0 / np.sqrt(601.0), score_total=220.0)
        comeback = dict(tipoff, score_diff=-40.0, seconds_remaining=600.0, period=4.0,
                        diff_per_sqrt_time=-40.0 / np.sqrt(601.0), score_total=220.0)
        ot = dict(tipoff, seconds_remaining=120.0, period=5.0, is_overtime=1.0,
                  is_clutch=1.0, score_diff=2.0, diff_per_sqrt_time=2.0 / np.sqrt(121.0))
        imputed = dict(tipoff, home_timeouts_remaining=5.0, away_timeouts_remaining=5.0,
                       timeouts_known=0.0)
        frame = pd.DataFrame([tipoff, tie_end, blowout, comeback, ot, imputed])
        probs = predict(frame)
        assert not np.isnan(probs).any()
        assert ((probs > 0) & (probs < 1)).all()
        assert probs[2] > probs[0] > probs[3]  # blowout > tipoff > comeback


class TestReport:
    def test_write_report_produces_markdown_and_figures(self, tmp_path, matrix):
        from wp_engine.train import write_report

        booster = fit_gbt(matrix, matrix, {"n_estimators": 20}, seed=13)
        raw = booster.predict(matrix[list(FEATURE_COLUMNS)])
        metrics_table = {
            "naive": evaluate(matrix["home_win"].to_numpy(), np.full(len(matrix), 0.55)),
            "gbt_raw": evaluate(matrix["home_win"].to_numpy(), raw),
        }
        report_path = write_report(
            test_frame=matrix,
            test_probs=raw,
            metrics_table=metrics_table,
            booster=booster,
            calibration_method="isotonic",
            reports_dir=tmp_path,
        )
        assert report_path.exists()
        text = report_path.read_text()
        assert "Reliability" in text and "gbt_raw" in text
        figures = list((tmp_path / "figures").glob("*.png"))
        assert len(figures) >= 3  # reliability, brier-by-phase, trajectories
