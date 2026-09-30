import { useState } from "react";
import type { AnswerAssessment, AnswerAttempt, AttemptComparison } from "../types";

export const ASSESSMENT_LABEL: Record<AnswerAssessment, { label: string; tone: "ok" | "warn" | "bad" | "muted" }> = {
  strongly_correct: { label: "Strong answer", tone: "ok" },
  mostly_correct: { label: "Mostly correct", tone: "ok" },
  partially_correct: { label: "Partially complete", tone: "warn" },
  incorrect: { label: "Needs improvement", tone: "bad" },
  insufficient_evidence: { label: "Not enough to assess", tone: "muted" },
  unable_to_determine: { label: "Correctness not verifiable", tone: "muted" },
};

const SCORE_LABEL: Record<string, string> = {
  correctness: "Correctness", relevance: "Relevance", completeness: "Completeness", technical_depth: "Depth", clarity: "Clarity",
};

/** Answer Intelligence for the latest attempt of one question. Purely presentational. */
export default function AnswerIntelPanel({ attempts, comparison, pending, compact }: {
  attempts: AnswerAttempt[]; comparison?: AttemptComparison | null; pending: boolean; compact?: boolean;
}) {
  const [showBetter, setShowBetter] = useState(!compact);
  const latest = attempts[attempts.length - 1];
  if (!latest && pending) return <div className="muted small ai-pending"><span className="spinner" /> Analyzing your answer…</div>;
  if (!latest?.intel) return null;
  const intel = latest.intel;
  const a = ASSESSMENT_LABEL[intel.assessment];
  const scores = Object.entries(intel.scores).filter(([, v]) => v !== null) as [string, number][];

  return (
    <div className="ai-panel">
      <div className="ai-head">
        <span className={`ai-badge tone-${a.tone}`}><span className="ai-dot" />{a.label}</span>
        {attempts.length > 1 && <span className="muted small">Attempt {latest.attempt}</span>}
        <span className="tag">{intel.source === "ai" ? "AI analysis" : "rule based"}</span>
      </div>
      {pending && <div className="muted small"><span className="spinner" /> Analyzing your new attempt…</div>}

      {comparison && latest.attempt > 1 && (
        comparison.comparable ? (
          <div className={`ai-compare ${(comparison.delta ?? 0) > 0 ? "up" : (comparison.delta ?? 0) < 0 ? "down" : ""}`}>
            {comparison.metric}: attempt 1 <strong>{comparison.before}</strong> → attempt {latest.attempt} <strong>{comparison.after}</strong>
            {" "}({(comparison.delta ?? 0) > 0 ? "+" : ""}{comparison.delta})
            {(comparison.delta ?? 0) > 0 && comparison.reliable && <span className="ai-improved"> Improvement detected</span>}
            {!comparison.reliable && comparison.reason && <div className="muted small">{comparison.reason}</div>}
          </div>
        ) : <div className="muted small">{comparison.reason}</div>
      )}

      {scores.length > 0 && (
        <div className="ai-scores">
          {scores.map(([k, v]) => <span key={k} className="ai-score"><span>{SCORE_LABEL[k]}</span><strong>{v}</strong></span>)}
        </div>
      )}

      {intel.what_was_good.length > 0 && (
        <div className="ai-block good"><div className="ai-label">What you did well</div><ul>{intel.what_was_good.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      )}
      {intel.what_was_missing.length > 0 && (
        <div className="ai-block missing"><div className="ai-label">What was missing</div><ul>{intel.what_was_missing.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      )}
      {intel.could_be_stronger.length > 0 && (
        <div className="ai-block"><div className="ai-label">Could be even stronger</div><ul>{intel.could_be_stronger.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      )}

      {intel.better_answer && (
        <div className="ai-better">
          <button className="fb-more" onClick={() => setShowBetter((v) => !v)} aria-expanded={showBetter}>
            {showBetter ? "Hide stronger answer" : "Show a stronger answer"}
          </button>
          {showBetter && (
            <>
              <p className="ai-better-text">“{intel.better_answer}”</p>
              {intel.why_better.length > 0 && (
                <div className="ai-block"><div className="ai-label">Why this is stronger</div><ul>{intel.why_better.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
              )}
              <div className="muted small">One possible wording built from your own answer and slides, not the only right answer.</div>
            </>
          )}
        </div>
      )}

      {intel.retry_recommended && intel.scaffold_question && (
        <div className="ai-hint"><span className="ai-label">Hint for your next try</span> {intel.scaffold_question}</div>
      )}
      {intel.note && <div className="muted small">{intel.note}</div>}
    </div>
  );
}
