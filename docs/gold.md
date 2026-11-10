# Gold watch and Telegram alerts

Open **Gold** in the sidebar for prices, the news read, the alert schedule, source health and recorded daily prices. Both the Gold page and overview dashboard let you choose **India** or **Hyderabad** for the 22K / 24K per-gram cards. The choice is remembered in this browser and also changes the historical table and local-price explanation. The global USD quote stays visible. Telegram briefs include both India and Hyderabad. This feature is independent of portfolio holdings.

The daily Telegram routine uses **Asia/Kolkata**:

| Brief | Time | Catch-up window |
| --- | --- | --- |
| Morning prices, news and outlook | 11:00 am | Until 11:30 am |
| Evening update | 5:00 pm | Until 6:00 pm |

The scheduler checks on each clock minute. Sources are normally refreshed every 15 minutes, with a fresh pass when a daily brief becomes due. A missed brief is not sent after its catch-up window. Each date/time/chat combination is queued once. The existing Telegram worker stores receipts, retries recoverable failures, and does not replay uncertain sends. Pending news-only alerts and messages prepared under an older schedule are superseded before delivery.

Almonds and PostgreSQL must be running and the Mac must be awake. This uses the local application's scheduler, with no cloud wake-up service. Telegram must already be connected and enabled. Pausing gold monitoring also prevents pending gold messages from being sent.

## Prices and source dates

