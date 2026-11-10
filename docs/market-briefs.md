# Indian market reviews

Almonds sends four market-wide Telegram reviews on **regular NSE equity trading days** in Asia/Kolkata:

| Review | Time | Delivery window | Contents |
| --- | --- | --- | --- |
| Morning | 8:45 am | Until 9:10 am | Prior-session benchmarks, domestic and global news, uncertain session view, possible drivers and what could change the view. |
| Updated morning brief | 11:00 am | Until 11:30 am | Current benchmarks, fresh domestic/global news and an updated outlook for the rest of today; saved as morning context for noon. |
| Noon | 12:30 pm | Until 1:00 pm | Current benchmarks, Bank Nifty, VIX, constituent breadth and sector moves; a review of the updated morning brief alongside a provisional comparison with the original pre-open view. |
| Closing | 4:00 pm | Until 5:00 pm | Reported closing observations, evidence-linked explanations, the final morning-view comparison, conditional next-session and longer-term scenarios. |

The scheduler checks on each clock minute and prepares each date/slot once, beginning at the configured time. Network collection and AI generation take additional time. A database uniqueness constraint and transaction lock prevent duplicate reports; the existing Telegram worker handles receipts, recoverable retries and uncertain outcomes. Expired reviews are superseded, never replayed later. Intraday observations that become more than 20 minutes old before delivery are superseded too. Each review is a single Telegram message under the size limit.

## Sources and limitations

