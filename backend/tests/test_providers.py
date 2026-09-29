"""Verifies request shape + response parsing for each real provider using a mocked HTTP transport."""
import asyncio
import json

import httpx
import pytest

from app.agents.llm import LLMError, LLMProvider, build_provider
from app.config import Settings


def make(name, handler, **kw):
    s = Settings(gemini_api_key="g", openai_api_key="o", anthropic_api_key="a", llm_max_retries=1, **kw)
    p = LLMProvider(name, {"gemini": s.gemini_model, "openai": s.openai_model, "anthropic": s.anthropic_model}[name], settings=s)
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return p


def test_gemini_shape():
    def handler(req: httpx.Request):
        body = json.loads(req.content)
        assert req.headers["x-goog-api-key"] == "g"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
    assert asyncio.run(make("gemini", handler).generate_json("sys", "p", images=[b"png"])) == {"ok": True}


def test_openai_shape():
    def handler(req):
        body = json.loads(req.content)
        assert req.headers["authorization"] == "Bearer o"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})
    assert asyncio.run(make("openai", handler).generate_json("sys", "p")) == {"ok": 1}


def test_anthropic_shape():
    def handler(req):
        body = json.loads(req.content)
        assert req.headers["x-api-key"] == "a" and body["system"] == "sys"
        return httpx.Response(200, json={"content": [{"type": "text", "text": 'Here: {"ok": 2}'}]})
    assert asyncio.run(make("anthropic", handler).generate_json("sys", "p")) == {"ok": 2}


def test_retry_then_circuit_breaker():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503, json={})
    p = make("openai", handler)
    for _ in range(3):
        with pytest.raises(LLMError):
            asyncio.run(p.generate_json("s", "p"))
    assert calls["n"] == 6  # 1 retry each
    assert p.health.to_dict()["circuit_open"]
    with pytest.raises(LLMError):
        asyncio.run(p.generate_json("s", "p"))
    assert calls["n"] == 6  # short circuited


def test_auth_error_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401, json={})
    with pytest.raises(LLMError, match="invalid API key"):
        asyncio.run(make("gemini", handler).generate_json("s", "p"))
    assert calls["n"] == 1


def test_malformed_json_is_error():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    with pytest.raises(LLMError):
        asyncio.run(make("openai", handler).generate_json("s", "p"))


def test_provider_selection():
    assert build_provider(Settings(llm_provider="auto", gemini_api_key="", openai_api_key="", anthropic_api_key="")).name == "offline"
    assert build_provider(Settings(llm_provider="auto", gemini_api_key="", openai_api_key="x", anthropic_api_key="")).name == "openai"
    assert build_provider(Settings(llm_provider="anthropic", gemini_api_key="g", openai_api_key="", anthropic_api_key="")).name == "offline"
