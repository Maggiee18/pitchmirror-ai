"""Answer Intelligence: an additive analysis layer on top of the existing answer evaluation.

For every answer (and every "Try Again" attempt) it produces an assessment, per-dimension scores, what was good,
what was missing, a grounded better answer and why it is better. The AI reasons; code validates, grounds and
falls back. A better answer that mentions facts not found in the question, slide, presentation or the user's own
answer is withheld rather than shown.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..analysis.text_utils import normalize, number_value, numbers_in, stems, tech_terms, words
from ..models import AnswerAttempt, AnswerIntelligence, AnswerScores, Question, SlideContext
from . import prompts
from .llm import LLMError, get_llm
from .slide_context import slide_corpus, slide_payload

log = logging.getLogger("pitchmirror.agents.answer_intel")

ASSESSMENTS = ("strongly_correct", "mostly_correct", "partially_correct", "incorrect", "insufficient_evidence", "unable_to_determine")
DEPTHS = ("surface", "moderate", "strong", "deep")
UNDETERMINED = "Unable to determine from available presentation context."

JUSTIFICATION_CUES = {
    "why": re.compile(r"\b(because|since|so that|due to|the reason|in order to|as it|as we needed)\b", re.I),
    "how": re.compile(r"\b(by|using|through|works by|we use|pipeline|step|first|then)\b", re.I),
    "trade-offs": re.compile(r"\b(trade[- ]?off|instead of|compared (to|with)|but|however|whereas|versus|vs\.?|at the cost of|downside)\b", re.I),
    "evidence": re.compile(r"\b(measured|tested|benchmark|evaluated|results?|showed|dataset|experiment)\b|\d", re.I),
}


def _clean_list(v, limit=4, max_len=220) -> list[str]:
    out = []
    for x in v if isinstance(v, list) else []:
        if isinstance(x, str) and x.strip():
            out.append(normalize(x)[:max_len])
        if len(out) >= limit:
            break
    return out


def _score(v) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def question_kind(q: str) -> str:
    ql = q.lower()
    if re.search(r"\bwhy\b|reason|justify|choose|chose|instead of", ql):
        return "why"
    if re.search(r"\bhow\b|walk me through|explain|mechanism", ql):
        return "how"
    if re.search(r"\bwhat\b.*\b(measure|metric|baseline|evidence|data)|\bcompared\b|\bnumber\b", ql):
        return "evidence"
    return "general"


def detect_justification(answer: str, slide: SlideContext) -> list[str]:
    found = [k for k, rx in JUSTIFICATION_CUES.items() if rx.search(answer)]
    if len(stems(answer) & stems(slide_corpus(slide))) >= 3:
        found.append("project-specific")
    return found


def _scaffold(question: str) -> str:
    terms = tech_terms(question)
    kind = question_kind(question)
    if kind == "why" and terms:
        return f"What requirement of your project made {terms[0]} a good fit, compared with the obvious alternative?"
    if kind == "why":
        return "What was the single most important requirement behind this decision?"
    if kind == "how":
        return "What are the two or three main steps, in order, and what does each one produce?"
    if kind == "evidence":
        return "What did you compare against, and which number shows the difference?"
    return "What is the one point you most want the examiner to remember, and what supports it?"


def rule_intel(question: str, answer: str, slide: SlideContext) -> AnswerIntelligence:
    """Deterministic fallback. It measures what it can and says plainly what it cannot judge."""
    toks = words(answer)
    if len(toks) < 3:
        return AnswerIntelligence(
            assessment="insufficient_evidence", what_was_missing=["No answer was captured, or it was too short to assess."],
            retry_recommended=True, scaffold_question=_scaffold(question), source="rule",
            note="Answer too short to analyse.",
        )
    q_st, a_st = stems(question), stems(answer)
    overlap = len(q_st & a_st) / max(len(q_st), 1)
    relevance = min(100, round(overlap * 140))
    just = detect_justification(answer, slide)
    kind = question_kind(question)
    good, missing = [], []
    labels = {"why": "Gave a reason (why)", "how": "Explained how it works", "trade-offs": "Mentioned a trade-off or alternative",
              "evidence": "Referred to evidence or numbers", "project-specific": "Tied the answer to your own project"}
    for j in just:
        good.append(labels[j])
    expected = {"why": ["why", "trade-offs"], "how": ["how"], "evidence": ["evidence"], "general": ["why"]}[kind]
    for e in expected + ["project-specific"]:
        if e not in just:
            missing.append({"why": "No explicit reason (why)", "how": "No explanation of how it works",
                            "trade-offs": "No alternatives or trade-offs considered", "evidence": "No evidence or measurement",
                            "project-specific": "Not connected to your project's specifics"}[e])
    if overlap < 0.25:
        missing.insert(0, "The answer shares few terms with the question; it may not address it directly")
    return AnswerIntelligence(
        assessment="unable_to_determine",
        scores=AnswerScores(relevance=relevance),
        justification=just,
        what_was_good=good[:4],
        what_was_missing=missing[:4],
        retry_recommended=bool(missing),
        scaffold_question=_scaffold(question) if missing else "",
        source="rule",
        note="Rule based: relevance is estimated from keyword overlap. Correctness, completeness and a better answer "
             "need the AI model. " + UNDETERMINED,
    )


def _grounding_violations(text: str, allowed: str) -> list[str]:
    """Technologies or numbers in `text` that appear nowhere in the allowed context."""
    allowed_low = allowed.lower()
    bad = [t for t in tech_terms(text) if t.lower() not in allowed_low]
    allowed_vals = {number_value(n) for n in numbers_in(allowed)}
    for n in numbers_in(text):
        v = number_value(n)
        if v is not None and v not in allowed_vals and not (v.is_integer() and v <= 10):
            bad.append(n)
    return bad


def validate_intel(data: dict, question: str, answer: str, slide: SlideContext, context: str) -> AnswerIntelligence:
    assessment = data.get("assessment") if data.get("assessment") in ASSESSMENTS else "unable_to_determine"
    sc = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    scores = AnswerScores(**{k: _score(sc.get(k)) for k in ("correctness", "relevance", "completeness", "technical_depth", "clarity")})
    if assessment in ("unable_to_determine", "insufficient_evidence"):
        scores.correctness = None
    intel = AnswerIntelligence(
        assessment=assessment,
        scores=scores,
        depth_level=data.get("depth_level") if data.get("depth_level") in DEPTHS else None,
        justification=[j for j in _clean_list(data.get("justification"), 5, 40)],
        what_was_good=_clean_list(data.get("what_was_good")),
        what_was_missing=_clean_list(data.get("what_was_missing")),
        could_be_stronger=_clean_list(data.get("could_be_stronger"), 3),
        better_answer=normalize(str(data.get("better_answer") or ""))[:900],
        why_better=_clean_list(data.get("why_better"), 4),
        retry_recommended=bool(data.get("retry_recommended")),
        scaffold_question=normalize(str(data.get("scaffold_question") or ""))[:300],
        source="ai",
    )
    if assessment == "unable_to_determine":
        intel.note = UNDETERMINED
    if intel.better_answer:
        allowed = " ".join([question, answer, slide_corpus(slide), context])
        bad = _grounding_violations(intel.better_answer, allowed)
        if bad:
            log.info("better answer withheld, ungrounded: %s", bad)
            intel.better_answer, intel.why_better = "", []
            intel.note = (intel.note + " " if intel.note else "") + (
                "Suggested answer withheld because it mentioned details not found in your presentation "
                f"({', '.join(bad[:3])}).")
    if not intel.better_answer:
        intel.why_better = []
    return intel


async def analyze_answer(q: Question, answer: str, slide: SlideContext, mode: str, context: str,
                         attempt: int, previous: list[AnswerAttempt]) -> AnswerIntelligence:
    if len(words(answer)) < 3:
        return rule_intel(q.question, answer, slide)
    llm = get_llm()
    if not llm.available:
        return rule_intel(q.question, answer, slide)
    audience = prompts.MODE_FOCUS.get(mode, prompts.MODE_FOCUS["presentation"])
    prev = [{"attempt": a.attempt, "answer": a.answer_text[:400],
             "assessment": a.intel.assessment if a.intel else None} for a in previous]
    try:
        data = await llm.generate_json(
            prompts.ANSWER_INTEL_SYSTEM_TMPL.format(audience=audience),
            prompts.answer_intel_prompt(q.question, answer[-3000:], slide_payload(slide), context[-2500:], mode, attempt, prev),
            max_tokens=1100,
        )
    except LLMError as exc:
        log.info("answer intelligence fell back to rules: %s", exc)
        intel = rule_intel(q.question, answer, slide)
        intel.note = f"AI analysis unavailable ({exc}). " + intel.note
        return intel
    return validate_intel(data, q.question, answer, slide, context)


def compare_attempts(first: AnswerAttempt, latest: AnswerAttempt) -> dict:
    """Deterministic comparison. Only reports a number when both attempts were scored the same way."""
    a, b = first.intel, latest.intel
    if not a or not b:
        return {"comparable": False, "reason": "One of the attempts has no analysis."}
    if a.source != b.source:
        return {"comparable": False, "reason": "The attempts were analysed differently (AI vs rules), so scores aren't comparable."}
    oa, ob = a.scores.overall(), b.scores.overall()
    if oa is None or ob is None:
        if a.source == "rule" and a.scores.relevance is not None and b.scores.relevance is not None:
            return {"comparable": True, "reliable": False, "metric": "relevance (rough keyword estimate)", "before": a.scores.relevance,
                    "after": b.scores.relevance, "delta": b.scores.relevance - a.scores.relevance,
                    "reason": "Without the AI model only keyword relevance can be compared; treat this as a rough signal."}
        return {"comparable": False, "reason": "Not enough scored dimensions to compare reliably."}
    dims = {}
    for k in ("correctness", "relevance", "completeness", "technical_depth", "clarity"):
        va, vb = getattr(a.scores, k), getattr(b.scores, k)
        if va is not None and vb is not None:
            dims[k] = {"before": va, "after": vb, "delta": vb - va}
    return {"comparable": True, "reliable": True, "metric": "answer quality (average of scored dimensions)", "before": oa, "after": ob,
            "delta": ob - oa, "dimensions": dims}
