"""LLM provider abstraction.

Primary providers: Gemini, OpenAI (or any OpenAI compatible endpoint), Anthropic. All return parsed JSON.
If no key is configured, or the provider keeps failing, callers fall back to deterministic logic, so the
app always stays runnable. A tiny circuit breaker stops us from hammering a failing API during a live demo.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from ..config import Settings, get_settings

log = logging.getLogger("pitchmirror.llm")


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


def extract_json(text: str) -> dict:
    """Parse a JSON object from a model response, tolerating code fences and leading prose."""
    if not text:
        raise LLMError("empty model response")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("model response did not contain JSON")
        try:
            obj = json.loads(t[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"malformed JSON from model: {exc}") from exc
    if not isinstance(obj, dict):
        raise LLMError("model JSON was not an object")
    return obj


@dataclass
class ProviderHealth:
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    calls: int = 0
    failures: int = 0
    total_latency_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "avg_latency_s": round(self.total_latency_s / max(self.calls - self.failures, 1), 2),
            "last_error": self.last_error,
            "circuit_open": time.time() < self.open_until,
        }


@dataclass
class LLMProvider:
    name: str
    model: str
    supports_vision: bool = True
    settings: Settings = field(default_factory=get_settings)
    health: ProviderHealth = field(default_factory=ProviderHealth)
    _client: Optional[httpx.AsyncClient] = None
    _thinking_opts: Optional[list] = None

    @property
    def available(self) -> bool:
        return self.name != "offline"

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=8.0))
        return self._client

    async def generate_json(
        self,
        system: str,
        prompt: str,
        images: Optional[list[bytes]] = None,
        max_tokens: int = 1200,
        timeout: Optional[float] = None,
        temperature: float = 0.3,
    ) -> dict:
        if not self.available:
            raise LLMUnavailable("no LLM provider configured")
        now = time.time()
        if now < self.health.open_until:
            raise LLMUnavailable(f"{self.name} temporarily disabled after repeated failures: {self.health.last_error}")
        timeout = timeout or self.settings.llm_timeout_seconds
        attempts = 1 + max(0, self.settings.llm_max_retries)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            t0 = time.time()
            self.health.calls += 1
            try:
                text = await asyncio.wait_for(
                    self._call(system, prompt, images or [], max_tokens, temperature), timeout=timeout
                )
                result = extract_json(text)
                self.health.total_latency_s += time.time() - t0
                self.health.consecutive_failures = 0
                return result
            except (httpx.HTTPStatusError, httpx.TransportError, asyncio.TimeoutError, LLMError) as exc:
                last_exc = exc
                self.health.failures += 1
                msg = _describe(exc)
                self.health.last_error = msg
                log.warning("LLM call failed (%s attempt %d/%d): %s", self.name, attempt + 1, attempts, msg)
                retryable = isinstance(exc, (asyncio.TimeoutError, httpx.TransportError, LLMError)) or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (408, 429, 500, 502, 503, 504)
                )
                if not retryable:
                    break
                await asyncio.sleep(0.6 * (attempt + 1))
        self.health.consecutive_failures += 1
        if self.health.consecutive_failures >= 3:
            self.health.open_until = time.time() + 60
            log.error("LLM circuit opened for 60s after repeated failures")
        raise LLMError(_describe(last_exc) if last_exc else "unknown LLM failure")

    async def _call(self, system: str, prompt: str, images: list[bytes], max_tokens: int, temperature: float) -> str:
        if self.name == "gemini":
            return await self._gemini(system, prompt, images, max_tokens, temperature)
        if self.name == "openai":
            return await self._openai(system, prompt, images, max_tokens, temperature)
        if self.name == "anthropic":
            return await self._anthropic(system, prompt, images, max_tokens, temperature)
        raise LLMUnavailable(self.name)

    async def _gemini(self, system, prompt, images, max_tokens, temperature) -> str:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for img in images:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img).decode()}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        gemini3 = self.model.startswith("gemini-3")
        while True:
            gen_cfg: dict[str, Any] = {"responseMimeType": "application/json", "temperature": temperature}
            thinking = self._gemini_thinking_options()[0] if self._gemini_thinking_options() else None
            if "2.5-flash" in self.model:
                gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}  # latency matters more than deep thinking here
            elif gemini3 and thinking:
                # Gemini 3 thinks by default and thinking tokens share maxOutputTokens: without this the JSON gets cut off.
                gen_cfg["thinkingConfig"] = {"thinkingLevel": thinking}
            if gemini3:
                gen_cfg["temperature"] = 1.0  # Google advises 1.0 for Gemini 3; lower values can loop
            gen_cfg["maxOutputTokens"] = max_tokens + (4096 if gemini3 else 0)
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": gen_cfg,
            }
            r = await self.client().post(url, json=body, headers={"x-goog-api-key": self.settings.gemini_api_key})
            if r.status_code == 400 and "thinkingConfig" in gen_cfg and "think" in r.text.lower() and self._gemini_thinking_options():
                log.warning("Gemini rejected thinking level %r, trying the next option", thinking)
                self._thinking_opts = self._gemini_thinking_options()[1:]
                continue
            r.raise_for_status()
            data = r.json()
            try:
                cand = data["candidates"][0]
                text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
            except (KeyError, IndexError) as exc:
                raise LLMError(f"unexpected Gemini response: {str(data)[:200]}") from exc
            if cand.get("finishReason") == "MAX_TOKENS":
                log.warning("Gemini hit the output token limit (usage %s)", data.get("usageMetadata"))
            return text

    def _gemini_thinking_options(self) -> list[str]:
        if not hasattr(self, "_thinking_opts") or self._thinking_opts is None:
            self._thinking_opts = ["minimal", "low"]
        return self._thinking_opts

    async def _openai(self, system, prompt, images, max_tokens, temperature) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode()}})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = await self.client().post(
            self.settings.openai_base_url.rstrip("/") + "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
        )
        r.raise_for_status()
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected OpenAI response: {str(data)[:200]}") from exc

    async def _anthropic(self, system, prompt, images, max_tokens, temperature) -> str:
        content: list[dict[str, Any]] = []
        for img in images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()}})
        content.append({"type": "text", "text": prompt + "\n\nRespond with a single JSON object only."})
        body = {
            "model": self.model,
            "system": system,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": content}],
        }
        r = await self.client().post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={"x-api-key": self.settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def public_info(self) -> dict:
        return {
            "name": self.name,
            "model": self.model if self.available else None,
            "vision": self.supports_vision and self.available,
            "mode": "ai" if self.available else "offline",
            "health": self.health.to_dict(),
        }


def _describe(exc: Exception | None) -> str:
    if exc is None:
        return "unknown error"
    if isinstance(exc, asyncio.TimeoutError):
        return "model timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = {401: "invalid API key", 403: "API key not permitted", 404: "model not found", 429: "rate limited"}.get(code, "")
        return f"HTTP {code}{' (' + hint + ')' if hint else ''}"
    return str(exc)[:200] or exc.__class__.__name__


_provider: Optional[LLMProvider] = None


def build_provider(settings: Settings) -> LLMProvider:
    choice = settings.llm_provider.lower().strip()
    keys = {
        "gemini": (settings.gemini_api_key, settings.gemini_model),
        "openai": (settings.openai_api_key, settings.openai_model),
        "anthropic": (settings.anthropic_api_key, settings.anthropic_model),
    }
    if choice in keys:
        key, model = keys[choice]
        if key:
            return LLMProvider(choice, model, settings=settings)
        log.warning("LLM_PROVIDER=%s but no API key set; running offline", choice)
        return LLMProvider("offline", "", supports_vision=False, settings=settings)
    if choice == "offline":
        return LLMProvider("offline", "", supports_vision=False, settings=settings)
    for name in ("gemini", "openai", "anthropic"):
        key, model = keys[name]
        if key:
            return LLMProvider(name, model, settings=settings)
    return LLMProvider("offline", "", supports_vision=False, settings=settings)


def get_llm() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = build_provider(get_settings())
        log.info("LLM provider: %s %s", _provider.name, _provider.model)
    return _provider


def set_llm(provider: LLMProvider) -> None:
    """Used by tests to inject a fake provider."""
    global _provider
    _provider = provider
