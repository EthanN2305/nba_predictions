"""Tests for collect.parse_game — PlayByPlayV3 event stream → GameState rows (Checkpoint 1.4).

NOTE: the phase docs were written against PlayByPlayV2, but the NBA stats API
no longer serves V2 (nba_api issue #591), so the parser targets PlayByPlayV3:
ISO-8601 clocks ('PT11M23.00S'), actionType strings, explicit
scoreHome/scoreAway, and 'location' (h/v) for team attribution.
"""

from datetime import date

import pandas as pd
import pytest

from wp_engine.collect import ParseError, parse_clock, parse_game
from wp_engine.schemas import GameRecord, GameState

HOME_ID, AWAY_ID = 100, 200
GAME_ID = "0022300777"


def ev(
    actionnumber,
    action_type,
    period=1,
    clock="PT12M00.00S",
    *,
    sub="",
    team=0,
    location="",
    score_home="",
    score_away="",
    desc="",
    person=0,
    shot_result="",
):
    """One raw PlayByPlayV3-shaped event row."""
    return {
        "gameId": GAME_ID,
        "actionNumber": actionnumber,
        "clock": clock,
        "period": period,
        "teamId": team,
        "teamTricode": "",
        "personId": person,
        "shotResult": shot_result,
        "isFieldGoal": 0,
        "scoreHome": score_home,
        "scoreAway": score_away,
        "location": location,
        "description": desc,
        "actionType": action_type,
        "subType": sub,
        "actionId": actionnumber,
    }


def make_record(final_home=2, final_away=0, home_win=None) -> GameRecord:
    return GameRecord(
        game_id=GAME_ID,
        season="2023-24",
        game_date=date(2024, 1, 15),
        home_team_id=HOME_ID,
        away_team_id=AWAY_ID,
        home_team_abbr="HHH",
        away_team_abbr="AAA",
        final_home_score=final_home,
        final_away_score=final_away,
        home_win=home_win if home_win is not None else final_home > final_away,
    )


def parse(events, record=None):
    return parse_game(
        GAME_ID, pbp=pd.DataFrame(events), record=record or make_record()
    )


class TestClockParsing:
    def test_parse_clock_iso_duration(self):
        assert parse_clock("PT12M00.00S") == 720.0
        assert parse_clock("PT11M23.00S") == 683.0
        assert parse_clock("PT00M09.40S") == 9.4

    def test_malformed_clock_raises(self):
        with pytest.raises(ParseError):
            parse_clock("GARBAGE")
        with pytest.raises(ParseError):
            parse_clock("")

    def test_seconds_remaining_total_regulation(self):
        events = [
            ev(1, "period", period=2, clock="PT05M30.00S", sub="start"),
            ev(2, "Made Shot", period=2, clock="PT05M28.00S", team=HOME_ID,
               score_home="2", score_away="0"),
        ]
        out = parse(events)
        # 330s left in Q2 + Q3 + Q4
        assert out.iloc[0]["seconds_remaining_period"] == 330.0
        assert out.iloc[0]["seconds_remaining_total"] == 330.0 + 2 * 720.0

    def test_seconds_remaining_total_in_overtime_is_ot_clock(self):
        events = [
            ev(1, "period", period=5, clock="PT05M00.00S", sub="start"),
            ev(2, "Made Shot", period=5, clock="PT04M58.00S", team=HOME_ID,
               score_home="2", score_away="0"),
        ]
        out = parse(events)
        assert out.iloc[0]["seconds_remaining_period"] == 300.0
        assert out.iloc[0]["seconds_remaining_total"] == 300.0
        assert out.iloc[0]["period"] == 5


