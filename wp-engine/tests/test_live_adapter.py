"""Checkpoint 4.1 — LiveGameAdapter: NBA liveData actions → canonical GameState.

Fixtures are SYNTHESIZED in the documented cdn.nba.com liveData shape
(actionNumber / ISO clock / actionType strings like "2pt", "freethrow",
"foul", explicit possession teamId) because cdn.nba.com is Akamai-blocked
from this network — see HANDOFF.md. The adapter is coded defensively
(unknown fields ignored, malformed events skipped).
"""

import pytest

from wp_engine.live import LiveGameAdapter, display_clock

GAME_ID = "0022300777"
HOME, AWAY = 100, 200


def act(
    action_number: int,
    action_type: str,
    clock: str = "PT10M00.00S",
    period: int = 1,
    *,
    sub: str = "",
    team: int | None = None,
    score_home: str | None = None,
    score_away: str | None = None,
    possession: int | None = None,
    shot_result: str | None = None,
    desc: str = "",
) -> dict:
    """One synthesized liveData action."""
    action = {
        "actionNumber": action_number,
        "actionType": action_type,
        "subType": sub,
        "clock": clock,
        "period": period,
        "description": desc,
    }
    if team is not None:
        action["teamId"] = team
    if score_home is not None:
        action["scoreHome"] = score_home
        action["scoreAway"] = score_away
    if possession is not None:
        action["possession"] = possession
    if shot_result is not None:
        action["shotResult"] = shot_result
    return action


def adapter() -> LiveGameAdapter:
    return LiveGameAdapter(game_id=GAME_ID, home_team_id=HOME, away_team_id=AWAY)


class TestDisplayClock:
    def test_regulation(self):
        assert display_clock(4, 151.0) == "Q4 02:31"

    def test_overtime_periods(self):
        assert display_clock(5, 45.0) == "OT1 00:45"
        assert display_clock(7, 300.0) == "OT3 05:00"


class TestScoring:
    def test_made_shot_updates_score_and_possession(self):
        states = adapter().process(
            [
                act(1, "period", clock="PT12M00.00S", sub="start"),
                act(2, "2pt", clock="PT11M40.00S", team=HOME, shot_result="Made",
                    score_home="2", score_away="0", possession=AWAY),
            ]
        )
        final = states[-1]
        assert (final.home_score, final.away_score) == (2, 0)
        assert final.score_diff == 2
        assert final.possession == -1  # live possession field: AWAY has the ball
        assert final.seconds_remaining_period == pytest.approx(700.0)

    def test_score_not_trusted_on_administrative_rows(self):
        states = adapter().process(
            [
                act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0"),
                act(2, "instantreplay", clock="PT09M00.00S", score_home="0", score_away="0"),
            ]
        )
        assert (states[-1].home_score, states[-1].away_score) == (2, 0)

    def test_monotonic_guard_ignores_score_going_backwards(self):
        states = adapter().process(
            [
                act(1, "3pt", team=HOME, shot_result="Made", score_home="3", score_away="0"),
                act(2, "freethrow", clock="PT09M00.00S", team=AWAY, shot_result="Made",
                    score_home="0", score_away="1"),
            ]
        )
        assert (states[-1].home_score, states[-1].away_score) == (3, 0)

    def test_missed_shot_does_not_change_score(self):
        states = adapter().process(
            [act(1, "2pt", team=HOME, shot_result="Missed", score_home="", score_away="")]
        )
        assert (states[-1].home_score, states[-1].away_score) == (0, 0)


class TestFoulsAndBonus:
    def test_common_fouls_count_and_trigger_bonus(self):
        actions = [act(1, "period", sub="start")]
        for i in range(5):
            actions.append(
                act(2 + i, "foul", clock=f"PT{10 - i}M00.00S", sub="personal", team=AWAY)
            )
        states = adapter().process(actions)
        final = states[-1]
        assert final.away_team_fouls_period == 5
        assert final.home_in_bonus is True  # HOME shoots FTs on the next foul
        assert final.away_in_bonus is False

    def test_offensive_and_technical_fouls_excluded(self):
        states = adapter().process(
            [
                act(1, "foul", sub="offensive", team=AWAY),
                act(2, "foul", clock="PT09M00.00S", sub="technical", team=AWAY),
            ]
        )
        assert states[-1].away_team_fouls_period == 0

    def test_fouls_reset_each_period(self):
        states = adapter().process(
            [
                act(1, "foul", clock="PT00M10.00S", sub="personal", team=HOME),
                act(2, "period", clock="PT12M00.00S", period=2, sub="start"),
            ]
        )
        assert states[-1].home_team_fouls_period == 0


class TestTimeouts:
    def test_timeout_decrements_team_count(self):
        states = adapter().process([act(1, "timeout", team=HOME, sub="full")])
        final = states[-1]
        assert final.home_timeouts_remaining == 6
        assert final.away_timeouts_remaining == 7


class TestOrderingDedupAmendment:
    def test_events_sorted_by_period_clock_action_number(self):
        # arrives out of order: the later event (less clock) first
        states = adapter().process(
            [
                act(9, "2pt", clock="PT08M00.00S", team=HOME, shot_result="Made",
                    score_home="4", score_away="0"),
                act(3, "2pt", clock="PT11M00.00S", team=HOME, shot_result="Made",
                    score_home="2", score_away="0"),
            ]
        )
        assert [s.event_num for s in states] == [3, 9]
        assert states[-1].home_score == 4

    def test_amendment_same_action_number_takes_latest(self):
        a = adapter()
        a.process([act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")])
        # amended: actually a 3-pointer
        states = a.process(
            [act(1, "3pt", team=HOME, shot_result="Made", score_home="3", score_away="0")]
        )
        assert len(states) == 1
        assert states[-1].home_score == 3

    def test_incremental_polling_returns_full_consistent_history(self):
        a = adapter()
        first = a.process(
            [act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")]
        )
        second = a.process(
            [
                act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0"),
                act(2, "3pt", clock="PT09M00.00S", team=AWAY, shot_result="Made",
                    score_home="2", score_away="3", possession=HOME),
            ]
        )
        assert len(first) == 1 and len(second) == 2
        assert second[0].home_score == 2
        assert second[-1].score_diff == -1


class TestDefensiveness:
    def test_malformed_clock_skips_event_without_crashing(self):
        states = adapter().process(
            [
                act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0"),
                act(2, "2pt", clock="garbage", team=AWAY, shot_result="Made",
                    score_home="2", score_away="2"),
            ]
        )
        assert len(states) == 1

    def test_unknown_action_types_are_state_noops(self):
        states = adapter().process(
            [act(1, "somebrandnewtype"), act(2, "steal", clock="PT09M00.00S", team=AWAY)]
        )
        assert len(states) == 2
        assert states[-1].home_score == 0

    def test_states_carry_game_id_and_valid_totals(self):
        states = adapter().process(
            [act(1, "2pt", period=3, clock="PT06M00.00S", team=HOME, shot_result="Made",
                 score_home="2", score_away="0")]
        )
        s = states[-1]
        assert s.game_id == GAME_ID
        assert s.seconds_remaining_total == pytest.approx(360.0 + 720.0)
