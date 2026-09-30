# PitchMirror

**Present. Get understood. Get better.**

[![CI](https://github.com/Maggiee18/pitchmirror-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Maggiee18/pitchmirror-ai/actions/workflows/ci.yml)

**Live demo:** LIVE_DEMO_URL (open in Chrome or Edge and allow the microphone)

PitchMirror is a real time coach for presentations, vivas, interviews and pitches. It looks at the slide you are on and listens to what you say at the same time. When the two don't match, it tells you. It spots charts, tables and diagrams you never explained, watches your eye contact through the webcam (on your own device), measures your delivery, and then behaves like a real examiner or investor: it asks a question grounded in your slide and your words, reads it aloud, scores your spoken answer and follows up on what you actually said.

## Features

| | What it does | How |
|---|---|---|
| Slide aware analysis | Flags contradictions ("slide says 13%, you said 25"), missed key points, read aloud slides, off topic speech | Deterministic evidence (key point coverage, number matching including table cells and spoken number words, verbatim overlap) + optional LLM reasoning |
| Visual explanation check | Detects charts, tables and diagrams, then checks whether you explained them | PDF vector/raster/table detection, PPTX chart data, optional vision model |
| Adaptive audience | Mode specific questions (Pitch, Viva, Interview, Presentation), spoken aloud, with deeper follow ups built from your answer | LLM with grounded rule fallback, browser speech synthesis |
| Delivery | Words per minute, fillers, pauses, repetition, rushed sections | Measured from transcript timing and microphone voice activity |
| Presence (camera) | Eye contact, looking down, turning to the screen, long look aways. Combined with the transcript it can say *"you read slide 3 off the screen"* | MediaPipe Face Landmarker **in the browser**; only frame counts leave the device |
| Transparent report | Raw measurements kept separate from interpretation, every score shows how it was calculated | Rule based scoring, optional LLM summary |

## Quick start

**Windows:** double click `start.bat`. **macOS/Linux:** `./start.sh`. Then open http://localhost:8000 in Chrome or Edge.

Only Python 3.10+ is needed (the frontend ships prebuilt in `frontend/dist`). The app works with no API key using the rule engine; add keys for AI analysis:

| Variable | Purpose | Where to get it |
|---|---|---|
| `GEMINI_API_KEY` (recommended) | Slide understanding (vision), semantic analysis, questions, report | https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | Defaults to `gemini-3.8-flash`; `gemini-3.5-flash-lite` is faster | |
| `GROQ_API_KEY` (optional) | Server Whisper transcription (Firefox/Safari, or better accuracy) | https://console.groq.com/keys |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Alternatives to Gemini | platform.openai.com, console.anthropic.com |

Copy `.env.example` to `.env` in the project root and fill in the keys. `.env` is git ignored.

Developer setup:

```bash
pip install -r backend/requirements-dev.txt
cd backend && python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev      # http://localhost:5173, proxies to :8000
```

Docker: `docker build -t pitchmirror . && docker run -p 7860:7860 --env-file .env pitchmirror` then open http://localhost:7860.

## 3 minute demo (Viva)

1. Pick **Viva**, click **Try the sample viva deck**, click **Start presenting**, allow microphone and camera, look at the camera for a second while it calibrates.
2. Explain slides 1 to 3 naturally (arrow keys change slides).
3. On **slide 4**, say *"Our YOLOv8 model improves accuracy by 25 percent over YOLOv5"* and don't mention the chart. Press →.
4. A red **Mismatch** card appears with both quotes, and the examiner **asks aloud** which number is correct.
5. Click **Answer**, reply out loud, click **Done answering**. Your answer is scored and a **follow up** builds on what you said.
6. **End presentation** for the report: delivery, presence, slide by slide consistency, visuals, questions and transparent scores.

## How it works

```
Microphone ─► Web Speech API (or Whisper) ─► transcript ─┐
Microphone ─► voice activity detection ─► pauses, speaking time ─┤
Webcam ─► MediaPipe (in browser) ─► gaze frame counts ──────────┤  WebSocket (sequence numbers, acks, auto reconnect)
Slide changes ──────────────────────────────────────────────────┘
                                   │
            FastAPI session engine: records events, schedules analysis as background tasks with timeouts
                                   │
  Deterministic layer ─► delivery metrics, key point coverage, number conflicts, verbatim ratio, visual references, eye contact
                                   │   structured evidence
  LLM layer (optional) ─► consistency + visual explanation, adaptive questions, answer scoring, report narrative
                                   │   JSON schema validation + grounding check (quotes must exist)
                                   ▼
                        live feedback, questions and report in the UI
```

| Component | Code | Deterministic or AI |
|---|---|---|
| Context | `backend/app/ingest/*`, `agents/slide_context.py` | Deterministic parsing; vision description optional |
| Delivery | `analysis/delivery.py` | Deterministic |
| Presence | `frontend/src/hooks/useCameraPresence.ts`, `analysis/presence.py` | On device model, deterministic counting |
| Consistency + visuals | `analysis/evidence.py`, `agents/slide_analyzer.py` | Evidence deterministic, reasoning AI with rule fallback |
| Audience | `agents/audience.py` | AI with grounded rule fallback |
| Report | `agents/report.py` | Deterministic sections and scores, AI summary optional |
| Real time engine | `realtime/engine.py`, `realtime/session.py` | Async, non blocking |

LangGraph was deliberately not used: the flow is event driven rather than a multi step plan, and a plain async orchestrator is faster and easier to debug in a live demo.

## Evaluation and tests

`python scripts/evaluate.py` runs labelled presenter scenarios through the real pipeline and writes [docs/evaluation.md](docs/evaluation.md). Current results with the rule engine (no API key):

| Check | Result |
|---|---|
| Tuning scenarios fully correct | 13/13 |
| Hold out scenarios (written after tuning, reported as is) | 5/6, precision 86%, recall 100% |
| Fabricated AI claims rejected by the grounding filter | 4/4, genuine claim kept |
| Delivery metric checks (WPM, fillers, pauses) | 4/4 |
| Analysis latency (rule layer) | under 1 ms per slide |

Run `python scripts/evaluate.py --ai` with a key configured to add the AI engine's numbers. These are small hand labelled sets that show the system behaves as designed, not a validated accuracy on real presentations.

Test suite (runs in GitHub Actions on every push, together with a frontend build and a Docker smoke test):

```bash
cd backend && python -m pytest -q                         # unit, WebSocket integration, provider, benchmark and security tests
python scripts/e2e_demo.py http://127.0.0.1:8000          # full browser demo with scripted speech, fake mic and camera
python scripts/e2e_failures.py http://127.0.0.1:8000      # invalid file, too many slides, no microphone, typing fallback
```

## Reliability

- Every AI feedback item must quote the slide and/or transcript; quotes are verified and anything not found is discarded (the report shows how many).
- LLM calls have timeouts, one retry and a circuit breaker; on failure the rule engine takes over and the presentation continues.
- Transcript messages carry sequence numbers and are re sent after a reconnect; the server removes duplicates. Refreshing the page restores the session.
- The microphone pauses while the examiner speaks, so questions are never transcribed as your speech.
- Camera, voice and AI are all optional; if any is unavailable the rest keeps working.
- Anything that cannot be measured says *"Unable to determine from available audio/slide context."*

## Deployment

**Hugging Face Spaces (recommended, free, HTTPS for microphone access):**

1. Create a Space at https://huggingface.co/new-space with SDK **Docker** (blank template).
2. In the Space settings add secrets `GEMINI_API_KEY` and `GROQ_API_KEY`.
3. Create a write token at https://huggingface.co/settings/tokens.
4. In this GitHub repo, Settings → Secrets and variables → Actions: add secret `HF_TOKEN` and variable `HF_SPACE` (for example `Maggiee18/pitchmirror`).
5. Push to `main`. The `Deploy to Hugging Face Space` workflow builds and publishes it. Open `https://<user>-<space>.hf.space`.

**Render (alternative):** New → Blueprint → select this repo (`render.yaml`), then add the two keys.

A public link is protected by per IP rate limits and a cap on concurrent sessions so it cannot burn through the API quota.

## Privacy and security

- Raw audio and video are never stored or uploaded. Eye contact runs entirely in the browser; only frame counts are sent. Browser speech recognition in Chrome uses Google's speech service; Whisper chunks are processed in memory and discarded.
- The uploaded file is deleted right after parsing. Slide images live in `.pitchmirror_data/<session>/` until the session expires (3 hours idle), is deleted, or the server restarts. Transcripts, feedback and reports are held in memory only. There are no accounts and no database.
- Uploads are validated by extension and file signature, size and slide count limits apply, PPTX zip bombs are rejected, file names are server generated, session ids are validated, served paths are contained, uploaded text is never rendered as HTML, and slide text is passed to models as untrusted data.

## AI tools disclosure

| Tool | Used for |
|---|---|
| Claude (Anthropic), via Claude Cowork | Pair programming: generated most of the code, tests and documentation under my direction; I designed the product, chose features, tested it, fixed configuration and made the final decisions |
| Google Gemini API (`gemini-3.8-flash` / `gemini-3.5-flash`) | At runtime: reading slide images, slide/speech consistency reasoning, adaptive questions, answer scoring, report summary |
| Groq Whisper (`whisper-large-v3-turbo`) | Optional server speech to text |
| Browser Web Speech API | Live speech to text (Google's service in Chrome) and spoken questions (speech synthesis) |
| Google MediaPipe Face Landmarker | On device head pose and eye direction for eye contact |

Open source libraries: FastAPI, Uvicorn, PyMuPDF, python-pptx, Pillow, httpx, pydantic, React, Vite, TypeScript, Playwright, pytest, matplotlib. No datasets were used. The sample CrowdSense deck is fictional demo content generated by `scripts/make_sample_deck.py`; its numbers are illustrative.

## Known limitations

- Live transcription needs Chrome or Edge (or a Groq/OpenAI key for Whisper); other browsers can type.
- Without an AI key, consistency checks are keyword and number based: strong on numeric mismatches and missed points, weaker on paraphrased contradictions. Raster charts are only understood by the vision model.
- Eye contact is an estimate from head pose and eye direction relative to a short calibration; lighting and glasses affect it.
- PPTX renders pixel perfect only with LibreOffice installed (the Docker image includes it); otherwise a text preview is shown.
- Sessions are in memory, so restarting the server ends open sessions (the UI explains this and returns to upload).

## Project structure

```
backend/app/        FastAPI app: ingest/, analysis/ (deterministic), agents/ (LLM + fallbacks), realtime/ (WebSocket engine)
backend/tests/      pytest suite
frontend/src/       React + TypeScript UI (hooks for speech, voice activity, camera, Whisper, WebSocket)
scripts/            sample deck generator, evaluation benchmark, browser end to end tests
docs/               evaluation results
samples/            demo decks
```
