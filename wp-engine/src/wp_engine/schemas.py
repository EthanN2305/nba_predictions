"""Canonical Pydantic schemas shared by every phase of the win-probability engine.

``GameState`` is the contract between the historical parser (Phase 1), the
feature builder (Phase 2), and the live adapter (Phase 4). Field names must
never change without updating all phases.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class GameState(BaseModel):
    """One snapshot of an NBA game at a single play-by-play event.

    Time convention: ``seconds_remaining_total`` is true seconds remaining in
    the game including any overtime periods (regulation periods are 720s,
    OT periods 300s). Phase 2 decides how to normalize OT for modeling.
    """

    game_id: str
    period: int = Field(ge=1, description="1-4 regulation, 5+ = OT")
    seconds_remaining_period: float = Field(ge=0)
    seconds_remaining_total: float = Field(ge=0)
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    score_diff: int = Field(description="home_score - away_score")
    home_team_fouls_period: int = Field(ge=0)
    away_team_fouls_period: int = Field(ge=0)
    home_in_bonus: bool
    away_in_bonus: bool
    possession: Literal[1, -1, 0] = Field(
        description="1 = home, -1 = away, 0 = unknown/dead ball"
    )
    home_timeouts_remaining: int | None = Field(default=None, ge=0)
    away_timeouts_remaining: int | None = Field(default=None, ge=0)
    event_num: int = Field(description="ordering key within the game")


class GameRecord(BaseModel):
    """One historical game with its final outcome — the training label source."""

    game_id: str
    season: str = Field(description='e.g. "2023-24"')
    game_date: date
    home_team_id: int
    away_team_id: int
    home_team_abbr: str
    away_team_abbr: str
    final_home_score: int = Field(ge=0)
    final_away_score: int = Field(ge=0)
    home_win: bool = Field(description="THE LABEL: final_home_score > final_away_score")
