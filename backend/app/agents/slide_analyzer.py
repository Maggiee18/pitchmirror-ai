"""Slide-Speech Consistency Agent + Visual Explanation Agent (one LLM call, validated and grounded).

Pipeline: transcript + slide -> deterministic evidence -> LLM reasoning -> schema validation -> grounding check.
Anything the model claims must be traceable to the slide or the transcript; otherwise it is discarded.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..analysis.evidence import compute_evidence, rule_feedback
from ..analysis.text_utils import fuzzy_contains, normalize
from ..models import FEEDBACK_CATEGORIES, FeedbackItem, SlideAnalysisState, SlideContext, VisualStatus
from . import prompts
from .llm import LLMError, get_llm
from .slide_context import slide_corpus, slide_payload

log = logging.getLogger("pitchmirror.agents.analyzer")

NEEDS_SPEECH_EVIDENCE = {"slide_speech_consistency", "unsupported_claim", "off_topic", "reading_slide"}
NEEDS_SLIDE_EVIDENCE = {"slide_speech_consistency", "missed_concept", "visual_gap"}
INTERIM_ALLOWED = {"slide_speech_consistency", "unsupported_claim", "off_topic", "reading_slide"}


@dataclass
class AnalysisResult:
    state: SlideAnalysisState
    feedback: list[FeedbackItem] = field(default_factory=list)
    rejected: int = 0
    evidence: dict = field(default_factory=dict)


def validate_feedback(raw: list, slide: SlideContext, transcript: str, final: bool) -> tuple[list[FeedbackItem], int]:
    items: list[FeedbackItem] = []
    rejected = 0
    corpus = slide_corpus(slide)
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            rejected += 1
            continue
        cat = str(r.get("category", "")).strip()
        kind = str(r.get("kind", "")).strip()
        sev = str(r.get("severity", "medium")).strip()
        obs = normalize(str(r.get("observation", "")))
        ev_slide = normalize(str(r.get("evidence_slide", "") or ""))
        ev_speech = normalize(str(r.get("evidence_speech", "") or ""))
        if cat not in FEEDBACK_CATEGORIES or kind not in ("positive", "improvement", "warning") or not obs:
            rejected += 1
            continue
        if sev not in ("low", "medium", "high"):
            sev = "medium"
        if not final and cat not in INTERIM_ALLOWED:
            continue  # too early to judge omissions
        # --- grounding checks: quotes must exist in the source they claim to come from ---
        if cat in NEEDS_SPEECH_EVIDENCE and cat != "reading_slide" and not ev_speech:
            rejected += 1
            continue
        if cat in NEEDS_SLIDE_EVIDENCE and not ev_slide:
            rejected += 1
            continue
        if ev_speech and not fuzzy_contains(transcript, ev_speech, 0.7):
            log.info("rejected ungrounded speech quote: %r", ev_speech[:80])
            rejected += 1
            continue
        if ev_slide and not fuzzy_contains(corpus, ev_slide, 0.6):
            log.info("rejected ungrounded slide quote: %r", ev_slide[:80])
            rejected += 1
            continue
        if kind == "warning" and not (ev_slide and ev_speech):
            kind = "improvement"
        items.append(FeedbackItem(
            slide_number=slide.slide_number, kind=kind, category=cat, severity=sev,
            observation=obs[:240], evidence_slide=ev_slide[:300], evidence_speech=ev_speech[:300],
            explanation=normalize(str(r.get("explanation", "")))[:300],
            suggestion=normalize(str(r.get("suggestion", "")))[:300], source="ai",
        ))
    positives = [i for i in items if i.kind == "positive"][:1]
    others = [i for i in items if i.kind != "positive"][:3]
    return others + positives, rejected


def _visuals_from_evidence(ev: dict) -> list[VisualStatus]:
    return [VisualStatus(element=v["element"][:160], explained=v["status"],
                         evidence=", ".join(v["matched_terms"][:5])) for v in ev["visuals"]]


async def analyze_slide(
    slide: SlideContext,
    transcript: str,
    mode: str,
    previous_feedback: list[str],
    deck_outline: list[str],
    final: bool,
) -> AnalysisResult:
    ev = compute_evidence(slide, transcript)
    state = SlideAnalysisState(
        slide_number=slide.slide_number,
        covered_concepts=ev["covered"],
        missed_concepts=ev["missed"],
        visuals=_visuals_from_evidence(ev),
        reading_ratio=ev["reading_ratio"],
        words_analyzed=ev["words_on_slide"],
        source="rule",
    )
    rules = rule_feedback(slide, ev, final)
    llm = get_llm()
    if not llm.available:
        return AnalysisResult(state, rules, 0, ev)

    evidence_for_model = {
        "words_spoken_on_slide": ev["words_on_slide"],
        "keyword_coverage": {"covered": ev["covered"], "not_detected": ev["missed"]},
        "slide_numbers": ev["slide_numbers"],
        "possible_number_conflicts": ev["possible_number_conflicts"],
        "visual_reference_check": [{k: v[k] for k in ("element", "status", "matched_terms")} for v in ev["visuals"]],
        "verbatim_reading_ratio": ev["reading_ratio"],
    }
    audience = prompts.MODE_FOCUS.get(mode, prompts.MODE_FOCUS["presentation"])
    try:
        data = await llm.generate_json(
            prompts.ANALYZE_SYSTEM_TMPL.format(audience=audience),
            prompts.analyze_prompt(slide_payload(slide), transcript, evidence_for_model, previous_feedback, deck_outline, final),
            max_tokens=1100,
        )
    except LLMError as exc:
        state.last_error = str(exc)
        return AnalysisResult(state, rules, 0, ev)

    items, rejected = validate_feedback(data.get("feedback"), slide, transcript, final)
    # Deterministic number conflicts are strong evidence; keep them if the model did not report the mismatch.
    if not any(i.category == "slide_speech_consistency" for i in items):
        items = [r for r in rules if r.category == "slide_speech_consistency"] + items

    covered = [normalize(c)[:120] for c in data.get("covered_concepts") or [] if isinstance(c, str)]
    missed = [normalize(c)[:120] for c in data.get("missed_concepts") or [] if isinstance(c, str)]
    corpus = slide_corpus(slide)
    missed = [m for m in missed if fuzzy_contains(corpus, m, 0.5)]  # must come from the slide
    visuals = []
    for v in data.get("visuals") or []:
        if isinstance(v, dict) and isinstance(v.get("element"), str):
            ex = v.get("explained") if v.get("explained") in ("yes", "partial", "no", "unknown") else "unknown"
            q = normalize(str(v.get("evidence") or ""))[:200]
            if q and not fuzzy_contains(transcript, q, 0.7):
                q = ""
            visuals.append(VisualStatus(element=normalize(v["element"])[:160], explained=ex, evidence=q))
    state.covered_concepts = covered or state.covered_concepts
    state.missed_concepts = missed if (covered or missed) else state.missed_concepts
    if visuals and slide.visual_elements:
        state.visuals = visuals
    state.source = "ai"
    return AnalysisResult(state, items, rejected, ev)
