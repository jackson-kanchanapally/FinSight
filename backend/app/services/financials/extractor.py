"""Read financial-statement rows by geometry and explicit column headers.

This deliberately rejects OCR-like number fragments rather than fixing them
heuristically. It uses the PDF word boxes because plain DocumentPage text
loses table columns. DocumentPage records remain the searchable page archive.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pymupdf

from app.models.financial import FinancialScope, MetricCode, OriginalUnit, PeriodType
from app.services.financials.period import PeriodIdentity, identify_period
from app.services.financials.calculator import exceptional_effect

MONTHS = {
    name.lower()
    for name in (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
}
ROW_PATTERNS = {
    MetricCode.EBITDA: r"^(?:reported\s+)?ebitda$",
    MetricCode.EBIT: r"^(?:ebit|operating profit)$",
    MetricCode.REVENUE_FROM_OPERATIONS: r"revenue from operations",
    MetricCode.OTHER_INCOME: r"other income",
    MetricCode.TOTAL_INCOME: r"total income",
    MetricCode.EMPLOYEE_EXPENSE: r"employee benefits expense",
    MetricCode.FINANCE_COST: r"finance costs?",
    MetricCode.DEPRECIATION: r"depreciation and amortisation expenses?",
    MetricCode.OTHER_EXPENSE: r"other expenses",
    MetricCode.TOTAL_EXPENSES: r"total\s+e\S*enses\b",
    MetricCode.PBT: r"profit\s+before\s+tax\b",
    MetricCode.PAT: r"profit\s+for\s+the\s+period\s*/\s*year",
    MetricCode.BASIC_EPS: r"^basic",
    MetricCode.DILUTED_EPS: r"^diluted",
    MetricCode.EXCEPTIONAL_ITEMS: r"^exceptional items?\b",
    MetricCode.PAT_ATTRIBUTABLE: r"^(?:profit.*attributable to (?:owners|equity holders)|owners of the (?:company|parent))\b",
    MetricCode.NON_CONTROLLING_INTEREST: r"^(?:non[- ]controlling interests?)\b",
    MetricCode.OPERATING_CASH_FLOW: r"^net cash (?:generated from|from|provided by|used in) operating activities\b",
    MetricCode.CAPITAL_EXPENDITURE: r"^(?:capital expenditure|purchase of property,? plant and equipment)\b",
    MetricCode.TOTAL_DEBT: r"^total (?:debt|borrowings)\b",
    MetricCode.CASH_AND_CASH_EQUIVALENTS: r"^cash and cash equivalents\b",
    MetricCode.TOTAL_EQUITY: r"^total equity\b",
}
CORE = {
    MetricCode.REVENUE_FROM_OPERATIONS,
    MetricCode.OTHER_INCOME,
    MetricCode.TOTAL_INCOME,
    MetricCode.TOTAL_EXPENSES,
    MetricCode.PBT,
    MetricCode.TOTAL_TAX_EXPENSE,
    MetricCode.PAT,
}
NUMBER = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?$")


@dataclass(frozen=True)
class RawFact:
    code: MetricCode
    original_value: Decimal
    original_unit: OriginalUnit
    page: int
    label: str
    column_date: date
    calculated: bool = False


@dataclass
class RawPeriod:
    identity: PeriodIdentity
    scope: FinancialScope
    facts: dict[MetricCode, RawFact] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def _lines(
    words: list[tuple], min_x: float, max_x: float, min_y: float
) -> list[tuple[float, str]]:
    rows: list[tuple[float, list[tuple]]] = []
    for word in sorted(
        (w for w in words if min_x <= w[0] < max_x and w[1] >= min_y),
        key=lambda w: ((w[1] + w[3]) / 2, w[0]),
    ):
        center = (word[1] + word[3]) / 2
        row = next((r for r in rows if abs(r[0] - center) < 3), None)
        if row is None:
            rows.append((center, [word]))
        else:
            row[1].append(word)
    return [
        (y, " ".join(w[4] for w in sorted(items, key=lambda w: w[0])))
        for y, items in rows
    ]


def _headers(
    page: pymupdf.Page, words: list[tuple]
) -> list[tuple[PeriodIdentity, float]]:
    header = [w for w in words if 135 <= w[1] <= 170]
    year_words = [w for w in header if w[4].upper() == "YEAR"]
    year_x = min((w[0] for w in year_words), default=page.rect.width + 1)
    result: list[tuple[PeriodIdentity, float]] = []
    for word in header:
        if word[4].lower() not in MONTHS:
            continue
        companions = sorted(
            (
                w
                for w in header
                if word[0] <= w[0] < word[0] + 70 and abs(w[1] - word[1]) < 3
            ),
            key=lambda w: w[0],
        )
        tokens = [w[4] for w in companions]
        raw = " ".join(tokens[:5]).replace(" .", ".").replace(" ,", ",")
        match = re.match(r"([A-Za-z]+)\s+(\d{1,2})\s*[,\.]?\s*(\d{4})", raw)
        if not match:
            continue
        try:
            end = datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
            column_center = (word[0] + companions[min(len(companions) - 1, 2)][2]) / 2
            period_type = (
                PeriodType.ANNUAL if column_center >= year_x else PeriodType.QUARTERLY
            )
            result.append((identify_period(end, period_type), column_center))
        except ValueError:
            continue
    result.sort(key=lambda value: value[1])
    if len(result) < 2 or len({(r[0].end, r[0].period_type) for r in result}) != len(
        result
    ):
        raise ValueError("Financial table period headers are missing or ambiguous")
    return result


def _compact_headers(words: list[tuple]) -> list[tuple[PeriodIdentity, float]]:
    """Read statement columns whose month/day and year occupy adjacent lines."""
    header = [word for word in words if 85 <= word[1] <= 132 and word[0] > 300]
    year_x = min(
        (word[0] for word in header if word[4].lower() == "year"), default=1000
    )
    result = []
    for month in header:
        if month[4].lower() not in MONTHS:
            continue
        day = next(
            (
                word
                for word in header
                if 0 < word[0] - month[0] < 35
                and abs(word[1] - month[1]) < 3
                and re.fullmatch(r"\d{1,2}[,.]?", word[4])
            ),
            None,
        )
        year = next(
            (
                word
                for word in header
                if abs(word[0] - month[0]) < 20
                and 5 < word[1] - month[1] < 18
                and re.fullmatch(r"20\d{2}", word[4])
            ),
            None,
        )
        if not day or not year:
            continue
        try:
            end = datetime.strptime(
                f"{month[4]} {day[4].strip(',.')} {year[4]}", "%B %d %Y"
            ).date()
            center = (month[0] + day[2]) / 2
            kind = PeriodType.ANNUAL if center >= year_x else PeriodType.QUARTERLY
            result.append((identify_period(end, kind), center))
        except ValueError:
            continue
    result.sort(key=lambda item: item[1])
    if len(result) < 2 or len(
        {(item[0].end, item[0].period_type) for item in result}
    ) != len(result):
        raise ValueError("Financial table period headers are missing or ambiguous")
    return result


def _number(words: list[tuple], left: float, right: float, y: float) -> Decimal | None:
    selected = sorted(
        (
            w
            for w in words
            if left <= (w[0] + w[2]) / 2 < right and abs((w[1] + w[3]) / 2 - y) <= 4
        ),
        key=lambda w: w[0],
    )
    raw = "".join(w[4] for w in selected)
    if not NUMBER.fullmatch(raw):
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    clean = raw.strip("()").replace(",", "")
    value = Decimal(clean)
    return -value if negative else value


def _statement_pages(pdf: pymupdf.Document):
    for page_number, page in enumerate(pdf, start=1):
        heading = page.get_text()[:650]
        match = re.search(
            r"\b(Standalone|Consolidated)\b(?:\s+\w+){0,3}\s+Financial Results for the (quarter|year) ended",
            heading,
            re.IGNORECASE,
        )
        if match:
            yield page_number, page, FinancialScope(match.group(1).upper())


def extract_pdf_tables(path: Path) -> list[RawPeriod]:
    periods: list[RawPeriod] = []
    with pymupdf.open(path) as pdf:
        for page_number, page, scope in _statement_pages(pdf):
            text = page.get_text()
            if "Rupees million" in text or "Rupees miILIon" in text:
                unit = OriginalUnit.INR_MILLION
            elif re.search(r"Rupees? lakhs?", text, re.IGNORECASE):
                unit = OriginalUnit.INR_LAKH
            elif re.search(
                r"\b(?:Rupees?|[Ilf]{0,2}NR)\s*crores?\b", text, re.IGNORECASE
            ):
                unit = OriginalUnit.INR_CRORE
            else:
                continue
            words = page.get_text("words")
            compact_layout = False
            try:
                headers = _headers(page, words)
            except ValueError:
                try:
                    headers = _compact_headers(words)
                    compact_layout = True
                except ValueError:
                    continue
            first_center = headers[0][1]
            label_lines = _lines(
                words, 0, first_center - 45, 135 if compact_layout else 180
            )
            row_y: dict[MetricCode, tuple[float, str]] = {}
            for y, label in label_lines:
                for code, pattern in ROW_PATTERNS.items():
                    if code not in row_y and re.search(pattern, label, re.IGNORECASE):
                        row_y[code] = (y, label)
            current_tax = next(
                (
                    (y, label)
                    for y, label in label_lines
                    if re.search(r"^(?:current|cun\s*ent)\s+tax$", label, re.I)
                ),
                None,
            )
            deferred_tax = next(
                (
                    (y, label)
                    for y, label in label_lines
                    if re.search(r"^deferred tax$", label, re.I)
                ),
                None,
            )
            oci = next(
                (
                    y
                    for y, label in label_lines
                    if label.lower().startswith("other comprehensive income")
                ),
                None,
            )
            for index, (identity, center) in enumerate(headers):
                left = (
                    (headers[index - 1][1] + center) / 2 if index else first_center - 45
                )
                right = (
                    (center + headers[index + 1][1]) / 2
                    if index + 1 < len(headers)
                    else page.rect.width
                )
                period = RawPeriod(identity, scope)
                for code, (y, label) in row_y.items():
                    value = _number(words, left, right, y)
                    # Compact scanned statements in whole crores sometimes OCR
                    # a thousands separator as a decimal point (1.068 vs 1,068).
                    # Omit that cell rather than publish an unchecked value.
                    if (
                        compact_layout
                        and value is not None
                        and code not in (MetricCode.BASIC_EPS, MetricCode.DILUTED_EPS)
                        and value != value.to_integral_value()
                    ):
                        value = None
                    if value is not None:
                        period.facts[code] = RawFact(
                            code,
                            value,
                            (
                                OriginalUnit.INR_PER_SHARE
                                if code
                                in (MetricCode.BASIC_EPS, MetricCode.DILUTED_EPS)
                                else unit
                            ),
                            page_number,
                            label,
                            identity.end,
                        )
                # Some PDFs corrupt the printed subtotal's text while leaving all
                # component cells and an independent PBT cell readable. Recover
                # only when both arithmetic paths agree; retain CALCULATED origin.
                expense_codes = (
                    MetricCode.EMPLOYEE_EXPENSE,
                    MetricCode.FINANCE_COST,
                    MetricCode.DEPRECIATION,
                    MetricCode.OTHER_EXPENSE,
                )
                if all(code in period.facts for code in expense_codes) and all(
                    code in period.facts
                    for code in (MetricCode.TOTAL_INCOME, MetricCode.PBT)
                ):
                    expenses = sum(
                        (period.facts[code].original_value for code in expense_codes),
                        Decimal(0),
                    )
                    income = period.facts[MetricCode.TOTAL_INCOME].original_value
                    pbt_printed = period.facts[MetricCode.PBT].original_value
                    reported_expenses = period.facts.get(MetricCode.TOTAL_EXPENSES)
                    if (
                        reported_expenses is None
                        or abs(reported_expenses.original_value - expenses)
                        > Decimal(".03")
                    ) and abs(income - expenses - pbt_printed) <= Decimal(".03"):
                        period.facts[MetricCode.TOTAL_EXPENSES] = RawFact(
                            MetricCode.TOTAL_EXPENSES,
                            expenses,
                            unit,
                            page_number,
                            "Calculated: expense components; checked against total income - printed PBT",
                            identity.end,
                            True,
                        )
                if current_tax and deferred_tax:
                    current = _number(words, left, right, current_tax[0])
                    deferred = _number(words, left, right, deferred_tax[0])
                    if current is not None and deferred is not None:
                        expected = current + deferred
                        tax_row_y = current_tax[0] + 30
                        printed = _number(words, left, right, tax_row_y)
                        if printed is not None and abs(printed - expected) <= Decimal(
                            ".02"
                        ):
                            period.facts[MetricCode.TOTAL_TAX_EXPENSE] = RawFact(
                                MetricCode.TOTAL_TAX_EXPENSE,
                                printed,
                                unit,
                                page_number,
                                "Total tax expense (checked against current + deferred tax)",
                                identity.end,
                            )
                        else:
                            period.facts[MetricCode.TOTAL_TAX_EXPENSE] = RawFact(
                                MetricCode.TOTAL_TAX_EXPENSE,
                                expected,
                                unit,
                                page_number,
                                "Calculated: current tax + deferred tax; printed subtotal unreadable",
                                identity.end,
                                True,
                            )
                pbt = period.facts.get(MetricCode.PBT)
                tax = period.facts.get(MetricCode.TOTAL_TAX_EXPENSE)
                if pbt is None:
                    income = period.facts.get(MetricCode.TOTAL_INCOME)
                    expenses = period.facts.get(MetricCode.TOTAL_EXPENSES)
                    if income and expenses:
                        period.facts[MetricCode.PBT] = pbt = RawFact(
                            MetricCode.PBT,
                            income.original_value - expenses.original_value,
                            unit,
                            page_number,
                            "Calculated: total income - total expenses",
                            identity.end,
                            True,
                        )
                if pbt and tax and oci:
                    expected_pat = pbt.original_value - tax.original_value
                    # The PAT row sits between total tax and other comprehensive income.
                    rows = sorted(
                        {
                            round((w[1] + w[3]) / 2, 1)
                            for w in words
                            if tax_row_y + 9 < (w[1] + w[3]) / 2 < oci - 5
                            and left <= (w[0] + w[2]) / 2 < right
                        }
                    )
                    for y in rows:
                        printed = _number(words, left, right, y)
                        if printed is not None and abs(
                            printed - expected_pat
                        ) <= Decimal(".02"):
                            period.facts[MetricCode.PAT] = RawFact(
                                MetricCode.PAT,
                                printed,
                                unit,
                                page_number,
                                "Profit for period/year (table row; checked against PBT - tax)",
                                identity.end,
                            )
                            break
                    if MetricCode.PAT not in period.facts:
                        period.facts[MetricCode.PAT] = RawFact(
                            MetricCode.PAT,
                            expected_pat,
                            unit,
                            page_number,
                            "Calculated: printed PBT - current and deferred tax; PAT cell unreadable",
                            identity.end,
                            True,
                        )
                _validate(period)
                periods.append(period)
    return periods


def _validate(period: RawPeriod) -> None:
    missing = CORE - period.facts.keys()
    if missing:
        period.issues.append(
            "Missing or unreadable table rows: "
            + ", ".join(sorted(code.value for code in missing))
        )
    facts = period.facts
    checks = (
        (
            MetricCode.TOTAL_INCOME,
            (MetricCode.REVENUE_FROM_OPERATIONS, MetricCode.OTHER_INCOME),
            "+",
        ),
        (MetricCode.PBT, (MetricCode.TOTAL_INCOME, MetricCode.TOTAL_EXPENSES), "-"),
        (MetricCode.PAT, (MetricCode.PBT, MetricCode.TOTAL_TAX_EXPENSE), "-"),
        (MetricCode.PAT, (MetricCode.PAT_ATTRIBUTABLE, MetricCode.NON_CONTROLLING_INTEREST), "+"),
    )
    for target, (first, second), operation in checks:
        if {target, first, second} <= facts.keys():
            a, b = facts[first].original_value, facts[second].original_value
            expected = a + b if operation == "+" else a - b
            rounding = max(Decimal(".03"), sum((Decimal(1).scaleb(facts[code].original_value.as_tuple().exponent)/2
                for code in (target, first, second)), Decimal(0)))
            exception = facts.get(MetricCode.EXCEPTIONAL_ITEMS)
            if target == MetricCode.PBT and exception and abs(facts[target].original_value-expected) > rounding:
                effect = exceptional_effect(exception.original_value, exception.label)
                if effect is not None:
                    expected += effect
            if abs(facts[target].original_value - expected) > rounding:
                period.issues.append(
                    f"{target.value} does not reconcile with the table"
                )
