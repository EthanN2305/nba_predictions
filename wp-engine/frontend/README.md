# wp-engine frontend

Live NBA win-probability charts. Vite + React 19 + TypeScript, Tailwind v4,
Recharts. Dark "arena at night" theme: home is always amber, away is always
blue, and the chart is a two-tone river split at the 50% tug line.

## Run the demo (replay mode)

```bash
# terminal 1 — backend with a replayed game at 60× speed (~48s per game hour)
cd wp-engine && source .venv/bin/activate
python -m wp_engine.replay --game-id 0022300061 --speed 60

# terminal 2 — frontend
cd wp-engine/frontend
npm install
npm run dev            # http://localhost:5173
```

Open http://localhost:5173 — the DEN–LAL card appears with a live sparkline;
click it to watch the full chart draw itself. `http://localhost:5173/?replay=1`
kicks off the demo replay automatically. Other fun fixtures:
`0022300062` (GSW home loss), `0022300083` (SAS OT win — the axis grows).

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `VITE_API_BASE` | `http://localhost:8000` | backend base URL (REST + WS derive from it) |

## Scripts

```bash
npm run dev        # Vite dev server
npm test           # Vitest: clock→x-axis transform + socket reducer
npm run build      # tsc -b && vite build (zero-TS-error gate)
```

## Map

```
src/
├── lib/            types.ts (wire contract), gameTime.ts (clock→x), api.ts
├── hooks/          socketReducer.ts (pure, tested), useGameSocket.ts (WS + backoff reconnect)
├── components/     WPChart (the river), TugBar, Sparkline, StatusBadge, SwingPlays
└── pages/          GameList (/), GameView (/game/:gameId)
```
