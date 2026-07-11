"""Checkpoint 6.1 — end-to-end replay regression suite.

Five-plus diverse REAL games (wire-to-wire, close loss, OT, blowout,
low-scoring, 30-point comeback) replayed through the full
adapter→features→model→hub→WebSocket pipeline against the PINNED fixture
model (tests/fixtures/models — committed, deterministic). Asserts the
product-level invariants that individual unit tests can't see.
"""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from wp_engine.collect import states_path
from wp_engine.features import game_elapsed_seconds
from wp_engine.schemas import WinProbUpdate

from tests.conftest import FIXTURE_GAMES, FIXTURE_SEASON

PINNED_MODELS = Path(__file__).parent.parent / "fixtures" / "models"
SMOOTHNESS_CAP_PP = 25.0
FINAL_TWO_MINUTES = 120.0


@pytest.fixture(scope="module")
def client(fixture_data_dir):
    app = create_app(models_dir=PINNED_MODELS, data_dir=fixture_data_dir)
    with TestClient(app) as test_client:
        yield test_client


def replay_over_ws(client, game_id: str) -> list[WinProbUpdate]:
    """Drive one replay through the API and validate every frame."""
    updates: list[WinProbUpdate] = []
    with client.websocket_connect(f"/ws/games/{game_id}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert client.post(f"/replay/{game_id}", params={"speed": 0}).status_code == 202
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "replay_finished":
                break
            assert frame["type"] == "update"
            frame.pop("type")
            updates.append(WinProbUpdate(**frame))  # schema validation per frame
    return updates


@pytest.mark.parametrize("game_id", FIXTURE_GAMES)
def test_replay_pipeline_invariants(client, fixture_data_dir, game_id):
    states = pd.read_parquet(states_path(fixture_data_dir, FIXTURE_SEASON, game_id))
    home_won = bool(states["home_win"].iloc[0])

    updates = replay_over_ws(client, game_id)

    # one tick per parsed event, post-dedup
    assert len(updates) == len(states)

    # the ending must be called correctly and emphatically
    final = updates[-1]
    if home_won:
        assert final.wp_home > 0.95, f"{game_id}: home won but final wp {final.wp_home}"
    else:
        assert final.wp_home < 0.05, f"{game_id}: home lost but final wp {final.wp_home}"

    # smoothness guard: no single event may swing WP > 25 pp outside the
    # final two minutes of the game
    elapsed = [
        game_elapsed_seconds(u.period, _clock_seconds(u.clock)) for u in updates
    ]
    total = max(elapsed)
    for i in range(1, len(updates)):
        if elapsed[i] < total - FINAL_TWO_MINUTES:
            jump = abs(updates[i].wp_home - updates[i - 1].wp_home)
            assert jump <= SMOOTHNESS_CAP_PP / 100, (
                f"{game_id}: {jump:.1%} jump at event {updates[i].event_num} "
                f"({updates[i].clock}) — {updates[i].description}"
            )


def _clock_seconds(clock: str) -> float:
    minutes, seconds = clock.split(" ")[1].split(":")
    return int(minutes) * 60 + int(seconds)
