"""Phase 1: historical play-by-play data collection.

Pipeline: ``build_game_index`` (one row per game with the home_win label) →
``harvest_pbp`` (raw PlayByPlayV2 frames, one parquet per game) →
``parse_game`` (raw events → canonical GameState rows).

All network access goes through injectable ``fetch`` callables so the
transformation logic is unit-testable offline; the default fetchers wrap
``nba_api`` with rate limiting and retries.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from wp_engine.schemas import GameRecord, GameState

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    """Data root: $WP_DATA_DIR if set, else <repo>/data."""
    return Path(os.environ.get("WP_DATA_DIR", _REPO_ROOT / "data"))


GAME_INDEX_COLUMNS = [f for f in GameRecord.model_fields]


def _fetch_league_game_log(season: str) -> pd.DataFrame:
    """Fetch one-row-per-team-per-game logs for a Regular Season via nba_api."""
    from nba_api.stats.endpoints import leaguegamelog

    log = leaguegamelog.LeagueGameLog(
        season=season, season_type_all_star="Regular Season"
    )
    return log.get_data_frames()[0]


def build_game_index(
    season: str,
    *,
    data_dir: Path | None = None,
    fetch: Callable[[str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Build the one-row-per-game index with final scores and the home_win label.

    The league game log returns one row per team per game; home/away is
    resolved from MATCHUP ("XXX vs. YYY" = XXX at home, "XXX @ YYY" = XXX away).
    Games missing either side's row are skipped with a warning.

    Saves ``data/raw/game_index_{season}.parquet`` and returns the dataframe.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    fetch = fetch or _fetch_league_game_log

    team_rows = fetch(season)
    records: list[GameRecord] = []
    for game_id, group in team_rows.groupby("GAME_ID"):
        home = group[group["MATCHUP"].str.contains(" vs. ", regex=False)]
        away = group[group["MATCHUP"].str.contains(" @ ", regex=False)]
        if len(home) != 1 or len(away) != 1:
            logger.warning(
                "Skipping game %s: expected 1 home + 1 away row, got %d rows",
                game_id,
                len(group),
            )
            continue
        h, a = home.iloc[0], away.iloc[0]
        records.append(
            GameRecord(
                game_id=str(game_id),
                season=season,
                game_date=pd.Timestamp(h["GAME_DATE"]).date(),
                home_team_id=int(h["TEAM_ID"]),
                away_team_id=int(a["TEAM_ID"]),
                home_team_abbr=str(h["TEAM_ABBREVIATION"]),
                away_team_abbr=str(a["TEAM_ABBREVIATION"]),
                final_home_score=int(h["PTS"]),
                final_away_score=int(a["PTS"]),
                home_win=int(h["PTS"]) > int(a["PTS"]),
            )
        )

    index = pd.DataFrame([r.model_dump() for r in records], columns=GAME_INDEX_COLUMNS)
    index = index.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    out = data_dir / "raw" / f"game_index_{season}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    index.to_parquet(out, index=False)
    logger.info("Wrote %d games to %s", len(index), out)
    return index


# ---------------------------------------------------------------------------
# Checkpoint 1.3 — play-by-play harvester
# ---------------------------------------------------------------------------

REQUEST_INTERVAL_S = 0.7
BACKOFF_SCHEDULE_S = (2, 4, 8, 16)


@dataclass
class HarvestSummary:
    """Outcome of one harvest_pbp run."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _fetch_playbyplay(game_id: str) -> pd.DataFrame:
    """Fetch the raw PlayByPlayV3 frame for one game via nba_api.

    V3, not the V2 the phase docs name: the stats API stopped serving
    PlayByPlayV2 (nba_api issue #591).
    """
    from nba_api.stats.endpoints import playbyplayv3

    return playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=30).get_data_frames()[0]


def pbp_path(data_dir: Path, season: str, game_id: str) -> Path:
    return data_dir / "raw" / "pbp" / season / f"{game_id}.parquet"


