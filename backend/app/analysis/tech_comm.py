"""Technical Communication Intelligence: deterministic extraction and measurement.

Technical language density = technical term occurrences / meaningful words x 100. It is a descriptive number,
not a quality score. Whether terms were explained and whether the density suits the audience is judged by the
LLM when available (agents/intelligence_report.py); the heuristics here are the transparent fallback.
"""
from __future__ import annotations

import re
from collections import Counter

from .text_utils import STOPWORDS, content_words, normalize, tech_terms

# Domain vocabulary that is technical even when written in lower case. Speech recognisers lower-case most words,
# so casing alone (used by tech_terms) is not enough. Multi-word terms are matched before single words.
LEXICON = {
    # AI / ML / data
    "machine learning", "deep learning", "neural network", "neural networks", "convolutional neural network", "transformer",
    "transformers", "attention mechanism", "large language model", "language model", "llm", "llms", "multimodal",
    "embedding", "embeddings", "vector database", "fine-tuning", "fine tuning", "fine-tuned", "inference", "training set",
    "test set", "validation set", "overfitting", "underfitting", "regularization", "dropout", "backpropagation",
    "gradient descent", "learning rate", "loss function", "hyperparameter", "hyperparameters", "epoch", "epochs",
    "batch size", "f1 score", "iou", "map", "mean average precision", "confusion matrix",
    "object detection", "semantic segmentation", "instance segmentation", "classification", "regression", "clustering",
    "tokenizer", "prompt engineering", "retrieval augmented generation", "rag", "agent", "agents", "orchestration",
    "optical flow", "density estimation", "density map", "tracker", "tracking", "bounding box", "bounding boxes",
    "anchor boxes", "non-maximum suppression", "feature extraction", "data augmentation", "annotation", "annotated",
    "ground truth", "benchmark", "baseline", "latency", "throughput", "fps", "gpu", "cpu", "tpu", "cuda", "quantization",
    "pruning", "edge deployment", "computer vision", "nlp", "natural language processing", "speech recognition",
    "speech-to-text", "text-to-speech", "whisper", "ocr", "pipeline", "dataset", "datasets",
    # software / systems
    "api", "apis", "rest api", "endpoint", "endpoints", "websocket", "websockets", "microservice", "microservices",
    "backend", "frontend", "full stack", "database", "schema", "cache", "caching",
    "load balancer", "scalability", "scalable", "horizontal scaling", "container", "containers", "docker", "kubernetes",
    "deployment", "ci/cd", "authentication", "authorization", "encryption", "json", "http", "https", "sql", "nosql",
    "orm", "asynchronous", "async", "concurrency", "queue", "message queue", "event-driven",
    "serverless", "cloud", "sdk", "framework", "algorithm", "algorithms", "time complexity", "recursion",
    "hash map", "data structure", "compiler", "parser", "runtime", "latency budget", "state machine", "circuit breaker",
    "rate limiting", "tensor", "tensors",
    # business / pitch jargon that also needs unpacking for general audiences
    "tam", "sam", "som", "cac", "ltv", "arr", "mrr", "churn", "saas", "b2b", "b2c", "go-to-market", "unit economics",
}
# Words that look technical by shape but aren't worth flagging
IGNORE = {"ok", "ai", "i", "a", "we", "us", "pdf", "ppt", "q&a", "srm", "cse", "b.tech", "btech", "cgpa"}

# Phrases that usually introduce an explanation right after a term
EXPLAIN_CUES = re.compile(
    r"\b(which|that|meaning|means|is a|is an|are|refers to|basically|in other words|i\.e\.?|for example|such as|"
    r"helps|allows|lets|used to|responsible for|so that|in simple terms|think of it as|like a)\b", re.I)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sentences_from(texts: list[str]) -> list[str]:
    out = []
    for t in texts:
        for s in SENTENCE_SPLIT.split(normalize(t)):
            if s.strip():
                out.append(s.strip())
    return out


