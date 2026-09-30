import { useCallback, useEffect, useRef, useState } from "react";

/** Speaks audience questions aloud with the browser's built-in voices. */
export function useSpeaker(lang: string) {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [speaking, setSpeaking] = useState(false);
  const voiceRef = useRef<SpeechSynthesisVoice | null>(null);

  useEffect(() => {
    if (!supported) return;
    const pick = () => {
      const voices = window.speechSynthesis.getVoices();
      const byLang = voices.filter((v) => v.lang?.toLowerCase().startsWith(lang.toLowerCase().slice(0, 2)));
      voiceRef.current =
        byLang.find((v) => v.lang === lang && /google|natural|online/i.test(v.name)) ||
        byLang.find((v) => v.lang === lang) ||
        byLang.find((v) => /google|natural|online/i.test(v.name)) ||
        byLang[0] ||
        null;
    };
    pick();
    window.speechSynthesis.addEventListener("voiceschanged", pick);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", pick);
  }, [lang, supported]);

  const speak = useCallback(
    (text: string) => {
      if (!supported || !text) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      if (voiceRef.current) u.voice = voiceRef.current;
      u.lang = voiceRef.current?.lang || lang;
      u.rate = 1;
      u.onstart = () => setSpeaking(true);
      u.onend = () => setSpeaking(false);
      u.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(u);
      // safety: never leave the mic paused if onend doesn't fire
      window.setTimeout(() => setSpeaking(false), Math.min(20000, 1500 + text.length * 90));
    },
    [lang, supported],
  );

  const stop = useCallback(() => {
    if (supported) window.speechSynthesis.cancel();
    setSpeaking(false);
  }, [supported]);

  useEffect(() => () => stop(), [stop]);

  return { supported, speaking, speak, stop };
}
