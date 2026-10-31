# Mainboard IPO research

Open **IPOs** in the sidebar. This section works independently of holdings. Upcoming and open mainboard issues are sorted numerically by **GMP ÷ upper price band × 100**, descending. Zero and negative GMP retain their values; missing quotes appear last. SME issues and FPOs are excluded.

## Collection and alerts

The scheduler checks every five minutes and performs one discovery/research pass per day after **08:00 Asia/Kolkata**. Its first successful weekly pass sends a Monday shortlist, with catch-up if Almonds was asleep/offline. The weekly horizon is the next 14 days. Every discovered issue is refreshed and screened daily, including the day before opening. A qualifying company gets one pre-opening review per opening date, with an opening-morning catch-up before 09:00 IST if needed. Changed dates create a new review identity. Unconfirmed dates stay visible but cannot trigger an alert.

Only qualifying issues are sent; an empty shortlist sends nothing. Weekly messages are ordered by descending GMP percentage, with one numbered message part per company. Pre-opening reviews use the same ordering. **Refresh IPOs** also queues due briefs when monitoring and Telegram are enabled, using the same deduplication keys.

The existing Telegram worker handles HTML escaping, multipart receipts, retries and uncertain outcomes. IPO messages have independent queue identities and do not require company-event records. Before delivery, the worker checks expiry, changed dates, and latest stored eligibility. Briefs expire at the earliest of opening day at 09:00 IST, the quote's freshness limit, or 24 hours after preparation. Successful parts are never resent. `UNCERTAIN` sends are not automatically replayed.

Almonds and PostgreSQL must be running, the Mac must be awake, and Telegram must be configured. Source failures preserve stored records and retry after 30 minutes. Failed company details exclude that issue from alerts and also retry. Startup catches up using durable history. There is no external cloud scheduler.

## Sources and screen

The adapter reads InvestorGain's [mainboard dashboard](https://www.investorgain.com/ipo-dashboard/mainline/), [GMP report](https://www.investorgain.com/report/ipo-gmp-live/331/), and company pages. It respects robots policy, uses HTTPS with a fixed host, rejects redirects, and bounds response size and company count. Broken schemas produce explicit gaps, never fixture data. Coverage is limited to those public tables, rather than an exhaustive exchange-certified calendar. Company data is third-party research; original RHP links are included where supplied. GMP is unofficial.

The deterministic attractiveness screen requires:

- Mainboard classification, confirmed opening/closing dates, sector and business overview.
- Upper price, lot size, total issue size, fresh issue and OFS. Fresh + OFS must reconcile within ₹0.10 crore. A missing component becomes zero only if the other equals the entire issue.
- GMP at least **10%**, with a quote timestamp at most **24 hours** old. Missing quotes stay unknown; future-dated quotes are rejected.
- Positive total income and PAT in two consecutive annual periods, neither declining. Interim and annual figures are never compared. The adapter recognizes March 31 annual periods in crore-denominated source tables; other fiscal calendars remain insufficient data.
- Positive annual EPS, explicitly tied by the source note to the same annual period, with annual P/E at most **40×**. Annual financials older than 550 days are excluded. Annualised interim post-IPO EPS is not used.

Borrowings/net worth above 2× and OFS of at least 75% are flagged as risks, requiring sector/company context. Non-positive reported net worth rejects the issue. Missing leverage is flagged. This is a rules-based research filter, not an AI recommendation or sector-relative valuation. It does not extract the full RHP or audit all company-specific risks.

Telegram briefs use short sections: GMP and quote time; dates, price, minimum application and issue/fresh/OFS size; annual profit/income growth, P/E and proceeds; then a short risk line and source/RHP links. Proceeds use concise categories instead of clipped source paragraphs. Material high leverage and high OFS are highlighted in the risk line. The dashboard retains the full company overview, financial figures, ROE, leverage, exclusions and missing data.

## Configuration and API

```dotenv
IPO_MONITOR_ENABLED=true
IPO_CHECK_HOUR_IST=8
IPO_MIN_GMP_PCT=10
IPO_MAX_PE=40
IPO_GMP_MAX_AGE_HOURS=24
```

The check hour accepts 0–8, preserving a pre-opening opportunity. Telegram uses the existing bot and destination. `IPO_MONITOR_ENABLED=false` pauses discovery and IPO delivery; manual research refresh remains available. `TELEGRAM_ENABLED=false` pauses all Telegram delivery.

- `GET /api/ipos`: ranked research, source/error status, criteria, schedule and recent delivery outcomes.
- `POST /api/ipos/refresh`: refresh research and enqueue eligible due alerts; returns `FAILED` with retained data on source failure, or `BUSY` during another PostgreSQL refresh.

Apply Alembic through `0026_mainboard_ipos` and rebuild before restarting. Migration `0025_telegram_queries` retains the installed database's existing predecessor history; IPOs do not alter its tables. The installed app has newer Telegram/Codex features than this checkout. Use the targeted IPO overlay rather than replacing it wholesale.

The initial installed update was prepared with `python3 ops/prepare-ipo-update.py`, verified in `/tmp/almonds-ipo-update`, then applied with `--install`. This helper backs up changed files and preserves the installed configuration and other features. It deliberately refuses a second initial installation; future updates should review the installed source before preparing another overlay.

Verification: `cd backend && .venv/bin/python -m pytest tests/test_ipos.py tests/test_telegram.py tests/test_intelligent_earnings.py -q`; `cd frontend && npm run build`.
