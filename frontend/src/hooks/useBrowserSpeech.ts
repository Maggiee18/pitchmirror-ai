import { useCallback, useEffect, useRef, useState } from "react";

/* Minimal typings for the Web Speech API (not in lib.dom for all TS versions). */
interface SRAlternative { transcript: string }
interface SRResult { isFinal: boolean; 0: SRAlternative; length: number }
interface SREvent { resultIndex: number; results: { length: number; [i: number]: SRResult } }
interface SRErrorEvent { error: string }
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SREvent) => void) | null;
  onerror: ((e: SRErrorEvent) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

function getCtor(): (new () => SpeechRecognitionLike) | null {
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition || w.webkitSpeechRecognition || null) as (new () => SpeechRecognitionLike) | null;
}

export const browserSpeechSupported = () => getCtor() !== null;

interface Options {
  active: boolean;
  lang: string;
  now: () => number; // seconds since presentation start
  onFinal: (text: string, tStart: number, tEnd: number) => void;
  onError: (kind: "denied" | "no-mic" | "network" | "other", detail: string) => void;
}

/**
 * Live transcription with the browser's speech recognizer (Chrome/Edge). Automatically restarts after the
 * recognizer times out, which Chrome does after silence or ~60s.
 */
export function useBrowserSpeech({ active, lang, now, onFinal, onError }: Options) {
  const [interim, setInterim] = useState("");
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const cb = useRef({ onFinal, onError, now });
  cb.current = { onFinal, onError, now };
  const utterStart = useRef<number | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  const failures = useRef(0);

  const stop = useCallback(() => {
    const r = recRef.current;
    recRef.current = null;
    if (r) {
      r.onend = null;
      try {
        r.abort();
      } catch {
        /* already stopped */
      }
    }
    setInterim("");
  }, []);

  useEffect(() => {
    const Ctor = getCtor();
    if (!active || !Ctor) {
      stop();
      return;
    }
    let restartTimer: number | undefined;
    const start = () => {
      const rec = new Ctor();
      rec.lang = lang;
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (e) => {
        failures.current = 0;
        let interimText = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          const text = res[0].transcript.trim();
          if (utterStart.current === null && text) utterStart.current = cb.current.now();
          if (res.isFinal) {
            if (text) cb.current.onFinal(text, utterStart.current ?? cb.current.now(), cb.current.now());
            utterStart.current = null;
          } else {
            interimText += text + " ";
          }
        }
        setInterim(interimText.trim());
      };
      rec.onerror = (e) => {
        if (e.error === "no-speech" || e.error === "aborted") return;
        if (e.error === "not-allowed" || e.error === "service-not-allowed") {
          activeRef.current = false;
          cb.current.onError("denied", e.error);
        } else if (e.error === "audio-capture") {
          activeRef.current = false;
          cb.current.onError("no-mic", e.error);
        } else if (e.error === "network") {
          failures.current += 1;
          cb.current.onError("network", "Speech service unreachable, retrying…");
        } else {
          failures.current += 1;
          cb.current.onError("other", e.error);
        }
      };
      rec.onend = () => {
        setInterim("");
        utterStart.current = null;
        if (activeRef.current && recRef.current === rec) {
          const delay = Math.min(5000, 150 * 2 ** failures.current);
          restartTimer = window.setTimeout(() => {
            if (activeRef.current && recRef.current === rec) start();
          }, delay);
        }
      };
      recRef.current = rec;
      try {
        rec.start();
      } catch {
        /* start() throws if already started; onend will retry */
      }
    };
    start();
    return () => {
      window.clearTimeout(restartTimer);
      stop();
    };
  }, [active, lang, stop]);

  return { interim };
}
