"""Checkpoint 6.3 — env-driven Settings, JSON logging, graceful shutdown."""

import asyncio
import json
import logging

from wp_engine.config import Settings, json_log_record, setup_logging
from wp_engine.hub import GameHub


class TestSettings:
    def test_defaults(self):
        settings = Settings()
        assert settings.cors_origins == "http://localhost:5173"
        assert settings.enable_live is False
        assert settings.poll_interval == 3.0
        assert settings.max_concurrent_nba_requests == 3
        assert settings.origins == ["http://localhost:5173"]

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("WP_CORS_ORIGINS", "https://a.example, https://b.example")
        monkeypatch.setenv("WP_ENABLE_LIVE", "1")
        monkeypatch.setenv("WP_POLL_INTERVAL", "5.5")
        settings = Settings()
        assert settings.origins == ["https://a.example", "https://b.example"]
        assert settings.enable_live is True
        assert settings.poll_interval == 5.5


class TestJsonLogging:
    def test_records_render_as_json_with_context(self):
        setup_logging()
        record = logging.LogRecord(
            name="wp_engine.poller", level=logging.INFO, pathname="", lineno=0,
            msg="tick %s", args=("ok",), exc_info=None,
        )
        record.game_id = "0022300061"
        payload = json.loads(json_log_record(record))
        assert payload["message"] == "tick ok"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "wp_engine.poller"
        assert payload["game_id"] == "0022300061"
        assert "ts" in payload


class TestGracefulShutdown:
    def test_server_closing_frame_reaches_all_subscribers(self):
        async def scenario():
            hub = GameHub()
            hub.set_meta("g1", status="live")
            queue_one = hub.subscribe("g1")
            queue_two = hub.subscribe("g2")
            hub.notify_shutdown()
            assert (await queue_one.get())["type"] == "server_closing"
            assert (await queue_two.get())["type"] == "server_closing"

        asyncio.run(scenario())
