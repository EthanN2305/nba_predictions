# NBA Live Win-Probability Engine

A production-grade engine that computes **live, per-event win probabilities**
for NBA games — monotone-constrained LightGBM over 3 seasons of play-by-play,
streamed to a React chart over WebSockets.

**Everything lives in [`wp-engine/`](wp-engine/)** — start with its
[README](wp-engine/README.md) and [`wp-engine/docs/architecture.md`](wp-engine/docs/architecture.md).
The original six-phase build plans are in [`docs/`](docs/).

## 60-second demo

```bash
cd wp-engine
docker compose up --build
# → http://localhost:5173 — DEN–LAL replays live at 60× speed
```

Or without Docker:

```bash
cd wp-engine
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev]"
source .venv/bin/activate && brew install libomp   # macOS
python scripts/bootstrap_demo.py                   # demo data from committed fixtures
python -m wp_engine.replay --game-id 0022300061 --speed 60
# then: cd frontend && npm install && npm run dev  → http://localhost:5173
```

## Numbers

- 3,690 games harvested and parsed (2021-22 → 2023-24), zero failures
- 1,186,110 training rows, 30 features, one shared feature code path
- Held-out test: **Brier 0.1565 · log loss 0.4663 · AUC 0.857** (naive 0.253, logistic 0.168)
- 206 backend tests + 12 frontend tests, e2e replay regression + golden-trajectory drift guard
