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
    "cross_slide_consistency",  # potential contradiction with an earlier slide (presentation memory)
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


AnswerAssessment = Literal[
    "strongly_correct", "mostly_correct", "partially_correct", "incorrect", "insufficient_evidence", "unable_to_determine"
]


class AnswerScores(BaseModel):
    """0-100 per dimension; None means it could not be determined (never a guessed number)."""

    correctness: Optional[int] = None
    relevance: Optional[int] = None
    completeness: Optional[int] = None
    technical_depth: Optional[int] = None
    clarity: Optional[int] = None

    def overall(self) -> Optional[int]:
        vals = [v for v in (self.correctness, self.relevance, self.completeness, self.technical_depth, self.clarity) if v is not None]
        return round(sum(vals) / len(vals)) if len(vals) >= 3 else None


class AnswerIntelligence(BaseModel):
    """Answer Intelligence layer: additive to AnswerEvaluation, never replaces it."""

    assessment: AnswerAssessment = "unable_to_determine"
    scores: AnswerScores = Field(default_factory=AnswerScores)
    depth_level: Optional[Literal["surface", "moderate", "strong", "deep"]] = None
    justification: list[str] = Field(default_factory=list)  # which of why/how/trade-offs/evidence/project-specific were present
    what_was_good: list[str] = Field(default_factory=list)
    what_was_missing: list[str] = Field(default_factory=list)
    could_be_stronger: list[str] = Field(default_factory=list)
    better_answer: str = ""
    why_better: list[str] = Field(default_factory=list)
    retry_recommended: bool = False
    scaffold_question: str = ""
    note: str = ""
    source: Literal["rule", "ai"] = "rule"


class AnswerAttempt(BaseModel):
    attempt: int
    answer_text: str
    intel: Optional[AnswerIntelligence] = None
    created_at: float = Field(default_factory=time.time)


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
    attempts: list[AnswerAttempt] = Field(default_factory=list)  # Answer Intelligence per attempt (1 = original answer)


class TranscriptSegment(BaseModel):
    seq: int
    text: str
    slide_number: int
    t_start: float  # seconds since presentation start
    t_end: float
    kind: Literal["speech", "answer"] = "speech"
    question_id: Optional[str] = None
    source: Literal["browser", "whisper", "typed"] = "browser"
    attempt: int = 1  # answer attempt number for "Try Again" (answer segments only)


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
    rehearsal: Optional[dict] = None
