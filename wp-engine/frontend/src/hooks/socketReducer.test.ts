import { describe, expect, it } from "vitest";

import type { WinProbUpdate } from "../lib/types";
import { initialSocketState, socketReducer } from "./socketReducer";

const tick = (eventNum: number, wp = 0.5): WinProbUpdate => ({
  game_id: "0022300061",
  event_num: eventNum,
  period: 1,
  clock: "Q1 10:00",
  home_score: 2,
  away_score: 0,
  wp_home: wp,
  ts: "2026-07-11T00:00:00Z",
  description: null,
  is_replay: true,
});

describe("socketReducer", () => {
  it("snapshot replaces state and marks the feed live", () => {
    const state = socketReducer(initialSocketState, {
      type: "snapshot",
      updates: [tick(1), tick(2)],
    });
    expect(state.updates).toHaveLength(2);
    expect(state.status).toBe("live");
  });

  it("updates append and dedupe by event_num (amendments/reconnects)", () => {
    let state = socketReducer(initialSocketState, {
      type: "snapshot",
      updates: [tick(1)],
    });
    state = socketReducer(state, { type: "update", ...tick(2, 0.6) });
    state = socketReducer(state, { type: "update", ...tick(2, 0.6) }); // resend
    expect(state.updates).toHaveLength(2);
    expect(state.latest?.wp_home).toBe(0.6);
  });

  it("a snapshot after reconnect resets rather than duplicating", () => {
    let state = socketReducer(initialSocketState, {
      type: "snapshot",
      updates: [tick(1), tick(2)],
    });
    state = socketReducer(state, { type: "disconnected" });
    expect(state.status).toBe("connecting");
    state = socketReducer(state, {
      type: "snapshot",
      updates: [tick(1), tick(2), tick(3)],
    });
    expect(state.updates).toHaveLength(3);
    expect(state.status).toBe("live");
  });

  it("control frames set terminal/degraded status", () => {
    let state = socketReducer(initialSocketState, {
      type: "snapshot",
      updates: [tick(1)],
    });
    state = socketReducer(state, { type: "feed_degraded", game_id: "x" });
    expect(state.status).toBe("degraded");
    state = socketReducer(state, { type: "replay_finished", game_id: "x" });
    expect(state.status).toBe("final");
  });

  it("pings are ignored", () => {
    const before = socketReducer(initialSocketState, {
      type: "snapshot",
      updates: [tick(1)],
    });
    expect(socketReducer(before, { type: "ping" })).toBe(before);
  });
});
