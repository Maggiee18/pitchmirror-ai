"""Deterministic slide vs speech evidence.

This layer turns raw text into structured, checkable evidence (coverage, numbers, overlap). The LLM then
reasons over this evidence; in offline mode, rule based feedback is generated from it directly.
"""
from __future__ import annotations

from ..models import FeedbackItem, SlideContext
from .text_utils import (
    content_words,
    is_covered,
    ngrams,
    number_value,
    numbers_in,
    phrase_covered,
    spoken_numbers,
    stem,
    stems,
    tokens,
    words,
)

VISUAL_REFERENCE_WORDS = {
    "chart", "graph", "plot", "figure", "table", "diagram", "bar", "bars", "axis", "column", "columns", "row",
    "rows", "pipeline", "flow", "arrow", "arrows", "box", "boxes", "block", "trend", "curve", "line", "peak",
    "left", "right", "top", "bottom", "compare", "compared", "comparison", "highest", "lowest", "x-axis", "y-axis",
    "legend", "image", "picture", "screenshot", "architecture", "stage", "step", "module", "shows", "see",
}
VISUAL_REF_STEMS = {stem(w) for w in VISUAL_REFERENCE_WORDS}


def _slide_lines(slide: SlideContext) -> list[str]:
    lines = [slide.title, *slide.text]
    for v in slide.visual_elements:
        if v.data_summary:
            lines.append(v.data_summary)
    return [l for l in lines if l]


def _is_year(tok: str) -> bool:
    v = number_value(tok)
    return v is not None and "%" not in tok and 1900 <= v <= 2100 and float(v).is_integer()


def number_evidence(slide: SlideContext, speech: str) -> dict:
    said_vals = spoken_numbers(speech)
    speech_toks = tokens(speech)

    def mentioned(v: float) -> bool:
        return any(abs(s - v) <= max(0.01 * abs(v), 1e-6) for s in said_vals)

    slide_nums = []
    for line in [slide.title, *slide.text]:
        for tok in numbers_in(line):
            v = number_value(tok)
            if v is None:
                continue
            slide_nums.append({"token": tok, "value": v, "line": line, "percent": "%" in tok, "mentioned": mentioned(v)})

    # speech numbers with a context window, so they can be compared with the slide line they seem to talk about
    speech_nums = []
    for i, t in enumerate(speech_toks):
        v = number_value(t) if any(ch.isdigit() for ch in t) else None
        if v is None:
            continue
        ctx = speech_toks[max(0, i - 8) : i + 9]
        pct = (i + 1 < len(speech_toks) and speech_toks[i + 1] in ("percent", "%", "per")) or t.endswith("%")
        speech_nums.append({"value": v, "percent": pct, "context": " ".join(ctx)})

    conflicts = []
    for sn in slide_nums:
        if sn["mentioned"] or _is_year(sn["token"]):
            continue
        line_stems = stems(sn["line"])
        for sp in speech_nums:
            if sp["percent"] != sn["percent"]:
                continue
            if abs(sp["value"] - sn["value"]) <= 0.05 * max(abs(sn["value"]), 1):
                continue
            ctx_stems = stems(sp["context"])
            overlap = line_stems & ctx_stems
            if len(overlap) >= 2:
                conflicts.append({
                    "slide_line": sn["line"], "slide_value": sn["token"],
                    "speech_excerpt": sp["context"], "speech_value": sp["value"],
                    "shared_terms": sorted(overlap)[:6],
                })
                break
    return {
        "slide_numbers": [{k: n[k] for k in ("token", "line", "mentioned")} for n in slide_nums][:15],
        "possible_number_conflicts": conflicts[:4],
    }


def visual_evidence(slide: SlideContext, speech: str) -> list[dict]:
    sp_stems = stems(speech)
    generic_refs = sorted(sp_stems & VISUAL_REF_STEMS)
    out = []
    n_words = len(speech.split())
    for v in slide.visual_elements:
        label_stems = stems(v.data_summary + " " + v.description) - VISUAL_REF_STEMS
        label_stems -= {"occupy", "slide", "raster", "vector", "shape", "nearby", "text", "label", "row", "column"}
        overlap = sorted(label_stems & sp_stems)
        if n_words < 15:
            status = "unknown"
        elif generic_refs and len(overlap) >= 2:
            status = "yes"
        elif generic_refs or len(overlap) >= 2:
            status = "partial"
        else:
            status = "no"
        out.append({"element": f"{v.kind}: {v.description}", "kind": v.kind, "status": status,
                    "reference_words": generic_refs[:6], "matched_terms": overlap[:8]})
    return out


def reading_ratio(slide: SlideContext, speech: str) -> float | None:
    sp = words(speech)
    if len(sp) < 20:
        return None
    slide_grams = set(ngrams(words(" ".join([slide.title, *slide.text])), 4))
    sp_grams = ngrams(sp, 4)
    if not sp_grams or not slide_grams:
        return 0.0
    return round(sum(1 for g in sp_grams if g in slide_grams) / len(sp_grams), 2)


