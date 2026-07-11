"""Phase 4 — model loading + per-event prediction wrapper.

``LivePredictor`` is the single pipeline stage between canonical GameStates
and the ``WinProbUpdate`` wire format. One instance per game (it owns that
game's stateful ``FeatureBuilder``); the SAME class serves live polling and
replay, so there is no live/replay skew either.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from wp_engine.features import FeatureBuilder, PregameContext
from wp_engine.live import display_clock
from wp_engine.schemas import GameState, WinProbUpdate
from wp_engine.train import load_predictor

_EPS = 1e-6


class LivePredictor:
    """GameState → features → calibrated wp_home → WinProbUpdate."""

    def __init__(
        self,
        *,
        game_id: str,
        models_dir: Path | None = None,
        pregame: PregameContext | None = None,
        predictor=None,
        is_replay: bool = False,
    ) -> None:
        self.game_id = game_id
        self.is_replay = is_replay
        self._builder = FeatureBuilder(pregame)
        # share one loaded predictor across games by passing `predictor`;
        # load from disk only when standalone
        self._predict = predictor if predictor is not None else load_predictor(
            models_dir=models_dir
        )

    def on_state(
        self, state: GameState, *, description: str | None = None
    ) -> WinProbUpdate:
        """Consume the next chronological GameState, return the update tick."""
        features = self._builder.update(state)
        wp_home = float(self._predict(pd.DataFrame([features]))[0])
        wp_home = min(max(wp_home, _EPS), 1 - _EPS)
        return WinProbUpdate(
            game_id=self.game_id,
            event_num=state.event_num,
            period=state.period,
            clock=display_clock(state.period, state.seconds_remaining_period),
            home_score=state.home_score,
            away_score=state.away_score,
            wp_home=wp_home,
            ts=datetime.now(timezone.utc),
            description=description,
            is_replay=self.is_replay,
        )
