# Almonds user guide

Almonds brings your recorded trades, company filings, financial history and investment research into one local workspace. This guide explains the implemented features and the steps to use them. Start with the [README](../README.md) to install and run the application.

## Find your way around

| Sidebar entry | What you can do |
| --- | --- |
| **Overview** | See open positions, remaining cost basis, realised profit/loss, cost allocation, recent transactions and a compact Gold watch. |
| **Holdings** | Inspect shares still held, average acquisition cost and remaining cost basis. |
| **Activity** | Review recorded BUY and SELL transactions. |
| **Events** | Check company disclosures, upload PDFs, open source documents and see Telegram delivery status. |
| **Financials** | Select a company and reporting scope; read results, investor analysis, forecasts, accuracy, thesis and valuation inputs. |
| **Valuation** | Jump to the selected company's dated valuation, history and scenario tools within Financials. |
| **Companies** | View registered companies and their identifiers or delete an eligible company. |
| **Company Memory** | Index stored documents and search historical company evidence. |
| **IPOs** | Research upcoming and open mainboard IPOs and the attractiveness screen. |
| **Gold** | Inspect global and Indian gold references, drivers, source health and alerts. |

Forecasts, management commentary, Investment Thesis and Thesis Monitoring are panels inside **Financials**, rather than separate sidebar pages. Market-wide briefs currently arrive through Telegram; their schedule, history and previews are available through the API.

## 1. Register companies

1. Select **Add company**.
2. Enter the exchange symbol and company name. Symbols are stored in uppercase.
3. Select the exchange and add sector, ISIN or BSE code when available.
4. Save, then find the company under **Companies**.

Registering an NSE company makes it eligible for live disclosure monitoring and automatic research; you do not need to record a holding first. Good issuer identifiers help source matching. BSE company registration is supported, but the BSE disclosure adapter is a placeholder, and automatic market research currently uses NSE.

Deleting a company requires confirmation and removes its associated transactions and research records when deletion is allowed. Companies with immutable thesis or forecast history cannot be deleted: keep the audit trail and deactivate thesis pillars instead. Treat company deletion as a permanent data operation.

## 2. Record trades and understand the portfolio

1. Select **Record transaction** and choose a registered company.
2. Select **BUY** or **SELL**.
3. Enter shares, price, transaction date and charges, then save.
4. Check **Holdings**, **Activity** and **Overview**.

Almonds uses weighted average cost with decimal arithmetic. BUY charges increase acquisition cost. SELL charges reduce proceeds and realised profit. Selling shares removes cost at the current weighted average; the remaining shares retain that average cost. A sale exceeding the shares available at its transaction date is rejected.

For an example with no charges, buying 34 shares at ₹429 and selling 24 at ₹1,191 leaves 10 shares, an average acquisition price of ₹429, a remaining cost basis of ₹4,290 and realised profit of ₹18,288.

The allocation view measures **remaining acquisition cost**, rather than current market value. Portfolio accounting does not connect to a broker, execute orders, or automatically adjust your transaction ledger for dividends, splits or other corporate actions. Record your actual transactions accurately; dated EOD research prices serve the separate valuation tools.

## 3. Monitor company disclosures

Open **Events → Check for events**. Browse the resulting list, then open an event for its date, category, origin, source references, PDF and processing status. The source summary distinguishes new events, duplicates, errors and unresolved symbols.

In live mode, Almonds reads the configured official NSE announcements source for registered NSE companies. The ESDS investor-relations adapter provides additional ESDS-specific coverage. Multiple source observations or identical PDFs can map to one event while preserving each reference. Source failures remain visible; they do not turn into invented events.

Monitoring modes are configured in the private `.env`:

| Mode | Behavior |
| --- | --- |
| `live` | Fetch the supported external sources. Automatic research can schedule these checks. |
| `fixture` | Replay invented ESDS disclosures for development, for eligible open positions. Disable automatic research before using this mode. |
| `none` | Disable configured disclosure-source collection. |

