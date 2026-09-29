"""Report Generator: deterministic sections + transparent estimated scores + optional LLM narrative."""
from __future__ import annotations

import logging
import time

from ..analysis.delivery import LONG_PAUSE_S, PACE_FAST, PACE_SLOW, compute_delivery, interpret_delivery
from ..analysis.text_utils import normalize
from ..models import SlideContext
from . import prompts
from .audience import rule_question
from .llm import LLMError, get_llm

log = logging.getLogger("pitchmirror.agents.report")

UNDETERMINED = "Unable to determine from available audio/slide context."


def _score_delivery(raw: dict) -> dict:
    if raw["total_words"] < 30:
        return {"name": "Delivery", "value": None, "derivation": ["Fewer than 30 words captured. " + UNDETERMINED]}
    score, lines = 100.0, ["Start at 100."]
    wpm = raw.get("wpm")
    if wpm is None:
        lines.append("Pace not measured (not enough timed speech); no pace adjustment.")
    elif wpm > PACE_FAST or wpm < PACE_SLOW:
        dev = wpm - PACE_FAST if wpm > PACE_FAST else PACE_SLOW - wpm
        pen = min(30, dev * 0.5)
        score -= pen
        lines.append(f"Pace {wpm} wpm is {dev:.0f} outside {PACE_SLOW}–{PACE_FAST}: −{pen:.0f}.")
    else:
        lines.append(f"Pace {wpm} wpm within {PACE_SLOW}–{PACE_FAST}: no penalty.")
    rate = raw.get("fillers_per_100_words") or 0
    pen = min(25, rate * 5)
    score -= pen
    lines.append(f"{rate} fillers per 100 words × 5: −{pen:.0f} (capped at 25).")
    lp = raw.get("long_pause_count", 0)
    pen = min(20, lp * 5)
    score -= pen
    lines.append(f"{lp} pauses ≥ {LONG_PAUSE_S:.0f}s × 5: −{pen:.0f} (capped at 20)." if raw.get("pauses_measured")
                 else "Pauses not measured: no adjustment.")
    reps = len(raw["repetition"]["repeated_phrases"])
    pen = min(15, reps * 3)
    score -= pen
    lines.append(f"{reps} repeated phrases × 3: −{pen:.0f} (capped at 15).")
    return {"name": "Delivery", "value": round(max(0, score)), "derivation": lines}


def _presented(session) -> list[int]:
    return [s.slide_number for s in session.slides if session.words_on_slide(s.slide_number) >= 12]


def _score_coverage(session, presented: list[int]) -> dict:
    lines, fracs = [], []
    for n in presented:
        st = session.analysis.get(n)
        if not st:
            continue
        total = len(st.covered_concepts) + len(st.missed_concepts)
        if total == 0:
            continue
        f = len(st.covered_concepts) / total
        fracs.append(f)
        lines.append(f"Slide {n}: {len(st.covered_concepts)}/{total} key points covered ({st.source}).")
    if not fracs:
        return {"name": "Slide coverage", "value": None, "derivation": [UNDETERMINED]}
    lines.insert(0, "Average share of each presented slide's key points that you explained.")
    return {"name": "Slide coverage", "value": round(sum(fracs) / len(fracs) * 100), "derivation": lines}


def _score_consistency(session, presented: list[int]) -> dict:
    if not presented:
        return {"name": "Slide-speech consistency", "value": None, "derivation": [UNDETERMINED]}
    score, lines = 100, ["Start at 100."]
    for f in session.feedback:
        if f.category in ("slide_speech_consistency", "unsupported_claim", "off_topic", "reading_slide"):
            pen = {"high": 25, "medium": 10, "low": 5}[f.severity]
            score -= pen
            lines.append(f"Slide {f.slide_number} {f.category.replace('_', ' ')} ({f.severity}): −{pen}.")
    if len(lines) == 1:
        lines.append("No mismatches, unsupported claims, off-topic or read-aloud segments were detected.")
    return {"name": "Slide-speech consistency", "value": max(0, score), "derivation": lines}


