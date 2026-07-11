"""Checkpoint 6.2 — weekly drift monitor.

Replays recently completed games through the pipeline and compares the
realized Brier score against the training-time benchmark recorded in
feature_meta.json. >20% relative degradation → retraining recommendation.

Usage:
    python -m wp_engine.monitor --date 2026-04-10            # via live CDN
    python -m wp_engine.monitor --date 2026-04-10 --stats    # via stats API

The default path exercises the LIVE adapter (cdn.nba.com liveData →
LiveGameAdapter), which doubles as a live-vs-historical parser agreement
check. ``--stats`` falls back to the historical PlayByPlayV3 parser when the
CDN is unreachable (it is Akamai-blocked from some networks — see HANDOFF).
"""

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from wp_engine.collect import default_data_dir, parse_game
from wp_engine.features import FEATURE_COLUMNS, build_offline, build_pregame_context
from wp_engine.train import load_predictor

DEGRADATION_THRESHOLD = 0.20  # relative Brier increase that triggers a warning
DEFAULT_BENCHMARK = 0.1565  # Phase 3 held-out test Brier (fallback)


def recommendation(*, live_brier: float, benchmark: float) -> str:
    """One-line verdict comparing realized vs benchmark Brier."""
    relative = (live_brier - benchmark) / benchmark
    if relative > DEGRADATION_THRESHOLD:
        return (
            f"RETRAIN RECOMMENDED: realized Brier {live_brier:.4f} is "
            f"{relative:+.0%} vs benchmark {benchmark:.4f} "
            f"(threshold +{DEGRADATION_THRESHOLD:.0%}). "
            "Run `python -m wp_engine.train all` on refreshed data."
        )
    return (
        f"OK: realized Brier {live_brier:.4f} vs benchmark {benchmark:.4f} "
        f"({relative:+.0%})"
    )


def evaluate_states(
    states_by_game: dict[str, pd.DataFrame],
    predictor,
    *,
    game_index: pd.DataFrame,
) -> dict:
    """Realized Brier over full games of GameState rows (label = home_win)."""
    per_game: dict[str, float] = {}
    squared_errors: list[np.ndarray] = []
    for game_id, states in states_by_game.items():
        features = build_offline(states, build_pregame_context(game_index, game_id))
        wp = predictor(features[list(FEATURE_COLUMNS)])
        outcome = float(states["home_win"].iloc[0])
        errors = (wp - outcome) ** 2
        per_game[game_id] = float(errors.mean())
        squared_errors.append(errors)
    all_errors = np.concatenate(squared_errors)
    return {
        "per_game": per_game,
        "brier": float(all_errors.mean()),
        "n_games": len(per_game),
        "n_events": int(all_errors.size),
    }


def _benchmark_from_meta(models_dir: Path) -> float:
    meta_path = models_dir / "feature_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        try:
            return float(meta["model"]["metrics"]["test"]["brier"])
        except (KeyError, TypeError, ValueError):
            pass
    return DEFAULT_BENCHMARK


def _final_games_for_date(target: date) -> list[dict]:
    """Completed games for a date via the stats scoreboard (works even where
    the live CDN is blocked)."""
    from nba_api.stats.endpoints import scoreboardv2

    board = scoreboardv2.ScoreboardV2(game_date=target.strftime("%m/%d/%Y"))
    header = board.game_header.get_data_frame()
    final = header[header["GAME_STATUS_ID"] == 3]
    return [
        {
            "game_id": row["GAME_ID"],
            "home_team_id": int(row["HOME_TEAM_ID"]),
            "away_team_id": int(row["VISITOR_TEAM_ID"]),
        }
        for _, row in final.iterrows()
    ]


async def _states_via_live_adapter(game: dict) -> pd.DataFrame:
    """Fetch liveData actions and convert through the LIVE adapter path."""
    import httpx

    from wp_engine.live import LiveGameAdapter, fetch_live_pbp

    async with httpx.AsyncClient() as client:
        actions = await fetch_live_pbp(game["game_id"], client)
    adapter = LiveGameAdapter(
        game_id=game["game_id"],
        home_team_id=game["home_team_id"],
        away_team_id=game["away_team_id"],
    )
    states = adapter.process(actions)
    frame = pd.DataFrame([s.model_dump() for s in states])
    final = states[-1]
    frame["home_win"] = final.home_score > final.away_score
    return frame


def _states_via_stats(game_id: str, season: str) -> pd.DataFrame:
    """Historical parser fallback (stats.nba.com)."""
    return parse_game(game_id, season=season)


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m wp_engine.monitor``."""
    parser = argparse.ArgumentParser(prog="python -m wp_engine.monitor")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=date.today() - timedelta(days=1)
    )
    parser.add_argument("--season", default=None, help="needed for --stats path")
    parser.add_argument(
        "--stats", action="store_true",
        help="use the historical stats parser instead of the live adapter",
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    data_dir = args.data_dir if args.data_dir is not None else default_data_dir()

    games = _final_games_for_date(args.date)
    print(f"{args.date}: {len(games)} completed games")
    if not games:
        return

    states_by_game: dict[str, pd.DataFrame] = {}
    for game in games:
        try:
            if args.stats:
                states_by_game[game["game_id"]] = _states_via_stats(
                    game["game_id"], args.season
                )
            else:
                states_by_game[game["game_id"]] = asyncio.run(
                    _states_via_live_adapter(game)
                )
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  {game['game_id']}: FAILED ({exc})")

    if not states_by_game:
        print("no games could be fetched — nothing to score")
        return

    # pregame context needs the season index; use a neutral one if missing
    season = args.season or "2023-24"
    index_path = data_dir / "raw" / f"game_index_{season}.parquet"
    game_index = pd.read_parquet(index_path)

    models_dir = data_dir / "models"
    report = evaluate_states(
        states_by_game, load_predictor(models_dir=models_dir), game_index=game_index
    )
    for game_id, brier in sorted(report["per_game"].items()):
        print(f"  {game_id}: Brier {brier:.4f}")
    benchmark = _benchmark_from_meta(models_dir)
    print(
        f"overall: Brier {report['brier']:.4f} over {report['n_events']} events "
        f"in {report['n_games']} games"
    )
    print(recommendation(live_brier=report["brier"], benchmark=benchmark))


if __name__ == "__main__":
    main()
