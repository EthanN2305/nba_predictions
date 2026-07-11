"""Checkpoints 4.3 + 4.4 — FastAPI app, WebSocket contract, replay mode.

The WS update-stream test drives a real replay through the app's own
pipeline (poller-equivalent path) instead of poking the hub from another
thread — exactly how Phase 5 will consume the service.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import create_app

GAME_ID = "0022300061"  # DEN 119-107 LAL (home win) — committed fixture


@pytest.fixture(scope="module")
def client(tiny_models_dir, fixture_data_dir):
    app = create_app(models_dir=tiny_models_dir, data_dir=fixture_data_dir)
    with TestClient(app) as test_client:
        yield test_client


class TestRest:
    def test_healthz(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_games_empty_initially(self, client):
        assert client.get("/games").json() == []

    def test_history_of_unknown_game_is_empty_list(self, client):
        assert client.get("/games/nope/history").json() == []


class TestReplayEndToEnd:
    def test_replay_streams_full_game_over_websocket(self, client):
        with client.websocket_connect(f"/ws/games/{GAME_ID}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            n_snapshot = len(snapshot["updates"])

            started = client.post(f"/replay/{GAME_ID}", params={"speed": 0})
            assert started.status_code == 202

            first = ws.receive_json()
            assert first["type"] == "update"
            assert first["game_id"] == GAME_ID
            assert first["is_replay"] is True
            assert 0 < first["wp_home"] < 1
            assert first["clock"].startswith("Q1")

            last = first
            while True:
                frame = ws.receive_json()
                if frame.get("type") == "replay_finished":
                    break
                last = frame
            # DEN won at home: final tick must be near-certain home win
            assert last["home_score"] == 119 and last["away_score"] == 107
            assert last["wp_home"] > 0.9
            assert n_snapshot == 0

    def test_history_backfill_after_replay(self, client):
        history = client.get(f"/games/{GAME_ID}/history").json()
        assert len(history) > 300  # every event of the game
        assert history[-1]["wp_home"] > 0.9

    def test_games_lists_replayed_game_with_meta(self, client):
        games = client.get("/games").json()
        game = next(g for g in games if g["game_id"] == GAME_ID)
        assert game["is_replay"] is True
        assert game["home_score"] == 119

    def test_replay_conflict_for_unknown_game(self, client):
        assert client.post("/replay/notagame", params={"speed": 0}).status_code == 404
