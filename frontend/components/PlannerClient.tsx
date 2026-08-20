"use client";

import { FormEvent, useState } from "react";
import { api, DemandWindow, terminalLabel } from "@/lib/api";

type PlannerResult = {
  status: string;
  message?: string;
  airport?: string;
  terminal?: string;
  departure_time?: string;
  recommended_arrival?: string;
  prediction_method?: string;
  risk_level?: string;
  prediction?: { median: number; p90: number; p95: number };
  demand_windows?: DemandWindow[];
};

export function PlannerClient() {
  const [result, setResult] = useState<PlannerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    const form = new FormData(event.currentTarget);
    const payload = {
      airport: form.get("airport"),
      flight_date: form.get("flight_date"),
      flight_number: form.get("flight_number") || null,
      terminal: form.get("terminal") || null,
      departure_time: form.get("departure_time") || null,
      queue_type: form.get("queue_type"),
      risk_level: form.get("risk_level"),
      checked_bag: form.get("checked_bag") === "on",
      international: form.get("international") === "on",
    };
    try {
      setResult(await api<PlannerResult>("/api/planner/predict", { method: "POST", body: JSON.stringify(payload) }));
    } catch {
      setError("The planner API is not reachable yet. Please try again after the services start.");
    } finally {
      setLoading(false);
    }
  }

  return <div className="page-shell planner-shell">
    <section className="planner-intro"><p className="eyebrow">Plan with a margin</p><h1>When should you<br/><em>join the line?</em></h1><p>Enter a flight or a terminal and departure time. Predictions stay unavailable until enough real matching observations exist.</p></section>
    <div className="planner-layout">
      <form className="planner-form panel" onSubmit={submit}>
        <div className="form-heading"><span>01</span><div><h2>Flight details</h2><p>Flight lookup becomes available after you add your FlightAware key.</p></div></div>
        <div className="form-grid">
          <label><span>Airport</span><select name="airport"><option value="JFK">JFK · Kennedy</option><option value="LGA">LGA · LaGuardia</option></select></label>
          <label><span>Date</span><input type="date" name="flight_date" required /></label>
          <label><span>Flight number <small>optional</small></span><input name="flight_number" placeholder="DL123" autoCapitalize="characters" /></label>
          <label><span>Terminal <small>optional with flight</small></span><input name="terminal" placeholder="4" /></label>
          <label><span>Departure time</span><input type="time" name="departure_time" defaultValue="19:00" /></label>
          <label><span>Security lane</span><select name="queue_type"><option value="general">General security</option><option value="precheck">TSA PreCheck</option></select></label>
        </div>
        <div className="form-heading second"><span>02</span><div><h2>Your buffer</h2><p>Choose how much uncertainty you want to absorb.</p></div></div>
        <div className="risk-options"><label htmlFor="risk-normal" aria-label="Normal risk"><input id="risk-normal" type="radio" name="risk_level" value="normal"/><span><b>Normal</b><small>Median wait</small></span></label><label htmlFor="risk-conservative" aria-label="Conservative risk"><input id="risk-conservative" type="radio" name="risk_level" value="conservative" defaultChecked/><span><b>Conservative</b><small>90th percentile</small></span></label><label htmlFor="risk-very-safe" aria-label="Very safe risk"><input id="risk-very-safe" type="radio" name="risk_level" value="very_conservative"/><span><b>Very safe</b><small>95th percentile</small></span></label></div>
        <div className="check-row"><label><input type="checkbox" name="checked_bag"/> Checking a bag</label><label><input type="checkbox" name="international"/> International flight</label></div>
        <button className="primary-button" disabled={loading}>{loading ? "Checking the data…" : "Calculate my airport arrival"}<span>→</span></button>
        {error && <p className="form-error">{error}</p>}
      </form>
      <section className="planner-result panel" aria-live="polite">
        {!result ? <ResultEmpty /> : result.status === "ready" ? <ReadyResult result={result}/> : <div className="result-empty"><div className="clock-face collecting"><i/><span>···</span></div><p className="eyebrow">{result.airport} {result.terminal ? terminalLabel(result.terminal) : ""}</p><h2>{result.status === "insufficient_data" ? "Still collecting the evidence." : "One detail is still missing."}</h2><p>{result.message}</p>{result.departure_time && <small>Departure: {new Date(result.departure_time).toLocaleString()}</small>}</div>}
      </section>
    </div>
  </div>;
}

function ResultEmpty() {
  return <div className="result-empty"><div className="clock-face"><i/><span>?</span></div><p className="eyebrow">Your recommendation</p><h2>Ready when your details are.</h2><p>The result will show its assumptions, data availability, and uncertainty—not a made-up number.</p></div>;
}

function ReadyResult({ result }: { result: PlannerResult }) {
  if (!result.prediction || !result.recommended_arrival || !result.departure_time) return null;
  return <div className="ready-result"><p className="eyebrow">Recommended airport arrival</p><h2>{new Date(result.recommended_arrival).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</h2><p>{result.airport} · {terminalLabel(result.terminal ?? "—")} · {new Date(result.departure_time).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} departure</p><div className="prediction-bands"><div><span>Median</span><strong>{result.prediction.median} min</strong></div><div><span>P90</span><strong>{result.prediction.p90} min</strong></div><div><span>P95</span><strong>{result.prediction.p95} min</strong></div></div><small>Method: {String(result.prediction_method).replaceAll("_", " ")} · Risk setting: {String(result.risk_level).replaceAll("_", " ")}</small></div>;
}
