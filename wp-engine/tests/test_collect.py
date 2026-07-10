"""Checkpoint 1.5 validation suite: end-to-end parses of real fixture games
plus the `python -m wp_engine.collect validate` summary.

Fixtures (committed under tests/fixtures/) are real PlayByPlayV3 payloads:
- 0022300061  DEN 119-107 LAL  (regulation, home win)
- 0022300062  GSW 104-108 PHX  (regulation, home LOSS, close game)
- 0022300083  SAS 126-122 HOU  (overtime, home win)
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wp_engine.collect import parse_game, validate
from wp_engine.schemas import GameRecord

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_GAMES = ["0022300061", "0022300062", "0022300083"]


def fixture_record(game_id: str) -> GameRecord:
    index = pd.read_parquet(FIXTURES / "game_index.parquet")
    row = index[index["game_id"] == game_id].iloc[0]
    return GameRecord(**row.to_dict())


@pytest.fixture(scope="module", params=FIXTURE_GAMES)
def parsed_game(request):
    game_id = request.param
    pbp = pd.read_parquet(FIXTURES / "pbp" / f"{game_id}.parquet")
    return game_id, parse_game(game_id, pbp=pbp, record=fixture_record(game_id))


class TestRealGamesEndToEnd:
    def test_final_score_diff_sign_matches_home_win(self, parsed_game):
        game_id, states = parsed_game
        final = states.iloc[-1]
        assert (final["score_diff"] > 0) == bool(final["home_win"])

    def test_seconds_remaining_total_non_increasing_within_regulation(self, parsed_game):
        _, states = parsed_game
        regulation = states[states["period"] <= 4]
        assert regulation["seconds_remaining_total"].is_monotonic_decreasing or (
            np.diff(regulation["seconds_remaining_total"]) <= 0
        ).all()

    def test_seconds_remaining_non_increasing_within_each_ot_period(self, parsed_game):
        _, states = parsed_game
        for period, group in states[states["period"] > 4].groupby("period"):
            assert (np.diff(group["seconds_remaining_period"]) <= 0).all(), period

    def test_scores_monotonically_non_decreasing(self, parsed_game):
        _, states = parsed_game
        assert (np.diff(states["home_score"]) >= 0).all()
        assert (np.diff(states["away_score"]) >= 0).all()

    def test_every_period_starts_with_zero_team_fouls(self, parsed_game):
        _, states = parsed_game
        for period, group in states.groupby("period"):
            first = group.iloc[0]
            assert first["home_team_fouls_period"] == 0, period
            assert first["away_team_fouls_period"] == 0, period

    def test_possession_inferred_on_most_events(self, parsed_game):
        game_id, states = parsed_game
        coverage = (states["possession"] != 0).mean()
        assert coverage > 0.70, f"{game_id}: possession coverage {coverage:.1%}"

    def test_overtime_game_has_period_five(self):
        pbp = pd.read_parquet(FIXTURES / "pbp" / "0022300083.parquet")
        states = parse_game(
            "0022300083", pbp=pbp, record=fixture_record("0022300083")
        )
        assert states["period"].max() == 5


class TestValidateSummary:
    @pytest.fixture
    def parsed_data_dir(self, tmp_path):
        """A data dir with the fixture games parsed into states parquet files."""
        index = pd.read_parquet(FIXTURES / "game_index.parquet")
        raw = tmp_path / "raw"
        states_dir = raw / "states" / "2023-24"
        states_dir.mkdir(parents=True)
        index.to_parquet(raw / "game_index_2023-24.parquet", index=False)
        for game_id in FIXTURE_GAMES:
            pbp = pd.read_parquet(FIXTURES / "pbp" / f"{game_id}.parquet")
            states = parse_game(game_id, pbp=pbp, record=fixture_record(game_id))
            states.to_parquet(states_dir / f"{game_id}.parquet", index=False)
        return tmp_path

    def test_summary_counts_and_no_mismatches(self, parsed_data_dir):
        summary = validate(data_dir=parsed_data_dir)
        assert summary["games_parsed"] == 3
        assert summary["pct_games_possession_over_70"] == 100.0
        assert summary["pct_label_mismatches"] == 0.0
        assert summary["mismatched_games"] == []

    def test_detects_label_mismatch(self, parsed_data_dir):
        # Tamper with one game: flip its label so the final score disagrees
        path = parsed_data_dir / "raw" / "states" / "2023-24" / "0022300061.parquet"
        states = pd.read_parquet(path)
        states["home_win"] = False  # DEN actually won
        states.to_parquet(path, index=False)

        summary = validate(data_dir=parsed_data_dir)
        assert summary["mismatched_games"] == ["0022300061"]
        assert summary["pct_label_mismatches"] == pytest.approx(100 / 3)

    def test_cli_validate_prints_summary(self, parsed_data_dir, capsys):
        from wp_engine.collect import main

        main(["validate", "--data-dir", str(parsed_data_dir)])
        out = capsys.readouterr().out
        assert "games parsed: 3" in out
        assert "label mismatches" in out
