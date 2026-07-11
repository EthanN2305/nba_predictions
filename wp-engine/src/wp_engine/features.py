"""Shared feature builder for the win-probability engine (Phase 2).

This is the SINGLE feature-computation module used by both offline training
and live inference (Phase 4). Every feature is computable from a
chronological sequence of :class:`~wp_engine.schemas.GameState` objects for
one game up to the current event, plus static pregame metadata — no
look-ahead, ever.

Time conventions
----------------
``seconds_remaining``: seconds left in regulation while ``period <= 4``
(equal to ``GameState.seconds_remaining_total``); in overtime it is the
seconds left in the *current* OT period only, with ``is_overtime = 1``.
Future overtimes are unknowable live, so they are never counted.

``elapsed``: a monotonic game clock (seconds since tip-off) used for all
trailing-window bookkeeping: regulation periods contribute 720s each, OT
periods 300s each.
"""

import math

from wp_engine.schemas import GameState

REGULATION_PERIOD_SECONDS = 720.0
OT_PERIOD_SECONDS = 300.0
TIME_SINCE_LEAD_CHANGE_CAP = 1200.0


def game_elapsed_seconds(period: int, seconds_remaining_period: float) -> float:
    """Monotonic seconds elapsed since tip-off (OT periods are 300s)."""
    if period <= 4:
        return (period - 1) * REGULATION_PERIOD_SECONDS + (
            REGULATION_PERIOD_SECONDS - seconds_remaining_period
        )
    return (
        4 * REGULATION_PERIOD_SECONDS
        + (period - 5) * OT_PERIOD_SECONDS
        + (OT_PERIOD_SECONDS - seconds_remaining_period)
    )


class FeatureBuilder:
    """Stateful, incremental feature computer for one game.

    Feed it every ``GameState`` in chronological order via :meth:`update`;
    each call returns the full feature vector (``dict[str, float]``) for
    that moment. The same instance/code path runs live in Phase 4.
    """

    def __init__(self) -> None:
        self._lead_changes = 0
        self._last_leader = 0  # sign of last nonzero score_diff seen
        self._last_lead_change_elapsed: float | None = None
        self._largest_lead_home = 0
        self._largest_lead_away = 0

    def update(self, state: GameState) -> dict[str, float]:
        """Consume the next event, return the feature vector for this moment."""
        elapsed = game_elapsed_seconds(state.period, state.seconds_remaining_period)
        is_overtime = state.period >= 5
        seconds_remaining = (
            state.seconds_remaining_period if is_overtime else state.seconds_remaining_total
        )

        leader = (state.score_diff > 0) - (state.score_diff < 0)
        if leader != 0:
            if self._last_leader != 0 and leader != self._last_leader:
                self._lead_changes += 1
                self._last_lead_change_elapsed = elapsed
            self._last_leader = leader

        since_change = (
            elapsed
            if self._last_lead_change_elapsed is None
            else elapsed - self._last_lead_change_elapsed
        )

        self._largest_lead_home = max(self._largest_lead_home, state.score_diff)
        self._largest_lead_away = max(self._largest_lead_away, -state.score_diff)

        return {
            "score_diff": float(state.score_diff),
            "seconds_remaining": float(seconds_remaining),
            "period": float(state.period),
            "is_overtime": float(is_overtime),
            "diff_per_sqrt_time": state.score_diff / math.sqrt(seconds_remaining + 1),
            "score_total": float(state.home_score + state.away_score),
            "lead_changes_so_far": float(self._lead_changes),
            "time_since_lead_change": min(since_change, TIME_SINCE_LEAD_CHANGE_CAP),
            "largest_lead_home": float(self._largest_lead_home),
            "largest_lead_away": float(self._largest_lead_away),
        }
