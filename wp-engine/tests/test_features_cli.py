"""Checkpoint 2.5 — materializing the training matrix.

Covers the per-game matrix builder (features computed on the FULL event
stream, then downsampled to at most one row per game-clock second, keeping
the last event of each second), the season build CLI, feature_meta.json,
and the harvested-data parity command.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from wp_engine.collect import parse_game, states_path
from wp_engine.features import (
    FEATURE_COLUMNS,
    PregameContext,
    build_game_matrix,
    build_season,
    check_parity,
    game_elapsed_seconds,
    sanity_table,
)
from wp_engine.schemas import GameRecord

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_GAMES = ["0022300061", "0022300062", "0022300083"]
SEASON = "2023-24"

MATRIX_EXTRA_COLUMNS = ["game_id", "event_num", "home_win"]


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory) -> Path:
    """A miniature Phase 1 data root built from the 3 committed fixtures."""
    root = tmp_path_factory.mktemp("data")
    (root / "raw").mkdir()
    index = pd.read_parquet(FIXTURES / "game_index.parquet")
    index.to_parquet(root / "raw" / f"game_index_{SEASON}.parquet")
    for game_id in FIXTURE_GAMES:
        record = GameRecord(**index[index["game_id"] == game_id].iloc[0].to_dict())
        pbp = pd.read_parquet(FIXTURES / "pbp" / f"{game_id}.parquet")
        states = parse_game(game_id, pbp=pbp, record=record)
        out = states_path(root, SEASON, game_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        states.to_parquet(out)
    return root


@pytest.fixture(scope="module")
def fixture_states(data_dir) -> pd.DataFrame:
    return pd.read_parquet(states_path(data_dir, SEASON, "0022300061"))


class TestBuildGameMatrix:
    def test_at_most_one_row_per_game_clock_second(self, fixture_states):
        matrix = build_game_matrix(fixture_states, PregameContext())
        seconds = [
            int(game_elapsed_seconds(int(p), float(s)))
            for p, s in zip(fixture_states["period"], fixture_states["seconds_remaining_period"])
        ]
        assert len(matrix) == len(set(seconds))

    def test_keeps_last_event_of_each_second(self, fixture_states):
        matrix = build_game_matrix(fixture_states, PregameContext())
        # the final event of the game always survives downsampling
        assert matrix["event_num"].iloc[-1] == fixture_states["event_num"].iloc[-1]
        # kept rows are the LAST raw event at each second: every kept
        # event_num is the max among the raw rows sharing its second
        elapsed = [
            int(game_elapsed_seconds(int(p), float(s)))
            for p, s in zip(fixture_states["period"], fixture_states["seconds_remaining_period"])
        ]
        raw = pd.DataFrame(
            {"sec": elapsed, "event_num": fixture_states["event_num"].to_numpy()}
        )
        last_per_sec = raw.groupby("sec")["event_num"].max()
        assert set(matrix["event_num"]) == set(last_per_sec)

    def test_features_computed_on_full_stream_before_downsampling(self, fixture_states):
        matrix = build_game_matrix(fixture_states, PregameContext())
        # score_total at the final row must equal the true final total, which
        # is only correct if every raw event was fed through the builder
        final = fixture_states.iloc[-1]
        assert matrix["score_total"].iloc[-1] == float(
            final["home_score"] + final["away_score"]
        )

    def test_matrix_has_features_plus_id_event_label(self, fixture_states):
        matrix = build_game_matrix(fixture_states, PregameContext())
        assert list(matrix.columns) == list(FEATURE_COLUMNS) + MATRIX_EXTRA_COLUMNS
        assert (matrix["game_id"] == "0022300061").all()
        assert matrix["home_win"].dtype == bool


class TestBuildSeason:
    def test_writes_matrix_and_meta(self, data_dir):
        summary = build_season(SEASON, data_dir=data_dir)
        assert summary["games"] == 3
        out = data_dir / "processed" / f"features_{SEASON}.parquet"
        assert out.exists()
        matrix = pd.read_parquet(out)
        assert summary["rows"] == len(matrix)
        assert set(matrix["game_id"]) == set(FIXTURE_GAMES)
        assert list(matrix.columns) == list(FEATURE_COLUMNS) + MATRIX_EXTRA_COLUMNS

    def test_feature_meta_contents(self, data_dir):
        build_season(SEASON, data_dir=data_dir)
        meta = json.loads((data_dir / "models" / "feature_meta.json").read_text())
        assert meta["feature_columns"] == list(FEATURE_COLUMNS)
        assert all(meta["dtypes"][c] == "float64" for c in FEATURE_COLUMNS)
        assert meta["imputation"]["timeouts_remaining"] == 5.0
        assert meta["sampling"]  # policy documented in the artifact itself
        assert len(meta["code_version"]) >= 8

    def test_pregame_context_is_leakage_free_openers_are_neutral(self, data_dir):
        build_season(SEASON, data_dir=data_dir)
        matrix = pd.read_parquet(data_dir / "processed" / f"features_{SEASON}.parquet")
        # both Oct 24 fixture games are season openers for their teams
        opener = matrix[matrix["game_id"] == "0022300061"]
        assert (opener["pregame_win_pct_diff"] == 0.0).all()
        assert (opener["rest_days_home"] == 7.0).all()


class TestCheckParity:
    def test_parity_holds_on_sampled_games(self, data_dir):
        checked = check_parity(SEASON, n=2, data_dir=data_dir)
        assert len(checked) == 2
        assert set(checked) <= set(FIXTURE_GAMES)


class TestSanityTable:
    """Checkpoint 2.6 — empirical home win rate by (score_diff, time) bucket."""

    def test_win_rates_are_valid_probabilities(self, data_dir):
        build_season(SEASON, data_dir=data_dir)
        table = sanity_table(SEASON, data_dir=data_dir)
        values = table.to_numpy()
        observed = values[~pd.isna(values)]
        assert len(observed) > 0
        assert ((observed >= 0.0) & (observed <= 1.0)).all()

    def test_big_late_home_lead_means_home_win(self, data_dir):
        build_season(SEASON, data_dir=data_dir)
        table = sanity_table(SEASON, data_dir=data_dir)
        # in the fixture games, every event with home up 10+ in the last
        # 5 minutes (DEN, won) came from a game the home team won
        late = table.iloc[0]  # lowest seconds_remaining bucket
        big_lead = late.dropna().iloc[-1]  # highest OBSERVED score_diff bucket
        assert big_lead == 1.0
