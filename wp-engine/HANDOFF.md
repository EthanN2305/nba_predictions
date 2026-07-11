# Project Handoff — ALL SIX PHASES COMPLETE

> The per-phase handoffs are archived in `docs/handoffs/` and consolidated
> into **`docs/architecture.md`** — read that first. This file records the
> final state and the short list of things a future session should know.

## State (2026-07-11)

- Phases 1–6 done, committed on `main`, per-phase TDD throughout.
- **206 backend pytest tests + 12 frontend Vitest tests, all green.**
  Coverage: features.py 92%, live.py 96% (>85% target met).
- Model: monotone LightGBM, test Brier 0.1565 / log loss 0.4663 / AUC 0.857
  (`reports/evaluation.md`). Raw beat isotonic/Platt → identity calibrator.
- Demo: `docker compose up --build` (or `scripts/bootstrap_demo.py` +
  `python -m wp_engine.replay --game-id 0022300061 --speed 60` + `npm run dev`).
- Acceptance: `reports/acceptance.md` — everything verified except two
  external-world items (below).

## Open items (external blockers, instructions recorded)

1. **Live smoke test on a real game night** — offseason now, and
   `cdn.nba.com` is Akamai-403-blocked from this network (stats.nba.com is
   fine). On opening night, from an unblocked network:
   `WP_ENABLE_LIVE=1 uvicorn api.main:app` → screenshot final chart →
   append to `reports/acceptance.md`, and eyeball one real liveData payload
   against `LiveGameAdapter` (its fixtures are synthesized per the
   documented schema).
2. **Monitor on real games** — `python -m wp_engine.monitor --date <that
   night>`; paste output into the acceptance report.

## Invariants a future session must not break

- One feature code path (`FeatureBuilder`); parity + golden tests pin it.
  Regenerating goldens (`scripts/build_golden.py`) or the pinned model
  (`scripts/build_fixture_model.py`) must be conscious + documented.
- Split by game_id and by time, never by row.
- `wp_home` is always P(HOME wins). `GameState` field names never change.
- One uvicorn worker (in-memory `GameHub`); Redis pub/sub is the scale path.

## Retraining (e.g., with a new season)

```bash
python -m wp_engine.collect all --season 2024-25   # harvest (~30 min)
python -m wp_engine.features build --season 2024-25
# bump TRAIN_SEASONS / EVAL_SEASON in train.py, then:
python -m wp_engine.train all --trials 30          # ≈15 min, rewrites reports/
```
