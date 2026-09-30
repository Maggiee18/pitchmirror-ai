"""PitchMirror API: REST for upload/report, WebSocket for the live session. Also serves the built frontend."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .agents.llm import get_llm
from .config import ROOT_DIR, get_settings
from .ingest import detect_kind, parse_presentation
from .realtime import engine
from .realtime.session import Session, store
from .stt import clean_whisper_text, get_stt

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pitchmirror")

MODES = ("pitch", "viva", "interview", "presentation")
SID_RE = re.compile(r"^s_[0-9a-f]{10}$")
SAMPLES_DIR = ROOT_DIR / "samples"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


async def _janitor() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            n = store.cleanup_expired()
            if n:
                log.info("expired %d idle sessions", n)
        except Exception:
            log.exception("cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # stale data from a previous run can never be reattached (sessions are in memory), so clear it
    shutil.rmtree(settings.data_path, ignore_errors=True)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    llm = get_llm()
    stt = get_stt()
    log.info("LLM: %s | server STT: %s", f"{llm.name} ({llm.model})" if llm.available else "offline (rule-based)",
             stt.name if stt else "disabled (browser speech recognition only)")
    task = asyncio.create_task(_janitor())
    yield
    task.cancel()


app = FastAPI(title="PitchMirror", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"], allow_headers=["*"],
)


class RateLimiter:
    """Tiny in-memory sliding window limiter, so a public demo link can't burn through the API keys."""

    def __init__(self) -> None:
        self.hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window_s: float) -> None:
        now = time.time()
        recent = [t for t in self.hits.get(key, []) if now - t < window_s]
        if len(recent) >= limit:
            raise HTTPException(429, "Too many requests. Please wait a little and try again.")
        recent.append(now)
        self.hits[key] = recent
        if len(self.hits) > 5000:  # bound memory
            self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < window_s}


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")


def _guard_new_session(request: Request) -> None:
    limiter.check("up:" + client_ip(request), settings.uploads_per_hour_per_ip, 3600)
    if len(store) >= settings.max_sessions:
        store.cleanup_expired()
        if len(store) >= settings.max_sessions:
            raise HTTPException(503, "The server is busy. Please try again in a few minutes.")


def _session_or_404(sid: str) -> Session:
    if not SID_RE.match(sid or ""):
        raise HTTPException(404, "Session not found")
    s = store.get(sid)
    if s is None:
        raise HTTPException(404, "Session not found or expired. Please upload your deck again.")
    return s


def _mode_or_400(mode: str) -> str:
    mode = (mode or "").lower().strip()
    if mode not in MODES:
        raise HTTPException(400, f"Mode must be one of {', '.join(MODES)}")
    return mode


def _safe_display_name(name: str) -> str:
    base = Path(name or "presentation").name
    return re.sub(r"[^\w.\- ()]+", "_", base)[:120] or "presentation"


async def _create_session(path: Path, kind: str, display_name: str, mode: str, sid: str, session_dir: Path) -> dict:
    try:
        slides = await run_in_threadpool(parse_presentation, path, kind, session_dir, settings.render_width_px, settings.max_slides)
    except ValueError as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        log.exception("parse failed")
        raise HTTPException(422, "Could not process this file. Try exporting it to PDF.") from exc
    if all(s.is_empty for s in slides):
        log.info("deck has no extractable text; relying on vision / presenter speech")
    session = store.create(display_name, mode, slides, session_dir, sid)
    session.track(engine.enrich_all(session))
    return session.public(engine.provider_info(), engine.live_metrics(session)).model_dump()


# ----------------------------------------------------------------------------- REST
@app.get("/api/health")
async def health():
    stt = get_stt()
    return {
        "ok": True, "llm": get_llm().public_info(),
        "stt": {"server_whisper": bool(stt), "provider": stt.name if stt else None},
        "limits": {"max_upload_mb": settings.max_upload_mb, "max_slides": settings.max_slides},
        "sessions": len(store),
    }


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...), mode: str = Form("presentation")):
    mode = _mode_or_400(mode)
    _guard_new_session(request)
    head = await file.read(2048)
    kind = detect_kind(file.filename or "", head) if head else None
    if not kind:
        raise HTTPException(400, "The file is empty.")
    sid = store.new_id()
    session_dir = settings.data_path / sid
    session_dir.mkdir(parents=True)
    tmp = session_dir / f"upload.{kind}"  # server chosen name: no user controlled paths
    size = len(head)
    try:
        with tmp.open("wb") as fh:
            fh.write(head)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")
                fh.write(chunk)
        return await _create_session(tmp, kind, _safe_display_name(file.filename), mode, sid, session_dir)
    except HTTPException:
        if store.get(sid) is None:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)  # the original upload is never kept


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/samples")
async def list_samples():
    if not SAMPLES_DIR.exists():
        return []
    return [{"name": p.name, "kind": p.suffix[1:]} for p in sorted(SAMPLES_DIR.iterdir()) if p.suffix in (".pdf", ".pptx")]