class TestScoreParsing:
    def test_scores_forward_filled_from_scoring_events(self):
        events = [
            ev(1, "period", sub="start"),                                     # no score yet
            ev(2, "Made Shot", clock="PT11M40.00S", team=HOME_ID,
               score_home="2", score_away="0"),                               # home 2
            ev(3, "Turnover", clock="PT11M20.00S", team=HOME_ID),             # ffill
            ev(4, "Made Shot", clock="PT11M00.00S", team=AWAY_ID,
               score_home="2", score_away="3"),                               # away 3
        ]
        out = parse(events, record=make_record(final_home=2, final_away=3))
        assert list(out["home_score"]) == [0, 2, 2, 2]
        assert list(out["away_score"]) == [0, 0, 0, 3]
        assert list(out["score_diff"]) == [0, 2, 2, -1]

    def test_final_score_mismatch_raises_parse_error(self):
        events = [
            ev(1, "Made Shot", clock="PT11M00.00S", team=HOME_ID,
               score_home="2", score_away="0"),
        ]
        with pytest.raises(ParseError):
            parse(events, record=make_record(final_home=50, final_away=60))

    def test_non_monotonic_free_throw_score_is_ignored(self):
        # Real data (game 0022100016): a legitimate-looking "Free Throw" row
        # very late in the game carries a corrupted score (0-1) that is LOWER
        # than the already-reached running score (115-113). Since basketball
        # scores never decrease, this must be ignored rather than applied.
        events = [
            ev(1, "Made Shot", clock="PT00M05.00S", team=HOME_ID,
               score_home="115", score_away="113"),
            ev(2, "Free Throw", clock="PT00M03.70S", sub="Free Throw 1 of 2",
               team=HOME_ID, score_home="0", score_away="1"),
            ev(3, "period", clock="PT00M00.00S", sub="end",
               score_home="115", score_away="113"),
        ]
        out = parse(events, record=make_record(final_home=115, final_away=113))
        assert list(out["home_score"]) == [115, 115, 115]
        assert list(out["away_score"]) == [113, 113, 113]

    def test_instant_replay_row_does_not_overwrite_score_with_stale_value(self):
        # Real data (game 0022301202): a buzzer-beater 3PT shot makes it 122-112,
        # then an "Instant Replay" review row and the period-end row both carry a
        # stale/reverted scoreHome of 119 — not an actual scoring event, so it
        # must not overwrite the true final score.
        events = [
            ev(1, "Made Shot", clock="PT00M00.30S", team=HOME_ID,
               score_home="122", score_away="112"),
            ev(2, "Instant Replay", clock="PT00M00.00S", sub="Support Ruling",
               score_home="119", score_away="112"),
            ev(3, "period", clock="PT00M00.00S", sub="end",
               score_home="119", score_away="112"),
        ]
        out = parse(events, record=make_record(final_home=122, final_away=112))
        assert list(out["home_score"]) == [122, 122, 122]


class TestPossessionInference:
    def test_full_possession_script(self):
        events = [
            ev(1, "period", sub="start"),                                     # unknown
            ev(2, "Jump Ball", clock="PT11M58.00S", team=HOME_ID,
               desc="Jump Ball A vs. B: Tip to C"),                           # recipient unknown → 0
            ev(3, "Made Shot", clock="PT11M40.00S", team=HOME_ID,
               score_home="2", score_away="0"),                               # made → away ball
            ev(4, "Missed Shot", clock="PT11M20.00S", team=AWAY_ID,
               desc="MISS Jumper"),                                           # miss → pending (away)
            ev(5, "Rebound", clock="PT11M18.00S", team=HOME_ID),              # def reb → home ball
            ev(6, "Missed Shot", clock="PT11M00.00S", team=HOME_ID,
               desc="MISS Layup"),                                            # miss → pending (home)
            ev(7, "Rebound", clock="PT10M58.00S", team=HOME_ID),              # off reb → home keeps
            ev(8, "Turnover", clock="PT10M40.00S", team=HOME_ID,
               sub="Bad Pass"),                                               # TO → away ball
            ev(9, "Substitution", clock="PT10M30.00S", team=AWAY_ID),         # sub: unchanged
            ev(10, "", clock="PT10M20.00S", team=AWAY_ID,
               desc="X STEAL (1 STL)"),                                       # steal row: no-op
        ]
        out = parse(events)
        assert list(out["possession"]) == [0, 0, -1, -1, 1, 1, 1, -1, -1, -1]

    def test_team_rebound_attributed_via_person_id(self):
        events = [
            ev(1, "Missed Shot", clock="PT11M20.00S", team=AWAY_ID, desc="MISS"),
            ev(2, "Rebound", clock="PT11M18.00S", sub="Normal Rebound",
               location="h", person=HOME_ID, desc="HHH Rebound"),             # team def rebound
        ]
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))
        assert out.iloc[-1]["possession"] == 1

    def test_made_final_free_throw_flips_possession(self):
        events = [
            ev(1, "Turnover", clock="PT09M00.00S", team=HOME_ID),              # away ball
            ev(2, "Foul", clock="PT08M50.00S", sub="Shooting", team=HOME_ID),
            ev(3, "Free Throw", clock="PT08M50.00S", sub="Free Throw 1 of 2",
               team=AWAY_ID, shot_result="Made", score_home="0", score_away="1"),
            ev(4, "Free Throw", clock="PT08M50.00S", sub="Free Throw 2 of 2",
               team=AWAY_ID, shot_result="Made", score_home="0", score_away="2"),
        ]
        out = parse(events, record=make_record(final_home=0, final_away=2))
        assert list(out["possession"]) == [-1, -1, -1, 1]

    def test_missed_final_free_throw_leaves_rebound_pending(self):
        events = [
            ev(1, "Foul", clock="PT08M50.00S", sub="Shooting", team=HOME_ID),
            ev(2, "Free Throw", clock="PT08M50.00S", sub="Free Throw 1 of 1",
               team=AWAY_ID, shot_result="Missed", desc="MISS FT"),
            ev(3, "Rebound", clock="PT08M48.00S", team=HOME_ID),               # def reb → home
        ]
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))
        assert list(out["possession"]) == [0, -1, 1]


