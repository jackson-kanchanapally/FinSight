# NSE market data (official MCP)

Almonds reads exchange data from the National Stock Exchange's official [Model Context Protocol](https://www.nseindia.com/nse-mcp) servers. Both are public, need no API key, and speak Streamable HTTP:

| Server | Endpoint | Data |
| --- | --- | --- |
| Bhavcopy (EOD) | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | Official end-of-day Bhavcopy for ~5,000 equities over five years, index history and valuation ratios, corporate actions. Updated after the close. |
| CM Market Live | `https://mcp.nseindia.in/cmmkt/mcp` | Cash-market quotes, ~140 live indices, top gainers/losers. Refreshed every 1–5 minutes during 09:15–15:30 IST. |

NSE describes the service as informational and educational. It is not intended for trading, commercial redistribution, or training AI models, and NSE does not warrant accuracy or timeliness. Almonds uses it for personal research and labels every view with that disclaimer.

## What you get

### Markets page

Open **Markets** in the sidebar.

- **Key indices**: Nifty 50, Bank, Next 50, Midcap 100, Smallcap 100, IT, and India VIX, with change versus the previous close.
- **Breadth**: advancing and declining mainboard stocks, plus how many equity indices rose.
- **India VIX**: the level, its 1-week and 1-month direction, and its percentile within the past year.
- **Nifty 50 valuation**: current P/E against its five-year min, median, and max, with P/B and dividend yield.
- **Sector rotation**: the best and worst equity indices over 1D, 1W, 1M, 3M, 6M, or 1Y.
- **Top movers**: the live top gainers and losers across all NSE securities.
- **Stock research**: search any NSE symbol or company name and open a one-page read:
  - live quote and three-month price line
  - 52-week range
  - 20/50/200-day moving averages and the 50/200 relationship
  - 30-session volume profile
  - 12-month return, beta, and correlation versus the Nifty 50
  - trailing-12-month dividend yield, plus upcoming and recent corporate actions
- **Holdings vs the market**: each open position's price, day move, distance from its 52-week high, 1-year return versus the Nifty 50, beta, and weight. Also shows a value-weighted portfolio beta.

Links of the form `#markets/SYMBOL` open the research card directly. Holding symbols in **Current holdings** link there.

### Portfolio marked to market

**Overview** shows the market value, the day's change, and unrealised P&L of open positions. **Current holdings** adds price, market value, and unrealised P&L per position. Live quotes are used when available. Any symbol the live server cannot quote falls back to the latest Bhavcopy close, labelled `EOD`. Both views refresh every minute while the market is open. Recorded-trade accounting (cost basis, realised P&L) is unchanged.

### Add-company symbol check

With exchange `NSE`, the **Add company** form looks up the symbol as you type. It confirms a listed symbol with its last close, warns when a symbol has stopped trading (for example `HDFC` after its merger), and suggests close matches.

### Market brief fallback

Market briefs still read NSE's public index page first. If that request fails, they use the live MCP index values (Nifty 50, Bank Nifty, India VIX, and sectors) instead of reporting the benchmarks as unavailable. Nifty 500 breadth is not available from the MCP, so it is omitted in that case.

### Splits and bonuses (share basis)

