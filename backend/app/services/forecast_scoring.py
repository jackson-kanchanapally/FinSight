"""Versioned, Decimal-only scoring. No fitting and no outcome-derived confidence."""
from collections import defaultdict
from decimal import Decimal

D = Decimal
VERSION = "FORECAST_ACCURACY_V2"
FLOOR = D("0.000001")
SCORED = {"MET", "PARTIALLY_MET", "MISSED"}


def finite(value):
    if value is None:
        return None
    number = D(str(value))
    if not number.is_finite():
        raise ValueError("NON_FINITE_FORECAST_VALUE")
    return number


def point_errors(predicted, actual, *, percentage_units=False, baseline=None):
    predicted, actual, baseline = map(finite, (predicted, actual, baseline))
    if predicted is None or actual is None:
        return dict(signed_error=None, absolute_error=None, absolute_percentage_error=None,
                    directional_correctness=None)
    error = predicted - actual
    sign = lambda value: (value > 0) - (value < 0)
    return dict(signed_error=str(error), absolute_error=str(abs(error)),
                absolute_percentage_error=str(abs(error) / abs(actual) * 100)
                if not percentage_units and abs(actual) > FLOOR else None,
                directional_correctness=sign(predicted - baseline) == sign(actual - baseline)
                if baseline is not None else None)


def continuous_metrics(pairs, *, percentage_units=False):
    """Equal-weight comparable points; WAPE alone weights by absolute actual size.

    Negative actuals support absolute/signed errors and WAPE. MAPE is restricted
    to positive actuals and is secondary; its distinct denominator is exposed.
    Missing pairs are omitted and counted. Outliers are retained, never clipped.
    Bounds may be absent; coverage uses only complete ordered intervals.
    """
    valid, intervals = [], []
    for predicted, actual, low, high in pairs:
        predicted, actual, low, high = map(finite, (predicted, actual, low, high))
        if predicted is None or actual is None:
            continue
        valid.append((predicted, actual))
        if low is not None and high is not None:
            if low > high:
                raise ValueError("UNORDERED_FORECAST_INTERVAL")
            intervals.append((low <= actual <= high, high - low))
    n = len(valid)
    errors = [p - a for p, a in valid]
    total = sum((abs(e) for e in errors), D(0))
    denominator = sum((abs(a) for _, a in valid), D(0))
    percentages = [abs(p-a)/a*100 for p, a in valid if not percentage_units and a > FLOOR]
    return dict(n=n, missing_n=len(pairs)-n, mae=str(total/n) if n else None,
                rmse=str((sum(e*e for e in errors)/n).sqrt()) if n else None,
                bias=str(sum(errors)/n) if n else None,
                wape_pct=str(total/denominator*100) if not percentage_units and denominator > FLOOR else None,
                mape_pct=str(sum(percentages)/len(percentages)) if percentages else None,
                percentage_n=len(percentages), interval_n=len(intervals),
                interval_coverage=str(D(sum(c for c, _ in intervals))/len(intervals)) if intervals else None,
                mean_interval_width=str(sum(w for _, w in intervals)/len(intervals)) if intervals else None)


def wilson(successes, n):
    """95% binomial Wilson interval; descriptive, assumes independent outcomes."""
    if not n:
        return None
    p, z2 = D(successes)/n, D("1.96")**2
    center = (p + z2/(2*n))/(1+z2/n)
    half = D("1.96")*((p*(1-p)+z2/(4*n))/n).sqrt()/(1+z2/n)
    return {"lower": str(max(D(0), center-half)), "upper": str(min(D(1), center+half))}


def calibration(observations, minimum):
    """Buckets preserve metric/horizon grouping supplied by the caller.

    Observations carry frozen predictions and primary outcomes, never hindcasts.
    Labels describe success under frozen tolerances, not probabilities. Nominal
    intervals and explicit MET probabilities are evaluated separately.
    """
    buckets = defaultdict(list)
    for row in observations:
        if row.get("probability") is not None:
            p = finite(row["probability"])
            if not 0 <= p <= 1:
                raise ValueError("INVALID_PROBABILITY")
            buckets[("MET_PROBABILITY", str(min(int(p*10), 9)))].append(row)
        if row.get("confidence_label"):
            buckets[("CONFIDENCE_LABEL", row["confidence_label"])].append(row)
        if row.get("interval_probability") is not None and row.get("low") is not None and row.get("high") is not None:
            p = finite(row["interval_probability"])
            if not 0 < p < 1:
                raise ValueError("INVALID_INTERVAL_PROBABILITY")
            buckets[("PREDICTION_INTERVAL", str(p))].append(row)
    result = []
    for (kind, bucket), rows in sorted(buckets.items()):
        n = len(rows)
        interval = kind == "PREDICTION_INTERVAL"
        successes = sum(finite(r["low"]) <= finite(r["actual"]) <= finite(r["high"]) if interval else r["met"] for r in rows)
        expected = (sum(finite(r["interval_probability"] if interval else r["probability"]) for r in rows)/n
                    if kind != "CONFIDENCE_LABEL" else None)
        brier = (sum((finite(r["probability"])-int(r["met"]))**2 for r in rows)/n
                 if kind == "MET_PROBABILITY" else None)
        result.append(dict(kind=kind, bucket=bucket, n=n, observed=str(D(successes)/n),
                           expected=str(expected) if expected is not None else None,
                           brier=str(brier) if brier is not None and n >= minimum else None,
                           mean_interval_width=str(sum(finite(r["high"])-finite(r["low"]) for r in rows)/n) if interval else None,
                           uncertainty_95=wilson(successes, n),
                           status="SUFFICIENT_DATA" if n >= minimum else "INSUFFICIENT_DATA",
                           evaluation_ids=[r["evaluation_id"] for r in rows]))
    return result
