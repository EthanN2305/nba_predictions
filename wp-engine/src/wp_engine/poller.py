"""Phase 4 — GamePoller (one per live game) and GameDirectory (scoreboard watcher).

Fault-tolerance contract: network errors log-and-continue; 5 consecutive
failures mark the feed degraded (clients are notified via ``on_degraded``);
the poller NEVER crashes the process and resumes cleanly when the feed
returns.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from wp_engine.inference import LivePredictor
from wp_engine.live import LiveGameAdapter
from wp_engine.schemas import WinProbUpdate

logger = logging.getLogger(__name__)

LIVE_STATUS = 2
FINAL_STATUS = 3


class GamePoller:
    """Poll one game's live feed, push WinProbUpdates through the pipeline."""

    def __init__(
        self,
        *,
        game_id: str,
        home_team_id: int,
        away_team_id: int,
        live_predictor: LivePredictor,
        publish: Callable[[WinProbUpdate], None],
        fetch: Callable[[], Awaitable[list[dict]]] | None = None,
        poll_interval: float = 3.0,
        idle_interval: float = 30.0,
        max_failures: int = 5,
        on_degraded: Callable[[], None] | None = None,
    ) -> None:
        self.game_id = game_id
        self._adapter = LiveGameAdapter(
            game_id=game_id, home_team_id=home_team_id, away_team_id=away_team_id
        )
        self._live_predictor = live_predictor
        self._publish = publish
        self._fetch = fetch
        self._poll_interval = poll_interval
        self._idle_interval = idle_interval
        self._max_failures = max_failures
        self._on_degraded = on_degraded

        self._emitted: set[int] = set()
        self._idle_polls = 0
        self._failures = 0
        self.degraded = False
        self.finished = False

    @property
    def interval(self) -> float:
        """3s during live play; back off to 30s once the feed goes quiet
        (halftime/timeout: two consecutive polls with nothing new)."""
        return self._idle_interval if self._idle_polls >= 2 else self._poll_interval

    def step(self, actions: list[dict]) -> list[WinProbUpdate]:
        """Synchronous pipeline core: actions → new states → updates.

        Only actionNumbers never emitted before produce updates — cumulative
        payloads, resends and amendments are all deduplicated here. (A rare
        late-arriving event with an OLD actionNumber is fed to the stateful
        feature builder out of order; features tolerate this and history is
        append-only by design.)
        """
        states = self._adapter.process(actions)
        updates: list[WinProbUpdate] = []
        for state in states:
            if state.event_num in self._emitted:
                continue
            self._emitted.add(state.event_num)
            raw = self._adapter._actions.get(state.event_num, {})
            updates.append(
                self._live_predictor.on_state(
                    state, description=raw.get("description") or None
                )
            )
            if (
                str(raw.get("actionType", "")).lower() == "game"
                and "end" in str(raw.get("subType", "")).lower()
            ):
                self.finished = True
        self._idle_polls = 0 if updates else self._idle_polls + 1
        return updates

    async def run(self) -> None:
        """Poll until the game ends. Never raises."""
        while not self.finished:
            try:
                actions = await self._fetch()
            except Exception as exc:  # noqa: BLE001 — fault tolerance by contract
                self._failures += 1
                logger.warning(
                    "%s: live fetch failed (%d consecutive): %s",
                    self.game_id, self._failures, exc,
                )
                if self._failures >= self._max_failures and not self.degraded:
                    self.degraded = True
                    if self._on_degraded is not None:
                        self._on_degraded()
                await asyncio.sleep(self.interval)
                continue
            if self._failures:
                logger.info("%s: live feed recovered", self.game_id)
            self._failures = 0
            self.degraded = False
            for update in self.step(actions):
                self._publish(update)
            if not self.finished:
                await asyncio.sleep(self.interval)


class GameDirectory:
    """Watch the scoreboard; start pollers for games going live, stop finished."""

    def __init__(
        self,
        *,
        start_poller: Callable[[dict], None],
        stop_poller: Callable[[str], None],
        interval: float = 60.0,
    ) -> None:
        self._start_poller = start_poller
        self._stop_poller = stop_poller
        self._interval = interval
        self._tracked: set[str] = set()

    def tick(self, scoreboard_games: list[dict]) -> None:
        """Reconcile tracked pollers against one scoreboard snapshot."""
        for game in scoreboard_games:
            game_id, status = game.get("gameId"), game.get("gameStatus")
            if status == LIVE_STATUS and game_id not in self._tracked:
                self._tracked.add(game_id)
                self._start_poller(game)
            elif status == FINAL_STATUS and game_id in self._tracked:
                self._tracked.discard(game_id)
                self._stop_poller(game_id)

    async def run(self, fetch_scoreboard: Callable[[], Awaitable[list[dict]]]) -> None:
        """Poll the scoreboard every ``interval`` seconds. Never raises."""
        while True:
            try:
                self.tick(await fetch_scoreboard())
            except Exception as exc:  # noqa: BLE001
                logger.warning("scoreboard fetch failed: %s", exc)
            await asyncio.sleep(self._interval)
