/** The main win-probability river: x = game minutes, y = P(home win),
 * amber above the 50% tug line, blue below, pulsing dot on the live value. */

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { clockToElapsedMinutes, quarterBreaks, totalMinutes } from "../lib/gameTime";
import type { WinProbUpdate } from "../lib/types";

export interface ChartPoint {
  x: number;
  wp: number; // 0-100
  clock: string;
  score: string;
  description: string | null;
  swing: number; // pp change vs previous event
}

export function toChartPoints(updates: WinProbUpdate[]): ChartPoint[] {
  let previous: number | null = null;
  return updates.map((u) => {
    const wp = u.wp_home * 100;
    const swing = previous === null ? 0 : wp - previous;
    previous = wp;
    return {
      x: clockToElapsedMinutes(u.period, u.clock),
      wp,
      clock: u.clock,
      score: `${u.home_score}–${u.away_score}`,
      description: u.description,
      swing,
    };
  });
}

export const SWING_THRESHOLD_PP = 8;

function ChartTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ChartPoint }> }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded border border-seam bg-panel px-3 py-2 text-sm shadow-lg">
      <div className="font-clock text-xs text-mist">
        {point.clock} · {point.score}
      </div>
      <div className="font-score text-lg font-bold">
        {point.wp.toFixed(1)}% home
      </div>
      {point.description && (
        <div className="max-w-56 text-xs text-mist">{point.description}</div>
      )}
    </div>
  );
}

export function WPChart({
  updates,
  isLive,
  clutch,
}: {
  updates: WinProbUpdate[];
  isLive: boolean;
  clutch: boolean;
}) {
  const points = useMemo(() => toChartPoints(updates), [updates]);
  const maxPeriod = updates.length
    ? Math.max(...updates.map((u) => u.period))
    : 4;
  const extent = totalMinutes(maxPeriod);
  const swings = useMemo(
    () => points.filter((p) => Math.abs(p.swing) >= SWING_THRESHOLD_PP),
    [points],
  );
  const last = points[points.length - 1];

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: -18 }}>
        <defs>
          {/* y-domain is fixed 0–100, so the 50% line is exactly mid-gradient */}
          <linearGradient id="wp-split" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--color-home)" stopOpacity={0.35} />
            <stop offset="0.5" stopColor="var(--color-home)" stopOpacity={0.06} />
            <stop offset="0.5" stopColor="var(--color-away)" stopOpacity={0.06} />
            <stop offset="1" stopColor="var(--color-away)" stopOpacity={0.35} />
          </linearGradient>
          <linearGradient id="wp-stroke" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0.5" stopColor="var(--color-home)" />
            <stop offset="0.5" stopColor="var(--color-away)" />
          </linearGradient>
        </defs>
        {clutch && (
          <ReferenceArea
            x1={43}
            x2={48}
            fill="var(--color-chalk)"
            fillOpacity={0.04}
          />
        )}
        <XAxis
          dataKey="x"
          type="number"
          domain={[0, extent]}
          ticks={quarterBreaks(maxPeriod)}
          tickFormatter={(v: number) => `${v}'`}
          stroke="var(--color-seam)"
          tick={{ fill: "var(--color-mist)", fontSize: 11 }}
        />
        <YAxis
          domain={[0, 100]}
          ticks={[0, 25, 50, 75, 100]}
          tickFormatter={(v: number) => `${v}%`}
          stroke="var(--color-seam)"
          tick={{ fill: "var(--color-mist)", fontSize: 11 }}
        />
        {quarterBreaks(maxPeriod).slice(0, -1).map((x) => (
          <ReferenceLine key={x} x={x} stroke="var(--color-seam)" />
        ))}
        <ReferenceLine y={50} stroke="var(--color-mist)" strokeWidth={1.5} />
        <Tooltip content={<ChartTooltip />} isAnimationActive={false} />
        <Area
          type="stepAfter"
          dataKey="wp"
          stroke="url(#wp-stroke)"
          strokeWidth={1.8}
          fill="url(#wp-split)"
          baseValue={50}
          dot={false}
          isAnimationActive={false}
        />
        {swings.map((p) => (
          <ReferenceDot
            key={`${p.x}-${p.wp.toFixed(1)}`}
            x={p.x}
            y={p.wp}
            r={3}
            fill={p.swing > 0 ? "var(--color-home)" : "var(--color-away)"}
            stroke="var(--color-court)"
          />
        ))}
        {last && isLive && (
          <ReferenceDot
            x={last.x}
            y={last.wp}
            r={5}
            fill={last.wp >= 50 ? "var(--color-home)" : "var(--color-away)"}
            stroke="var(--color-chalk)"
            className="animate-live-pulse"
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}
