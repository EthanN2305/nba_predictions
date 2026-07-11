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

import argparse
import hashlib
import json
import math
import random
from collections import deque
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from wp_engine.collect import default_data_dir
from wp_engine.schemas import GameState

REGULATION_PERIOD_SECONDS = 720.0
OT_PERIOD_SECONDS = 300.0
TIME_SINCE_LEAD_CHANGE_CAP = 1200.0

# Empirical median of non-null timeouts_remaining across the harvested
# 2023-24 season (nulls are ~0.2% of events). Used when Phase 1 lost track.
TIMEOUTS_IMPUTE_VALUE = 5.0

CLUTCH_SECONDS = 300.0
CLUTCH_MAX_DIFF = 5

RUN_WINDOW_SHORT = 120.0
RUN_WINDOW_LONG = 300.0
MOMENTUM_HALFLIFE = 90.0

# NOTE deviation from the phase doc: `turnovers_last_300s_diff` is NOT
# implemented — GameState carries no turnover information, and the Golden
# Rule forbids features that the live GameState stream cannot produce.

REST_DAYS_CAP = 7.0
DEFAULT_REST_DAYS = 2.0  # typical NBA rest, used when pregame context is absent

# THE canonical, ordered feature list. Phase 3 trains on exactly these
# columns in this order; Phase 4 must read them from feature_meta.json.
FEATURE_COLUMNS: tuple[str, ...] = (
    # Checkpoint 2.1 — core clock & score
    "score_diff",
    "seconds_remaining",
    "period",
    "is_overtime",
    "diff_per_sqrt_time",
    "score_total",
    "lead_changes_so_far",
    "time_since_lead_change",
    "largest_lead_home",
    "largest_lead_away",
    # Checkpoint 2.2 — game situation
    "possession",
    "possession_x_time",
    "home_in_bonus",
    "away_in_bonus",
    "foul_diff_period",
    "home_timeouts_remaining",
    "away_timeouts_remaining",
    "timeouts_known",
    "is_clutch",
    # Checkpoint 2.3 — rolling momentum (trailing game-clock windows)
    "run_last_120s",
    "run_last_300s",
    "scoring_rate_home_300s",
    "scoring_rate_away_300s",
    "fouls_last_300s_diff",
    "momentum_ewm",
    # Checkpoint 2.4 — pregame context
    "pregame_win_pct_home",
    "pregame_win_pct_away",
    "pregame_win_pct_diff",
    "rest_days_home",
    "rest_days_away",
)


class PregameContext(BaseModel):
    """Static pregame metadata, computed as-of game date with no leakage.

    Neutral defaults let live inference start before context is available.
    Hooks for Elo / net-rating differentials can be added here later without
    touching ``FeatureBuilder``.
    """

    home_win_pct: float = Field(default=0.5, ge=0, le=1)
    away_win_pct: float = Field(default=0.5, ge=0, le=1)
    home_rest_days: float = Field(default=DEFAULT_REST_DAYS, ge=0)
    away_rest_days: float = Field(default=DEFAULT_REST_DAYS, ge=0)