class TestFoulsAndBonus:
    def test_fouls_count_reset_each_period_and_bonus(self):
        fouls_q1 = [
            ev(i, "Foul", clock=f"PT{11 - i:02d}M00.00S", sub="Personal",
               team=HOME_ID)
            for i in range(1, 6)                                       # 5 home fouls in Q1
        ]
        events = (
            [ev(0, "period", sub="start")]
            + fouls_q1
            + [ev(10, "period", clock="PT00M00.00S", sub="end")]
            + [ev(11, "period", period=2, sub="start")]                # Q2 start: reset
            + [ev(12, "Foul", period=2, clock="PT11M00.00S", sub="Personal",
                  team=AWAY_ID)]
        )
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))

        q1_last_foul = out.iloc[5]
        assert q1_last_foul["home_team_fouls_period"] == 5
        # home has 5 team fouls → away shoots FTs → AWAY is in the bonus
        assert bool(q1_last_foul["away_in_bonus"]) is True
        assert bool(q1_last_foul["home_in_bonus"]) is False

        q2_start = out.iloc[7]
        assert q2_start["home_team_fouls_period"] == 0
        assert bool(q2_start["away_in_bonus"]) is False

        q2_away_foul = out.iloc[8]
        assert q2_away_foul["away_team_fouls_period"] == 1

    def test_offensive_and_technical_fouls_do_not_count_toward_bonus(self):
        events = [
            ev(1, "Foul", clock="PT11M00.00S", sub="Offensive", team=HOME_ID),
            ev(2, "Foul", clock="PT10M00.00S", sub="Offensive Charge", team=HOME_ID),
            ev(3, "Foul", clock="PT09M00.00S", sub="Technical", team=HOME_ID),
            ev(4, "Foul", clock="PT08M00.00S", sub="Personal", team=HOME_ID),
        ]
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))
        assert out.iloc[-1]["home_team_fouls_period"] == 1


class TestTimeouts:
    def test_timeouts_decrement_from_seven_attributed_by_location(self):
        # V3 timeout rows carry teamId=0; the 'location' column says which side
        events = [
            ev(1, "Timeout", clock="PT10M00.00S", sub="Regular", location="v",
               desc="AAA Timeout: Regular"),
            ev(2, "Timeout", clock="PT08M00.00S", sub="Regular", location="h",
               desc="HHH Timeout: Regular"),
        ]
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))
        assert out.iloc[0]["away_timeouts_remaining"] == 6
        assert out.iloc[0]["home_timeouts_remaining"] == 7
        assert out.iloc[1]["home_timeouts_remaining"] == 6

    def test_unattributable_timeout_ignored(self):
        events = [
            ev(1, "Timeout", clock="PT10M00.00S", sub="Regular"),  # official/TV timeout
        ]
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))
        assert out.iloc[0]["home_timeouts_remaining"] == 7
        assert out.iloc[0]["away_timeouts_remaining"] == 7

    def test_inconsistent_tracking_becomes_none(self):
        events = [
            ev(i, "Timeout", clock=f"PT{11 - i:02d}M00.00S", sub="Regular",
               location="h")
            for i in range(1, 10)  # 9 timeouts: more than the 7 allowed
        ]
        out = parse(events, record=make_record(final_home=0, final_away=0, home_win=False))
        assert pd.isna(out.iloc[-1]["home_timeouts_remaining"])


class TestOutputContract:
    def test_one_row_per_event_with_label_and_valid_states(self):
        events = [
            ev(1, "period", sub="start"),
            ev(2, "Made Shot", clock="PT11M40.00S", team=HOME_ID,
               score_home="2", score_away="0"),
            ev(3, "period", clock="PT00M00.00S", sub="end"),
        ]
        out = parse(events)
        assert len(out) == 3
        assert set(GameState.model_fields) <= set(out.columns)
        assert "home_win" in out.columns
        assert out["home_win"].all()  # home won 2-0
        # every row must validate as a GameState
        for _, row in out.iterrows():
            state = {k: row[k] for k in GameState.model_fields}
            for key, value in state.items():
                if pd.isna(value):
                    state[key] = None
                elif hasattr(value, "item"):
                    state[key] = value.item()
            GameState(**state)

    def test_out_of_order_events_reordered_chronologically(self):
        # Real V3 feeds log substitutions/amendments late with earlier clocks;
        # the parser must emit rows in game-time order, not log order.
        events = [
            ev(10, "Made Shot", clock="PT06M56.00S", team=HOME_ID,
               score_home="2", score_away="0"),
            ev(11, "Substitution", clock="PT07M39.00S", team=AWAY_ID),  # earlier game time
        ]
        out = parse(events)
        assert list(out["event_num"]) == [11, 10]
        assert out["seconds_remaining_total"].is_monotonic_decreasing

    def test_event_num_from_action_number(self):
        events = [
            ev(7, "period", sub="start"),
            ev(9, "Made Shot", clock="PT11M40.00S", team=HOME_ID,
               score_home="2", score_away="0"),
        ]
        out = parse(events)
        assert list(out["event_num"]) == [7, 9]
