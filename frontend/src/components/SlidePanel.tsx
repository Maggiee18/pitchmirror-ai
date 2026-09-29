import { useState } from "react";
import { api } from "../api";
import type { SlideAnalysis, SlideContext } from "../types";

const VIS_LABEL: Record<string, string> = { chart: "Chart", table: "Table", diagram: "Diagram", image: "Image" };

export default function SlidePanel({ sessionId, slide, total, analysis }: {
  sessionId: string; slide: SlideContext; total: number; analysis?: SlideAnalysis;
}) {
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const covered = new Set((analysis?.covered_concepts ?? []).map((c) => c.toLowerCase()));
  const concepts = slide.key_concepts.slice(0, 6);
  const extraCovered = (analysis?.covered_concepts ?? []).filter((c) => !concepts.some((k) => k.toLowerCase() === c.toLowerCase()));

  return (
    <section className="panel slide-panel" aria-label="Current slide">
      <div className="panel-head">
        <span className="panel-title">Slide {slide.slide_number} of {total}</span>
        {slide.enriched && <span className="tag" title="A vision model has described this slide">vision</span>}
      </div>
      <div className="slide-frame">
        {failed[slide.slide_number] ? (
          <div className="slide-fallback">
            <h3>{slide.title || `Slide ${slide.slide_number}`}</h3>
            <ul>{slide.text.slice(0, 8).map((t, i) => <li key={i}>{t}</li>)}</ul>
          </div>
        ) : (
          <img
            key={slide.slide_number}
            src={api.slideUrl(sessionId, slide.slide_number)}
            alt={slide.title || `Slide ${slide.slide_number}`}
            onError={() => setFailed((f) => ({ ...f, [slide.slide_number]: true }))}
          />
        )}
      </div>

      <div className="slide-meta">
        {slide.is_empty && <div className="info-box small">No text or visuals were detected on this slide. Feedback will rely on what you say.</div>}
        {slide.visual_elements.length > 0 && (
          <div className="meta-block">
            <div className="meta-label">Visuals to explain</div>
            <ul className="visual-list">
              {slide.visual_elements.map((v, i) => {
                const st = analysis?.visuals.find((x) => x.element.toLowerCase().includes(v.kind)) ?? analysis?.visuals[i];
                const status = st?.explained ?? "unknown";
                return (
                  <li key={i} className={`vis vis-${status}`} title={v.description}>
                    <span className="vis-kind">{VIS_LABEL[v.kind]}</span>
                    <span className="vis-desc">{v.description}</span>
                    <span className="vis-status">{status === "unknown" ? "not assessed yet" : status === "yes" ? "explained" : status === "partial" ? "partly" : "not explained"}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {concepts.length > 0 && (
          <div className="meta-block">
            <div className="meta-label">Key points {analysis ? <span className="muted">({analysis.source === "ai" ? "AI" : "keyword"} check)</span> : null}</div>
            <ul className="concepts">
              {concepts.map((c) => {
                const done = covered.has(c.toLowerCase()) || [...covered].some((x) => x.includes(c.toLowerCase()) || c.toLowerCase().includes(x));
                return <li key={c} className={done ? "done" : ""}><span className="check">{done ? "✓" : "○"}</span>{c}</li>;
              })}
              {extraCovered.slice(0, 2).map((c) => <li key={"x" + c} className="done"><span className="check">✓</span>{c}</li>)}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
