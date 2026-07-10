"""Tests for the canonical Pydantic schemas (Checkpoint 1.1)."""

from datetime import date

import pytest
from pydantic import ValidationError

from wp_engine.schemas import GameRecord, GameState


def make_state(**overrides) -> GameState:
    """A valid mid-game GameState; override fields per test."""
    base = dict(
        game_id="0022300001",
        period=2,
        seconds_remaining_period=345.0,
        seconds_remaining_total=1785.0,
        home_score=48,
        away_score=51,
        score_diff=-3,
        home_team_fouls_period=3,
        away_team_fouls_period=5,
        home_in_bonus=False,
        away_in_bonus=True,
        possession=1,
        home_timeouts_remaining=6,
        away_timeouts_remaining=5,
        event_num=178,
    )
    base.update(overrides)
    return GameState(**base)


class TestGameState:
    def test_constructs_with_valid_fields(self):
        state = make_state()
        assert state.game_id == "0022300001"
        assert state.score_diff == -3
        assert state.possession == 1

    def test_possession_must_be_home_away_or_unknown(self):
        for ok in (1, -1, 0):
            assert make_state(possession=ok).possession == ok
        with pytest.raises(ValidationError):
            make_state(possession=2)

    def test_period_must_be_positive(self):
        with pytest.raises(ValidationError):
            make_state(period=0)

    def test_scores_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            make_state(home_score=-1)

    def test_timeouts_may_be_none_when_tracking_unreliable(self):
        state = make_state(home_timeouts_remaining=None, away_timeouts_remaining=None)
        assert state.home_timeouts_remaining is None
        assert state.away_timeouts_remaining is None

    def test_round_trips_through_json(self):
        state = make_state()
        assert GameState.model_validate_json(state.model_dump_json()) == state


class TestGameRecord:
    def test_constructs_with_valid_fields(self):
        rec = GameRecord(
            game_id="0022300001",
            season="2023-24",
            game_date=date(2023, 10, 24),
            home_team_id=1610612743,
            away_team_id=1610612747,
            home_team_abbr="DEN",
            away_team_abbr="LAL",
            final_home_score=119,
            final_away_score=107,
            home_win=True,
        )
        assert rec.home_win is True
        assert rec.game_date.year == 2023

    def test_home_win_label_matches_scores_is_not_enforced_by_schema(self):
        # The schema stores what it is given; label consistency is checked by
        # the validation suite (Checkpoint 1.5), not the model.
        rec = GameRecord(
            game_id="x",
            season="2023-24",
            game_date=date(2024, 1, 1),
            home_team_id=1,
            away_team_id=2,
            home_team_abbr="AAA",
            away_team_abbr="BBB",
            final_home_score=100,
            final_away_score=90,
            home_win=True,
        )
        assert rec.final_home_score - rec.final_away_score > 0
