# Phase 5 — Frontend: Live Win-Probability Chart

> **Prompt to Claude:** You are building Phase 5 of the NBA Live Win-Probability Engine.
> `00-PROJECT-OVERVIEW.md` and the Phase 4 `HANDOFF.md` (which contains the exact WebSocket
> message contract and REST endpoints) are pasted above. Build a clean React frontend that
> renders live-updating win-probability charts for today's games.

## Stack

- **Vite + React + TypeScript** in `frontend/`.
- Charting: **Recharts** (AreaChart/LineChart) — or `lightweight-charts` if smoother
  streaming performance is needed; start with Recharts.
- Styling: Tailwind. Dark theme default (this is a scoreboard product).
- No global state library needed; a custom `useGameSocket` hook + React context is enough.

## Checkpoint 5.1 — API Layer & Types

- Generate TypeScript types mirroring `WinProbUpdate` and the snapshot/update frames from
  the Phase 4 contract — copy them verbatim from `HANDOFF.md`, don't improvise names.
- `useGameSocket(gameId)`:
  - Connects to `WS /ws/games/{id}`; ingests the snapshot into state, then appends updates.
  - **Reconnect with exponential backoff** (1s → 30s cap) and re-request snapshot on
    reconnect (server sends it automatically per contract).
  - Expose `{ updates, status: 'connecting'|'live'|'degraded'|'final', latest }`.
- REST fallback: on initial page load fetch `/games/{id}/history` so the chart renders
  even before the socket opens.

## Checkpoint 5.2 — Game List Page

- Route `/`: today's games from `GET /games` (poll every 30s as fallback).
- Each card: team abbreviations + logos placeholder, score, period/clock, a **mini WP
  sparkline**, and current probability as a horizontal tug-of-war bar (home color from
  left, away from right).
- Clicking a card → `/game/:gameId`.

## Checkpoint 5.3 — The Main Chart (the centerpiece)

Route `/game/:gameId`:
- **X-axis = game time**, not event index: 0 → 48 min with gridlines at quarter breaks
  (12/24/36/48), extending dynamically for OT. Compute x from `period` + clock.
- **Y-axis = P(home win)** 0–100%, with a bold reference line at 50%.
- Render as a line with a two-tone gradient fill: area above 50% tinted home color,
  below tinted away color (Recharts: two stacked Areas or a gradient with offset at 50%).
- **Live behavior:** new points animate in; a pulsing dot marks the current value; header
  shows score, clock, and big current percentages for both teams (`72.4%` / `27.6%`).
- **Tooltip:** hover any point → clock, score, WP, and the event `description`
  ("Curry 3PT" etc.).
- **Key-moment annotations:** mark points where |ΔWP| ≥ 8 pp in one event with a small
  dot + description label (the "swing plays" strip below the chart lists them).
- Quarter-break vertical separators and a subtle shaded region for clutch time
  (last 5 min, close game) if applicable.

## Checkpoint 5.4 — Status & Polish

- Connection badge: LIVE (green pulse) / RECONNECTING / FINAL / REPLAY.
- Final state: chart freezes, winner highlighted, "peak despair/peak hope" stat callouts
  (min and max WP for the winner).
- Responsive: chart usable on mobile width; game list is a single column.
- Empty states: no games today → friendly message + link to replay demo game.
- A `?replay=1` dev flag pointing at the Phase 4 replay game for demos.

## Checkpoint 5.5 — Verification

- `npm run build` passes with zero TypeScript errors.
- Run against the Phase 4 replay mode at 60× speed and confirm: snapshot backfills the full
  curve instantly, live points append smoothly (no full-chart re-mount jank — memoize),
  reconnect works (kill/restart the backend mid-replay).
- Basic component tests (Vitest) for the clock→x-axis transform and the socket reducer.

## Deliverables & Handoff

1. Complete `frontend/` app, `npm run dev` + documented `VITE_API_BASE` env var.
2. Screenshot/GIF-worthy replay demo instructions in `frontend/README.md`.
3. `HANDOFF.md`: routes, components map, any contract mismatches discovered with Phase 4
   (and whether fixed frontend-side or flagged for backend).
