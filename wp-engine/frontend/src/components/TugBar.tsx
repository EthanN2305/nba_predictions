/** Horizontal tug-of-war: home amber pushes from the left, away blue from
 * the right; the seam sits at the current probability. */

export function TugBar({ wpHome }: { wpHome: number }) {
  const pct = Math.round(wpHome * 1000) / 10;
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-seam"
      role="meter"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="home win probability"
    >
      <div
        className="h-full bg-home transition-[width] duration-500 ease-out"
        style={{ width: `${pct}%`, boxShadow: "1px 0 0 0 #0b0e13" }}
      />
    </div>
  );
}
