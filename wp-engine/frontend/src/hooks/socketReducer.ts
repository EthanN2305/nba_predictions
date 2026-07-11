/** Pure reducer over socket frames — unit-tested independently of React. */

import type { SocketFrame, WinProbUpdate } from "../lib/types";

export type FeedStatus = "connecting" | "live" | "degraded" | "final";

export interface SocketState {
  updates: WinProbUpdate[];
  latest: WinProbUpdate | null;
  status: FeedStatus;
}

export const initialSocketState: SocketState = {
  updates: [],
  latest: null,
  status: "connecting",
};

export type SocketAction = SocketFrame | { type: "disconnected" };

export function socketReducer(
  state: SocketState,
  action: SocketAction,
): SocketState {
  switch (action.type) {
    case "snapshot": {
      const updates = action.updates;
      return {
        updates,
        latest: updates.length ? updates[updates.length - 1] : null,
        status: "live",
      };
    }
    case "update": {
      const { type: _ignored, ...update } = action;
      // dedupe by event_num: reconnect races and resends must not double-plot
      if (state.updates.some((u) => u.event_num === update.event_num)) {
        return state;
      }
      return {
        updates: [...state.updates, update],
        latest: update,
        status: "live",
      };
    }
    case "replay_finished":
      return { ...state, status: "final" };
    case "feed_degraded":
      return { ...state, status: "degraded" };
    case "disconnected":
      return { ...state, status: "connecting" };
    case "ping":
      return state;
    default:
      return state;
  }
}
