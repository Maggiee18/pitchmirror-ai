"""Extended report sections (additive): Q&A Intelligence, Technical Communication, Claims & Consistency, Rehearsal.

Deterministic measurements first; one optional LLM call refines term explainability, audience fit, claim types and
challenge questions. Every AI output is validated against the deterministic candidates, so the model can only
comment on terms and claims that code actually found in the presenter's speech.
"""
from __future__ import annotations

import logging

from ..analysis.claims import detect_claims
from ..analysis.memory import find_inconsistencies, statements_from
from ..analysis.rehearsal import compare_metrics, slide_metrics, weakest_slide
from ..analysis.tech_comm import analyze_terms, heuristic_audience
from ..analysis.text_utils import normalize, stems
from . import prompts
from .llm import LLMError, get_llm

log = logging.getLogger("pitchmirror.agents.intelligence")

WEAK = {"partially_correct", "incorrect", "insufficient_evidence"}
RANK = {"strongly_correct": 5, "mostly_correct": 4, "partially_correct": 3, "unable_to_determine": 2.5,
        "insufficient_evidence": 1, "incorrect": 0}


def qa_intelligence(session) -> list[dict]:
    out = []
    for q in session.questions:
        if not q.attempts:
            continue
        atts = []
        for a in q.attempts:
            i = a.intel
            atts.append({
                "attempt": a.attempt, "answer": a.answer_text,
                "assessment": i.assessment if i else None, "overall": i.scores.overall() if i else None,
                "scores": i.scores.model_dump() if i else None,
                "what_was_good": i.what_was_good if i else [], "what_was_missing": i.what_was_missing if i else [],
                "could_be_stronger": i.could_be_stronger if i else [], "better_answer": i.better_answer if i else "",
                "why_better": i.why_better if i else [], "note": i.note if i else "", "source": i.source if i else None,
            })
        first, last = atts[0], atts[-1]
        retry = None
        if len(atts) > 1:
            if first["overall"] is not None and last["overall"] is not None and first["source"] == last["source"]:
                retry = {"comparable": True, "before": first["overall"], "after": last["overall"],
                         "delta": last["overall"] - first["overall"], "attempts": len(atts)}
            elif first["source"] == last["source"] == "rule":
                retry = {"comparable": False, "attempts": len(atts),
                         "reason": "Without the AI model answers aren't scored, so no improvement number is shown."}
            else:
                retry = {"comparable": False, "attempts": len(atts),
                         "reason": "Attempts could not be scored the same way, so no improvement number is shown."}
        out.append({"question_id": q.id, "question": q.question, "slide_number": q.slide_number, "depth": q.depth,
                    "attempts": atts, "latest_assessment": last["assessment"], "retry": retry,
                    "weak": last["assessment"] in WEAK})
    return out


def _speech_pairs(session) -> list[tuple[int, str]]:
    return [(s.slide_number, s.text) for s in session.segments if s.kind == "speech"]


def _validate_llm(data: dict, terms: list[dict], claims: list[dict]) -> tuple[dict, dict, dict]:
    by_term = {t["term"].lower(): t for t in terms}
    term_updates = {}
    for t in data.get("terms") or []:
        if not isinstance(t, dict):
            continue
        key = str(t.get("term", "")).lower().strip()
        if key in by_term and t.get("explained") in ("yes", "partial", "no"):
            simp = normalize(str(t.get("simplification") or ""))[:300]
            term_updates[key] = {"explained": t["explained"], "simplification": simp}
    aud = data.get("audience") if isinstance(data.get("audience"), dict) else {}
    audience = {}
    if aud.get("level") in ("appropriate", "consider_simplifying", "likely_difficult"):
        audience = {"level": aud["level"], "rationale": normalize(str(aud.get("rationale", "")))[:400], "source": "ai"}
    claim_updates = {}
    for c in data.get("claims") or []:
        if not isinstance(c, dict):
            continue
        text = normalize(str(c.get("claim", "")))
        match = next((x for x in claims if x["claim"][:60].lower() == text[:60].lower()), None)
        if not match:
            continue
        upd = {}
        if c.get("type") in ("factual", "quantitative", "technical", "subjective", "opinion"):
            upd["type"] = c["type"]
        ch = normalize(str(c.get("challenge") or ""))[:300]
        if ch and len(stems(ch) & stems(match["claim"])) >= 1:
            upd["challenge"] = ch
        # the model may relax "needs evidence" only if code also found no numbers missing from the slides
        if isinstance(c.get("needs_evidence"), bool) and not (match["type"] == "quantitative" and match["needs_evidence"]):
            upd["needs_evidence"] = c["needs_evidence"]
        claim_updates[match["claim"]] = upd
    return term_updates, audience, claim_updates


