"""Context Agent: builds compact, token-efficient slide payloads and (optionally) enriches slides with a vision model."""
from __future__ import annotations

import logging
from pathlib import Path

from ..analysis.text_utils import normalize
from ..models import SlideContext, VisualElement
from . import prompts
from .llm import LLMError, get_llm

log = logging.getLogger("pitchmirror.agents.context")


def slide_payload(slide: SlideContext, max_chars: int = 1800) -> dict:
    text, used = [], 0
    for line in slide.text:
        if used + len(line) > max_chars:
            break
        text.append(line)
        used += len(line)
    payload = {
        "slide_number": slide.slide_number,
        "title": slide.title,
        "text": text,
        "key_concepts": slide.key_concepts,
        "important_elements": slide.important_elements,
        "visual_elements": [
            {"kind": v.kind, "description": v.description, **({"data": v.data_summary} if v.data_summary else {})}
            for v in slide.visual_elements
        ],
    }
    if slide.visual_description:
        payload["visual_description"] = slide.visual_description
    if slide.notes:
        payload["speaker_notes"] = slide.notes[:400]
    if slide.is_empty:
        payload["note"] = "This slide has no extractable text or visuals."
    return payload


def slide_corpus(slide: SlideContext) -> str:
    """All grounded text for a slide, used to verify quotes returned by the model."""
    parts = [slide.title, *slide.text, slide.visual_description, slide.notes, *slide.key_concepts, *slide.important_elements]
    for v in slide.visual_elements:
        parts += [v.description, v.data_summary]
    return "\n".join(p for p in parts if p)


def _clean_list(values, limit: int, max_len: int = 160) -> list[str]:
    out = []
    if not isinstance(values, list):
        return out
    for v in values:
        if isinstance(v, str) and v.strip():
            out.append(normalize(v)[:max_len])
        if len(out) >= limit:
            break
    return out


async def enrich_slide(slide: SlideContext, image_path: Path) -> SlideContext:
    """Ask the vision model what the slide shows. Returns a new SlideContext (original untouched on failure)."""
    llm = get_llm()
    if not llm.available or not llm.supports_vision:
        return slide
    try:
        image = image_path.read_bytes()
    except OSError:
        return slide
    try:
        data = await llm.generate_json(
            prompts.ENRICH_SYSTEM, prompts.enrich_prompt(slide_payload(slide)), images=[image], max_tokens=900, timeout=30
        )
    except LLMError as exc:
        log.info("slide %s enrichment failed: %s", slide.slide_number, exc)
        return slide

    s = slide.model_copy(deep=True)
    desc = data.get("visual_description")
    if isinstance(desc, str):
        s.visual_description = normalize(desc)[:600]

    vision_visuals: list[VisualElement] = []
    for v in data.get("visual_elements") or []:
        if not isinstance(v, dict):
            continue
        kind = str(v.get("kind", "")).lower()
        if kind not in ("chart", "table", "diagram", "image"):
            continue
        d = v.get("description")
        if not isinstance(d, str) or not d.strip():
            continue
        vision_visuals.append(VisualElement(kind=kind, description=normalize(d)[:300], source="vision",
                                            data_summary=normalize(str(v.get("data_summary") or ""))[:500]))
    exact = [v for v in s.visual_elements if v.source == "pptx"]
    detected_tables = [v for v in s.visual_elements if v.source == "detected" and v.kind == "table"]
    if vision_visuals:
        exact_kinds = {v.kind for v in exact}
        merged = exact + [v for v in vision_visuals if v.kind not in exact_kinds]
        # keep exact table cell data from PDF extraction alongside the vision description
        for t in detected_tables:
            if not any(v.kind == "table" and v.data_summary for v in merged):
                merged.append(t)
        s.visual_elements = merged[:5]
    elif isinstance(data.get("visual_elements"), list):
        # the vision model saw no meaningful visuals: drop pixel-heuristic detections (usually decoration)
        s.visual_elements = exact + detected_tables

    kc = _clean_list(data.get("key_concepts"), 6, 80)
    if kc:
        s.key_concepts = (kc + [c for c in s.key_concepts if c.lower() not in {k.lower() for k in kc}])[:8]
    ie = _clean_list(data.get("important_elements"), 5)
    if ie:
        s.important_elements = ie
    s.enriched = True
    return s
