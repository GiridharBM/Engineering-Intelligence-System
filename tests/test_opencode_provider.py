# SPDX-License-Identifier: Apache-2.0

"""Deterministic Chat Completions adapter tests for OpenCode, OpenAI and xAI."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from eis.adapters.models.base import ChatMessage, CompletionRequest, ProviderError, TokenUsage
from eis.adapters.models.openai import OpenAIProvider
from eis.adapters.models.opencode import OpenCodeProvider
from eis.adapters.models.xai import XAIProvider
from eis.config import ProviderConfig

FAKE_KEY = "test-only-secret-value"
PROVIDERS = (
    ("opencode", "https://opencode.ai/inference/openai/v1", OpenCodeProvider),
    ("openai", "https://api.openai.com/v1", OpenAIProvider),
    ("xai", "https://api.x.ai/v1", XAIProvider),
)


def _config(provider: str, base_url: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        api_key=SecretStr(FAKE_KEY),
        base_url=base_url,
    )


def _request() -> CompletionRequest:
    return CompletionRequest(
        messages=(ChatMessage(role="user", content="Hello EIS"),),
        temperature=0.2,
        max_tokens=32,
    )


@pytest.mark.parametrize(("provider", "base_url", "adapter"), PROVIDERS)
def test_chat_completions_request_and_response_normalization(
    provider: str, base_url: str, adapter: type
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "req_test",
                "model": "test-model",
                "choices": [
                    {
                        "message": {"content": f"Hello from {provider}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 7,
                },
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            instance = adapter(_config(provider, base_url), client=client)
            return await instance.complete(_request())

    response = asyncio.run(run())
    assert response.text == f"Hello from {provider}"
    assert response.provider == provider
    assert response.model == "test-model"
    assert response.finish_reason == "stop"
    assert response.usage == TokenUsage(3, 4, 7)
    assert response.provider_request_id == "req_test"
    assert captured["url"] == f"{base_url}/chat/completions"
    assert captured["authorization"] == f"Bearer {FAKE_KEY}"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 32
    assert body["messages"] == [{"role": "user", "content": "Hello EIS"}]


@pytest.mark.parametrize(("provider", "base_url", "adapter"), PROVIDERS)
def test_chat_completions_http_error_is_sanitized(
    provider: str, base_url: str, adapter: type
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            instance = adapter(_config(provider, base_url), client=client)
            await instance.complete(_request())

    with pytest.raises(ProviderError) as exc:
        asyncio.run(run())
    assert "HTTP 401" in str(exc.value)
    assert FAKE_KEY not in str(exc.value)
    assert "invalid key" not in str(exc.value)


@pytest.mark.parametrize(("provider", "base_url", "adapter"), PROVIDERS)
def test_chat_completions_missing_choices_is_rejected(
    provider: str, base_url: str, adapter: type
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "test-model"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            instance = adapter(_config(provider, base_url), client=client)
            await instance.complete(_request())

    with pytest.raises(ProviderError, match="choices"):
        asyncio.run(run())


def test_chat_completions_invalid_json_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            instance = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            await instance.complete(_request())

    with pytest.raises(ProviderError, match="invalid JSON"):
        asyncio.run(run())


def test_chat_completions_trailing_slash_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://opencode.ai/inference/openai/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            instance = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1/"),
                client=client,
            )
            return await instance.complete(_request())

    assert asyncio.run(run()).text == "ok"
