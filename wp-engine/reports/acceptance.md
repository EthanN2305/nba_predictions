# Phase 6 — Final Acceptance Checklist

Run date: 2026-07-11. Environment: macOS (Apple Silicon), Python 3.12,
Node 26 — the development machine. Each item records what was actually
verified; nothing below is asserted without having been run.

## ✅ Replay demo from a clean clone

`docker compose up` packaging is complete (backend multi-stage Dockerfile,
frontend nginx image, compose with auto-replay `WP_REPLAY_GAME=0022300061`).
**Verified via the exact in-container path run locally** (no Docker daemon on
this machine): fresh `WP_DATA_DIR` → `scripts/bootstrap_demo.py` built the
demo data root from committed fixtures (6 games + pinned model) → uvicorn
with `WP_REPLAY_GAME` auto-replayed DEN–LAL → `/games` showed DEN/LAL meta,
473-tick history, final wp 0.9875. Remaining risk when first running compose
on a Docker host: image-build details only (deps install cleanly; libgomp1
included for LightGBM).

## ✅ Full test suite green + coverage

- Backend: **206 passed** (`python -m pytest`), frontend: **12 passed**
  (`npm test`) + `npm run build` with zero TS errors.
- Coverage (target features.py & live.py > 85%): **features.py 92%**,
  **live.py 96%**, overall wp_engine 82%. Lowest: monitor.py 38% (network
  CLI paths) and replay.py 61% (serve-mode wiring) — core logic of both is
  unit-tested.

## ✅ Calibration report regenerated and committed

`reports/evaluation.md` + figures were produced by `python -m wp_engine.train
all` against the current committed feature code and are in git (Phase 3
commit `dbeebcd`). Test metrics: Brier 0.1565, log loss 0.4663, AUC 0.857;
reliability diagram hugs the diagonal with mild 0.4–0.8 overconfidence.

## ⏸ Live smoke test on a real game night — DEFERRED

Not executable now for two independent reasons: (1) it is the NBA offseason
(July), no live games exist; (2) `cdn.nba.com` — every `nba_api.live`
endpoint — returns Akamai 403 from this network, so even a dated live feed
cannot be fetched (stats.nba.com works fine; verified both this session).
**To complete on opening night from an unblocked network:**
`WP_ENABLE_LIVE=1 uvicorn api.main:app`, open the frontend, screenshot the
final chart, and attach it here.

## ⏸ Monitor output for that game — DEFERRED (same blockers)

The monitor itself is implemented and tested against fixture games
(`tests/test_monitor.py`). To complete: `python -m wp_engine.monitor
--date <that night>` and paste the output below this line.

## ✅ Handoffs consolidated

`docs/architecture.md` consolidates all six phase handoffs (originals
preserved under `docs/handoffs/`). It is the new-contributor entry point.

---

### Verdict

All engineering deliverables are done and verified. The two deferred items
are external-world checks (live NBA games + an unblocked network), with
exact instructions recorded above for the first real game night.
