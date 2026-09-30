"""Real-time session engine.

The WebSocket loop only records events and schedules work; every LLM call runs in a background task with a
timeout, so a slow or failing model never blocks the presentation. Results are pushed to all connected clients.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import ValidationError

from ..agents import audience
from ..agents.llm import get_llm
from ..agents.slide_analyzer import analyze_slide
from ..agents.slide_context import enrich_slide
from ..analysis.delivery import compute_delivery, count_fillers
from ..analysis.presence import EYE_CONTACT_LOW, LONG_LOOK_AWAY_S
from ..analysis.presence import clean_sample as clean_presence
from ..analysis.text_utils import stems
from ..config import get_settings
from ..models import FeedbackItem, PauseEvent, Question, TranscriptSegment
from .session import Session

log = logging.getLogger("pitchmirror.engine")

MAX_SEGMENT_CHARS = 2000
AUTO_QUESTION_MODES = {"viva", "interview", "pitch", "presentation"}


# ----------------------------------------------------------------------------- broadcasting
async def broadcast(session: Session, event: dict) -> None:
    dead = []
    for ws in list(session.subscribers):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        session.subscribers.discard(ws)


def live_metrics(session: Session) -> dict:
    ts = dict(session.time_on_slide)
    if session.status == "live" and session.slide_entered_at:
        ts[session.current_slide] = ts.get(session.current_slide, 0.0) + (time.time() - session.slide_entered_at)
    d = compute_delivery(session.segments, session.pauses, session.voiced_s, ts, session.elapsed())
    raw = d["raw"]
    return {
        "elapsed_s": raw["elapsed_s"], "total_words": raw["total_words"], "wpm": raw["wpm"],
        "filler_total": raw["filler_total"], "fillers": raw["fillers"], "pause_count": raw["pause_count"],
        "long_pause_count": raw["long_pause_count"], "speaking_time_s": raw["speaking_time_s"],
        "speaking_time_source": raw["speaking_time_source"],
        "presence": session.presence.live(),
    }


def provider_info() -> dict:
    return get_llm().public_info()


def snapshot(session: Session) -> dict:
    return {"type": "snapshot", "session": session.public(provider_info(), live_metrics(session)).model_dump()}


# ----------------------------------------------------------------------------- enrichment
async def enrich_all(session: Session) -> None:
    llm = get_llm()
    settings = get_settings()
    if not (llm.available and llm.supports_vision and settings.enrich_slides_with_vision):
        session.enrichment.update(status="skipped", reason="no vision model configured")
        await broadcast(session, {"type": "enrichment", "enrichment": session.enrichment})
        return
    session.enrichment.update(status="running", done=0)
    sem = asyncio.Semaphore(3)

    async def one(idx: int) -> None:
        async with sem:
            slide = session.slides[idx]
            new = await enrich_slide(slide, session.dir / slide.image_file)
            session.slides[idx] = new
            session.enrichment["done"] += 1
            await broadcast(session, {"type": "slide_updated", "slide": new.model_dump(), "enrichment": session.enrichment})

    # present-order: current slide first so the first analysis benefits
    order = sorted(range(len(session.slides)), key=lambda i: abs(i + 1 - session.current_slide))
    await asyncio.gather(*(one(i) for i in order), return_exceptions=True)
    ok = sum(1 for s in session.slides if s.enriched)
    session.enrichment.update(status="done", enriched=ok)
    await broadcast(session, {"type": "enrichment", "enrichment": session.enrichment})


# ----------------------------------------------------------------------------- feedback helpers
def _is_duplicate(session: Session, item: FeedbackItem) -> bool:
    new = stems(item.observation + " " + item.evidence_slide)
    for f in session.feedback:
        if f.slide_number != item.slide_number or f.category != item.category:
            continue
        old = stems(f.observation + " " + f.evidence_slide)
        if new and old and len(new & old) / max(len(new | old), 1) > 0.5:
            return True
        if item.category in ("visual_gap", "missed_concept", "reading_slide", "off_topic") and f.evidence_slide == item.evidence_slide:
            return True
    return False


async def add_feedback(session: Session, items: list[FeedbackItem]) -> list[FeedbackItem]:
    added = []
    for it in items:
        if _is_duplicate(session, it):
            continue
        session.feedback.append(it)
        added.append(it)
        await broadcast(session, {"type": "feedback", "item": it.model_dump()})
    return added


async def add_question(session: Session, q: Question) -> None:
    session.questions.append(q)
    await broadcast(session, {"type": "question", "item": q.model_dump()})


# ----------------------------------------------------------------------------- analysis scheduling
def schedule_analysis(session: Session, slide_n: int, final: bool) -> None:
    if session.slide(slide_n) is None:
        return
    if slide_n in session.analysis_running:
        session.analysis_pending[slide_n] = session.analysis_pending.get(slide_n, False) or final
        return
    session.analysis_running.add(slide_n)
    session.track(_run_analysis(session, slide_n, final))


async def _run_analysis(session: Session, slide_n: int, final: bool) -> None:
    try:
        await broadcast(session, {"type": "analyzing", "slide_number": slide_n, "active": True})
        slide = session.slide(slide_n)
        text = session.speech_text(slide_n)
        words_now = len(text.split())
        prev = [f.observation for f in session.feedback if f.slide_number == slide_n][-8:]
        outline = [f"{s.slide_number}. {s.title}"[:70] for s in session.slides][:30]
        window = " ".join(text.split()[-450:])
        result = await asyncio.wait_for(
            analyze_slide(slide, window, session.mode, prev, outline, final),
            timeout=get_settings().llm_timeout_seconds * 2 + 5,
        )
        if session.status == "ready":
            return  # session was reset while we were thinking
        session.analysis[slide_n] = result.state
        session.words_at_last_analysis[slide_n] = words_now
        session.last_analysis_at[slide_n] = time.time()
        session.rejected_items += result.rejected
        if final:
            session.finalized_slides.add(slide_n)
        fused = [f for f in presence_feedback(session, slide_n, result.state.reading_ratio, final)
                 if not any(g.slide_number == slide_n and g.id != f.id and "eye contact" in g.observation.lower() for g in session.feedback)]
        for f in fused:  # camera + transcript fusion is new evidence, so it is not deduplicated against transcript-only items
            session.feedback.append(f)
            await broadcast(session, {"type": "feedback", "item": f.model_dump()})
        added = fused + await add_feedback(session, result.feedback)
        await broadcast(session, {
            "type": "slide_analysis", "state": result.state.model_dump(),
            "rejected_total": session.rejected_items, "error": result.state.last_error or None,
        })
        if session.status == "live":
            maybe_auto_question(session, slide_n, added, final)
    except asyncio.TimeoutError:
        log.warning("analysis timed out for slide %s", slide_n)
        await broadcast(session, {"type": "notice", "level": "warn", "message": f"Analysis of slide {slide_n} timed out; continuing."})
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("analysis crashed for slide %s", slide_n)
        await broadcast(session, {"type": "notice", "level": "warn", "message": f"Analysis of slide {slide_n} failed; continuing."})
    finally:
        session.analysis_running.discard(slide_n)
        await broadcast(session, {"type": "analyzing", "slide_number": slide_n, "active": False})
        pending = session.analysis_pending.pop(slide_n, None)
        if pending is not None and session.status != "ready":
            schedule_analysis(session, slide_n, pending)


def answering_now(msg: dict) -> bool:
    return bool(msg.get("answering"))


def presence_feedback(session: Session, slide_n: int, reading_ratio: float | None, final: bool) -> list[FeedbackItem]:
    """Fuse camera evidence with transcript evidence. Only possible because we know the slide text."""
    if not final:
        return []
    p = session.presence.per_slide.get(slide_n)
    if p is None:
        return []
    summ = p.summary()
    ec = summ["eye_contact_pct"]
    if ec is None or summ["seconds"] < 10:
        return []
    items = []
    if ec < EYE_CONTACT_LOW and reading_ratio is not None and reading_ratio >= 0.25:
        items.append(FeedbackItem(
            slide_number=slide_n, kind="improvement", category="reading_slide", severity="medium",
            observation=f"You read slide {slide_n} off the screen: eye contact was {ec:.0f}% while {int(reading_ratio * 100)}% of your words matched the slide text.",
            evidence_slide=(session.slide(slide_n).text or [session.slide(slide_n).title])[0][:200],
            explanation="Camera (head and eye direction) and transcript both point to reading.",
            suggestion="Know the point of each bullet, face the audience and say it in your own words.",
        ))
    elif ec < EYE_CONTACT_LOW:
        items.append(FeedbackItem(
            slide_number=slide_n, kind="improvement", category="delivery", severity="low",
            observation=f"Low eye contact on slide {slide_n} (about {ec:.0f}% of the time).",
            explanation="Estimated from your webcam, processed in your browser.",
            suggestion="Deliver the key sentence of this slide to the audience, not the screen.",
        ))
    elif ec >= 70 and (reading_ratio is None or reading_ratio < 0.25):
        items.append(FeedbackItem(
            slide_number=slide_n, kind="positive", category="strength", severity="low",
            observation=f"Strong eye contact on slide {slide_n} (about {ec:.0f}%) while explaining in your own words.",
        ))
    return items


def maybe_auto_question(session: Session, slide_n: int, added: list[FeedbackItem], final: bool) -> None:
    settings = get_settings()
    if session.mode not in AUTO_QUESTION_MODES or session.question_pending or session.open_question():
        return
    if time.time() - session.last_auto_question_at < settings.auto_question_min_interval_s:
        return
    probe_worthy = {"slide_speech_consistency", "unsupported_claim", "visual_gap", "off_topic"}
    has_issue = any(f.category in probe_worthy for f in added)
    if not has_issue:
        return  # ask when there is something worth probing; "Ask me a question" covers the rest
    if session.words_on_slide(slide_n) < 15:
        return
    session.last_auto_question_at = time.time()
    schedule_question(session, slide_n, auto=True)


def schedule_question(session: Session, slide_n: int, auto: bool = False) -> None:
    if session.question_pending:
        return
    session.question_pending = True
    session.track(_run_question(session, slide_n, auto))


async def _run_question(session: Session, slide_n: int, auto: bool) -> None:
    try:
        await broadcast(session, {"type": "question_pending", "active": True})
        slide = session.slide(slide_n)
        text = session.speech_text(slide_n) or session.recent_speech()
        asked = [q.question for q in session.questions]
        q = await asyncio.wait_for(
            audience.generate_question(slide, " ".join(text.split()[-400:]), session.feedback, asked, session.mode),
            timeout=get_settings().llm_timeout_seconds + 5,
        )
        if q is None:
            if not auto:
                await broadcast(session, {"type": "notice", "level": "info",
                                          "message": "Say a bit more about this slide first so the question can be specific."})
            return
        if session.status == "ready":
            return
        await add_question(session, q)
    except asyncio.TimeoutError:
        await broadcast(session, {"type": "notice", "level": "warn", "message": "Question generation timed out."})
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("question generation failed")
    finally:
        session.question_pending = False
        await broadcast(session, {"type": "question_pending", "active": False})


async def _evaluate_answer(session: Session, q: Question) -> None:
    answer = " ".join(s.text for s in session.segments if s.kind == "answer" and s.question_id == q.id).strip()
    q.answer_text = answer[:4000]
    await broadcast(session, {"type": "question_update", "item": q.model_dump(), "evaluating": True})
    slide = session.slide(q.slide_number) or session.slides[0]
    try:
        ev, follow = await asyncio.wait_for(
            audience.evaluate_answer(q, answer, slide, session.mode, session.question_chain(q)),
            timeout=get_settings().llm_timeout_seconds + 5,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("answer evaluation failed: %s", exc)
        ev, follow = audience.rule_evaluate(q, answer)
    q.evaluation = ev
    q.status = "answered"
    await broadcast(session, {"type": "question_update", "item": q.model_dump(), "evaluating": False})
    if follow is not None and session.status == "live":
        await add_question(session, follow)


# ----------------------------------------------------------------------------- live delivery nudges
async def _nudge(session: Session, key: str, item: FeedbackItem, cooldown: float = 60.0) -> None:
    now = time.time()
    if now - session.last_nudge_at.get(key, 0) < cooldown:
        return
    session.last_nudge_at[key] = now
    await add_feedback(session, [item])


async def delivery_nudges(session: Session, seg: TranscriptSegment) -> None:
    speech = [s for s in session.segments if s.kind == "speech" and s.source != "typed"]
    recent_text = " ".join(s.text for s in speech[-4:])
    fillers = count_fillers(recent_text)
    n_recent = len(recent_text.split())
    if n_recent >= 25 and sum(fillers.values()) / n_recent >= 0.08:
        top = ", ".join(f"'{k}'" for k, _ in fillers.most_common(2))
        await _nudge(session, "fillers", FeedbackItem(
            slide_number=seg.slide_number, kind="improvement", category="delivery", severity="low",
            observation=f"Filler words are piling up ({sum(fillers.values())} in your last {n_recent} words: {top}).",
            evidence_speech=" ".join(recent_text.split()[-18:]),
            explanation="Measured from the transcript.", suggestion="Pause silently instead of filling the gap."), 75)
    window = [s for s in speech if s.t_end >= seg.t_end - 30]
    dur = (window[-1].t_end - window[0].t_start) if window else 0
    words = sum(len(s.text.split()) for s in window)
    if dur >= 15 and words / dur * 60 >= 190:
        await _nudge(session, "pace", FeedbackItem(
            slide_number=seg.slide_number, kind="improvement", category="delivery", severity="medium",
            observation=f"You're speaking fast: about {round(words / dur * 60)} words/min over the last {int(dur)}s.",
            explanation="Measured from transcript timing (comfortable range 110–170).",
            suggestion="Slow down and let key numbers land."), 90)


# ----------------------------------------------------------------------------- message handling
def _num(v: Any, default: float = 0.0, lo: float = -1e9, hi: float = 1e9) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _switch_slide(session: Session, new_n: int) -> None:
    now = time.time()
    old = session.current_slide
    if session.slide_entered_at and session.status == "live":
        session.time_on_slide[old] = session.time_on_slide.get(old, 0.0) + (now - session.slide_entered_at)
    session.current_slide = new_n
    session.slide_entered_at = now if session.status == "live" else None
    if session.status == "live" and old != new_n:
        new_words = session.words_on_slide(old) - session.words_at_last_analysis.get(old, 0)
        if session.words_on_slide(old) >= 12 and (old not in session.finalized_slides or new_words >= 15):
            schedule_analysis(session, old, final=True)


async def handle_message(session: Session, msg: dict) -> None:
    t = msg.get("type")
    session.last_active = time.time()

    if t == "ping":
        await broadcast(session, {"type": "pong", "t": msg.get("t")})
        return

    if t == "start":
        if session.status == "ended":
            return
        if session.status != "live":
            session.status = "live"
            session.started_at = session.started_at or time.time()
            session.slide_entered_at = time.time()
        await broadcast(session, {"type": "status", "status": session.status, "started_at": session.started_at})
        return

    if t == "slide":
        n = int(_num(msg.get("slide_number"), session.current_slide, 1, len(session.slides)))
        if n != session.current_slide:
            _switch_slide(session, n)
        await broadcast(session, {"type": "slide", "slide_number": n})
        return

    if t == "transcript":
        seq = int(_num(msg.get("seq"), 0, 0))
        if seq and seq <= session.last_seq:
            await broadcast(session, {"type": "ack", "seq": seq})  # duplicate after reconnect
            return
        if session.status != "live":
            return
        text = str(msg.get("text") or "").strip()[:MAX_SEGMENT_CHARS]
        try:
            seg = TranscriptSegment(
                seq=seq or session.last_seq + 1, text=text,
                slide_number=int(_num(msg.get("slide_number"), session.current_slide, 1, len(session.slides))),
                t_start=_num(msg.get("t_start"), session.elapsed(), 0, 86400),
                t_end=_num(msg.get("t_end"), session.elapsed(), 0, 86400),
                kind="answer" if msg.get("kind") == "answer" else "speech",
                question_id=msg.get("question_id") if isinstance(msg.get("question_id"), str) else None,
                source=msg.get("source") if msg.get("source") in ("browser", "whisper", "typed") else "browser",
            )
        except ValidationError:
            return
        session.last_seq = max(session.last_seq, seg.seq)
        if seg.t_end < seg.t_start:
            seg.t_end = seg.t_start
        if text:
            session.segments.append(seg)
            await broadcast(session, {"type": "segment", "item": seg.model_dump()})
        await broadcast(session, {"type": "ack", "seq": seg.seq})
        if not text or seg.kind != "speech":
            return
        await broadcast(session, {"type": "metrics", "metrics": live_metrics(session)})
        if seg.source != "typed":
            await delivery_nudges(session, seg)
        settings = get_settings()
        n = seg.slide_number
        new_words = session.words_on_slide(n) - session.words_at_last_analysis.get(n, 0)
        since = time.time() - session.last_analysis_at.get(n, 0)
        if new_words >= settings.analysis_min_new_words and since >= settings.analysis_min_interval_s:
            # after ~90 words on one slide, it's fair to judge omissions even before the presenter moves on
            schedule_analysis(session, n, final=session.words_on_slide(n) >= 90)
        return

    if t == "pause":
        if session.status != "live":
            return
        d = _num(msg.get("duration_s"), 0, 0, 600)
        if d >= 1.0:
            p = PauseEvent(slide_number=session.current_slide, t=session.elapsed(), duration_s=round(d, 2))
            session.pauses.append(p)
            if d >= 6:
                await _nudge(session, f"pause_{session.current_slide}", FeedbackItem(
                    slide_number=session.current_slide, kind="improvement", category="delivery", severity="low",
                    observation=f"Long pause of {d:.1f}s on this slide.",
                    explanation="Measured by microphone voice activity.",
                    suggestion="If you lost your thread, a short signpost ('the key point here is…') helps you recover."), 120)
        return

    if t == "presence":
        if session.status != "live":
            return
        sample = clean_presence(msg)
        if sample is None:
            return
        n = int(_num(msg.get("slide_number"), session.current_slide, 1, len(session.slides)))
        session.presence.add_sample(n, sample)
        return

    if t == "look_away":
        if session.status != "live" or answering_now(msg):
            return
        d = _num(msg.get("duration_s"), 0, 0, 600)
        if d >= LONG_LOOK_AWAY_S:
            n = int(_num(msg.get("slide_number"), session.current_slide, 1, len(session.slides)))
            session.presence.look_aways.append({"slide_number": n, "duration_s": round(d, 1)})
            if d >= 8:
                await _nudge(session, "look_away", FeedbackItem(
                    slide_number=n, kind="improvement", category="delivery", severity="low",
                    observation=f"You looked away from the audience for about {d:.0f}s.",
                    explanation="Estimated from your webcam (head and eye direction), processed in your browser.",
                    suggestion="Glance at the slide, then turn back and deliver the point."), 90)
        return

    if t == "activity":
        if session.status != "live":
            return
        n = int(_num(msg.get("slide_number"), session.current_slide, 1, len(session.slides)))
        session.voiced_s[n] = session.voiced_s.get(n, 0.0) + _num(msg.get("voiced_s"), 0, 0, 120)
        return

    if t == "ask_question":
        if session.status != "live":
            return
        n = int(_num(msg.get("slide_number"), session.current_slide, 1, len(session.slides)))
        schedule_question(session, n)
        return

    if t == "answer_done":
        q = session.question(str(msg.get("question_id") or ""))
        if q is None or q.status != "open":
            return
        typed = str(msg.get("typed_answer") or "").strip()[:MAX_SEGMENT_CHARS]
        if typed:
            session.last_seq += 1
            session.segments.append(TranscriptSegment(seq=session.last_seq, text=typed, slide_number=q.slide_number,
                                                      t_start=session.elapsed(), t_end=session.elapsed(), kind="answer",
                                                      question_id=q.id, source="typed"))
        session.track(_evaluate_answer(session, q))
        return

    if t == "skip_question":
        q = session.question(str(msg.get("question_id") or ""))
        if q and q.status == "open":
            q.status = "skipped"
            await broadcast(session, {"type": "question_update", "item": q.model_dump()})
        return

    if t == "end":
        await end_session(session)
        return

    await broadcast(session, {"type": "notice", "level": "warn", "message": f"Unknown message type: {str(t)[:40]}"})


async def end_session(session: Session) -> None:
    if session.status == "ended":
        return
    if session.status == "live" and session.slide_entered_at:
        session.time_on_slide[session.current_slide] = session.time_on_slide.get(session.current_slide, 0.0) + (
            time.time() - session.slide_entered_at)
    session.status = "ended"
    session.ended_at = time.time()
    session.report_status = "generating"
    await broadcast(session, {"type": "status", "status": "ended"})
    session.track(_finalize_and_report(session))


async def _finalize_and_report(session: Session) -> None:
    from ..agents.report import build_report  # local import avoids a cycle

    try:
        # final analysis of every presented slide that has not been judged with its full transcript
        for s in session.slides:
            n = s.slide_number
            wc = session.words_on_slide(n)
            if wc >= 12 and (n not in session.finalized_slides or wc - session.words_at_last_analysis.get(n, 0) >= 10):
                schedule_analysis(session, n, final=True)
        deadline = time.time() + get_settings().llm_timeout_seconds * 2 + 10
        while (session.analysis_running or session.question_pending) and time.time() < deadline:
            await asyncio.sleep(0.25)
        session.report = await build_report(session)
        session.report_status = "ready"
        await broadcast(session, {"type": "report_ready"})
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("report generation failed")
        session.report_status = "failed"
        await broadcast(session, {"type": "notice", "level": "error", "message": "Report generation failed. Try reloading the report."})