def harvest_pbp(
    season: str,
    *,
    data_dir: Path | None = None,
    fetch_pbp: Callable[[str], pd.DataFrame] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    request_interval_s: float = REQUEST_INTERVAL_S,
    max_retries: int = len(BACKOFF_SCHEDULE_S),
    game_ids: list[str] | None = None,
    progress: bool = True,
) -> HarvestSummary:
    """Download raw PlayByPlayV2 frames for every game in the season index.

    - Resumable: games whose parquet already exists are skipped, so the run
      can be re-launched after a crash without re-downloading.
    - Rate limited: sleeps ``request_interval_s`` after every network call.
    - Retries: up to ``max_retries`` with exponential backoff (2/4/8/16s);
      games that still fail are recorded in ``data/raw/failed_{season}.json``
      (game_id -> last error message) and the run continues.
    - Raw data is sacred: frames are stored untouched, all columns.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    fetch_pbp = fetch_pbp or _fetch_playbyplay

    if game_ids is None:
        index_path = data_dir / "raw" / f"game_index_{season}.parquet"
        game_ids = pd.read_parquet(index_path)["game_id"].tolist()

    summary = HarvestSummary()
    failures: dict[str, str] = {}

    iterator = game_ids
    if progress:
        from tqdm import tqdm

        iterator = tqdm(game_ids, desc=f"harvest {season}", unit="game")

    for n_done, game_id in enumerate(iterator, start=1):
        out = pbp_path(data_dir, season, game_id)
        if out.exists():
            summary.skipped.append(game_id)
            continue

        error: str | None = None
        for attempt in range(1 + max_retries):
            try:
                raw = fetch_pbp(game_id)
                sleep(request_interval_s)
                error = None
                break
            except Exception as exc:  # noqa: BLE001 — any network error is retryable
                error = f"{type(exc).__name__}: {exc}"
                if attempt < max_retries:
                    backoff = BACKOFF_SCHEDULE_S[min(attempt, len(BACKOFF_SCHEDULE_S) - 1)]
                    logger.warning(
                        "Fetch %s failed (attempt %d/%d): %s — retrying in %ds",
                        game_id, attempt + 1, 1 + max_retries, error, backoff,
                    )
                    sleep(backoff)

        if error is not None:
            logger.error("Giving up on game %s: %s", game_id, error)
            failures[game_id] = error
            summary.failed.append(game_id)
            continue

        out.parent.mkdir(parents=True, exist_ok=True)
        raw.to_parquet(out, index=False)
        summary.downloaded.append(game_id)

        if n_done % 50 == 0:
            logger.info(
                "%s: %d done / %d remaining / %d failed",
                season, n_done, len(game_ids) - n_done, len(summary.failed),
            )

    if failures:
        failed_path = data_dir / "raw" / f"failed_{season}.json"
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        failed_path.write_text(json.dumps(failures, indent=2))
        logger.warning("%d games failed; recorded in %s", len(failures), failed_path)

    logger.info(
        "Harvest %s complete: %d downloaded, %d skipped, %d failed",
        season, len(summary.downloaded), len(summary.skipped), len(summary.failed),
    )
    return summary


# ---------------------------------------------------------------------------
# Checkpoint 1.4 — event stream → GameState parser (PlayByPlayV3)
# ---------------------------------------------------------------------------
# NOTE: the phase docs specified PlayByPlayV2, but the NBA stats API no longer
# serves V2 data (nba_api issue #591), so harvesting and parsing target
# PlayByPlayV3: ISO-8601 clock strings, actionType/subType strings, explicit
# scoreHome/scoreAway columns, and 'location' (h/v) for team attribution.


class ParseError(Exception):
    """A game's raw play-by-play could not be parsed into valid GameStates."""


REGULATION_PERIODS = 4
REGULATION_PERIOD_S = 720.0
OT_PERIOD_S = 300.0
TIMEOUTS_PER_TEAM = 7  # post-2017 rules

# Foul subTypes that do NOT count toward the period team-foul (bonus) total:
# offensive fouls/charges, technicals, double fouls. Substring match against
# the V3 subType — approximate; see HANDOFF for known edge cases.
NON_TEAM_FOUL_SUBTYPES = ("Offensive", "Technical", "Double")

