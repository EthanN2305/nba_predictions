"""Phase 4 — live NBA feed adapter: cdn.nba.com liveData → canonical GameState.

The live endpoints return DIFFERENT shapes than the historical stats API.
actionType mapping table (liveData string → state effect):

| liveData actionType        | effect on GameState                              |
|----------------------------|--------------------------------------------------|
| ``2pt`` / ``3pt`` (Made)   | trust scoreHome/scoreAway (+ monotonic guard)    |
| ``freethrow`` (Made)       | trust scoreHome/scoreAway (+ monotonic guard)    |
| ``foul`` (subType not offensive/technical/double) | team-foul count, bonus  |
| ``timeout`` (with teamId)  | decrement that team's timeouts (None if broken)  |
| ``period`` (start/end)     | period-foul counters reset on period change      |
| ``rebound``/``turnover``/``jumpball``/``steal``/``block``/… | state no-op — |
|                            | possession comes from the explicit field below   |

Unlike the historical V3 feed, liveData actions carry an explicit
``possession`` field (the teamId in possession) — used directly instead of
Phase 1's inference rules, with last-known-value carry-forward.

Defensive posture (the live API is undocumented and shifts): unknown fields
and actionTypes are ignored, malformed clocks/periods skip the event, scores
are only trusted on scoring actions and never allowed to decrease.

NOTE: cdn.nba.com was Akamai-blocked from the development network, so the
adapter is validated against synthesized fixtures in the documented payload
shape plus the replay pipeline — verify against a real live payload from an
unblocked network before production use (see HANDOFF.md).
"""

import asyncio
import logging

import httpx

from wp_engine.collect import ParseError, parse_clock, seconds_remaining_total
from wp_engine.schemas import GameState

logger = logging.getLogger(__name__)

SCORING_ACTION_TYPES = {"2pt", "3pt", "freethrow"}
EXCLUDED_FOUL_SUBTYPES = ("offensive", "technical", "double")

LIVE_PBP_URL = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"
SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
LIVE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json",
    "Referer": "https://www.nba.com/",
}


def display_clock(period: int, seconds_remaining_period: float) -> str:
    """Human display string: ``Q4 02:31`` in regulation, ``OT1 00:45`` in OT."""
    label = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    minutes, seconds = divmod(int(round(seconds_remaining_period)), 60)
    return f"{label} {minutes:02d}:{seconds:02d}"


def _parse_score(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


class LiveGameAdapter:
    """Stateful converter: cumulative liveData actions → chronological GameStates.

    ``process`` stores every action by ``actionNumber`` (amendments share the
    number — the latest version wins) and REPLAYS the state machine from
    scratch each call. A full game is only ~600 events, so a rebuild costs
    ~1 ms and makes dedup/amendment/out-of-order arrival trivially correct.
    """

    def __init__(self, *, game_id: str, home_team_id: int, away_team_id: int) -> None:
        self.game_id = game_id
        self.home_team_id = home_team_id
        self.away_team_id = away_team_id
        self._actions: dict[int, dict] = {}

    def _side(self, team_id) -> int:
        if team_id == self.home_team_id:
            return 1
        if team_id == self.away_team_id:
            return -1
        return 0

    def process(self, actions: list[dict]) -> list[GameState]:
        """Ingest a (cumulative or incremental) action batch; return the full
        chronological GameState history."""
        for action in actions:
            number = action.get("actionNumber")
            if isinstance(number, int):
                self._actions[number] = action

        ordered: list[tuple[int, float, int, dict]] = []
        for number, action in self._actions.items():
            try:
                seconds = parse_clock(action.get("clock", ""))
                period = int(action.get("period"))
            except (ParseError, TypeError, ValueError):
                logger.warning("skipping malformed live action %s", number)
                continue
            if period < 1:
                continue
            ordered.append((period, seconds, number, action))
        # chronological: period asc, clock desc (more seconds = earlier), number
        ordered.sort(key=lambda item: (item[0], -item[1], item[2]))

        states: list[GameState] = []
        home_score = away_score = 0
        home_fouls = away_fouls = 0
        home_timeouts: int | None = 7
        away_timeouts: int | None = 7
        possession = 0
        current_period = 0

        for period, seconds, number, action in ordered:
            if period != current_period:
                home_fouls = away_fouls = 0
                current_period = period

            action_type = str(action.get("actionType", "")).lower()
            sub_type = str(action.get("subType", "")).lower()
            side = self._side(action.get("teamId"))

            if action_type in SCORING_ACTION_TYPES and (
                str(action.get("shotResult", "")).lower() == "made"
            ):
                new_home = _parse_score(action.get("scoreHome"))
                new_away = _parse_score(action.get("scoreAway"))
                # scores never decrease in basketball — reject corrupt rows
                if (
                    new_home is not None
                    and new_away is not None
                    and new_home >= home_score
                    and new_away >= away_score
                ):
                    home_score, away_score = new_home, new_away
                else:
                    logger.warning(
                        "ignoring non-monotonic score on action %s", number
                    )
            elif action_type == "foul" and side != 0:
                if not any(x in sub_type for x in EXCLUDED_FOUL_SUBTYPES):
                    if side == 1:
                        home_fouls += 1
                    else:
                        away_fouls += 1
            elif action_type == "timeout" and side != 0:
                if side == 1:
                    home_timeouts = (
                        home_timeouts - 1
                        if home_timeouts is not None and home_timeouts > 0
                        else None
                    )
                else:
                    away_timeouts = (
                        away_timeouts - 1
                        if away_timeouts is not None and away_timeouts > 0
                        else None
                    )

            if "possession" in action:
                possession = self._side(action.get("possession"))

            states.append(
                GameState(
                    game_id=self.game_id,
                    period=period,
                    seconds_remaining_period=seconds,
                    seconds_remaining_total=seconds_remaining_total(period, seconds),
                    home_score=home_score,
                    away_score=away_score,
                    score_diff=home_score - away_score,
                    home_team_fouls_period=home_fouls,
                    away_team_fouls_period=away_fouls,
                    home_in_bonus=away_fouls >= 5,
                    away_in_bonus=home_fouls >= 5,
                    possession=possession,
                    home_timeouts_remaining=home_timeouts,
                    away_timeouts_remaining=away_timeouts,
                    event_num=number,
                )
            )
        return states


# Courtesy cap on concurrent NBA requests — even with 10+ simultaneous game
# pollers, at most N requests are in flight. Semaphores bind to the running
# event loop, so keep one per loop.
_semaphores: dict[int, asyncio.Semaphore] = {}


def _nba_semaphore() -> asyncio.Semaphore:
    from wp_engine.config import get_settings

    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _semaphores:
        _semaphores[loop_id] = asyncio.Semaphore(
            get_settings().max_concurrent_nba_requests
        )
    return _semaphores[loop_id]


async def fetch_live_pbp(
    game_id: str, client: httpx.AsyncClient
) -> list[dict]:
    """Fetch the raw liveData action list for one game (raises on HTTP error)."""
    async with _nba_semaphore():
        response = await client.get(
            LIVE_PBP_URL.format(game_id=game_id), headers=LIVE_HEADERS, timeout=10
        )
    response.raise_for_status()
    return response.json()["game"]["actions"]


async def fetch_scoreboard(client: httpx.AsyncClient) -> list[dict]:
    """Fetch today's games from the live scoreboard."""
    async with _nba_semaphore():
        response = await client.get(SCOREBOARD_URL, headers=LIVE_HEADERS, timeout=10)
    response.raise_for_status()
    return response.json()["scoreboard"]["games"]
