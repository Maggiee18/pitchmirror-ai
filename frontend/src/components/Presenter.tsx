import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import { useCameraPresence } from "../hooks/useCameraPresence";
import { useSpeaker } from "../hooks/useSpeaker";
import { browserSpeechSupported, useBrowserSpeech } from "../hooks/useBrowserSpeech";
import { useSessionSocket } from "../hooks/useSessionSocket";
import { useVoiceActivity } from "../hooks/useVoiceActivity";
import { useWhisperRecorder } from "../hooks/useWhisperRecorder";
import { initialLive, reducer } from "../sessionState";
import type { Health, Mode, ServerEvent } from "../types";
import { MODE_INFO } from "../types";
import FeedbackPanel from "./FeedbackPanel";
import { Logo } from "./Home";
import SlidePanel from "./SlidePanel";
import TranscriptPanel from "./TranscriptPanel";

type Engine = "browser" | "whisper" | "typed";
type MicState = "off" | "requesting" | "on" | "denied" | "no-mic" | "error";

const LANGS = [
  ["en-IN", "English (India)"],
  ["en-US", "English (US)"],
  ["en-GB", "English (UK)"],
] as const;

export default function Presenter({ sessionId, onHome, onReport }: { sessionId: string; onHome: () => void; onReport: () => void }) {
  const [state, dispatch] = useReducer(reducer, initialLive);
  const session = state.session;
  const [health, setHealth] = useState<Health | null>(null);
  const [engine, setEngine] = useState<Engine>(browserSpeechSupported() ? "browser" : "typed");
  const [lang, setLang] = useState<string>(() => (navigator.language?.startsWith("en-") ? navigator.language : "en-IN"));
  const [mic, setMic] = useState<MicState>("off");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [paused, setPaused] = useState(false);
  const [answeringId, setAnsweringId] = useState<string | null>(null);
  const [ending, setEnding] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [cameraWanted, setCameraWanted] = useState<boolean>(() => !!navigator.mediaDevices?.getUserMedia);
  const [voiceOn, setVoiceOn] = useState(true);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [, forceTick] = useState(0);
  const clock = useRef({ base: 0, at: performance.now(), running: false });

  // ---------------------------------------------------------------- clock (presentation-relative seconds)
  const now = useCallback(() => {
    const c = clock.current;
    return c.running ? c.base + (performance.now() - c.at) / 1000 : c.base;
  }, []);
  const syncClock = (elapsed: number, running: boolean) => {
    clock.current = { base: elapsed, at: performance.now(), running };
  };
  useEffect(() => {
    const t = window.setInterval(() => forceTick((x) => x + 1), 1000);
    return () => window.clearInterval(t);
  }, []);

  // ---------------------------------------------------------------- socket
  const socketRef = useRef<ReturnType<typeof useSessionSocket> | null>(null);
  const onEvent = useCallback((e: ServerEvent) => {
    if (e.type === "snapshot") {
      syncClock(e.session.elapsed_s, e.session.status === "live");
      socketRef.current?.syncSeq(e.session.last_seq);
    }
    if (e.type === "status") {
      if (e.status === "live" && !clock.current.running) syncClock(clock.current.base, true);
      if (e.status === "ended") syncClock(now(), false);
    }
    dispatch({ type: "server", event: e });
  }, [now]);
  const socket = useSessionSocket(sessionId, onEvent);
  socketRef.current = socket;

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (state.reportReady && session?.status === "ended") onReport();
  }, [state.reportReady, session?.status, onReport]);

  // ---------------------------------------------------------------- microphone
  const stopMic = useCallback(() => {
    setStream((s) => {
      s?.getTracks().forEach((t) => t.stop());
      return null;
    });
    setMic("off");
  }, []);
  useEffect(() => () => stream?.getTracks().forEach((t) => t.stop()), [stream]);

  const requestMic = useCallback(async (): Promise<boolean> => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setMic("error");
      dispatch({ type: "notice", level: "error", message: "Microphone access needs HTTPS or localhost in a modern browser." });
      return false;
    }
    setMic("requesting");
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      setStream(s);
      setMic("on");
      s.getAudioTracks()[0]?.addEventListener("ended", () => {
        setMic("no-mic");
        dispatch({ type: "notice", level: "warn", message: "Microphone disconnected. Reconnect it and click the mic button." });
      });
      return true;
    } catch (err) {
      const name = (err as DOMException)?.name;
      setMic(name === "NotAllowedError" || name === "SecurityError" ? "denied" : "no-mic");
      return false;
    }
  }, []);

  const live = session?.status === "live";
  const speaker = useSpeaker(lang);
  // the mic is paused while the examiner speaks so the question isn't transcribed as your speech
  const listening = live && mic === "on" && !paused && engine !== "typed" && !speaker.speaking;

  // ---------------------------------------------------------------- transcript sinks
  const currentSlide = session?.current_slide ?? 1;
  const slideRef = useRef(currentSlide);
  slideRef.current = currentSlide;
  const answeringRef = useRef(answeringId);
  answeringRef.current = answeringId;

  const emitText = useCallback(
    (text: string, tStart: number, tEnd: number, source: "browser" | "whisper" | "typed") => {
      const q = answeringRef.current;
      socket.sendTranscript({
        text, t_start: tStart, t_end: tEnd, slide_number: slideRef.current, source,
        kind: q ? "answer" : "speech", question_id: q,
      });
    },
    [socket],
  );

  const { interim } = useBrowserSpeech({
    active: listening && engine === "browser",
    lang,
    now,
    onFinal: (t, a, b) => emitText(t, a, b, "browser"),
    onError: (kind, detail) => {
      if (kind === "denied") {
        setMic("denied");
      } else if (kind === "no-mic") {
        setMic("no-mic");
      } else {
        dispatch({ type: "notice", level: "warn", message: kind === "network" ? detail : `Speech recognition: ${detail}` });
      }
    },
  });

  const vad = useVoiceActivity({
    stream,
    enabled: listening,
    onPause: (d) => {
      if (!answeringRef.current) socket.send({ type: "pause", duration_s: d });
    },
    onActivity: (v) => {
      if (!answeringRef.current) socket.send({ type: "activity", voiced_s: v, slide_number: slideRef.current });
    },
  });

  const camera = useCameraPresence({
    enabled: live && cameraWanted,
    videoRef,
    onSample: (s) => socket.send({ type: "presence", slide_number: slideRef.current, ...s }),
    onLookAway: (d) => socket.send({ type: "look_away", slide_number: slideRef.current, duration_s: d, answering: !!answeringRef.current }),
  });

  // speak new questions aloud (not ones that already existed when the page loaded)
  const knownQuestions = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!session) return;
    if (knownQuestions.current === null) {
      knownQuestions.current = new Set(session.questions.map((q) => q.id));
      return;
    }
    for (const q of session.questions) {
      if (knownQuestions.current.has(q.id)) continue;
      knownQuestions.current.add(q.id);
      if (voiceOn && live && q.status === "open") speaker.speak(q.question);
    }
  }, [session, voiceOn, live, speaker]);

  const whisper = useWhisperRecorder({
    stream,
    active: listening && engine === "whisper",
    now,
    voicedRecentRef: vad.voicedRecentRef,
    onText: (t, a, b) => emitText(t, a, b, "whisper"),
    onError: (m) => dispatch({ type: "notice", level: "warn", message: `Whisper: ${m}` }),
  });

  // ---------------------------------------------------------------- actions
  const start = async () => {
    if (engine !== "typed") {
      const ok = await requestMic();
      if (!ok) return;
    }
    socket.send({ type: "start" });
  };

  const goSlide = useCallback(
    (n: number) => {
      if (!session) return;
      const clamped = Math.max(1, Math.min(session.slides.length, n));
      if (clamped === session.current_slide) return;
      dispatch({ type: "local_slide", n: clamped });
      socket.send({ type: "slide", slide_number: clamped });
    },
    [session, socket],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight" || e.key === "PageDown") goSlide(currentSlide + 1);
      if (e.key === "ArrowLeft" || e.key === "PageUp") goSlide(currentSlide - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goSlide, currentSlide]);

  const end = () => {
    speaker.stop();
    setEnding(true);
    setAnsweringId(null);
    stopMic();
    socket.send({ type: "end" });
  };

  const leave = () => {
    setConfirmLeave(false);
    stopMic();
    onHome();
  };
  // Leaving mid presentation would silently drop the run, so ask first.
  const requestHome = () => {
    if (session?.status === "live") setConfirmLeave(true);
    else leave();
  };

  const changeMode = async (m: Mode) => {
    try {
      await api.setMode(sessionId, m);
      if (session) dispatch({ type: "set_session", session: { ...session, mode: m } });
    } catch (e) {
      dispatch({ type: "notice", level: "warn", message: e instanceof Error ? e.message : "Could not change mode" });
    }
  };

  const restart = async () => {
    await api.restart(sessionId);
    socket.resetOutbox();
    setEnding(false);
    setAnsweringId(null);
    syncClock(0, false);
  };

  // auto-dismiss notices
  useEffect(() => {
    if (!state.notices.length) return;
    const n = state.notices[0];
    const t = window.setTimeout(() => dispatch({ type: "dismiss", id: n.id }), n.level === "error" ? 9000 : 5500);
    return () => window.clearTimeout(t);
  }, [state.notices]);

  const openQuestion = useMemo(() => session?.questions.slice().reverse().find((q) => q.status === "open") ?? null, [session?.questions]);
  useEffect(() => {
    if (answeringId && !session?.questions.some((q) => q.id === answeringId && q.status === "open")) setAnsweringId(null);
  }, [session?.questions, answeringId]);

  // ---------------------------------------------------------------- render
  if (state.fatal) {
    return (
      <div className="center-screen">
        <div className="card narrow">
          <h2>Session unavailable</h2>
          <p>{state.fatal}</p>
          <p className="muted">Sessions live in server memory, so they are cleared when the server restarts or after a period of inactivity.</p>
          <button className="btn primary" onClick={onHome}>Upload a deck</button>
        </div>
      </div>
    );
  }
  if (!session) {
    return (
      <div className="center-screen">
        <span className="spinner" /> <span className="muted">Connecting to your session…</span>
      </div>
    );
  }

  const elapsed = now();
  const slide = session.slides[session.current_slide - 1];
  const whisperAvailable = !!health?.stt.server_whisper;
  const provider = session.provider;

  return (
    <div className="presenter">
      {/* ------------------------------------------------ top bar */}
      <header className="topbar">
        <button className="brand as-button" onClick={requestHome} title="Back to home">
          <Logo />
          <span>PitchMirror</span>
        </button>
        <button className="btn small home-btn" onClick={requestHome} aria-label="Go to home page">← Home</button>
        <div className="deck-name" title={session.filename}>{session.filename}</div>
        <div className="topbar-mid">
          {session.status === "ready" ? (
            <select className="mode-select" value={session.mode} onChange={(e) => changeMode(e.target.value as Mode)} aria-label="Mode">
              {(Object.keys(MODE_INFO) as Mode[]).map((m) => (
                <option key={m} value={m}>{MODE_INFO[m].label} mode</option>
              ))}
            </select>
          ) : (
            <span className="pill pill-accent">{MODE_INFO[session.mode].label} mode</span>
          )}
          <span className={`status status-${session.status}`}>
            {session.status === "live" ? (paused ? "Paused" : "Live") : session.status === "ready" ? "Ready" : "Ended"}
          </span>
          <span className="timer" aria-label="Elapsed time">{fmtTime(elapsed)}</span>
        </div>
        <div className="topbar-right">
          {state.analyzing.length > 0 && (
            <span className="pill pill-muted"><span className="dot pulse" /> Analysing slide {state.analyzing.join(", ")}</span>
          )}
          {session.enrichment.status === "running" && (
            <span className="pill pill-muted" title="A vision model is reading your slides">
              Reading slides {session.enrichment.done}/{session.enrichment.total}
            </span>
          )}
          <span
            className={`pill ${provider.mode === "ai" ? (provider.health.circuit_open ? "pill-warn" : "pill-ok") : "pill-muted"}`}
            title={provider.health.last_error ? `Last error: ${provider.health.last_error}` : ""}
          >
            {provider.mode === "ai" ? (provider.health.circuit_open ? "AI paused, using rules" : provider.model) : "Rule engine"}
          </span>
          <span className={`conn conn-${socket.conn}`} title={`Connection: ${socket.conn}`}>
            {socket.conn === "open" ? "Connected" : socket.conn === "closed" ? "Disconnected" : "Reconnecting…"}
          </span>
        </div>
      </header>

      {/* ------------------------------------------------ main */}
      <main className="stage">
        <SlidePanel
          sessionId={sessionId}
          slide={slide}
          total={session.slides.length}
          analysis={session.analysis[String(slide.slide_number)]}
          videoRef={videoRef}
          camera={live && cameraWanted ? camera : null}
        />
        <TranscriptPanel
          segments={session.segments}
          interim={listening && engine === "browser" ? interim : ""}
          currentSlide={session.current_slide}
          metrics={session.metrics}
          live={live}
          engine={engine}
          answering={answeringId ? session.questions.find((q) => q.id === answeringId) ?? null : null}
          speaking={vad.speaking}
          whisperBusy={whisper.busy}
          onTyped={(text) => {
            const t = now();
            emitText(text, t, t, "typed");
          }}
        />
        <FeedbackPanel
          feedback={session.feedback}
          questions={session.questions}
          currentSlide={session.current_slide}
          questionPending={state.questionPending}
          evaluating={state.evaluating}
          answeringId={answeringId}
          live={live}
          onAnswer={(id) => setAnsweringId(id)}
          onDone={(id, typed) => {
            socket.send({ type: "answer_done", question_id: id, typed_answer: typed });
            setAnsweringId(null);
          }}
          onSpeak={speaker.supported ? (text) => speaker.speak(text) : undefined}
          examinerSpeaking={speaker.speaking}
          onSkip={(id) => {
            socket.send({ type: "skip_question", question_id: id });
            if (answeringId === id) setAnsweringId(null);
          }}
        />

        {session.status === "ready" && (
          <div className="overlay">
            <div className="card start-card">
              <h2>Ready when you are</h2>
              <p>
                <strong>{MODE_INFO[session.mode].label} mode.</strong> Present the way you normally would. Use the arrow keys or the buttons below to
                change slides so PitchMirror knows which slide you're on.
              </p>
              <ul className="tips">
                <li>Explain your charts and diagrams out loud; unexplained visuals are flagged.</li>
                <li>Questions appear on the right. Click <em>Answer</em>, speak, then <em>Done</em>.</li>
                <li>Only the transcript is sent to the server. Raw audio is not stored.</li>
              </ul>
              <div className="engine-row">
                <label>
                  Transcription
                  <select value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
                    <option value="browser" disabled={!browserSpeechSupported()}>Browser (live, Chrome/Edge)</option>
                    <option value="whisper" disabled={!whisperAvailable}>Whisper (server){whisperAvailable ? "" : " – not configured"}</option>
                    <option value="typed">Type instead (no microphone)</option>
                  </select>
                </label>
                {engine === "browser" && (
                  <label>
                    Accent
                    <select value={lang} onChange={(e) => setLang(e.target.value)}>
                      {LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </label>
                )}
              </div>
              <div className="toggle-row">
                <label className="check-label">
                  <input type="checkbox" checked={cameraWanted} onChange={(e) => setCameraWanted(e.target.checked)} />
                  Camera for eye contact (video stays on this device)
                </label>
                {speaker.supported && (
                  <label className="check-label">
                    <input type="checkbox" checked={voiceOn} onChange={(e) => setVoiceOn(e.target.checked)} />
                    Examiner reads questions aloud
                  </label>
                )}
              </div>
              {(mic === "denied" || mic === "no-mic" || mic === "error") && (
                <div className="error-box">
                  {mic === "denied"
                    ? "Microphone permission was blocked. Allow it from the address bar's site settings, then try again, or continue by typing."
                    : "No working microphone was found. Plug one in and try again, or continue by typing."}
                  <button className="btn small" onClick={() => setEngine("typed")}>Continue by typing</button>
                </div>
              )}
              <button className="btn primary big" onClick={start} disabled={mic === "requesting" || socket.conn !== "open"}>
                {mic === "requesting" ? "Waiting for microphone…" : socket.conn !== "open" ? "Connecting…" : "Start presenting"}
              </button>
            </div>
          </div>
        )}

        {(ending || session.status === "ended") && !state.reportReady && (
          <div className="overlay">
            <div className="card start-card">
              <h2><span className="spinner" /> Building your report</h2>
              <p className="muted">Finishing the analysis of every slide you presented and scoring your answers. This usually takes a few seconds.</p>
            </div>
          </div>
        )}
        {session.status === "ended" && state.reportReady && (
          <div className="overlay">
            <div className="card start-card">
              <h2>Presentation finished</h2>
              <div className="row">
                <button className="btn primary" onClick={onReport}>View report</button>
                <button className="btn" onClick={restart}>Practice again</button>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* ------------------------------------------------ controls */}
      <footer className="controls">
        <div className="ctrl-group">
          {live && engine !== "typed" && (
            <button
              className={`mic-btn ${listening ? "on" : ""}`}
              onClick={async () => {
                if (mic !== "on") {
                  const ok = await requestMic();
                  if (ok) setPaused(false);
                } else setPaused((p) => !p);
              }}
              aria-label={listening ? "Pause microphone" : "Resume microphone"}
            >
              <MicIcon />
              <span className="mic-meter" style={{ transform: `scaleY(${listening ? 0.15 + vad.level * 0.85 : 0.1})` }} />
              {listening ? "Listening" : speaker.speaking ? "Examiner speaking" : mic === "on" ? "Paused" : mic === "denied" ? "Mic blocked" : "Enable mic"}
            </button>
          )}
          {live && (
            <button className={`btn small ${cameraWanted ? "toggle-on" : ""}`} onClick={() => setCameraWanted((c) => !c)} aria-pressed={cameraWanted}
              title="Webcam eye contact, analysed on this device">
              {cameraWanted ? "Camera on" : "Camera off"}
            </button>
          )}
          {live && speaker.supported && (
            <button className={`btn small ${voiceOn ? "toggle-on" : ""}`} onClick={() => { if (voiceOn) speaker.stop(); setVoiceOn((v) => !v); }} aria-pressed={voiceOn}
              title="Examiner reads questions aloud">
              {voiceOn ? "Voice on" : "Voice off"}
            </button>
          )}
          {live && engine === "typed" && <span className="muted small">Typing mode: enter your explanation in the transcript box.</span>}
          {live && (
            <select className="engine-inline" value={engine} onChange={(e) => setEngine(e.target.value as Engine)} aria-label="Transcription engine">
              <option value="browser" disabled={!browserSpeechSupported()}>Browser speech</option>
              <option value="whisper" disabled={!whisperAvailable}>Whisper</option>
              <option value="typed">Typing</option>
            </select>
          )}
        </div>
        <div className="ctrl-group slide-nav">
          <button className="btn" onClick={() => goSlide(session.current_slide - 1)} disabled={session.current_slide <= 1} aria-label="Previous slide">‹ Prev</button>
          <span className="slide-count">{session.current_slide} / {session.slides.length}</span>
          <button className="btn" onClick={() => goSlide(session.current_slide + 1)} disabled={session.current_slide >= session.slides.length} aria-label="Next slide">Next ›</button>
        </div>
        <div className="ctrl-group">
          {live && (
            <button
              className="btn"
              onClick={() => socket.send({ type: "ask_question", slide_number: session.current_slide })}
              disabled={state.questionPending || !!openQuestion}
              title={openQuestion ? "Answer or skip the open question first" : "Get a question about this slide"}
            >
              {state.questionPending ? "Thinking…" : "Ask me a question"}
            </button>
          )}
          {live && <button className="btn danger" onClick={end} disabled={ending}>End presentation</button>}
          {session.status === "ended" && <button className="btn" onClick={restart}>Practice again</button>}
        </div>
      </footer>

      {confirmLeave && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="leave-title" onClick={() => setConfirmLeave(false)}>
          <div className="card start-card" onClick={(e) => e.stopPropagation()}>
            <h2 id="leave-title">Leave this presentation?</h2>
            <p className="muted">You're still presenting. End it first to get your report, or leave now and lose this run's report.</p>
            <div className="row">
              <button className="btn primary" onClick={() => { setConfirmLeave(false); end(); }}>End and see report</button>
              <button className="btn danger" onClick={leave}>Leave without report</button>
              <button className="btn ghost" onClick={() => setConfirmLeave(false)}>Keep presenting</button>
            </div>
          </div>
        </div>
      )}

      <div className="toasts" aria-live="polite">
        {state.notices.map((n) => (
          <div key={n.id} className={`toast toast-${n.level}`} onClick={() => dispatch({ type: "dismiss", id: n.id })}>{n.message}</div>
        ))}
      </div>
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0014 0M12 18v3" />
    </svg>
  );
}
