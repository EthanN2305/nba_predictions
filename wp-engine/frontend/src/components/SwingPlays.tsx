/** The swing-plays strip: every event that moved the needle ≥ 8 pp. */

import { SWING_THRESHOLD_PP, type ChartPoint } from "./WPChart";

export function SwingPlays({ points }: { points: ChartPoint[] }) {
  const swings = points.filter((p) => Math.abs(p.swing) >= SWING_THRESHOLD_PP);
  if (!swings.length) return null;
  return (
    <section className="mt-4">
      <h2 className="mb-2 font-clock text-xs tracking-widest text-mist">
        SWING PLAYS · ≥{SWING_THRESHOLD_PP} pp
      </h2>
      <ul className="space-y-1.5">
        {swings.map((p) => (
          <li
            key={`${p.x}-${p.wp.toFixed(1)}`}
            className="flex items-baseline gap-3 rounded bg-panel px-3 py-1.5 text-sm"
          >
            <span className="font-clock text-xs text-mist">{p.clock}</span>
            <span
              className={`font-score font-bold ${p.swing > 0 ? "text-home" : "text-away"}`}
            >
              {p.swing > 0 ? "+" : ""}
              {p.swing.toFixed(1)} pp
            </span>
            <span className="truncate text-mist">
              {p.description ?? p.score}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
