"""Regenerate the golden WP trajectory (tests/fixtures/golden/).

Run ONLY when a feature-logic or pinned-model change is intentional, and say
so in the commit message — the golden test exists to catch silent drift.

Usage: .venv/bin/python scripts/build_golden.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wp_engine.collect import parse_game  # noqa: E402
from wp_engine.features import build_offline, build_pregame_context  # noqa: E402
from wp_engine.schemas import GameRecord  # noqa: E402
from wp_engine.train import load_predictor  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
GOLDEN_GAME = "0022300061"  # DEN 119-107 LAL


def main() -> None:
    index = pd.read_parquet(FIXTURES / "game_index.parquet")
    record = GameRecord(**index[index["game_id"] == GOLDEN_GAME].iloc[0].to_dict())
    states = parse_game(
        GOLDEN_GAME,
        pbp=pd.read_parquet(FIXTURES / "pbp" / f"{GOLDEN_GAME}.parquet"),
        record=record,
    )
    features = build_offline(states, build_pregame_context(index, GOLDEN_GAME))
    wp = load_predictor(models_dir=FIXTURES / "models")(features)
    golden = {
        "game_id": GOLDEN_GAME,
        "trajectory": [
            {"event_num": int(e), "wp_home": float(p)}
            for e, p in zip(states["event_num"], wp)
        ],
    }
    out = FIXTURES / "golden" / f"{GOLDEN_GAME}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(golden, indent=1) + "\n")
    print(f"wrote {out} ({len(golden['trajectory'])} events)")


if __name__ == "__main__":
    main()
