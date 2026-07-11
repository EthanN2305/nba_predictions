# Phase 5 → Phase 6 Handoff

> Paste this file (plus `docs/00-PROJECT-OVERVIEW.md` and `docs/06-phase-hardening.md`)
> at the start of the Phase 6 conversation. Earlier handoffs: `docs/handoffs/`.

## What was built

Phase 5 (frontend) is complete: a Vite + React 19 + TypeScript app in
`frontend/` with a dark arena-scoreboard design (home = amber `#FFB020`,
away = blue `#4FA3FF`), 12 Vitest tests, `npm run build` clean.

## Routes & components

| Route | Page | Pieces |
|-------|------|--------|
| `/` | `GameList` — cards from `GET /games` (30s poll), mini SVG sparkline (per-card `GET history`), tug-of-war bar, empty state with a "Replay a demo game" button; `?replay=1` dev flag auto-starts the demo | `Sparkline`, `TugBar` |
| `/game/:gameId` | `GameView` — big score + dual percentages header, status badge (LIVE pulse / REPLAY / RECONNECTING / FEED DEGRADED / FINAL), the WP river chart, clutch shading, winner peak-despair/peak-hope callouts on FINAL, swing-plays strip (|ΔWP| ≥ 8 pp) | `WPChart`, `StatusBadge`, `SwingPlays` |

Library layer: `lib/types.ts` (wire contract, verbatim), `lib/gameTime.ts`
(clock→x transform: 12-min quarters, +5 per OT — tested), `lib/api.ts`
(`VITE_API_BASE`, default `http://localhost:8000`), `hooks/socketReducer.ts`
(pure reducer — tested) + `hooks/useGameSocket.ts` (REST backfill, WS,
1s→30s exponential-backoff reconnect; server resends snapshot on reconnect
and the reducer dedupes by `event_num`).

## Chart implementation notes

- X-axis is game minutes (`clockToElapsedMinutes`), gridlines at 12/24/36/48
  (+5 per OT); `type="stepAfter"` line — WP is a step function of events.
- Two-tone fill: fixed y-domain [0,100] makes the 50% line exactly
  mid-gradient (`<stop offset="0.5">` twice) — no runtime computation.
- `baseValue={50}` anchors the area at the tug line.
- Pulsing `ReferenceDot` marks the live value; swing events get small dots.
- Chart data is memoized (`toChartPoints`) — no full re-mount per tick.

## Contract mismatches discovered with Phase 4

None. The wire contract worked as documented. One deliberate frontend-side
choice: `GET /games` has no team-name fallback when meta is missing (live
games not yet seeded by the directory) — cards render "HOME/AWAY" labels.

## Verification done

- `npm test` (12) + `npm run build` (zero TS errors).
- Full-stack smoke: uvicorn + `POST /replay/0022300062?speed=0` → `/games`
  shows GSW/PHX meta, history = 553 ticks, final wp_home = 0.016 (home lost ✓).
- Not yet automated: killing the backend mid-replay to watch the reconnect
  badge (logic unit-tested via the reducer; browser-level e2e is a Phase 6
  candidate if Playwright is added).

## What Phase 6 must know

- The e2e replay harness can drive everything through
  `POST /replay/{id}?speed=0` + `WS /ws/games/{id}` — that's how
  `tests/test_ws.py` already does it; extend to 5 diverse games + frame
  validation against `WinProbUpdate`.
- Golden-trajectory test: `python -m wp_engine.replay --game-id … --print
  --speed 0` is deterministic given fixed model artifacts — but artifacts are
  gitignored, so commit the golden wp series produced by a PINNED tiny model
  (tests/conftest.py `tiny_models_dir`) or regenerate goldens on retrain.
- Skew guard: compare `feature_meta.json` `feature_columns` ==
  `features.FEATURE_COLUMNS` == booster `feature_name()`.
- Frontend chunk is ~600 kB minified (Recharts) — fine locally; code-split if
  it ever matters.

## Exact commands to reproduce

```bash
cd wp-engine && source .venv/bin/activate
python -m pytest                     # 180 backend tests
cd frontend && npm install && npm test && npm run build   # 12 tests, clean build
# demo: python -m wp_engine.replay --game-id 0022300061 --speed 60  +  npm run dev
```