def build_pregame_context(game_index: pd.DataFrame, game_id: str) -> PregameContext:
    """Compute pregame context for ``game_id`` from the Phase 1 game index.

    Only games on strictly earlier dates count (same-day games are excluded
    because their ordering is unknowable pregame). Season openers get a
    neutral 0.5 win% and the ``REST_DAYS_CAP`` rest value.
    """
    idx = game_index.copy()
    idx["game_date"] = pd.to_datetime(idx["game_date"])
    match = idx[idx["game_id"] == game_id]
    if match.empty:
        raise KeyError(f"game_id {game_id!r} not in game index")
    game = match.iloc[0]
    prior = idx[idx["game_date"] < game["game_date"]]

    def win_pct(team_id: int) -> float:
        as_home = prior[prior["home_team_id"] == team_id]
        as_away = prior[prior["away_team_id"] == team_id]
        played = len(as_home) + len(as_away)
        if played == 0:
            return 0.5
        wins = int(as_home["home_win"].sum()) + int((~as_away["home_win"]).sum())
        return wins / played

    def rest_days(team_id: int) -> float:
        dates = prior.loc[
            (prior["home_team_id"] == team_id) | (prior["away_team_id"] == team_id),
            "game_date",
        ]
        if dates.empty:
            return REST_DAYS_CAP
        return min((game["game_date"] - dates.max()).days, REST_DAYS_CAP)

    return PregameContext(
        home_win_pct=win_pct(game["home_team_id"]),
        away_win_pct=win_pct(game["away_team_id"]),
        home_rest_days=rest_days(game["home_team_id"]),
        away_rest_days=rest_days(game["away_team_id"]),
    )


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

    def __init__(self, pregame: PregameContext | None = None) -> None:
        self._pregame = pregame if pregame is not None else PregameContext()
        self._lead_changes = 0
        self._last_leader = 0  # sign of last nonzero score_diff seen
        self._last_lead_change_elapsed: float | None = None
        self._largest_lead_home = 0
        self._largest_lead_away = 0
        # Trailing-window bookkeeping. Baseline is the 0-0 / 0-foul game
        # start; deltas are per-event changes. Entries:
        # (elapsed, home_pts, away_pts, home_fouls, away_fouls)
        self._window_events: deque[tuple[float, int, int, int, int]] = deque()
        self._prev_elapsed = 0.0
        self._prev_home_score = 0
        self._prev_away_score = 0
        self._prev_period = 0
        self._prev_home_fouls = 0
        self._prev_away_fouls = 0
        self._momentum = 0.0

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

        # --- trailing-window momentum bookkeeping -------------------------
        home_pts = state.home_score - self._prev_home_score
        away_pts = state.away_score - self._prev_away_score
        if state.period == self._prev_period:
            # counters only ever increase within a period; guard anyway
            home_fouls = max(0, state.home_team_fouls_period - self._prev_home_fouls)
            away_fouls = max(0, state.away_team_fouls_period - self._prev_away_fouls)
        else:
            # period boundary: counters reset, the new value IS the delta
            home_fouls = state.home_team_fouls_period
            away_fouls = state.away_team_fouls_period

        dt = elapsed - self._prev_elapsed
        if dt > 0:
            self._momentum *= 0.5 ** (dt / MOMENTUM_HALFLIFE)
        self._momentum += home_pts - away_pts

        if home_pts or away_pts or home_fouls or away_fouls:
            self._window_events.append(
                (elapsed, home_pts, away_pts, home_fouls, away_fouls)
            )
        # evict anything at least RUN_WINDOW_LONG game-seconds old
        while self._window_events and self._window_events[0][0] <= elapsed - RUN_WINDOW_LONG:
            self._window_events.popleft()

        home_pts_300 = away_pts_300 = fouls_diff_300 = 0.0
        run_120 = 0.0
        for ts, h_pts, a_pts, h_f, a_f in self._window_events:
            home_pts_300 += h_pts
            away_pts_300 += a_pts
            fouls_diff_300 += h_f - a_f
            if ts > elapsed - RUN_WINDOW_SHORT:
                run_120 += h_pts - a_pts

        self._prev_elapsed = elapsed
        self._prev_home_score = state.home_score
        self._prev_away_score = state.away_score
        self._prev_period = state.period
        self._prev_home_fouls = state.home_team_fouls_period
        self._prev_away_fouls = state.away_team_fouls_period
        # ------------------------------------------------------------------

        timeouts_known = (
            state.home_timeouts_remaining is not None
            and state.away_timeouts_remaining is not None
        )
        home_timeouts = (
            float(state.home_timeouts_remaining)
            if state.home_timeouts_remaining is not None
            else TIMEOUTS_IMPUTE_VALUE
        )
        away_timeouts = (
            float(state.away_timeouts_remaining)
            if state.away_timeouts_remaining is not None
            else TIMEOUTS_IMPUTE_VALUE
        )

        clutch_time = state.period >= 5 or (
            state.period == 4 and state.seconds_remaining_period <= CLUTCH_SECONDS
        )
        is_clutch = clutch_time and abs(state.score_diff) <= CLUTCH_MAX_DIFF

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
            "possession": float(state.possession),
            "possession_x_time": state.possession / math.sqrt(seconds_remaining + 1),
            "home_in_bonus": float(state.home_in_bonus),
            "away_in_bonus": float(state.away_in_bonus),
            "foul_diff_period": float(
                state.home_team_fouls_period - state.away_team_fouls_period
            ),
            "home_timeouts_remaining": home_timeouts,
            "away_timeouts_remaining": away_timeouts,
            "timeouts_known": float(timeouts_known),
            "is_clutch": float(is_clutch),
            "run_last_120s": run_120,
            "run_last_300s": home_pts_300 - away_pts_300,
            "scoring_rate_home_300s": home_pts_300 / (RUN_WINDOW_LONG / 60.0),
            "scoring_rate_away_300s": away_pts_300 / (RUN_WINDOW_LONG / 60.0),
            "fouls_last_300s_diff": fouls_diff_300,
            "momentum_ewm": self._momentum,
            "pregame_win_pct_home": self._pregame.home_win_pct,
            "pregame_win_pct_away": self._pregame.away_win_pct,
            "pregame_win_pct_diff": self._pregame.home_win_pct
            - self._pregame.away_win_pct,
            "rest_days_home": self._pregame.home_rest_days,
            "rest_days_away": self._pregame.away_rest_days,
        }


