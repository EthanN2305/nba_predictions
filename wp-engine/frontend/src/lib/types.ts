/** Wire types — copied verbatim from the Phase 4 HANDOFF contract. */

export interface WinProbUpdate {
  game_id: string;
  event_num: number;
  period: number;
  clock: string; // display string, e.g. "Q4 02:31" / "OT1 00:45"
  home_score: number;
  away_score: number;
  wp_home: number; // calibrated P(home win)
  ts: string;
  description: string | null;
  is_replay: boolean;
}

export interface SnapshotFrame {
  type: "snapshot";
  updates: WinProbUpdate[];
}

export interface UpdateFrame extends WinProbUpdate {
  type: "update";
}

export interface ControlFrame {
  type: "replay_finished" | "feed_degraded";
  game_id: string;
}

export interface PingFrame {
  type: "ping";
}

export type SocketFrame = SnapshotFrame | UpdateFrame | ControlFrame | PingFrame;

export interface GameSummary {
  game_id: string;
  home?: string;
  away?: string;
  is_replay?: boolean;
  status?: string;
  wp_home: number | null;
  clock: string | null;
  home_score: number | null;
  away_score: number | null;
}
