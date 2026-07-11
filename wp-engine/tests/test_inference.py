"""Phase 4 — LivePredictor: GameState stream → WinProbUpdate stream.

The glue between the shared FeatureBuilder, the trained predictor and the
wire format. One LivePredictor per game; the SAME class serves live polling
and replay.
"""

import pytest

from wp_engine.inference import LivePredictor
from wp_engine.schemas import WinProbUpdate

from tests.test_features import make_state


@pytest.fixture()
def live_predictor(tiny_models_dir) -> LivePredictor:
    return LivePredictor(game_id="0022300061", models_dir=tiny_models_dir)


class TestLivePredictor:
    def test_on_state_returns_valid_update(self, live_predictor):
        update = live_predictor.on_state(
            make_state(event_num=7, period=4, srp=151.0, home=98, away=95, poss=1),
            description="Jokic 2PT",
        )
        assert isinstance(update, WinProbUpdate)
        assert update.game_id == "0022300061"
        assert update.event_num == 7
        assert update.clock == "Q4 02:31"
        assert (update.home_score, update.away_score) == (98, 95)
        assert 0 < update.wp_home < 1
        assert update.description == "Jokic 2PT"
        assert update.is_replay is False

    def test_stateful_features_accumulate_across_states(self, live_predictor):
        # momentum features require the builder to persist across calls;
        # a home run late in a close game must raise wp vs the first tick
        first = live_predictor.on_state(make_state(event_num=1, period=4, srp=300.0))
        run = None
        for i, pts in enumerate((3, 6, 9, 12)):
            run = live_predictor.on_state(
                make_state(event_num=2 + i, period=4, srp=290.0 - i * 20, home=pts)
            )
        assert run.wp_home > first.wp_home

    def test_replay_flag_propagates(self, tiny_models_dir):
        lp = LivePredictor(
            game_id="X", models_dir=tiny_models_dir, is_replay=True
        )
        assert lp.on_state(make_state()).is_replay is True

    def test_ot_clock_display(self, live_predictor):
        update = live_predictor.on_state(make_state(event_num=9, period=5, srp=45.0))
        assert update.clock == "OT1 00:45"
