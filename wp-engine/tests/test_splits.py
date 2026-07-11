"""Checkpoint 3.1 — correct data splitting.

Split by GAME (never by row) and by TIME: train = two older seasons,
validation = first half of the newest season by date, test = second half.
"""

from pathlib import Path

import pandas as pd
import pytest

from wp_engine.train import make_splits

TRAIN_SEASONS = ("2021-22", "2022-23")
EVAL_SEASON = "2023-24"


def _index(season: str, games: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": gid,
                "season": season,
                "game_date": date,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_team_abbr": "H",
                "away_team_abbr": "A",
                "final_home_score": 100,
                "final_away_score": 90,
                "home_win": True,
            }
            for gid, date in games
        ]
    )


@pytest.fixture()
def data_dir(tmp_path) -> Path:
    (tmp_path / "raw").mkdir()
    _index("2021-22", [("0022100001", "2021-10-19"), ("0022100002", "2021-10-20")]).to_parquet(
        tmp_path / "raw" / "game_index_2021-22.parquet"
    )
    _index("2022-23", [("0022200001", "2022-10-18"), ("0022200002", "2022-10-19")]).to_parquet(
        tmp_path / "raw" / "game_index_2022-23.parquet"
    )
    _index(
        "2023-24",
        [
            ("0022300004", "2024-01-15"),  # deliberately out of id order
            ("0022300001", "2023-10-24"),
            ("0022300003", "2024-01-10"),
            ("0022300002", "2023-10-25"),
        ],
    ).to_parquet(tmp_path / "raw" / "game_index_2023-24.parquet")
    return tmp_path


class TestMakeSplits:
    def test_no_game_appears_in_two_splits(self, data_dir):
        splits = make_splits(data_dir=data_dir)
        train, val, test = set(splits.train_games), set(splits.val_games), set(splits.test_games)
        assert not train & val and not train & test and not val & test

    def test_train_is_the_two_older_seasons(self, data_dir):
        splits = make_splits(data_dir=data_dir)
        assert set(splits.train_games) == {
            "0022100001",
            "0022100002",
            "0022200001",
            "0022200002",
        }

    def test_eval_season_halved_by_date_not_by_id(self, data_dir):
        splits = make_splits(data_dir=data_dir)
        # date order: 001 (Oct 24), 002 (Oct 25), 003 (Jan 10), 004 (Jan 15)
        assert set(splits.val_games) == {"0022300001", "0022300002"}
        assert set(splits.test_games) == {"0022300003", "0022300004"}

    def test_split_is_deterministic(self, data_dir):
        a, b = make_splits(data_dir=data_dir), make_splits(data_dir=data_dir)
        assert a.train_games == b.train_games
        assert a.val_games == b.val_games
        assert a.test_games == b.test_games
