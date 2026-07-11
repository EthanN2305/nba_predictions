"""Live CDN fetchers against a mocked httpx transport — payload parsing,
HTTP-error propagation, and the global request-concurrency cap (6.3)."""

import asyncio
import json

import httpx
import pytest

from wp_engine.live import fetch_live_pbp, fetch_scoreboard


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestFetchers:
    def test_fetch_live_pbp_returns_action_list(self):
        async def scenario():
            def handler(request: httpx.Request) -> httpx.Response:
                assert "playbyplay_0022300061" in str(request.url)
                return httpx.Response(
                    200, json={"game": {"actions": [{"actionNumber": 1}]}}
                )

            async with mock_client(handler) as client:
                return await fetch_live_pbp("0022300061", client)

        assert asyncio.run(scenario()) == [{"actionNumber": 1}]

    def test_fetch_scoreboard_returns_games(self):
        async def scenario():
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200, json={"scoreboard": {"games": [{"gameId": "X"}]}}
                )

            async with mock_client(handler) as client:
                return await fetch_scoreboard(client)

        assert asyncio.run(scenario()) == [{"gameId": "X"}]

    def test_http_error_raises(self):
        async def scenario():
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(403, text="Access Denied")

            async with mock_client(handler) as client:
                await fetch_live_pbp("0022300061", client)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(scenario())


class TestRequestSemaphore:
    def test_concurrent_requests_capped_at_settings_limit(self):
        depth = {"now": 0, "max": 0}

        async def scenario():
            async def handler(request: httpx.Request) -> httpx.Response:
                depth["now"] += 1
                depth["max"] = max(depth["max"], depth["now"])
                await asyncio.sleep(0.01)
                depth["now"] -= 1
                return httpx.Response(
                    200, json={"game": {"actions": []}}
                )

            async with mock_client(handler) as client:
                await asyncio.gather(
                    *(fetch_live_pbp(f"00223{i:05d}", client) for i in range(8))
                )

        asyncio.run(scenario())
        assert depth["max"] <= 3  # default WP_MAX_CONCURRENT_NBA_REQUESTS
