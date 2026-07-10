"""Tests for collect.parse_season — batch parsing robustness (Checkpoint 1.4)."""

import json

import pandas as pd
import pytest

from wp_engine.collect import parse_season

from test_parse import AWAY_ID, HOME_ID, ev

SEASON = "2023-24"


def good_game_events(game_id):
    rows = [
        ev(1, "period", sub="start"),
        ev(2, "Made Shot", clock="PT11M40.00S", team=HOME_ID,
           score_home="2", score_away="0"),
        ev(3, "period", clock="PT00M00.00S", sub="end"),
    ]
    for r in rows:
        r["gameId"] = game_id
    return pd.DataFrame(rows)


def bad_game_events(game_id):
    rows = [
        ev(1, "Made Shot", clock="GARBAGE", team=HOME_ID,
           score_home="2", score_away="0"),
    ]
    for r in rows:
        r["gameId"] = game_id
    return pd.DataFrame(rows)


@pytest.fixture
def season_dir(tmp_path):
    """A data dir with a game index and harvested pbp for 3 games (1 malformed)."""
    game_ids = ["0022300001", "0022300002", "0022300003"]
    index = pd.DataFrame(
        [
            {
                "game_id": gid,
                "season": SEASON,
                "game_date": pd.Timestamp("2024-01-15").date(),
                "home_team_id": HOME_ID,
                "away_team_id": AWAY_ID,
                "home_team_abbr": "HHH",
                "away_team_abbr": "AAA",
                "final_home_score": 2,
                "final_away_score": 0,
                "home_win": True,
            }
            for gid in game_ids
        ]
    )
    raw = tmp_path / "raw"
    pbp_dir = raw / "pbp" / SEASON
    pbp_dir.mkdir(parents=True)
    index.to_parquet(raw / f"game_index_{SEASON}.parquet", index=False)

    good_game_events("0022300001").to_parquet(pbp_dir / "0022300001.parquet", index=False)
    bad_game_events("0022300002").to_parquet(pbp_dir / "0022300002.parquet", index=False)
    good_game_events("0022300003").to_parquet(pbp_dir / "0022300003.parquet", index=False)
    return tmp_path


class TestParseSeason:
    def test_parses_good_games_and_records_failures(self, season_dir):
        summary = parse_season(SEASON, data_dir=season_dir, progress=False)

        assert summary.parsed == ["0022300001", "0022300003"]
        assert summary.failed == ["0022300002"]

        for gid in summary.parsed:
            out = season_dir / "raw" / "states" / SEASON / f"{gid}.parquet"
            assert out.exists()
            df = pd.read_parquet(out)
            assert len(df) == 3
            assert df["home_win"].all()

        failures = json.loads((season_dir / "raw" / "parse_failures.json").read_text())
        assert "0022300002" in failures

    def test_skips_already_parsed_games(self, season_dir):
        parse_season(SEASON, data_dir=season_dir, progress=False)
        summary = parse_season(SEASON, data_dir=season_dir, progress=False)
        assert summary.parsed == []
        assert summary.skipped == ["0022300001", "0022300003"]
