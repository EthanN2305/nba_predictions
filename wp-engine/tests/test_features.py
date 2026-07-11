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


class TestGameSituationFeatures:
    def test_possession_and_possession_x_time(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=4, srp=63.0, home=90, away=88, poss=-1))
        assert f["possession"] == -1.0
        assert f["possession_x_time"] == pytest.approx(-1 / math.sqrt(63.0 + 1))

    def test_bonus_and_foul_diff(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(home_fouls=2, away_fouls=5))
        assert f["home_in_bonus"] == 1.0  # away has 5 team fouls
        assert f["away_in_bonus"] == 0.0
        assert f["foul_diff_period"] == -3.0

    def test_timeouts_known(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(home_to=4, away_to=2))
        assert f["home_timeouts_remaining"] == 4.0
        assert f["away_timeouts_remaining"] == 2.0
        assert f["timeouts_known"] == 1.0

    def test_timeouts_none_imputed_with_median_and_flagged(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(home_to=None, away_to=3))
        assert f["home_timeouts_remaining"] == 5.0  # empirical median
        assert f["away_timeouts_remaining"] == 3.0
        assert f["timeouts_known"] == 0.0

    def test_is_clutch_late_fourth_and_close(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=4, srp=299.0, home=100, away=96))
        assert f["is_clutch"] == 1.0

    def test_is_clutch_requires_close_score(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=4, srp=120.0, home=110, away=96))
        assert f["is_clutch"] == 0.0

    def test_is_clutch_not_early_in_fourth(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=4, srp=301.0, home=100, away=98))
        assert f["is_clutch"] == 0.0

    def test_all_of_overtime_is_clutch_when_close(self):
        fb = FeatureBuilder()
        f = fb.update(make_state(period=5, srp=300.0, home=100, away=100))
        assert f["is_clutch"] == 1.0


class TestRollingMomentumFeatures:
    def test_run_last_120s_scores_inside_window(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))                     # elapsed 0
        fb.update(make_state(event_num=2, srp=710.0, home=2))             # elapsed 10, home +2
        fb.update(make_state(event_num=3, srp=670.0, home=2, away=3))     # elapsed 50, away +3
        f = fb.update(make_state(event_num=4, srp=620.0, home=2, away=3)) # elapsed 100
        assert f["run_last_120s"] == pytest.approx(-1.0)

    def test_run_last_120s_old_scores_fall_out(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))
        fb.update(make_state(event_num=2, srp=710.0, home=2))             # elapsed 10
        fb.update(make_state(event_num=3, srp=670.0, home=2, away=3))     # elapsed 50
        # elapsed 135: the +2 at elapsed 10 is 125s old -> outside the window
        f = fb.update(make_state(event_num=4, srp=585.0, home=2, away=3))
        assert f["run_last_120s"] == pytest.approx(-3.0)

    def test_event_exactly_window_old_is_excluded(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))
        fb.update(make_state(event_num=2, srp=710.0, home=2))             # elapsed 10
        f = fb.update(make_state(event_num=3, srp=590.0, home=2))         # elapsed 130
        assert f["run_last_120s"] == pytest.approx(0.0)

    def test_run_last_300s_wider_window(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))
        fb.update(make_state(event_num=2, srp=710.0, home=2))             # elapsed 10
        f = fb.update(make_state(event_num=3, srp=470.0, home=2, away=3)) # elapsed 250
        assert f["run_last_120s"] == pytest.approx(-3.0)
        assert f["run_last_300s"] == pytest.approx(-1.0)

    def test_scoring_rates_are_points_per_minute(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))
        fb.update(make_state(event_num=2, srp=700.0, home=6))             # elapsed 20
        fb.update(make_state(event_num=3, srp=680.0, home=6, away=4))     # elapsed 40
        f = fb.update(make_state(event_num=4, srp=660.0, home=10, away=4))
        assert f["scoring_rate_home_300s"] == pytest.approx(10 / 5.0)
        assert f["scoring_rate_away_300s"] == pytest.approx(4 / 5.0)

    def test_fouls_last_300s_diff_from_counter_deltas(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))
        fb.update(make_state(event_num=2, srp=700.0, home_fouls=1))
        fb.update(make_state(event_num=3, srp=680.0, home_fouls=2))
        f = fb.update(make_state(event_num=4, srp=660.0, home_fouls=2, away_fouls=1))
        assert f["fouls_last_300s_diff"] == pytest.approx(2 - 1)

    def test_foul_counter_reset_at_period_start_not_counted(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, period=1, srp=10.0, home_fouls=6, away_fouls=2))
        # new period: counters reset to 0 -> must NOT register as negative fouls
        f = fb.update(make_state(event_num=2, period=2, srp=720.0))
        assert f["fouls_last_300s_diff"] == pytest.approx(4.0)

    def test_momentum_ewm_halflife_decay_is_exact(self):
        fb = FeatureBuilder()
        fb.update(make_state(event_num=1, srp=720.0))                     # baseline
        f = fb.update(make_state(event_num=2, srp=630.0, home=2))         # +2 at elapsed 90
        assert f["momentum_ewm"] == pytest.approx(2.0)
        # 90s (= one halflife) later, away scores 3: 2 * 0.5 - 3 = -2
        f = fb.update(make_state(event_num=3, srp=540.0, home=2, away=3))
        assert f["momentum_ewm"] == pytest.approx(-2.0)
