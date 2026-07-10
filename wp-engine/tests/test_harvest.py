"""Tests for collect.harvest_pbp (Checkpoint 1.3)."""

import json

import pandas as pd
import pytest

from wp_engine.collect import harvest_pbp


def raw_pbp_frame(game_id: str) -> pd.DataFrame:
    """Minimal raw PlayByPlayV3-shaped frame; harvester must store it untouched."""
    return pd.DataFrame(
        {
            "gameId": [game_id, game_id],
            "actionNumber": [2, 4],
            "actionType": ["period", "Jump Ball"],
            "subType": ["start", ""],
            "clock": ["PT12M00.00S", "PT12M00.00S"],
            "period": [1, 1],
            "scoreHome": ["", ""],
            "scoreAway": ["", ""],
            "description": ["Start of 1st Period", "Jump Ball"],
        }
    )


class RecordingFetcher:
    """Fake PBP fetcher that records calls and can fail per game."""

    def __init__(self, fail_games=(), fail_times=99):
        self.calls: list[str] = []
        self.fail_games = set(fail_games)
        self.fail_times = fail_times  # how many times a failing game fails
        self._failures: dict[str, int] = {}

    def __call__(self, game_id: str) -> pd.DataFrame:
        self.calls.append(game_id)
        if game_id in self.fail_games:
            n = self._failures.get(game_id, 0)
            if n < self.fail_times:
                self._failures[game_id] = n + 1
                raise ConnectionError(f"boom {game_id}")
        return raw_pbp_frame(game_id)


class RecordingSleeper:
    def __init__(self):
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


GAMES = ["0022300001", "0022300002", "0022300003"]


def run_harvest(tmp_path, fetcher, sleeper=None, **kwargs):
    return harvest_pbp(
        "2023-24",
        data_dir=tmp_path,
        fetch_pbp=fetcher,
        sleep=sleeper or RecordingSleeper(),
        game_ids=list(GAMES),
        progress=False,
        **kwargs,
    )


class TestHarvestPbp:
    def test_writes_one_parquet_per_game_with_raw_columns(self, tmp_path):
        fetcher = RecordingFetcher()
        summary = run_harvest(tmp_path, fetcher)

        assert summary.downloaded == GAMES
        for gid in GAMES:
            path = tmp_path / "raw" / "pbp" / "2023-24" / f"{gid}.parquet"
            assert path.exists()
            df = pd.read_parquet(path)
            assert list(df.columns) == list(raw_pbp_frame(gid).columns)

    def test_skips_games_already_downloaded(self, tmp_path):
        first = RecordingFetcher()
        run_harvest(tmp_path, first)

        second = RecordingFetcher()
        summary = run_harvest(tmp_path, second)
        assert second.calls == []
        assert summary.skipped == GAMES
        assert summary.downloaded == []

    def test_sleeps_between_requests(self, tmp_path):
        sleeper = RecordingSleeper()
        run_harvest(tmp_path, RecordingFetcher(), sleeper)
        # one rate-limit sleep per fetched game
        assert sleeper.sleeps.count(0.7) == len(GAMES)

    def test_retries_with_exponential_backoff_then_records_failure(self, tmp_path):
        sleeper = RecordingSleeper()
        fetcher = RecordingFetcher(fail_games=["0022300002"])
        summary = run_harvest(tmp_path, fetcher, sleeper)

        # 1 initial try + 4 retries
        assert fetcher.calls.count("0022300002") == 5
        for backoff in (2, 4, 8, 16):
            assert backoff in sleeper.sleeps
        assert summary.failed == ["0022300002"]
        # other games unaffected
        assert summary.downloaded == ["0022300001", "0022300003"]

        failures = json.loads((tmp_path / "raw" / "failed_2023-24.json").read_text())
        assert "0022300002" in failures
        assert "boom" in failures["0022300002"]

    def test_transient_failure_recovers_within_retries(self, tmp_path):
        fetcher = RecordingFetcher(fail_games=["0022300001"], fail_times=2)
        summary = run_harvest(tmp_path, fetcher)
        assert summary.failed == []
        assert (tmp_path / "raw" / "pbp" / "2023-24" / "0022300001.parquet").exists()

    def test_reads_game_ids_from_index_when_not_given(self, tmp_path):
        index = pd.DataFrame({"game_id": GAMES})
        raw = tmp_path / "raw"
        raw.mkdir(parents=True)
        index.to_parquet(raw / "game_index_2023-24.parquet", index=False)

        fetcher = RecordingFetcher()
        summary = harvest_pbp(
            "2023-24",
            data_dir=tmp_path,
            fetch_pbp=fetcher,
            sleep=RecordingSleeper(),
            progress=False,
        )
        assert summary.downloaded == GAMES
