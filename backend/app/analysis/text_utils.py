"""Small, dependency-free text helpers used by the deterministic analysis layer."""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = set(
    """
a about above after again against all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here hers
herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves also using used use via per vs etc may might
must shall one two three new get got make made like well really let lets going gonna thing things okay ok yeah
here's there's it's that's we're they're i'm you're let's slide slides next today basically actually
""".split()
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-\+\.#']*[A-Za-z0-9\+#]|[A-Za-z]")
NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\$|₹|€|£)?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:\s?%|x|k|m|b|ms|s)?(?![\w])"
    r"|(?<![\w.])[-+]?(?:\$|₹|€|£)?\d+(?:\.\d+)?(?:\s?%|x|k|m|b|ms|s)?(?![\w])",
    re.IGNORECASE,
)

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


GENERIC_SLIDE_WORDS = {"figure", "fig", "table", "chart", "source", "note", "notes", "example", "overview", "introduction"}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def words(text: str) -> list[str]:
    return [w.lower().strip(".'") for w in WORD_RE.findall(text or "")]


TOKEN_RE = re.compile(r"[A-Za-z0-9$₹€£][A-Za-z0-9\-\+\.#%,']*")


def tokens(text: str) -> list[str]:
    """Words AND numbers, lowercased, trailing punctuation stripped (used where numbers matter)."""
    return [t.lower().rstrip(".,'") for t in TOKEN_RE.findall(text or "")]


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in STOPWORDS and len(w) > 1]


def stem(w: str) -> str:
    """Very light stemmer so 'models'/'model', 'detection'/'detect' match."""
    w = w.lower()
    for suf in ("ations", "ation", "ings", "ing", "ments", "ment", "ies", "es", "ed", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def stems(text: str) -> set[str]:
    return {stem(w) for w in content_words(text)}


def numbers_in(text: str) -> list[str]:
    found = [m.group(0).strip() for m in NUMBER_RE.finditer(text or "")]
    return [n for n in found if n]


def number_value(token: str) -> float | None:
    t = token.lower().replace(",", "").replace("$", "").replace("₹", "").replace("€", "").replace("£", "")
    t = t.replace("%", "").strip()
    mult = 1.0
    for suf, m in (("ms", 1), ("k", 1e3), ("m", 1e6), ("b", 1e9), ("x", 1), ("s", 1)):
        if t.endswith(suf):
            t = t[: -len(suf)]
            mult = m
            break
    try:
        return float(t) * mult
    except ValueError:
        return None


def spoken_numbers(text: str) -> list[float]:
    """Numbers said in speech, including simple number words ('forty two percent')."""
    vals: list[float] = []
    for tok in numbers_in(text):
        v = number_value(tok)
        if v is not None:
            vals.append(v)
    toks = words(text)
    i = 0
    while i < len(toks):
        if toks[i] in NUMBER_WORDS:
            v = NUMBER_WORDS[toks[i]]
            j = i + 1
            if v >= 20 and j < len(toks) and toks[j] in NUMBER_WORDS and NUMBER_WORDS[toks[j]] < 10:
                v += NUMBER_WORDS[toks[j]]
                j += 1
            if j < len(toks) and toks[j] == "hundred":
                v *= 100
                j += 1
            vals.append(float(v))
            i = j
        else:
            i += 1
    return vals


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def shorten(line: str, max_chars: int = 72) -> str:
    line = normalize(line).rstrip(".:;")
    if len(line) <= max_chars:
        return line
    cut = line[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


def key_phrases(lines: list[str], limit: int = 8) -> list[str]:
    """Key points of a slide: each bullet/line is a point; frequent terms fill in when a slide has few lines."""
    phrases: list[str] = []
    for line in lines:
        line = normalize(line).strip("•-–·* ")
        if len(content_words(line)) >= 1:
            phrases.append(shorten(line))
    if len(phrases) < 3:
        counts = Counter(w for line in lines for w in content_words(line) if len(w) > 3 and w not in GENERIC_SLIDE_WORDS)
        for w, n in counts.most_common(limit * 2):
            if not any(w in p.lower() for p in phrases):
                phrases.append(w)
            if len(phrases) >= 4:
                break
    seen: set[str] = set()
    out = []
    for p in phrases:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out[:limit]


TECH_TERM_RE = re.compile(r"\b(?:[A-Za-z]+[0-9][A-Za-z0-9\-]*|[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[a-z]*[A-Z0-9]*)\b")


def tech_terms(text: str) -> list[str]:
    """Named methods/models/tools (YOLOv8, DeepSORT, CSRNet, RTX 3060) — good targets for design questions."""
    out: list[str] = []
    for m in TECH_TERM_RE.findall(text or ""):
        if m.upper() in {"I", "OK", "AI", "PDF"} or m.lower() in STOPWORDS:
            continue
        if m not in out:
            out.append(m)
    return out


def phrase_covered(phrase: str, spoken_stems: set[str]) -> float:
    """Fraction of a phrase's content stems that appear in speech."""
    ps = stems(phrase)
    if not ps:
        return 0.0
    return len(ps & spoken_stems) / len(ps)


def is_covered(phrase: str, spoken_stems: set[str]) -> bool:
    ps = stems(phrase)
    if not ps:
        return False
    hit = len(ps & spoken_stems)
    return hit / len(ps) >= 0.5 or (hit >= 3 and hit / len(ps) >= 0.3)


def fuzzy_contains(haystack: str, needle: str, threshold: float = 0.6) -> bool:
    """True if most content words of `needle` appear in `haystack` (used to verify LLM-quoted evidence)."""
    n = stems(needle)
    if not n:
        return not needle.strip()
    h = stems(haystack)
    return len(n & h) / len(n) >= threshold


def truncate_words(text: str, max_words: int, keep: str = "end") -> str:
    toks = (text or "").split()
    if len(toks) <= max_words:
        return text
    return " ".join(toks[-max_words:]) if keep == "end" else " ".join(toks[:max_words])