@app.post("/api/samples/{name}")
async def start_sample(request: Request, name: str, mode: str = Form("viva")):
    mode = _mode_or_400(mode)
    _guard_new_session(request)
    path = SAMPLES_DIR / Path(name).name
    if path.suffix not in (".pdf", ".pptx") or not path.is_file():
        raise HTTPException(404, "Sample not found")
    sid = store.new_id()
    session_dir = settings.data_path / sid
    session_dir.mkdir(parents=True)
    return await _create_session(path, path.suffix[1:], path.name, mode, sid, session_dir)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    s = _session_or_404(sid)
    return s.public(engine.provider_info(), engine.live_metrics(s)).model_dump()


@app.post("/api/sessions/{sid}/mode")
async def set_mode(sid: str, mode: str = Form(...)):
    s = _session_or_404(sid)
    if s.status != "ready":
        raise HTTPException(409, "Mode can only be changed before you start.")
    s.mode = _mode_or_400(mode)
    return {"mode": s.mode}


@app.post("/api/sessions/{sid}/restart")
async def restart(sid: str):
    s = _session_or_404(sid)
    s.reset_run()
    await engine.broadcast(s, engine.snapshot(s))
    return {"ok": True}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    _session_or_404(sid)
    store.delete(sid)
    return {"deleted": True}


@app.get("/api/sessions/{sid}/slides/{n}.png")
async def slide_image(sid: str, n: int):
    s = _session_or_404(sid)
    slide = s.slide(n)
    if slide is None or not slide.image_file:
        raise HTTPException(404, "Slide not found")
    path = (s.dir / slide.image_file).resolve()
    if s.dir.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Slide not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/sessions/{sid}/report")
async def get_report(sid: str):
    s = _session_or_404(sid)
    if s.report_status == "ready" and s.report:
        return {"status": "ready", "report": s.report}
    if s.status != "ended":
        return {"status": "not_ended"}
    if s.report_status == "failed":
        from .agents.report import build_report
        try:
            s.report = await build_report(s)
            s.report_status = "ready"
            return {"status": "ready", "report": s.report}
        except Exception:
            log.exception("report retry failed")
            raise HTTPException(500, "Report generation failed.")
    return {"status": "generating"}


@app.post("/api/transcribe")
async def transcribe(request: Request):
    stt = get_stt()
    if stt is None:
        raise HTTPException(503, "Server transcription is not configured (set GROQ_API_KEY or OPENAI_API_KEY).")
    limiter.check("stt:" + client_ip(request), settings.transcribe_per_minute_per_ip, 60)
    ctype = request.headers.get("content-type", "audio/webm").split(";")[0]
    if not ctype.startswith("audio/"):
        raise HTTPException(415, "Expected an audio upload.")
    body = await request.body()
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(413, "Audio chunk too large.")
    if len(body) < 1500:
        return {"text": ""}
    try:
        text = await stt.transcribe(body, ctype)
    except Exception as exc:
        log.warning("whisper failed: %s", exc)
        raise HTTPException(502, "Transcription service failed.")
    return {"text": clean_whisper_text(text)}


# ----------------------------------------------------------------------------- WebSocket
@app.websocket("/ws/{sid}")
async def ws_session(websocket: WebSocket, sid: str):
    await websocket.accept()
    session = store.get(sid) if SID_RE.match(sid or "") else None
    if session is None:
        await websocket.send_json({"type": "fatal", "message": "Session not found or expired. Please upload your deck again."})
        await websocket.close(code=4404)
        return
    session.subscribers.add(websocket)
    try:
        await websocket.send_json(engine.snapshot(session))
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 20000:
                await websocket.send_json({"type": "notice", "level": "warn", "message": "Message too large; ignored."})
                continue
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await websocket.send_json({"type": "notice", "level": "warn", "message": "Malformed message ignored."})
                continue
            try:
                await engine.handle_message(session, msg)
            except Exception:
                log.exception("error handling %s", msg.get("type"))
                await websocket.send_json({"type": "notice", "level": "warn", "message": "Something went wrong processing that event."})
    except WebSocketDisconnect:
        pass
    finally:
        session.subscribers.discard(websocket)


# ----------------------------------------------------------------------------- frontend
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def spa(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        candidate = (FRONTEND_DIST / path).resolve()
        if path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
