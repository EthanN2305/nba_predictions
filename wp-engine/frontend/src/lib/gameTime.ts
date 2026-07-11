/** Clock → x-axis transforms. The backend sends period + a display clock
 * ("Q4 02:31" / "OT1 00:45"); the chart's x-axis is game minutes elapsed:
 * regulation quarters are 12 minutes, overtimes 5. */

const REGULATION_MINUTES = 48;
const QUARTER_MINUTES = 12;
const OT_MINUTES = 5;

const CLOCK_RE = /(\d+):(\d{2})\s*$/;

/** Minutes of game time remaining in the current period, from the display clock. */
function minutesRemaining(clock: string): number {
  const match = CLOCK_RE.exec(clock);
  if (!match) return 0;
  return parseInt(match[1], 10) + parseInt(match[2], 10) / 60;
}

/** Game minutes elapsed since tip-off (0 → 48, then +5 per OT). */
export function clockToElapsedMinutes(period: number, clock: string): number {
  const remaining = minutesRemaining(clock);
  if (period <= 4) {
    return (period - 1) * QUARTER_MINUTES + (QUARTER_MINUTES - remaining);
  }
  return (
    REGULATION_MINUTES + (period - 5) * OT_MINUTES + (OT_MINUTES - remaining)
  );
}

/** X positions of period boundaries for gridlines, through maxPeriod. */
export function quarterBreaks(maxPeriod: number): number[] {
  const breaks: number[] = [];
  for (let p = 1; p <= Math.max(maxPeriod, 4); p++) {
    breaks.push(
      p <= 4
        ? p * QUARTER_MINUTES
        : REGULATION_MINUTES + (p - 4) * OT_MINUTES,
    );
  }
  return breaks;
}

/** Total x-axis extent for a game that reached maxPeriod. */
export function totalMinutes(maxPeriod: number): number {
  return maxPeriod <= 4
    ? REGULATION_MINUTES
    : REGULATION_MINUTES + (maxPeriod - 4) * OT_MINUTES;
}
