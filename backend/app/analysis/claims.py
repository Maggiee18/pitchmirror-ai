"""Claim detection (deterministic candidates; the LLM refines classification and challenges in the report).

A claim is only called an "unsupported claim candidate" when code found no support for it on the slides, and it is
always phrased as a candidate, never as a verdict.
"""
from __future__ import annotations

import re

from ..models import SlideContext
from .tech_comm import sentences_from
from .text_utils import fuzzy_contains, is_claim_line, is_contact_line, number_value, numbers_in, tech_terms

USE_CUE = re.compile(r"\b(we use|we used|using|built (with|on|using)|runs? on|powered by|based on|implemented (in|with|using)|"
                     r"stores? (data |it |everything )?in|we chose|we selected|written in|deployed on|trained on)\b", re.I)
SUBJECTIVE = re.compile(r"\b(best|better than|highly|significantly|extremely|very (fast|accurate|efficient|scalable)|robust|"
                        r"scalable|seamless(ly)?|state[- ]of[- ]the[- ]art|cutting[- ]edge|revolutionary|first ever|the only|"
                        r"unique|users? (love|prefer|like)|everyone|always|never fails?|guarantee[sd]?|perfect(ly)?|"
                        r"real[- ]time|lightning|blazing|highly accurate|very accurate)\b", re.I)
OPINION = re.compile(r"\b(i think|we think|i believe|we believe|in my opinion|we feel|probably|i guess)\b", re.I)


def _challenge(sentence: str, ctype: str) -> str:
    low = sentence.lower()
    short = sentence if len(sentence) <= 90 else sentence[:87] + "…"
    if ctype == "quantitative":
        return f"You said “{short}”. Compared with which baseline, and how exactly was it measured?"
    if "scalab" in low:
        return "Which part of your architecture becomes the bottleneck first as load grows, and how would you scale it?"
    if re.search(r"real[- ]time|fast|latency|lightning|blazing", low):
        return "What latency or throughput did you actually measure, and on what hardware?"
    if re.search(r"users? (love|prefer|like)|everyone", low):
        return "How did you measure what users prefer, and with how many users?"
    if re.search(r"robust|never fails?|perfect|guarantee|always", low):
        return "Under which conditions does it fail, and how did you test for them?"
    if re.search(r"best|better than|state[- ]of[- ]the[- ]art|cutting[- ]edge|only|unique|first", low):
        return "Better than what exactly, and by which metric?"
    return "What evidence supports that?"


def detect_claims(speech: list[tuple[int, str]], slides: list[SlideContext]) -> list[dict]:
    """speech: (slide_number, text) for each spoken segment, in order."""
    deck_numbers = {number_value(n) for s in slides for line in [s.title, *s.text] for n in numbers_in(line)}
    deck_numbers |= {number_value(n) for s in slides for v in s.visual_elements for n in numbers_in(v.data_summary)}
    out, seen = [], set()
    for slide_n, text in speech:
        slide = slides[slide_n - 1] if 1 <= slide_n <= len(slides) else None
        corpus = " ".join([slide.title, *slide.text] + [v.data_summary for v in slide.visual_elements]) if slide else ""
        for sent in sentences_from([text]):
            if len(sent.split()) < 4 or is_contact_line(sent):
                continue
            key = sent.lower()[:80]
            if key in seen:
                continue
            ctype = None
            if is_claim_line(sent):
                ctype = "quantitative"
            elif OPINION.search(sent):
                ctype = "opinion"
            elif SUBJECTIVE.search(sent):
                ctype = "subjective"
            elif USE_CUE.search(sent) and tech_terms(sent):
                ctype = "technical"
            if not ctype:
                continue
            seen.add(key)
            on_slide = bool(corpus) and fuzzy_contains(corpus, sent, 0.5)
            nums = [number_value(n) for n in numbers_in(sent)]
            nums = [v for v in nums if v is not None and not (1900 <= v <= 2100 and float(v).is_integer())]
            if ctype == "quantitative":
                supported = on_slide or (bool(nums) and all(v in deck_numbers for v in nums))
                evidence = "numbers appear on your slides" if supported else "these numbers were not found on your slides"
            elif ctype in ("subjective", "opinion"):
                supported = on_slide
                evidence = "stated on the slide" if supported else "no supporting evidence found on the slide"
            else:
                supported = True  # a statement of what you use is not something code can refute
                evidence = "statement of the technology used"
            out.append({
                "slide_number": slide_n, "claim": sent[:300], "type": ctype,
                "needs_evidence": ctype in ("quantitative", "subjective") and not supported,
                "evidence": evidence,
                "challenge": _challenge(sent, ctype) if ctype in ("quantitative", "subjective") else "",
                "source": "rule",
            })
    # most important first: unsupported quantitative, unsupported subjective, the rest
    order = {("quantitative", True): 0, ("subjective", True): 1, ("quantitative", False): 2, ("technical", False): 3,
             ("subjective", False): 4, ("opinion", False): 5, ("opinion", True): 5}
    out.sort(key=lambda c: order.get((c["type"], c["needs_evidence"]), 6))
    return out[:15]
