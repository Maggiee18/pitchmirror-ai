import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Options {
  stream: MediaStream | null;
  active: boolean;
  now: () => number;
  voicedRecentRef: React.MutableRefObject<number>;
  onText: (text: string, tStart: number, tEnd: number) => void;
  onError: (msg: string) => void;
}

const CHUNK_MS = 6000;

function pickMime(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  for (const c of candidates) if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(c)) return c;
  return "";
}

/**
 * Server Whisper transcription. Records self-contained ~6s chunks (a fresh MediaRecorder per chunk so every
 * file has headers), skips chunks with no detected voice, and sends them to /api/transcribe. Audio is not stored.
 */
export function useWhisperRecorder({ stream, active, now, voicedRecentRef, onText, onError }: Options) {
  const [busy, setBusy] = useState(false);
  const cb = useRef({ onText, onError, now });
  cb.current = { onText, onError, now };

  useEffect(() => {
    if (!stream || !active || typeof MediaRecorder === "undefined") return;
    let cancelled = false;
    let rec: MediaRecorder | null = null;
    let timer: number | undefined;
    const mime = pickMime();

    const cycle = () => {
      if (cancelled) return;
      const chunks: BlobPart[] = [];
      const tStart = cb.current.now();
      voicedRecentRef.current = 0;
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        const voiced = voicedRecentRef.current;
        const tEnd = cb.current.now();
        cycle(); // start the next chunk immediately
        if (voiced < 0.4) return; // silence: don't transcribe (Whisper invents text on silence)
        const blob = new Blob(chunks, { type: (mime || "audio/webm").split(";")[0] });
        setBusy(true);
        try {
          const text = await api.transcribe(blob);
          if (text) cb.current.onText(text, tStart, tEnd);
        } catch (err) {
          cb.current.onError(err instanceof Error ? err.message : "Transcription failed");
        } finally {
          setBusy(false);
        }
      };
      rec.start();
      timer = window.setTimeout(() => rec && rec.state === "recording" && rec.stop(), CHUNK_MS);
    };
    cycle();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      // stopping flushes the last partial chunk; cycle() will not restart because cancelled is set
      if (rec && rec.state === "recording") rec.stop();
    };
  }, [stream, active, voicedRecentRef]);

  return { busy };
}
