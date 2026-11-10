import { useEffect, useState } from "react";
import { request } from "./api";

type TelegramStatus = {
  enabled: boolean;
  configured: boolean;
  counts: Record<string, number>;
  last_sent_at: string | null;
  last_error: string | null;
  latest_ai_status: string | null;
  alert_types?: {
    holdings_in_close_brief: boolean;
    reaction_followups: boolean;
    reaction_followup_sessions: number;
    big_move_alerts: boolean;
    big_move_pct: number;
    nse_mcp_enabled: boolean;
  };
};

export function TelegramAlerts() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const value = await request<TelegramStatus>("/api/telegram/status");
        if (active) { setStatus(value); setError(""); }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load Telegram status.");
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function test() {
    setSending(true);
    setError("");
    setNotice("");
    try {
      await request("/api/telegram/test", { method: "POST" });
      setNotice("Test message sent to your Telegram chat.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Telegram test failed.");
    } finally {
      setSending(false);
    }
  }

  const connected = status?.enabled && status.configured;
  const waiting = (status?.counts.PENDING || 0) + (status?.counts.RETRY || 0) + (status?.counts.PROCESSING || 0);

  return <details className="event-upload telegram-alerts">
    <summary>Telegram alerts · {status ? (connected ? "Enabled" : "Not connected") : "Checking…"}</summary>
    <p className="events-intro">New portfolio disclosures arrive in your Telegram chat with a short AI thesis. Earnings alerts include source-backed financials, profit drivers, valuation, confidence and result freshness. When AI or secure remote report access is unavailable, the alert says so.</p>
    {status && <>
      {!connected && <p className="events-intro">Connect your personal bot using the local Telegram setup command, then restart Almonds.</p>}
      {status.alert_types && <ul className="events-intro telegram-alert-types">
        <li>Holdings in the 4 PM closing brief: <strong>{status.alert_types.holdings_in_close_brief && status.alert_types.nse_mcp_enabled ? "on" : "off"}</strong> (value, day change, 52-week extremes, volume spikes)</li>
        <li>Reply under each results alert after {status.alert_types.reaction_followup_sessions} sessions, for holdings: <strong>{status.alert_types.reaction_followups && status.alert_types.nse_mcp_enabled ? "on" : "off"}</strong></li>
        <li>Big-move alert when a holding moves ±{status.alert_types.big_move_pct} pts vs the Nifty 50: <strong>{status.alert_types.big_move_alerts && status.alert_types.nse_mcp_enabled ? "on" : "off"}</strong></li>
      </ul>}
      <p className="events-intro">{status.counts.SENT || 0} sent · {waiting} queued · {status.counts.FAILED || 0} failed</p>
      {!!status.counts.UNCERTAIN && <p role="alert" className="event-error">{status.counts.UNCERTAIN} deliveries have an unknown outcome. Check your Telegram chat before requesting another version.</p>}
      {status.last_sent_at && <p className="events-intro">Last delivered: {new Date(status.last_sent_at).toLocaleString("en-IN")}</p>}
      {status.last_error && <p className="event-error" role="alert">{status.last_error}</p>}
      {status.latest_ai_status && status.latest_ai_status !== "SUCCESS" && <p className="events-intro">The latest alert could not include a validated AI thesis. Check the local analyst and retry analysis from the event.</p>}
      <button className="button button-outline button-small" disabled={!connected || sending} onClick={() => void test()}>
        {sending ? "Sending…" : "Send Telegram test"}
      </button>
    </>}
    {notice && <p role="status" className="events-intro">{notice}</p>}
    {error && <p role="alert" className="event-error">{error}</p>}
  </details>;
}