- Global: [Gold API's public XAU endpoints](https://gold-api.com/faqs) provide the gold spot quote in **USD per troy ounce** and an `exchangeRate` in the INR response. Telegram shows **USD/INR as rupees per $1**, not the INR price of a troy ounce. The exchange rate is checked against the paired USD and INR gold quotes and shown only when their timestamps are within five minutes. Its percentage change compares with the previous stored exchange rate; a rising USD/INR rate means a weaker rupee, and a falling rate means a stronger rupee. No percentage is invented for the first observation. The provider's `updatedAt` is its latest gold-price source fetch time, not an independent FX-market timestamp or a trade execution time. Quotes more than one hour old are marked old.
- India and Hyderabad: [Groww's published gold-rate data](https://groww.in/gold-rates), **INR per gram for 22K and 24K**. Separate entries identify India and Hyderabad, with their own prices, percentage changes and dates. The parser verifies each location, date, purity keys and plausible price relationship independently; one invalid location does not discard the other. The India entry is a nationwide published reference, not a city-specific checkout quote. Rates exclude GST and making charges. Source dates can differ between the two locations and always remain visible. Yesterday's quote is labelled explicitly; older rates are marked old. Neither location is substituted for the other, and neither is derived from USD spot.
- Global USD gold and USD/INR percentage comparisons each use the previous stored value of the same series and say so. Hyderabad percentage changes are the publisher's previous-day comparisons. The first observation of either global series has no manufactured comparison. Telegram omits the routine GST/making-charges note while the dashboard keeps the retail-rate basis.

The single retail page contains both location entries. It is cached for an hour once both of today's rates are available. Prices, currency and source timestamps are validated, redirects are rejected, responses are bounded, and HTML/RSS access respects publisher robots policy. A failed fetch preserves each component's original timestamp and displays a source gap. Old values are never relabelled as newly quoted prices. Historical Hyderabad-only records remain Hyderabad records; they do not become fabricated national history. If no usable price is available, the scheduled brief waits for recovery within its delivery window.

## News and important updates

The monitor reads [FXStreet's public news RSS](https://www.fxstreet.com/rss/news) for gold/XAU headlines, the [Federal Reserve monetary policy RSS](https://www.federalreserve.gov/feeds/press_monetary.xml), and [BBC World RSS](https://feeds.bbci.co.uk/news/world/rss.xml) for qualifying major developments. The dashboard covers the past 24 hours. This is coverage of those feeds, rather than an exhaustive global newswire.

The explanation follows **what was reported → why it may matter → what could change next**. Dated headlines and their short published summaries identify reported dollar, rates, yield, buying/selling or uncertainty factors. Each factor gets a simple mechanism: for example, gold pays no interest, so higher expected US rates can make interest-paying assets more attractive. This reflects the [World Gold Council's broader driver framework](https://www.gold.org/goldhub/research/gold-market-commentary-july-2026), while retaining uncertainty about the particular move. Inflation is explicitly two-sided; it is never automatically labelled positive or negative. Conditional predictions, negated moves and failed ceasefires are not treated as confirmed positive/negative developments in the opposite direction.

Conflicting reported moves and mechanisms yield a mixed read. When a report only says that gold rose or fell, the explanation says that the cause is not established. Next steps use conditional scenarios tied to the identified factor, with no numerical price forecast. India and Hyderabad get a separate local context: a measured rise in USD/INR indicates a weaker rupee that can raise the INR cost of imported gold, while a fall indicates a stronger rupee that can ease it, all else equal. This does not attribute a published retail-rate move to FX; local premiums and other factors remain unmeasured. Old or missing FX readings cannot establish current rupee direction. Global stored-quote changes and previous-day retail changes are labelled as different comparison periods.

The regular **11 am and 5 pm** briefs always arrive when usable price data is available. Extra alerts can be triggered by a **global USD gold-price change of at least 1% in either direction** from the last notified/initial reference quote, or a **new major reported event**. Price-move alerts retain a 60-minute cooldown, and scheduled briefs reset the price reference.

The major-event filter recognizes reported Fed policy decisions/statements, significant US inflation or jobs surprises, major conflict escalations and ceasefire agreements/breakdowns, Indian gold-import-duty changes, and central-bank gold-buying policy shifts. It excludes routine dollar/yield commentary, price-only headlines, speeches/minutes, companion Fed implementation notes, forecasts, previews, rumours and conditional/negated events. It uses conservative headline rules; it is not a comprehensive event wire or independent verification of reporting. A major event can alert even if gold has not yet moved 1%.

Events must be newly published since monitoring/policy enablement, first seen after that cutoff, and no more than six hours old. The first live upgrade records an enablement cutoff in the private environment without copying or printing its credentials. Closely related stories from different sources share an event family and overlapping geographic scope for six hours; ceasefire agreements and breakdowns are distinct events. This limits repeated coverage while allowing separate conflicts. Up to two major events are shown per alert, and only shown items are marked notified. A simultaneous price move or scheduled brief includes those events in the same message. Routine news remains in the dashboard and scheduled briefs. Detection follows the normal 15-minute source-refresh cycle.

Daily, major-event and price-move messages expire before delivery. A late queue worker supersedes expired messages. Manual dashboard refresh collects prices and news without sending a test or extra scheduled brief; the normal monitor evaluates new major events and large price moves on its next cycle.

## Settings and API

```dotenv
GOLD_MONITOR_ENABLED=true
GOLD_POLL_MINUTES=15
GOLD_ALERT_TIMES_IST=11:00,17:00
GOLD_PRICE_MOVE_PCT=1
GOLD_MOVE_COOLDOWN_MINUTES=60
GOLD_SOURCE_TIMEOUT_SECONDS=15
GOLD_MAJOR_EVENT_ALERTS_ENABLED=true
```

No additional paid data key is needed. Telegram uses the existing backend bot configuration. Alert times must be two distinct increasing HH:MM values in IST. Source failures and unavailable news remain visible on the dashboard.

- `GET /api/gold?version=2`: global, India and Hyderabad quotes with separate freshness, current explanations, dated headlines, seven days of recorded daily snapshots, schedule and recent gold deliveries. Version 1 remains the default for already-open older dashboards: its historical `prices.india` field continues to mean Hyderabad, while `prices.national` exposes India. This avoids relabelling prices before an older browser reloads.
- `POST /api/gold/refresh`: collect current evidence; no direct send. Returns `OK`, `PARTIAL`, `FAILED`, `CACHED` (checked within the last minute), or `BUSY`.

Apply Alembic migration `0027_gold_alerts` and rebuild the frontend before restarting the application. The migration adds independent gold state, snapshot and headline tables, and reuses the existing nullable-event Telegram queue.

For the existing macOS installation, `python3 ops/prepare-gold-update.py` prepares the initial narrow overlay at `/tmp/almonds-gold-update`. Use `--update` to prepare an incremental update after Gold is installed. It preserves the installed Codex and Telegram inbox features and never copies the private environment file. After reviewing and validating the staged app, `python3 ops/prepare-gold-update.py --install` verifies file hashes, backs up replaced files, and restarts the existing service; the startup script applies any required migration. The India/Hyderabad update uses the existing JSON records and requires no additional migration.

Verification: `cd backend && .venv/bin/python -m pytest tests/test_gold.py tests/test_ipos.py tests/test_telegram.py -q`; `cd frontend && npm run build && npm run test:gold`.
