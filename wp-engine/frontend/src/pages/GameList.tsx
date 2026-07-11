/** Route "/" — today's games (or replays) as cards with mini sparklines. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Sparkline } from "../components/Sparkline";
import { TugBar } from "../components/TugBar";
import { fetchGames, fetchHistory, startReplay } from "../lib/api";
import type { GameSummary, WinProbUpdate } from "../lib/types";

const DEMO_GAME = "0022300061";
const REFRESH_MS = 30000;

function GameCard({ game }: { game: GameSummary }) {
  const [history, setHistory] = useState<WinProbUpdate[]>([]);
  useEffect(() => {
    fetchHistory(game.game_id).then(setHistory).catch(() => undefined);
  }, [game.game_id, game.wp_home]);

  const wp = game.wp_home;
  return (
    <Link
      to={`/game/${game.game_id}`}
      className="block rounded-lg border border-seam bg-panel p-4 transition-colors hover:border-mist focus-visible:outline focus-visible:outline-2 focus-visible:outline-home"
    >
      <div className="flex items-baseline justify-between">
        <div className="font-score text-2xl font-bold tracking-wide">
          <span className="text-home">{game.home ?? "HOME"}</span>
          <span className="mx-2 text-mist">{game.home_score ?? "–"}</span>
          <span className="text-mist">·</span>
          <span className="mx-2 text-mist">{game.away_score ?? "–"}</span>
          <span className="text-away">{game.away ?? "AWAY"}</span>
        </div>
        <div className="font-clock text-xs text-mist">
          {game.clock ?? game.status ?? ""}
          {game.is_replay ? " · replay" : ""}
        </div>
      </div>
      <div className="mt-3">
        <Sparkline updates={history} />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <TugBar wpHome={wp ?? 0.5} />
        {wp !== null && (
          <span className="font-score text-lg font-bold text-home">
            {(wp * 100).toFixed(1)}%
          </span>
        )}
      </div>
    </Link>
  );
}

export function GameList() {
  const [games, setGames] = useState<GameSummary[] | null>(null);

  useEffect(() => {
    const load = () => fetchGames().then(setGames).catch(() => setGames([]));
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    // dev flag: ?replay=1 kicks off the demo replay game
    if (new URLSearchParams(window.location.search).get("replay") === "1") {
      startReplay(DEMO_GAME).then(() => window.setTimeout(load, 500));
    }
    return () => window.clearInterval(timer);
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6 flex items-baseline justify-between">
        <h1 className="font-score text-3xl font-extrabold tracking-wide">
          WIN PROBABILITY
        </h1>
        <span className="font-clock text-xs text-mist">wp-engine</span>
      </header>
      {games === null ? (
        <p className="text-mist">Connecting to the scoreboard…</p>
      ) : games.length === 0 ? (
        <div className="rounded-lg border border-seam bg-panel p-6 text-mist">
          <p>No games are streaming right now.</p>
          <button
            type="button"
            className="mt-3 rounded bg-home px-3 py-1.5 font-score font-bold text-court hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-chalk"
            onClick={() =>
              startReplay(DEMO_GAME).then(() =>
                window.setTimeout(
                  () => fetchGames().then(setGames).catch(() => undefined),
                  500,
                ),
              )
            }
          >
            Replay a demo game
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {games.map((game) => (
            <GameCard key={game.game_id} game={game} />
          ))}
        </div>
      )}
    </main>
  );
}
