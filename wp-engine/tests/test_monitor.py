"""Checkpoint 6.2 — the drift monitor: realized Brier vs the training-time
benchmark, with a retraining recommendation on >20% relative degradation."""

from pathlib import Path

import pandas as pd
import pytest

from wp_engine.collect import states_path
from wp_engine.monitor import evaluate_states, recommendation
from wp_engine.train import load_predictor

from tests.conftest import FIXTURE_GAMES, FIXTURE_SEASON

PINNED_MODELS = Path(__file__).parent / "fixtures" / "models"


class TestEvaluateStates:
    def test_reports_brier_per_game_and_overall(self, fixture_data_dir):
        predictor = load_predictor(models_dir=PINNED_MODELS)
        index = pd.read_parquet(
            fixture_data_dir / "raw" / f"game_index_{FIXTURE_SEASON}.parquet"
        )
        states = {
            gid: pd.read_parquet(states_path(fixture_data_dir, FIXTURE_SEASON, gid))
            for gid in FIXTURE_GAMES[:2]
        }
        report = evaluate_states(states, predictor, game_index=index)
        assert set(report["per_game"]) == set(FIXTURE_GAMES[:2])
        assert 0 < report["brier"] < 0.25  # far better than a coin flip
        assert report["n_events"] > 500


class TestRecommendation:
    def test_ok_when_within_20_percent(self):
        assert recommendation(live_brier=0.17, benchmark=0.1565).startswith("OK")

    def test_retrain_when_degraded(self):
        message = recommendation(live_brier=0.20, benchmark=0.1565)
        assert "RETRAIN" in message

    @pytest.mark.parametrize("brier", [0.1565, 0.10])
    def test_better_than_benchmark_is_ok(self, brier):
        assert recommendation(live_brier=brier, benchmark=0.1565).startswith("OK")
