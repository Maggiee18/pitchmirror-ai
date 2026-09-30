"""Adaptive Q&A Agent: asks grounded questions and deeper follow ups based on the presenter's answers."""
from __future__ import annotations

import re
import logging
from typing import Optional

from ..analysis.evidence import compute_evidence
from ..analysis.text_utils import STOPWORDS, is_claim_line, is_contact_line, normalize, numbers_in, stems, tech_terms, words
from ..models import AnswerEvaluation, FeedbackItem, Question, SlideContext
from . import prompts
from .llm import LLMError, get_llm
from .slide_context import slide_payload

log = logging.getLogger("pitchmirror.agents.audience")

ACTION_RE = re.compile(r"\b(developed|built|designed|implemented|created|led|deployed|engineered|trained|automated|"
                       r"architected|optimi[sz]ed|integrated|launched|debugged|validated)\b", re.IGNORECASE)

REASONING_MARKERS = {"because", "since", "so", "therefore", "chose", "choose", "trade", "tradeoff", "compared",
                     "instead", "measured", "tested", "evaluated", "benchmark", "result", "data", "assume", "assumption"}


def _is_repeat(q: str, asked: list[str]) -> bool:
    s = stems(q)
    for a in asked:
        t = stems(a)
        if s and t and len(s & t) / max(len(s | t), 1) > 0.6:
            return True
    return False


def weak_points(slide: SlideContext, feedback: list[FeedbackItem], transcript: str) -> list[str]:
    pts = [f"{f.category}: {f.observation} (slide: {f.evidence_slide})" for f in feedback
           if f.slide_number == slide.slide_number and f.kind != "positive"]
    ev = compute_evidence(slide, transcript)
    pts += [f"not explained: {m}" for m in ev["missed"][:3]]
    pts += [f"{v['kind']} explanation status: {v['status']}" for v in ev["visuals"] if v["status"] in ("no", "partial")]
    return pts[:8]


def rule_question(slide: SlideContext, transcript: str, feedback: list[FeedbackItem], asked: list[str], mode: str) -> Optional[Question]:
    """Deterministic, grounded fallback. Every template is filled with real slide/transcript content."""
    ev = compute_evidence(slide, transcript)
    n = slide.slide_number
    candidates: list[tuple[str, str, str]] = []
    for c in ev["possible_number_conflicts"]:
        candidates.append((
            f"Your slide says {c['slide_value']} ('{c['slide_line'][:80]}'), but you said {c['speech_value']:g}. Which is correct, and how was it measured?",
            "Number on the slide differs from the spoken number.", "medium"))
    for v in ev["visuals"]:
        if v["status"] in ("no", "partial"):
            if mode == "viva":
                q = f"Walk me through the {v['kind']} on slide {n}. What exactly does it show, and what should I conclude from it?"
            elif mode == "pitch":
                q = f"What is the one takeaway from the {v['kind']} on this slide that should convince an investor?"
            else:
                q = f"Can you explain what the {v['kind']} on this slide shows and why it matters?"
            candidates.append((q, f"The {v['kind']} was not clearly explained.", "medium"))
    if mode == "interview":
        # resumes and project decks: ask about what the candidate did, not about dates or grades
        work = [l for l in slide.text if ACTION_RE.search(l) and not is_contact_line(l) and len(l.split()) >= 5]
        spoken = [l for l in work if stems(l) & stems(transcript)]
        for line in (spoken or work)[:3]:
            candidates.append((f"You wrote '{line[:90]}'. What was your specific contribution, and what was the hardest problem you solved there?",
                               "Probes ownership and depth behind an experience or project claim.", "medium"))
    numeric_lines = [l for l in slide.text if is_claim_line(l)]
    for line in numeric_lines[:3]:
        if mode == "pitch":
            q = f"Your slide claims '{line[:90]}'. What assumptions and data are behind that number?"
        elif mode in ("viva", "interview"):
            q = f"Your slide states '{line[:90]}'. Where does that number come from, and how was it measured?"
        else:
            q = f"What caused the result '{line[:90]}'?"
        candidates.append((q, "Quantitative claim on the slide.", "medium"))
    terms = [t for t in tech_terms(slide.full_text() + " " + " ".join(v.data_summary for v in slide.visual_elements))
             if t.lower() in transcript.lower()] or tech_terms(slide.full_text())
    if slide.slide_number == 1:  # the title slide's names are usually the project itself, not a design choice
        terms = [t for t in terms if t.lower() not in slide.title.lower()]
    for term in terms[:3]:
        if mode in ("viva", "interview"):
            candidates.append((f"Why did you choose {term} here, and what would you lose with the most common alternative?",
                               f"Probes the design decision behind {term}.", "hard"))
        elif mode == "pitch":
            candidates.append((f"You rely on {term}. What stops a competitor from building the same thing?", "Probes differentiation.", "hard"))
    for m in ev["missed"][:2]:
        candidates.append((f"Your slide lists '{m}', but you didn't cover it. How does it fit into your argument?",
                           "Slide element not explained.", "easy"))
    if slide.title:
        candidates.append((f"What is the single most important takeaway from '{slide.title[:70]}', and what evidence supports it?",
                           "Check the presenter can state the slide's point.", "easy"))
    for q, reason, diff in candidates:
        if not _is_repeat(q, asked):
            return Question(slide_number=n, question=q, reason=reason, difficulty=diff, source="rule")
    return None


