/** WebSocket lifecycle: REST backfill, live frames, exponential-backoff
 * reconnect (1s → 30s cap). The server resends a full snapshot on every
 * (re)connect, and the reducer dedupes, so reconnects are seamless. */

import { useEffect, useReducer, useRef } from "react";

import { fetchHistory, wsUrl } from "../lib/api";
import type { SocketFrame } from "../lib/types";
import {
  initialSocketState,
  socketReducer,
  type SocketState,
} from "./socketReducer";

const BACKOFF_START_MS = 1000;
const BACKOFF_CAP_MS = 30000;

export function useGameSocket(gameId: string): SocketState {
  const [state, dispatch] = useReducer(socketReducer, initialSocketState);
  const finalRef = useRef(false);
  finalRef.current = state.status === "final";

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let backoff = BACKOFF_START_MS;
    let disposed = false;

    // REST backfill so the chart renders before the socket opens
    fetchHistory(gameId)
      .then((updates) => {
        if (!disposed && updates.length) {
          dispatch({ type: "snapshot", updates });
        }
      })
      .catch(() => undefined);

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(wsUrl(gameId));
      socket.onmessage = (event) => {
        const frame = JSON.parse(event.data) as SocketFrame;
        dispatch(frame);
        if (frame.type === "update" || frame.type === "snapshot") {
          backoff = BACKOFF_START_MS; // healthy traffic resets the backoff
        }
      };
      socket.onclose = () => {
        if (disposed || finalRef.current) return;
        dispatch({ type: "disconnected" });
        retryTimer = window.setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, BACKOFF_CAP_MS);
      };
      socket.onerror = () => socket?.close();
    };
    connect();

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [gameId]);

  return state;
}