def state_from_row(row: pd.Series) -> GameState:
    """One parsed-states parquet row → GameState (pd.NA timeouts → None)."""
    home_to = row["home_timeouts_remaining"]
    away_to = row["away_timeouts_remaining"]
    return GameState(
        game_id=row["game_id"],
        period=int(row["period"]),
        seconds_remaining_period=float(row["seconds_remaining_period"]),
        seconds_remaining_total=float(row["seconds_remaining_total"]),
        home_score=int(row["home_score"]),
        away_score=int(row["away_score"]),
        score_diff=int(row["score_diff"]),
        home_team_fouls_period=int(row["home_team_fouls_period"]),
        away_team_fouls_period=int(row["away_team_fouls_period"]),
        home_in_bonus=bool(row["home_in_bonus"]),
        away_in_bonus=bool(row["away_in_bonus"]),
        possession=int(row["possession"]),
        home_timeouts_remaining=None if pd.isna(home_to) else int(home_to),
        away_timeouts_remaining=None if pd.isna(away_to) else int(away_to),
        event_num=int(row["event_num"]),
    )


def build_offline(
    states: pd.DataFrame, pregame: PregameContext | None = None
) -> pd.DataFrame:
    """Offline feature path: one game's chronological GameState rows → matrix.

    Deliberately implemented as a row-by-row loop over the SAME
    ``FeatureBuilder`` that serves live inference — feature logic exists in
    exactly one place, so training/serving skew is impossible by
    construction. The parity test in ``tests/test_offline_parity.py`` pins
    this contract for any future (e.g. vectorized) reimplementation.
    """
    builder = FeatureBuilder(pregame)
    rows = [builder.update(state_from_row(row)) for _, row in states.iterrows()]
    return pd.DataFrame(rows, columns=list(FEATURE_COLUMNS), dtype="float64")


MATRIX_EXTRA_COLUMNS: tuple[str, ...] = ("game_id", "event_num", "home_win")


def build_game_matrix(
    states: pd.DataFrame, pregame: PregameContext | None = None
) -> pd.DataFrame:
    """One game's training-matrix rows: features + game_id/event_num/label.

    Features are computed on the FULL event stream (running counts like
    lead changes must see every event), THEN downsampled to at most one row
    per game-clock second, keeping the last event of each second.
    """
    matrix = build_offline(states, pregame)
    matrix["game_id"] = states["game_id"].to_numpy()
    matrix["event_num"] = states["event_num"].to_numpy()
    matrix["home_win"] = states["home_win"].to_numpy()
    second = [
        int(game_elapsed_seconds(int(p), float(s)))
        for p, s in zip(states["period"], states["seconds_remaining_period"])
    ]
    matrix["_second"] = second
    matrix = matrix.drop_duplicates(subset="_second", keep="last")
    return matrix.drop(columns="_second").reset_index(drop=True)


def _game_index_path(data_dir: Path, season: str) -> Path:
    return data_dir / "raw" / f"game_index_{season}.parquet"


