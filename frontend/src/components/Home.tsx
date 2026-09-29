import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { browserSpeechSupported } from "../hooks/useBrowserSpeech";
import type { Health, Mode } from "../types";
import { MODE_INFO } from "../types";

const MODES: Mode[] = ["pitch", "viva", "interview", "presentation"];

export default function Home({ onSession }: { onSession: (id: string) => void }) {
  const [mode, setMode] = useState<Mode>("viva");
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [samples, setSamples] = useState<{ name: string; kind: string }[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealthError(true));
    api.samples().then(setSamples).catch(() => undefined);
  }, []);

  const maxMb = health?.limits.max_upload_mb ?? 30;

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    const ext = file.name.toLowerCase().split(".").pop();
    if (ext !== "pdf" && ext !== "pptx") {
      setError(ext === "ppt" ? "Legacy .ppt isn't supported. Save as .pptx or export to PDF." : "Please choose a PDF or PPTX file.");
      return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      setError(`That file is ${(file.size / 1048576).toFixed(1)} MB; the limit is ${maxMb} MB.`);
      return;
    }
    setBusy(`Reading ${file.name}…`);
    try {
      const s = await api.upload(file, mode);
      onSession(s.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
      setBusy(null);
    }
  }

  async function startSample(name: string) {
    setError(null);
    setBusy("Loading the sample deck…");
    try {
      const s = await api.startSample(name, mode);
      onSession(s.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load sample");
      setBusy(null);
    }
  }

  const speechOk = browserSpeechSupported();
  const pdfSample = samples.find((s) => s.kind === "pdf");

  return (
    <div className="home">
      <header className="home-head">
        <div className="brand">
          <Logo />
          <span>PitchMirror</span>
        </div>
        {health && (
          <span className={`pill ${health.llm.mode === "ai" ? "pill-ok" : "pill-muted"}`} title={health.llm.mode === "ai" ? "" : "Add an API key in .env to enable AI analysis"}>
            {health.llm.mode === "ai" ? `AI: ${health.llm.model}` : "Offline rule engine"}
          </span>
        )}
      </header>

      <main className="home-main">
        <section className="hero">
          <h1>Present. Get understood. Get better.</h1>
          <p className="lede">
            PitchMirror looks at your slide and listens to what you say at the same time. It flags when the two don't match,
            spots charts and diagrams you skipped, and asks the questions a real {MODE_INFO[mode].audience.toLowerCase()} would ask.
          </p>
        </section>

        <section className="card setup">
          <h2>1. Choose a mode</h2>
          <div className="mode-grid" role="radiogroup" aria-label="Mode">
            {MODES.map((m) => (
              <button key={m} role="radio" aria-checked={mode === m} className={`mode-card ${mode === m ? "active" : ""}`} onClick={() => setMode(m)}>
                <strong>{MODE_INFO[m].label}</strong>
                <span>{MODE_INFO[m].blurb}</span>
              </button>
            ))}
          </div>

          <h2>2. Add your deck</h2>
          <div
            className={`dropzone ${drag ? "drag" : ""} ${busy ? "busy" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              if (!busy) handleFile(e.dataTransfer.files[0]);
            }}
            onClick={() => !busy && inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
          >
            <input ref={inputRef} type="file" accept=".pdf,.pptx" hidden onChange={(e) => handleFile(e.target.files?.[0] ?? undefined)} />
            {busy ? (
              <div className="busy-row">
                <span className="spinner" /> {busy}
              </div>
            ) : (
              <>
                <div className="drop-title">Drop a PDF or PPTX here, or click to browse</div>
                <div className="drop-sub">Up to {maxMb} MB. Slides are processed on your PitchMirror server and deleted when the session expires.</div>
              </>
            )}
          </div>
          {pdfSample && !busy && (
            <button className="btn-link" onClick={() => startSample(pdfSample.name)}>
              No deck handy? Try the sample viva deck (CrowdSense)
            </button>
          )}
          {error && <div className="error-box" role="alert">{error}</div>}
        </section>

        <section className="checks">
          {healthError && <div className="error-box">Can't reach the PitchMirror server. Is the backend running?</div>}
          {!speechOk && (
            <div className="warn-box">
              This browser has no built in speech recognition. Use Chrome or Edge for live transcription
              {health?.stt.server_whisper ? ", or switch to Whisper on the next screen." : ", or type your explanation instead."}
            </div>
          )}
          {health && health.llm.mode === "offline" && (
            <div className="info-box">
              Running without an AI model: feedback comes from the rule engine (keyword coverage, number checks, visual references).
              Add <code>GEMINI_API_KEY</code> (or OpenAI / Anthropic) to <code>.env</code> for semantic analysis and smarter questions.
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M9 22V10h7a4 4 0 010 8h-7" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M19 22l4-4" stroke="#cfc9ff" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}
