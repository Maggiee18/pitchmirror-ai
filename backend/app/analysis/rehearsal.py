"""Weakest slide detection and rehearsal comparison. Deterministic and evidence based: every point added to a
slide's weakness total comes with the reason and the evidence, and the weights are shown in the report."""
from __future__ import annotations

from .delivery import count_fillers

ISSUE_CATS = ("slide_speech_consistency", "unsupported_claim", "off_topic", "reading_slide", "cross_slide_consistency")
WEIGHTS = {
    "missed key point": 1, "visual not explained": 2, "visual partly explained": 1, "slide/speech mismatch": 3,
    "other content issue": 2, "earlier vs now inconsistency": 2, "claim without evidence": 1, "high filler rate": 1,
    "low eye contact": 1, "weak answer on this slide": 2,
}


def slide_metrics(session, n: int) -> dict:
    """Per-slide numbers the system can actually measure. None = not measurable for this slide."""
    speech = [s for s in session.segments if s.kind == "speech" and s.slide_number == n]
    text = " ".join(s.text for s in speech)
    words = len(text.split())
    st = session.analysis.get(n)
    coverage = None
    visual = None
    if st:
        total = len(st.covered_concepts) + len(st.missed_concepts)
        if total:
            coverage = round(len(st.covered_concepts) / total * 100)
        known = [v for v in st.visuals if v.explained != "unknown"]
        if known:
            visual = round(sum({"yes": 1, "partial": 0.5, "no": 0}[v.explained] for v in known) / len(known) * 100)
    issues = sum(1 for f in session.feedback if f.slide_number == n and f.category in ISSUE_CATS)
    spoken = [s for s in speech if s.source != "typed"]
    spoken_words = len(" ".join(s.text for s in spoken).split())
    fillers = (round(sum(sum(count_fillers(s.text).values()) for s in spoken) / spoken_words * 100, 1)
               if spoken_words >= 20 else None)
    voiced = session.voiced_s.get(n, 0.0)
    wpm = round(spoken_words / (voiced / 60)) if voiced >= 5 and spoken_words >= 10 else None
    p = session.presence.per_slide.get(n)
    eye = p.summary()["eye_contact_pct"] if p else None
    return {"words": words, "key_point_coverage": coverage, "visual_explanation": visual, "content_issues": issues,
            "fillers_per_100_words": fillers, "wpm": wpm, "eye_contact_pct": eye}


METRIC_INFO = {  # label, higher_is_better
    "key_point_coverage": ("Key point coverage %", True),
    "visual_explanation": ("Visual explanation %", True),
    "content_issues": ("Content issues flagged", False),
    "fillers_per_100_words": ("Fillers per 100 words", False),
    "wpm": ("Pace (words/min)", None),
    "eye_contact_pct": ("Eye contact %", True),
    "words": ("Words spoken", None),
}


def compare_metrics(before: dict, after: dict) -> list[dict]:
    rows = []
    for k, (label, higher_better) in METRIC_INFO.items():
        b, a = before.get(k), after.get(k)
        if b is None or a is None:
            continue
        better = None if higher_better is None or a == b else (a > b) == higher_better
        rows.append({"metric": k, "label": label, "before": b, "after": a, "improved": better})
    return rows


def weakest_slide(session, claims: list[dict], qa: list[dict]) -> dict | None:
    presented = [s.slide_number for s in session.slides if session.words_on_slide(s.slide_number) >= 12]
    if not presented:
        return None
    ranking = []
    for n in presented:
        reasons: list[dict] = []

        def add(kind: str, detail: str, times: int = 1):
            reasons.append({"reason": kind, "detail": detail, "points": WEIGHTS[kind] * times})

        st = session.analysis.get(n)
        if st and st.missed_concepts:
            k = min(3, len(st.missed_concepts))
            add("missed key point", f"{len(st.missed_concepts)} key point(s) not explained: " + "; ".join(st.missed_concepts[:2]), k)
        for v in (st.visuals if st else []):
            if v.explained == "no":
                add("visual not explained", v.element[:100])
            elif v.explained == "partial":
                add("visual partly explained", v.element[:100])
        for f in session.feedback:
            if f.slide_number != n:
                continue
            if f.category == "slide_speech_consistency":
                add("slide/speech mismatch", f.observation[:140])
            elif f.category in ("unsupported_claim", "off_topic", "reading_slide"):
                add("other content issue", f.observation[:140])
            elif f.category == "cross_slide_consistency":
                add("earlier vs now inconsistency", f.observation[:140])
        unsupported = [c for c in claims if c["slide_number"] == n and c.get("needs_evidence")]
        if unsupported:
            add("claim without evidence", "; ".join(c["claim"][:80] for c in unsupported[:2]), min(2, len(unsupported)))
        m = slide_metrics(session, n)
        if m["fillers_per_100_words"] is not None and m["fillers_per_100_words"] >= 5:
            add("high filler rate", f"{m['fillers_per_100_words']} fillers per 100 words")
        if m["eye_contact_pct"] is not None and m["eye_contact_pct"] < 40:
            add("low eye contact", f"eye contact {m['eye_contact_pct']}%")
        for q in qa:
            if q["slide_number"] == n and q.get("weak"):
                add("weak answer on this slide", q["question"][:120])
        total = sum(r["points"] for r in reasons)
        ranking.append({"slide_number": n, "title": session.slide(n).title or f"Slide {n}", "points": total,
                        "reasons": reasons, "metrics": m})
    ranking.sort(key=lambda r: -r["points"])
    if ranking[0]["points"] == 0:
        return {"slide_number": None, "message": "No slide stood out as clearly weaker than the others.", "ranking": ranking[:3],
                "weights": WEIGHTS}
    return {**ranking[0], "ranking": ranking[:3], "weights": WEIGHTS}
