/** Tiny inline WP curve for game cards — pure SVG, no chart library. */

import type { WinProbUpdate } from "../lib/types";
import { clockToElapsedMinutes, totalMinutes } from "../lib/gameTime";

export function Sparkline({ updates }: { updates: WinProbUpdate[] }) {
  if (updates.length < 2) {
    return <div className="h-8 rounded bg-seam/40" aria-hidden />;
  }
  const width = 160;
  const height = 32;
  const maxPeriod = Math.max(...updates.map((u) => u.period));
  const extent = totalMinutes(maxPeriod);
  const points = updates
    .map((u) => {
      const x = (clockToElapsedMinutes(u.period, u.clock) / extent) * width;
      const y = height - u.wp_home * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-8 w-full"
      preserveAspectRatio="none"
      aria-hidden
    >
      <line
        x1={0}
        y1={height / 2}
        x2={width}
        y2={height / 2}
        stroke="var(--color-seam)"
        strokeDasharray="3 3"
      />
      <polyline
        points={points}
        fill="none"
        stroke="var(--color-home)"
        strokeWidth={1.5}
      />
    </svg>
  );
}