def _visual_rows(session, presented: list[int]) -> list[dict]:
    rows = []
    for n in presented:
        slide: SlideContext = session.slide(n)
        st = session.analysis.get(n)
        if not slide.visual_elements:
            continue
        if st and st.visuals:
            for v in st.visuals:
                rows.append({"slide_number": n, "element": v.element, "explained": v.explained, "evidence": v.evidence})
        else:
            for v in slide.visual_elements:
                rows.append({"slide_number": n, "element": f"{v.kind}: {v.description}", "explained": "unknown", "evidence": ""})
    return rows


def _score_visuals(rows: list[dict]) -> dict:
    known = [r for r in rows if r["explained"] != "unknown"]
    if not known:
        return {"name": "Visual explanation", "value": None,
                "derivation": ["No charts, diagrams or tables on presented slides could be assessed. " + UNDETERMINED]}
    pts = {"yes": 1.0, "partial": 0.5, "no": 0.0}
    val = sum(pts[r["explained"]] for r in known) / len(known)
    lines = ["yes = 1, partial = 0.5, no = 0, averaged over assessed visuals."]
    lines += [f"Slide {r['slide_number']}: {r['element'][:60]} → {r['explained']}" for r in known]
    return {"name": "Visual explanation", "value": round(val * 100), "derivation": lines}


def _score_questions(session) -> dict:
    answered = [q for q in session.questions if q.status == "answered" and q.evaluation and q.evaluation.score is not None]
    if not answered:
        return {"name": "Question readiness", "value": None, "derivation": ["No questions were answered."]}
    lines = [f"'{q.question[:60]}…' → {q.evaluation.score}/5 ({q.evaluation.source})" for q in answered]
    lines.insert(0, "Average answer score (0–5) scaled to 100.")
    return {"name": "Question readiness", "value": round(sum(q.evaluation.score for q in answered) / len(answered) / 5 * 100),
            "derivation": lines}


def _rule_recommendations(session, interpretation: list[dict], visual_rows: list[dict]) -> list[dict]:
    recs = []
    for f in sorted(session.feedback, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f.severity]):
        if f.kind == "positive" or f.category == "delivery":
            continue
        recs.append({"title": f.category.replace("_", " ").capitalize() + f" (slide {f.slide_number})",
                     "detail": f.suggestion or f.observation, "evidence": f.evidence_slide or f.evidence_speech})
        if len(recs) >= 3:
            break
    missed_vis = [r for r in visual_rows if r["explained"] == "no"]
    if missed_vis and not any("Visual" in r["title"] for r in recs):
        slides = sorted({r["slide_number"] for r in missed_vis})
        recs.append({"title": "Explain your visuals", "detail": f"Rehearse a one sentence takeaway for the visuals on slide(s) {', '.join(map(str, slides))}.",
                     "evidence": "; ".join(r["element"][:60] for r in missed_vis[:3])})
    for i in interpretation:
        if i["kind"] == "improvement":
            recs.append({"title": "Delivery", "detail": i["suggestion"], "evidence": i["text"]})
    weak = [q for q in session.questions if q.evaluation and q.evaluation.score is not None and q.evaluation.score <= 2]
    if weak:
        recs.append({"title": "Prepare answers", "detail": "Prepare a crisp, evidence backed answer to: " + weak[0].question,
                     "evidence": "; ".join(weak[0].evaluation.gaps[:2])})
    return recs[:5]


