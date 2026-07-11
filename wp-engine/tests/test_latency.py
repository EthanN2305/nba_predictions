"""Checkpoint 3.6 — latency & robustness against the REAL trained artifacts.

These tests are skipped when `data/models/model.pkl` is absent (fresh clone:
run `python -m wp_engine.train all` first). Mechanics-level equivalents that
always run live in tests/test_model.py.
"""

import time

import numpy as np
import pandas as pd
import pytest

from wp_engine.collect import default_data_dir
from wp_engine.features import FEATURE_COLUMNS

MODELS_DIR = default_data_dir() / "models"

pytestmark = pytest.mark.skipif(
    not (MODELS_DIR / "model.pkl").exists(),
    reason="real model artifacts not trained yet (python -m wp_engine.train all)",
)


@pytest.fixture(scope="module")
def predictor():
    from wp_engine.train import load_predictor

    return load_predictor(models_dir=MODELS_DIR)


def _state_row(**overrides) -> dict:
    row = {c: 0.0 for c in FEATURE_COLUMNS}
    row.update(
        seconds_remaining=2880.0,
        period=1.0,
        home_timeouts_remaining=7.0,
        away_timeouts_remaining=7.0,
        timeouts_known=1.0,
        pregame_win_pct_home=0.5,
        pregame_win_pct_away=0.5,
        rest_days_home=2.0,
        rest_days_away=2.0,
    )
    row.update(overrides)
    return row


class TestLatency:
    def test_single_row_inference_under_10ms(self, predictor):
        frame = pd.DataFrame([_state_row()])
        predictor(frame)  # warmup (lazy allocations)
        n = 50
        start = time.perf_counter()
        for _ in range(n):
            predictor(frame)
        per_call_ms = (time.perf_counter() - start) / n * 1000
        assert per_call_ms < 10, f"single-row inference took {per_call_ms:.2f} ms"


class TestRealModelEdgeStates:
    def test_edge_states_valid_and_ordered(self, predictor):
        states = pd.DataFrame(
            [
                _state_row(),  # tip-off
                _state_row(seconds_remaining=0.0, period=4.0, is_clutch=1.0),  # tie, 0:00
                _state_row(
                    score_diff=40.0, seconds_remaining=600.0, period=4.0,
                    diff_per_sqrt_time=40.0 / np.sqrt(601.0), score_total=220.0,
                ),
                _state_row(
                    score_diff=-40.0, seconds_remaining=600.0, period=4.0,
                    diff_per_sqrt_time=-40.0 / np.sqrt(601.0), score_total=220.0,
                ),
                _state_row(
                    seconds_remaining=120.0, period=5.0, is_overtime=1.0,
                    is_clutch=1.0, score_diff=2.0,
                    diff_per_sqrt_time=2.0 / np.sqrt(121.0), score_total=200.0,
                ),
                _state_row(
                    home_timeouts_remaining=5.0, away_timeouts_remaining=5.0,
                    timeouts_known=0.0,
                ),  # imputation path
            ]
        )
        probs = predictor(states)
        assert not np.isnan(probs).any()
        assert ((probs > 0) & (probs < 1)).all()
        assert probs[2] > 0.95, "40-pt lead with 10 min left must be near-certain"
        assert probs[3] < 0.05
        # tie at the end of regulation should be a rough coin flip
        assert 0.3 < probs[1] < 0.7

    def test_tipoff_reflects_home_court_advantage(self, predictor):
        probs = predictor(pd.DataFrame([_state_row()]))
        assert 0.5 < probs[0] < 0.65