def _find_terms(sentence: str, display: dict[str, str] | None = None) -> list[str]:
    low = " " + re.sub(r"[^\w\s/+\-.]", " ", sentence.lower()) + " "
    found: list[str] = []
    taken = low
    for term in sorted(LEXICON, key=len, reverse=True):
        pat = f" {term} "
        if pat in taken:
            found.append(term)
            if display is not None:
                m = re.search(re.escape(term), sentence, re.I)
                display.setdefault(term, m.group(0) if m else term)
            taken = taken.replace(pat, " " + "#" * len(term) + " ")
    for t in tech_terms(sentence):
        tl = t.lower()
        if tl in IGNORE or tl in STOPWORDS or any(tl in f for f in found) or len(tl) < 2:
            continue
        found.append(tl)
        if display is not None:
            display.setdefault(tl, t)
    return found


def analyze_terms(speech_segments: list[str]) -> dict:
    """Deterministic measurement over what the presenter actually said."""
    sents = sentences_from(speech_segments)
    meaningful = sum(len(content_words(s)) for s in sents)
    occurrences: Counter = Counter()
    contexts: dict[str, list[tuple[str, str]]] = {}
    display: dict[str, str] = {}
    first_index: dict[str, int] = {}
    for i, s in enumerate(sents):
        for term in _find_terms(s, display):
            occurrences[term] += 1
            contexts.setdefault(term, [])
            if len(contexts[term]) < 3:
                ctx = s if len(s) < 260 else s[:257] + "…"
                nxt = sents[i + 1] if i + 1 < len(sents) else ""
                contexts[term].append((ctx, nxt[:200]))
            first_index.setdefault(term, i)
    total = sum(occurrences.values())
    density = round(total / meaningful * 100, 1) if meaningful >= 20 else None
    terms = []
    for term, n in sorted(occurrences.items(), key=lambda kv: -significance(kv[0], kv[1])):
        terms.append({"term": display.get(term, term), "key": term, "count": n,
                      "contexts": [c + (" " + nx if nx else "") for c, nx in contexts[term]][:2],
                      "explained_heuristic": heuristic_explained(term, contexts[term])})
    return {
        "meaningful_words": meaningful,
        "technical_occurrences": total,
        "unique_terms": len(occurrences),
        "density_pct": density,
        "terms": terms,
        "formula": "technical term occurrences / meaningful (non stopword) words x 100",
    }


def significance(term: str, count: int) -> float:
    """Domain-specific names (acronyms, versions, CamelCase) and multi-word concepts matter more than generic words."""
    shaped = bool(re.search(r"\d|[A-Z]{2,}", term)) or term.upper() == term or term in {t.lower() for t in tech_terms(term)}
    return count + (2 if shaped else 0) + (1 if " " in term else 0)


def heuristic_explained(term: str, contexts: list[tuple[str, str]]) -> str:
    """Rough fallback: an explanation cue after the term in the same sentence, or a next sentence about the term."""
    t = term.lower()
    for sent, nxt in contexts:
        low = sent.lower()
        idx = low.find(t)
        after = low[idx + len(t):] if idx >= 0 else ""
        if EXPLAIN_CUES.search(after[:140]) and len(after.split()) >= 6:
            return "yes"
        nl = nxt.lower()
        if nl and (nl.startswith(t) or nl.split(" ", 1)[0] in ("it", "this", "that")) and EXPLAIN_CUES.search(nl[:160]):
            return "yes"
    return "no"


# Rule of thumb bands per mode, used ONLY when the AI judgement is unavailable (clearly labelled in the report).
DENSITY_BANDS = {"viva": (30, 45), "interview": (30, 45), "pitch": (12, 22), "presentation": (15, 25)}


def heuristic_audience(mode: str, density: float | None, unexplained_ratio: float) -> dict:
    if density is None:
        return {"level": None, "rationale": "Not enough speech to measure technical density.", "source": "rule"}
    ok, hard = DENSITY_BANDS.get(mode, (15, 25))
    if density <= ok and unexplained_ratio <= 0.5:
        level = "appropriate"
    elif density <= hard or unexplained_ratio <= 0.3:
        level = "consider_simplifying"
    else:
        level = "likely_difficult"
    return {"level": level, "source": "rule",
            "rationale": f"Rule of thumb for {mode}: up to {ok}% is usually comfortable, above {hard}% gets hard to follow; "
                         f"{int(unexplained_ratio * 100)}% of key terms looked unexplained."}
