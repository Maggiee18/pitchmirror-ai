import { useMemo, useState } from "react";
import type { FeedbackItem, Question } from "../types";

const KIND_LABEL: Record<string, string> = { positive: "Strength", improvement: "Improve", warning: "Mismatch" };
const CAT_LABEL: Record<string, string> = {
  slide_speech_consistency: "Slide vs speech",
  missed_concept: "Missed point",
  visual_gap: "Visual not explained",
  reading_slide: "Reading the slide",
  off_topic: "Off topic",
  unsupported_claim: "Unsupported claim",
  delivery: "Delivery",
  strength: "Strength",
};

export default function FeedbackPanel(props: {
  feedback: FeedbackItem[];
  questions: Question[];
  currentSlide: number;
  questionPending: boolean;
  evaluating: string[];
  answeringId: string | null;
  live: boolean;
  onAnswer: (id: string) => void;
  onDone: (id: string, typed: string) => void;
  onSkip: (id: string) => void;
}) {
  const { feedback, questions, currentSlide, questionPending, evaluating, answeringId, live, onAnswer, onDone, onSkip } = props;
  const [filter, setFilter] = useState<"all" | "slide">("all");

  const open = questions.filter((q) => q.status === "open");
  const recentAnswered = questions.filter((q) => q.status === "answered").slice(-1);

  const items = useMemo(() => {
    const list = filter === "slide" ? feedback.filter((f) => f.slide_number === currentSlide) : feedback;
    return list.slice().sort((a, b) => b.created_at - a.created_at);
  }, [feedback, filter, currentSlide]);

  return (
    <section className="panel feedback-panel" aria-label="Live feedback">
      <div className="panel-head">
        <span className="panel-title">Audience &amp; feedback</span>
        <div className="seg-toggle" role="tablist">
          <button role="tab" aria-selected={filter === "all"} className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>All</button>
          <button role="tab" aria-selected={filter === "slide"} className={filter === "slide" ? "on" : ""} onClick={() => setFilter("slide")}>This slide</button>
        </div>
      </div>

      <div className="feedback-body">
        {questionPending && (
          <div className="card-q pending"><span className="spinner" /> Your audience is thinking of a question…</div>
        )}
        {open.map((q) => (
          <QuestionCard key={q.id} q={q} answering={answeringId === q.id} live={live}
            evaluating={evaluating.includes(q.id)} onAnswer={onAnswer} onDone={onDone} onSkip={onSkip} />
        ))}
        {recentAnswered.map((q) => (
          <AnsweredCard key={q.id} q={q} />
        ))}
        {evaluating.filter((id) => !open.some((q) => q.id === id)).length > 0 && (
          <div className="card-q pending"><span className="spinner" /> Evaluating your answer…</div>
        )}

        {items.length === 0 && open.length === 0 && !questionPending && (
          <div className="empty">
            {live ? "Feedback appears as you explain each slide. Moving to the next slide triggers a full check of the one you just finished." : "Feedback will appear here during your presentation."}
          </div>
        )}
        {items.map((f) => <FeedbackCard key={f.id} f={f} />)}
      </div>
    </section>
  );
}

function FeedbackCard({ f }: { f: FeedbackItem }) {
  const [open, setOpen] = useState(f.kind === "warning");
  const hasEvidence = !!(f.evidence_slide || f.evidence_speech || f.explanation);
  return (
    <article className={`fb fb-${f.kind} sev-${f.severity}`}>
      <div className="fb-head">
        <span className="fb-kind">{KIND_LABEL[f.kind]}</span>
        <span className="fb-cat">{CAT_LABEL[f.category] ?? f.category}</span>
        <span className="fb-slide">Slide {f.slide_number}</span>
      </div>
      <p className="fb-obs">{f.observation}</p>
      {f.suggestion && <p className="fb-sug">→ {f.suggestion}</p>}
      {hasEvidence && (
        <button className="fb-more" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Hide evidence" : "Show evidence"}
        </button>
      )}
      {open && hasEvidence && (
        <div className="fb-evidence">
          {f.evidence_slide && <div><span className="ev-label">Slide</span>“{f.evidence_slide}”</div>}
          {f.evidence_speech && <div><span className="ev-label">You said</span>“{f.evidence_speech}”</div>}
          {f.explanation && <div className="muted">{f.explanation}</div>}
          <div className="ev-source">{f.source === "ai" ? "AI analysis, quotes verified against slide and transcript" : "Rule based measurement"}</div>
        </div>
      )}
    </article>
  );
}

function QuestionCard({ q, answering, live, evaluating, onAnswer, onDone, onSkip }: {
  q: Question; answering: boolean; live: boolean; evaluating: boolean;
  onAnswer: (id: string) => void; onDone: (id: string, typed: string) => void; onSkip: (id: string) => void;
}) {
  const [typed, setTyped] = useState("");
  return (
    <article className={`card-q ${answering ? "answering" : ""}`}>
      <div className="fb-head">
        <span className="fb-kind q">{q.depth > 0 ? `Follow-up ${q.depth}` : "Question"}</span>
        <span className="fb-cat">{q.difficulty}</span>
        <span className="fb-slide">Slide {q.slide_number}</span>
      </div>
      <p className="q-text">{q.question}</p>
      {q.reason && <p className="q-reason">Why: {q.reason}</p>}
      {live && !evaluating && (
        answering ? (
          <div className="q-answer">
            <div className="rec-hint"><span className="dot rec" /> Speak your answer now. It is recorded as an answer, not as slide content.</div>
            <textarea value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Optional: type part of your answer" rows={2} maxLength={1500} />
            <div className="row">
              <button className="btn primary small" onClick={() => onDone(q.id, typed.trim())}>Done answering</button>
              <button className="btn small ghost" onClick={() => onSkip(q.id)}>Skip</button>
            </div>
          </div>
        ) : (
          <div className="row">
            <button className="btn primary small" onClick={() => onAnswer(q.id)}>Answer</button>
            <button className="btn small ghost" onClick={() => onSkip(q.id)}>Skip</button>
          </div>
        )
      )}
      {evaluating && <div className="muted small"><span className="spinner" /> Evaluating…</div>}
    </article>
  );
}

function AnsweredCard({ q }: { q: Question }) {
  const ev = q.evaluation;
  if (!ev) return null;
  return (
    <article className="card-q answered">
      <div className="fb-head">
        <span className="fb-kind q">Answer feedback</span>
        {ev.score !== null && <span className={`score-chip s${ev.score}`}>{ev.score}/5{ev.source === "rule" ? " est." : ""}</span>}
      </div>
      <p className="q-reason">“{q.question}”</p>
      {ev.summary && <p className="fb-obs">{ev.summary}</p>}
      {ev.gaps.length > 0 && <ul className="gaps">{ev.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>}
    </article>
  );
}
