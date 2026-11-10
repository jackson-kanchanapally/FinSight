import { useCallback, useEffect, useState } from "react";
import { displayDate, money } from "./format";
import {
  reactionApi,
  signed,
  tone,
  type CompanyReactions,
  type PriceReaction,
} from "./nseApi";
import "./markets.css";

const WINDOW_LABEL: Record<string, string> = {
  "1": "First session",
  "5": "5 sessions",
  "20": "20 sessions",
};
const REASONS: Record<string, string> = {
  OUTSIDE_NSE_HISTORY: "NSE Bhavcopy history covers the last five years only.",
  NO_PRICE_JUST_BEFORE_EVENT: "No NSE close was found just before the event.",
  SHARE_BASIS_UNCONFIRMED:
    "Splits and bonuses could not be confirmed yet; Almonds will retry.",
  TOO_FEW_SESSIONS_AFTER_EVENT:
    "The stock traded too few sessions after the event.",
  NIFTY_HISTORY_UNAVAILABLE_FOR_DATES:
    "NSE's Nifty 50 history currently starts in October 2024, so there is no relative return.",
};
const TIMING: Record<string, string> = {
  DURING_OR_BEFORE_SESSION:
    "Public before the close, so the same session's close is the first reaction.",
  AFTER_CLOSE_OR_HOLIDAY:
    "Public after the close or on a non-trading day; the next session is the first reaction.",
  DATE_ONLY:
    "Only the filing date is known, so the first window may include a move before the filing.",
};

const label = (value: string) => value.replaceAll("_", " ").toLowerCase();

