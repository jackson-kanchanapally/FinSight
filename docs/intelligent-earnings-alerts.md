# Intelligent earnings alerts

## Pipeline audit and changes

The implementation extends the existing pipeline; it adds no paid service, alternate extractor, or separate financial analyst.

| Stage | Existing implementation | Gap addressed |
| --- | --- | --- |
| Detection | `ingestion/monitor.py`, `sources/nse.py`, `sources/esds_ir.py` | A normalized authoritative result is persisted in `result_discoveries` before PDF download. A failed download or metadata conflict cannot hide a newer detected result. Per-company source-check status and coverage are retained. |
| Document storage | `events/event_service.py`, downloader, SHA-256 storage, numbered pages | Existing source references and hash deduplication remain. Hash equality is never treated as issuer-authenticity proof. |
| Extraction | `financials/extractor.py`, `financials/service.py` | Company/period/basis checks, reported precision, attributable-profit reconciliation, explicit exceptional-item signs, and additional supported rows feed the same fact tables. Conflicting revisions retain the original facts and an independent verification assessment. |
| Calculations | `financials/calculator.py`, `investor_evidence.py` | Compatible QoQ/YoY windows, tax rate/change, PBT/tax and detailed PAT bridges, cash conversion, FCF and balance-sheet ratios carry input IDs and formulas. Unsupported measures remain unassessed. |
| Thesis | `ai/analyst/context_builder.py`, `quarterly_packet.py`, `service.py`, existing validators | Earnings notifications now use the financial analyst, not the short event-PDF analyst. Structured profit drivers, source quality, freshness, previous thesis and a documented confidence rubric enter the existing prompt. Existing analyses remain immutable. |
| Persistence | Existing fact, metric and analysis tables; `telegram_deliveries` | The delivery stores the structured DTO, financial-analysis ID, formatted parts, stable idempotency key and individual receipts. Non-earnings event briefs remain supported. |
| Delivery | `telegram_alerts.py`, `telegram_client.py` | HTML escaping, deterministic section splitting, checkpointed multipart receipts, safe retry and an explicit `UNCERTAIN` state replace blind replay after ambiguous sends. |
| Reports/dashboard | Existing financial APIs and React views | Reporting basis, independent authenticity and stored/detected freshness are separate. Signed report routes offer scoped, expiring access when an HTTPS address is configured. |

## Financial definitions and lineage

Only validated periods and eligible source facts with matching units and source-column dates enter the alert. Comparisons require the same company, reporting basis and period type, canonical start dates, and the exact prior-quarter or prior-year end. A half-year statement is not compared as a quarter. Growth uses `(current - prior) / abs(prior) × 100`; zero or missing denominators remain unavailable. Negative-base growth is arithmetic, not a claim of durable improvement.

Reported EBITDA is kept distinct from the existing **PBT + finance costs + depreciation** proxy. The latter can include other income and exceptional items and is explicitly labelled a proxy; it is not presented as reported operating EBITDA. Reported and proxy EBITDA definitions are not mixed across comparisons.

Effective tax rate uses tax expense / positive PBT. Rate changes use percentage points. Total PAT is reconciled with PBT less taxes, and attributable profit plus non-controlling interest is checked when both are present. The detailed PAT bridge separates revenue, other income, expenses, tax, and explicitly signed exceptional items when their contributions reconcile. A PBT/tax bridge is used when detailed operating inputs are unavailable. Arithmetic contributions do not establish business causes.

Newly extracted facts preserve half the least printed unit as normalized `rounding_tolerance`. Reconciliation uses the sum of available printed tolerances; older records without precision retain the conservative legacy tolerance of ₹0.03 crore. Residuals and tolerance remain in the evidence report. Differently sourced or restated figures do not silently replace existing facts.

Cash conversion requires same-period OCF and positive PAT. FCF requires OCF and a supported capex outflow convention. Negative capex is accepted as a signed outflow only with an explicit purchase/capital-expenditure source label. Net debt and debt/equity require compatible balance-sheet dates and a positive equity denominator. Missing cash, debt, EPS or valuation data is not estimated from company descriptions.

