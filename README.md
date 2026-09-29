# PitchMirror

**Present. Get understood. Get better.**

PitchMirror is a real-time presentation, viva, interview and pitch coach. It looks at the slide you are on and listens to what you say at the same time. It flags when the two don't match, spots charts, tables and diagrams you never explained, measures your delivery, and asks questions like a real examiner, investor or interviewer, with deeper follow-ups based on your answers.

## Quick start

**Windows:** double-click `start.bat`. **macOS/Linux:** `./start.sh`. Then open **http://localhost:8000 in Chrome or Edge**.

The frontend ships prebuilt in `frontend/dist`, so only Python 3.10+ is required. The app runs without any API key (rule engine); add a key to `.env` for AI analysis:

| Variable | Purpose | Where to get it |
|---|---|---|
| `GEMINI_API_KEY` (recommended) | Semantic slide/speech analysis, vision reading of slides, questions, report | https://aistudio.google.com/apikey |
| `OPENAI_API_KEY` *(alternative)* | Same, via OpenAI; also enables Whisper STT | https://platform.openai.com/api-keys |
| `ANTHROPIC_API_KEY` *(alternative)* | Same, via Claude | https://console.anthropic.com/settings/keys |
| `GROQ_API_KEY` *(optional)* | Fast server Whisper transcription (Firefox/Safari or better accuracy) | https://console.groq.com/keys |

Put them in `.env` in the project root (copy from `.env.example`). Restart the server after editing.

Manual dev setup:

```bash
pip install -r backend/requirements.txt
cd backend && python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies to :8000)
```

Docker: `docker build -t pitchmirror . && docker run -p 8000:8000 --env-file .env pitchmirror`

## 3-minute demo script (Viva)

1. Open the app, pick **Viva**, click **Try the sample viva deck (CrowdSense)**.
2. **Start presenting**, allow the microphone. Explain slides 1–3 naturally (arrow keys change slides).
3. On **slide 4 (Detection Results)**, say: *"Our YOLOv8 model improves accuracy by 25 percent over YOLOv5 and it's really fast"*, and don't mention the bar chart. Press → .
4. A red **Mismatch** card appears (slide says 13%, you said 25) with both quotes as evidence, plus a note that the chart wasn't explained.
5. The examiner asks: *"Your slide says 13%… but you said 25. Which is correct, and how was it measured?"* Click **Answer**, speak, click **Done answering**.
6. Your answer is scored and a **follow-up** builds on something you actually said (e.g. *"You mentioned 600 images…"*).
7. **End presentation** → full report with transparent scores.

## How it works

```
Mic ─► Web Speech API (or Whisper) ─► transcript segments ─┐
Mic ─► voice activity detection (pauses, speaking time) ───┤  WebSocket (seq + ack, auto-reconnect)
Slide changes ─────────────────────────────────────────────┘
                                   │
                         FastAPI session engine (never blocks: all AI runs as background tasks with timeouts)
                                   │
  Deterministic layer ─► delivery metrics, key-point coverage, number conflicts, verbatim-reading ratio, visual references
                                   │  structured evidence
  LLM layer (optional) ─► slide-speech consistency + visual explanation (1 call per slide window)
                        ─► adaptive question / answer evaluation + follow-up
                        ─► report narrative
                                   │  JSON schema validation + grounding check
                                   ▼
                         feedback / questions pushed to the UI
```

| "Agent" | Implementation | Deterministic or LLM |
|---|---|---|
| Context | `ingest/*`, `agents/slide_context.py`: PDF/PPTX parsing, title/bullets, tables, raster and vector visuals, PPTX chart data + speaker notes, optional vision description | Deterministic, vision optional |
| Delivery | `analysis/delivery.py`: WPM, fillers, pauses, repetition, rushed sections | Deterministic only |
| Consistency + Visual | `analysis/evidence.py` → `agents/slide_analyzer.py` | Evidence deterministic; reasoning LLM with rule fallback |
| Adaptive audience | `agents/audience.py`: mode-specific questions, answer scoring, follow-ups | LLM with grounded rule fallback |
| Report | `agents/report.py`: sections + transparent scores | Deterministic; narrative LLM optional |

LangGraph was deliberately not used: the flow is an event-driven pipeline, not a multi-step plan, and a plain async orchestrator is faster and easier to debug during a live demo.

### Reliability rules

- **Grounding check:** every AI feedback item must quote the slide and/or transcript; quotes are verified against the real text and anything that can't be found is discarded (the report shows how many).
- Raw measurements and interpretations are shown separately. Anything not measurable says *"Unable to determine from available audio/slide context."*
- Feedback during a slide only reports contradictions / off-topic / read-aloud; omissions are judged once you leave the slide (or after ~90 words).
- LLM calls have timeouts, one retry, and a circuit breaker; on failure the rule engine takes over and the presentation continues.
- Transcript messages carry sequence numbers and are re-sent after a reconnect; the server de-duplicates them. Refreshing the page restores the session.
- Speech recognisers drop many "um/uh", so filler counts are labelled as a lower bound.

### Privacy

- Raw audio is never stored. Browser STT runs in the browser (Chrome sends audio to Google's speech service); Whisper chunks are processed in memory and discarded.
- The uploaded file is deleted right after parsing. Slide images live in `.pitchmirror_data/<session>/` until the session expires (default 3h idle), the user deletes it, or the server restarts.
- Transcripts, feedback and reports are held in memory only. There are no accounts and no database.

### Security

Extension + magic-byte validation, size and slide-count limits, zip-bomb checks for PPTX, server-generated file names (no user paths), session-id format validation, path containment checks when serving images, no HTML rendering of uploaded content (React escapes everything), and slide text is wrapped as untrusted data in prompts.

## Testing

```bash
pip install -r backend/requirements-dev.txt
cd backend && python -m pytest -q                         # 24 unit + integration + provider tests
python scripts/e2e_demo.py http://127.0.0.1:8000          # full browser demo flow (scripted speech, fake mic)
python scripts/e2e_failures.py http://127.0.0.1:8000      # bad file, too many slides, no mic, typing fallback
python scripts/make_sample_deck.py                        # regenerate sample decks
```

## Known limitations

- Live transcription needs Chrome or Edge (or a Whisper key). Firefox/Safari users can type.
- Without an AI key, consistency checks are keyword and number based: good at numeric mismatches and missed points, weaker at paraphrased contradictions. Raster charts can only be judged by the vision model.
- PPTX renders pixel-perfect only when LibreOffice is installed; otherwise a clean text preview is shown (all text, notes and chart data are still used).
- Sessions are in memory: restarting the server ends open sessions (the UI explains this and returns to upload).
