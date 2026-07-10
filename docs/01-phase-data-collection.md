# Phase 1 — Historical Play-by-Play Data Collection

> **Prompt to Claude:** You are building Phase 1 of the NBA Live Win-Probability Engine
> (see `00-PROJECT-OVERVIEW.md`, pasted above). Your job in this phase is to build a robust,
> resumable pipeline that harvests historical play-by-play data and produces the raw dataset
> that all later phases depend on. Follow the checkpoints in order.

## Objective

Download play-by-play logs for **3+ full NBA seasons** (e.g., 2021-22 through 2023-24 regular
seasons) using `nba_api`, parse each game's event stream into rows of the canonical
`GameState` schema plus the game outcome label, and store everything as parquet.

## Checkpoint 1.1 — Project Scaffold

- Create the repository layout from the overview (`wp-engine/` tree).
- Write `pyproject.toml` with pinned dependencies.
- Implement `src/wp_engine/schemas.py` with the canonical `GameState` model from the
  overview, plus:
  ```python
  class GameRecord(BaseModel):
      game_id: str
      season: str            # "2023-24"
      game_date: date
      home_team_id: int
      away_team_id: int
      home_team_abbr: str
      away_team_abbr: str
      final_home_score: int
      final_away_score: int
      home_win: bool         # THE LABEL
  ```
- Verify: `pip install -e .` succeeds and `python -c "from wp_engine.schemas import GameState"` works.

## Checkpoint 1.2 — Game Index Builder

Implement `collect.build_game_index(season: str) -> pd.DataFrame`:
- Use `nba_api.stats.endpoints.leaguegamefinder.LeagueGameFinder` (or
  `leaguegamelog.LeagueGameLog`) filtered to Regular Season to enumerate all game IDs.
- Deduplicate: the endpoint returns one row per team per game; collapse to one row per
  `GAME_ID` with home/away resolved via the `MATCHUP` column (`"XXX vs. YYY"` = home,
  `"XXX @ YYY"` = away).
- Emit one `GameRecord` per game including the final score and `home_win` label.
- Save to `data/raw/game_index_{season}.parquet`.

## Checkpoint 1.3 — Play-by-Play Harvester

Implement `collect.harvest_pbp(season: str)`:
- For each game in the index, call `nba_api.stats.endpoints.playbyplayv2.PlayByPlayV2(game_id=...)`.
- **Resumability:** skip games whose file `data/raw/pbp/{season}/{game_id}.parquet` already exists.
- **Rate limiting:** sleep 0.7s between requests; on failure retry up to 4 times with
  exponential backoff (2s, 4s, 8s, 16s); log and skip games that still fail, recording them
  in `data/raw/failed_{season}.json` for a later retry pass.
- Store the RAW PlayByPlayV2 dataframe untouched (all columns). Parsing happens next —
  raw data is sacred; never re-download because of a parsing bug.
- Add a `tqdm` progress bar and periodic log lines (games done / remaining / failures).

## Checkpoint 1.4 — Event Stream → GameState Parser

Implement `collect.parse_game(game_id: str) -> pd.DataFrame` producing one row per event,
conforming to the `GameState` schema + `home_win` label. This is the tricky part; handle:

- **Clock parsing:** `PCTIMESTRING` is `"MM:SS"` within the period. Compute
  `seconds_remaining_period` and `seconds_remaining_total` (periods 1–4 are 720s each;
  OT periods are 300s — for now store true seconds remaining including OT; Phase 2
  decides normalization).
- **Score parsing:** `SCORE` column is `"away - home"` (verify the ordering against the
  final result — playbyplayv2's SCORE format is `"VISITOR - HOME"`) and is **null for
  non-scoring events** → forward-fill from the last scoring event, initialize 0-0.
- **Possession inference:** PlayByPlayV2 has no possession column. Infer from
  `EVENTMSGTYPE` + which team's description column is populated
  (`HOMEDESCRIPTION` vs `VISITORDESCRIPTION`):
  - Made shot (1), turnover (5): possession flips to the other team after the event.
  - Defensive rebound (4): possession to rebounding team. Offensive rebound: stays.
  - Missed shot (2): possession pending rebound.
  - Jump ball (10), period start: set from event team.
  - When ambiguous, emit `possession = 0`. Document the inference rules in the docstring.
- **Team fouls / bonus:** count personal fouls (`EVENTMSGTYPE == 6`, excluding offensive
  fouls if distinguishable via `EVENTMSGACTIONTYPE`) per team per period; reset each period;
  `in_bonus = fouls >= 5` (period), with the last-2-minutes rule (≥2 fouls in final 2:00)
  as a stretch goal.
- **Timeouts:** track `EVENTMSGTYPE == 9` decrements from 7 per team (post-2017 rules);
  set to `None` if tracking becomes inconsistent rather than emitting wrong numbers.
- **Robustness:** wrap per-game parsing in try/except; a malformed game must not kill the
  batch run. Log parse failures to `data/raw/parse_failures.json`.

Save parsed output to `data/raw/states/{season}/{game_id}.parquet`.

## Checkpoint 1.5 — Validation Suite

Write `tests/test_collect.py` with at least:
- Parse 3 known games end-to-end (download live in test or use committed fixtures) and assert:
  final `score_diff` sign matches `home_win`; `seconds_remaining_total` is monotonically
  non-increasing within regulation; scores are monotonically non-decreasing; every period
  starts with `home_team_fouls_period == 0`.
- A summary script `python -m wp_engine.collect validate` that scans all parsed games and
  prints: games parsed, % with possession inferred on >70% of events, % with score/label
  mismatches (must be 0).

## Deliverables & Handoff

1. Full scaffold + working harvester (all checkpoints).
2. Actually run the harvest for at least ONE season end-to-end in this environment if network
   allows; otherwise ship the code with a dry-run mode over bundled fixture games.
3. Write `HANDOFF.md`: file paths produced, row counts, any schema deviations, list of known
   parsing edge cases deferred, and exact commands to reproduce.

**Do not** start feature engineering (rolling momentum etc.) in this phase — raw states only.
