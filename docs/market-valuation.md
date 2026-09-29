# Phase 6 market data and valuation

Almonds stores raw dated observations, then calculates valuation in Python using its verified financial facts. `MarketDataProvider` is a replaceable interface with official NSE MCP, manual, and optional Alpha Vantage implementations. Migration `0010_market_valuation_history` preserved existing manual snapshots; `0011_market_ohlcv_reference` adds OHLCV and a separate Market Lens reference table. Saving the same provider, origin and date is idempotent; different dates form history.

## Market source and refresh

The preferred provider is `nse_mcp`. Official [NSE MCP documentation](https://www.nseindia.com/nse-mcp) lists `https://mcp.nseindia.in/bhavcopy/cm/mcp` for Bhavcopy and `https://mcp.nseindia.in/cmmkt/mcp` for the 1–3 minute delayed CM feed. Its setup says **No Auth**. Almonds uses Bhavcopy only: the MCP tools discovered from the server are `get_corporate_actions` and `get_stock_history`, with a maximum of three months per history call and `next_end_date` pagination. This returns dated **unadjusted** daily OHLCV, from up to five years of available history. The optional delayed CM quote tool was verified but is not used for stored EOD valuation; on 28 September 2026 it returned “No allstocks data available” for ESDS. NSE describes Bhavcopy as updating from the previous trading day's final numbers, but observed availability can lag. The live ESDS response was last dated 23 September 2026 on the 28th. The UI must display the actual date and origin.

NSE says MCP data is for informational and educational, noncommercial use, and does not license commercial exploitation or AI training. No numeric rate limit is published on its MCP page. Almonds makes bounded, user initiated history calls and an optional single EOD refresh per weekday. It never scrapes NSE web pages or bypasses bot protection. Set `MARKET_DATA_ENABLED=true` to schedule refreshes, with `MARKET_DATA_REFRESH_HOUR` and `MARKET_DATA_REFRESH_MINUTE` in India time. Historical requests use locally stored dates where covered and fetch missing edge ranges. Old observations survive timeouts, rate limits and malformed responses. The scheduler is disabled by default.

Manual entry remains available: enter a dated close and HTTPS source in Financials. Source P/E and legacy PEG are reference values only. To use the optional secondary Alpha Vantage source, add a six digit BSE code and set `MARKET_DATA_PROVIDER=alpha_vantage` and `ALPHA_VANTAGE_API_KEY=...`. It is a third-party free EOD source with limited compact history and rate limits. Set `MARKET_DATA_PROVIDER=manual` for manual-only operation.

Every observation records provider, origin (MANUAL/EOD), price date, observed time, fetched time and source URL. NSE also supplies raw open, high, low, close and volume; its history is unadjusted for corporate actions and Almonds records that warning. The UI displays age and flags prices older than seven days. Market cap requires a separately sourced manual value or shares outstanding; it is never invented. An NSE EOD close alone can therefore produce P/E when eligible EPS exists, but not market cap based ratios.

## Point-in-time financial selection

TTM revenue, PAT, EBITDA, EBIT, operating cash flow and capex normally require four consecutive validated quarters of the same scope and verified source facts. For Q1, a validated FY annual result plus current Q1 minus prior Q1 provides equivalent trailing-year coverage; the method and all three periods are recorded. EBITDA combines PBT + finance costs + depreciation for each covered period. Free cash flow is operating cash flow minus absolute capex. TTM EPS uses diluted EPS only when the source periods have positive PAT and EPS, implied share counts are reasonably stable, and no split or bonus event demands adjustment. Missing, incompatible or review-required facts block the corresponding result. An annual total is never used on its own as the current TTM.

For a market date, only filings available by the 15:30 IST close are eligible. A date-only publication becomes eligible the next day because its clock time is unknown. A valuation on 15 June may use a 1 April filing but cannot use a 1 July filing. Market date, eligible period IDs, fact IDs, formulas and scope are retained with each metric. Recalculation after a historical fact correction updates the snapshot and its `updated_at` time.

## Formulas and statuses

| Metric | Formula and requirements |
| --- | --- |
| P/E TTM | Close / reliable positive diluted TTM EPS. Zero or negative EPS gives `NOT_MEANINGFUL` or unavailable. |
| P/S TTM | Market cap / positive TTM revenue. Market cap is sourced or close × sourced shares / 10 million, in ₹ crore. |
| Earnings yield | Diluted TTM EPS / close × 100. |
| Enterprise value | Market cap + latest verified debt − latest verified cash. Missing debt or cash blocks EV. |
| EV/Sales, EV/EBITDA | EV / positive TTM revenue or EBITDA. Nonpositive EBITDA is not meaningful. |
| P/B | Market cap / positive latest verified equity. |
| FCF yield | TTM free cash flow / positive market cap × 100. Negative yield remains negative and is labeled. |
| Forward PEG | Almonds-calculated P/E / positive forward EPS growth in percentage points. Growth type and source URL remain traceable. A user assumption is labeled `USER_ASSUMPTION`, never consensus. |

Each metric has a value or null, status, formula, input evidence IDs and reason when unavailable. The dedicated Valuation section opens these details and shows P/E and raw close history. No historical valuation sees future results. Historical valuation is recalculated from the currently verified record set; correcting or revoking an old fact can change a prior snapshot. The system does not yet version every correction as a separate historical knowledge state.

## Scenario analysis

Scenarios use **user assumptions**, not AI forecasts. Future EPS = current reliable TTM EPS × (1 + assumed annual growth)^years. Implied future price = future EPS × terminal P/E. Total return = implied price / current price − 1; annualized return = (implied price / current price)^(1/years) − 1. The UI submits a neutral Scenario A; the API accepts up to three user-named cases. Reverse growth solves required EPS CAGR for a target annual return, terminal P/E and horizon: target price = current price × (1 + target return)^years; required future EPS = target price / terminal P/E; required EPS CAGR = (required future EPS / current EPS)^(1/years) − 1. It is labeled **implied / required growth**, never forecast growth.

The reverse-expectations table evaluates 0%, 10%, 15%, and the entered annual-return target using the user's terminal P/E and horizon. It does not silently choose a terminal multiple. Where two published, verified diluted EPS figures exist for equivalent quarters, Python compares each required CAGR with the latest quarter's EPS YoY growth in percentage points. A single quarter's EPS growth is not a durable forecast; historical CAGR and sourced forward growth remain separate missing inputs. PAT or revenue growth is not substituted for EPS growth. A price more than seven calendar days old receives a dated warning beside the valuation.

Valuation change decomposes two dated, comparable valuations: price change = new price / old price − 1; EPS change = new TTM EPS / old TTM EPS − 1; multiple change = new P/E / old P/E − 1. It reports inputs and dates. Since NSE historical prices are unadjusted, split and bonus windows need extra review before interpreting price change as investor return.

The change endpoint returns `status: CALCULATED` when both dates have comparable positive TTM EPS and P/E. If an older price predates an eligible filing, it returns HTTP 200 with `status: INSUFFICIENT_DATA`, a reason, dates, and missing inputs. The UI selects the earliest and latest dates that both have calculated P/E. It explains when there are fewer than two comparable dates instead of repeatedly requesting an impossible comparison. For ESDS, the 4 September price predates the eligible FY2026 annual filing, so it cannot be compared with the 28 September valuation using TTM P/E.

## Market Lens reference CSV

[NSE Market Lens's official guide](https://marketlens.nseindia.com/screener-guidelines) documents a **Download CSV** action for displayed columns and filtered rows. Almonds imports a user-supplied CSV only, with an explicit export date and source URL. Supported column aliases are `Stock`/`Symbol`/`Ticker`, `P/E`, `P/B`, `Market Cap`, `P/S`, `ROE`, `ROCE`, `Debt to Equity`, and `Revenue Growth`. Unsupported columns are ignored; unknown companies are counted as skipped. `reference_metrics` is separate from calculated valuations. Comparable metrics are flagged `MATCH` within `MARKET_LENS_TOLERANCE_PCT` (default 5%) or `REVIEW_REQUIRED`; unmatched metrics remain `NOT_COMPARABLE`. The user should review dates, scope and methods before treating a difference as an error. There is no Market Lens scraping or undocumented API use.

## API examples

```sh
curl http://127.0.0.1:8000/api/companies/ESDS/market
curl http://127.0.0.1:8000/api/companies/ESDS/market/history
curl -X POST http://127.0.0.1:8000/api/companies/ESDS/market/refresh
curl 'http://127.0.0.1:8000/api/companies/ESDS/valuation?scope=CONSOLIDATED'
curl 'http://127.0.0.1:8000/api/companies/ESDS/valuation/history?scope=CONSOLIDATED'
curl -X POST http://127.0.0.1:8000/api/companies/ESDS/valuation/scenario -H 'Content-Type: application/json' -d '{"scope":"CONSOLIDATED","assumptions":[{"name":"Scenario A","eps_growth_pct":"20","terminal_pe":"30","years":3}]}'
curl -X POST http://127.0.0.1:8000/api/companies/ESDS/valuation/reverse -H 'Content-Type: application/json' -d '{"terminal_pe":"30","years":3,"target_annualized_return_pct":"12"}'
curl 'http://127.0.0.1:8000/api/companies/ESDS/valuation/change?old_market_snapshot_id=2&new_market_snapshot_id=7'
curl http://127.0.0.1:8000/api/companies/ESDS/valuation/reference
```

`PUT /api/companies/{symbol}/market-snapshot` remains the manual fallback. `POST /api/companies/{symbol}/valuation/recalculate?market_snapshot_id=...&scope=...` computes a chosen historical date. `POST /api/market-lens/import` accepts JSON with `csv_text`, `as_of_date`, and an official Market Lens `source_url`.

## ESDS and limitations

The live official NSE MCP integration stored 12 ESDS EOD observations dated 4–23 September 2026; incremental backfill added 4–11 September without redownloading the stored 15–23 September dates. The 23 September record has close ₹1,770.70, open ₹1,724.85, high ₹1,811.05, low ₹1,706.45 and volume 3,048,927. At that market date, the FY2026 annual filing dated 24 September was not public, so the engine correctly leaves TTM and ratios unavailable rather than using future information. The user-entered 28 September observation is newer and remains the current display price, clearly marked `MANUAL`: ₹1,672.50 close and ₹19,604 crore sourced market cap. The official PDF's FY2026 audited annual column was revalidated from reconciled component rows. The point-in-time bridge uses FY2026 annual + Q1 FY2027 − Q1 FY2026, all consolidated: TTM revenue ₹481.280 crore (₹472.210 + ₹133.657 − ₹124.587); PAT ₹124.415 crore (₹120.823 + ₹29.277 − ₹25.685); EBITDA ₹246.593 crore; diluted EPS ₹12.10. Calculated P/E is 138.223× (₹1,672.50 / ₹12.10), P/S is 40.733× (₹19,604 / ₹481.280) and earnings yield is 0.7235%. The separately entered source P/E of 163 differs and remains a reference; its EPS period and methodology should be checked. No Market Lens CSV was supplied, so no Market Lens comparison exists. Forward PEG lacks a sourced growth estimate. Verified debt and cash facts are absent, so EV/EBITDA and EV/Sales remain unavailable. No ratio is filled from a third-party reference.

Known limits: no corporate-action adjustment engine, no sourced shares API, no automated management/consensus forward estimates, no adjusted historical close, and no return model for dividends. NSE MCP availability and terms can change; no rate limit was documented on the official page. Recalculating historical valuation after fact corrections replaces prior calculation details rather than versioning every revision. Before an IPO/listing date, repeated backfill requests can retry the unavailable range because the app does not store empty coverage windows.

The local Ollama analyst timed out during the ESDS integration check. Its deterministic, evidence-cited fallback now runs for provider timeout/unavailability as well as invalid model output; the failure reason remains on the saved record for transparency. This fallback interpreted the 138.22× calculated P/E against positive YoY but weak QoQ results and stated that PEG is unavailable without a sourced growth estimate.
