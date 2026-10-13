"""Explainable changes between immutable, verified evidence snapshots."""
from datetime import date
from copy import deepcopy
from decimal import Decimal
from app.services.thesis_monitoring_evidence import VERSION

POLICY = {"revenue_change_pct": "5", "profit_change_pct": "10", "margin_change_pp": "1",
          "comparison": "Same period last year; same-period corrections compare with the stored revision.",
          "note": "Materiality thresholds flag research developments; they do not establish structural change."}


def historical_registry(registry, run_id):
    """Historical metric drill-down must resolve to the historical input values."""
    result = {}
    for key, value in registry.items():
        row = deepcopy(value)
        for field in ("input_evidence_ids",):
            if field in row:
                row[field] = [f"P{run_id}:{k}" for k in row[field]]
        if row.get("source_statement_id"):
            row["source_statement_id"] = f"P{run_id}:{row['source_statement_id']}"
        if row.get("actual_outcome", {}).get("evidence_ids"):
            row["actual_outcome"]["evidence_ids"] = [f"P{run_id}:{k}" for k in row["actual_outcome"]["evidence_ids"]]
        result[f"P{run_id}:{key}"] = row
    return result


def change(key, category, classification, reason, ids, *, material=False, basis="CONFIRMED", **extra):
    return dict(key=key, category=category, classification=classification, reason=reason,
                evidence_ids=list(dict.fromkeys(ids)), material=material, basis=basis, **extra)


def financial_changes(previous, current, previous_id):
    result = []
    for code, threshold in (("REVENUE_FROM_OPERATIONS", "5"), ("PAT", "10"),
                             ("PAT_MARGIN", "1"), ("EBITDA_MARGIN", "1")):
        # Assess both annual and quarterly evidence without mixing their bases.
        kinds = sorted({r.get("period_type") for r in current.values()
                        if r.get("metric_code") == code and r.get("period_type") in {"ANNUAL", "QUARTERLY"}})
        if not kinds:
            result.append(change(code, "FINANCIAL", "INSUFFICIENT_EVIDENCE",
                f"Verified {code.lower().replace('_', ' ')} is unavailable.", [], basis="INSUFFICIENT_EVIDENCE"))
        for kind in kinds:
            def candidates(registry):
                return [(k, r) for k, r in registry.items() if r.get("kind") in {"FACT", "METRIC"}
                        and r.get("metric_code") == code and r.get("period_type") == kind]
            values = candidates(current)
            key, now = max(values, key=lambda v: v[1]["period_end"])
            label = f"{kind.title()} {code.lower().replace('_', ' ')}"
            identity = f"{code}:{kind}"
            old_values = candidates(previous)
            stored = next(((k, r) for k, r in old_values if r["period_end"] == now["period_end"]
                           and r["unit"] == now["unit"]), None)
            if stored and Decimal(stored[1]["value"]) == Decimal(now["value"]):
                result.append(change(identity, "FINANCIAL", "UNCHANGED",
                    f"{label}: no change in the verified value for {now['period_end']} since the selected baseline.",
                    [key, f"P{previous_id}:{stored[0]}"], current_value=now["value"], previous_value=stored[1]["value"]))
                continue
            correction = bool(stored)
            if stored:
                prior_key, prior = f"P{previous_id}:{stored[0]}", stored[1]
            else:
                end = date.fromisoformat(now["period_end"])
                # Calendar year matching also handles leap-day year ends.
                prior_end = end.replace(year=end.year - 1, day=min(end.day, 28)) if end.month == 2 else end.replace(year=end.year - 1)
                comparable = [(k, r) for k, r in values if r["period_end"] == prior_end.isoformat()
                              and r["unit"] == now["unit"]]
                if not comparable:
                    result.append(change(identity, "FINANCIAL", "INSUFFICIENT_EVIDENCE",
                        f"{label}: a verified same-period comparison is missing. Sequential movement alone does not establish deterioration.",
                        [key], basis="INSUFFICIENT_EVIDENCE", current_value=now["value"]))
                    continue
                prior_key, prior = comparable[0]
            value, baseline = Decimal(now["value"]), Decimal(prior["value"])
            margin = code.endswith("MARGIN")
            if not margin and baseline <= 0:
                result.append(change(identity, "FINANCIAL", "INSUFFICIENT_EVIDENCE",
                    f"{label}: the comparison base is non-positive; a growth percentage would be misleading.",
                    [key, prior_key], basis="INSUFFICIENT_EVIDENCE", current_value=str(value), previous_value=str(baseline)))
                continue
            delta = value - baseline if margin else (value - baseline) / baseline * 100
            material = abs(delta) >= Decimal(threshold)
            classification = ("IMPROVING" if delta > 0 else "DETERIORATING") if material else "UNCHANGED"
            unit = "percentage points" if margin else "%"
            basis = "corrected stored result" if correction else "same period last year"
            result.append(change(identity, "FINANCIAL", classification,
                f"{label}: {value} versus {baseline} ({delta:+.2f} {unit}; {basis}). "
                + ("The change meets the monitoring threshold; persistence still needs evidence." if material else "The change is below the monitoring threshold."),
                [key, prior_key], material=material, current_value=str(value), previous_value=str(baseline),
                delta=str(delta), delta_unit="PERCENTAGE_POINTS" if margin else "PERCENT", corrected=correction))
    return result