Each displayed metric has a fact or metric evidence ID. Calculations record formulas and input evidence IDs; source facts retain document/page/column provenance. Calculated PDF cells are distinguished from reported cells. The authenticated report includes additional metrics and the calculation/source table.

## Independent source verification and freshness

`ResultVerification` is append-only and deduplicated by an evidence fingerprint:

- **VERIFIED:** a PDF acquired through an authoritative adapter has matching issuer identity and extracted period/basis; for a manually sourced stored period, independent official comparison additionally requires matching company identifiers (CIN/ISIN) and core figures without extraction conflicts.
- **PARTIALLY_VERIFIED:** authoritative references or matching figures exist, but the full identity/acquisition/identifier chain has not been established.
- **UNVERIFIED:** independent authoritative evidence has not been recorded. A pasted exchange URL does not change this.
- **CONFLICTING:** identifiers, company/period/basis or extracted figures disagree. Original documents, facts and the comparison remain available for review.

Filing references and their external IDs remain in `event_references`; verification assessments link the compared documents and events. A matching hash alone cannot upgrade authenticity. Previously stored records are not silently granted a new verification status on migration.

Freshness tracks the latest validated stored period, latest result discovered through available configured sources, each company's last successful source check, and newer results awaiting ingestion. Unknown-period newer filings and pending same-period revisions are treated cautiously. Source-metadata/PDF disagreements stay in `NEEDS_REVIEW`; they do not overwrite the detected reporting period with an older PDF period. The existing monitor downloads and processes detected filings and retries through its existing source jobs.

NSE RSS covers only its current announcement window. The existing issuer-IR adapter has bounded archive coverage. Neither proves exhaustive public-source coverage or discovers announcements outside the configured adapters. BSE and generic issuer crawling remain unsupported. An unavailable provider is a recorded failure, not a successful verification.

## Evidence-quality confidence

`EARNINGS_EVIDENCE_V1` is a documented quality rubric, **not statistical calibration or a forecast probability**.

- **HIGH:** all checks pass: core facts, independently readable PBT/PAT, authoritative source identity, tax/PAT and attributable-profit reconciliation, compatible YoY and QoQ trends, compatible cash conversion, verified comparison-period sources, no known source conflict or newer result, and a successful per-company authoritative source check in the last 24 hours.
- **MEDIUM:** core facts, independent profit check, verified current source, reconciliation, freshness/no-conflict and recent-source checks pass, and at least one compatible revenue/PAT comparison exists. Missing cash, YoY/QoQ history or historical-source verification is disclosed.
- **LOW:** those minimum conditions are not met. The alert lists the evidence-quality failures; low confidence does not invent a neutral or bullish conclusion.

The previous assessment comes from the saved analysis of the previous available compatible reporting period. Absent prior history is stated, not reconstructed. AI claims are revalidated against current structured evidence before inclusion; unsupported cached claims fall back to the deterministic report. Valuation is separate from business performance. Observed dated multiples may be displayed, but a multiple alone does not establish fair value, and user-entered growth assumptions are not required or treated as forecasts.

## Formatting, authentication and delivery

