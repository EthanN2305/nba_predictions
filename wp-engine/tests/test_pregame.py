"""Tests for Checkpoint 2.4 — pregame context from the Phase 1 game index.

Standings/rest are computed strictly as-of the game date (earlier dates
only), so no information from the game itself or the future can leak in.
"""

import pandas as pd
import pytest

from wp_engine.features import (
    DEFAULT_REST_DAYS,
    FeatureBuilder,
    PregameContext,
    build_pregame_context,
)

from tests.test_features import make_state

TEAM_A, TEAM_B, TEAM_C = 1610612737, 1610612738, 1610612739


def make_index() -> pd.DataFrame:
    """Tiny 3-team season: A beat B on Jan 1, B beat C on Jan 3."""
    rows = [
        # game_id, date, home, away, home_score, away_score
        ("g1", "2024-01-01", TEAM_A, TEAM_B, 100, 90),
        ("g2", "2024-01-03", TEAM_B, TEAM_C, 110, 105),
        ("g3", "2024-01-05", TEAM_A, TEAM_C, 0, 0),  # the game being predicted
    ]
    return pd.DataFrame(
        [
            {
                "game_id": gid,
                "season": "2023-24",
                "game_date": date,
                "home_team_id": home,
                "away_team_id": away,
                "home_team_abbr": "HHH",
                "away_team_abbr": "AAA",
                "final_home_score": hs,
                "final_away_score": as_,
                "home_win": hs > as_,
            }
            for gid, date, home, away, hs, as_ in rows
        ]
    )


class TestBuildPregameContext:
    def test_win_pct_uses_only_prior_games(self):
        ctx = build_pregame_context(make_index(), "g3")
        assert ctx.home_win_pct == 1.0  # A is 1-0 entering Jan 5
        assert ctx.away_win_pct == 0.0  # C is 0-1

    def test_rest_days_from_previous_game(self):
        ctx = build_pregame_context(make_index(), "g3")
        assert ctx.home_rest_days == 4.0  # A last played Jan 1
        assert ctx.away_rest_days == 2.0  # C last played Jan 3

    def test_season_opener_gets_neutral_defaults(self):
        ctx = build_pregame_context(make_index(), "g1")
        assert ctx.home_win_pct == 0.5
        assert ctx.away_win_pct == 0.5
        assert ctx.home_rest_days == 7.0  # capped "fully rested"
        assert ctx.away_rest_days == 7.0

    def test_rest_days_capped_at_seven(self):
        idx = make_index()
        idx.loc[idx["game_id"] == "g3", "game_date"] = "2024-02-01"
        ctx = build_pregame_context(idx, "g3")
        assert ctx.home_rest_days == 7.0

    def test_unknown_game_id_raises(self):
        with pytest.raises(KeyError):
            build_pregame_context(make_index(), "nope")


class TestPregameFeaturesInVector:
    def test_pregame_features_flow_into_feature_vector(self):
        pregame = PregameContext(
            home_win_pct=0.75, away_win_pct=0.25, home_rest_days=1.0, away_rest_days=3.0
        )
        fb = FeatureBuilder(pregame)
        f = fb.update(make_state())
        assert f["pregame_win_pct_home"] == 0.75
        assert f["pregame_win_pct_away"] == 0.25
        assert f["pregame_win_pct_diff"] == 0.5
        assert f["rest_days_home"] == 1.0
        assert f["rest_days_away"] == 3.0

    def test_no_pregame_context_uses_neutral_defaults(self):
        fb = FeatureBuilder()
        f = fb.update(make_state())
        assert f["pregame_win_pct_diff"] == 0.0
        assert f["rest_days_home"] == DEFAULT_REST_DAYS
        assert f["rest_days_away"] == DEFAULT_REST_DAYS
