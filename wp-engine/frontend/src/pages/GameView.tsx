/** Route "/game/:gameId" — the live chart page. */

import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { StatusBadge } from "../components/StatusBadge";
import { SwingPlays } from "../components/SwingPlays";
import { TugBar } from "../components/TugBar";
import { toChartPoints, WPChart } from "../components/WPChart";
import { useGameSocket } from "../hooks/useGameSocket";

function isClutch(updates: ReturnType<typeof useGameSocket>["updates"]): boolean {
  return updates.some(
    (u) =>
      u.period >= 4 &&
      Math.abs(u.home_score - u.away_score) <= 5 &&
      /^(Q4 0[0-4]:|OT)/.test(u.clock),
  );
}

export function GameView() {
  const { gameId = "" } = useParams();
  const { updates, latest, status } = useGameSocket(gameId);
  const points = useMemo(() => toChartPoints(updates), [updates]);

  const wpHome = latest ? latest.wp_home : 0.5;
  const homeWon = status === "final" && latest ? latest.home_score > latest.away_score : null;
  const winnerSeries = updates.map((u) =>
    homeWon === false ? 1 - u.wp_home : u.wp_home,
  );
  const peakDespair = winnerSeries.length ? Math.min(...winnerSeries) : null;
  const peakHope = winnerSeries.length ? Math.max(...winnerSeries) : null;

  return (
    <main className="mx-auto max-w-4xl px-4 py-6">
      <nav className="mb-4">
        <Link
          to="/"
          className="font-clock text-xs text-mist hover:text-chalk focus-visible:outline focus-visible:outline-2 focus-visible:outline-home"
        >
          ← all games
        </Link>
      </nav>

      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-score text-4xl font-extrabold tracking-wide">
              <span className={homeWon === true ? "text-home" : homeWon === false ? "text-mist" : "text-home"}>
                {latest?.home_score ?? 0}
              </span>
              <span className="mx-2 text-mist">–</span>
              <span className={homeWon === false ? "text-away" : homeWon === true ? "text-mist" : "text-away"}>
                {latest?.away_score ?? 0}
              </span>
            </h1>
            <StatusBadge status={status} isReplay={latest?.is_replay ?? false} />
          </div>
          <div className="mt-1 font-clock text-sm text-mist">
            {latest?.clock ?? "waiting for first event"} · game {gameId}
          </div>
        </div>
        <div className="text-right">
          <div className="font-score text-5xl font-extrabold leading-none">
            <span className="text-home">{(wpHome * 100).toFixed(1)}%</span>
          </div>
          <div className="font-score text-xl font-bold text-away">
            {((1 - wpHome) * 100).toFixed(1)}% away
          </div>
        </div>
      </header>

      <div className="mb-4">
        <TugBar wpHome={wpHome} />
      </div>

      <div className="h-72 rounded-lg border border-seam bg-panel p-3 sm:h-96">
        {updates.length === 0 ? (
          <div className="flex h-full items-center justify-center text-mist">
            No events yet — the curve appears with the first play.
          </div>
        ) : (
          <WPChart
            updates={updates}
            isLive={status === "live"}
            clutch={isClutch(updates)}
          />
        )}
      </div>

      {status === "final" && peakDespair !== null && peakHope !== null && (
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div className="rounded-lg border border-seam bg-panel p-4">
            <div className="font-clock text-xs tracking-widest text-mist">
              WINNER'S PEAK DESPAIR
            </div>
            <div className="font-score text-3xl font-extrabold">
              {(peakDespair * 100).toFixed(1)}%
            </div>
          </div>
          <div className="rounded-lg border border-seam bg-panel p-4">
            <div className="font-clock text-xs tracking-widest text-mist">
              WINNER'S PEAK HOPE
            </div>
            <div className="font-score text-3xl font-extrabold">
              {(peakHope * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      )}

      <SwingPlays points={points} />
    </main>
  );
}