# Only these actionTypes are trusted for scoreHome/scoreAway. Administrative
# rows (Instant Replay, period end/start) sometimes carry a stale or reverted
# score value — e.g. a shot is made, then a replay-review row echoes the
# PRE-shot score — and must never override the true running total.
SCORING_ACTION_TYPES = ("Made Shot", "Free Throw")

_CLOCK_RE = re.compile(r"^PT(\d+)M(\d+(?:\.\d+)?)S$")
_FT_TRIP_RE = re.compile(r"(\d+) of (\d+)")

STATE_COLUMNS = list(GameState.model_fields)


def parse_clock(clock: str) -> float:
    """Parse a V3/live ISO-8601 duration clock ('PT11M23.00S') → seconds remaining."""
    match = _CLOCK_RE.match(str(clock).strip())
    if not match:
        raise ParseError(f"Unparseable clock string: {clock!r}")
    return int(match.group(1)) * 60 + float(match.group(2))


def seconds_remaining_total(period: int, seconds_remaining_period: float) -> float:
    """True seconds remaining in the game.

    Regulation: time left in this period plus 720s per remaining period.
    Overtime: future OTs are unknowable, so this is just the current OT clock
    (Phase 2 decides normalization).
    """
    if period <= REGULATION_PERIODS:
        return seconds_remaining_period + (REGULATION_PERIODS - period) * REGULATION_PERIOD_S
    return seconds_remaining_period


def _side_of_team(team_id, home_id: int, away_id: int) -> int:
    if team_id is None or pd.isna(team_id):
        return 0
    team_id = int(team_id)
    if team_id == home_id:
        return 1
    if team_id == away_id:
        return -1
    return 0


def _event_side(row: pd.Series, home_id: int, away_id: int) -> int:
    """Which side acted: 1 = home, -1 = away, 0 = unknown/neutral.

    Prefers teamId; team-level events (team rebounds) carry the team id in
    personId; timeouts carry teamId=0 but set 'location' to 'h'/'v'.
    """
    side = _side_of_team(row.get("teamId"), home_id, away_id)
    if side:
        return side
    side = _side_of_team(row.get("personId"), home_id, away_id)
    if side:
        return side
    location = str(row.get("location") or "").strip().lower()
    if location == "h":
        return 1
    if location == "v":
        return -1
    return 0


