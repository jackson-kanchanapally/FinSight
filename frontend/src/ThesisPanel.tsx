import { useEffect, useState } from "react";
import {
  thesisApi,
  type Evidence,
  type Expectation,
  type Impact,
  type Pillar,
  type Thesis,
} from "./thesisApi";
import "./thesis.css";
const label = (v?: string) => (v || "").replaceAll("_", " ");
const categories = [
  "GROWTH",
  "PROFITABILITY",
  "BUSINESS_QUALITY",
  "COMPETITIVE_POSITION",
  "EXECUTION",
  "MANAGEMENT",
  "CAPITAL_ALLOCATION",
  "BALANCE_SHEET",
  "CASH_GENERATION",
  "VALUATION",
  "RISKS",
];
const blankTest = (): Expectation => ({
  description: "",
  expectation_type: "QUALITATIVE",
  checkpoint_id: null,
  metric_code: null,
  operator: null,
  target_value: null,
  upper_value: null,
  unit: null,
  target_period_end: null,
  period_type: null,
  is_active: true,
});
export function ThesisEvidence({
  ids,
  registry,
}: {
  ids: string[];
  registry: Evidence;
}) {
  return (
    <div className="thesis-evidence">
      {ids.map((id) => {
        const row = registry[id];
        const page = row?.page || row?.page_number;
        const url =
          row?.document_url ||
          (row?.document_id
            ? `/api/documents/${row.document_id}/file${page ? `#page=${page}` : ""}`
            : null);
        return (
          <details key={id}>
            <summary>
              {id}
              {page ? ` · page ${page}` : ""}
            </summary>
            <p>
              {label(row?.kind)} {label(row?.metric_code)} {row?.value}
            </p>
            {row?.source_quote && <blockquote>{row.source_quote}</blockquote>}
            {url && (
              <a href={url} target="_blank" rel="noreferrer">
                Open source{page ? ` · page ${page}` : ""}
              </a>
            )}
          </details>
        );
      })}
    </div>
  );
}
export function ThesisImpact({ analysisId }: { analysisId: number }) {
  const [data, setData] = useState<Impact | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      thesisApi
        .impact(analysisId)
        .then((v) => {
          if (active) {
            setData(v);
            setError("");
          }
        })
        .catch(() => {
          if (active) setError("Thesis impact could not be loaded.");
        });
    setData(null);
    void load();
    window.addEventListener("thesis-updated", load);
    return () => {
      active = false;
      window.removeEventListener("thesis-updated", load);
    };
  }, [analysisId]);
  const changes = data?.impacts.filter(row => row.previous_status !== row.new_status &&
    ["STRENGTHENING", "ON_TRACK", "WEAKENING", "BROKEN"].includes(row.new_status)) || [];
  return (
    <section className="ai-group">
      <h4>Thesis Impact</h4>
      {error && <p role="alert">{error}</p>}
      {changes.length ? (
        changes.map((row) => (
          <article key={row.pillar_id}>
            <strong>
              {row.name}: {label(row.previous_status)} → {label(row.new_status)}
            </strong>
            <p>{row.reason}</p>
            <small>{label(row.method)}</small>
            <ThesisEvidence
              ids={row.evidence_ids}
              registry={data?.evidence || {}}
            />
          </article>
        ))
      ) : (
        <p className="muted">
          No material change to the investment thesis was established this quarter.
        </p>
      )}
    </section>
  );
}
export function ThesisPanel({
  symbol,
  scope,
}: {
  symbol: string;
  scope: string;
}) {
  const [loadedData, setData] = useState<
      (Thesis & { queryDate: string }) | null
    >(null),
    [asOf, setAsOf] = useState(""),
    [analysisId, setAnalysisId] = useState(0);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [refresh, setRefresh] = useState(0);
  const [draft, setDraft] = useState<Pillar | null>(null),
    [proposals, setProposals] = useState<Pillar[]>([]),
    [proposalEvidence, setProposalEvidence] = useState<Evidence>({});
  const [testDraft, setTestDraft] = useState<{
    pillarId: number;
    test: Expectation;
  } | null>(null);
  useEffect(() => {
    let active = true;
    setData(null);
    setDraft(null);
    setTestDraft(null);
    setProposals([]);
    thesisApi
      .get(symbol, scope, asOf)
      .then((value) => {
        if (active) {
          setData({ ...value, queryDate: asOf });
          window.dispatchEvent(new Event("thesis-updated"));
          setAnalysisId((old) =>
            value.available_analyses.some((a) => a.id === old)
              ? old
              : value.available_analyses[0]?.id || 0,
          );
        }
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [symbol, scope, asOf, refresh]);
  async function action(work: () => Promise<unknown>, reload = true) {
    setBusy(true);
    setError("");
    try {
      await work();
      if (reload) {
        setRefresh((n) => n + 1);
        window.dispatchEvent(new Event("thesis-updated"));
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const data = loadedData?.queryDate === asOf ? loadedData : null;
  const historical = !!asOf;
  const newPillar = (): Pillar => ({
    scope,
    name: "",
    category: "GROWTH",
    description: "",
    importance: "IMPORTANT",
    created_from: "USER",
    source_analysis_id: null,
    evidence_ids: [],
    metric_codes: [],
    is_active: true,
    expectations: [],
  });
  return (
    <section
      className="financial-card thesis-panel"
      aria-label="Investment Thesis"
    >
      <header className="thesis-header">
        <div>
          <span className="thesis-eyebrow">LIVING INVESTMENT CASE</span>
          <h3>Investment Thesis</h3>
          <p className="muted">The reasons this company matters to your investment case.</p>
        </div>
        <span className="thesis-header-mark" aria-hidden="true">◇</span>
      </header>
      <details className="thesis-date-control"><summary>View an earlier date</summary><label>
        History as of{" "}
        <input
          type="date"
          value={asOf}
          disabled={busy}
          onChange={(e) => setAsOf(e.target.value)}
        />
      </label>
      {historical && (
        <button
          className="button button-outline button-small"
          disabled={busy}
          onClick={() => setAsOf("")}
        >
          Return to current thesis
        </button>
      )}</details>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!data ? (
        <p>Loading thesis…</p>
      ) : (
        <>
          <section className="thesis-summary"><div className="thesis-section-heading"><span aria-hidden="true">01</span><h4>Current Investment Case{historical ? ` · ${asOf}` : ""}</h4></div>
            {data.overview.summary ? <p>{data.overview.summary}</p> : <p className="muted">A thesis appears when verified quarterly research has enough evidence.</p>}
            {data.overview.generated_from_research && <small className="thesis-verified"><i aria-hidden="true">✓</i> Generated from verified research</small>}
          </section>
          {!!data.overview.changes.length && <section className="thesis-updates"><h4>Thesis Updated · {data.overview.changes[0].period}</h4>
            {data.overview.changes.map(change => <p key={change.pillar}><strong>{change.pillar}: {label(change.previous_status)} → {label(change.new_status)}</strong><br />{change.reason}</p>)}
          </section>}
          <div className="thesis-cards">{data.overview.cards.map(card => {
            const pillar = data.pillars.find(p => p.id === card.id)!;
            const stateClass = card.status.toLowerCase().replaceAll("_", "-");
            return <article className={`thesis-card thesis-pillar-card thesis-pillar-${stateClass}`} key={card.id}>
              <div className="thesis-card-top"><div><small>{label(card.importance)} PILLAR</small><h4>{card.name}</h4></div><span className={`thesis-state thesis-state-${stateClass}`}><i aria-hidden="true" />{label(card.status)}</span></div>
              <p className="thesis-interpretation">{card.interpretation}</p>
              {card.next_test && <p className="thesis-next"><small>NEXT TEST{card.next_test.target ? ` · ${card.next_test.target}` : ""}</small><br />{card.next_test.question}</p>}
              <details className="thesis-card-details"><summary>Evidence and details <span aria-hidden="true">＋</span></summary><p>{pillar.description}</p>
                <small>{label(pillar.importance)} · {pillar.accepted_by_user ? "Edited by you" : "Generated from verified research"}</small>
                {!!pillar.source_components?.length && <ul>{pillar.source_components.map((part, index) => <li key={index}>{part.name}: {part.description}</li>)}</ul>}
                {pillar.expectations.map(test => <p key={test.expectation_key}>{test.description} · {label(test.outcome?.status || "UNPROVEN")}</p>)}
                <ThesisEvidence ids={pillar.status_evidence_ids || []} registry={pillar.status_evidence || {}} />
                <ThesisEvidence ids={pillar.evidence_ids} registry={pillar.source_evidence || {}} />
              </details>
            </article>;
          })}</div>
          {!!data.overview.cards.length && <div className="thesis-snapshot">
            <section className="thesis-snapshot-working"><div className="thesis-snapshot-title"><span aria-hidden="true">✓</span><h4>What's Working</h4></div>{data.overview.what_working.length ? <ul>{data.overview.what_working.map(v => <li key={v}>{v}</li>)}</ul> : <p>No linked requirement has been verified as delivered yet.</p>}</section>
            <section className="thesis-snapshot-proof"><div className="thesis-snapshot-title"><span aria-hidden="true">?</span><h4>What Needs Proof</h4></div>{data.overview.needs_proof.length ? <ul>{data.overview.needs_proof.map(v => <li key={v}>{v}</li>)}</ul> : <p>No unresolved test is recorded.</p>}</section>
            <section className="thesis-snapshot-worry"><div className="thesis-snapshot-title"><span aria-hidden="true">!</span><h4>What Worries Me</h4></div>{data.overview.worries.length ? <ul>{data.overview.worries.map(v => <li key={v}>{v}</li>)}</ul> : <p>No direct contradiction has been established.</p>}</section>
          </div>}
          {data.overview.next_big_test && <section className="thesis-big-test"><div className="thesis-big-test-icon" aria-hidden="true">→</div><div><small>UPCOMING CATALYST</small><h4>Next Big Test</h4>
            <strong>{data.overview.next_big_test.target}{data.overview.next_big_test.target ? " · " : ""}{data.overview.next_big_test.pillar}</strong>
            <p>{data.overview.next_big_test.question}</p>
            {!!data.overview.additional_tests.length && <details><summary>Other upcoming tests</summary><ul>{data.overview.additional_tests.map(test => <li key={`${test.pillar_id}:${test.question}`}>{test.target ? `${test.target} · ` : ""}{test.question}</li>)}</ul></details>}
          </div></section>}
          <details className="thesis-editor"><summary>Edit thesis</summary>
          <h4>Thesis controls</h4>
          {!data.pillars.some((p) => p.is_active) && <p>No active pillars yet.</p>}
          <div className="thesis-case">
            {Object.entries(data.current_investment_case).map(
              ([state, rows]) => (
                <div key={state}>
                  <strong>{label(state)}</strong>
                  <ul>
                    {rows.map((row) => (
                      <li key={row.id}>
                        {row.name} · {label(row.importance)}
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            )}
          </div>
          {!historical && (
            <div className="thesis-actions">
              <button
                className="button button-outline button-small"
                disabled={busy}
                onClick={() => setDraft(newPillar())}
              >
                Add pillar
              </button>
              <label>
                Saved review{" "}
                <select
                  disabled={busy}
                  value={analysisId}
                  onChange={(e) => setAnalysisId(Number(e.target.value))}
                >
                  <option value={0}>Select a review</option>
                  {data.available_analyses.map((a) => (
                    <option key={a.id} value={a.id}>
                      #{a.id} · {a.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button button-outline button-small"
                disabled={busy || !analysisId}
                onClick={() =>
                  action(async () => {
                    const value = await thesisApi.bootstrap(symbol, analysisId);
                    setProposals(value.proposals);
                    setProposalEvidence(value.evidence);
                  }, false)
                }
              >
                Propose initial thesis
              </button>
              <button
                className="button button-outline button-small"
                disabled={
                  busy || !analysisId || !data.pillars.some((p) => p.is_active)
                }
                onClick={() =>
                  action(() => thesisApi.evaluate(symbol, analysisId))
                }
              >
                Evaluate thesis
              </button>
            </div>
          )}
          {busy && <p role="status">Saving or evaluating thesis…</p>}
          {!!proposals.length && (
            <section>
              <h4>Proposals — review before accepting</h4>
              <p>
                These are inferred requirements, not prior beliefs. Acceptance
                creates a baseline today.
              </p>
              {proposals.map((p, i) => (
                <article className="thesis-card" key={i}>
                  <strong>
                    {p.name} · {label(p.created_from)}
                  </strong>
                  <p>{p.description}</p>
                  <ThesisEvidence
                    ids={p.evidence_ids}
                    registry={proposalEvidence}
                  />
                  <button
                    className="button button-outline button-small"
                    disabled={busy}
                    onClick={() => setDraft(p)}
                  >
                    Review / edit / accept
                  </button>
                </article>
              ))}
            </section>
          )}
          {draft && !historical && (
            <form
              className="thesis-form"
              onSubmit={(e) => {
                e.preventDefault();
                void action(() => thesisApi.save(symbol, draft));
              }}
            >
              <h4>{draft.id ? "Edit pillar" : "Review new pillar"}</h4>
              <label>
                Name
                <input
                  required
                  minLength={3}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </label>
              <label>
                Investment requirement
                <textarea
                  aria-label="Investment requirement"
                  required
                  minLength={10}
                  value={draft.description}
                  onChange={(e) =>
                    setDraft({ ...draft, description: e.target.value })
                  }
                />
              </label>
              <label>
                Category
                <select
                  aria-label="Category"
                  value={draft.category}
                  onChange={(e) =>
                    setDraft({ ...draft, category: e.target.value })
                  }
                >
                  {categories.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Importance
                <select
                  aria-label="Importance"
                  value={draft.importance}
                  onChange={(e) =>
                    setDraft({ ...draft, importance: e.target.value })
                  }
                >
                  {["CORE", "IMPORTANT", "SECONDARY"].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <p>
                Origin: {label(draft.created_from)} · Scope: {label(scope)}
              </p>
              <ThesisEvidence
                ids={draft.evidence_ids}
                registry={draft.source_evidence || proposalEvidence}
              />
              {draft.expectations.map((t, i) => (
                <p key={i}>
                  Linked test: {t.description}
                  {t.checkpoint_id && ` · checkpoint ${t.checkpoint_id}`}
                </p>
              ))}
              <label>
                <input
                  type="checkbox"
                  checked={draft.is_active}
                  onChange={(e) =>
                    setDraft({ ...draft, is_active: e.target.checked })
                  }
                />{" "}
                Active pillar
              </label>
              <button
                className="button button-outline button-small"
                disabled={busy}
                type="submit"
              >
                {draft.id ? "Save revision" : "Accept pillar"}
              </button>
              <button
                className="button button-outline button-small"
                type="button"
                disabled={busy}
                onClick={() => setDraft(null)}
              >
                Cancel
              </button>
            </form>
          )}
          {data.pillars.map((p) => (
            <article className="thesis-card" key={p.id}>
              <h4>
                {p.name} · {label(p.status)}
                {!p.is_active && " · Inactive"}
              </h4>
              <small>
                {label(p.importance)} · {label(p.created_from)} · Revision{" "}
                {p.version}
              </small>
              <p>{p.description}</p>
              <p>{p.status_reason}</p>
              <ThesisEvidence
                ids={p.status_evidence_ids || []}
                registry={p.status_evidence || {}}
              />
              {p.contradiction && (
                <strong>
                  Thesis contradiction — a linked test challenges this
                  requirement.
                </strong>
              )}
              <ThesisEvidence
                ids={p.evidence_ids}
                registry={p.source_evidence || {}}
              />
              <h5>Expectations / Thesis Tests</h5>
              {!p.expectations.length && <p>No measurable test defined yet.</p>}
              {p.expectations.map((t) => (
                <div key={t.expectation_key}>
                  <p>
                    {t.description} · {label(t.outcome?.status || "UNPROVEN")}
                    {!t.is_active && " · Inactive"}
                  </p>
                  <small>
                    {t.checkpoint_id
                      ? `Linked management checkpoint ${t.checkpoint_id}`
                      : t.expectation_type === "METRIC"
                        ? `${label(t.metric_code || "")} ${t.operator} ${t.target_value} ${t.unit} · ${t.target_period_end}`
                        : "Qualitative requirement; verified measurement still needed."}
                    {t.outcome?.actual_value != null &&
                      ` · Actual ${t.outcome.actual_value}`}
                  </small>
                  {!historical && p.is_active && (
                    <button
                      className="button button-outline button-small"
                      disabled={busy}
                      onClick={() => setTestDraft({ pillarId: p.id!, test: t })}
                    >
                      Edit test
                    </button>
                  )}
                </div>
              ))}
              {!!p.research_gaps?.length && (
                <>
                  <h5>Research gaps</h5>
                  <ul>
                    {p.research_gaps.map((g) => (
                      <li key={g}>{g}</li>
                    ))}
                  </ul>
                </>
              )}
              {!historical && (
                <div className="thesis-actions">
                  <button
                    className="button button-outline button-small"
                    disabled={busy}
                    onClick={() =>
                      setDraft({ ...p, reason: "Investor requirement revised" })
                    }
                  >
                    Edit / deactivate
                  </button>
                  {p.is_active && (
                    <button
                      className="button button-outline button-small"
                      disabled={busy}
                      onClick={() =>
                        setTestDraft({ pillarId: p.id!, test: blankTest() })
                      }
                    >
                      Add expectation
                    </button>
                  )}
                </div>
              )}
            </article>
          ))}
          {testDraft && !historical && (
            <ExpectationForm
              value={testDraft.test}
              data={data}
              busy={busy}
              onChange={(test) => setTestDraft({ ...testDraft, test })}
              onCancel={() => setTestDraft(null)}
              onSave={() =>
                action(() =>
                  thesisApi.test(symbol, testDraft.pillarId, testDraft.test),
                )
              }
            />
          )}
          <section>
            <h4>
              Valuation Expectation Gap ·{" "}
              {label(data.expectation_gap.classification)}
            </h4>
            <p>{data.expectation_gap.reason}</p>
            {data.expectation_gap.required_eps_cagr_pct != null && (
              <p>
                Scenario requires EPS CAGR{" "}
                {data.expectation_gap.required_eps_cagr_pct}% over{" "}
                {data.expectation_gap.horizon_years} years.
                {data.expectation_gap.observed_eps_cagr_pct != null &&
                  ` Historical EPS CAGR: ${data.expectation_gap.observed_eps_cagr_pct}%.`}
              </p>
            )}
            <small>
              Scenario assumptions are not forecasts. Quarterly growth is not
              compared directly with multi-year CAGR.
            </small>
          </section>
          </details>
          {!!data.history.length && <details className="thesis-history">
            <summary>History and evidence</summary>
            {data.history.map((row, i) => (
              <article className="thesis-card" key={i}>
                <strong>
                  {row.effective_date} · {row.name} · {label(row.kind)}
                </strong>
                <p>
                  {label(row.previous_status)}{" "}
                  {row.new_status && `→ ${label(row.new_status)}`}
                </p>
                <p>{row.reason}</p>
                {row.definition?.description && (
                  <p>{row.definition.description}</p>
                )}
                {row.analysis_id && (
                  <small>
                    Saved review #{row.analysis_id}
                    {row.evidence_as_of &&
                      ` · evidence through ${row.evidence_as_of}`}
                  </small>
                )}
                <ThesisEvidence
                  ids={row.evidence_ids}
                  registry={row.evidence}
                />
              </article>
            ))}
          </details>}
        </>
      )}
    </section>
  );
}
function ExpectationForm({
  value: v,
  data,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  value: Expectation;
  data: Thesis;
  busy: boolean;
  onChange: (v: Expectation) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const set = (patch: Partial<Expectation>) => onChange({ ...v, ...patch });
  return (
    <form
      className="thesis-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <h4>{v.expectation_key ? "Edit expectation" : "Add expectation"}</h4>
      <label>
        Investment test
        <textarea
          aria-label="Investment test"
          required
          minLength={8}
          value={v.description}
          onChange={(e) => set({ description: e.target.value })}
        />
      </label>
      <label>
        Test type
        <select
          aria-label="Test type"
          value={v.expectation_type}
          onChange={(e) =>
            onChange({
              ...blankTest(),
              description: v.description,
              expectation_key: v.expectation_key,
              version: v.version,
              expectation_type: e.target
                .value as Expectation["expectation_type"],
            })
          }
        >
          <option value="QUALITATIVE">Qualitative requirement</option>
          <option value="CHECKPOINT">Link management checkpoint</option>
          <option value="METRIC">Verified numerical test</option>
        </select>
      </label>
      {v.expectation_type === "CHECKPOINT" && (
        <label>
          Existing commitment
          <select
            required
            value={v.checkpoint_id || ""}
            onChange={(e) => set({ checkpoint_id: Number(e.target.value) })}
          >
            <option value="">Select checkpoint</option>
            {data.checkpoint_options.map((cp) => (
              <option key={cp.id} value={cp.id}>
                #{cp.id} {cp.description}
              </option>
            ))}
          </select>
        </label>
      )}
      {v.expectation_type === "METRIC" && (
        <>
          <label>
            Metric
            <select
              required
              value={v.metric_code || ""}
              onChange={(e) => set({ metric_code: e.target.value })}
            >
              <option value="">Select metric</option>
              {data.metric_options.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Operator
            <select
              required
              value={v.operator || ""}
              onChange={(e) => set({ operator: e.target.value })}
            >
              <option value="">Select comparison</option>
              {["GTE", "LTE", "EQ", "BETWEEN"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Target
            <input
              required
              type="number"
              step="any"
              value={v.target_value || ""}
              onChange={(e) => set({ target_value: e.target.value })}
            />
          </label>
          {v.operator === "BETWEEN" && (
            <label>
              Upper target
              <input
                required
                type="number"
                step="any"
                value={v.upper_value || ""}
                onChange={(e) => set({ upper_value: e.target.value })}
              />
            </label>
          )}
          <label>
            Unit
            <select
              required
              value={v.unit || ""}
              onChange={(e) => set({ unit: e.target.value })}
            >
              <option value="">Select unit</option>
              {data.unit_options.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Reporting period end
            <input
              required
              type="date"
              value={v.target_period_end || ""}
              onChange={(e) => set({ target_period_end: e.target.value })}
            />
          </label>
          <label>
            Reporting basis
            <select
              required
              value={v.period_type || ""}
              onChange={(e) => set({ period_type: e.target.value })}
            >
              <option value="">Select basis</option>
              <option>QUARTERLY</option>
              <option>ANNUAL</option>
            </select>
          </label>
        </>
      )}
      <label>
        <input
          type="checkbox"
          checked={v.is_active}
          onChange={(e) => set({ is_active: e.target.checked })}
        />{" "}
        Active test
      </label>
      <button className="button button-outline button-small" disabled={busy}>
        Save expectation revision
      </button>
      <button
        className="button button-outline button-small"
        type="button"
        disabled={busy}
        onClick={onCancel}
      >
        Cancel
      </button>
    </form>
  );
}
