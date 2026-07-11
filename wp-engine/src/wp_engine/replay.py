"""Phase 4 — replay mode: historical parsed games through the LIVE pipeline.

Live games aren't always on (and cdn.nba.com is blocked from some networks),
so replay is a first-class path: parsed GameStates are fed through the SAME
``LivePredictor`` (FeatureBuilder → predictor → WinProbUpdate) and published
to the hub exactly like live ticks, flagged ``is_replay=true``.

CLI:
    python -m wp_engine.replay --game-id 0022300061 --speed 60          # serve + replay
    python -m wp_engine.replay --game-id 0022300061 --print             # stdout, no server
"""

import argparse
import asyncio
from pathlib import Path

import pandas as pd

from wp_engine.collect import default_data_dir
from wp_engine.features import (
    build_pregame_context,
    game_elapsed_seconds,
    state_from_row,
)
from wp_engine.hub import GameHub
from wp_engine.inference import LivePredictor
from wp_engine.schemas import GameRecord

DEFAULT_SPEED = 60.0


def find_game(data_dir: Path, game_id: str) -> tuple[Path, GameRecord] | None:
    """Locate a parsed game across seasons; return (states path, record)."""
    for states_file in sorted(data_dir.glob(f"raw/states/*/{game_id}.parquet")):
        season = states_file.parent.name
        index = pd.read_parquet(data_dir / "raw" / f"game_index_{season}.parquet")
        row = index[index["game_id"] == game_id]
        if not row.empty:
            return states_file, GameRecord(**row.iloc[0].to_dict())
    return None


async def replay_game(
    *,
    game_id: str,
    hub: GameHub,
    predictor=None,
    models_dir: Path | None = None,
    data_dir: Path | None = None,
    speed: float = DEFAULT_SPEED,
) -> int:
    """Replay one game into the hub at ``speed``× game-clock time.

    ``speed <= 0`` means as-fast-as-possible (no sleeps). Returns the number
    of updates published.
    """
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    found = find_game(data_dir, game_id)
    if found is None:
        raise FileNotFoundError(f"no parsed states for game {game_id!r}")
    states_file, record = found

    season = states_file.parent.name
    index = pd.read_parquet(data_dir / "raw" / f"game_index_{season}.parquet")
    live_predictor = LivePredictor(
        game_id=game_id,
        predictor=predictor,
        models_dir=models_dir,
        pregame=build_pregame_context(index, game_id),
        is_replay=True,
    )
    hub.set_meta(
        game_id,
        home=record.home_team_abbr,
        away=record.away_team_abbr,
        is_replay=True,
        status="replaying",
    )

    states = pd.read_parquet(states_file)
    published = 0
    previous_elapsed: float | None = None
    for _, row in states.iterrows():
        state = state_from_row(row)
        elapsed = game_elapsed_seconds(state.period, state.seconds_remaining_period)
        if speed > 0 and previous_elapsed is not None and elapsed > previous_elapsed:
            await asyncio.sleep((elapsed - previous_elapsed) / speed)
        previous_elapsed = elapsed
        hub.publish(live_predictor.on_state(state))
        published += 1

    hub.set_meta(game_id, status="final")
    hub.publish_event(game_id, {"type": "replay_finished", "game_id": game_id})
    return published


async def _print_replay(game_id: str, data_dir: Path | None, speed: float) -> None:
    hub = GameHub()
    queue = hub.subscribe(game_id)
    task = asyncio.create_task(
        replay_game(game_id=game_id, hub=hub, data_dir=data_dir, speed=speed)
    )
    while True:
        item = await queue.get()
        if isinstance(item, dict):
            break
        print(item.model_dump_json())
    await task


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m wp_engine.replay``."""
    parser = argparse.ArgumentParser(prog="python -m wp_engine.replay")
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--print", action="store_true", dest="print_mode",
        help="print updates to stdout instead of serving the API",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.print_mode:
        asyncio.run(_print_replay(args.game_id, args.data_dir, args.speed))
        return

    # serve mode: start the API with a replay kicked off at startup
    import uvicorn

    from api.main import create_app

    app = create_app(data_dir=args.data_dir, replay_on_start=(args.game_id, args.speed))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
