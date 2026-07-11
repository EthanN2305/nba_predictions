"""Bootstrap a runnable demo data root from COMMITTED fixtures only.

A clean clone has no harvested data and no trained model (both gitignored).
This script makes `docker compose up` (and any fresh checkout) demo-ready:

- parses the 6 committed fixture games into $WP_DATA_DIR/raw/states/,
- copies the pinned test model into $WP_DATA_DIR/models/ if none exists.

Idempotent: existing files are left untouched, so a real harvested data
root / trained model always wins.

Usage: python scripts/bootstrap_demo.py  (or via the Docker entrypoint)
"""

import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wp_engine.collect import default_data_dir, parse_game, states_path  # noqa: E402
from wp_engine.schemas import GameRecord  # noqa: E402

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures"
SEASON = "2023-24"


def main() -> None:
    data_dir = default_data_dir()
    index_path = data_dir / "raw" / f"game_index_{SEASON}.parquet"
    fixture_index = pd.read_parquet(FIXTURES / "game_index.parquet")

    if not index_path.exists():
        index_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_index.to_parquet(index_path)
        print(f"wrote demo game index → {index_path}")

    for game_id in fixture_index["game_id"]:
        out = states_path(data_dir, SEASON, game_id)
        if out.exists():
            continue
        record = GameRecord(
            **fixture_index[fixture_index["game_id"] == game_id].iloc[0].to_dict()
        )
        pbp = pd.read_parquet(FIXTURES / "pbp" / f"{game_id}.parquet")
        out.parent.mkdir(parents=True, exist_ok=True)
        parse_game(game_id, pbp=pbp, record=record).to_parquet(out)
        print(f"parsed demo game {game_id}")

    models_dir = data_dir / "models"
    if not (models_dir / "model.pkl").exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        for name in ("model.pkl", "calibrator.pkl", "feature_meta.json"):
            shutil.copy(FIXTURES / "models" / name, models_dir / name)
        print(f"installed pinned demo model → {models_dir} "
              "(train the real one with `python -m wp_engine.train all`)")

    print("demo data root ready:", data_dir)


if __name__ == "__main__":
    main()