The recent NSE feed is not a complete historical archive. A company can be registered correctly and still have no current event. Generic company IR and BSE adapters are not universal crawlers; adding a source URL does not implement a new parser. See [data sources](data-sources.md) and [automatic tracking](production-tracking.md).

### Read the source trail

Open an event and follow its source or document link. PDFs are stored by content hash; extracted text retains one-based page numbers. Live, manual and fixture origins stay distinct. A document hash confirms which file is stored, not whether its contents or issuer are authentic.

### Upload a company PDF

1. Expand **Events → Upload a company PDF**.
2. Select the company, provide a title and document date, and choose the event type.
3. Add the original HTTPS source URL if available.
4. Choose a PDF and select **Add PDF**.
5. Open the new event to inspect the document or process financial results.

Supported upload categories include financial results, earnings-call documents and transcripts, earnings releases, investor presentations, annual reports and other documents. The upload limit is 10,000,000 bytes. Repeating the same PDF for the same company is deduplicated.

Manual uploads retain `MANUAL` provenance, even when you supply an official-looking URL. Searchable embedded text is required; image-only PDFs show `NO_TEXT` because OCR is not implemented. A presentation, release or transcript can feed Management Commentary when its reporting period and date can be matched.

## 4. Extract and inspect financial statements

New live financial-results PDFs are processed after successful ingestion. For an uploaded PDF, choose **Financial results**, open the event and select **Process financial results**. A manual event categorized as **Other** also offers a results-confirmation action when appropriate.

Then open **Financials**, choose the company, and select **CONSOLIDATED** or **STANDALONE**. Read revenue, PAT, EPS, EBITDA or its proxy, margins, growth and history. Expand a number to inspect its source PDF, page, original unit or calculation inputs.

Processing checks issuer identity where required, reporting scope, dates, table units and arithmetic. PDFs with unsupported layouts, unreadable numbers or conflicting information may produce `NEEDS_REVIEW` or fail without creating usable periods. Manual result PDFs can be processed when they identify the selected company and pass extraction checks; table validation and independent official-source verification remain separate assessments.

| Term | Meaning |
| --- | --- |
| **Validated** | Extracted financial cells passed the supported table checks. This does not mean the statements were audited or independently authenticated. |
| **Needs review** | A period or document failed a required identity, layout or consistency check. Inspect its note and original PDF. |
| **Reported EBITDA** | An explicitly reported supported EBITDA figure. |
| **EBITDA proxy** | PBT + finance costs + depreciation/amortisation. It may include other income and exceptional items. |
| **Unavailable comparison** | A compatible prior period or denominator is missing. Almonds leaves the value unavailable. |
| **Potentially stale result** | Source coverage indicates a newer result may exist or be awaiting ingestion. Inspect stored and detected dates. |

Financial totals are normalized to ₹ crore; EPS is per share. YoY and QoQ compare compatible reporting scope and periods, using `(current − prior) / abs(prior) × 100`. Annual and quarterly periods remain distinct. Conflicting revisions do not silently replace an existing source history. See [extraction and validation](financial-extraction.md) and [earnings evidence definitions](intelligent-earnings-alerts.md).

## 5. Read event and quarterly investor analysis

### Short event briefs

Opening an event requests its short AI brief. The brief interprets the saved event and supported document evidence, with source citations. A successful brief is cached; **Regenerate** requests another version. Missing or invalid AI output stays explicit.

### Quarterly analysis

In **Financials**, select the company and reporting scope. The analysis panel uses the latest validated period in that scope; use **Analyze Results** or **Generate AI analysis**. Older validated periods remain visible in the history table, and their analysis can be requested through the API. Later actions may be labelled **Regenerate** or **Retry AI analysis**, depending on the saved result.

The investor report brings together:

- Quarterly performance, material changes, positives, concerns and an earnings bridge where supported.
- A results verdict, valuation context and an investor reading of what needs to happen next.
- Attributed management commentary, guidance, execution checkpoints and unresolved questions.
- Conditional scenarios, risks, evidence references and Thesis Impact when available.

