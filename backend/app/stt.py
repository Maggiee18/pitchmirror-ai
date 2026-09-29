"""Optional server-side speech-to-text (Whisper via Groq or OpenAI).

The browser's Web Speech API is the default live transcriber. This provider exists for browsers without it
(Firefox, Safari) or when higher accuracy is preferred. Audio chunks are processed in memory and never stored.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import Settings, get_settings

log = logging.getLogger("pitchmirror.stt")


@dataclass
class WhisperProvider:
    name: str
    url: str
    key: str
    model: str

    async def transcribe(self, audio: bytes, content_type: str, language: str = "en") -> str:
        ext = "webm" if "webm" in content_type else "ogg" if "ogg" in content_type else "mp4" if "mp4" in content_type else "wav"
        files = {"file": (f"chunk.{ext}", audio, content_type or "audio/webm")}
        data = {"model": self.model, "language": language, "response_format": "json", "temperature": "0"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            r = await client.post(self.url, headers={"Authorization": f"Bearer {self.key}"}, files=files, data=data)
            r.raise_for_status()
            return (r.json().get("text") or "").strip()


def get_stt(settings: Optional[Settings] = None) -> Optional[WhisperProvider]:
    s = settings or get_settings()
    choice = s.stt_provider.lower()
    if choice in ("auto", "groq") and s.groq_api_key:
        return WhisperProvider("groq", "https://api.groq.com/openai/v1/audio/transcriptions", s.groq_api_key, s.groq_stt_model)
    if choice in ("auto", "openai") and s.openai_api_key:
        return WhisperProvider("openai", s.openai_base_url.rstrip("/") + "/audio/transcriptions", s.openai_api_key, s.openai_stt_model)
    return None


# Whisper hallucinates these on silence; drop them rather than show a fabricated transcript.
SILENCE_HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", ".", "thanks for watching.",
    "please subscribe.", "subtitles by the amara.org community",
}


def clean_whisper_text(text: str) -> str:
    t = (text or "").strip()
    if t.lower() in SILENCE_HALLUCINATIONS:
        return ""
    return t
