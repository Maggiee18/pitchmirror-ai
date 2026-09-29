"""Typed data structures shared across ingestion, analysis, realtime and API layers."""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

Mode = Literal["pitch", "viva", "interview", "presentation"]
Severity = Literal["low", "medium", "high"]
FeedbackKind = Literal["positive", "improvement", "warning"]
Difficulty = Literal["easy", "medium", "hard"]
ExplainedStatus = Literal["yes", "partial", "no", "unknown"]

FEEDBACK_CATEGORIES = (
    "slide_speech_consistency",  # contradiction / mismatch between slide and speech
    "missed_concept",  # important slide concept not explained
    "visual_gap",  # chart/diagram/table not explained
    "reading_slide",  # reading slide verbatim instead of explaining
    "off_topic",  # speech unrelated to the slide
    "unsupported_claim",  # claim in speech not backed by slide
    "delivery",  # pace, fillers, pauses, repetition
    "strength",  # something done well
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class VisualElement(BaseModel):
    kind: Literal["chart", "table", "diagram", "image"]
    description: str
    source: Literal["detected", "pptx", "vision"] = "detected"
    data_summary: str = ""  # e.g. chart series / table headers, when extractable


class SlideContext(BaseModel):
    slide_number: int
    title: str = ""
    text: list[str] = Field(default_factory=list)
    notes: str = ""
    visual_elements: list[VisualElement] = Field(default_factory=list)
    visual_description: str = ""
    key_concepts: list[str] = Field(default_factory=list)
    important_elements: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    image_file: str = ""  # server-side file name (never a user supplied path)
    word_count: int = 0
    is_empty: bool = False
    enriched: bool = False  # True once a vision model has described this slide

    def full_text(self) -> str:
        parts = [self.title, *self.text]
        return "\n".join(p for p in parts if p)


class FeedbackItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fb"))
    slide_number: int
    kind: FeedbackKind
    category: str
    severity: Severity = "medium"
    observation: str
    evidence_slide: str = ""
    evidence_speech: str = ""
    explanation: str = ""
    suggestion: str = ""
    source: Literal["rule", "ai"] = "rule"
    created_at: float = Field(default_factory=time.time)


class AnswerEvaluation(BaseModel):
    score: Optional[int] = None  # 0..5, None when it cannot be determined
    addressed_question: Optional[bool] = None
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    summary: str = ""
    source: Literal["rule", "ai"] = "rule"


class Question(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q"))
    slide_number: int
    question: str
    reason: str = ""
    difficulty: Difficulty = "medium"
    depth: int = 0  # 0 = first question, 1+ = follow up
    parent_id: Optional[str] = None
    status: Literal["open", "answered", "skipped"] = "open"
    answer_text: str = ""
    evaluation: Optional[AnswerEvaluation] = None
    source: Literal["rule", "ai"] = "rule"
    created_at: float = Field(default_factory=time.time)


class TranscriptSegment(BaseModel):
    seq: int
    text: str
    slide_number: int
    t_start: float  # seconds since presentation start
    t_end: float
    kind: Literal["speech", "answer"] = "speech"
    question_id: Optional[str] = None
    source: Literal["browser", "whisper", "typed"] = "browser"


class PauseEvent(BaseModel):
    slide_number: int
    t: float
    duration_s: float


class VisualStatus(BaseModel):
    element: str
    explained: ExplainedStatus = "unknown"
    evidence: str = ""


class SlideAnalysisState(BaseModel):
    slide_number: int
    covered_concepts: list[str] = Field(default_factory=list)
    missed_concepts: list[str] = Field(default_factory=list)
    visuals: list[VisualStatus] = Field(default_factory=list)
    reading_ratio: Optional[float] = None
    words_analyzed: int = 0
    source: Literal["rule", "ai", "none"] = "none"
    last_error: str = ""


class SessionPublic(BaseModel):
    """What the frontend needs to render or restore a session."""

    id: str
    filename: str
    mode: Mode
    status: Literal["ready", "live", "ended"]
    slides: list[SlideContext]
    enrichment: dict
    provider: dict
    current_slide: int
    started_at: Optional[float]
    elapsed_s: float
    segments: list[TranscriptSegment]
    feedback: list[FeedbackItem]
    questions: list[Question]
    metrics: dict
    last_seq: int
    has_report: bool
    analysis: dict[int, SlideAnalysisState] = Field(default_factory=dict)
