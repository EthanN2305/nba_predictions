import type { FeedStatus } from "../hooks/socketReducer";

const STYLES: Record<string, { label: string; className: string; pulse?: boolean }> = {
  live: { label: "LIVE", className: "bg-live/15 text-live", pulse: true },
  replay: { label: "REPLAY", className: "bg-away/15 text-away", pulse: true },
  connecting: { label: "RECONNECTING", className: "bg-mist/15 text-mist" },
  degraded: { label: "FEED DEGRADED", className: "bg-home/15 text-home" },
  final: { label: "FINAL", className: "bg-seam text-chalk" },
};

export function StatusBadge({
  status,
  isReplay,
}: {
  status: FeedStatus;
  isReplay: boolean;
}) {
  const key = status === "live" && isReplay ? "replay" : status;
  const style = STYLES[key];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-clock text-xs tracking-widest ${style.className}`}
    >
      {style.pulse && (
        <span className="animate-live-pulse inline-block h-1.5 w-1.5 rounded-full bg-current" />
      )}
      {style.label}
    </span>
  );
}
