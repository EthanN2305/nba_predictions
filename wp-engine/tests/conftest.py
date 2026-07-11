"""Shared fixtures: tiny trained artifacts + a miniature data root so
live/API/replay tests never depend on the real (gitignored) model files or
harvested data."""

from pathlib import Path

import pandas as pd
import pytest

from wp_engine.features import FEATURE_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_GAMES = ["0022300061", "0022300062", "0022300083"]
FIXTURE_SEASON = "2023-24"


@pytest.fixture(scope="session")
def fixture_data_dir(tmp_path_factory) -> Path:
    """Phase 1-shaped data root built from the 3 committed real games."""
    from wp_engine.collect import parse_game, states_path
    from wp_engine.schemas import GameRecord

    root = tmp_path_factory.mktemp("fixture_data")
    (root / "raw").mkdir()
    index = pd.read_parquet(FIXTURES / "game_index.parquet")
    index.to_parquet(root / "raw" / f"game_index_{FIXTURE_SEASON}.parquet")
    for game_id in FIXTURE_GAMES:
        record = GameRecord(**index[index["game_id"] == game_id].iloc[0].to_dict())
        pbp = pd.read_parquet(FIXTURES / "pbp" / f"{game_id}.parquet")
        out = states_path(root, FIXTURE_SEASON, game_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        parse_game(game_id, pbp=pbp, record=record).to_parquet(out)
    return root


@pytest.fixture(scope="session")
def tiny_models_dir(tmp_path_factory):
    """A 30-tree GBT + identity calibrator persisted to a temp models dir."""
    from sklearn.isotonic import IsotonicRegression

    from tests.test_model import synthetic_matrix
    from wp_engine.train import fit_gbt, save_artifacts

    matrix = synthetic_matrix()
    out = tmp_path_factory.mktemp("tiny_models")
    booster = fit_gbt(matrix, matrix, {"n_estimators": 30}, seed=13)
    raw = booster.predict(matrix[list(FEATURE_COLUMNS)])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    iso.fit(raw, matrix["home_win"].to_numpy())
    save_artifacts(
        booster,
        calibrator=iso,
        calibration_method="isotonic",
        metrics={},
        models_dir=out,
    )
    return out
