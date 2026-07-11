"""Phase 4 — GameHub: in-memory per-game pub/sub with a bounded history.

In-memory is deliberate: one process serves the whole product. If this ever
needs to scale out, swap the internals for Redis pub/sub + a capped list —
the publish/subscribe/history API stays identical.
"""

import asyncio
from collections import defaultdict

from wp_engine.schemas import WinProbUpdate


class GameHub:
    """History buffer + fan-out queues, keyed by game_id."""

    def __init__(self, history_limit: int = 5000) -> None:
        self._history_limit = history_limit
        self._history: dict[str, list[WinProbUpdate]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._meta: dict[str, dict] = {}

    def publish(self, update: WinProbUpdate) -> None:
        history = self._history[update.game_id]
        history.append(update)
        if len(history) > self._history_limit:
            del history[: len(history) - self._history_limit]
        for queue in list(self._subscribers[update.game_id]):
            try:
                queue.put_nowait(update)
            except asyncio.QueueFull:  # slow consumer: drop the tick, not the app
                pass

    def publish_event(self, game_id: str, event: dict) -> None:
        """Push a control frame (replay_finished, feed_degraded…) to
        subscribers without recording it in the history."""
        for queue in list(self._subscribers[game_id]):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def history(self, game_id: str) -> list[WinProbUpdate]:
        return list(self._history.get(game_id, []))

    def subscribe(self, game_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[game_id].add(queue)
        return queue

    def unsubscribe(self, game_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[game_id].discard(queue)

    def set_meta(self, game_id: str, **meta) -> None:
        """Attach display metadata (tricodes, status, is_replay…) to a game."""
        self._meta.setdefault(game_id, {}).update(meta)

    def games(self) -> list[dict]:
        """Summary of every known game: meta + latest tick if any."""
        summaries = []
        for game_id in sorted(set(self._history) | set(self._meta)):
            latest = self._history[game_id][-1] if self._history.get(game_id) else None
            summaries.append(
                {
                    "game_id": game_id,
                    **self._meta.get(game_id, {}),
                    "wp_home": latest.wp_home if latest else None,
                    "clock": latest.clock if latest else None,
                    "home_score": latest.home_score if latest else None,
                    "away_score": latest.away_score if latest else None,
                }
            )
        return summaries