For every registered NSE company, Almonds stores NSE corporate actions and their adjustment factors. Valuation uses them to put EPS and price on the same share basis. See [market valuation](market-valuation.md#splits-bonuses-and-share-basis). `GET /api/companies/{symbol}/corporate-actions` shows the stored actions and the confirmed window.

### Market reaction to events

For each material event (types in `PRICE_REACTION_EVENT_TYPES`) of an NSE company, Almonds records:

- **Base session**: the last NSE close before the event was public. A filing time-stamped before 15:30 IST is first reflected in that day's close; a later one in the next session's. A date-only filing is measured from the previous session, and the panel says its first window may include a move before the filing.
- **Windows**: close-to-close returns 1, 5 and 20 sessions after the base, with any split or bonus removed using NSE factors.
- **Excess return**: the stock's return minus the Nifty 50's over the same sessions. NSE's index history currently begins in October 2024, so older events show only the stock's return.
- **First-session volume** against the prior 20-session average.

Status is `PENDING` until 20 sessions have passed, then `COMPLETE`. `UNAVAILABLE` means the event is older than NSE's five-year window, no close exists just before it, or splits could not be confirmed yet (retried). A background job runs every `PRICE_REACTION_POLL_MINUTES` and measures new events. It re-measures pending ones every 6 hours and retries transient failures after 20 hours.

Where reactions appear:

- **Event details**: a "What the share price did" panel.
- **Markets → stock card** (registered companies): median reaction versus the Nifty 50 per event type, with how many events beat it. **Measure now** runs the job for that company.
- **Quarterly AI analyst**: the results filing's reaction is an `X` evidence item. The validator accepts its numbers and dates. The evidence rules require it to be quoted as what the market did, never as a cause or as proof of result quality. The existing causal-claim check still applies.

Medians over a handful of events describe the past; they do not predict the next reaction.

### Valuation prices

The existing `nse_mcp` price provider (`MARKET_DATA_PROVIDER=nse_mcp`) continues to fetch dated, unadjusted EOD closes for valuation. See [market valuation](market-valuation.md).

## Reading the numbers correctly

- **Unadjusted prices.** Bhavcopy closes, the 52-week range, moving averages, and price history are not adjusted for splits or bonuses. When a split or bonus ex-date falls in the last ~400 days, the research card shows a warning naming the event. Relative return and beta come from NSE's `get_stock_vs_index` tool, which applies NSE's corporate-action adjustments.
- **Market hours versus data date.** The session badge uses the clock for regular hours, then checks the data's own date. On a weekday holiday, or while the feed is delayed, it shows **Showing last session** instead of **Market open**.
- **Day change** for a holding is quantity × (price − previous close). It ignores whether shares were bought today.
- **Dividend yield** adds the rupee amounts in NSE dividend purposes with ex-dates in the last 365 days, then divides by the current price. Special dividends are included.
- **Top movers** are often circuit-limited small caps; check liquidity before reading into them.
- Nothing here is a recommendation.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/nse/market-pulse?sector_period=1D` | Indices, breadth, VIX, Nifty valuation, sector and stock movers |
| `GET /api/nse/portfolio` | Open positions marked to market, with beta and 1-year relative return |
| `GET /api/nse/stocks/{symbol}` | One-stock research card |
| `GET /api/nse/search?q=tata` | Symbol search; flags inactive symbols |
| `GET /api/nse/status` | Whether each server responds, and the tools it currently exposes |
| `GET /api/companies/{symbol}/corporate-actions` / `POST …/refresh` | Stored NSE corporate actions and the confirmed share-basis window |
| `GET /api/companies/{symbol}/price-reactions` / `POST …/refresh` | Event reactions and per-type medians; refresh measures now |
| `GET /api/events/{id}/price-reaction` | One event's reaction (or `null`) |

Partial failures do not fail a request. Each response carries an `errors` list naming the unavailable pieces. Money fields are decimal strings.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `NSE_MCP_ENABLED` | `true` | Master switch for the Markets page, live holdings, symbol check, and brief fallback. |
| `NSE_MCP_BHAVCOPY_URL` | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | EOD server (HTTPS required). |
| `NSE_MCP_LIVE_URL` | `https://mcp.nseindia.in/cmmkt/mcp` | Live server (HTTPS required). |
| `NSE_MCP_TIMEOUT_SECONDS` | `12` | Per-request timeout. |
| `NSE_MCP_LIVE_CACHE_SECONDS` | `60` | How long live results are reused. |
| `NSE_MCP_EOD_CACHE_MINUTES` | `30` | How long Bhavcopy results are reused. |
| `NSE_MCP_MAX_CONCURRENCY` | `4` | Concurrent requests to NSE per process. |
| `PRICE_REACTIONS_ENABLED` | `true` | Background corporate-action sync and event reaction measurement. |
| `PRICE_REACTION_POLL_MINUTES` | `30` | How often the job looks for due work. |
| `PRICE_REACTION_BATCH_SIZE` | `150` | Events measured per run at most. |
| `PRICE_REACTION_EVENT_TYPES` | Results, orders, M&A, capex, management/auditor changes, dividends, splits/bonuses, buybacks, fund raises, ratings, promoter/pledge, regulatory, litigation | Event types that get a reaction. |

## Implementation notes

`backend/app/market/nse_mcp.py` is a small dependency-free client built on `httpx`. It:

- keeps one session per server and handles both JSON and `text/event-stream` responses
- re-initialises an expired session once
- treats tool payloads of the form `{"error": …}` as failures, because NSE returns them with `isError: false`
- caches results and bounds concurrency

`backend/app/services/nse_insights.py` builds the investor views. Tool names and schemas can change; `GET /api/nse/status` lists what each server currently offers. Tests use an in-process fake server (`backend/tests/test_nse_mcp.py`) and never contact NSE.
