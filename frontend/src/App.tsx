import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  api,
  type Company,
  type CompanyInput,
  type Holding,
  type Portfolio,
  type Transaction,
  type TransactionInput,
} from "./api";
import { displayDate, money, quantity, todayLocal } from "./format";

type DashboardData = {
  companies: Company[];
  transactions: Transaction[];
  portfolio: Portfolio;
};
type Dialog = "company" | "transaction" | null;

function Icon({
  name,
  size = 20,
}: {
  name:
    | "grid"
    | "layers"
    | "activity"
    | "plus"
    | "arrow"
    | "close"
    | "refresh"
    | "check"
    | "wallet"
    | "trend"
    | "receipt"
    | "search";
  size?: number;
}) {
  const paths: Record<typeof name, ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    layers: (
      <>
        <path d="m12 3 9 5-9 5-9-5 9-5Z" />
        <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
      </>
    ),
    activity: <path d="M3 12h4l3-7 4 14 3-7h4" />,
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    close: <path d="M5 5 19 19M19 5 5 19" />,
    refresh: (
      <>
        <path d="M20 11a8 8 0 0 0-14.5-4.7L3 9" />
        <path d="M3 4v5h5M4 13a8 8 0 0 0 14.5 4.7L21 15" />
        <path d="M21 20v-5h-5" />
      </>
    ),
    check: <path d="m4 12 5 5L20 6" />,
    wallet: (
      <>
        <rect x="3" y="6" width="18" height="15" rx="3" />
        <path d="M3 10h18M6 6V4a2 2 0 0 1 2-2h9" />
        <path d="M16 15h2" />
      </>
    ),
    trend: (
      <>
        <path d="m3 17 6-6 4 4 8-8" />
        <path d="M15 7h6v6" />
      </>
    ),
    receipt: (
      <>
        <path d="M5 3h14v18l-3-2-4 2-4-2-3 2V3Z" />
        <path d="M9 8h6M9 12h6" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m16 16 5 5" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

function Modal({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-top">
          <div>
            <p className="eyebrow">FIN SIGHT / NEW ENTRY</p>
            <h2>{title}</h2>
            <p className="muted">{subtitle}</p>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

function CompanyDialog({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (value: CompanyInput) => Promise<void>;
}) {
  const [symbol, setSymbol] = useState("");
  const [name, setName] = useState("");
  const [exchange, setExchange] = useState("NSE");
  const [sector, setSector] = useState("");
  const [isin, setIsin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await onSave({
        symbol: symbol.trim().toUpperCase(),
        name: name.trim(),
        exchange: exchange.trim(),
        sector: sector.trim() || null,
        isin: isin.trim() || null,
      });
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not add company.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Add a company"
      subtitle="Register a company before recording its trades."
      onClose={onClose}
    >
      <form className="entry-form" onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            <span>
              Symbol <b>*</b>
            </span>
            <input
              autoFocus
              required
              maxLength={20}
              pattern="[A-Za-z0-9.-]+"
              placeholder="e.g. ESDS"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            />
          </label>
          <label className="field">
            <span>
              Exchange <b>*</b>
            </span>
            <input
              required
              maxLength={50}
              placeholder="e.g. NSE"
              value={exchange}
              onChange={(event) => setExchange(event.target.value)}
            />
          </label>
          <label className="field span-2">
            <span>
              Company name <b>*</b>
            </span>
            <input
              required
              maxLength={200}
              placeholder="Full company name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="field">
            <span>
              Sector <small>Optional</small>
            </span>
            <input
              maxLength={100}
              placeholder="e.g. Technology"
              value={sector}
              onChange={(event) => setSector(event.target.value)}
            />
          </label>
          <label className="field">
            <span>
              ISIN <small>Optional</small>
            </span>
            <input
              minLength={12}
              maxLength={12}
              placeholder="12-character code"
              value={isin}
              onChange={(event) => setIsin(event.target.value.toUpperCase())}
            />
          </label>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            className="button button-quiet"
            type="button"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="button button-primary"
            type="submit"
            disabled={busy}
          >
            {busy ? "Saving…" : "Add company"} <Icon name="arrow" size={17} />
          </button>
        </div>
      </form>
    </Modal>
  );
}

function TransactionDialog({
  companies,
  holdings,
  onClose,
  onSave,
}: {
  companies: Company[];
  holdings: Holding[];
  onClose: () => void;
  onSave: (value: TransactionInput) => Promise<void>;
}) {
  const [kind, setKind] = useState<"BUY" | "SELL">("BUY");
  const [symbol, setSymbol] = useState(companies[0]?.symbol ?? "");
  const [tradeQuantity, setTradeQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [charges, setCharges] = useState("0");
  const [date, setDate] = useState(todayLocal());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const options =
    kind === "SELL"
      ? companies.filter((company) =>
          holdings.some(
            (holding) =>
              holding.symbol === company.symbol && Number(holding.quantity) > 0,
          ),
        )
      : companies;
  const selectedSymbol = options.some((company) => company.symbol === symbol)
    ? symbol
    : (options[0]?.symbol ?? "");
  const available = Number(
    holdings.find((holding) => holding.symbol === selectedSymbol)?.quantity ??
      0,
  );
  const cashValue =
    Number(tradeQuantity) * Number(price) +
    (kind === "BUY" ? 1 : -1) * Number(charges);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSymbol)
      return setError("Choose a company with an available position.");
    if (
      !(Number(tradeQuantity) > 0) ||
      !(Number(price) > 0) ||
      Number(charges) < 0
    )
      return setError(
        "Enter a positive quantity and price, and non-negative charges.",
      );
    if (kind === "SELL" && Number(tradeQuantity) > available)
      return setError(`Only ${quantity(available)} shares are currently held.`);
    setError("");
    setBusy(true);
    try {
      await onSave({
        symbol: selectedSymbol,
        transaction_type: kind,
        quantity: tradeQuantity,
        price,
        transaction_date: date,
        charges,
      });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not record transaction.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Record a transaction"
      subtitle="Your transaction history drives every portfolio calculation."
      onClose={onClose}
    >
      <form className="entry-form" onSubmit={submit}>
        <div className="segmented" role="group" aria-label="Transaction type">
          <button
            type="button"
            className={kind === "BUY" ? "selected" : ""}
            onClick={() => {
              setKind("BUY");
              setError("");
            }}
          >
            Buy shares
          </button>
          <button
            type="button"
            className={kind === "SELL" ? "selected" : ""}
            onClick={() => {
              setKind("SELL");
              setError("");
            }}
          >
            Sell shares
          </button>
        </div>
        <div className="form-grid">
          <label className="field span-2">
            <span>
              Company <b>*</b>
            </span>
            <select
              required
              value={selectedSymbol}
              onChange={(event) => setSymbol(event.target.value)}
              disabled={options.length === 0}
            >
              {options.length === 0 && (
                <option value="">No available holdings</option>
              )}
              {options.map((company) => (
                <option value={company.symbol} key={company.id}>
                  {company.symbol} — {company.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>
              Quantity <b>*</b>
            </span>
            <input
              required
              type="number"
              min="0.0001"
              step="0.0001"
              inputMode="decimal"
              placeholder="0"
              value={tradeQuantity}
              onChange={(event) => setTradeQuantity(event.target.value)}
            />
          </label>
          <label className="field">
            <span>
              Price per share <b>*</b>
            </span>
            <div className="input-prefix">
              <span>₹</span>
              <input
                required
                type="number"
                min="0.0001"
                step="0.0001"
                inputMode="decimal"
                placeholder="0.00"
                value={price}
                onChange={(event) => setPrice(event.target.value)}
              />
            </div>
          </label>
          <label className="field">
            <span>
              Trade date <b>*</b>
            </span>
            <input
              required
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Charges</span>
            <div className="input-prefix">
              <span>₹</span>
              <input
                required
                type="number"
                min="0"
                step="0.0001"
                inputMode="decimal"
                value={charges}
                onChange={(event) => setCharges(event.target.value)}
              />
            </div>
          </label>
        </div>
        {kind === "SELL" && (
          <p className="field-hint">
            Available to sell: <strong>{quantity(available)} shares</strong>.
            The server checks dated holdings before saving.
          </p>
        )}
        {Number(tradeQuantity) > 0 && Number(price) > 0 && (
          <div className="trade-preview">
            <span>
              {kind === "BUY"
                ? "Estimated cash outflow"
                : "Estimated sale proceeds"}
            </span>
            <strong>{money(cashValue)}</strong>
          </div>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            className="button button-quiet"
            type="button"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="button button-primary"
            type="submit"
            disabled={busy || options.length === 0}
          >
            {busy ? "Saving…" : `Record ${kind.toLowerCase()}`}{" "}
            <Icon name="arrow" size={17} />
          </button>
        </div>
      </form>
    </Modal>
  );
}

function StatCard({
  label,
  value,
  detail,
  icon,
  tone = "default",
}: {
  label: string;
  value: string;
  detail: string;
  icon: "wallet" | "trend" | "layers" | "receipt";
  tone?: "default" | "green";
}) {
  return (
    <div className={`stat-card ${tone === "green" ? "stat-card-green" : ""}`}>
      <div className="stat-top">
        <span>{label}</span>
        <span className="stat-icon">
          <Icon name={icon} size={18} />
        </span>
      </div>
      <strong>{value}</strong>
      <span className="stat-detail">{detail}</span>
    </div>
  );
}

function cashFlow(transaction: Transaction): number {
  const gross = Number(transaction.quantity) * Number(transaction.price);
  return transaction.transaction_type === "BUY"
    ? -(gross + Number(transaction.charges))
    : gross - Number(transaction.charges);
}

function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [activityFilter, setActivityFilter] = useState<"ALL" | "BUY" | "SELL">(
    "ALL",
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [companies, transactions, portfolio] = await Promise.all([
        api.companies(),
        api.transactions(),
        api.portfolio(),
      ]);
      setData({ companies, transactions, portfolio });
    } catch (cause) {
      setLoadError(
        cause instanceof Error ? cause.message : "Could not load portfolio.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function saveCompany(value: CompanyInput) {
    await api.createCompany(value);
    setDialog(null);
    setNotice(`${value.symbol} added to your companies.`);
    await refresh();
  }

  async function saveTransaction(value: TransactionInput) {
    await api.createTransaction(value);
    setDialog(null);
    setNotice(
      `${value.transaction_type} transaction recorded for ${value.symbol}.`,
    );
    await refresh();
  }

  const companies = data?.companies ?? [];
  const holdings = data?.portfolio.holdings ?? [];
  const transactions = data?.transactions ?? [];
  const totalCost = holdings.reduce(
    (sum, holding) => sum + Number(holding.cost_basis),
    0,
  );
  const realised = Number(data?.portfolio.realised_pnl ?? 0);
  const sortedTransactions = useMemo(
    () =>
      [...transactions].sort(
        (a, b) =>
          b.transaction_date.localeCompare(a.transaction_date) || b.id - a.id,
      ),
    [transactions],
  );
  const filteredTransactions = sortedTransactions.filter((transaction) => {
    const matchesType =
      activityFilter === "ALL" ||
      transaction.transaction_type === activityFilter;
    const matchesQuery =
      `${transaction.symbol} ${companies.find((company) => company.symbol === transaction.symbol)?.name ?? ""}`
        .toLowerCase()
        .includes(query.toLowerCase());
    return matchesType && matchesQuery;
  });
  const allocated = [...holdings]
    .sort((a, b) => Number(b.cost_basis) - Number(a.cost_basis))
    .slice(0, 5);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" aria-label="FinSight overview">
          <span className="brand-mark">
            F<span>.</span>
          </span>
          <span className="brand-name">
            FinSight<small>PORTFOLIO INTELLIGENCE</small>
          </span>
        </a>
        <div className="sidebar-group-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <a href="#overview">
            <Icon name="grid" /> Overview
          </a>
          <a href="#holdings">
            <Icon name="layers" /> Holdings
          </a>
          <a href="#activity">
            <Icon name="activity" /> Activity
          </a>
          <a href="#companies">
            <Icon name="wallet" /> Companies
          </a>
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-note">
          <span className="note-spark">✦</span>
          <strong>Clarity compounds.</strong>
          <p>Every position starts with a well-recorded transaction.</p>
        </div>
        <div className="sidebar-footer">
          <span className="status-dot" /> LOCAL WORKSPACE <span>PHASE 02</span>
        </div>
      </aside>

      <main id="overview" className="main-content">
        <header className="topbar">
          <span className="breadcrumb">
            Workspace <span>/</span> <strong>Overview</strong>
          </span>
          <div className="topbar-right">
            <span className="today-label">{displayDate(todayLocal())}</span>
            <button
              className="icon-button refresh-button"
              onClick={() => void refresh()}
              aria-label="Refresh dashboard"
              title="Refresh dashboard"
            >
              <Icon name="refresh" size={18} />
            </button>
          </div>
        </header>
        <div className="page-content">
          <div className="hero">
            <div>
              <p className="eyebrow">
                YOUR INVESTMENT DESK <span className="eyebrow-line" />
              </p>
              <h1>
                Your portfolio,
                <br />
                <em>in focus.</em>
              </h1>
              <p className="hero-copy">
                A clear view of what you own, what you paid, and what you have
                realised.
              </p>
            </div>
            <div className="hero-actions">
              <button
                className="button button-primary"
                disabled={companies.length === 0}
                onClick={() => setDialog("transaction")}
              >
                <Icon name="plus" size={17} /> Record transaction
              </button>
              <button
                className="button button-outline"
                onClick={() => setDialog("company")}
              >
                <Icon name="plus" size={17} /> Add company
              </button>
            </div>
          </div>

          {loadError && (
            <div className="load-error" role="alert">
              <span>{loadError}</span>
              <button
                className="button button-outline"
                onClick={() => void refresh()}
              >
                Try again
              </button>
            </div>
          )}
          {loading && !data && (
            <div className="loading-state">Loading your portfolio…</div>
          )}

          {data && (
            <>
              <div className="section-heading">
                <div>
                  <p className="eyebrow">AT A GLANCE</p>
                  <h2>Portfolio snapshot</h2>
                </div>
                <span className="section-aside">
                  Based on recorded transactions
                </span>
              </div>
              <section className="stats-grid" aria-label="Portfolio summary">
                <StatCard
                  label="Open positions"
                  value={String(holdings.length).padStart(2, "0")}
                  detail={`${companies.length} registered ${companies.length === 1 ? "company" : "companies"}`}
                  icon="layers"
                />
                <StatCard
                  label="Remaining cost basis"
                  value={money(totalCost)}
                  detail="Capital tied to current holdings"
                  icon="wallet"
                  tone="green"
                />
                <StatCard
                  label="Realised P&L"
                  value={money(realised)}
                  detail="After transaction charges"
                  icon="trend"
                />
                <StatCard
                  label="Transactions"
                  value={String(transactions.length).padStart(2, "0")}
                  detail="Buys and sells recorded"
                  icon="receipt"
                />
              </section>

              <section className="overview-grid" aria-label="Portfolio detail">
                <div className="panel allocation-panel">
                  <div className="panel-heading">
                    <div>
                      <p className="eyebrow">THE BIG PICTURE</p>
                      <h2>Capital allocation</h2>
                    </div>
                    <span className="panel-caption">BY COST BASIS</span>
                  </div>
                  {allocated.length ? (
                    <>
                      <div className="allocation-total">
                        <span>Remaining invested capital</span>
                        <strong>{money(totalCost)}</strong>
                      </div>
                      <div className="allocation-list">
                        {allocated.map((holding, index) => {
                          const percent = totalCost
                            ? (Number(holding.cost_basis) / totalCost) * 100
                            : 0;
                          return (
                            <div
                              className="allocation-item"
                              key={holding.symbol}
                            >
                              <div className="allocation-info">
                                <span>
                                  <i
                                    className={`allocation-dot dot-${index}`}
                                  />
                                  {holding.symbol}
                                  <small>{holding.name}</small>
                                </span>
                                <strong>{percent.toFixed(1)}%</strong>
                              </div>
                              <div className="allocation-track">
                                <div
                                  className={`allocation-fill fill-${index}`}
                                  style={{ width: `${percent}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </>
                  ) : (
                    <div className="empty-panel">
                      <span className="empty-symbol">
                        <Icon name="layers" size={25} />
                      </span>
                      <h3>No open positions yet</h3>
                      <p>
                        Add a company and record a buy to see your allocation.
                      </p>
                      <button
                        className="text-link"
                        onClick={() =>
                          setDialog(
                            companies.length ? "transaction" : "company",
                          )
                        }
                      >
                        Get started <Icon name="arrow" size={16} />
                      </button>
                    </div>
                  )}
                </div>
                <div className="panel recent-panel">
                  <div className="panel-heading">
                    <div>
                      <p className="eyebrow">LATEST MOVES</p>
                      <h2>Recent activity</h2>
                    </div>
                    <a className="text-link" href="#activity">
                      View all <Icon name="arrow" size={16} />
                    </a>
                  </div>
                  {sortedTransactions.length ? (
                    <div className="recent-list">
                      {sortedTransactions.slice(0, 4).map((transaction) => (
                        <div className="recent-row" key={transaction.id}>
                          <span
                            className={`trade-icon ${transaction.transaction_type === "BUY" ? "buy-icon" : "sell-icon"}`}
                          >
                            <Icon
                              name={
                                transaction.transaction_type === "BUY"
                                  ? "plus"
                                  : "arrow"
                              }
                              size={17}
                            />
                          </span>
                          <span className="recent-text">
                            <strong>
                              {transaction.symbol}{" "}
                              <small>
                                {transaction.transaction_type === "BUY"
                                  ? "bought"
                                  : "sold"}
                              </small>
                            </strong>
                            <small>
                              {displayDate(transaction.transaction_date)} ·{" "}
                              {quantity(transaction.quantity)} shares
                            </small>
                          </span>
                          <strong className="recent-value">
                            {cashFlow(transaction) < 0 ? "−" : "+"}
                            {money(Math.abs(cashFlow(transaction)))}
                          </strong>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-panel">
                      <span className="empty-symbol">
                        <Icon name="receipt" size={25} />
                      </span>
                      <h3>No transactions yet</h3>
                      <p>Your latest buys and sells will appear here.</p>
                    </div>
                  )}
                </div>
              </section>

              <section id="holdings" className="section-block">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">WHAT YOU OWN</p>
                    <h2>Current holdings</h2>
                  </div>
                  <span className="section-aside">
                    Weighted average cost method
                  </span>
                </div>
                <div className="table-card">
                  <div className="table-overline">
                    <span>OPEN POSITIONS</span>
                    <span>
                      {holdings.length}{" "}
                      {holdings.length === 1 ? "POSITION" : "POSITIONS"}
                    </span>
                  </div>
                  {holdings.length ? (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Company</th>
                            <th>Quantity</th>
                            <th>Avg. purchase</th>
                            <th>Cost basis</th>
                            <th>Realised P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {holdings.map((holding) => (
                            <tr key={holding.symbol}>
                              <td>
                                <div className="company-cell">
                                  <span className="ticker-avatar">
                                    {holding.symbol.slice(0, 2)}
                                  </span>
                                  <span>
                                    <strong>{holding.symbol}</strong>
                                    <small>{holding.name}</small>
                                  </span>
                                </div>
                              </td>
                              <td>{quantity(holding.quantity)}</td>
                              <td>{money(holding.average_purchase_price)}</td>
                              <td className="cell-strong">
                                {money(holding.cost_basis)}
                              </td>
                              <td
                                className={
                                  Number(holding.realised_pnl) >= 0
                                    ? "positive-value"
                                    : "negative-value"
                                }
                              >
                                {money(holding.realised_pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="table-empty">
                      No open positions. Record a BUY transaction to begin.
                    </div>
                  )}
                </div>
              </section>

              <section id="activity" className="section-block">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">YOUR LEDGER</p>
                    <h2>Transaction history</h2>
                  </div>
                  <button
                    className="button button-outline button-small"
                    disabled={companies.length === 0}
                    onClick={() => setDialog("transaction")}
                  >
                    <Icon name="plus" size={16} /> New transaction
                  </button>
                </div>
                <div className="table-card">
                  <div className="table-toolbar">
                    <div className="search-box">
                      <Icon name="search" size={17} />
                      <input
                        aria-label="Search transactions"
                        placeholder="Search by company or symbol"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </div>
                    <div
                      className="filter-group"
                      role="group"
                      aria-label="Filter transactions"
                    >
                      {(["ALL", "BUY", "SELL"] as const).map((filter) => (
                        <button
                          key={filter}
                          className={activityFilter === filter ? "active" : ""}
                          onClick={() => setActivityFilter(filter)}
                        >
                          {filter === "ALL" ? "All trades" : filter}
                        </button>
                      ))}
                    </div>
                  </div>
                  {filteredTransactions.length ? (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Company</th>
                            <th>Type</th>
                            <th>Quantity</th>
                            <th>Price / share</th>
                            <th>Charges</th>
                            <th>Cash flow</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredTransactions.map((transaction) => (
                            <tr key={transaction.id}>
                              <td>
                                {displayDate(transaction.transaction_date)}
                              </td>
                              <td>
                                <strong>{transaction.symbol}</strong>
                              </td>
                              <td>
                                <span
                                  className={`type-pill ${transaction.transaction_type === "BUY" ? "buy-pill" : "sell-pill"}`}
                                >
                                  {transaction.transaction_type}
                                </span>
                              </td>
                              <td>{quantity(transaction.quantity)}</td>
                              <td>{money(transaction.price)}</td>
                              <td>{money(transaction.charges)}</td>
                              <td className="cell-strong">
                                {cashFlow(transaction) < 0 ? "−" : "+"}
                                {money(Math.abs(cashFlow(transaction)))}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="table-empty">
                      {transactions.length
                        ? "No transactions match this filter."
                        : "No transactions recorded yet."}
                    </div>
                  )}
                </div>
              </section>

              <section id="companies" className="section-block companies-block">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">YOUR COVERAGE</p>
                    <h2>Companies</h2>
                  </div>
                  <button
                    className="button button-outline button-small"
                    onClick={() => setDialog("company")}
                  >
                    <Icon name="plus" size={16} /> Add company
                  </button>
                </div>
                {companies.length ? (
                  <div className="company-grid">
                    {companies.map((company) => {
                      const holding = holdings.find(
                        (item) => item.symbol === company.symbol,
                      );
                      return (
                        <div className="company-card" key={company.id}>
                          <div className="company-card-top">
                            <span className="ticker-avatar large">
                              {company.symbol.slice(0, 2)}
                            </span>
                            <span
                              className={`company-state ${holding ? "state-held" : ""}`}
                            >
                              {holding ? "OPEN POSITION" : "NO POSITION"}
                            </span>
                          </div>
                          <strong>{company.symbol}</strong>
                          <span className="company-card-name">
                            {company.name}
                          </span>
                          <div className="company-card-meta">
                            <span>{company.exchange}</span>
                            <span>{company.sector || "Sector not set"}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="table-card table-empty">
                    No companies added yet. Add one to start recording trades.
                  </div>
                )}
              </section>

              <footer className="footer">
                <span>
                  FinSight <i>·</i> Portfolio clarity, one trade at a time.
                </span>
                <span>
                  Values shown in INR <i>·</i> No live market prices
                </span>
              </footer>
            </>
          )}
        </div>
      </main>
      {notice && (
        <div className="toast" role="status">
          <Icon name="check" size={18} />
          {notice}
          <button
            onClick={() => setNotice("")}
            aria-label="Dismiss notification"
          >
            <Icon name="close" size={15} />
          </button>
        </div>
      )}
      {dialog === "company" && (
        <CompanyDialog onClose={() => setDialog(null)} onSave={saveCompany} />
      )}
      {dialog === "transaction" && (
        <TransactionDialog
          companies={companies}
          holdings={holdings}
          onClose={() => setDialog(null)}
          onSave={saveTransaction}
        />
      )}
    </div>
  );
}

export default App;