def _code_version() -> str:
    """Content hash of this module — recorded in feature_meta.json so Phase 4
    can detect a stale model/feature-code mismatch."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def feature_meta() -> dict:
    """The feature contract Phase 3 trains against and Phase 4 must load."""
    return {
        "feature_columns": list(FEATURE_COLUMNS),
        "dtypes": {c: "float64" for c in FEATURE_COLUMNS},
        "imputation": {
            "timeouts_remaining": TIMEOUTS_IMPUTE_VALUE,
            "rest_days_default": DEFAULT_REST_DAYS,
            "pregame_win_pct_default": 0.5,
        },
        "sampling": (
            "features computed on the full event stream, then downsampled to "
            "at most one row per game-clock second per game (last event of "
            "each second wins)"
        ),
        "ot_handling": (
            "seconds_remaining = regulation seconds left; in OT it is the "
            "seconds left in the current OT only, with is_overtime = 1"
        ),
        "label": "home_win",
        "code_version": _code_version(),
    }


def build_season(season: str, *, data_dir: Path | None = None) -> dict:
    """Materialize ``data/processed/features_{season}.parquet`` plus
    ``data/models/feature_meta.json`` from the Phase 1 parsed states."""
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    index = pd.read_parquet(_game_index_path(data_dir, season))
    state_files = sorted((data_dir / "raw" / "states" / season).glob("*.parquet"))

    frames = []
    for path in state_files:
        states = pd.read_parquet(path)
        pregame = build_pregame_context(index, path.stem)
        frames.append(build_game_matrix(states, pregame))
    matrix = pd.concat(frames, ignore_index=True)

    out_path = data_dir / "processed" / f"features_{season}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(out_path)

    meta_path = data_dir / "models" / "feature_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(feature_meta(), indent=2) + "\n")

    return {
        "season": season,
        "games": len(frames),
        "rows": len(matrix),
        "home_win_rate": float(matrix["home_win"].mean()),
        "out_path": str(out_path),
        "meta_path": str(meta_path),
    }


def check_parity(
    season: str, *, n: int = 20, data_dir: Path | None = None, seed: int = 0
) -> list[str]:
    """Assert offline/incremental parity on ``n`` sampled harvested games.

    Raises ``AssertionError`` on the first mismatching game; returns the
    list of checked game_ids on success.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    index = pd.read_parquet(_game_index_path(data_dir, season))
    state_files = sorted((data_dir / "raw" / "states" / season).glob("*.parquet"))
    sample = random.Random(seed).sample(state_files, min(n, len(state_files)))

    checked = []
    for path in sample:
        states = pd.read_parquet(path)
        pregame = build_pregame_context(index, path.stem)
        offline = build_offline(states, pregame)
        builder = FeatureBuilder(pregame)
        incremental = pd.DataFrame(
            [builder.update(state_from_row(row)) for _, row in states.iterrows()],
            columns=list(FEATURE_COLUMNS),
            dtype="float64",
        )
        pd.testing.assert_frame_equal(offline, incremental, check_exact=True)
        checked.append(path.stem)
    return checked


SANITY_DIFF_BINS = [float("-inf"), -20, -10, -5, -2, 2, 5, 10, 20, float("inf")]
SANITY_TIME_BINS = [0, 300, 720, 1440, 2160, 2880]


def sanity_table(season: str, *, data_dir: Path | None = None) -> pd.DataFrame:
    """Empirical P(home win) bucketed by (seconds_remaining, score_diff).

    The classic WP fan chart in table form: rows are time buckets
    (ascending seconds remaining; OT rows land in the lowest bucket since
    OT ``seconds_remaining`` is <= 300), columns are score-diff buckets.
    Reads the materialized matrix, so run ``build`` first.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    matrix = pd.read_parquet(
        data_dir / "processed" / f"features_{season}.parquet",
        columns=["seconds_remaining", "score_diff", "home_win"],
    )
    time_bucket = pd.cut(
        matrix["seconds_remaining"], SANITY_TIME_BINS, right=False, include_lowest=True
    )
    diff_bucket = pd.cut(matrix["score_diff"], SANITY_DIFF_BINS, right=False)
    return matrix.pivot_table(
        values="home_win",
        index=time_bucket,
        columns=diff_bucket,
        aggfunc="mean",
        observed=False,
    )


def main(argv: list[str] | None = None) -> None:
    """CLI: ``python -m wp_engine.features {build,parity,sanity} --season 2023-24``."""
    parser = argparse.ArgumentParser(prog="python -m wp_engine.features")
    parser.add_argument("command", choices=["build", "parity", "sanity"])
    parser.add_argument("--season", required=True, help='e.g. "2023-24"')
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--n", type=int, default=20, help="parity sample size")
    args = parser.parse_args(argv)

    if args.command == "build":
        summary = build_season(args.season, data_dir=args.data_dir)
        print(json.dumps(summary, indent=2))
    elif args.command == "parity":
        checked = check_parity(args.season, n=args.n, data_dir=args.data_dir)
        print(f"parity OK on {len(checked)} games: {', '.join(checked)}")
    else:
        table = sanity_table(args.season, data_dir=args.data_dir)
        print(f"empirical home win rate by (time, score_diff) bucket — {args.season}")
        print(table.round(3).to_string())


if __name__ == "__main__":
    main()