function Windows({ reaction }: { reaction: PriceReaction }) {
  return (
    <div className="rx-windows">
      {["1", "5", "20"].map((key) => {
        const window = reaction.windows[key];
        return (
          <div className="rx-window" key={key}>
            <span>{WINDOW_LABEL[key]}</span>
            {window ? (
              <>
                <strong
                  className={tone(
                    window.excess_return_pct ?? window.return_pct,
                  )}
                >
                  {window.excess_return_pct !== null
                    ? `${signed(window.excess_return_pct)} pts`
                    : `${signed(window.return_pct)}%`}
                </strong>
                <small>
                  {window.excess_return_pct !== null
                    ? `Stock ${signed(window.return_pct)}% · Nifty ${signed(window.index_return_pct)}%`
                    : "Stock return (no Nifty comparison)"}
                </small>
                <small>to {displayDate(window.date)}</small>
              </>
            ) : (
              <strong className="rx-pending">Waiting</strong>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function EventReactionPanel({ eventId }: { eventId: number }) {
  const [reaction, setReaction] = useState<PriceReaction | null | undefined>(
    undefined,
  );
  useEffect(() => {
    let alive = true;
    reactionApi
      .event(eventId)
      .then((value) => alive && setReaction(value))
      .catch(() => alive && setReaction(null));
    return () => {
      alive = false;
    };
  }, [eventId]);
  if (reaction === undefined) return null;
  return (
    <div className="panel rx-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">MARKET REACTION · NSE</p>
          <h2>What the share price did</h2>
        </div>
        {reaction && (
          <span className="panel-caption">{label(reaction.status)}</span>
        )}
      </div>
      {!reaction ? (
        <p className="mk-muted">
          Not measured. Reactions are measured for material events of NSE
          companies after each session.
        </p>
      ) : reaction.status === "UNAVAILABLE" &&
        !Object.keys(reaction.windows).length ? (
        <p className="mk-muted">
          {REASONS[reaction.reason ?? ""] ??
            `Unavailable (${reaction.reason}).`}
        </p>
      ) : (
        <>
          <Windows reaction={reaction} />
          <p className="mk-muted">
            Measured from the{" "}
            {reaction.base_date && displayDate(reaction.base_date)} close
            {reaction.base_close && ` of ${money(reaction.base_close)}`}; points
            are the stock's return minus the {reaction.benchmark}'s.
            {reaction.volume_ratio &&
              ` First-session volume was ${Number(reaction.volume_ratio).toFixed(1)}× the prior 20-session average.`}
          </p>
          {reaction.timing && (
            <p className="mk-muted">{TIMING[reaction.timing]}</p>
          )}
          {reaction.adjustments.length > 0 && (
            <p className="mk-muted">
              Adjusted for{" "}
              {reaction.adjustments
                .map((a) => `${a.purpose} (ex ${displayDate(a.ex_date)})`)
                .join(", ")}
              .
            </p>
          )}
          {reaction.reason && REASONS[reaction.reason] && (
            <p className="mk-muted">{REASONS[reaction.reason]}</p>
          )}
          <p className="mk-muted">
            An observation of what the market did, not an explanation of why.
          </p>
        </>
      )}
    </div>
  );
}

export function CompanyReactionHistory({ symbol }: { symbol: string }) {
  const [data, setData] = useState<CompanyReactions | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await reactionApi.company(symbol));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not load reactions.",
      );
    }
  }, [symbol]);
  useEffect(() => {
    void load();
  }, [load]);

  async function measure() {
    setBusy(true);
    setError("");
    try {
      setData(await reactionApi.refresh(symbol));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Measurement failed.");
    } finally {
      setBusy(false);
    }
  }

  const summary = Object.entries(data?.summary_by_event_type ?? {});
  const recent = (data?.reactions ?? [])
    .filter((r) => Object.keys(r.windows).length)
    .slice(0, 8);
  return (
    <section className="mk-block rx-history">
      <div className="rx-history-head">
        <h3>How the market reacted to {symbol}'s events</h3>
        <button
          className="button button-outline button-small"
          onClick={() => void measure()}
          disabled={busy}
        >
          {busy ? "Measuring…" : "Measure now"}
        </button>
      </div>
      {error && <p className="negative-value">{error}</p>}
      {summary.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Event type</th>
                <th>Events</th>
                <th>1 session</th>
                <th>5 sessions</th>
                <th>20 sessions</th>
              </tr>
            </thead>
            <tbody>
              {summary.map(([type, group]) => (
                <tr key={type}>
                  <td className="rx-type">{label(type)}</td>
                  <td>{group.count}</td>
                  {["1", "5", "20"].map((key) => {
                    const w = group.windows[key];
                    return (
                      <td
                        key={key}
                        className={tone(w?.median_excess_return_pct)}
                      >
                        {w ? `${signed(w.median_excess_return_pct)} pts` : "—"}
                        {w && (
                          <small className="rx-sub">
                            {" "}
                            {w.outperformed}/{w.observations} beat
                          </small>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {summary.length > 0 && (
        <p className="mk-muted">
          Median return vs the Nifty 50, in percentage points.
        </p>
      )}
      {recent.length > 0 ? (
        <ul className="rx-list">
          <li className="rx-list-head">
            <span>Recent events</span>
            <span>1</span>
            <span>5</span>
            <span>20</span>
          </li>
          {recent.map((r) => (
            <li key={r.event_id}>
              <a href={`#event-${r.event_id}`}>
                <strong>{r.event ? label(r.event.event_type) : "event"}</strong>
                <span>
                  {r.event && displayDate(r.event.published_at.slice(0, 10))}
                </span>
              </a>
              {["1", "5", "20"].map((key) => {
                const w = r.windows[key];
                const value = w?.excess_return_pct ?? w?.return_pct;
                return (
                  <span key={key} className={tone(value)}>
                    {w
                      ? `${signed(value)}${w.excess_return_pct !== null ? " pts" : "%"}`
                      : "…"}
                  </span>
                );
              })}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mk-muted">
          {data
            ? "No measured reactions yet. Reactions are measured for material events after each session."
            : "Loading…"}
        </p>
      )}
      {data && <p className="mk-muted">{data.note}</p>}
    </section>
  );
}
