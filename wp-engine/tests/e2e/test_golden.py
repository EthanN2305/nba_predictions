"""Checkpoint 6.1 — golden-trajectory test.

The committed WP trajectory of DEN–LAL must be reproduced within 1e-6.
This is the project's silent-drift alarm: ANY change to feature logic,
parsing, or the pinned model shows up here. If a change is intentional,
regenerate with scripts/build_golden.py and say so in the commit message.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wp_engine.collect import states_path
from wp_engine.features import build_offline, build_pregame_context
from wp_engine.train import load_predictor

from tests.conftest import FIXTURE_SEASON

FIXTURES = Path(__file__).parent.parent / "fixtures"
GOLDEN_GAME = "0022300061"
TOLERANCE = 1e-6


def test_golden_trajectory_reproduced(fixture_data_dir):
    golden = json.loads((FIXTURES / "golden" / f"{GOLDEN_GAME}.json").read_text())
    states = pd.read_parquet(states_path(fixture_data_dir, FIXTURE_SEASON, GOLDEN_GAME))
    index = pd.read_parquet(FIXTURES / "game_index.parquet")

    features = build_offline(states, build_pregame_context(index, GOLDEN_GAME))
    wp = load_predictor(models_dir=FIXTURES / "models")(features)

    expected = golden["trajectory"]
    assert list(states["event_num"]) == [t["event_num"] for t in expected]
    diffs = np.abs(wp - np.array([t["wp_home"] for t in expected]))
    worst = int(np.argmax(diffs))
    assert diffs.max() <= TOLERANCE, (
        f"trajectory drifted: max |Δwp| = {diffs.max():.2e} at event "
        f"{expected[worst]['event_num']} — if intentional, regenerate via "
        "scripts/build_golden.py and document it"
    )


def test_golden_file_is_sane():
    golden = json.loads((FIXTURES / "golden" / f"{GOLDEN_GAME}.json").read_text())
    trajectory = golden["trajectory"]
    assert len(trajectory) > 300
    assert trajectory[-1]["wp_home"] > 0.95  # DEN won at home
    assert all(0 < t["wp_home"] < 1 for t in trajectory)