async def generate_question(slide: SlideContext, transcript: str, feedback: list[FeedbackItem], asked: list[str], mode: str) -> Optional[Question]:
    llm = get_llm()
    if llm.available:
        audience = prompts.MODE_FOCUS.get(mode, prompts.MODE_FOCUS["presentation"])
        try:
            data = await llm.generate_json(
                prompts.QUESTION_SYSTEM_TMPL.format(audience=audience),
                prompts.question_prompt(slide_payload(slide), transcript[-2500:], weak_points(slide, feedback, transcript), asked, mode),
                max_tokens=300, temperature=0.5,
            )
            q = normalize(str(data.get("question", "")))
            if 8 <= len(q) <= 300 and not _is_repeat(q, asked):
                diff = data.get("difficulty") if data.get("difficulty") in ("easy", "medium", "hard") else "medium"
                return Question(slide_number=slide.slide_number, question=q, reason=normalize(str(data.get("reason", "")))[:300],
                                difficulty=diff, source="ai")
        except LLMError as exc:
            log.info("question generation fell back to rules: %s", exc)
    return rule_question(slide, transcript, feedback, asked, mode)


def number_phrases(text: str) -> list[str]:
    """'600 images', '38 FPS', '13 percent' — numbers with the noun that follows them, best candidates first."""
    toks = re.findall(r"[\w.%,$-]+", text or "")
    out: list[tuple[int, str]] = []
    for i, t in enumerate(toks):
        if not re.match(r"^\$?\d[\d,.]*%?$", t):
            continue
        parts, j = [t.rstrip(".,")], i + 1
        if j < len(toks) and toks[j].lower() in ("percent", "%", "x", "times"):
            parts.append(toks[j])
            j += 1
        nouns = []
        while j < len(toks) and len(nouns) < 2 and toks[j].lower() not in STOPWORDS and not toks[j][0].isdigit():
            nouns.append(toks[j].rstrip(".,"))
            j += 1
        out.append((len(nouns), " ".join(parts + nouns)))
    out.sort(key=lambda x: -x[0])
    return [p for _, p in out]


def _rule_follow_up(question: Question, answer: str) -> str:
    """Build a deeper follow up from something concrete in the answer: a number, then a named method."""
    q_low = question.question.lower()
    q_numbers = set(numbers_in(question.question))
    for phrase in number_phrases(answer):
        if phrase.split()[0] not in q_numbers and phrase.lower() not in q_low:
            if question.depth == 0:
                return f"You mentioned {phrase}. How did you arrive at that, and is it enough to trust the result?"
            return f"If {phrase} changed significantly in a real deployment, what would break first and how would you notice?"
    for term in tech_terms(answer):
        if term.lower() not in q_low:
            return (f"You brought up {term}. What trade-off did you accept by using it?" if question.depth == 0
                    else f"How would you prove {term} is the bottleneck or the strength here? What experiment would you run?")
    if question.depth == 0:
        return "Can you give one concrete number or example that backs up that answer?"
    return "What is the biggest limitation of that approach, and how would you address it next?"


