"""Deterministic slide vs speech evidence.

This layer turns raw text into structured, checkable evidence (coverage, numbers, overlap). The LLM then
reasons over this evidence; in offline mode, rule based feedback is generated from it directly.
"""
from __future__ import annotations

from ..models import FeedbackItem, SlideContext
from .text_utils import (
    content_words,
    is_contact_line,
    is_covered,
    is_year_value,
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


def _table_rows(slide: SlideContext) -> list[str]:
    """Turn extracted table data ('h1 | h2; r1 | r2') into header-labelled rows so numbers keep their meaning."""
    rows_out = []
    for v in slide.visual_elements:
        if v.kind != "table" or "|" not in v.data_summary:
            continue
        rows = [[c.strip() for c in r.split("|")] for r in v.data_summary.split(";") if r.strip()]
        if len(rows) < 2:
            continue
        header = rows[0]
        for r in rows[1:]:
            cells = [f"{h} {c}".strip() for h, c in zip(header[1:], r[1:])]
            rows_out.append(f"{r[0]}: " + ", ".join(cells))
    return rows_out


def _is_number_token(t: str) -> bool:
    return any(ch.isdigit() for ch in t) and number_value(t) is not None


def number_evidence(slide: SlideContext, speech: str) -> dict:
    said_vals = spoken_numbers(speech)
    speech_toks = tokens(speech)

    def mentioned(v: float) -> bool:
        return any(abs(s - v) <= max(0.01 * abs(v), 1e-6) for s in said_vals)

    slide_nums = []
    for line in [slide.title, *slide.text, *_table_rows(slide)]:
        if is_contact_line(line):
            continue
        line_words = words(line)
        for tok in numbers_in(line):
            v = number_value(tok)
            if v is None or _is_year(tok):
                continue
            slide_nums.append({"token": tok, "value": v, "line": line, "percent": "%" in tok, "mentioned": mentioned(v),
                               "stems": stems(line), "bigrams": set(ngrams(line_words, 2))})

    # Each spoken number gets the words around it, bounded by the neighbouring numbers, so a sentence that lists
    # several figures does not mix up their contexts.
    num_idx = [i for i, t in enumerate(speech_toks) if _is_number_token(t)]
    conflicts, seen = [], set()
    for k, i in enumerate(num_idx):
        t = speech_toks[i]
        v = number_value(t)
        if is_year_value(v, t) or abs(v) >= 1e6 and "," not in t:
            continue  # years ("since 2023") and phone-like numbers are not claims
        lo = max(num_idx[k - 1] + 1 if k else 0, i - 8)
        hi = min(num_idx[k + 1] if k + 1 < len(num_idx) else len(speech_toks), i + 9)
        ctx_toks = speech_toks[lo:hi]
        pct = (i + 1 < len(speech_toks) and speech_toks[i + 1] in ("percent", "%", "per")) or t.endswith("%")
        ctx_stems = stems(" ".join(ctx_toks))
        ctx_bigrams = set(ngrams([w.strip(".,'") for w in ctx_toks], 2))
        scored = []
        for sn in slide_nums:
            if sn["percent"] != pct:
                continue
            overlap = sn["stems"] & ctx_stems
            if len(overlap) >= 2:
                scored.append(((len(overlap), len(sn["bigrams"] & ctx_bigrams)), sn, overlap))
        if not scored:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best, overlap = scored[0]
        ties = [x for x in scored if x[0] == best_score and abs(x[1]["value"] - best["value"]) > 1e-9]
        if len(ties) > 1:
            continue  # ambiguous which slide figure is meant
        if abs(v - best["value"]) <= 0.05 * max(abs(best["value"]), 1) or best["mentioned"] or best["token"] in seen:
            continue
        seen.add(best["token"])
        conflicts.append({
            "slide_line": best["line"], "slide_value": best["token"],
            "speech_excerpt": " ".join(ctx_toks), "speech_value": v,
            "shared_terms": sorted(overlap)[:6],
        })
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
    if rel is not None and rel < 0.1 and wc >= 30:
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
