"""In-memory session store.

Data separation (privacy):
- Temporary processing data: uploaded file (deleted right after parsing), audio chunks (never written to disk).
- Presentation content: rendered slide PNGs + parsed context, kept under DATA_DIR/<session id>/ until the session
  expires (SESSION_TTL_MINUTES) or the user deletes it.
- Generated feedback / transcript / report: in memory only, lost on server restart.
- There is no persistent user database and no accounts.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

from ..analysis.presence import PresenceTracker
from ..config import get_settings
from ..models import (
    FeedbackItem,
    Mode,
    PauseEvent,
    Question,
    SessionPublic,
    SlideAnalysisState,
    SlideContext,
    TranscriptSegment,
    new_id,
)

log = logging.getLogger("pitchmirror.session")


@dataclass
class Session:
    id: str
    filename: str
    mode: Mode
    slides: list[SlideContext]
    dir: Path
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    status: str = "ready"  # ready | live | ended
    current_slide: int = 1
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    slide_entered_at: Optional[float] = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    pauses: list[PauseEvent] = field(default_factory=list)
    voiced_s: dict[int, float] = field(default_factory=dict)
    time_on_slide: dict[int, float] = field(default_factory=dict)
    feedback: list[FeedbackItem] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    analysis: dict[int, SlideAnalysisState] = field(default_factory=dict)
    words_at_last_analysis: dict[int, int] = field(default_factory=dict)
    last_analysis_at: dict[int, float] = field(default_factory=dict)
    finalized_slides: set[int] = field(default_factory=set)
    enrichment: dict[str, Any] = field(default_factory=lambda: {"status": "pending", "done": 0, "total": 0})
    report: Optional[dict] = None
    report_status: str = "none"  # none | generating | ready | failed
    last_seq: int = 0
    rejected_items: int = 0
    last_auto_question_at: float = 0.0
    last_nudge_at: dict[str, float] = field(default_factory=dict)
    subscribers: set[WebSocket] = field(default_factory=set)
    tasks: set[asyncio.Task] = field(default_factory=set)
    analysis_running: set[int] = field(default_factory=set)
    analysis_pending: dict[int, bool] = field(default_factory=dict)
    question_pending: bool = False
    presence: PresenceTracker = field(default_factory=PresenceTracker)
    rehearsal: Optional[dict] = None  # set when this session re-presents one slide of a finished session

    # ------------------------------------------------------------------ helpers
    def slide(self, n: int) -> Optional[SlideContext]:
        if 1 <= n <= len(self.slides):
            return self.slides[n - 1]
        return None

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.ended_at or time.time()
        return max(0.0, end - self.started_at)

    def speech_text(self, slide_n: int) -> str:
        return " ".join(s.text for s in self.segments if s.kind == "speech" and s.slide_number == slide_n)

    def recent_speech(self, max_words: int = 250) -> str:
        toks = " ".join(s.text for s in self.segments if s.kind == "speech").split()
        return " ".join(toks[-max_words:])

    def words_on_slide(self, slide_n: int) -> int:
        return len(self.speech_text(slide_n).split())

    def open_question(self) -> Optional[Question]:
        return next((q for q in reversed(self.questions) if q.status == "open"), None)

    def question(self, qid: str) -> Optional[Question]:
        return next((q for q in self.questions if q.id == qid), None)

    def question_chain(self, q: Question) -> list[Question]:
        chain, cur = [], q
        while cur.parent_id:
            parent = self.question(cur.parent_id)
            if parent is None:
                break
            chain.insert(0, parent)
            cur = parent
        return chain

    def track(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.exception("background task failed in session %s", self.id, exc_info=task.exception())

    def reset_run(self) -> None:
        """Practice again: keep slides, clear everything the presenter produced."""
        for t in list(self.tasks):
            t.cancel()
        self.status = "ready"
        self.current_slide = 1
        self.started_at = self.ended_at = self.slide_entered_at = None
        self.segments, self.pauses, self.feedback, self.questions = [], [], [], []
        self.voiced_s, self.time_on_slide, self.analysis = {}, {}, {}
        self.words_at_last_analysis, self.last_analysis_at = {}, {}
        self.finalized_slides = set()
        self.report, self.report_status = None, "none"
        self.last_seq = 0
        self.rejected_items = 0
        self.last_auto_question_at = 0.0
        self.last_nudge_at = {}
        self.presence = PresenceTracker()
        self.analysis_running, self.analysis_pending = set(), {}

    def public(self, provider: dict, metrics: dict) -> SessionPublic:
        return SessionPublic(
            id=self.id, filename=self.filename, mode=self.mode, status=self.status, slides=self.slides,
            enrichment=self.enrichment, provider=provider, current_slide=self.current_slide,
            started_at=self.started_at, elapsed_s=round(self.elapsed(), 1), segments=self.segments[-400:],
            feedback=self.feedback, questions=self.questions, metrics=metrics, last_seq=self.last_seq,
            has_report=self.report is not None, analysis=self.analysis, rehearsal=self.rehearsal,
        )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, filename: str, mode: Mode, slides: list[SlideContext], directory: Path, sid: str) -> Session:
        s = Session(id=sid, filename=filename, mode=mode, slides=slides, dir=directory)
        s.enrichment["total"] = len(slides)
        self._sessions[sid] = s
        return s

    @staticmethod
    def new_id() -> str:
        return new_id("s")

    def get(self, sid: str) -> Optional[Session]:
        s = self._sessions.get(sid)
        if s:
            s.last_active = time.time()
        return s

    def delete(self, sid: str) -> bool:
        s = self._sessions.pop(sid, None)
        if not s:
            return False
        for t in list(s.tasks):
            t.cancel()
        shutil.rmtree(s.dir, ignore_errors=True)
        return True

    def cleanup_expired(self) -> int:
        ttl = get_settings().session_ttl_minutes * 60
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_active > ttl and not s.subscribers]
        for sid in expired:
            self.delete(sid)
        return len(expired)

    def __len__(self) -> int:
        return len(self._sessions)


store = SessionStore()
