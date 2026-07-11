"""Tests for the Phase 2 shared feature builder (``wp_engine.features``).

Checkpoint 2.1: core clock & score features computed incrementally by
``FeatureBuilder.update()`` from a chronological ``GameState`` stream.
"""

import math

import pytest

from wp_engine.features import FeatureBuilder
from wp_engine.schemas import GameState


def make_state(
    event_num: int = 1,
    period: int = 1,
    srp: float = 720.0,
    home: int = 0,
    away: int = 0,
    poss: int = 0,
    home_fouls: int = 0,
    away_fouls: int = 0,
    home_to: int | None = 7,
    away_to: int | None = 7,
    game_id: str = "0022300061",
) -> GameState:
    """Build a consistent GameState; seconds_remaining_total follows the
    Phase 1 convention (regulation-normalized; OT = current OT only)."""
    if period <= 4:
        srt = srp + (4 - period) * 720.0
    else:
        srt = srp
    return GameState(
        game_id=game_id,
        period=period,
        seconds_remaining_period=srp,
        seconds_remaining_total=srt,
        home_score=home,
        away_score=away,
        score_diff=home - away,
        home_team_fouls_period=home_fouls,
        away_team_fouls_period=away_fouls,
        home_in_bonus=away_fouls >= 5,
        away_in_bonus=home_fouls >= 5,
        possession=poss,
        home_timeouts_remaining=home_to,
        away_timeouts_remaining=away_to,
        event_num=event_num,
    )


class TestCoreClockScoreFeatures:
    def test_first_event_basic_fields(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=1, srp=700.0, home=2, away=0))
        assert f["score_diff"] == 2.0
        assert f["seconds_remaining"] == 700.0 + 3 * 720.0
        assert f["period"] == 1.0
        assert f["is_overtime"] == 0.0
        assert f["score_total"] == 2.0

    def test_overtime_uses_current_ot_clock_and_flag(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=5, srp=180.0, home=100, away=100))
        assert f["seconds_remaining"] == 180.0
        assert f["is_overtime"] == 1.0

    def test_diff_per_sqrt_time(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=4, srp=30.0, home=105, away=100))
        assert f["diff_per_sqrt_time"] == pytest.approx(5 / math.sqrt(30.0 + 1))

    def test_lead_changes_counted_only_on_leader_flip(self):
        fb = FeatureBuilder()
        # 0-0 tie: no leader yet
        assert fb.update(make_state(event_num=1))["lead_changes_so_far"] == 0.0
        # home takes first lead: NOT a lead change
        assert (
            fb.update(make_state(event_num=2, srp=700.0, home=2))["lead_changes_so_far"]
            == 0.0
        )
        # tie again: not a change
        assert (
            fb.update(make_state(event_num=3, srp=690.0, home=2, away=2))[
                "lead_changes_so_far"
            ]
            == 0.0
        )
        # away takes the lead: 1 change
        assert (
            fb.update(make_state(event_num=4, srp=680.0, home=2, away=5))[
                "lead_changes_so_far"
            ]
            == 1.0
        )
        # away extends: no change
        assert (
            fb.update(make_state(event_num=5, srp=670.0, home=2, away=7))[
                "lead_changes_so_far"
            ]
            == 1.0
        )
        # home retakes: 2 changes
        assert (
            fb.update(make_state(event_num=6, srp=660.0, home=8, away=7))[
                "lead_changes_so_far"
            ]
            == 2.0
        )

    def test_time_since_lead_change_uses_game_clock(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))
        # away leads at 710s left in P1 (elapsed = 10s) -> first lead, no change
        fb.update(make_state(event_num=2, srp=710.0, away=2))
        # home flips the lead at 700s left (elapsed = 20s): lead change here
        fb.update(make_state(event_num=3, srp=700.0, home=3, away=2))
        # 15 game-seconds later
        f = fb.update(make_state(event_num=4, srp=685.0, home=3, away=2))
        assert f["time_since_lead_change"] == pytest.approx(15.0)

    def test_time_since_lead_change_before_any_change_is_elapsed_capped(self):
        fb = FeatureBuilder()
        # 3rd period, 600s left => elapsed = 2*720 + 120 = 1560 > cap
        f = fb.update(make_state(period=3, srp=600.0, home=50, away=40))
        assert f["time_since_lead_change"] == 1200.0

    def test_largest_leads_run_max_with_zero_floor(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(event_num=1, home=4, away=0))
        assert f["largest_lead_home"] == 4.0
        assert f["largest_lead_away"] == 0.0
        f = fb.update(make_state(event_num=2, srp=700.0, home=4, away=9))
        assert f["largest_lead_home"] == 4.0
        assert f["largest_lead_away"] == 5.0
        f = fb.update(make_state(event_num=3, srp=690.0, home=10, away=9))
        assert f["largest_lead_home"] == 4.0  # unchanged until exceeded
        f = fb.update(make_state(event_num=4, srp=680.0, home=15, away=9))
        assert f["largest_lead_home"] == 6.0

    def test_all_feature_values_are_floats(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(home=10, away=8, poss=1))
        assert all(isinstance(v, float) for v in f.values()), {
            k: type(v) for k, v in f.items() if not isinstance(v, float)
        }
