import type { ReactNode } from "react";
import type { AIAnalysis, InvestorReport, QuarterlyReview, ReviewItem } from "./api";

export function QuarterlyReviewPanel({ analysis, report, review, evidence }: {
  analysis: AIAnalysis;
  report: InvestorReport;
  review: QuarterlyReview;
  evidence: (ids: string[]) => ReactNode;
}) {
  const label = (value: string) => value.replaceAll("_", " ");
  const item = (row: ReviewItem, index: number) => (
    <article key={index} className="quarterly-review-item">
      <small>{label(row.basis)}{row.basis === "MANAGEMENT_CLAIM" ? ` · ${row.signal}` : ""}</small>
      <p>{row.text}</p>
      {evidence(row.evidence_ids)}
    </article>
  );
  const section = (title: string, rows: ReviewItem[], empty?: string) => (
    <section className="ai-group">
      <h4>{title}</h4>
      {rows.length ? rows.map(item) : <p className="muted">{empty || "No additional material evidence is available."}</p>}
    </section>
  );
  const previous = (report.checkpoints || []).filter(row => row.is_previous);
  return (
    <div className="investor-report quarterly-review">
      <section className="ai-group investor-conclusion">
        <h3>Quarterly Investor View</h3>
        <p className="muted">{analysis.status === "SUCCESS" ? "Evidence-checked investor interpretation" : "Deterministic evidence review"}</p>
        {analysis.structured_output?.review_trace?.generation_status === "PARTIAL_VALIDATED" &&
          <p className="financial-footnote">Some model statements did not pass evidence checks. The remaining review combines supported interpretation with verified evidence.</p>}
        {review.quarterly_investor_view.map(item)}
      </section>
      <div className="ai-verdict-summary">
        <div><span>Results verdict</span><strong>{label(report.results.verdict)}</strong></div>
        <div><span>Valuation verdict</span><strong>{label(report.valuation.verdict)}</strong><small>Conditional on disclosed scenarios</small></div>
      </div>
      {section("Key Developments", review.key_developments)}
      {section("Management Signals", review.management_signals, "No material attributed management signal is available.")}
      {section("What Changed", review.what_changed, report.history_note)}
      <section className="ai-group">
        <h4>Management Execution</h4>
        <p>{report.execution_summary}</p>
        {!!previous.length && <div className="scenario-table-wrap"><table className="scenario-table">
          <thead><tr><th>Previous commitment</th><th>Expected by</th><th>Outcome</th><th>Evidence</th></tr></thead>
          <tbody>{previous.map(row => <tr key={row.id}>
            <td>{row.commitment}</td><td>{row.target_period || row.target_date || "Not specified"}</td>
            <td>{label(row.status)}{row.actual_outcome.actual_value != null && ` · ${row.actual_outcome.actual_value}`}<small>{row.actual_outcome.note}</small></td>
            <td><a href={row.document_url} target="_blank" rel="noreferrer">Commitment page {row.page}</a>
              {row.actual_outcome.actual_sources?.map(source => <p key={source.fact_id}><a href={source.document_url} target="_blank" rel="noreferrer">Verified outcome · page {source.page ?? "unavailable"}</a></p>)}
              {evidence(row.actual_outcome.evidence_ids)}</td>
          </tr>)}</tbody>
        </table></div>}
      </section>
      <section className="ai-group">
        <h4>Valuation Implication</h4>
        <strong>{label(review.valuation_implication.status)}</strong>
        <p>{review.valuation_implication.text}</p>
        {evidence(review.valuation_implication.evidence_ids)}
      </section>
      {section("Key Risks", review.key_risks)}
      {section("Watch Next Quarter", review.watch_next_quarter)}
      {!!report.checkpoints?.length && <details className="ai-supporting">
        <summary>Investor checkpoints · {report.checkpoints.length}</summary>
        {report.checkpoints.map(row => <article key={row.id}>
          <strong>{label(row.metric)} · {label(row.status)}</strong>
          <p>{row.commitment}</p>
          <p>Target: {row.target_period || "No explicit period"}{row.target_date && ` · deadline ${row.target_date}`}. Evaluated as of {row.evaluated_as_of}.</p>
          {row.measurement_note && <p>{row.measurement_note}</p>}
          <a href={row.document_url} target="_blank" rel="noreferrer">Commitment source · page {row.page}</a>
          {evidence(row.actual_outcome.evidence_ids)}
        </article>)}
      </details>}
      <section className="ai-group investor-conclusion">
        <h4>Investor Conclusion</h4>
        <p>{review.investor_conclusion.text}</p>
        {evidence(review.investor_conclusion.evidence_ids)}
      </section>
      <details className="ai-supporting">
        <summary>View full commentary · {report.full_statement_count ?? report.management_statements.length} statements</summary>
        <p>These are management claims. The complete record is retained for source inspection.</p>
        {report.source_documents.map(doc => <p key={doc.document_id}><a href={doc.document_url} target="_blank" rel="noreferrer">{label(doc.document_type)} · {doc.source}</a></p>)}
        {report.management_statements.map(row => <article key={row.evidence_id}>
          <p>{row.speaker_name || row.speaker_role}: “{row.raw_statement}”</p>
          <a href={row.document_url} target="_blank" rel="noreferrer">Source page {row.page_number}</a>
        </article>)}
      </details>
      {analysis.status === "VERIFIED_FALLBACK" && <p className="financial-footnote">{analysis.model_provider === "deterministic" ? "This review was generated from the verified evidence and deterministic calculations. Use Generate AI analysis for a model interpretation." : "The model interpretation was unavailable or did not pass evidence checks. Results, valuation and checkpoint outcomes above come from the verified evidence review."}</p>}
    </div>
  );
}