Calculations, financial facts and deterministic verdict inputs come from Python services. The optional Ollama model provides interpretation. Model claims are checked against supplied evidence, citations and supported numbers; rejected output may leave a clearly identified deterministic or verified fallback. A source-backed narrative is not a forecast guarantee or an instruction to trade.

For local AI, start Ollama and pull the configured `OLLAMA_MODEL` (the example uses `qwen3:8b`). The model can be changed in `.env` without changing the business logic. Company Memory is optional; unavailable retrieval does not prevent the existing financial analysis path. See [local AI analyst](local-ai-analyst.md).

### Management commentary and checkpoints

Upload or ingest the company's presentation, earnings release or transcript with the correct category and reporting date. Open the quarterly report and **View full commentary** where available to follow statements back to their source pages and speakers.

Commentary tracks guidance type, target period, conditions and changes when the text supports them. Checkpoints represent claims to test against later evidence. An aspiration, reported promise or absence of a later disclosure is not proof of delivery or failure. Ambiguous dates and missing evidence stay unresolved. The quarterly review prioritizes material milestones, capex, funding, demand and changes to the investment story.

## 6. Use dated prices, valuation and scenarios

Select the company and reporting scope in **Financials**, then open **Valuation**. Select **Refresh EOD price** when the configured provider supports that company. This fetches dated daily observations, not a real-time brokerage quote.

Where available, the panel shows price, market cap, TTM inputs, calculated valuation ratios, recent result trends and valuation history. Expand **Calculation and sources** or unavailable-input details to understand each number. Price history and valuation changes retain their source dates; raw prices may be affected by corporate actions.

TTM calculations require compatible verified financial inputs. They may use four quarters or a supported annual bridge. Forward PEG needs a sourced positive growth assumption; enterprise multiples require compatible debt and cash. Missing inputs are not guessed. Prices older than seven days are flagged and excluded from the fresh market context used for new results analysis.

### Enter a sourced snapshot

In **Financials → Share price and valuation**, enter the dated closing price and HTTPS source. Optional inputs include source P/E and market cap, plus a forward annual EPS growth estimate with its source type and reference. Select **Save market snapshot**.

Source P/E and PEG remain reference inputs, separate from Almonds' own calculated trailing valuation. Manual prices stay identified as manual observations. They do not verify a financial filing.

### Forward scenario

In **Valuation → Scenario analysis**, enter annual EPS growth, terminal P/E and years, then select **Calculate scenario**. Read the implied future EPS, price, total return and annualized return. This needs reliable positive TTM EPS. The entered values are your assumptions, not system forecasts.

### Reverse required growth

In **Required EPS growth**, enter a target annual return and select **Calculate required growth**. The tool reuses the terminal P/E and horizon above to calculate the earnings growth needed to meet that return. Compare the requirement with available observed EPS growth without assuming it will occur.

### Three-case P/E assessment

The investor report's **What P/E do current fundamentals support?** panel exposes conservative, base and optimistic scenarios. Inspect assumption provenance, edit the supported inputs, and use **Save assumptions and update report**. The resulting range is a disclosed sensitivity analysis, not a universally correct fair value. Historical valuation context appears only when sufficient dated evidence exists.

### Import a reference comparison

The API supports a user-exported NSE Market Lens CSV through `POST /api/market-lens/import`, with its as-of date and original Market Lens URL. Imported metrics appear in the **Almonds vs NSE Market Lens** comparison where applicable. This is an advanced API workflow; there is no Market Lens upload form in the current UI. See [market valuation](market-valuation.md) and the running API documentation at `http://127.0.0.1:8000/docs`.

## 7. Search Company Memory

Company Memory searches already stored and parsed documents; it does not fetch missing company history on demand.

