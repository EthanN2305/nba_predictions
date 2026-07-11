"""Phase 4 — FastAPI service: REST + WebSocket win-probability streaming.

Run:  uvicorn api.main:app
Env:  WP_DATA_DIR       data root (default <repo>/data)
      WP_MODELS_DIR     model artifacts dir (default <data>/models)
      WP_CORS_ORIGINS   comma-separated origins (default http://localhost:5173)
      WP_ENABLE_LIVE=1  start the live GameDirectory (requires cdn.nba.com access)

WebSocket contract (/ws/games/{game_id}):
  on connect → {"type": "snapshot", "updates": [WinProbUpdate…]}
  then       → {"type": "update", …WinProbUpdate fields…} per event
  control    → {"type": "replay_finished"|"feed_degraded", "game_id": …}
  keepalive  → {"type": "ping"} every 20s of silence
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from wp_engine.hub import GameHub
from wp_engine.inference import LivePredictor
from wp_engine.live import fetch_live_pbp, fetch_scoreboard
from wp_engine.poller import GameDirectory, GamePoller
from wp_engine.replay import find_game, replay_game
from wp_engine.schemas import WinProbUpdate
from wp_engine.train import load_predictor

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 20.0


def _default_data_dir() -> Path:
    from wp_engine.collect import default_data_dir

    return default_data_dir()


def create_app(
    *,
    models_dir: Path | None = None,
    data_dir: Path | None = None,
    replay_on_start: tuple[str, float] | None = None,
) -> FastAPI:
    """App factory. Tests inject tiny artifacts; production uses defaults."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = GameHub()
        app.state.data_dir = Path(data_dir) if data_dir else _default_data_dir()
        app.state.predictor = load_predictor(
            models_dir=models_dir, data_dir=app.state.data_dir
        )
        app.state.tasks: set[asyncio.Task] = set()
        app.state.replaying: set[str] = set()

        def _spawn(coro) -> asyncio.Task:
            task = asyncio.create_task(coro)
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
            return task

        app.state.spawn = _spawn

        if os.environ.get("WP_ENABLE_LIVE") == "1":
            _spawn(_run_live_directory(app))
        if replay_on_start is not None:
            game_id, speed = replay_on_start
            _spawn(_run_replay(app, game_id, speed))

        yield
        for task in list(app.state.tasks):
            task.cancel()

    app = FastAPI(title="wp-engine", lifespan=lifespan)
    origins = os.environ.get("WP_CORS_ORIGINS", "http://localhost:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_routes(app)
    return app


async def _run_replay(app: FastAPI, game_id: str, speed: float) -> None:
    app.state.replaying.add(game_id)
    try:
        count = await replay_game(
            game_id=game_id,
            hub=app.state.hub,
            predictor=app.state.predictor,
            data_dir=app.state.data_dir,
            speed=speed,
        )
        logger.info("replay %s finished: %d updates", game_id, count)
    except Exception:  # noqa: BLE001 — a failed replay must not kill the app
        logger.exception("replay %s failed", game_id)
    finally:
        app.state.replaying.discard(game_id)


async def _run_live_directory(app: FastAPI) -> None:
    """Scoreboard-driven poller management (requires cdn.nba.com access)."""
    hub: GameHub = app.state.hub

    async with httpx.AsyncClient() as client:

        def start_poller(game: dict) -> None:
            game_id = game["gameId"]
            home, away = game.get("homeTeam", {}), game.get("awayTeam", {})
            hub.set_meta(
                game_id,
                home=home.get("teamTricode"),
                away=away.get("teamTricode"),
                is_replay=False,
                status="live",
            )
            poller = GamePoller(
                game_id=game_id,
                home_team_id=home.get("teamId"),
                away_team_id=away.get("teamId"),
                live_predictor=LivePredictor(
                    game_id=game_id, predictor=app.state.predictor
                ),
                publish=hub.publish,
                fetch=lambda: fetch_live_pbp(game_id, client),
                on_degraded=lambda: hub.publish_event(
                    game_id, {"type": "feed_degraded", "game_id": game_id}
                ),
            )
            app.state.spawn(poller.run())

        directory = GameDirectory(
            start_poller=start_poller,
            stop_poller=lambda game_id: hub.set_meta(game_id, status="final"),
        )
        await directory.run(lambda: fetch_scoreboard(client))


def _register_routes(app: FastAPI) -> None:
    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/games")
    async def games() -> list[dict]:
        return app.state.hub.games()

    @app.get("/games/{game_id}/history")
    async def history(game_id: str) -> list[dict]:
        return [u.model_dump(mode="json") for u in app.state.hub.history(game_id)]

    @app.post("/replay/{game_id}", status_code=202)
    async def start_replay(game_id: str, speed: float = 60.0):
        if find_game(app.state.data_dir, game_id) is None:
            return JSONResponse({"error": "unknown game"}, status_code=404)
        if game_id in app.state.replaying:
            return JSONResponse({"error": "replay already running"}, status_code=409)
        app.state.replaying.add(game_id)
        app.state.spawn(_run_replay(app, game_id, speed))
        return {"status": "replay started", "game_id": game_id, "speed": speed}

    @app.websocket("/ws/games/{game_id}")
    async def ws_game(websocket: WebSocket, game_id: str) -> None:
        await websocket.accept()
        hub: GameHub = websocket.app.state.hub
        queue = hub.subscribe(game_id)
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "updates": [
                        u.model_dump(mode="json") for u in hub.history(game_id)
                    ],
                }
            )
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=PING_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
                    continue
                if isinstance(item, WinProbUpdate):
                    await websocket.send_json(
                        {"type": "update", **item.model_dump(mode="json")}
                    )
                else:
                    await websocket.send_json(item)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(game_id, queue)


app = create_app()
