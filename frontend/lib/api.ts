export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export type Airport = { code: "JFK" | "LGA"; name: string; terminals: string[] };
export type Wait = {
  observed_at: string;
  airport: string;
  terminal: string;
  checkpoint: string;
  queue_type: "general" | "precheck";
  is_open: boolean;
  is_wait_time_available: boolean;
  wait_minutes: number | null;
  status: string | null;
};
export type DemandWindow = {
  offset_start_minutes: number;
  offset_end_minutes: number;
  flights: number;
  scheduled_seats: number;
  flights_with_capacity: number;
  capacity_coverage: number | null;
};
export type FlightDemandPoint = {
  timestamp: string;
  window_start: string;
  window_end: string;
  flights: number;
};

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "content-type": "application/json", ...(options?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function terminalLabel(value: string) {
  return /^terminal/i.test(value) ? value : `Terminal ${value}`;
}

export function ageLabel(value?: string) {
  if (!value) return "not collected yet";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}
