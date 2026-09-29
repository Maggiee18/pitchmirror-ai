"""Presentation ingestion entry point with upload validation."""
from __future__ import annotations

from pathlib import Path

from ..models import SlideContext
from .pdf_parser import parse_pdf
from .pptx_parser import parse_pptx

ALLOWED = {".pdf": b"%PDF", ".pptx": b"PK\x03\x04"}


def detect_kind(filename: str, head: bytes) -> str:
    """Validate by extension AND magic bytes. Returns 'pdf' or 'pptx'; raises ValueError otherwise."""
    ext = Path(filename or "").suffix.lower()
    if ext == ".ppt":
        raise ValueError("Legacy .ppt files are not supported. Save as .pptx or export to PDF.")
    if ext not in ALLOWED:
        raise ValueError("Please upload a PDF or PPTX file.")
    magic = ALLOWED[ext]
    # PDFs may have a few junk bytes before the header
    if ext == ".pdf" and magic not in head[:1024]:
        raise ValueError("This file does not look like a valid PDF.")
    if ext == ".pptx" and not head.startswith(magic):
        raise ValueError("This file does not look like a valid PPTX.")
    return ext[1:]


def parse_presentation(path: Path, kind: str, out_dir: Path, render_width: int, max_slides: int) -> list[SlideContext]:
    if kind == "pdf":
        try:
            return parse_pdf(path, out_dir, render_width, max_slides)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Could not read this PDF. It may be corrupted.") from exc
    return parse_pptx(path, out_dir, render_width, max_slides)
