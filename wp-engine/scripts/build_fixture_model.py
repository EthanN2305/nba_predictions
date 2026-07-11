"""Regenerate the PINNED test model in tests/fixtures/models/.

This model is committed so the e2e replay suite and the golden-trajectory
test are deterministic on fresh clones (real artifacts in data/models/ are
gitignored). Regenerating it invalidates tests/fixtures/golden/ — rerun
scripts/build_golden.py afterwards and note the change in the commit message.

Usage: .venv/bin/python scripts/build_fixture_model.py
"""

import pickle
import sys
from pathlib import Path

import lightgbm as lgb

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wp_engine.features import FEATURE_COLUMNS  # noqa: E402
from wp_engine.train import (  # noqa: E402
    GBT_FIXED_PARAMS,
    load_matrices,
    save_artifacts,
)

OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "models"
N_GAMES = 400  # compact but realistic
PARAMS = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 50,
    "feature_fraction": 0.7,
    "lambda_l2": 1.0,
    "seed": 13,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,  # bitwise reproducibility for the golden test
}
N_TREES = 150


def main() -> None:
    matrix = load_matrices(("2021-22", "2022-23"))
    games = sorted(matrix["game_id"].unique())[:N_GAMES]
    sample = matrix[matrix["game_id"].isin(games)]
    print(f"training on {len(games)} games / {len(sample)} rows")
    booster = lgb.train(
        {**GBT_FIXED_PARAMS, **PARAMS},
        lgb.Dataset(sample[list(FEATURE_COLUMNS)], label=sample["home_win"]),
        num_boost_round=N_TREES,
    )
    save_artifacts(
        booster,
        calibrator=None,
        calibration_method="raw",
        metrics={"note": "pinned fixture model — tests only, not the product model"},
        models_dir=OUT,
    )
    size_kb = (OUT / "model.pkl").stat().st_size // 1024
    print(f"wrote {OUT}/model.pkl ({size_kb} KB)")


if __name__ == "__main__":
    main()