`schemas/earnings_notification.py` defines `EarningsNotification` and retains the complete snapshot, thesis, quantified drivers, watch items, valuation, confidence, data quality and report link for auditing. Following user feedback, `telegram_formatter.py` now renders only bold result highlights, a Positive/Negative/Neutral assessment, a short plain-language thesis and up to two attributed management highlights. Internal evidence IDs and technical tables remain in the persisted DTO and complete report; the webpage is unchanged. Important source conflicts/staleness remain a short caution. The formatter escapes Telegram HTML and packs complete sections/lines into parts below the Telegram limit. It never slices an HTML tag or substitutes a missing metric with zero. The format follows the official [Telegram sendMessage contract](https://core.telegram.org/bots/api#sendmessage).

Set these only after provisioning a remotely reachable HTTPS route:

```dotenv
REPORT_PUBLIC_BASE_URL=https://your-reports-host.example
REPORT_ACCESS_SECRET=<separate random secret of at least 32 characters>
REPORT_LINK_TTL_SECONDS=86400
```

The host is operator-configured; Almonds does not provision a tunnel or hosting service. Expose **only** `/api/reports/earnings/*` through the HTTPS proxy; the rest of the existing unauthenticated local API must remain private. Local, insecure and credential-bearing report URLs are rejected. Until a remote route and secret exist, the DTO explicitly records that authenticated remote report access is unavailable, and the short Telegram brief omits the link. It never emits localhost as a remotely accessible full report.

HMAC links bind the exact period, delivery snapshot or source-document resource and expiry (maximum 24 hours). Document access is restricted to the report's source PDF; a report signature cannot authorize a different document. Responses disable caching and referrers; access logs redact signatures. Rotate the report secret to revoke outstanding links. Delivery links open the persisted notification snapshot, preserving the original figures and analysis version.

The result/notification-version/destination key and database constraints prevent repeated processing from enqueueing the same notification. Automatic alerts retain one version per event/destination; an explicitly selected `analysis_id` represents a deliberate new version. Per-part receipts prevent resending acknowledged earlier parts. Definite rejections and pre-connect failures use persisted exponential backoff and Telegram `retry_after`. Permanent errors remain `FAILED`.

Telegram has no send-message idempotency parameter. Read/write timeouts, invalid success receipts, and a worker restart during an in-flight send become **UNCERTAIN** and are not automatically replayed. This avoids claiming an impossible exactly-once external-send guarantee. Check the chat before deliberately requesting another version. Upgrading an old in-flight delivery likewise preserves it as uncertain.

Useful local endpoints:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/telegram/earnings/{period_id}/preview` | Structured current report without sending or invoking AI generation. |
| `POST /api/telegram/earnings/{period_id}/queue` | Explicitly queue a validated stored result, including a manual/historical result. Repeated requests reuse the delivery. |
| `POST /api/telegram/earnings/{period_id}/queue?analysis_id={id}` | Queue one explicitly selected saved analysis version belonging to the period. |
| `POST /api/telegram/deliveries/{delivery_id}/retry` | Retry a definitively failed delivery while preserving acknowledged parts. Rejects uncertain/sent deliveries. |
| `GET /api/telegram/status` | Delivery state/counts and configuration health without bot credentials. |

## Migration and checks

From `backend`, run `.venv/bin/alembic upgrade head` and restart the API. Migration `0024_intelligent_earnings_alerts` adds discovery/check/verification records, printed precision, and resumable versioned delivery fields. It preserves existing facts, analyses, documents and delivery receipts. PostgreSQL enum additions are retained on downgrade; downgrade refuses to discard versioned delivery history that shares an event.

For the installed local service, build the frontend and run `python3 ops/install-production.py` from the project root. The launcher applies migrations before starting one API worker. Never run tests against the production database or send real Telegram messages from test fixtures.

The offline suite includes full mock-source → real PDF ingestion/extraction → financial analysis/fallback → persisted DTO → mocked multipart Telegram delivery, plus ETERNAL/ESDS fixtures, reconciliation, exceptional/attributable profit, period/unit mismatches, source conflicts, freshness, confidence, unsafe links, HTML escaping, authenticated snapshots, ambiguous-send recovery, duplicate prevention and migration preservation. Optional existing real-PDF checks use local files only.

Validation for the complete pipeline update: **501 backend tests passed, 16 optional tests skipped**; TypeScript/Vite production build and the general, monitoring and forecast-accuracy browser checks passed. The subsequent Telegram-only simplification passed **507 backend tests, 16 skipped**, including attributed current-period management highlights, result direction, HTML escaping/splitting, clean output and retained conflict/staleness cautions. Frontend files are unchanged by the simplification. The existing ESDS real-PDF regression passed, and the local ETERNAL PDF retained its validated consolidated Q1 FY2027 revenue/PBT/tax/PAT figures. No real Telegram message was sent by these checks. Large scenario histories are summarized only in the bounded AI prompt; current financial/source/confidence evidence and the full persisted report remain available.

Deployment choice: Almonds remains on the user's Mac, with remote report hosting unconfigured. This is an explicit unavailable state in the stored report DTO; the short Telegram brief omits unavailable links.

Operational limits remain explicit: a remote HTTPS route is required for phone-accessible reports; provider gaps can prevent authoritative verification; unsupported cash-flow layouts remain unassessed; and full historical source coverage is unavailable with the existing adapters. These states do not block ingestion or create fabricated research.