def detect_changes(previous, current, previous_id=None):
    changes = financial_changes(previous, current, previous_id)
    for key, row in current.items():
        old = previous.get(key)
        if old and old.get("revision") == row.get("revision"):
            continue
        if row["kind"] == "FACT" and old and old.get("value") != row.get("value") and not any(
                c.get("corrected") and key in c["evidence_ids"] for c in changes):
            value, baseline = Decimal(row["value"]), Decimal(old["value"])
            comparable = row.get("unit") == old.get("unit") and baseline > 0
            favorable = row["metric_code"] in {"REVENUE_FROM_OPERATIONS", "PAT", "BASIC_EPS", "DILUTED_EPS"}
            delta = (value - baseline) / baseline * 100 if comparable else None
            threshold = Decimal("5" if row["metric_code"] == "REVENUE_FROM_OPERATIONS" else "10")
            material = bool(delta is not None and abs(delta) >= threshold)
            classification = ("IMPROVING" if delta > 0 else "DETERIORATING") if material and favorable else "UNCHANGED" if comparable and favorable else "INSUFFICIENT_EVIDENCE"
            changes.append(change(f"REVISION:{key}", "FINANCIAL", classification,
                f"The stored {row['metric_code'].lower().replace('_', ' ')} for {row['period_end']} was revised from {baseline} to {value} {row['unit']}. "
                "This is a correction to prior evidence, not a new period of performance.",
                [key, f"P{previous_id}:{key}"], material=material, corrected=True,
                basis="CONFIRMED" if comparable and favorable else "INSUFFICIENT_EVIDENCE"))
        elif row["kind"] == "CHECKPOINT_RECORD":
            status = row["status"]
            ids = [key, row["source_statement_id"], *row["actual_outcome"].get("evidence_ids", [])]
            if old:
                ids.append(f"P{previous_id}:{key}")
            confirmed = status in {"DELIVERED", "MISSED"} and any(current[k]["kind"] in {"FACT", "METRIC"} for k in ids if k in current)
            classification = ("IMPROVING" if status == "DELIVERED" else "DETERIORATING") if confirmed else "INSUFFICIENT_EVIDENCE"
            if status == "WITHDRAWN":
                classification = "DETERIORATING"
            changes.append(change(key, "EXECUTION", classification,
                f"{row['metric'].replace('_', ' ').title()}: {status.lower().replace('_', ' ')}. "
                + (row["actual_outcome"].get("note") or "A verified outcome is still required."), ids,
                material=confirmed or status in {"WITHDRAWN", "DELAYED"},
                basis="CONFIRMED" if confirmed else "MANAGEMENT_CLAIM" if status in {"WITHDRAWN", "DELAYED"} else "INSUFFICIENT_EVIDENCE",
                resolved_risk=bool(old and old.get("status") == "MISSED" and status == "DELIVERED")))
        elif row["kind"] == "GUIDANCE":
            matches = [(k, v) for k, v in {**previous, **current}.items() if v.get("kind") == "GUIDANCE"
                and k != key and all(v.get(f) == row.get(f) for f in ("metric_code", "target_period", "unit"))
                and v["published_at"] < row["published_at"]]
            comparison = (key, old) if old else max(matches, key=lambda v: v[1]["published_at"], default=None)
            if not comparison:
                changes.append(change(key, "GUIDANCE", "INSUFFICIENT_EVIDENCE",
                    "Management provided guidance; a comparable previous target is unavailable. Guidance is not delivered performance.",
                    [key, row["source_statement_id"]], basis="MANAGEMENT_CLAIM"))
                continue
            prior_key, prior = comparison
            lower = Decimal(row["lower"]) - Decimal(prior["lower"])
            upper = Decimal(row["upper"]) - Decimal(prior["upper"])
            moved = bool(lower or upper)
            direction = ("maintained" if not moved else "raised" if lower >= 0 and upper >= 0
                         else "lowered" if lower <= 0 and upper <= 0 else "revised")
            favorable = row["metric_code"] in {"REVENUE", "REVENUE_GROWTH", "PAT", "PAT_GROWTH", "PAT_MARGIN", "EBITDA_MARGIN", "EPS_GROWTH"}
            classification = ("IMPROVING" if direction == "raised" else "DETERIORATING") if favorable and direction in {"raised", "lowered"} else "UNCHANGED" if not moved else "INSUFFICIENT_EVIDENCE"
            reference = f"P{previous_id}:{prior_key}" if prior_key in previous else prior_key
            changes.append(change(key, "GUIDANCE", classification,
                f"Management {direction} {row['metric_code'].replace('_', ' ').lower()} guidance for {row['target_period']}: "
                f"{prior['lower']}–{prior['upper']} to {row['lower']}–{row['upper']} {row['unit']}. This remains a management target."
                + (" The range bounds moved in opposite directions; no upgrade or downgrade is established." if direction == "revised" else ""),
                [key, row["source_statement_id"], reference], material=moved, basis="MANAGEMENT_CLAIM"))
        elif row["kind"] == "MANAGEMENT_STATEMENT" and row.get("statement_type") == "GUIDANCE_WITHDRAWN":
            changes.append(change(key, "GUIDANCE", "DETERIORATING",
                "Management explicitly withdrew guidance. The prior target can no longer support the investment expectation; this does not establish a financial loss.",
                [key], material=True, basis="MANAGEMENT_CLAIM"))
        elif row["kind"] == "MANAGEMENT_STATEMENT" and (row.get("topic") == "RISKS" or row.get("materiality_reason") == "EXECUTION_RISK"):
            changes.append(change(key, "RISK", "INSUFFICIENT_EVIDENCE",
                "A new or revised management risk statement needs follow-up; its financial effect and resolution are not yet verified.",
                [key], material=True, basis="MANAGEMENT_CLAIM"))
        elif row["kind"] == "CORPORATE_DISCLOSURE":
            changes.append(change(key, "CORPORATE_EVENT", "INSUFFICIENT_EVIDENCE",
                f"New or revised {row['event_type'].lower().replace('_', ' ')} disclosure. Confirm its scale, terms and effect on the thesis from the source.",
                [key], material=True, basis="INSUFFICIENT_EVIDENCE"))
    # A disappearing fact is missing evidence, never proof that a risk resolved.
    for key, row in previous.items():
        if key not in current and row.get("kind") in {"FACT", "METRIC", "CHECKPOINT_RECORD"}:
            changes.append(change(f"REMOVED:{key}", "EVIDENCE", "INSUFFICIENT_EVIDENCE",
                "Previously used evidence is no longer verified or available; the historical assessment is retained.",
                [f"P{previous_id}:{key}"], basis="INSUFFICIENT_EVIDENCE"))
    return changes


