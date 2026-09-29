"""Deterministic delivery metrics. No LLM involved: every number here is measured, not inferred."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from ..models import PauseEvent, TranscriptSegment
from .text_utils import STOPWORDS, ngrams, words

SINGLE_FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}
PHRASE_FILLERS = ["you know", "i mean", "sort of", "kind of", "basically", "actually", "literally", "so yeah"]
PAUSE_MIN_S = 1.5
LONG_PAUSE_S = 4.0
PACE_SLOW = 110
PACE_FAST = 170
RUSHED_WPM = 190

STT_FILLER_CAVEAT = (
    "Speech recognisers often drop 'um'/'uh', so filler counts are a lower bound of what was actually said."
)


def count_fillers(text: str) -> Counter:
    c: Counter = Counter()
    toks = words(text)
    for t in toks:
        if t in SINGLE_FILLERS:
            c[t] += 1
    low = " " + " ".join(toks) + " "
    for ph in PHRASE_FILLERS:
        n = len(re.findall(rf"(?<= ){re.escape(ph)}(?= )", low))
        if n:
            c[ph] += n
    return c


def repetitions(text: str) -> dict:
    toks = words(text)
    stutters = Counter(
        toks[i] for i in range(1, len(toks)) if toks[i] == toks[i - 1] and toks[i] not in SINGLE_FILLERS
    )
    phrase_counts = Counter(
        g for g in ngrams(toks, 3)
        if sum(1 for w in g if w not in STOPWORDS) >= 2
    )
    repeated = [{"phrase": " ".join(g), "count": n} for g, n in phrase_counts.most_common(8) if n >= 3]
    return {
        "immediate_repeats": [{"word": w, "count": n} for w, n in stutters.most_common(8)],
        "repeated_phrases": repeated,
    }


def _speech(segments: Iterable[TranscriptSegment]) -> list[TranscriptSegment]:
    """Spoken presentation segments only. Typed text is content, not delivery, so it is excluded here."""
    return [s for s in segments if s.kind == "speech" and s.source != "typed"]


def compute_delivery(
    segments: list[TranscriptSegment],
    pauses: list[PauseEvent],
    voiced_s_by_slide: dict[int, float],
    time_on_slide_s: dict[int, float],
    elapsed_s: float,
) -> dict:
    speech = _speech(segments)
    text = " ".join(s.text for s in speech)
    total_words = len(text.split())

    voiced_total = sum(voiced_s_by_slide.values())
    seg_total = sum(max(0.0, s.t_end - s.t_start) for s in speech)
    if voiced_total >= 3:
        speaking_s, speaking_source = voiced_total, "microphone voice activity"
    elif seg_total >= 3:
        speaking_s, speaking_source = seg_total, "transcript segment timing"
    else:
        speaking_s, speaking_source = 0.0, "not measured"
    wpm = round(total_words / (speaking_s / 60), 1) if speaking_s >= 5 and total_words >= 10 else None

    fillers = count_fillers(text)
    filler_total = sum(fillers.values())

    real_pauses = [p for p in pauses if p.duration_s >= PAUSE_MIN_S]
    long_pauses = [p for p in real_pauses if p.duration_s >= LONG_PAUSE_S]

    rushed = []
    for s in speech:
        dur = s.t_end - s.t_start
        n = len(s.text.split())
        if dur >= 4 and n >= 12:
            seg_wpm = n / dur * 60
            if seg_wpm >= RUSHED_WPM:
                rushed.append({"slide_number": s.slide_number, "wpm": round(seg_wpm), "excerpt": " ".join(s.text.split()[:12]) + "…"})

    per_slide: dict[int, dict] = {}
    for s in speech:
        d = per_slide.setdefault(s.slide_number, {"words": 0, "fillers": 0})
        d["words"] += len(s.text.split())
        d["fillers"] += sum(count_fillers(s.text).values())
    for n, d in per_slide.items():
        d["time_on_slide_s"] = round(time_on_slide_s.get(n, 0.0), 1)
        v = voiced_s_by_slide.get(n, 0.0)
        d["voiced_s"] = round(v, 1)
        d["wpm"] = round(d["words"] / (v / 60), 1) if v >= 5 and d["words"] >= 10 else None
    for n, t in time_on_slide_s.items():
        per_slide.setdefault(n, {"words": 0, "fillers": 0, "time_on_slide_s": round(t, 1), "voiced_s": 0.0, "wpm": None})

    return {
        "raw": {
            "elapsed_s": round(elapsed_s, 1),
            "total_words": total_words,
            "speaking_time_s": round(speaking_s, 1),
            "speaking_time_source": speaking_source,
            "wpm": wpm,
            "filler_total": filler_total,
            "fillers": dict(fillers.most_common()),
            "fillers_per_100_words": round(filler_total / total_words * 100, 1) if total_words else None,
            "pause_count": len(real_pauses),
            "long_pause_count": len(long_pauses),
            "longest_pause_s": round(max((p.duration_s for p in real_pauses), default=0.0), 1),
            "pauses_measured": bool(voiced_s_by_slide) or bool(pauses),
            "repetition": repetitions(text),
            "rushed_segments": rushed[:6],
            "per_slide": {str(k): v for k, v in sorted(per_slide.items())},
        },
        "caveats": [STT_FILLER_CAVEAT]
        + ([f"{typed} typed segment(s) were excluded from delivery metrics."] if (typed := sum(1 for s in segments if s.source == "typed")) else [])
        + (
            [] if voiced_s_by_slide or pauses else ["Pauses were not measured (no microphone voice activity data)."]
        ),
    }


def interpret_delivery(raw: dict) -> list[dict]:
    """Rule based interpretation of the raw metrics. Each item cites the measurement it came from."""
    out = []
    wpm = raw.get("wpm")
    if wpm is not None:
        if wpm > PACE_FAST:
            out.append({"kind": "improvement", "text": f"Pace is fast at {wpm} words/min (comfortable range {PACE_SLOW}–{PACE_FAST}).",
                        "suggestion": "Slow down around key numbers and diagrams; pause after each main claim."})
        elif wpm < PACE_SLOW:
            out.append({"kind": "improvement", "text": f"Pace is slow at {wpm} words/min (comfortable range {PACE_SLOW}–{PACE_FAST}).",
                        "suggestion": "Tighten transitions and avoid long gaps between sentences."})
        else:
            out.append({"kind": "positive", "text": f"Pace is in a comfortable range at {wpm} words/min.", "suggestion": ""})
    rate = raw.get("fillers_per_100_words")
    if rate is not None and raw.get("total_words", 0) >= 30:
        if rate >= 4:
            top = ", ".join(f"'{k}' ×{v}" for k, v in list(raw["fillers"].items())[:3])
            out.append({"kind": "improvement", "text": f"{rate} fillers per 100 words ({top}).",
                        "suggestion": "Replace fillers with a silent pause; it reads as confidence."})
        elif rate <= 1.5:
            out.append({"kind": "positive", "text": f"Low filler usage ({rate} per 100 words detected).", "suggestion": ""})
    if raw.get("long_pause_count"):
        out.append({"kind": "improvement",
                    "text": f"{raw['long_pause_count']} long pause(s) of {LONG_PAUSE_S:.0f}s or more (longest {raw['longest_pause_s']}s).",
                    "suggestion": "Long silences mid explanation suggest the flow is not rehearsed; practise the transition into that slide."})
    reps = raw.get("repetition", {}).get("repeated_phrases", [])
    if reps:
        out.append({"kind": "improvement", "text": "Repeated phrases: " + ", ".join(f"'{r['phrase']}' ×{r['count']}" for r in reps[:3]),
                    "suggestion": "Vary wording or cut the repeated phrase."})
    if raw.get("rushed_segments"):
        slides = sorted({r["slide_number"] for r in raw["rushed_segments"]})
        out.append({"kind": "improvement", "text": f"Rushed sections (≥{RUSHED_WPM} words/min) on slide(s) {', '.join(map(str, slides))}.",
                    "suggestion": "These are usually the parts you are least comfortable with. Rehearse them slowly."})
    return out