async def build_report(session) -> dict:
    delivery = compute_delivery(session.segments, session.pauses, session.voiced_s, session.time_on_slide, session.elapsed())
    raw = delivery["raw"]
    interpretation = interpret_delivery(raw)
    presented = _presented(session)

    per_slide = []
    for s in session.slides:
        n = s.slide_number
        st = session.analysis.get(n)
        words = session.words_on_slide(n)
        per_slide.append({
            "slide_number": n, "title": s.title or f"Slide {n}", "words": words,
            "time_on_slide_s": round(session.time_on_slide.get(n, 0.0), 1),
            "presented": n in presented,
            "covered": st.covered_concepts if st else [],
            "missed": st.missed_concepts if st else [],
            "reading_ratio": st.reading_ratio if st else None,
            "source": st.source if st else "none",
            "note": "" if n in presented else ("Not presented" if words == 0 else "Too little speech to assess"),
        })
    visual_rows = _visual_rows(session, presented)

    fb = [f.model_dump() for f in session.feedback]
    mismatches = [f for f in fb if f["category"] == "slide_speech_consistency"]
    unsupported = [f for f in fb if f["category"] in ("unsupported_claim", "off_topic", "reading_slide")]

    questions = [q.model_dump() for q in session.questions]
    weak_q = [q for q in questions if q["evaluation"] and q["evaluation"]["score"] is not None and q["evaluation"]["score"] <= 2]

    scores = [
        _score_delivery(raw), _score_coverage(session, presented), _score_consistency(session, presented),
        _score_visuals(visual_rows), _score_questions(session),
    ]

    asked = [q.question for q in session.questions]
    practice = []
    for n in presented:
        q = rule_question(session.slide(n), session.speech_text(n), session.feedback, asked + [p["question"] for p in practice], session.mode)
        if q:
            practice.append({"question": q.question, "slide_number": n, "why": q.reason})
        if len(practice) >= 5:
            break
    narrative = {"summary": "", "top_recommendations": _rule_recommendations(session, interpretation, visual_rows),
                 "practice_questions": practice, "source": "rule"}

    llm = get_llm()
    if llm.available and raw["total_words"] >= 20:
        evidence = {
            "duration_s": raw["elapsed_s"], "slides_total": len(session.slides), "slides_presented": presented,
            "delivery_raw": {k: raw[k] for k in ("wpm", "total_words", "filler_total", "fillers", "pause_count", "long_pause_count", "repetition", "rushed_segments")},
            "delivery_interpretation": [i["text"] for i in interpretation],
            "per_slide": [{k: p[k] for k in ("slide_number", "title", "covered", "missed", "reading_ratio")} for p in per_slide if p["presented"]],
            "feedback": [{k: f[k] for k in ("slide_number", "kind", "category", "severity", "observation", "evidence_slide", "evidence_speech")} for f in fb][:30],
            "visuals": visual_rows[:15],
            "questions": [{"q": q["question"], "slide": q["slide_number"], "answer": q["answer_text"][:300],
                           "score": (q["evaluation"] or {}).get("score"), "gaps": (q["evaluation"] or {}).get("gaps")} for q in questions][:12],
        }
        try:
            data = await llm.generate_json(prompts.REPORT_SYSTEM, prompts.report_prompt(session.mode, evidence), max_tokens=1400, timeout=35)
            recs = [
                {"title": normalize(str(r.get("title", "")))[:80], "detail": normalize(str(r.get("detail", "")))[:400],
                 "evidence": normalize(str(r.get("evidence", "")))[:300]}
                for r in data.get("top_recommendations") or [] if isinstance(r, dict) and r.get("detail")
            ][:5]
            pqs = []
            for p in data.get("practice_questions") or []:
                if isinstance(p, dict) and isinstance(p.get("question"), str):
                    try:
                        sn = int(p.get("slide_number") or 0)
                    except (TypeError, ValueError):
                        sn = 0
                    pqs.append({"question": normalize(p["question"])[:300], "slide_number": sn if 1 <= sn <= len(session.slides) else None,
                                "why": normalize(str(p.get("why", "")))[:200]})
            if recs:
                narrative = {"summary": normalize(str(data.get("summary", "")))[:800], "top_recommendations": recs,
                             "practice_questions": pqs[:6] or practice, "source": "ai"}
        except LLMError as exc:
            log.info("report narrative fell back to rules: %s", exc)
            narrative["error"] = str(exc)

    return {
        "session_id": session.id,
        "generated_at": time.time(),
        "provider": llm.public_info(),
        "overview": {
            "mode": session.mode, "filename": session.filename, "duration_s": round(session.elapsed(), 1),
            "slides_total": len(session.slides), "slides_presented": len(presented),
            "slides_not_presented": [p["slide_number"] for p in per_slide if not p["presented"]],
        },
        "delivery": {"raw": raw, "caveats": delivery["caveats"], "interpretation": interpretation},
        "consistency": {"per_slide": per_slide, "mismatches": mismatches, "other_issues": unsupported,
                        "rejected_ungrounded_items": session.rejected_items},
        "visuals": {"items": visual_rows,
                    "counts": {k: sum(1 for r in visual_rows if r["explained"] == k) for k in ("yes", "partial", "no", "unknown")}},
        "questions": {"asked": questions, "weak": weak_q},
        "feedback": fb,
        "scores": scores,
        "scores_note": "Scores are transparent rule-based estimates, not validated measures. Each shows how it was derived.",
        "narrative": narrative,
    }