def relevance(slide: SlideContext, speech: str) -> float | None:
    sp = set(stem(w) for w in content_words(speech))
    if len(sp) < 12:
        return None
    sl = stems(" ".join(_slide_lines(slide) + slide.key_concepts + [slide.visual_description, slide.notes]))
    if not sl:
        return None
    return round(len(sp & sl) / min(len(sp), 25), 2)


def compute_evidence(slide: SlideContext, speech: str) -> dict:
    sp_stems = stems(speech)
    coverage = []
    for c in slide.key_concepts:
        cov = phrase_covered(c, sp_stems)
        coverage.append({"concept": c, "coverage": round(cov, 2), "covered": is_covered(c, sp_stems)})
    return {
        "words_on_slide": len(speech.split()),
        "concept_coverage": coverage,
        "covered": [c["concept"] for c in coverage if c["covered"]],
        "missed": [c["concept"] for c in coverage if not c["covered"]],
        **number_evidence(slide, speech),
        "visuals": visual_evidence(slide, speech),
        "reading_ratio": reading_ratio(slide, speech),
        "relevance": relevance(slide, speech),
    }


def rule_feedback(slide: SlideContext, ev: dict, final: bool) -> list[FeedbackItem]:
    """Offline feedback derived only from deterministic evidence. Used when no LLM is configured or it fails."""
    n = slide.slide_number
    items: list[FeedbackItem] = []
    wc = ev["words_on_slide"]

    for c in ev["possible_number_conflicts"]:
        items.append(FeedbackItem(
            slide_number=n, kind="warning", category="slide_speech_consistency", severity="high",
            observation=f"Possible number mismatch: the slide says {c['slide_value']}, but you said {c['speech_value']:g}.",
            evidence_slide=c["slide_line"], evidence_speech=c["speech_excerpt"],
            explanation=f"Both refer to {', '.join(c['shared_terms'][:3])}, but the values differ.",
            suggestion="Check which number is correct and say the one on the slide, or explain the difference.",
        ))

    rr = ev["reading_ratio"]
    if rr is not None and rr >= 0.45 and wc >= 25:
        items.append(FeedbackItem(
            slide_number=n, kind="improvement", category="reading_slide", severity="medium",
            observation=f"About {int(rr * 100)}% of what you said repeats the slide text word for word.",
            evidence_slide=slide.text[0] if slide.text else slide.title,
            explanation="The audience can read the slide; they need the why and the so what.",
            suggestion="Explain the reasoning or impact behind each bullet instead of reading it.",
        ))

    rel = ev["relevance"]
    if rel is not None and rel < 0.1 and wc >= 40:
        items.append(FeedbackItem(
            slide_number=n, kind="warning", category="off_topic", severity="medium",
            observation="What you are saying has little overlap with this slide's content.",
            evidence_slide=slide.title,
            explanation=f"Only {int(rel * 100)}% of your key terms match the slide.",
            suggestion="Either move to the slide you are describing or tie your point back to this slide.",
        ))

    if final and wc >= 12:
        for v in ev["visuals"]:
            if v["status"] == "no":
                items.append(FeedbackItem(
                    slide_number=n, kind="improvement", category="visual_gap", severity="medium",
                    observation=f"The {v['kind']} on this slide was not explained.",
                    evidence_slide=v["element"],
                    explanation="No reference to the visual or its labels was detected in your speech.",
                    suggestion=f"Walk the audience through the {v['kind']}: what it shows and the one takeaway.",
                ))
        important_missed = [c for c in ev["missed"] if len(c.split()) >= 2 or numbers_in(c)][:3]
        if important_missed and len(ev["missed"]) <= len(ev["covered"]):
            important_missed = [c for c in important_missed if numbers_in(c)][:2]  # mostly covered: only flag missed numbers
        if important_missed:
            items.append(FeedbackItem(
                slide_number=n, kind="improvement", category="missed_concept", severity="medium" if len(important_missed) > 1 else "low",
                observation="Some key points on this slide were not mentioned.",
                evidence_slide="; ".join(important_missed),
                explanation=f"Covered {len(ev['covered'])} of {len(ev['concept_coverage'])} key points (keyword match).",
                suggestion="Mention these points or remove them from the slide.",
            ))
        if len(ev["covered"]) >= max(2, int(0.7 * len(ev["concept_coverage"]))) and (rr is None or rr < 0.45):
            items.append(FeedbackItem(
                slide_number=n, kind="positive", category="strength", severity="low",
                observation="You covered most of this slide's key points in your own words.",
                evidence_slide="; ".join(ev["covered"][:3]),
            ))
    for it in items:
        it.source = "rule"
    return items
