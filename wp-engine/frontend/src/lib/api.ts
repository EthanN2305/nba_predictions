/** REST + WebSocket endpoints. Configure the backend with VITE_API_BASE
 * (default http://localhost:8000). */

import type { GameSummary, WinProbUpdate } from "./types";

export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export function wsUrl(gameId: string): string {
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}/ws/games/${gameId}`;
}

export async function fetchGames(): Promise<GameSummary[]> {
  const response = await fetch(`${API_BASE}/games`);
  if (!response.ok) throw new Error(`GET /games → ${response.status}`);
  return response.json();
}

export async function fetchHistory(gameId: string): Promise<WinProbUpdate[]> {
  const response = await fetch(`${API_BASE}/games/${gameId}/history`);
  if (!response.ok) throw new Error(`GET history → ${response.status}`);
  return response.json();
}

export async function startReplay(gameId: string, speed = 60): Promise<void> {
  await fetch(`${API_BASE}/replay/${gameId}?speed=${speed}`, { method: "POST" });
}
