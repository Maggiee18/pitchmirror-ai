import { useEffect, useRef, useState } from "react";

interface Options {
  stream: MediaStream | null;
  enabled: boolean;
  onPause: (durationS: number) => void;
  onActivity: (voicedS: number) => void;
}

/**
 * Energy based voice activity detection on the microphone stream. This is what gives us REAL measurements
 * of pauses and speaking time (instead of guessing them from the transcript). Audio never leaves the browser here.
 */
export function useVoiceActivity({ stream, enabled, onPause, onActivity }: Options) {
  const [level, setLevel] = useState(0);
  const [speaking, setSpeaking] = useState(false);
  const cb = useRef({ onPause, onActivity });
  cb.current = { onPause, onActivity };
  const voicedRecentRef = useRef(0); // seconds of voice since last read (used to skip silent Whisper chunks)

  useEffect(() => {
    if (!stream || !enabled) return;
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);

    const TICK = 50;
    let floor = 0.008;
    let voiced = false;
    let lastVoiceAt = performance.now();
    let silenceStart: number | null = null;
    let hadSpeech = false;
    let voicedAcc = 0;
    let uiTick = 0;

    const timer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      const threshold = Math.max(0.012, floor * 2.6);
      const now = performance.now();
      const isVoice = rms > threshold;
      if (!isVoice) floor = floor * 0.98 + rms * 0.02; // adapt to room noise while silent
      if (isVoice) {
        lastVoiceAt = now;
        if (!voiced) {
          voiced = true;
          if (hadSpeech && silenceStart !== null) {
            const d = (now - silenceStart) / 1000;
            if (d >= 1.0) cb.current.onPause(d);
          }
          silenceStart = null;
        }
        hadSpeech = true;
        voicedAcc += TICK / 1000;
        voicedRecentRef.current += TICK / 1000;
      } else if (voiced && now - lastVoiceAt > 250) {
        voiced = false; // 250ms hangover so word gaps are not pauses
        silenceStart = lastVoiceAt;
      }
      if (voicedAcc >= 2) {
        cb.current.onActivity(voicedAcc);
        voicedAcc = 0;
      }
      if (++uiTick % 2 === 0) {
        setLevel(Math.min(1, rms / 0.12));
        setSpeaking(voiced);
      }
    }, TICK);

    return () => {
      window.clearInterval(timer);
      if (voicedAcc > 0) cb.current.onActivity(voicedAcc);
      src.disconnect();
      ctx.close().catch(() => undefined);
      setLevel(0);
      setSpeaking(false);
    };
  }, [stream, enabled]);

  return { level, speaking, voicedRecentRef };
}
