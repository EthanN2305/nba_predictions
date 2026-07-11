"""Checkpoint 6.3 — single Settings object (env-driven) + structured logging.

Every runtime knob lives here; no scattered ``os.environ`` reads. All env
vars use the ``WP_`` prefix (``WP_ENABLE_LIVE=1``, ``WP_CORS_ORIGINS=…``).
"""

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; construct fresh (tests) or use get_settings()."""

    model_config = SettingsConfigDict(env_prefix="WP_")

    data_dir: Path | None = None  # default: <repo>/data (collect.default_data_dir)
    models_dir: Path | None = None  # default: <data_dir>/models
    cors_origins: str = "http://localhost:5173"
    enable_live: bool = False
    poll_interval: float = 3.0
    idle_interval: float = 30.0
    scoreboard_interval: float = 60.0
    ws_ping_interval: float = 20.0
    ws_send_timeout: float = 5.0  # slow-client drop threshold (backpressure)
    max_concurrent_nba_requests: int = 3
    replay_game: str | None = None  # auto-replay this game at startup (demo)
    replay_speed: float = 60.0

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def json_log_record(record: logging.LogRecord) -> str:
    """One log record → one JSON line, extras (game_id…) included."""
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    for key, value in record.__dict__.items():
        if key not in _STANDARD_ATTRS and not key.startswith("_"):
            payload[key] = value
    if record.exc_info and record.exc_info[1] is not None:
        payload["error"] = repr(record.exc_info[1])
    return json.dumps(payload, default=str)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json_log_record(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Structured JSON logs on stdout for the wp_engine/api namespaces."""
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    # replace only our previous handler, not e.g. pytest's
    root.handlers = [
        h for h in root.handlers if not isinstance(h.formatter, _JsonFormatter)
    ]
    root.addHandler(handler)
    root.setLevel(level)
