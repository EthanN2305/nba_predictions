"""Tests for collect.build_game_index (Checkpoint 1.2)."""

import pandas as pd
import pytest

from wp_engine.collect import build_game_index

GAME_INDEX_COLUMNS = [
    "game_id",
    "season",
    "game_date",
    "home_team_id",
    "away_team_id",
    "home_team_abbr",
    "away_team_abbr",
    "final_home_score",
    "final_away_score",
    "home_win",
]


def team_rows(
    game_id="0022300001",
    game_date="2023-10-24",
    home=("DEN", 1610612743, 119),
    away=("LAL", 1610612747, 107),
):
    """Two rows (one per team) as returned by LeagueGameLog for one game."""
    h_abbr, h_id, h_pts = home
    a_abbr, a_id, a_pts = away
    return [
        {
            "SEASON_ID": "22023",
            "TEAM_ID": h_id,
            "TEAM_ABBREVIATION": h_abbr,
            "GAME_ID": game_id,
            "GAME_DATE": game_date,
            "MATCHUP": f"{h_abbr} vs. {a_abbr}",
            "WL": "W" if h_pts > a_pts else "L",
            "PTS": h_pts,
        },
        {
            "SEASON_ID": "22023",
            "TEAM_ID": a_id,
            "TEAM_ABBREVIATION": a_abbr,
            "GAME_ID": game_id,
            "GAME_DATE": game_date,
            "MATCHUP": f"{a_abbr} @ {h_abbr}",
            "WL": "L" if h_pts > a_pts else "W",
            "PTS": a_pts,
        },
    ]


def fake_fetch_factory(rows):
    df = pd.DataFrame(rows)

    def fetch(season: str) -> pd.DataFrame:
        return df.copy()

    return fetch


class TestBuildGameIndex:
    def test_collapses_team_rows_to_one_game_row(self, tmp_path):
        fetch = fake_fetch_factory(team_rows())
        index = build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)

        assert len(index) == 1
        row = index.iloc[0]
        assert row["game_id"] == "0022300001"
        assert row["home_team_abbr"] == "DEN"
        assert row["away_team_abbr"] == "LAL"
        assert row["home_team_id"] == 1610612743
        assert row["away_team_id"] == 1610612747
        assert row["final_home_score"] == 119
        assert row["final_away_score"] == 107
        assert bool(row["home_win"]) is True

    def test_home_loss_labelled_false(self, tmp_path):
        fetch = fake_fetch_factory(
            team_rows(home=("DEN", 1610612743, 100), away=("LAL", 1610612747, 110))
        )
        index = build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)
        assert bool(index.iloc[0]["home_win"]) is False

    def test_has_exactly_game_record_columns(self, tmp_path):
        fetch = fake_fetch_factory(team_rows())
        index = build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)
        assert list(index.columns) == GAME_INDEX_COLUMNS

    def test_writes_parquet_to_raw_dir(self, tmp_path):
        fetch = fake_fetch_factory(team_rows())
        build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)
        out = tmp_path / "raw" / "game_index_2023-24.parquet"
        assert out.exists()
        assert len(pd.read_parquet(out)) == 1

    def test_skips_game_missing_counterpart_row(self, tmp_path):
        rows = team_rows() + team_rows(game_id="0022300002")[:1]  # second game: home row only
        fetch = fake_fetch_factory(rows)
        index = build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)
        assert list(index["game_id"]) == ["0022300001"]

    def test_sorted_by_date_then_game_id(self, tmp_path):
        rows = team_rows(
            game_id="0022300009", game_date="2023-10-26"
        ) + team_rows(game_id="0022300002", game_date="2023-10-24")
        fetch = fake_fetch_factory(rows)
        index = build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)
        assert list(index["game_id"]) == ["0022300002", "0022300009"]

    def test_rows_validate_as_game_records(self, tmp_path):
        from wp_engine.schemas import GameRecord

        fetch = fake_fetch_factory(team_rows())
        index = build_game_index("2023-24", data_dir=tmp_path, fetch=fetch)
        rec = GameRecord(**index.iloc[0].to_dict())
        assert rec.season == "2023-24"
