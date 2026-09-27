from app.ai.analyst.schemas import AnalystOutput
from app.ai.evaluation.esds import evaluate_esds


def test_evaluation_is_inspectable_and_flags_missing_answers():
    output = AnalystOutput.model_validate({"summary": "Revenue increased YoY.", "summary_evidence_ids": ["M1"],
        "positives": [], "concerns": [], "material_changes": [], "questions": [],
        "data_quality_notes": []})
    report = evaluate_esds(output, {}, 2026)
    assert report["checks"]["revenue_change_explained"]
    assert not report["checks"]["pat_change_explained"]
    assert not report["checks"]["improvement_identified"]
    assert not report["all_passed"]
