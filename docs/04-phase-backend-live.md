# Phase 4 — Live Backend: nba_api.live → FastAPI → WebSockets

> **Prompt to Claude:** You are building Phase 4 of the NBA Live Win-Probability Engine.
> `00-PROJECT-OVERVIEW.md` plus Phase 2 & 3 `HANDOFF.md` files are pasted above. You have
> `FeatureBuilder` (shared feature module) and `load_predictor()` (calibrated model).
> Build the production service that follows live games and pushes win probabilities
> to connected clients over WebSockets.

## Architecture for This Phase

```
nba_api.live poller (asyncio task, 1 per tracked game)
        │  raw live PBP events (new since last poll)
        ▼
LiveGameAdapter  ── converts live-endpoint payloads → canonical GameState
        ▼
FeatureBuilder.update(state)  ── same module as training
        ▼
predictor(features) → wp_home
        ▼
GameHub (in-memory pub/sub)  ── history buffer + broadcast
        ▼
FastAPI WebSocket /ws/games/{game_id}  → clients
```

## Checkpoint 4.1 — The Live Adapter (hardest part — do it first)

`nba_api.live.nba.endpoints` (`scoreboard.ScoreBoard`, `boxscore.BoxScore`,
`playbyplay.PlayByPlay`) return **different field names and structures than the historical
stats endpoints**. Build `src/wp_engine/live.py` with:

- `LiveGameAdapter.parse_events(payload) -> list[GameState]`:
  - Live PBP clock is ISO-8601 duration format (`"PT11M23.00S"`) — write and unit-test a parser.
  - Live events carry `scoreHome`/`scoreAway` directly (no fragile string splitting).
  - Map live `actionType`/`subType` strings to the possession/foul inference rules from
    Phase 1 (document the mapping table; it will differ from EVENTMSGTYPE codes).
  - Deduplicate by `actionNumber`; live feeds can resend or amend events (amendments share
    `actionNumber` — take the latest version).
- **First, verify the actual current payload shapes**: fetch a real live or recent game feed
  (or use `scoreboard` for today's games) and print/inspect the JSON before coding against
  assumptions. The live API is undocumented and shifts; code defensively with Pydantic
  models using `extra="ignore"` and explicit fallbacks.

## Checkpoint 4.2 — The Poller

`GamePoller` (one asyncio task per game):
- Poll live PBP every **3 seconds** during live play; back off to 30s at halftime/timeouts
  (detect via game status / clock not advancing); stop on final.
- Maintain `last_action_number`; process only new/amended events, in order.
- On each new event: adapter → `FeatureBuilder.update` → predictor → publish
  `WinProbUpdate` to the hub.
- Fault tolerance: network errors log-and-continue; 5 consecutive failures → mark game
  feed degraded and notify clients; never crash the process.
- `GameDirectory` task: poll `scoreboard` every 60s, auto-start pollers for games going
  live, auto-stop finished ones.

Message schema (add to `schemas.py`):
```python
class WinProbUpdate(BaseModel):
    game_id: str
    event_num: int
    period: int
    clock: str                 # display string "Q4 02:31"
    home_score: int
    away_score: int
    wp_home: float             # calibrated
    ts: datetime               # server time
    description: str | None    # e.g. "Curry 3PT (28 PTS)"
```

## Checkpoint 4.3 — FastAPI App

`src/api/main.py`:
- `GET /games` → today's games with status + current wp_home.
- `GET /games/{game_id}/history` → full `list[WinProbUpdate]` so far (chart backfill).
- `WS /ws/games/{game_id}` → on connect: send `{"type":"snapshot", "updates":[...]}`
  (full history), then stream `{"type":"update", ...}` messages. Heartbeat ping every 20s;
  clean up dead connections.
- `GET /healthz`.
- CORS configured for the Phase 5 frontend origin (env var).
- Lifespan handler: load predictor once at startup; start `GameDirectory`.
- `GameHub`: per-game set of WebSocket connections + bounded history list (it's fine
  in-memory; note Redis as the scale-out path in comments, don't build it).

## Checkpoint 4.4 — Replay Mode (critical for development)

Live games aren't always on. Build a first-class simulation path:
- `python -m wp_engine.replay --game-id 0022300456 --speed 60` replays a historical parsed
  game through the EXACT same pipeline (adapter bypassed; GameStates fed into
  FeatureBuilder → predictor → hub) at 60× speed.
- The API can serve replayed games identically to live ones (flag `is_replay=true`).
- This is how Phase 5 and Phase 6 will be developed and demoed — make it smooth.

## Checkpoint 4.5 — Tests

- Clock parser unit tests (`PT11M23.00S`, `PT0M09.40S`, malformed).
- Adapter tests against 2–3 captured live payload fixtures (fetch and commit fixtures).
- WebSocket integration test with `httpx`/`TestClient`: connect, receive snapshot, feed a
  fake event through the hub, assert an update frame arrives.
- Poller dedup/amendment logic test.

## Deliverables & Handoff

1. `live.py`, `inference.py`, `api/main.py`, `replay.py` — service runs with
   `uvicorn api.main:app` and demonstrably streams a replayed game end to end.
2. Captured live-payload fixtures + the actionType mapping table.
3. Passing tests.
4. `HANDOFF.md`: run commands, WebSocket message contract (exact JSON examples of snapshot
   and update frames), REST endpoints, env vars, and known live-feed quirks discovered.
