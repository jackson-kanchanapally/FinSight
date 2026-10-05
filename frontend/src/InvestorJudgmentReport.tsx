import { useState, type ReactNode } from "react";
import type { AIAnalysis, InvestorReport, TestedPERange } from "./api";
import { TestedPEPanel } from "./TestedPEPanel";
import { QuarterlyReviewPanel } from "./QuarterlyReview";

type ReportProps = {
  analysis: AIAnalysis; report: InvestorReport; symbol: string; scope: string;
  evidence: (ids: string[]) => ReactNode; onScenarioSaved: () => void;
};

export function InvestorJudgmentReport(props: ReportProps) {
  const review = props.analysis.structured_output?.quarterly_review || props.report.quarterly_review;
  if (!review) return <InvestorJudgmentDetails {...props} />;
  return <>
    <QuarterlyReviewPanel analysis={props.analysis} report={props.report} review={review} evidence={props.evidence} />
    <details className="ai-supporting">
      <summary>Financial calculations, valuation scenarios and full evidence</summary>
      <InvestorJudgmentDetails {...props} />
    </details>
  </>;
}

function InvestorJudgmentDetails({
  analysis,
  report,
  symbol,
  scope,
  evidence,
  onScenarioSaved,
}: {
  analysis: AIAnalysis;
  report: InvestorReport;
  symbol: string;
  scope: string;
  evidence: (ids: string[]) => ReactNode;
  onScenarioSaved: () => void;
}) {
  const [assessment, setAssessment] = useState<TestedPERange | null>(null);
  const tested = assessment || report.tested_pe;
  const quality = analysis.structured_output?.quality_report;
  const format = (value?: string | null, suffix = "") =>
    value == null
      ? "Unavailable"
      : `${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}${suffix}`;
  const label = (value?: string) =>
    (value || "INSUFFICIENT_EVIDENCE").replaceAll("_", " ");
  const note = (title: string, text?: string, ids: string[] = []) => (
    <section className="ai-group">
      <h4>{title}</h4>
      <p>{text}</p>
      {evidence(ids)}
    </section>
  );
  return (
    <div className="investor-report">
      <div className="ai-verdict-summary investor-overview">
        {[
          ["Results", label(report.results.verdict)],
          ["Results confidence", report.results.confidence],
          ["Valuation", label(tested?.verdict || report.valuation.verdict)],
          [
            "Valuation confidence",
            tested?.confidence || report.valuation.confidence,
          ],
          ["Current P/E", format(tested?.current_pe, "×")],
          [
            "Tested P/E range",
            tested?.supported_pe_range
              ? `${tested.supported_pe_range.lower}–${tested.supported_pe_range.upper}×`
              : "Missing verified inputs",
          ],
          [
            "Required EPS growth",
            format(
              tested?.reverse_expectations?.required_eps_cagr_pct,
              "% CAGR",
            ),
          ],
          ["Investor stance", label(report.investor_stance)],
        ].map(([title, value]) => (
          <div key={title}>
            <span>{title}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <section className="ai-group investor-conclusion">
        <h4>Investor Conclusion</h4>
        <p>{report.investor_conclusion?.text}</p>
        {evidence(report.investor_conclusion?.evidence_ids || [])}
      </section>
      <details className="ai-supporting">
        <summary>Results Verdict · {label(report.results.verdict)}</summary>
        <p>{report.results.reasons.join(" · ")}</p>
        <p>{report.results.methodology}</p>
        {evidence(report.results.evidence_ids)}
      </details>
      {note(
        "Valuation Verdict",
        report.valuation.reason,
        tested?.supported_pe_range ? ["A_BASE"] : [],
      )}
      {note(
        "Investor Stance",
        `${label(report.investor_stance)}. A research posture based on business evidence and the explicit valuation scenario.`,
      )}
      <TestedPEPanel
        symbol={symbol}
        scope={scope}
        onResult={setAssessment}
        onSaved={onScenarioSaved}
      />
      {quality &&
        note(
          "Operating Performance",
          quality.operating_performance.text,
          quality.operating_performance.evidence_ids,
        )}
      <section className="ai-group">
        <h4>Earnings Quality</h4>
        <p>{report.earnings_quality?.text}</p>
        {report.earnings_quality?.pat_bridge?.length ? (
          <>
            <p>
              Arithmetic PAT bridge · {report.earnings_quality.basis}. Expense
              and tax increases reduce profit. Components explain the accounting
              movement; business causes require separate evidence.
            </p>
            <div className="scenario-table-wrap">
              <table className="scenario-table">
                <thead>
                  <tr>
                    <th>Component</th>
                    <th>Contribution to PAT change</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {report.earnings_quality.pat_bridge.map((row) => (
                    <tr key={row.component}>
                      <td>{label(row.component)}</td>
                      <td>{format(row.contribution_crore, " Cr")}</td>
                      <td>{evidence(row.evidence_ids)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
        {report.earnings_quality?.cash_conversion?.length ? (
          report.earnings_quality.cash_conversion.map((row) => (
            <p key={row.label}>
              {row.label}: {row.value}× {evidence(row.evidence_ids)}
            </p>
          ))
        ) : (
          <p>
            Cash conversion is unassessed; results confidence reflects this gap.
          </p>
        )}
      </section>
      <section className="ai-group">
        <h4>Management Commentary</h4>
        {report.management_statements.length ? (
          report.management_statements.map((row) => (
            <article key={row.evidence_id}>
              <p>
                Management claim ·{" "}
                {row.speaker_name || row.speaker_role || "Attributed speaker"}:
                “{row.raw_statement}”
              </p>
              <a href={row.document_url} target="_blank" rel="noreferrer">
                Source page {row.page_number}
              </a>
            </article>
          ))
        ) : (
          <p>No attributed management statement is linked to this period.</p>
        )}
      </section>
      <section className="ai-group">
        <h4>Guidance vs Actual</h4>
        {report.management_guidance.length ? (
          report.management_guidance.map((row) => (
            <p key={row.id}>
              {label(row.metric)} · {row.target_period}: {row.lower_bound}–
              {row.upper_bound} {row.unit} · {label(row.execution_outcome)}{" "}
              {evidence([row.statement_evidence_id])}
            </p>
          ))
        ) : (
          <p>No comparable, dated guidance and actual pair is available.</p>
        )}
        {report.claim_comparisons.map((row) => (
          <p key={row.statement_evidence_id}>
            {label(row.status)} · {row.note}{" "}
            {evidence([
              row.statement_evidence_id,
              ...row.financial_evidence_ids,
            ])}
          </p>
        ))}
      </section>
      {quality &&
        note(
          "Historical / Seasonal Context",
          quality.seasonality_context.text,
          quality.seasonality_context.evidence_ids,
        )}
      {note("Valuation Analysis", tested?.methodology)}
      {(
        [
          ["What Supports the Valuation?", report.valuation_support],
          ["What Challenges the Valuation?", report.valuation_challenges],
        ] as const
      ).map(([title, notes]) => (
        <section className="ai-group" key={title}>
          <h4>{title}</h4>
          {notes.length ? (
            notes.map((row, index) => (
              <p key={index}>
                {row.text} {evidence(row.evidence_ids)}
              </p>
            ))
          ) : (
            <p>
              No additional verified operating evidence is available for this
              side of the assessment.
            </p>
          )}
        </section>
      ))}
      {(
        [
          ["What Would Strengthen the Case?", report.strengthen_case],
          ["What Would Weaken the Case?", report.weaken_case],
          ["Research Gaps", report.research_gaps],
        ] as const
      ).map(([title, lines]) => (
        <section className="ai-group" key={title}>
          <h4>{title}</h4>
          <ul>
            {lines?.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      ))}
      <section className="ai-group">
        <h4>Data Quality</h4>
        <p>
          Results use validated facts with page evidence. Scenario assumptions
          are separate from verified observations.{" "}
          {analysis.model_provider === "deterministic"
            ? "This report is generated deterministically."
            : `Interpretation: ${analysis.model_provider} / ${analysis.model_name}.`}
        </p>
        {analysis.structured_output?.data_quality_notes.map((line) => (
          <p key={line}>{line}</p>
        ))}
        {!!analysis.validation_diagnostics?.length && (
          <details>
            <summary>Model validation diagnostics</summary>
            {analysis.validation_diagnostics.map((row, index) => (
              <div key={index}>
                <strong>
                  {row.code} · {row.field || "response"}
                </strong>
                <p>Model value: {row.model_value || "not supplied"}</p>
                <pre>{JSON.stringify(row.expected, null, 2)}</pre>
              </div>
            ))}
          </details>
        )}
        <p className="ai-metadata">
          Generated {new Date(analysis.created_at).toLocaleString()} ·{" "}
          {analysis.prompt_version}
        </p>
      </section>
    </div>
  );
}
