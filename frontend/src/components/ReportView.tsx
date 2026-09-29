import { useEffect, useState } from "react";
import { api, fmtTime } from "../api";
import type { Report, Score } from "../types";
import { MODE_INFO } from "../types";
import { Logo } from "./Home";

export default function ReportView({ sessionId, onHome, onPracticeAgain }: { sessionId: string; onHome: () => void; onPracticeAgain: () => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [status, setStatus] = useState<string>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const poll = async (attempt = 0) => {
      try {
        const r = await api.report(sessionId);
        if (stop) return;
        setStatus(r.status);
        if (r.status === "ready" && r.report) setReport(r.report);
        else if (r.status === "generating" && attempt < 60) timer = window.setTimeout(() => poll(attempt + 1), 1500);
      } catch (e) {
        if (!stop) setError(e instanceof Error ? e.message : "Could not load report");
      }
    };
    poll();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [sessionId]);

  const practiceAgain = async () => {
    try {
      await api.restart(sessionId);
      onPracticeAgain();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not restart");
    }
  };

  const download = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `pitchmirror-report-${report.overview.mode}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (error)
    return (
      <div className="center-screen">
        <div className="card narrow">
          <h2>Report unavailable</h2>
          <p>{error}</p>
          <button className="btn primary" onClick={onHome}>Back to start</button>
        </div>
      </div>
    );
  if (!report)
    return (
      <div className="center-screen">
        {status === "not_ended" ? (
          <div className="card narrow">
            <h2>This presentation is still in progress</h2>
            <button className="btn primary" onClick={onPracticeAgain}>Back to presenting</button>
          </div>
        ) : (
          <><span className="spinner" /> <span className="muted">Preparing your report…</span></>
        )}
      </div>
    );

  const r = report;
  const d = r.delivery.raw;
  const n = r.narrative;

  return (
    <div className="report">
      <header className="topbar report-top no-print">
        <button className="brand as-button" onClick={onHome}><Logo /><span>PitchMirror</span></button>
        <div className="deck-name">{r.overview.filename}</div>
        <div className="topbar-right">
          <button className="btn" onClick={practiceAgain}>Practice again</button>
          <button className="btn" onClick={download}>Download JSON</button>
          <button className="btn" onClick={() => window.print()}>Print / PDF</button>
          <button className="btn primary" onClick={onHome}>New deck</button>
        </div>
      </header>

      <main className="report-main">
        <section className="r-hero">
          <div>
            <div className="eyebrow">{MODE_INFO[r.overview.mode].label} report</div>
            <h1>{r.overview.filename}</h1>
            <div className="overview-row">
              <Stat label="Duration" value={fmtTime(r.overview.duration_s)} />
              <Stat label="Slides presented" value={`${r.overview.slides_presented} / ${r.overview.slides_total}`} />
              <Stat label="Words spoken" value={String(d.total_words)} />
              <Stat label="Questions asked" value={String(r.questions.asked.length)} />
            </div>
          </div>
          {n.summary && <p className="r-summary">{n.summary}</p>}
        </section>

        <section className="r-section">
          <h2>Scores <span className="est">estimates</span></h2>
          <p className="muted small">{r.scores_note}</p>
          <div className="score-grid">{r.scores.map((s) => <ScoreCard key={s.name} s={s} />)}</div>
        </section>

        <section className="r-section">
          <h2>Top improvements</h2>
          {n.top_recommendations.length === 0 ? <p className="muted">Nothing specific stood out. Present a longer run to get more detailed feedback.</p> : (
            <ol className="recs">
              {n.top_recommendations.map((x, i) => (
                <li key={i}><strong>{x.title}</strong><p>{x.detail}</p>{x.evidence && <p className="muted small">Evidence: {x.evidence}</p>}</li>
              ))}
            </ol>
          )}
          <p className="muted small">Written by {n.source === "ai" ? "the AI model from the collected evidence" : "the rule engine"}.</p>
        </section>

        <section className="r-section two-col">
          <div>
            <h2>Delivery · raw measurements</h2>
            <table className="kv">
              <tbody>
                <tr><td>Speaking pace</td><td>{d.wpm !== null ? `${d.wpm} words/min` : "Not enough timed speech"}</td></tr>
                <tr><td>Speaking time</td><td>{fmtTime(d.speaking_time_s)} <span className="muted small">({d.speaking_time_source})</span></td></tr>
                <tr><td>Filler words</td><td>{d.filler_total}{d.fillers_per_100_words !== null ? ` (${d.fillers_per_100_words} per 100 words)` : ""}{Object.keys(d.fillers).length > 0 && <div className="muted small">{Object.entries(d.fillers).map(([k, v]) => `${k} ×${v}`).join(", ")}</div>}</td></tr>
                <tr><td>Pauses ≥ 1.5s</td><td>{d.pauses_measured ? `${d.pause_count} (${d.long_pause_count} ≥ 4s, longest ${d.longest_pause_s}s)` : "Not measured"}</td></tr>
                <tr><td>Repeated phrases</td><td>{d.repetition.repeated_phrases.length ? d.repetition.repeated_phrases.map((p) => `“${p.phrase}” ×${p.count}`).join(", ") : "None detected"}</td></tr>
                <tr><td>Rushed sections</td><td>{d.rushed_segments.length ? d.rushed_segments.map((x) => `slide ${x.slide_number} (${x.wpm} wpm)`).join(", ") : "None detected"}</td></tr>
              </tbody>
            </table>
            <ul className="caveats">{r.delivery.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
          </div>
          <div>
            <h2>Delivery · interpretation</h2>
            {r.delivery.interpretation.length === 0 ? <p className="muted">Unable to determine from available audio/slide context.</p> : (
              <ul className="interp">
                {r.delivery.interpretation.map((x, i) => (
                  <li key={i} className={x.kind}><span>{x.text}</span>{x.suggestion && <em>{x.suggestion}</em>}</li>
                ))}
              </ul>
            )}
            <h3>Per slide</h3>
            <table className="grid-table">
              <thead><tr><th>Slide</th><th>Time</th><th>Words</th><th>WPM</th><th>Fillers</th></tr></thead>
              <tbody>
                {Object.entries(d.per_slide).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td>{fmtTime(v.time_on_slide_s)}</td><td>{v.words}</td><td>{v.wpm ?? "–"}</td><td>{v.fillers}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="r-section">
          <h2>Content consistency</h2>
          {r.consistency.mismatches.length > 0 && (
            <div className="mismatch-list">
              <h3>Detected mismatches</h3>
              {r.consistency.mismatches.map((f) => (
                <div key={f.id} className="mismatch">
                  <strong>Slide {f.slide_number}:</strong> {f.observation}
                  <div className="ev-pair"><span>Slide: “{f.evidence_slide}”</span><span>You said: “{f.evidence_speech}”</span></div>
                </div>
              ))}
            </div>
          )}
          {r.consistency.other_issues.length > 0 && (
            <>
              <h3>Unsupported claims, off-topic or read-aloud</h3>
              <ul>{r.consistency.other_issues.map((f) => <li key={f.id}>Slide {f.slide_number}: {f.observation}</li>)}</ul>
            </>
          )}
          <table className="grid-table wide">
            <thead><tr><th>Slide</th><th>Explained</th><th>Not explained</th><th>Read aloud</th></tr></thead>
            <tbody>
              {r.consistency.per_slide.map((p) => (
                <tr key={p.slide_number} className={p.presented ? "" : "dim"}>
                  <td><strong>{p.slide_number}.</strong> {p.title}</td>
                  {p.presented ? (
                    <>
                      <td>{p.covered.join("; ") || "–"}</td>
                      <td>{p.missed.join("; ") || "–"}</td>
                      <td>{p.reading_ratio !== null ? `${Math.round(p.reading_ratio * 100)}%` : "–"}</td>
                    </>
                  ) : <td colSpan={3} className="muted">{p.note}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {r.consistency.rejected_ungrounded_items > 0 && (
            <p className="muted small">{r.consistency.rejected_ungrounded_items} AI observation(s) were discarded because their quotes could not be found in your slides or transcript.</p>
          )}
        </section>

        <section className="r-section">
          <h2>Visual explanation</h2>
          {r.visuals.items.length === 0 ? <p className="muted">No charts, diagrams or tables on the slides you presented.</p> : (
            <table className="grid-table wide">
              <thead><tr><th>Slide</th><th>Visual</th><th>Explained?</th><th>Evidence</th></tr></thead>
              <tbody>
                {r.visuals.items.map((v, i) => (
                  <tr key={i}><td>{v.slide_number}</td><td>{v.element}</td><td><span className={`vx vx-${v.explained}`}>{v.explained === "unknown" ? "unable to determine" : v.explained}</span></td><td className="muted small">{v.evidence || "–"}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="r-section">
          <h2>Audience questions</h2>
          {r.questions.asked.length === 0 ? <p className="muted">No questions were asked during this run.</p> : (
            <div className="qa-list">
              {r.questions.asked.map((q) => (
                <div key={q.id} className={`qa ${q.depth ? "follow" : ""}`}>
                  <div className="qa-q"><span className="tag">{q.depth ? `follow-up ${q.depth}` : `slide ${q.slide_number}`}</span> {q.question}</div>
                  {q.status === "skipped" ? <div className="muted small">Skipped</div> : q.answer_text ? <div className="qa-a">“{q.answer_text}”</div> : <div className="muted small">No answer captured</div>}
                  {q.evaluation && (
                    <div className="qa-eval">
                      {q.evaluation.score !== null && <span className={`score-chip s${q.evaluation.score}`}>{q.evaluation.score}/5{q.evaluation.source === "rule" ? " est." : ""}</span>}
                      {q.evaluation.summary} {q.evaluation.gaps.length > 0 && <span className="muted">Gaps: {q.evaluation.gaps.join("; ")}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          <h3>Practise these next</h3>
          <ul className="practice">
            {n.practice_questions.map((p, i) => <li key={i}>{p.question} {p.slide_number ? <span className="muted small">(slide {p.slide_number})</span> : null}</li>)}
          </ul>
        </section>

        <footer className="r-foot muted small">
          Analysis: {r.provider.mode === "ai" ? `${r.provider.name} ${r.provider.model}` : "rule engine (no AI model configured)"} · Transcripts and feedback are held in server memory only and are deleted when the session expires.
        </footer>
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="stat"><div className="stat-v">{value}</div><div className="stat-l">{label}</div></div>;
}

function ScoreCard({ s }: { s: Score }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="score-card">
      <div className="score-name">{s.name}</div>
      <div className="score-val">{s.value === null ? <span className="muted small">n/a</span> : <>{s.value}<span className="of">/100</span></>}</div>
      {s.value !== null && <div className="bar"><div style={{ width: `${s.value}%` }} /></div>}
      <button className="fb-more" onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "How is this calculated?"}</button>
      {open && <ul className="deriv">{s.derivation.map((x, i) => <li key={i}>{x}</li>)}</ul>}
      {!open && s.value === null && <div className="muted small">{s.derivation[0]}</div>}
    </div>
  );
}
