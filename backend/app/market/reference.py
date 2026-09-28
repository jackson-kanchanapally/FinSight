"""User-supplied NSE Market Lens CSV, kept apart from calculated valuations."""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.market import ReferenceMetric, ValuationSnapshot

ALIASES = {
    "p/e": "pe_ttm", "pe": "pe_ttm", "price to earning (p/e)": "pe_ttm",
    "p/b": "price_to_book", "pb": "price_to_book", "price to book": "price_to_book",
    "market cap": "market_cap", "market cap (cr)": "market_cap",
    "roe": "roe", "roce": "roce", "debt to equity": "debt_to_equity",
    "revenue growth": "revenue_growth", "p/s": "ps_ttm",
}
SYMBOL_COLUMNS = ("stock", "symbol", "ticker", "nse symbol")


def import_market_lens_csv(db: Session, csv_text: str, as_of_date: date,
                           source_url: str) -> dict:
    if len(csv_text) > 2_000_000:
        raise ValueError("CSV_TOO_LARGE")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("CSV_HEADER_REQUIRED")
    columns = {column.strip().lower(): column for column in reader.fieldnames}
    symbol_column = next((columns[key] for key in SYMBOL_COLUMNS if key in columns), None)
    if not symbol_column:
        raise ValueError("CSV_SYMBOL_COLUMN_REQUIRED")
    metric_columns = [(original, code) for alias, code in ALIASES.items()
                      if (original := columns.get(alias)) is not None]
    if not metric_columns:
        raise ValueError("CSV_METRIC_COLUMN_REQUIRED")
    imported = 0
    skipped = 0
    for index, row in enumerate(reader):
        if index >= 5000:
            raise ValueError("CSV_TOO_MANY_ROWS")
        symbol = (row.get(symbol_column) or "").strip().upper()
        company = db.scalar(select(Company).where(Company.symbol == symbol)) if symbol else None
        if company is None:
            skipped += 1
            continue
        for column, code in metric_columns:
            raw = (row.get(column) or "").replace("₹", "").replace(",", "").replace("Cr", "").replace("%", "").strip()
            if not raw or raw in {"-", "N/A", "NM"}:
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                raise ValueError(f"CSV_INVALID_NUMBER_ROW_{index + 2}") from None
            if not value.is_finite() or abs(value) > Decimal("1000000000000"):
                raise ValueError(f"CSV_INVALID_NUMBER_ROW_{index + 2}")
            record = db.scalar(select(ReferenceMetric).where(ReferenceMetric.company_id == company.id,
                ReferenceMetric.metric_code == code, ReferenceMetric.provider == "NSE_MARKET_LENS",
                ReferenceMetric.as_of_date == as_of_date))
            if record is None:
                record = ReferenceMetric(company_id=company.id, metric_code=code,
                    provider="NSE_MARKET_LENS", as_of_date=as_of_date, source_url=source_url)
                db.add(record)
            record.value = value
            record.source_url = source_url
            imported += 1
    db.commit()
    return {"imported_metrics": imported, "skipped_companies": skipped}


def comparisons(db: Session, valuation: ValuationSnapshot, tolerance_pct: Decimal) -> list[dict]:
    market_date = date.fromisoformat(valuation.metric_details["metadata"]["market_date"])
    references = list(db.scalars(select(ReferenceMetric).where(
        ReferenceMetric.company_id == valuation.company_id,
        ReferenceMetric.as_of_date <= market_date)
        .order_by(ReferenceMetric.as_of_date.desc(), ReferenceMetric.id.desc())))
    latest = {}
    for item in references:
        latest.setdefault(item.metric_code, item)
    calculated = valuation.metric_details
    output = []
    for code, reference in latest.items():
        own = calculated.get("market_cap" if code == "market_cap" else code)
        own_value = Decimal(own["value"]) if own and own.get("value") is not None else None
        difference = ((own_value - reference.value) / abs(reference.value) * 100
                      if own_value is not None and reference.value != 0 else None)
        output.append({"metric_code": code, "almonds": str(own_value) if own_value is not None else None,
            "market_lens": str(reference.value), "difference_pct": str(difference.quantize(Decimal("0.01")))
                if difference is not None else None,
            "status": "MATCH" if difference is not None and abs(difference) <= tolerance_pct else
                      "REVIEW_REQUIRED" if difference is not None else "NOT_COMPARABLE",
            "reference_date": reference.as_of_date.isoformat(), "source_url": reference.source_url})
    return output
