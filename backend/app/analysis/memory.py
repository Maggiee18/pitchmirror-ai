"""Presentation memory: remembers what the presenter said on earlier slides and flags POTENTIAL cross-slide
inconsistencies (e.g. "we use MongoDB" on slide 3, "our data is in PostgreSQL" on slide 8). Deterministic, cheap enough
to run live, and always phrased as a question to clarify, never as an error."""
from __future__ import annotations

import re

from ..models import FeedbackItem, SlideContext
from .tech_comm import sentences_from
from .text_utils import number_value, numbers_in, stems

CATEGORIES = {
    "database": ["mongodb", "postgresql", "postgres", "mysql", "sqlite", "firebase", "firestore", "dynamodb", "cassandra",
                 "oracle", "mariadb", "supabase", "neo4j"],
    "frontend framework": ["react", "angular", "vue", "svelte", "next.js", "nextjs", "flutter", "react native"],
    "backend framework": ["fastapi", "flask", "django", "express", "spring boot", "laravel", "ruby on rails", "asp.net"],
    "cloud provider": ["aws", "azure", "gcp", "google cloud", "heroku", "digitalocean"],
    "object detector": ["yolov5", "yolov7", "yolov8", "yolov9", "yolov10", "yolov11", "faster r-cnn", "retinanet", "detr", "ssd"],
    "programming language": ["python", "java", "javascript", "typescript", "golang", "rust", "kotlin", "c++", "c#"],
}
ALIASES = {"postgres": "postgresql", "nextjs": "next.js"}
PRETTY = {"mongodb": "MongoDB", "postgresql": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite", "dynamodb": "DynamoDB",
          "fastapi": "FastAPI", "aws": "AWS", "gcp": "GCP", "yolov5": "YOLOv5", "yolov8": "YOLOv8", "yolov7": "YOLOv7",
          "react": "React", "django": "Django", "flask": "Flask", "firebase": "Firebase", "azure": "Azure", "python": "Python",
          "java": "Java", "javascript": "JavaScript", "typescript": "TypeScript"}
USE_CUE = re.compile(r"\b(use|used|using|uses|built|build|runs?|running|powered|based|implemented|store|stores|stored|keep|keeps|kept|save|saves|saved|persist|"
                     r"chose|choose|selected|written|deployed|hosted|trained|switched|moved|migrated|with)\b", re.I)
METRICS = ["accuracy", "precision", "recall", "f1", "map", "latency", "fps", "users", "revenue", "error rate", "mae"]


def _norm_term(t: str) -> str:
    return ALIASES.get(t, t)


def statements_from(slide_number: int, text: str) -> list[dict]:
    out = []
    for sent in sentences_from([text]):
        low = " " + re.sub(r"[.,;:!?](?=\s|$)", " ", re.sub(r"[^\w\s.+#-]", " ", sent.lower())) + " "
        for cat, terms in CATEGORIES.items():
            for t in terms:
                if f" {t} " in low and USE_CUE.search(low):
                    out.append({"slide_number": slide_number, "kind": "tech", "category": cat, "value": _norm_term(t),
                                "sentence": sent[:240]})
        toks = low.split()
        for i, w in enumerate(toks):
            for m in METRICS:
                if w.startswith(m.split()[0]):
                    # nearest number within 8 words of the metric name
                    cands = sorted(range(max(0, i - 8), min(len(toks), i + 9)), key=lambda j: abs(j - i))
                    for j in cands:
                        tok = toks[j]
                        if any(ch.isdigit() for ch in tok):
                            v = number_value(tok)
                            if v is not None and not (1900 <= v <= 2100 and float(v).is_integer()):
                                out.append({"slide_number": slide_number, "kind": "metric", "category": m, "value": v,
                                            "sentence": sent[:240], "stems": sorted(stems(sent) - {m})})
                                break
    return out


def _pretty(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return PRETTY.get(v, v)


def find_inconsistencies(statements: list[dict], slides: list[SlideContext]) -> list[dict]:
    deck_text = " ".join(" ".join([s.title, *s.text]) for s in slides).lower()
    found, keys = [], set()
    for i, a in enumerate(statements):
        for b in statements[i + 1:]:
            if a["slide_number"] == b["slide_number"] or a["kind"] != b["kind"] or a["category"] != b["category"]:
                continue
            if a["value"] == b["value"]:
                continue
            if a["kind"] == "tech":
                # the later sentence mentioning both, or both on the slides, means both are genuinely used
                if a["value"] in b["sentence"].lower() or b["value"] in a["sentence"].lower():
                    continue
                if a["value"] in deck_text and b["value"] in deck_text:
                    continue
            else:
                va, vb = a["value"], b["value"]
                if abs(va - vb) <= 0.05 * max(abs(va), abs(vb), 1):
                    continue
                if len(set(a["stems"]) & set(b["stems"])) < 2:
                    continue  # probably about different things (different model, dataset...)
            key = (a["category"], str(a["value"]), str(b["value"]))
            if key in keys:
                continue
            keys.add(key)
            found.append({
                "category": a["category"], "earlier": {"slide_number": a["slide_number"], "value": a["value"], "sentence": a["sentence"]},
                "later": {"slide_number": b["slide_number"], "value": b["value"], "sentence": b["sentence"]},
                "message": f"Potential inconsistency ({a['category']}): on slide {a['slide_number']} you said "
                           f"{_pretty(a['value'])}, on slide {b['slide_number']} {_pretty(b['value'])}. "
                           "Please clarify whether both are true or one needs correcting.",
            })
    return found[:8]


def memory_feedback(inconsistencies: list[dict], slide_n: int) -> list[FeedbackItem]:
    items = []
    for inc in inconsistencies:
        if inc["later"]["slide_number"] != slide_n:
            continue
        items.append(FeedbackItem(
            slide_number=slide_n, kind="warning", category="cross_slide_consistency", severity="medium",
            observation=inc["message"],
            evidence_speech=inc["later"]["sentence"][:300],
            explanation=f"Earlier (slide {inc['earlier']['slide_number']}): “{inc['earlier']['sentence'][:160]}”",
            suggestion="If both are used, say what each one does. If not, correct the one that is wrong.",
            source="rule",
        ))
    return items
