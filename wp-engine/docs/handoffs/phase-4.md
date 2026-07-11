# Phase 4 → Phase 5 Handoff

> Paste this file (plus `docs/00-PROJECT-OVERVIEW.md` and `docs/05-phase-frontend.md`)
> at the start of the Phase 5 conversation. Earlier handoffs: `docs/handoffs/`.

## What was built

Phase 4 (live backend) is complete: live adapter, fault-tolerant poller,
in-memory hub, FastAPI REST + WebSocket service, and first-class replay mode.
180 pytest tests, all green.

| File | Contents |
|------|----------|
| `src/wp_engine/live.py` | `LiveGameAdapter` (liveData actions → GameState, dedup/amendment/ordering), `display_clock`, live CDN fetchers, actionType mapping table (module docstring) |
| `src/wp_engine/inference.py` | `LivePredictor` — GameState → FeatureBuilder → predictor → `WinProbUpdate` (one per game; same class for live and replay) |
| `src/wp_engine/poller.py` | `GamePoller` (3s poll, 30s idle backoff, 5-failure degraded, never crashes), `GameDirectory` (scoreboard watcher) |
| `src/wp_engine/hub.py` | `GameHub` — bounded history + asyncio pub/sub + control frames (Redis noted as scale-out path) |
| `src/wp_engine/replay.py` | replay CLI: `--print` mode and serve mode; also exposed as `POST /replay/{id}` |
| `src/api/main.py` | FastAPI app factory (`create_app`), lifespan (predictor loaded once), CORS, routes below |
| `src/wp_engine/schemas.py` | + `WinProbUpdate` |

## Run commands

```bash
uvicorn api.main:app                                        # serve (no live pollers by default)
python -m wp_engine.replay --game-id 0022300061 --speed 60  # serve + auto-replay (~48s per game hour)
curl -X POST 'localhost:8000/replay/0022300061?speed=60'    # replay into a running server
WP_ENABLE_LIVE=1 uvicorn api.main:app                       # live pollers (see caveat!)
```

Env vars: `WP_DATA_DIR` (data root), `WP_CORS_ORIGINS`
(default `http://localhost:5173` — Vite dev server), `WP_ENABLE_LIVE`.

## REST endpoints

- `GET /healthz` → `{"status":"ok"}`
- `GET /games` → `[{"game_id","home","away","is_replay","status","wp_home","clock","home_score","away_score"}]`
- `GET /games/{game_id}/history` → full `WinProbUpdate[]` so far (chart backfill)
- `POST /replay/{game_id}?speed=60` → `202 {"status":"replay started",…}`,
  `404` unknown game, `409` already replaying

## WebSocket contract — `WS /ws/games/{game_id}`

On connect (exact JSON examples):

```json
{"type": "snapshot", "updates": [ {…WinProbUpdate…}, … ]}
```

Then one frame per event:

```json
{"type": "update", "game_id": "0022300061", "event_num": 302, "period": 4,
 "clock": "Q4 02:31", "home_score": 98, "away_score": 95,
 "wp_home": 0.8734, "ts": "2026-07-11T14:57:09.825448Z",
 "description": "Jokic 2PT", "is_replay": true}
```

Control frames: `{"type":"replay_finished","game_id":…}`,
`{"type":"feed_degraded","game_id":…}`, and `{"type":"ping"}` after 20s of
silence (ignore it client-side). `wp_home` is always P(HOME wins) — the
frontend flips display per user preference.

## Architecture notes for Phase 5

- The chart's x-axis should be **game-clock time**, not event index. Compute
  elapsed from `period` + `clock` (regulation periods 720s, OT 300s) — the
  backend deliberately doesn't send elapsed to keep the wire format minimal.
- History + snapshot means a client can connect mid-game and render the full
  curve immediately, then append updates.
- Replay games are indistinguishable from live except `is_replay: true` —
  develop the frontend entirely against
  `python -m wp_engine.replay --game-id … --speed 60`.
- One WebSocket per game; `GET /games` for the lobby/list view.

## ⚠️ Known live-feed quirks / caveats

- **cdn.nba.com is Akamai-blocked from this development network** (403 on
  every liveData URL; stats.nba.com works). Consequences: live-payload
  fixtures are SYNTHESIZED in the documented shape (`tests/test_live_adapter.py`
  documents this); `WP_ENABLE_LIVE=1` is code-complete but unverified against
  a real feed. Before production: run the scoreboard fetch from an unblocked
  network and eyeball one real game through the adapter.
- liveData `actionNumber` can arrive out of order and amendments reuse the
  same number — the adapter replays its state machine from scratch each poll
  (~1 ms per full game), so ordering/dedup/amendment are structurally correct.
- Live feed carries an explicit `possession` teamId field (better than the
  historical feed) — used directly, with carry-forward.
- Timeout counts go to `None` if the feed turns inconsistent (mirrors Phase 1);
  features then use the imputation + `timeouts_known=0` path automatically.

## Exact commands to reproduce

```bash
cd wp-engine && source .venv/bin/activate
python -m pytest                                            # 180 tests
python -m wp_engine.replay --game-id 0022300083 --print --speed 0 | tail -1
# → OT game finishes at wp_home ≈ 0.99 for the home winner
```