1. Start the configured Qdrant service and Ollama.
2. Pull the configured embedding model (`nomic-embed-text:latest` in the example).
3. Open **Company Memory**, choose a company, and select **Index / update documents**.
4. Inspect indexing status and passage counts.
5. Enter a question, optionally set **Known by date**, document type or topic, and select **Search evidence**.

Results show source documents, publication dates, page numbers and original passages. The date filter limits which published evidence can be retrieved. Structured PostgreSQL records remain authoritative for financial facts and guidance; retrieved text supplies context. Relevance scores measure retrieval similarity rather than truth or investment confidence. Missing embeddings, Qdrant failures and partial indexing are displayed. See [Company Memory design](phase7-report.md).

## 8. Build and maintain an investment thesis

Open **Financials → Investment Thesis** for the selected company and reporting scope. When enough verified quarterly research exists, Almonds can generate grouped pillars and evaluate them from saved evidence.

The main view shows the current investment case, pillar states, **What's Working**, **What Needs Proof**, **What Worries Me** and the **Next Big Test**. Expand a pillar's **Evidence and details** to see its requirements and source references.

For manual controls, expand **Edit thesis**. You can add or revise a pillar, set importance, add qualitative or numerical tests, link management checkpoints, review proposed pillars from a saved quarterly analysis, or select **Evaluate thesis**. Review proposals before accepting them. Deactivate a pillar when it no longer belongs in the case; do not erase its historical audit trail.

Use **View an earlier date** to inspect a stored historical thesis. Changes create revisions and recorded assessments rather than backdating today's interpretation. A pillar can remain unproven when the necessary evidence has not arrived. See [living thesis](phase8-report.md) and [automatic thesis evaluation](phase81-report.md).

### Follow Thesis Monitoring

Verified results, supported metric changes, attributed guidance/commentary and material official disclosures can refresh the thesis automatically. **Thesis Monitoring** shows developments, evidence confidence, unresolved questions and catalysts.

Expand **Assessment timeline**, select an assessment or compare two stored assessments, then inspect the evidence and pillar explanations. Numerical checks are deterministic. An automatic state update does not prove economic causation or predict a price move. See [monitoring policy](phase82-report.md).

## 9. Track expectations, forecasts and accuracy

Open **Financials → Almonds Forecasts & Accuracy**. This area contains the expectation ledger, automatic forecasts, automated valuation context and scoring views. Keep the company and consolidated/standalone selection consistent with the evidence you want to inspect.

### Expectation ledger

Filter by target period, metric and source. Inspect the original expectation, actual outcome, variance, evidence and revision history. **Evaluate available results** checks recorded expectations against eligible stored outcomes. Missing outcomes, pending periods and excluded records remain separate from evaluated scores.

To capture your own forecast, expand **Advanced: register a manual expectation**. Provide the metric, target period, basis, unit, exact value or range, result cutoff, source reference and description. Management/AI entries require supported evidence IDs. You can link an original thesis revision, indicate guidance type, and optionally specify a probability of meeting the frozen tolerance.

Save prospective expectations before their cutoff. Mark old imports as historical: they remain visible but are excluded from prospective scores. Capture times, original confidence and tolerance policy are retained; changing later settings does not rewrite an old forecast.

The same area shows management accountability and thesis history when eligible records exist. A promise without sufficiently attributable outcome evidence remains unresolved.

### Automatic research and forecasts

With `RESEARCH_AUTOMATION_ENABLED=true`, adding a supported company creates durable research jobs. The worker coordinates disclosure checks, document/commentary work, permitted NSE EOD market refreshes and research evaluation. Jobs retain status, source coverage, errors, retry timing and successful runs.

Under **Upcoming forecasts**, inspect the model estimate, low/base/high range, horizon, capture time, version and historical inputs. These forecasts use the deterministic verified-history engine; they do not require you to enter financial estimates manually or ask an LLM to invent them. Insufficient or stale history remains an availability gap.

