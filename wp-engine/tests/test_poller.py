"""Checkpoint 4.2 — GamePoller dedup/amendment/backoff/fault tolerance and
the GameHub pub/sub, tested with injected fake fetchers (no network)."""

import asyncio

import pytest

from wp_engine.hub import GameHub
from wp_engine.inference import LivePredictor
from wp_engine.poller import GameDirectory, GamePoller

from tests.test_live_adapter import HOME, AWAY, act

GAME_ID = "0022300777"


def make_poller(tiny_models_dir, published: list, **kwargs) -> GamePoller:
    return GamePoller(
        game_id=GAME_ID,
        home_team_id=HOME,
        away_team_id=AWAY,
        live_predictor=LivePredictor(game_id=GAME_ID, models_dir=tiny_models_dir),
        publish=published.append,
        fetch=kwargs.pop("fetch", None),
        **kwargs,
    )


class TestPollerStep:
    def test_step_emits_one_update_per_new_event(self, tiny_models_dir):
        published: list = []
        poller = make_poller(tiny_models_dir, published)
        updates = poller.step(
            [act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")]
        )
        assert len(updates) == 1
        assert updates[0].home_score == 2

    def test_step_is_idempotent_on_cumulative_payloads(self, tiny_models_dir):
        poller = make_poller(tiny_models_dir, [])
        batch = [
            act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")
        ]
        assert len(poller.step(batch)) == 1
        assert poller.step(batch) == []  # same payload again → nothing new

    def test_amendment_does_not_reemit(self, tiny_models_dir):
        poller = make_poller(tiny_models_dir, [])
        poller.step(
            [act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")]
        )
        amended = poller.step(
            [act(1, "3pt", team=HOME, shot_result="Made", score_home="3", score_away="0")]
        )
        assert amended == []

    def test_interval_backs_off_when_no_new_events(self, tiny_models_dir):
        poller = make_poller(tiny_models_dir, [], poll_interval=3.0, idle_interval=30.0)
        batch = [
            act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")
        ]
        poller.step(batch)
        assert poller.interval == 3.0
        poller.step(batch)  # nothing new twice in a row
        poller.step(batch)
        assert poller.interval == 30.0

    def test_game_end_action_finishes_poller(self, tiny_models_dir):
        poller = make_poller(tiny_models_dir, [])
        poller.step(
            [
                act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0"),
                act(2, "game", clock="PT00M00.00S", period=4, sub="end"),
            ]
        )
        assert poller.finished is True


class TestPollerRunLoop:
    def test_run_publishes_and_stops_on_game_end(self, tiny_models_dir):
        published: list = []
        batches = [
            [act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0")],
            [
                act(1, "2pt", team=HOME, shot_result="Made", score_home="2", score_away="0"),
                act(2, "game", clock="PT00M00.00S", period=4, sub="end"),
            ],
        ]

        async def fetch():
            return batches.pop(0) if batches else []

        poller = make_poller(tiny_models_dir, published, fetch=fetch, poll_interval=0)
        asyncio.run(poller.run())
        assert poller.finished is True
        assert len(published) == 2  # the 2pt tick, then the game-end tick (dedup)

    def test_five_consecutive_failures_mark_degraded_but_never_crash(
        self, tiny_models_dir
    ):
        calls = {"n": 0}

        async def failing_fetch():
            calls["n"] += 1
            if calls["n"] <= 5:
                raise ConnectionError("boom")
            return [act(1, "game", clock="PT00M00.00S", period=4, sub="end")]

        degraded_flags: list = []
        poller = make_poller(
            tiny_models_dir,
            [],
            fetch=failing_fetch,
            poll_interval=0,
            on_degraded=lambda: degraded_flags.append(True),
        )
        asyncio.run(poller.run())
        assert degraded_flags == [True]
        assert poller.finished is True  # recovered and completed


class TestGameHub:
    def test_publish_appends_history_and_notifies_subscribers(self, tiny_models_dir):
        async def scenario():
            hub = GameHub()
            queue = hub.subscribe(GAME_ID)
            predictor = LivePredictor(game_id=GAME_ID, models_dir=tiny_models_dir)
            from tests.test_features import make_state

            update = predictor.on_state(make_state(event_num=1))
            hub.publish(update)
            assert hub.history(GAME_ID) == [update]
            assert await asyncio.wait_for(queue.get(), 1) == update
            hub.unsubscribe(GAME_ID, queue)
            hub.publish(update)
            assert queue.empty()

        asyncio.run(scenario())

    def test_history_is_bounded(self, tiny_models_dir):
        hub = GameHub(history_limit=3)
        predictor = LivePredictor(game_id=GAME_ID, models_dir=tiny_models_dir)
        from tests.test_features import make_state

        for i in range(5):
            hub.publish(predictor.on_state(make_state(event_num=i + 1)))
        history = hub.history(GAME_ID)
        assert len(history) == 3
        assert history[-1].event_num == 5


class TestGameDirectory:
    def test_starts_live_games_and_stops_finished_ones(self, tiny_models_dir):
        started: list[str] = []
        stopped: list[str] = []

        directory = GameDirectory(
            start_poller=lambda game: started.append(game["gameId"]),
            stop_poller=stopped.append,
        )
        scoreboard = [
            {"gameId": "A", "gameStatus": 1},  # scheduled
            {"gameId": "B", "gameStatus": 2},  # live
            {"gameId": "C", "gameStatus": 2},  # live
        ]
        directory.tick(scoreboard)
        assert started == ["B", "C"]
        directory.tick(
            [
                {"gameId": "B", "gameStatus": 3},  # finished
                {"gameId": "C", "gameStatus": 2},
            ]
        )
        assert stopped == ["B"]
        # already-tracked live game is not started twice
        assert started == ["B", "C"]
