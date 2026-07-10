# Phase 6 — Hardening, End-to-End Validation & Deployment

> **Prompt to Claude:** You are building the final phase of the NBA Live Win-Probability
> Engine. `00-PROJECT-OVERVIEW.md` and all prior `HANDOFF.md` files are pasted above.
> Everything works in development; make it trustworthy and deployable.

## Checkpoint 6.1 — End-to-End Replay Regression Suite

The replay harness (Phase 4) becomes the system test:
- `tests/e2e/test_replay_pipeline.py`: replay 5 diverse historical games (blowout, comeback,
  OT, wire-to-wire, low-scoring) through poller-adapter-features-model-hub with a real
  WebSocket client attached. Assert:
  - Update count matches expected event count (post-dedup).
  - Final wp_home > 0.95 when home won, < 0.05 when home lost.
  - No single-event WP jump > 25 pp outside the final 2 minutes (smoothness guard).
  - All frames validate against the `WinProbUpdate` schema.
- **Golden-file test:** commit the WP trajectory of one game; future code changes must
  reproduce it within 1e-6 (or the golden file is consciously regenerated with a changelog
  note). This catches silent feature-logic drift — the project's biggest risk.

## Checkpoint 6.2 — Skew & Drift Guards

- CI check that `feature_meta.json`'s feature list == columns produced by
  `FeatureBuilder.update()` == columns the model expects. Fail loudly on mismatch.
- Startup assertion in the API: model version in `feature_meta.json` matches loaded pickle.
- A weekly-runnable `python -m wp_engine.monitor` script: replays yesterday's completed
  games via the LIVE adapter path and reports realized Brier score vs the Phase 3 test
  benchmark. If live Brier degrades > 20% relative, print a retraining recommendation.
  (This doubles as verification that the live adapter and historical parser agree.)

## Checkpoint 6.3 — Operational Hardening

- **Structured logging** (`structlog` or stdlib JSON): per-game logs with game_id context;
  poll latency, inference latency, connected-client counts.
- **Backpressure:** slow WebSocket clients get dropped after a bounded send queue (asyncio
  `wait_for` on send), never stall the broadcast loop.
- Graceful shutdown: pollers cancelled cleanly, clients receive a `{"type":"server_closing"}`
  frame.
- Config via environment (pydantic-settings): poll intervals, CORS origins, model paths,
  replay flags. Single `Settings` object; no scattered `os.environ` reads.
- Rate-limit courtesy: global semaphore capping concurrent nba_api requests (e.g., 3) even
  with 10+ simultaneous games.

## Checkpoint 6.4 — Packaging & Deployment

- **Dockerfiles:** multi-stage backend image (uv/pip install → slim runtime, model files
  baked in or volume-mounted — support both) and a static frontend build served by nginx
  or the backend's `StaticFiles`.
- `docker-compose.yml`: backend + frontend, one command up, replay demo works in-container.
- `README.md` (top-level, rewrite): architecture diagram, quickstart (compose), how to
  retrain, how to run replay demo, endpoint docs, screenshots.
- Brief deployment notes for a single small VM (this is a hobby-scale service): uvicorn
  workers=1 (state is in-memory — document why >1 worker breaks the hub, and that Redis
  pub/sub is the fix if ever needed).

## Checkpoint 6.5 — Final Acceptance Checklist

Run and record results in `reports/acceptance.md`:
- [ ] `docker compose up` → replay demo visible in browser within 2 minutes from clean clone.
- [ ] Full pytest suite green; coverage report generated (target: features.py and live.py > 85%).
- [ ] Calibration report regenerated and committed (Phase 3 script rerun on current model).
- [ ] Live smoke test on a real game night (or scheduled): document one real game tracked
      end-to-end with a screenshot of the final chart.
- [ ] `monitor` script output for that game committed.
- [ ] All HANDOFF.md files consolidated into `docs/architecture.md`.

## Deliverables

1. E2E + golden-file test suite, skew guards, monitor script.
2. Docker packaging + compose + rewritten README.
3. `reports/acceptance.md` fully checked off.
4. A final `docs/architecture.md` consolidating all phase handoffs — the document a new
   contributor reads first.