async def build_intelligence(session) -> dict:
    qa = qa_intelligence(session)
    speech_texts = [t for _, t in _speech_pairs(session)]
    measured = analyze_terms(speech_texts)
    terms = measured.pop("terms")
    key_terms = terms[:15]
    claims = detect_claims(_speech_pairs(session), session.slides)
    statements = []
    for n, t in _speech_pairs(session):
        statements += statements_from(n, t)
    inconsistencies = find_inconsistencies(statements, session.slides)

    for t in key_terms:
        t["explained"] = t["explained_heuristic"]
        t["explained_source"] = "rule"
        t["simplification"] = ""
    audience = None
    llm = get_llm()
    if llm.available and (key_terms or claims):
        try:
            data = await llm.generate_json(
                prompts.TECH_COMM_SYSTEM,
                prompts.tech_comm_prompt(
                    session.mode, {k: measured[k] for k in ("meaningful_words", "technical_occurrences", "unique_terms", "density_pct")},
                    [{"term": t["term"], "count": t["count"], "said": t["contexts"]} for t in key_terms],
                    [{"claim": c["claim"], "slide": c["slide_number"], "code_evidence": c["evidence"]} for c in claims[:10]],
                ),
                max_tokens=1600, timeout=30,
            )
            term_updates, audience_ai, claim_updates = _validate_llm(data, key_terms, claims)
            for t in key_terms:
                u = term_updates.get(t["term"].lower())
                if u:
                    t["explained"], t["simplification"], t["explained_source"] = u["explained"], u["simplification"], "ai"
            for c in claims:
                if c["claim"] in claim_updates:
                    c.update(claim_updates[c["claim"]])
                    c["source"] = "ai"
            audience = audience_ai or None
        except LLMError as exc:
            log.info("technical communication AI step fell back to rules: %s", exc)

    needing = [t for t in key_terms if t["explained"] == "no"]
    explained = [t for t in key_terms if t["explained"] in ("yes", "partial")]
    if audience is None:
        audience = heuristic_audience(session.mode, measured["density_pct"], len(needing) / max(len(key_terms), 1))

    weakest = weakest_slide(session, claims, qa)
    rehearsal = None
    if getattr(session, "rehearsal", None):
        r = session.rehearsal
        after = slide_metrics(session, r["slide_number"])
        rehearsal = {**{k: r[k] for k in ("parent_id", "slide_number", "slide_title")}, "before": r["baseline"], "after": after,
                     "changes": compare_metrics(r["baseline"], after)}

    scored = [q for q in qa if q["attempts"][-1]["assessment"]]
    strongest = sorted(scored, key=lambda q: (RANK.get(q["latest_assessment"], 2), q["attempts"][-1]["overall"] or 0), reverse=True)
    return {
        "qa": {
            "items": qa,
            "strongest": [q["question_id"] for q in strongest[:2] if RANK.get(q["latest_assessment"], 0) >= 4],
            "weakest": [q["question_id"] for q in reversed(strongest) if q["weak"]][:2],
        },
        "technical_communication": {
            **measured,
            "terms_detected": len(terms),
            "key_terms": [{k: t[k] for k in ("term", "count", "explained", "explained_source", "simplification", "contexts")} for t in key_terms],
            "terms_explained": len(explained),
            "terms_needing_explanation": len(needing),
            "audience": {**audience, "mode": session.mode},
            "note": "Technical language density is descriptive, not a quality score.",
        },
        "claims": {"items": claims, "needing_evidence": sum(1 for c in claims if c.get("needs_evidence"))},
        "consistency_memory": inconsistencies,
        "weakest_slide": weakest,
        "rehearsal": rehearsal,
    }