def _parse_score_value(value) -> int | None:
    """A scoreHome/scoreAway cell: int-like string, or empty/None when absent."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ParseError(f"Unparseable score value: {value!r}") from exc


def parse_game(
    game_id: str,
    *,
    season: str | None = None,
    data_dir: Path | None = None,
    pbp: pd.DataFrame | None = None,
    record: GameRecord | None = None,
) -> pd.DataFrame:
    """Parse one game's raw PlayByPlayV3 events into GameState rows + label.

    Each output row is the game state AFTER the event resolves (scores, fouls,
    timeouts and possession reflect the event's outcome).

    Possession inference rules (the historical feed has no possession column):
    - Made Shot: flips to the other team.
    - Missed Shot: stays with the shooting team, pending the rebound.
    - Free Throw: stays with the shooting team; a MADE final attempt of the
      trip ('X of Y' with X == Y, shotResult == 'Made') flips it; a missed
      final attempt is pending the rebound; technical FTs change nothing.
    - Rebound: possession to the rebounding team (offensive rebound is a
      no-op because the shooter's team already held possession). Team rebounds
      carry the team id in personId.
    - Turnover: flips to the other team.
    - Jump Ball: the tip recipient is only in the description text, so 0
      (unknown); the next attributable event resolves it.
    - period start/end: unknown → 0 (the possession arrow is not tracked).
    - Fouls, substitutions, violations, timeouts, steal/block companion rows
      (empty actionType): unchanged.
    - Anything ambiguous → 0.

    Team fouls: 'Foul' events count toward the period team-foul total unless
    the subType contains 'Offensive', 'Technical' or 'Double'
    (NON_TEAM_FOUL_SUBTYPES); totals reset each period. Bonus semantics:
    ``home_in_bonus`` means the HOME team shoots free throws on the next
    common foul, i.e. the AWAY team has ≥5 team fouls this period (note: the
    last-2-minutes bonus rule is not modeled — deferred, see HANDOFF).

    Timeouts: 'Timeout' events decrement from 7 (post-2017 rules), attributed
    via the 'location' column ('h'/'v'); unattributable timeouts (official/TV)
    are ignored; if a team's count would go below zero, tracking is
    inconsistent and becomes None from then on.

    scoreHome/scoreAway are only populated on scoring events → forward-filled,
    initialized 0-0. The final parsed score is verified against ``record``;
    a mismatch raises ParseError.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()

    if pbp is None:
        if season is None:
            raise ValueError("season is required when pbp is not provided")
        path = pbp_path(data_dir, season, game_id)
        if not path.exists():
            raise ParseError(f"No harvested play-by-play at {path}")
        pbp = pd.read_parquet(path)

    if record is None:
        record = _lookup_record(game_id, season, data_dir)

    home_id, away_id = record.home_team_id, record.away_team_id
    # actionNumber order is NOT chronological in real V3 feeds (substitutions
    # and amendments get logged late with earlier clocks) → order by game
    # time, tiebreak same-clock events by actionNumber to keep pair ordering
    # (e.g. steal companion row before its turnover).
    events = pbp.copy()
    events["_clock_s"] = events["clock"].map(parse_clock)
    events = events.sort_values(
        ["period", "_clock_s", "actionNumber"], ascending=[True, False, True]
    ).reset_index(drop=True)

    away_score = home_score = 0
    possession = 0
    pending_rebound_side = 0  # side that shot the ball, if a rebound is pending
    current_period = 0
    home_fouls = away_fouls = 0
    home_timeouts: int | None = TIMEOUTS_PER_TEAM
    away_timeouts: int | None = TIMEOUTS_PER_TEAM

    rows: list[dict] = []
    for _, row in events.iterrows():
        action_type = str(row.get("actionType") or "").strip()
        sub_type = str(row.get("subType") or "").strip()
        period = int(row["period"])
        secs_period = float(row["_clock_s"])
        side = _event_side(row, home_id, away_id)

        if period != current_period:  # new period: team fouls reset
            current_period = period
            home_fouls = away_fouls = 0

        if action_type in SCORING_ACTION_TYPES:
            new_home = _parse_score_value(row.get("scoreHome"))
            new_away = _parse_score_value(row.get("scoreAway"))
            if new_home is not None and new_away is not None:
                # Basketball scores never decrease; a handful of real games
                # carry a corrupted score on an otherwise-legitimate scoring
                # row (seen on end-of-game free throws). Ignore any update
                # that would decrease either team's total rather than trust
                # a single glitched value over the whole game's trajectory.
                if new_home >= home_score and new_away >= away_score:
                    home_score, away_score = new_home, new_away
                else:
                    logger.warning(
                        "Game %s: ignoring non-monotonic score %s-%s at event "
                        "%s (running total was %s-%s)",
                        game_id, new_home, new_away, row["actionNumber"],
                        home_score, away_score,
                    )

        if action_type == "Made Shot":
            possession = -side if side else 0
            pending_rebound_side = 0
        elif action_type == "Missed Shot":
            possession = side
            pending_rebound_side = side
        elif action_type == "Free Throw":
            trip = _FT_TRIP_RE.search(sub_type)
            is_final = bool(trip) and trip.group(1) == trip.group(2)
            missed = (
                str(row.get("shotResult") or "").strip() == "Missed"
                or "MISS" in str(row.get("description") or "")
            )
            if is_final and side:
                if missed:
                    possession = side
                    pending_rebound_side = side
                else:
                    possession = -side
                    pending_rebound_side = 0
            elif side and trip:
                possession = side
        elif action_type == "Rebound":
            possession = side
            pending_rebound_side = 0
        elif action_type == "Turnover":
            possession = -side if side else 0
            pending_rebound_side = 0
        elif action_type == "Jump Ball":
            possession = 0
        elif action_type == "period":
            possession = 0
            pending_rebound_side = 0
        elif action_type == "Foul":
            if not any(tag in sub_type for tag in NON_TEAM_FOUL_SUBTYPES):
                if side == 1:
                    home_fouls += 1
                elif side == -1:
                    away_fouls += 1
        elif action_type == "Timeout":
            if side == 1:
                home_timeouts = None if home_timeouts in (None, 0) else home_timeouts - 1
            elif side == -1:
                away_timeouts = None if away_timeouts in (None, 0) else away_timeouts - 1
        else:
            pass  # substitutions, violations, steal/block companion rows: no change

        rows.append(
            {
                "game_id": str(game_id),
                "period": period,
                "seconds_remaining_period": secs_period,
                "seconds_remaining_total": seconds_remaining_total(period, secs_period),
                "home_score": home_score,
                "away_score": away_score,
                "score_diff": home_score - away_score,
                "home_team_fouls_period": home_fouls,
                "away_team_fouls_period": away_fouls,
                "home_in_bonus": away_fouls >= 5,
                "away_in_bonus": home_fouls >= 5,
                "possession": possession,
                "home_timeouts_remaining": home_timeouts,
                "away_timeouts_remaining": away_timeouts,
                "event_num": int(row["actionNumber"]),
            }
        )

    out = pd.DataFrame(rows, columns=STATE_COLUMNS)

    parsed_final = (
        (int(out.iloc[-1]["home_score"]), int(out.iloc[-1]["away_score"]))
        if len(out)
        else (0, 0)
    )
    expected = (record.final_home_score, record.final_away_score)
    if parsed_final != expected:
        raise ParseError(
            f"Game {game_id}: parsed final {parsed_final} does not match "
            f"record {expected}"
        )

    out["home_win"] = record.home_win
    out["home_timeouts_remaining"] = out["home_timeouts_remaining"].astype("Int64")
    out["away_timeouts_remaining"] = out["away_timeouts_remaining"].astype("Int64")
    return out


def _lookup_record(game_id: str, season: str | None, data_dir: Path) -> GameRecord:
    """Load this game's GameRecord from the season index parquet."""
    if season is None:
        raise ValueError("season is required to look up the game record")
    index_path = data_dir / "raw" / f"game_index_{season}.parquet"
    if not index_path.exists():
        raise ParseError(f"No game index at {index_path}")
    index = pd.read_parquet(index_path)
    match = index[index["game_id"] == game_id]
    if len(match) != 1:
        raise ParseError(f"Game {game_id} not found in {index_path}")
    return GameRecord(**match.iloc[0].to_dict())


def states_path(data_dir: Path, season: str, game_id: str) -> Path:
    return data_dir / "raw" / "states" / season / f"{game_id}.parquet"


@dataclass
class ParseSummary:
    """Outcome of one parse_season run."""

    parsed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def parse_season(
    season: str,
    *,
    data_dir: Path | None = None,
    game_ids: list[str] | None = None,
    progress: bool = True,
) -> ParseSummary:
    """Parse every harvested game of a season into GameState parquet files.

    Resumable (already-parsed games are skipped). A malformed game never kills
    the batch: the error is logged and recorded in
    ``data/raw/parse_failures.json`` (merged across runs), then the run
    continues.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()

    index_path = data_dir / "raw" / f"game_index_{season}.parquet"
    index = pd.read_parquet(index_path)
    records = {
        str(row["game_id"]): GameRecord(**row.to_dict()) for _, row in index.iterrows()
    }
    if game_ids is None:
        pbp_dir = data_dir / "raw" / "pbp" / season
        game_ids = sorted(p.stem for p in pbp_dir.glob("*.parquet"))

    summary = ParseSummary()
    failures: dict[str, str] = {}

    iterator = game_ids
    if progress:
        from tqdm import tqdm

        iterator = tqdm(game_ids, desc=f"parse {season}", unit="game")

    for game_id in iterator:
        out = states_path(data_dir, season, game_id)
        if out.exists():
            summary.skipped.append(game_id)
            continue
        try:
            record = records.get(game_id)
            if record is None:
                raise ParseError(f"Game {game_id} not in game index for {season}")
            states = parse_game(game_id, season=season, data_dir=data_dir, record=record)
            out.parent.mkdir(parents=True, exist_ok=True)
            states.to_parquet(out, index=False)
            summary.parsed.append(game_id)
        except Exception as exc:  # noqa: BLE001 — batch must survive bad games
            logger.error("Parse failed for game %s: %s", game_id, exc)
            failures[game_id] = f"{type(exc).__name__}: {exc}"
            summary.failed.append(game_id)

    if failures:
        failures_path = data_dir / "raw" / "parse_failures.json"
        existing = (
            json.loads(failures_path.read_text()) if failures_path.exists() else {}
        )
        existing.update(failures)
        failures_path.write_text(json.dumps(existing, indent=2))
        logger.warning("%d parse failures recorded in %s", len(failures), failures_path)

    logger.info(
        "Parse %s complete: %d parsed, %d skipped, %d failed",
        season, len(summary.parsed), len(summary.skipped), len(summary.failed),
    )
    return summary


# ---------------------------------------------------------------------------
# Checkpoint 1.5 — validation summary + CLI
# ---------------------------------------------------------------------------

def validate(*, data_dir: Path | None = None) -> dict:
    """Scan all parsed games and summarize data quality.

    Reports: games parsed, % of games with possession inferred on >70% of
    events, and % of games whose final score_diff sign disagrees with the
    home_win label (must be 0 — a mismatch means the parser or index is wrong).
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    states_root = data_dir / "raw" / "states"

    games = 0
    possession_ok = 0
    mismatched: list[str] = []
    for path in sorted(states_root.glob("*/*.parquet")):
        states = pd.read_parquet(path)
        if states.empty:
            mismatched.append(path.stem)
            continue
        games += 1
        if (states["possession"] != 0).mean() > 0.70:
            possession_ok += 1
        final = states.iloc[-1]
        if (final["score_diff"] > 0) != bool(final["home_win"]):
            mismatched.append(path.stem)

    return {
        "games_parsed": games,
        "pct_games_possession_over_70": 100.0 * possession_ok / games if games else 0.0,
        "pct_label_mismatches": 100.0 * len(mismatched) / games if games else 0.0,
        "mismatched_games": mismatched,
    }


def main(argv: list[str] | None = None) -> None:
    """CLI: python -m wp_engine.collect {index,harvest,parse,validate,all} ..."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m wp_engine.collect")
    parser.add_argument("command", choices=["index", "harvest", "parse", "validate", "all"])
    parser.add_argument("--season", default="2023-24", help='e.g. "2023-24"')
    parser.add_argument("--data-dir", default=None, help="override data root")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()

    if args.command in ("index", "all"):
        index = build_game_index(args.season, data_dir=data_dir)
        print(f"game index {args.season}: {len(index)} games")
    if args.command in ("harvest", "all"):
        summary = harvest_pbp(args.season, data_dir=data_dir)
        print(
            f"harvest {args.season}: {len(summary.downloaded)} downloaded, "
            f"{len(summary.skipped)} skipped, {len(summary.failed)} failed"
        )
    if args.command in ("parse", "all"):
        summary = parse_season(args.season, data_dir=data_dir)
        print(
            f"parse {args.season}: {len(summary.parsed)} parsed, "
            f"{len(summary.skipped)} skipped, {len(summary.failed)} failed"
        )
    if args.command in ("validate", "all"):
        report = validate(data_dir=data_dir)
        print(f"games parsed: {report['games_parsed']}")
        print(
            f"possession inferred on >70% of events: "
            f"{report['pct_games_possession_over_70']:.1f}% of games"
        )
        print(f"label mismatches: {report['pct_label_mismatches']:.1f}% (must be 0)")
        if report["mismatched_games"]:
            print(f"mismatched games: {report['mismatched_games']}")


if __name__ == "__main__":
    main()
