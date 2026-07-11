"""Offline/incremental parity — the anti-training/serving-skew guarantee.

``build_offline`` must produce a matrix bit-identical to feeding the same
states one at a time through ``FeatureBuilder.update()``. Runs against the
three committed real games (including the OT game 0022300083). The full
20-game sample check runs against harvested data via
``python -m wp_engine.features parity``.
"""

from pathlib import Path

import pandas as pd
import pytest

from wp_engine.collect import parse_game
from wp_engine.features import (
    FEATURE_COLUMNS,
    FeatureBuilder,
    PregameContext,
    build_offline,
    state_from_row,
)
from wp_engine.schemas import GameRecord

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_GAMES = ["0022300061", "0022300062", "0022300083"]

PREGAME = PregameContext(
    home_win_pct=0.6, away_win_pct=0.4, home_rest_days=2.0, away_rest_days=1.0
)


@pytest.fixture(scope="module", params=FIXTURE_GAMES)
def fixture_states(request):
    game_id = request.param
    index = pd.read_parquet(FIXTURES / "game_index.parquet")
    record = GameRecord(**index[index["game_id"] == game_id].iloc[0].to_dict())
    pbp = pd.read_parquet(FIXTURES / "pbp" / f"{game_id}.parquet")
    return parse_game(game_id, pbp=pbp, record=record)


class TestBuildOffline:
    def test_one_output_row_per_state_row(self, fixture_states):
        features = build_offline(fixture_states, PREGAME)
        assert len(features) == len(fixture_states)

    def test_columns_are_the_canonical_ordered_feature_list(self, fixture_states):
        features = build_offline(fixture_states, PREGAME)
        assert list(features.columns) == list(FEATURE_COLUMNS)

    def test_all_columns_float64_no_nans(self, fixture_states):
        features = build_offline(fixture_states, PREGAME)
        assert (features.dtypes == "float64").all()
        assert not features.isna().any().any()


class TestOfflineIncrementalParity:
    def test_bit_identical_to_incremental_updates(self, fixture_states):
        offline = build_offline(fixture_states, PREGAME)

        builder = FeatureBuilder(PREGAME)
        incremental = pd.DataFrame(
            [builder.update(state_from_row(row)) for _, row in fixture_states.iterrows()],
            columns=list(FEATURE_COLUMNS),
        )
        pd.testing.assert_frame_equal(offline, incremental, check_exact=True)

    def test_default_pregame_when_omitted(self, fixture_states):
        offline = build_offline(fixture_states)
        assert (offline["pregame_win_pct_diff"] == 0.0).all()


class TestNoLeakage:
    """Checkpoint 2.6: features for a truncated game prefix must be
    bit-identical to the corresponding rows of the full-game matrix — no
    feature may peek at events that haven't happened yet."""

    def test_truncated_prefix_matches_full_game_rows(self, fixture_states):
        full = build_offline(fixture_states, PREGAME)
        for cut in (1, len(fixture_states) // 3, len(fixture_states) - 1):
            prefix = build_offline(fixture_states.iloc[:cut], PREGAME)
            pd.testing.assert_frame_equal(
                prefix,
                full.iloc[:cut].reset_index(drop=True),
                check_exact=True,
            )


class TestStateFromRow:
    def test_null_timeouts_become_none(self, fixture_states):
        row = fixture_states.iloc[0].copy()
        row["home_timeouts_remaining"] = pd.NA
        state = state_from_row(row)
        assert state.home_timeouts_remaining is None
