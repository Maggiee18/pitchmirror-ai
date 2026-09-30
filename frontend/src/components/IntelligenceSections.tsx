import { useState } from "react";
import type { Intelligence } from "../types";
import { ASSESSMENT_LABEL } from "./AnswerIntelPanel";

const AUDIENCE: Record<string, { label: string; tone: string }> = {
  appropriate: { label: "Appropriate for this audience", tone: "ok" },
  consider_simplifying: { label: "Consider simplifying some terms", tone: "warn" },
  likely_difficult: { label: "Several concepts may be hard to follow", tone: "bad" },
};

export function RehearsalComparison({ intel, onOpenReport }: { intel: Intelligence; onOpenReport?: (id: string) => void }) {
  const r = intel.rehearsal;
  if (!r) return null;
  return (
    <section className="r-section">
      <h2>Rehearsal: slide {r.slide_number} · {r.slide_title}</h2>
      {r.changes.length === 0 ? (
        <p className="muted">Not enough comparable measurements between the two attempts. Speak for a little longer on the slide to compare.</p>
      ) : (
        <table className="grid-table wide">
          <thead><tr><th>Measure</th><th>Before</th><th>After</th><th></th></tr></thead>
          <tbody>
            {r.changes.map((c) => (
              <tr key={c.metric}>
                <td>{c.label}</td><td>{c.before}</td><td><strong>{c.after}</strong></td>
                <td>{c.improved === null ? "" : <span className={`vx ${c.improved ? "vx-yes" : "vx-no"}`}>{c.improved ? "better" : "worse"}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">Only measures the system could calculate for both attempts are shown.</p>
      {onOpenReport && <button className="btn small" onClick={() => onOpenReport(r.parent_id)}>Back to the full report</button>}
    </section>
  );
}

export default function IntelligenceSections({ intel, onRehearse, rehearsing }: {
  intel: Intelligence; onRehearse?: (slide: number) => void; rehearsing?: boolean;
}) {
  if (intel.error) return <section className="r-section"><p className="muted">{intel.error}</p></section>;
  const tc = intel.technical_communication;
  const qa = intel.qa;
  const needing = tc.key_terms.filter((t) => t.explained === "no");
  const aud = tc.audience.level ? AUDIENCE[tc.audience.level] : null;
  const w = intel.weakest_slide;

  return (
    <>
      {qa.items.length > 0 && (
        <section className="r-section">
          <h2>Q&amp;A intelligence</h2>
          <div className="qa-list">
            {qa.items.map((q) => <QAItem key={q.question_id} q={q} strong={qa.strongest.includes(q.question_id)} weak={qa.weakest.includes(q.question_id)} />)}
          </div>
        </section>
      )}

      <section className="r-section two-col">
        <div>
          <h2>Technical communication</h2>
          <table className="kv">
            <tbody>
              <tr><td>Technical language density</td><td>{tc.density_pct !== null ? `${tc.density_pct}%` : "Not enough speech to measure"}
                <div className="muted small">{tc.technical_occurrences} technical term uses / {tc.meaningful_words} meaningful words</div></td></tr>
              <tr><td>Technical terms detected</td><td>{tc.terms_detected}</td></tr>
              <tr><td>Key terms explained</td><td>{tc.terms_explained}</td></tr>
              <tr><td>Key terms needing explanation</td><td>{tc.terms_needing_explanation}</td></tr>
            </tbody>
          </table>
          <p className="muted small">{tc.note} Formula: {tc.formula}.</p>
        </div>
        <div>
          <h2>Audience fit · {tc.audience.mode}</h2>
          {aud ? <span className={`ai-badge tone-${aud.tone}`}><span className="ai-dot" />{aud.label}</span> : <span className="muted">Unable to determine.</span>}
          <p className="small" style={{ marginTop: 8 }}>{tc.audience.rationale}</p>
          <p className="muted small">{tc.audience.source === "ai" ? "AI judgement using your mode and measurements." : "Rule of thumb (AI judgement unavailable)."}</p>
          {needing.length > 0 && (
            <>
              <h3>Terms to explain or simplify</h3>
              <ul className="term-list">
                {needing.slice(0, 8).map((t) => (
                  <li key={t.term}>
                    <strong>{t.term}</strong> <span className="muted small">×{t.count}</span>
                    {t.simplification && <div className="small">Audience friendly: “{t.simplification}”</div>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </section>

      {(intel.claims.items.length > 0 || intel.consistency_memory.length > 0) && (
        <section className="r-section">
          <h2>Claims &amp; consistency</h2>
          {intel.consistency_memory.length > 0 && (
            <div className="mismatch-list">
              <h3>Potential cross-slide inconsistencies</h3>
              {intel.consistency_memory.map((c, i) => (
                <div key={i} className="mismatch">
                  {c.message}
                  <div className="ev-pair"><span>Slide {c.earlier.slide_number}: “{c.earlier.sentence}”</span><span>Slide {c.later.slide_number}: “{c.later.sentence}”</span></div>
                </div>
              ))}
            </div>
          )}
          {intel.claims.items.length > 0 && (
            <>
              <h3>Important claims ({intel.claims.needing_evidence} may need evidence)</h3>
              <table className="grid-table wide">
                <thead><tr><th>Slide</th><th>Claim</th><th>Type</th><th>Evidence</th><th>Question to prepare for</th></tr></thead>
                <tbody>
                  {intel.claims.items.map((c, i) => (
                    <tr key={i}>
                      <td>{c.slide_number}</td><td>“{c.claim}”</td><td>{c.type}</td>
                      <td>{c.needs_evidence ? <span className="vx vx-no">needs evidence</span> : <span className="muted small">{c.evidence}</span>}</td>
                      <td className="small">{c.challenge || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}

      {!intel.rehearsal && w && (
        <section className="r-section">
          <h2>Rehearsal</h2>
          {w.slide_number === null ? <p className="muted">{w.message}</p> : (
            <>
              <p><strong>Weakest slide: {w.slide_number}. {w.title}</strong> <span className="muted small">({w.points} weakness points)</span></p>
              <ul>{(w.reasons ?? []).map((r, i) => <li key={i}>{r.reason}: {r.detail} <span className="muted small">(+{r.points})</span></li>)}</ul>
              {onRehearse && (
                <button className="btn primary" onClick={() => onRehearse(w.slide_number!)} disabled={rehearsing}>
                  {rehearsing ? "Preparing…" : `Rehearse slide ${w.slide_number}`}
                </button>
              )}
              <details className="small muted" style={{ marginTop: 8 }}>
                <summary>How is this chosen?</summary>
                Each issue adds points: {Object.entries(w.weights).map(([k, v]) => `${k} +${v}`).join(", ")}. The slide with the most points is shown.
                {w.ranking.length > 1 && <> Next: {w.ranking.slice(1).map((r) => `slide ${r.slide_number} (${r.points})`).join(", ")}.</>}
              </details>
            </>
          )}
        </section>
      )}
    </>
  );
}

function QAItem({ q, strong, weak }: { q: Intelligence["qa"]["items"][number]; strong: boolean; weak: boolean }) {
  const [open, setOpen] = useState(weak);
  const last = q.attempts[q.attempts.length - 1];
  const a = last.assessment ? ASSESSMENT_LABEL[last.assessment] : null;
  return (
    <div className={`qa ${q.depth ? "follow" : ""}`}>
      <div className="qa-q">
        <span className="tag">slide {q.slide_number}</span> {q.question}
        {strong && <span className="tag tag-ok"> strongest</span>}{weak && <span className="tag tag-warn"> needs work</span>}
      </div>
      <div className="qa-eval">
        {a && <span className={`ai-badge tone-${a.tone}`}><span className="ai-dot" />{a.label}</span>}
        {last.overall !== null && <span className="muted small">quality {last.overall}/100</span>}
        {q.retry && (q.retry.comparable
          ? <span className="small">Attempt 1 {q.retry.before} → attempt {q.retry.attempts} {q.retry.after} ({(q.retry.delta ?? 0) > 0 ? "+" : ""}{q.retry.delta})</span>
          : <span className="muted small">{q.retry.reason}</span>)}
        <button className="fb-more" onClick={() => setOpen((o) => !o)}>{open ? "Hide details" : "Details"}</button>
      </div>
      {open && (
        <div className="qa-detail">
          {q.attempts.map((t) => (
            <div key={t.attempt} className="small">
              <div className="qa-a">Attempt {t.attempt}: “{t.answer || "no answer captured"}”</div>
              {t.what_was_good.length > 0 && <div><span className="ai-label">What you did well</span> {t.what_was_good.join("; ")}</div>}
              {t.what_was_missing.length > 0 && <div><span className="ai-label">What was missing</span> {t.what_was_missing.join("; ")}</div>}
            </div>
          ))}
          {last.better_answer && (
            <div className="small"><span className="ai-label">A stronger answer</span><p className="ai-better-text">“{last.better_answer}”</p>
              {last.why_better.length > 0 && <div><span className="ai-label">Why it is stronger</span> {last.why_better.join("; ")}</div>}</div>
          )}
          {last.note && <div className="muted small">{last.note}</div>}
        </div>
      )}
    </div>
  );
}