def assessment(previous_run, registry, entries):
    previous = previous_run.evidence_registry if previous_run else {}
    changes = detect_changes(previous, registry, previous_run.id if previous_run else None)
    states, prior_states, catalysts, questions = [], [], [], []
    for entry in entries:
        definition, item = entry["revision"].definition, entry["assessment"]
        states.append({"pillar_id": entry["pillar"].id, "name": definition["name"], "status": item["new_status"]})
        prior_states.append({"pillar_id": entry["pillar"].id, "name": definition["name"], "status": entry["previous"]})
        prior_evaluation = entry.get("prior_evaluation")
        prior_outcomes = {o["expectation_key"]: o for o in prior_evaluation.expectations} if prior_evaluation else {}
        for outcome in entry["outcomes"]:
            old = prior_outcomes.get(outcome["expectation_key"])
            if (outcome.get("checkpoint_id") or outcome["status"] not in {"DELIVERED", "MISSED"}
                    or not outcome["evidence_ids"] or old and old["status"] == outcome["status"] and old["revision_id"] == outcome["revision_id"]):
                continue
            passed = outcome["status"] == "DELIVERED"
            resolved = bool(old and old["status"] == "MISSED" and old["revision_id"] == outcome["revision_id"] and passed)
            changes.append(change(f"TEST:{outcome['expectation_key']}", "EXECUTION", "IMPROVING" if passed else "DETERIORATING",
                f"{definition['name']}: a verified value {'meets' if passed else 'misses'} the stored investor requirement. "
                + ("The previously recorded shortfall is resolved on the corrected evidence." if resolved else "This checks the stated requirement for its exact reporting period."),
                outcome["evidence_ids"], material=True, resolved_risk=resolved))
        for test in entry["tests"]:
            outcome = next((o for o in entry["outcomes"] if o["revision_id"] == test.id), None)
            if outcome and outcome["status"] in {"DELIVERED", "WITHDRAWN"}:
                continue
            source = registry.get(f"K{test.checkpoint_id}", {})
            catalysts.append({"description": test.definition["description"],
                "target_date": source.get("target_date") or test.definition.get("target_period_end"),
                "evidence_ids": [f"K{test.checkpoint_id}"] if source else [],
                "basis": "MANAGEMENT_CLAIM" if source else "INVESTOR_REQUIREMENT"})
        questions.extend(entry["gaps"])
    questions.extend(c["reason"] for c in changes if c["classification"] == "INSUFFICIENT_EVIDENCE")
    improving = [c for c in changes if c["classification"] == "IMPROVING"]
    deteriorating = [c for c in changes if c["classification"] == "DETERIORATING"]
    conflicting = bool(improving and deteriorating)
    measurable = any(c["basis"] == "CONFIRMED" and c["classification"] != "INSUFFICIENT_EVIDENCE" for c in changes)
    confidence = "LOW" if not measurable else "MEDIUM" if conflicting or questions else "HIGH"
    reason = ("Verified developments are mixed; supportive and adverse evidence are shown separately." if conflicting else
              "Material developments require review against the investment requirements." if any(c["material"] for c in changes) else
              "No material change is established by the available verified evidence.")
    return {"version": VERSION, "policy": POLICY, "first_assessment": previous_run is None,
        "previous_state": prior_states, "current_state": states, "changes": changes,
        "supporting_evidence": list(dict.fromkeys(k for c in improving for k in c["evidence_ids"])),
        "contradicting_evidence": list(dict.fromkeys(k for c in deteriorating for k in c["evidence_ids"])),
        "confidence": confidence, "confidence_reason": "Confidence describes evidence coverage and agreement, not the probability of investment success.",
        "conflicting_evidence": conflicting, "summary": reason, "unresolved_questions": list(dict.fromkeys(questions)),
        "next_catalysts": catalysts, "evidence_revisions": {k: v.get("revision") for k, v in registry.items()},
        "previous_evidence": historical_registry(previous, previous_run.id) if previous_run else {},
        "model": None, "method": "DETERMINISTIC"}
