import { useEffect, useRef, useState } from "react";
import type { LiveMetrics, Question, Segment } from "../types";

export default function TranscriptPanel(props: {
  segments: Segment[];
  interim: string;
  currentSlide: number;
  metrics: LiveMetrics;
  live: boolean;
  engine: "browser" | "whisper" | "typed";
  answering: Question | null;
  speaking: boolean;
  whisperBusy: boolean;
  onTyped: (text: string) => void;
}) {
  const { segments, interim, currentSlide, metrics, live, engine, answering, speaking, whisperBusy, onTyped } = props;
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);

  useEffect(() => {
    if (stick) endRef.current?.scrollIntoView({ block: "end" });
  }, [segments.length, interim, stick]);

  const groups: { slide: number; items: Segment[] }[] = [];
  for (const s of segments) {
    const last = groups[groups.length - 1];
    if (last && last.slide === s.slide_number) last.items.push(s);
    else groups.push({ slide: s.slide_number, items: [s] });
  }

  const submit = () => {
    const t = draft.trim();
    if (!t) return;
    onTyped(t);
    setDraft("");
  };

  return (
    <section className="panel transcript-panel" aria-label="Transcript">
      <div className="panel-head">
        <span className="panel-title">Live transcript</span>
        {live && engine !== "typed" && (
          <span className={`listen-dot ${speaking ? "active" : ""}`}>{whisperBusy ? "transcribing…" : speaking ? "hearing you" : "listening"}</span>
        )}
      </div>

      <div className="metrics-strip" title="Raw measurements (not AI estimates)">
        <Metric label="Pace" value={metrics.wpm !== null ? `${Math.round(metrics.wpm)}` : "–"} unit="wpm" warn={metrics.wpm !== null && (metrics.wpm > 170 || metrics.wpm < 110)} />
        <Metric label="Words" value={String(metrics.total_words)} />
        <Metric label="Fillers" value={String(metrics.filler_total)} warn={metrics.total_words > 30 && metrics.filler_total / metrics.total_words > 0.04} />
        <Metric label="Pauses" value={String(metrics.pause_count)} sub={metrics.long_pause_count ? `${metrics.long_pause_count} long` : undefined} />
        {metrics.presence?.camera && (
          <Metric label="Eye contact" value={metrics.presence.eye_contact_pct !== null ? `${Math.round(metrics.presence.eye_contact_pct)}` : "–"} unit="%"
            warn={metrics.presence.eye_contact_pct !== null && metrics.presence.eye_contact_pct < 40} />
        )}
      </div>

      <div
        className="transcript-body"
        ref={boxRef}
        onScroll={() => {
          const b = boxRef.current;
          if (b) setStick(b.scrollHeight - b.scrollTop - b.clientHeight < 60);
        }}
      >
        {segments.length === 0 && !interim && (
          <div className="empty">
            {live
              ? engine === "typed"
                ? "Type what you would say for this slide below."
                : "Start speaking. Your words will appear here."
              : "Your transcript will appear here once you start."}
          </div>
        )}
        {groups.map((g, i) => (
          <div key={i} className={`t-group ${g.slide === currentSlide ? "current" : ""}`}>
            <div className="t-slide">Slide {g.slide}</div>
            {g.items.map((s) => (
              <p key={s.seq} className={`t-seg ${s.kind === "answer" ? "answer" : ""} ${s.source === "typed" ? "typed" : ""}`}>
                {s.kind === "answer" && <span className="t-badge">answer</span>}
                {s.source === "typed" && <span className="t-badge">typed</span>}
                {s.text}
              </p>
            ))}
          </div>
        ))}
        {interim && <p className="t-seg interim">{interim}</p>}
        <div ref={endRef} />
      </div>

      {answering && (
        <div className="answer-banner">Answering: "{answering.question}"</div>
      )}
      {live && (
        <form
          className="typed-row"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={engine === "typed" ? "Type your explanation for this slide and press Enter" : "Or type a correction / sentence here"}
            maxLength={1500}
            aria-label="Type transcript text"
          />
          <button className="btn small" type="submit" disabled={!draft.trim()}>Send</button>
        </form>
      )}
    </section>
  );
}

function Metric({ label, value, unit, sub, warn }: { label: string; value: string; unit?: string; sub?: string; warn?: boolean }) {
  return (
    <div className={`metric ${warn ? "warn" : ""}`}>
      <div className="m-value">{value}{unit && <span className="m-unit"> {unit}</span>}</div>
      <div className="m-label">{label}{sub ? ` · ${sub}` : ""}</div>
    </div>
  );
}