- [NSE public index watch](https://www.nseindia.com/market-data/live-market-indices): Nifty 50, Bank Nifty, India VIX, Nifty 500 and selected sector indices. Numbers, previous-close references, source timestamps, ranges and percentage consistency are validated. Breadth means **Nifty 500 constituents**, not every Indian stock. Sector-index returns are not treated as index-point contributions. If that page cannot be read, index values come from NSE's official live MCP server instead ([details](nse-mcp.md)); Nifty 500 breadth is then omitted.
- [Moneycontrol Sensex](https://www.moneycontrol.com/indian-indices/sensex-4.html): an indicative publisher quote with a separately verified page identity and timestamp. BSE's public endpoint rejected automated access; the app does not bypass that protection. Quotes may be delayed. Noon comparisons require same-session, recent observations for both benchmarks. Closing comparisons require both source timestamps at or after 3:30 pm IST. Old observations retain their dates and cannot establish today's result.
- [NSE trading calendar](https://www.nseindia.com/resources/exchange-communication-holidays): refreshed daily from the equity-segment holiday feed. Weekends and declared holidays are excluded. A valid same-year cache may be used for up to seven days with a visible gap; without one, the scheduler waits for recovery. Special weekend or evening sessions, including Muhurat trading, do not use these regular-session alert times.
- News feeds: Economic Times Markets, Economy and Global Markets; Moneycontrol market reports; BBC Business and World; Federal Reserve releases; and RBI releases where access is permitted. Only dated, relevant headlines and short feed summaries enter the evidence snapshot. Publication dates after collection are excluded. Duplicate URLs/headlines and tracking parameters are removed. Coverage is bounded and **not an exhaustive global or national newswire**. Every brief reports source gaps; RBI currently rejects the automated robots-policy request and is therefore marked unavailable.
- RSS and HTML requests obey publisher robots policy, have fixed HTTPS hosts, timeouts and size bounds, and do not follow redirects. Source text is untrusted model input, never executable instructions.

The existing configured AI provider writes short claims linked to saved evidence IDs. Unknown citations, unsupported numbers, certain price predictions and unconditional forward scenarios are rejected. Reported explanations and possible contributing factors are labelled separately. A linked source and a simultaneous price move do not prove causation. Confidence is qualitative, never a calibrated win probability. Missing or invalid AI output leaves a transparent factual brief and an **unclear** view rather than an invented forecast. Major index/source gaps also prevent scoring.

## Comparing the morning view

The original 8:45 am pre-open report, evidence cutoff, prior-close baseline levels and direction threshold are immutable. There is no after-open morning backfill and previews never become forecasts. A midmorning/noon/closing comparison requires a confirmed Telegram delivery of that same session's morning report before the 9:15 am open. Missing or uncertain delivery, missing benchmarks, stale prices, a conflicting previous-close reference or an unclear morning view produces **unscored**.

The default predeclared direction rule is **0.30%** from the frozen prior-close levels for both Nifty 50 and Sensex:

- **UP:** both returns are at least +0.30%.
- **DOWN:** both returns are at most −0.30%.
- **RANGE:** both remain strictly inside ±0.30%.
- **MIXED:** the two benchmarks do not meet the same direction rule.

An observed direction matching the saved morning expectation is **supported**; a different common direction is **not supported**; benchmark disagreement is **mixed**. Threshold checks use unrounded returns, with four-decimal returns retained in the assessment. Midmorning and noon are always provisional. A separate evidence-linked reasoning check discusses whether the originally proposed drivers are corroborated, contradicted or still unverified; direction alone does not confirm them. This evaluates a broad directional scenario, not forecast magnitude, timing, economic causality or trading profitability. The API's small accuracy sample includes only scored closing reviews, never the interim morning/noon checks.

## Memory and next-day use

The 11:00 am alert is an updated morning brief. Noon receives that same session's saved news, source gaps, outlook and report identity, even if those headlines have left the live feeds. Its reasoning check must cite the updated morning brief and current evidence, plus the original pre-open brief when available. The noon message identifies the updated brief's time. A missing update is stated explicitly; another day's or a later report is never substituted. The original pre-open direction baseline and closing accuracy remain separate from this updated news context.

The next morning receives the previous available closing report (up to 20 calendar days old), its original dated scenarios and its forecast assessment alongside fresh overnight evidence. This is stored context for reconsideration, not model retraining or a promise of improving returns. A missed closing review is not fabricated. Today’s midmorning/noon/closing review always refers to today’s frozen morning report, never another day's forecast.

## Configuration and review

```dotenv
MARKET_BRIEFS_ENABLED=true
MARKET_BRIEF_TIMES_IST=08:45,11:00,12:30,16:00
MARKET_BRIEF_SOURCE_TIMEOUT_SECONDS=12
MARKET_BRIEF_AI_TIMEOUT_SECONDS=90
MARKET_BRIEF_DIRECTION_THRESHOLD_PCT=0.3
```

`TELEGRAM_ENABLED` must also be enabled and connected. The app, PostgreSQL and the Mac must remain running and awake for scheduled delivery; this feature does not create a cloud scheduler. Company-event, Gold and IPO alerts keep their independent settings.

- `GET /api/market-briefs`: schedule, calendar health, recent reviews, delivery status and the closing-only accuracy count.
- `GET /api/market-briefs/reports/{id}`: original saved evidence, analysis, assessment and rendered message.
- `POST /api/market-briefs/preview?slot=morning|midmorning|noon|close`: current evidence and a marked preview; saves no report, queues no message and cannot manufacture a historical baseline.

For development, apply Alembic through `0028_market_briefs` and restart. For the installed macOS app, `python3 ops/prepare-market-briefs.py` stages the narrow addition at `/tmp/almonds-market-briefs-update`, preserving the installed Codex provider, Telegram inbox and existing company-alert filter. After reviewing the staged source and tests, `python3 ops/prepare-market-briefs.py --install` verifies hashes, backs up prior files, restarts the service and checks the market-review API. Startup applies the additive migration. No private environment file is copied.

Verification: `backend/.venv/bin/python -m pytest backend/tests/test_market_briefs.py backend/tests/test_telegram.py backend/tests/test_gold.py backend/tests/test_ipos.py -q`.

Useful later additions are a licensed GIFT Nifty/global-index/Brent/yield feed and published FII/DII flows. Those inputs should carry their actual session and publication times; a previous-day flow figure must not be presented as the cause of an intraday move. No additional unscheduled alerts are enabled by this feature.