Read **Research automation status** and **Source coverage and missing data** to see what is waiting. **Retry automatic research** refreshes the panel; the advanced retry API enqueues eligible missing work while preserving active leases and backoff. Source access and sufficient company history are still required. See [autonomous research implementation](../PHASE_8_3_AUTONOMOUS_REPORT.md).

### Forecast Accuracy

Use the **Forecast Accuracy** filters for metric, horizon, period type and date range. Inspect **Prediction vs actual**, snapshot/source details, historical accuracy, calibration and **Data quality**. The scoring action queues background evaluation rather than fabricating an outcome.

Original forecasts and the first eligible actual remain the primary comparison. Later source corrections are shown separately. Pending results, historical imports, unsupported units and other exclusions do not become successful forecasts. Calibration summaries need enough eligible observations and retain sample uncertainty; an empty panel or small sample is not a reliable win rate. See [forecast scoring and calibration](../PHASE_8_3B_REPORT.md).

## 10. Connect Telegram and receive company alerts

1. Create a personal bot with Telegram's **@BotFather**, then send `/start` to your new bot.
2. From the repository root, run:

   ```sh
   backend/.venv/bin/python backend/scripts/setup_telegram.py --test
   ```

3. Enter the token in the hidden prompt and choose your private destination chat.
4. Restart the app that uses the updated `.env`.
5. Open **Events → Telegram alerts** to inspect connection/delivery counts or select **Send Telegram test**.

The setup command stores the backend token and destination in the ignored private `.env`. Keep the bot token out of issues and screenshots. The browser receives connection status, not the token.

Automatic company alerts cover selected investor-relevant new live events for monitored portfolio companies: results, dividends, important orders/cancellations, acquisitions/divestments, capex, management/auditor changes, buybacks, fund raising, ratings, promoter transactions/pledges, regulation and litigation. Routine meetings, presentations and compliance notices stay in the Events view. The approved category filter is not a judgment of company-specific financial materiality.

Earnings messages show a short results brief with supported growth comparisons, result direction, thesis and attributed management highlights. Other events use summary, impact and conditional next-step sections. AI and evidence gaps stay explicit. Manual uploads, fixture records and old database events do not automatically replay as new live company alerts; old publications are excluded by the configured age limit.

Delivery tracks message receipts and retry state. **UNCERTAIN** means the outcome of a send is unknown: inspect your chat before deliberately requesting another version. Definite recoverable failures can retry; ambiguous sends are not automatically replayed. Set `TELEGRAM_ENABLED=false` and restart to pause Telegram globally. See [Telegram setup and behavior](telegram.md).

### Optional full-report links