def rule_evaluate(question: Question, answer: str) -> tuple[AnswerEvaluation, Optional[Question]]:
    toks = words(answer)
    if len(toks) < 3:
        return AnswerEvaluation(score=0, addressed_question=False, gaps=["No answer was captured."],
                                summary="No meaningful answer was captured.", source="rule"), None
    q_stems, a_stems = stems(question.question), stems(answer)
    overlap = len(q_stems & a_stems) / max(len(q_stems), 1)
    has_reason = bool(set(toks) & REASONING_MARKERS)
    has_numbers = bool(numbers_in(answer))
    score = 2 + (overlap >= 0.25) + has_reason + (has_numbers or len(toks) >= 40)
    strengths, gaps = [], []
    (strengths if has_reason else gaps).append("Gave reasoning" if has_reason else "No explicit reasoning (because / trade-off / evidence)")
    (strengths if has_numbers else gaps).append("Used concrete numbers" if has_numbers else "No concrete numbers or measurements")
    if overlap < 0.25:
        gaps.append("Answer shares few terms with the question; it may not address it directly")
    ev = AnswerEvaluation(score=min(score, 5), addressed_question=overlap >= 0.25, strengths=strengths, gaps=gaps,
                          summary=f"Heuristic estimate: {len(toks)} words, reasoning {'present' if has_reason else 'absent'}, "
                                  f"numbers {'present' if has_numbers else 'absent'}.", source="rule")
    follow = None
    if question.depth < 2:
        follow = Question(slide_number=question.slide_number, question=_rule_follow_up(question, answer),
                          reason="Follow up on your previous answer.", difficulty="hard" if question.depth else "medium",
                          depth=question.depth + 1, parent_id=question.id, source="rule")
    return ev, follow


async def evaluate_answer(question: Question, answer: str, slide: SlideContext, mode: str, chain: list[Question]) -> tuple[AnswerEvaluation, Optional[Question]]:
    if len(words(answer)) < 3:
        return rule_evaluate(question, answer)
    llm = get_llm()
    if not llm.available:
        return rule_evaluate(question, answer)
    audience = prompts.MODE_FOCUS.get(mode, prompts.MODE_FOCUS["presentation"])
    history = [{"q": q.question, "a": q.answer_text[:400]} for q in chain]
    try:
        data = await llm.generate_json(
            prompts.EVAL_SYSTEM_TMPL.format(audience=audience),
            prompts.eval_prompt(question.question, answer[-3000:], slide_payload(slide), question.depth, mode, history),
            max_tokens=600,
        )
    except LLMError as exc:
        log.info("answer evaluation fell back to rules: %s", exc)
        return rule_evaluate(question, answer)
    try:
        score = int(data.get("score"))
        score = max(0, min(5, score))
    except (TypeError, ValueError):
        score = None
    addressed = data.get("addressed_question") if isinstance(data.get("addressed_question"), bool) else None
    ev = AnswerEvaluation(
        score=score, addressed_question=addressed,
        strengths=[normalize(s)[:160] for s in (data.get("strengths") or []) if isinstance(s, str)][:3],
        gaps=[normalize(s)[:160] for s in (data.get("gaps") or []) if isinstance(s, str)][:3],
        summary=normalize(str(data.get("summary", "")))[:300], source="ai",
    )
    follow = None
    fu = data.get("follow_up")
    if question.depth < 3 and isinstance(fu, dict):
        text = normalize(str(fu.get("question", "")))
        if 8 <= len(text) <= 300 and not _is_repeat(text, [q.question for q in chain] + [question.question]):
            diff = fu.get("difficulty") if fu.get("difficulty") in ("easy", "medium", "hard") else "hard"
            follow = Question(slide_number=question.slide_number, question=text, reason=normalize(str(fu.get("reason", "")))[:300],
                              difficulty=diff, depth=question.depth + 1, parent_id=question.id, source="ai")
    return ev, follow
