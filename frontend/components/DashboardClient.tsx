"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Airport, api, ageLabel, DemandWindow, FlightDemandPoint, terminalLabel, Wait } from "@/lib/api";

type StatusResponse = {
  latest_runs: { source: string; airport: string; status: string; finished_at: string | null; records_received: number }[];
  counts: { wait_observations: number; flight_snapshots: number };
};

export function DashboardClient() {
  const [airports, setAirports] = useState<Airport[]>([]);
  const [airport, setAirport] = useState<"JFK" | "LGA">("JFK");
  const [terminal, setTerminal] = useState("");
  const [queue, setQueue] = useState<"general" | "precheck">("general");
  const [waits, setWaits] = useState<Wait[]>([]);
  const [history, setHistory] = useState<Wait[]>([]);
  const [demand, setDemand] = useState<DemandWindow[]>([]);
  const [demandHistory, setDemandHistory] = useState<FlightDemandPoint[]>([]);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      const terminalParam = terminal ? `&terminal=${encodeURIComponent(terminal)}` : "";
      const [airportData, currentData, historyData, demandData, demandHistoryData, statusData] = await Promise.all([
        api<{ airports: Airport[] }>("/api/airports"),
        api<{ waits: Wait[] }>(`/api/dashboard/current?airport=${airport}${terminalParam}`),
        api<{ observations: Wait[] }>(`/api/dashboard/history?airport=${airport}&queue_type=${queue}&hours=24${terminalParam}`),
        api<{ windows: DemandWindow[] }>(`/api/dashboard/demand?airport=${airport}${terminalParam}`),
        api<{ points: FlightDemandPoint[] }>(`/api/dashboard/demand/history?airport=${airport}&hours=24${terminalParam}`),
        api<StatusResponse>("/api/system/status"),
      ]);
      setAirports(airportData.airports);
      setWaits(currentData.waits);
      setHistory(historyData.observations);
      setDemand(demandData.windows);
      setDemandHistory(demandHistoryData.points);
      setStatus(statusData);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [airport, terminal, queue]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 60_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  const selectedAirport = airports.find((item) => item.code === airport);
  const visibleWaits = waits.filter((item) => item.queue_type === queue);
  const chart = history
    .filter((item) => item.wait_minutes !== null)
    .map((item) => ({
      time: new Date(item.observed_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      wait: item.wait_minutes,
    }));
  const demandChart = demandHistory.map((item) => ({
    time: new Date(item.timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    flights: item.flights,
  }));
  const lastWait = useMemo(() => visibleWaits.map((item) => item.observed_at).sort().at(-1), [visibleWaits]);
  const collectionRun = status?.latest_runs.find((item) => item.source === "port_authority" && item.airport === airport);

  return (
    <div className="page-shell">
      <section className="dashboard-hero">
        <div>
          <p className="eyebrow">New York airport pulse</p>
          <h1>Plan when you get<br />to the airport</h1>
          <p className="hero-copy">Live security waits meet scheduled departure demand. Predictions come online as real observations accumulate.</p>
        </div>
        <div className="hero-status-card">
          <div className="status-orbit"><span>{airport}</span></div>
          <div><small>Collector status</small><strong>{collectionRun?.status === "success" ? "Receiving live data" : "Waiting for first poll"}</strong><p>{status?.counts.wait_observations ?? 0} wait observations stored</p></div>
        </div>
      </section>

      <section className="control-bar" aria-label="Dashboard filters">
        <label><span>Airport</span><select value={airport} onChange={(event) => { setAirport(event.target.value as "JFK" | "LGA"); setTerminal(""); }}><option value="JFK">JFK · Kennedy</option><option value="LGA">LGA · LaGuardia</option></select></label>
        <label><span>Terminal</span><select value={terminal} onChange={(event) => setTerminal(event.target.value)}><option value="">All terminals</option>{selectedAirport?.terminals.map((value) => <option key={value} value={value}>{terminalLabel(value)}</option>)}</select></label>
        <div className="segmented" aria-label="Security lane"><button className={queue === "general" ? "active" : ""} onClick={() => setQueue("general")}>General</button><button className={queue === "precheck" ? "active" : ""} onClick={() => setQueue("precheck")}>TSA PreCheck</button></div>
        <button className="refresh-button" onClick={() => void load()} aria-label="Refresh live data">↻ <span>Refresh</span></button>
      </section>

      {error && <div className="notice error">The API is not reachable yet. The dashboard will retry automatically.</div>}

      <div className="dashboard-grid">
        <section className="panel waits-panel">
          <div className="panel-heading"><div><p className="eyebrow">Right now</p><h2>Security waits</h2></div><span className="updated">Updated {ageLabel(lastWait)}</span></div>
          {loading ? <LoadingRows /> : visibleWaits.length ? <div className="wait-grid">{visibleWaits.map((item) => <article className="wait-card" key={`${item.terminal}-${item.checkpoint}-${item.queue_type}`}><div><span className="terminal-pill">{terminalLabel(item.terminal || "—")}</span><p>{item.checkpoint || "Main checkpoint"}</p></div><div className="wait-number">{item.is_open && item.wait_minutes !== null ? <><strong>{item.wait_minutes}</strong><span>min</span></> : <strong className="dash">—</strong>}</div><small>{item.is_open ? item.status || "Open" : "Closed"}</small></article>)}</div> : <EmptyState title="The first live poll is on its way" body="The scheduler stores only real Port Authority observations. No placeholder wait times are shown." />}
        </section>

        <section className="panel demand-panel">
          <div className="panel-heading"><div><p className="eyebrow">Next three hours</p><h2>Departure pressure</h2></div><span className="data-tag">FlightAware</span></div>
          {demand.some((item) => item.flights > 0) ? <div className="demand-list"><div className="demand-header"><span>Time Window</span><span>Seat pressure</span><span>Flights</span><span>Seats</span></div>{demand.map((item) => { const max = Math.max(...demand.map((value) => value.scheduled_seats), 1); const pressurePercent = Math.round(item.scheduled_seats / max * 100); const pressureLabel = item.scheduled_seats ? `${item.scheduled_seats.toLocaleString()} scheduled seats, ${pressurePercent}% of the busiest window` : "Scheduled seat capacity unavailable"; return <div className="demand-row" key={item.offset_start_minutes}><span>+{item.offset_start_minutes}–{item.offset_end_minutes}m</span><div className="bar-track" role="img" aria-label={pressureLabel} title={pressureLabel}><i style={{ width: `${Math.max(4, pressurePercent)}%` }} /></div><strong>{item.flights}</strong><b>{item.scheduled_seats ? item.scheduled_seats.toLocaleString() : "—"}</b></div>; })}</div> : <EmptyState title="No schedule sample for this window" body="Departure counts and seats will appear after a budget-approved FlightAware collection day." compact />}
        </section>

        <section className="panel chart-panel">
          <div className="panel-heading"><div><p className="eyebrow">Past 24 hours</p><h2>Wait-time rhythm</h2></div><div className="legend"><i /> {queue === "general" ? "General security" : "TSA PreCheck"}</div></div>
          {chart.length ? <div className="chart-wrap"><ResponsiveContainer width="100%" height={290}><AreaChart data={chart} margin={{ top: 16, right: 12, left: -22, bottom: 0 }}><defs><linearGradient id="waitFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#e7ff5a" stopOpacity={0.38}/><stop offset="100%" stopColor="#e7ff5a" stopOpacity={0}/></linearGradient></defs><CartesianGrid vertical={false} stroke="#27313a"/><XAxis dataKey="time" stroke="#74808b" tickLine={false} axisLine={false} minTickGap={42}/><YAxis stroke="#74808b" tickLine={false} axisLine={false}/><Tooltip contentStyle={{ background: "#111920", border: "1px solid #34414b", borderRadius: 12 }}/><Area type="monotone" dataKey="wait" stroke="#e7ff5a" strokeWidth={2} fill="url(#waitFill)" /></AreaChart></ResponsiveContainer></div> : <EmptyState title="A history is being written" body="This chart will grow with each five-minute observation." compact />}
        </section>

        <section className="panel chart-panel flights-chart-panel">
          <div className="panel-heading"><div><p className="eyebrow">Past 24 hours · passenger-arrival window</p><h2>Flights departing 30m–2h30 ahead</h2></div><div className="legend flight"><i /> Scheduled departures</div></div>
          {demandChart.length ? <div className="chart-wrap"><ResponsiveContainer width="100%" height={290}><AreaChart data={demandChart} margin={{ top: 16, right: 12, left: -22, bottom: 0 }}><defs><linearGradient id="flightFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#62e3cf" stopOpacity={0.38}/><stop offset="100%" stopColor="#62e3cf" stopOpacity={0}/></linearGradient></defs><CartesianGrid vertical={false} stroke="#27313a"/><XAxis dataKey="time" stroke="#74808b" tickLine={false} axisLine={false} minTickGap={42}/><YAxis allowDecimals={false} stroke="#74808b" tickLine={false} axisLine={false}/><Tooltip contentStyle={{ background: "#111920", border: "1px solid #34414b", borderRadius: 12 }}/><Area type="monotone" dataKey="flights" stroke="#62e3cf" strokeWidth={2} fill="url(#flightFill)" /></AreaChart></ResponsiveContainer></div> : <EmptyState title="No sampled schedule covers this period" body="This chart appears only where a completed FlightAware schedule lets us calculate a real two-hour departure count." compact />}
        </section>
      </div>
    </div>
  );
}

function EmptyState({ title, body, compact = false }: { title: string; body: string; compact?: boolean }) {
  return <div className={`empty-state ${compact ? "compact" : ""}`}><div className="empty-signal"><i/><i/><i/></div><h3>{title}</h3><p>{body}</p></div>;
}

function LoadingRows() {
  return <div className="loading-rows"><i/><i/><i/></div>;
}