Signed, expiring earnings-report URLs require a separately reachable HTTPS route, `REPORT_PUBLIC_BASE_URL` and an independent signing secret. Expose only the intended report route with suitable infrastructure, rather than the entire local application. Without that setup, phone-accessible full-report links remain unavailable. This does not prevent the short Telegram brief. See [signed report access](intelligent-earnings-alerts.md#formatting-authentication-and-delivery).

## 11. Receive market-wide morning, noon and closing briefs

Market reviews are independent of your holdings. Enable `MARKET_BRIEFS_ENABLED` and connect Telegram. Regular NSE equity trading days use the configured Asia/Kolkata schedule:

| Time, IST | Brief | How it is used |
| --- | --- | --- |
| **8:45 AM** | Original pre-open outlook | Saves the session expectation and prior-close baselines. |
| **11:00 AM** | Updated morning brief | Refreshes morning news and the outlook for the rest of today; saves this context for noon. |
| **12:30 PM** | Noon update | Reviews the saved 11 AM news/outlook alongside fresh observations and keeps the original pre-open direction comparison traceable. |
| **4:00 PM** | Closing review | Reviews closing observations and the original morning view, then records conditional next-session scenarios. |

Reports cover available Nifty 50, Sensex, Bank Nifty, India VIX, breadth, sector observations and dated domestic/global news. The 11 AM and noon checks are provisional. The 11 AM brief's actual saved news is retained for noon even if a headline disappears from live feeds; a missing update is stated explicitly.

Preparation starts at the scheduled time. Delivery windows end at 9:10 AM, 11:30 AM, 1:00 PM and 5:00 PM respectively; stale or expired reports are not replayed. Calendar/source failures can prevent a brief. Weekends, declared holidays and special sessions do not use this regular-session routine.

There is no dedicated market-brief screen yet. Use `GET /api/market-briefs` for schedule/history/delivery status, `GET /api/market-briefs/reports/{id}` for a saved report, or `POST /api/market-briefs/preview?slot=morning|midmorning|noon|close` for an unsaved preview. A preview does not queue Telegram or create a historical forecast. See [market brief sources and comparison rules](market-briefs.md).

## 12. Research mainboard IPOs

Open **IPOs** and select **Refresh IPOs**. Upcoming/open mainboard issues are sorted by GMP as a percentage of the upper price band; missing GMP appears last. Use **Show attractive IPOs only** to apply the screen. Expand each issue for dates, minimum application, price band, lot size, issue/fresh/OFS sizes, financials, business overview, risks, missing data and source/RHP links.

The default screen requires confirmed issue dates and details, fresh GMP of at least **10%**, compatible positive annual financials without declines, and supported annual P/E at most **40×**. It flags leverage and high OFS. Incomplete evidence can keep an issue visible without making it eligible for an alert.

Discovery runs after 8 AM IST with periodic scheduler checks. With monitoring and Telegram enabled, qualifying issues can receive a weekly Monday shortlist and a review before opening, with bounded catch-up. An empty eligible shortlist sends nothing. Refreshing manually can also queue due eligible briefs, so it is more than a connection test.

This feature works independently of holdings. SME issues and FPOs are excluded. GMP is unofficial third-party information and the screen is not a listing-gain prediction, full RHP review or an application/order facility. See [IPO sources, criteria and timing](ipos.md).

## 13. Follow Gold watch

Open **Gold**, or use the compact cards in **Overview**. Select **India** or **Hyderabad** for the 22K/24K per-gram references; the browser remembers the choice. The global quote remains in USD per troy ounce. Each quote retains its own source date and freshness; retail references exclude GST and making charges.

Select **Refresh gold** to collect prices and news. Read the reported drivers, possible mechanisms, conditional next steps, USD/INR context, source-health messages and recorded daily history. A source failure preserves the old value with its original timestamp. India is not substituted for Hyderabad or derived from the USD spot quote.

With Gold monitoring and Telegram enabled, the default routine sends briefs at **11 AM** and **5 PM IST**. Extra alerts can follow a global USD gold move of at least **1%** or qualifying newly reported major events. Price-move alerts have a cooldown; major-event detection uses conservative rules rather than every routine headline. The ordinary source refresh interval is 15 minutes. Manual refresh does not directly send an extra scheduled brief.

Telegram includes both Indian locations regardless of the browser's selection. Gold works independently of portfolio holdings and uses its own schedule. Source comparisons, thresholds and expiry rules are described in [Gold watch](gold.md).

## Daily use and practical limits

Keep the application, PostgreSQL and any optional services running for background work. A sleeping or stopped host cannot collect sources or send scheduled messages; these features do not create an external wake-up service. Source access, publication timing, unsupported PDF layouts and sufficient historical evidence determine what Almonds can show.

A useful daily routine is to review morning Telegram briefs, check Events and any new result-processing notes, inspect your selected company's Financials and thesis developments, then read the noon/closing reviews. Record actual trades in Activity as they occur, and inspect source evidence before relying on an interpretation.

The UI provides research and accounting tools. It has no broker execution, comprehensive multi-exchange crawler, OCR pipeline, guaranteed real-time feed or public multi-user authentication system. Keep it in a trusted local environment unless you add and review deployment access controls. The MIT license covers this repository's code, not third-party filings, data-provider access rights or model weights.
