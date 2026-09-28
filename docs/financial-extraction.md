# Financial extraction and validation

Phase 4 uses stored official financial-result PDFs. It calls neither a secondary-data service nor an AI model. The [ESDS Q1 FY27 results PDF](https://www.esds.co.in/assets/images/investor-relations/Announcements/ESDS_BM_Outcome_BSE_NSE_Intimation.pdf) is the first integration case. Source access is reviewed in [data-sources.md](data-sources.md).

## Data path

    LIVE financial-results event + processed, SHA-256 checked PDF
      → detect financial-statement pages
      → read explicit scope, column dates, units, labels and word coordinates
      → parse individual cells without guessing damaged OCR text
      → check income, PBT and PAT arithmetic
      → store periods and facts with page/column/label provenance
      → calculate metrics from validated same-scope periods
      → financial APIs and React Financials section

PyMuPDF's automatic table finder returned no tables for this ESDS document. Plain page text loses the columns. The extractor therefore uses PDF word coordinates. It requires a result-page heading with Standalone or Consolidated, explicit date headers, a supported unit heading, and recognizable row labels. It rejects malformed numeric fragments rather than guessing that, for example, “l46.81” means “346.81”. A core row that fails table arithmetic places the period in NEEDS_REVIEW. Searchable PDF text remains in document_pages; facts retain one-based source page numbers.

The first extractor recognizes quarterly and annual columns with Indian fiscal boundaries. FY2027 runs from 1 April 2026 to 31 March 2027. Thus 30 June 2026 is Q1 FY2027; 31 March 2026 can be Q4 FY2026 or annual FY2026 depending on the table header. The database uniqueness key includes period type and scope.

## Model and meaning

| Table | Purpose |
| --- | --- |
| financial_periods | Company, period dates, fiscal year, quarter/type, standalone or consolidated scope, source event/document, and status. Unique on company + period end + type + scope. |
| financial_facts | Controlled metric code, normalized Decimal value/unit, original PDF value/unit when printed, source document/page/row label/column date, extraction method, and status. |
| financial_metrics | Python-calculated values, formula code, input fact IDs, and optional comparison period. No value is written for unavailable comparisons. |
| financial_processing_runs | Last processing status and reason even if no reliable period can be created. |
| financial_fact_corrections | Reserved audit structure for a future correction workflow: previous/corrected value, reason, and timestamp. No correction API is exposed yet; source facts are not edited in place. |

VALIDATED means the extracted cells reconciled with the PDF table; it does **not** mean the issuer's statements were audited. A period with unreadable or inconsistent core cells is stored as NEEDS_REVIEW. The frontend shows financial numbers only from validated periods and lists review reasons separately. A source PDF revision for an occupied company/period/type/scope is flagged as a conflict, not silently substituted.

Money totals use canonical INR_CRORE; EPS uses INR_PER_SHARE. Printed source values preserve their original unit and amount. Conversion: ₹1 crore = ₹10 million = ₹100 lakh. A calculated PBT row has no original PDF value/unit and is marked CALCULATED.

EBITDA is **not directly reported** in the ESDS statement. almonds calculates it as PBT + finance costs + depreciation and amortisation from validated facts; the UI labels it calculated. ADJUSTED_EBITDA is a separate code and is never treated as unadjusted EBITDA. Other controlled codes include revenue from operations, total/other income, PAT, expenses, basic/diluted EPS, margins, growth, and margin change.

Formulas use Python Decimal:

- YoY = (current − same period last fiscal year) / abs(prior) × 100.
- QoQ = (current − immediately previous quarter) / abs(prior) × 100.
- EBITDA margin = calculated EBITDA / revenue from operations × 100.
- PAT margin = PAT / revenue from operations × 100.
- Margin change in basis points = (current percentage − prior percentage) × 100.

Comparisons require the same company, scope, and period type. Missing periods or zero denominators yield no metric. Annual periods compare to prior annual periods, never to Q4.

## Manual comparison with the official ESDS PDF

These source values were read directly from the printed statement pages and checked against the extractor. The PDF reports money totals in **₹ million**; divide by 10 for the stored ₹ crore values.

| Scope and period | PDF page | Source revenue ₹m | Source PAT ₹m | Basic EPS ₹ | Stored revenue ₹Cr | Stored PAT ₹Cr | Extraction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Consolidated Q1 FY2027 | 7 | 1,336.57 | 292.77 | 2.92 | 133.657 | 29.277 | Validated |
| Consolidated Q4 FY2026 comparison | 7 | 1,675.00 | 676.76 | 6.71 | 167.500 | 67.676 | Validated |
| Consolidated Q1 FY2026 comparison | 7 | 1,245.87 | 256.85 | 2.56 | 124.587 | 25.685 | Validated |
| Standalone Q1 FY2027 | 3 | 1,161.66 | 181.99 | 1.81 | 116.166 | 18.199 | Validated |

The current consolidated quarter gives revenue YoY **7.280053%**, QoQ **−20.204776%**, PAT YoY **13.984816%**, and PAT QoQ **−56.739465%**. Calculated EBITDA is ₹57.511 crore and its margin is **43.028798%**. These are Python calculations, not values reported by the issuer.

The filing also has columns that its text layer does not read reliably. The consolidated FY2026 annual total-expenses text, for example, appeared as “3” instead of the printed “3,132.29”. Almonds now reconstructs that subtotal from all four readable expense components **only when** it also agrees with independently printed total income minus PBT. Tax and PAT are calculated from readable current/deferred tax and printed PBT; their provenance is labeled CALCULATED. The audited annual column can therefore be validated and used in an annual TTM bridge. Damaged standalone comparison columns still remain NEEDS_REVIEW. The standalone current-quarter printed PBT is ₹242.86 million; its PDF text was damaged, so almonds obtains ₹242.86 million from validated total income minus expenses and marks the fact CALCULATED. No unreadable cell is silently treated as a printed fact.

## Run and inspect

After running Alembic upgrade head, process an existing live financial-results event:

    curl -X POST http://127.0.0.1:8000/api/financials/process/1
    curl http://127.0.0.1:8000/api/companies/ESDS/financials/latest
    curl http://127.0.0.1:8000/api/companies/ESDS/financials
    curl http://127.0.0.1:8000/api/financials/process/1

Event/document IDs can differ after a database reset. The processing POST is idempotent: repeating it returns periods_created: 0. New live financial results with successfully stored PDFs are processed automatically after ingestion; extraction failure is logged and recorded separately without rolling back the event.

An optional real-PDF test uses a previously downloaded local official PDF. It makes no network request:

    cd backend
    ALMONDS_ESDS_PDF=data/documents/6f/6fffc199c424ce799f656041afa5a4b5e9897104fe769565e5da4c6449e222fa.pdf .venv/bin/python -m pytest -q tests/test_esds_real_pdf.py
