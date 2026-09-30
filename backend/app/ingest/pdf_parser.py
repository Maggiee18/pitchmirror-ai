"""PDF -> structured slide context using PyMuPDF.

Everything here is deterministic. Visual elements are *detected* (images, vector drawings, tables);
their meaning is refined later by an optional vision model (see agents/slide_understanding.py).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from ..analysis.text_utils import content_words, is_contact_line, key_phrases, normalize, numbers_in
from ..models import SlideContext, VisualElement

log = logging.getLogger("pitchmirror.ingest.pdf")

CHART_HINTS = ("chart", "graph", "plot", "trend", "growth", "accuracy", "loss", "revenue", "users", "%", "vs",
               "comparison", "distribution", "histogram", "curve", "over time", "per month", "monthly")
DIAGRAM_HINTS = ("architecture", "pipeline", "flow", "diagram", "workflow", "system", "module", "layer",
                 "process", "model", "overview")


NUMERIC_ONLY = re.compile(r"^[\s\d.,%$€£₹+\-x]+$")


def clean_bullet(text: str) -> str:
    return normalize(text).lstrip("•●○◦▪■-–—·*> ").strip()


def _line_text(line: dict) -> str:
    return normalize("".join(span.get("text", "") for span in line.get("spans", [])))


def _line_size(line: dict) -> float:
    sizes = [span.get("size", 0) for span in line.get("spans", []) if span.get("text", "").strip()]
    return max(sizes) if sizes else 0.0


def _near_text(lines: list[tuple[pymupdf.Rect, str]], bbox: pymupdf.Rect, margin: float) -> list[str]:
    grown = pymupdf.Rect(bbox.x0 - margin, bbox.y0 - margin, bbox.x1 + margin, bbox.y1 + margin)
    return [t for r, t in lines if grown.intersects(r)]


def _classify(nearby: list[str], default: str) -> str:
    joined = " ".join(nearby).lower()
    n_numbers = len(numbers_in(joined))
    if any(h in joined for h in ("table",)):
        return "table"
    if n_numbers >= 4 or any(h in joined for h in CHART_HINTS[:12]):
        return "chart"
    if any(h in joined for h in DIAGRAM_HINTS):
        return "diagram"
    return default


def parse_page(page: pymupdf.Page, slide_number: int, out_dir: Path, render_width: int) -> SlideContext:
    page_rect = page.rect
    page_area = max(page_rect.width * page_rect.height, 1.0)

    # ---- render image --------------------------------------------------------
    zoom = render_width / max(page_rect.width, 1.0)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    image_name = f"slide_{slide_number:03d}.png"
    pix.save(str(out_dir / image_name))

    # ---- text with font sizes (lines for title detection, blocks for paragraphs) ----
    td = page.get_text("dict")
    lines: list[tuple[pymupdf.Rect, str, float, int]] = []
    for b_idx, block in enumerate(td.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            txt = _line_text(line)
            if txt:
                lines.append((pymupdf.Rect(line["bbox"]), txt, _line_size(line), b_idx))

    title_ids: set[int] = set()
    title = ""
    if lines:
        upper = [l for l in lines if l[0].y0 < page_rect.y0 + page_rect.height * 0.4] or lines
        max_size = max(l[2] for l in upper)
        title_lines = [l for l in upper if l[2] >= max_size - 0.5]
        title = normalize(" ".join(l[1] for l in title_lines))[:200]
        title_ids = {id(l) for l in title_lines}
    rect_lines = [(r, t) for r, t, _, _ in lines if True]

    visuals: list[VisualElement] = []
    visual_regions: list[tuple[pymupdf.Rect, VisualElement]] = []

    # ---- tables ------------------------------------------------------------
    try:
        tabs = page.find_tables()
        for tab in tabs.tables:
            data = tab.extract() or []
            if len(data) < 2:
                continue
            header = [normalize(str(c or "")) for c in data[0]]
            v = VisualElement(
                kind="table",
                description=f"Table with {len(data) - 1} rows; columns: {', '.join(h for h in header if h)}",
                data_summary="; ".join(" | ".join(normalize(str(c or "")) for c in row) for row in data[:8])[:700],
            )
            visuals.append(v)
            visual_regions.append((pymupdf.Rect(tab.bbox), v))
    except Exception as exc:  # table detection is best effort
        log.debug("table detection failed on slide %s: %s", slide_number, exc)
    table_rects = [r for r, v in visual_regions]

    # ---- raster images ------------------------------------------------------
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page_rect
            frac = (r.width * r.height) / page_area
            if frac < 0.04 or frac > 0.97:
                continue  # logos / icons / full-slide backgrounds
            nearby = [t for t in _near_text(rect_lines, r, margin=48) if t != title]
            kind = _classify(nearby, "image")
            caption = next((t for t in nearby if len(content_words(t)) >= 3), "")
            desc = (f"{kind.capitalize()} near the text '{caption[:120]}'" if caption
                    else f"{kind.capitalize()} covering about {int(frac * 100)}% of the slide")
            visuals.append(VisualElement(kind=kind, description=desc))
    except Exception as exc:
        log.debug("image detection failed on slide %s: %s", slide_number, exc)

    # ---- vector drawings (charts / diagrams drawn natively) -----------------
    try:
        drawings = page.get_drawings()
        rects = [pymupdf.Rect(d["rect"]) for d in drawings if d.get("rect")]
        rects = [
            r for r in rects
            if (r.width * r.height) / page_area < 0.9 and not any(t.contains(r) for t in table_rects)
            and (r.width > 2 or r.height > 2)
        ]
        # bullet dots and underlines are tiny; a chart/diagram has many shapes spread over an area
        big = [r for r in rects if r.width * r.height > page_area * 0.002 or r.width > 40 or r.height > 40]
        if len(big) >= 4:
            union = big[0]
            for r in big[1:]:
                union = union | r
            frac = (union.width * union.height) / page_area
            if frac >= 0.08:
                inside = [t for r, t in rect_lines if union.intersects(r) and t != title]
                kind = _classify(inside, "diagram")
                v = VisualElement(
                    kind=kind,
                    description=(f"{kind.capitalize()} of " + ", ".join(inside[:4]))[:160] if inside
                    else f"{kind.capitalize()} covering about {int(frac * 100)}% of the slide",
                    data_summary=("Labels: " + "; ".join(inside[:12]))[:500] if inside else "",
                )
                visuals.append(v)
                visual_regions.append((union, v))
    except Exception as exc:
        log.debug("drawing detection failed on slide %s: %s", slide_number, exc)

    # ---- body paragraphs: group lines per block, drop title + text living inside tables/charts ----
    def inside_visual(r: pymupdf.Rect, t: str) -> bool:
        for vr, v in visual_regions:
            if vr.contains(r) or (v.kind != "diagram" and vr.intersects(r)):
                return True
            # short numeric labels (chart axes) inside a diagram region
            if v.kind == "diagram" and vr.intersects(r) and NUMERIC_ONLY.match(t):
                return True
        return False

    diagram_labels: list[str] = []
    # Paragraphs: lines of one text block, but a change in font size starts a new paragraph
    # (section headings in resumes and reports often share a block with the text around them).
    paragraphs: list[list[str]] = []
    prev_block, prev_size = None, None
    for l in lines:
        r, t, size, b_idx = l
        if id(l) in title_ids:
            continue
        if inside_visual(r, t):
            if any(v.kind == "diagram" and vr.intersects(r) for vr, v in visual_regions) and not NUMERIC_ONLY.match(t):
                diagram_labels.append(t)
            continue
        if b_idx != prev_block or prev_size is None or abs(size - prev_size) > 0.5:
            paragraphs.append([])
        paragraphs[-1].append(t)
        prev_block, prev_size = b_idx, size
    body = [clean_bullet(" ".join(ts)) for ts in paragraphs]
    body = [b for b in body if b]

    all_text = [title, *body]
    word_count = sum(len(t.split()) for t in all_text)
    numbers = []
    for t in all_text:
        if is_contact_line(t):
            continue
        for n in numbers_in(t):
            if n not in numbers:
                numbers.append(n)

    important = [l for l in body if numbers_in(l) and not is_contact_line(l)][:5]
    important += [f"{v.kind}: {v.description}" for v in visuals]

    return SlideContext(
        slide_number=slide_number,
        title=title,
        text=body[:40],
        visual_elements=visuals,
        key_concepts=key_phrases(body + diagram_labels or [title]),
        important_elements=important[:10],
        numbers=numbers[:20],
        image_file=image_name,
        word_count=word_count,
        is_empty=word_count == 0 and not visuals,
    )


def parse_pdf(path: Path, out_dir: Path, render_width: int, max_slides: int) -> list[SlideContext]:
    doc = pymupdf.open(str(path))
    try:
        if doc.needs_pass:
            raise ValueError("This PDF is password protected. Please upload an unlocked copy.")
        if doc.page_count == 0:
            raise ValueError("The PDF has no pages.")
        if doc.page_count > max_slides:
            raise ValueError(f"The deck has {doc.page_count} slides; the limit is {max_slides}.")
        return [parse_page(doc[i], i + 1, out_dir, render_width) for i in range(doc.page_count)]
    finally:
        doc.close()
