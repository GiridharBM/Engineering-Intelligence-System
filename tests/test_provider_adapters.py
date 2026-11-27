# SPDX-License-Identifier: Apache-2.0

"""Gemini/Anthropic adapters, consent gate and model gateway tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from eis.adapters.models.anthropic import AnthropicProvider
from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    TokenUsage,
)
from eis.adapters.models.gemini import GeminiProvider
from eis.adapters.models.opencode import OpenCodeProvider
from eis.adapters.models.registry import ProviderRegistry
from eis.config import ConsentRecord, ProviderConfig
from eis.core.audit import InMemoryAuditSink, JsonlAuditSink
from eis.core.consent import ConsentGate, ConsentRequired
from eis.core.model_gateway import ModelGateway

FAKE_KEY = "test-only-secret-value"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"


def _config(provider: str, base_url: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        api_key=SecretStr(FAKE_KEY),
        base_url=base_url,
    )


def _request(*, max_tokens: int | None = 32) -> CompletionRequest:
    return CompletionRequest(
        messages=(
            ChatMessage(role="system", content="Be concise."),
            ChatMessage(role="user", content="Hello EIS"),
        ),
        temperature=0.2,
        max_tokens=max_tokens,
    )


def test_gemini_request_and_response_normalization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers["x-goog-api-key"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "responseId": "gemini-request-id",
                "modelVersion": "test-model",
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Hello from Gemini"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 3,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 7,
                },
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GeminiProvider(_config("gemini", GEMINI_BASE), client=client)
            return await provider.complete(_request())

    response = asyncio.run(run())
    assert response.text == "Hello from Gemini"
    assert response.provider == "gemini"
    assert response.finish_reason == "stop"
    assert response.usage == TokenUsage(3, 4, 7)
    assert response.provider_request_id == "gemini-request-id"
    assert captured["url"] == f"{GEMINI_BASE}/models/test-model:generateContent"
    assert captured["api_key"] == FAKE_KEY
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["systemInstruction"] == {"parts": [{"text": "Be concise."}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Hello EIS"}]}]
    assert body["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 32}


def test_gemini_http_error_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "invalid key"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GeminiProvider(_config("gemini", GEMINI_BASE), client=client)
            await provider.complete(_request())

    with pytest.raises(ProviderError) as exc:
        asyncio.run(run())
    assert "HTTP 403" in str(exc.value)
    assert FAKE_KEY not in str(exc.value)


def test_gemini_missing_candidates_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"responseId": "x"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GeminiProvider(_config("gemini", GEMINI_BASE), client=client)
            await provider.complete(_request())

    with pytest.raises(ProviderError, match="candidates"):
        asyncio.run(run())


def test_anthropic_request_and_response_normalization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers["x-api-key"]
        captured["version"] = request.headers["anthropic-version"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "model": "test-model",
                "content": [{"type": "text", "text": "Hello from Claude"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicProvider(_config("anthropic", ANTHROPIC_BASE), client=client)
            return await provider.complete(_request())

    response = asyncio.run(run())
    assert response.text == "Hello from Claude"
    assert response.provider == "anthropic"
    assert response.finish_reason == "stop"
    assert response.usage == TokenUsage(3, 4, 7)
    assert response.provider_request_id == "msg_test"
    assert captured["url"] == f"{ANTHROPIC_BASE}/messages"
    assert captured["api_key"] == FAKE_KEY
    assert captured["version"] == "2023-06-01"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 32
    assert body["system"] == "Be concise."
    assert body["messages"] == [{"role": "user", "content": "Hello EIS"}]


def test_anthropic_requires_max_tokens() -> None:
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicProvider(_config("anthropic", ANTHROPIC_BASE), client=client)
            await provider.complete(_request(max_tokens=None))

    with pytest.raises(ProviderError, match="max_tokens"):
        asyncio.run(run())


def test_anthropic_missing_text_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [], "id": "msg_test"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicProvider(_config("anthropic", ANTHROPIC_BASE), client=client)
            await provider.complete(_request())

    with pytest.raises(ProviderError, match="no text"):
        asyncio.run(run())


def test_registry_creates_adapters_and_refuses_ollama() -> None:
    registry = ProviderRegistry()
    assert registry.create(_config("openai", "https://api.openai.com/v1")).name == "openai"
    assert registry.create(_config("gemini", GEMINI_BASE)).name == "gemini"
    assert registry.create(_config("anthropic", ANTHROPIC_BASE)).name == "anthropic"
    with pytest.raises(Exception, match="Ollama"):
        registry.create(_config("ollama", "http://127.0.0.1:11434"))


def _consent() -> ConsentRecord:
    return ConsentRecord(
        consent_id="cns_test",
        provider="opencode",
        data_classes=frozenset({"C0", "C1"}),
        purpose="tests",
    )


def test_gateway_makes_no_call_without_consent() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            sink = InMemoryAuditSink()
            gateway = ModelGateway(provider, ConsentGate([]), sink)
            with pytest.raises(ConsentRequired):
                await gateway.complete(_request())
            return sink

    sink = asyncio.run(run())
    assert called is False
    assert sink.model_calls[-1].status == "consent_denied"
    assert sink.model_calls[-1].consent_id is None
    assert sink.egress[-1].decision == "deny"
    assert sink.egress[-1].consent_id is None


def test_gateway_records_allowed_call_without_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "req_test",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            sink = InMemoryAuditSink()
            gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)
            response = await gateway.complete(_request())
            return response, sink

    response, sink = asyncio.run(run())
    assert response.text == "ok"
    assert sink.egress[-1].decision == "allow"
    assert sink.egress[-1].consent_id == "cns_test"
    audit = sink.model_calls[-1]
    assert audit.status == "success"
    assert audit.usage == TokenUsage(1, 2, 3)
    assert audit.provider_request_id == "req_test"
    assert len(audit.request_hash) == 64
    assert "Hello EIS" not in str(audit.as_dict())
    assert FAKE_KEY not in str(audit.as_dict())


def test_gateway_records_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            sink = InMemoryAuditSink()
            gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)
            with pytest.raises(ProviderError):
                await gateway.complete(_request())
            return sink

    sink = asyncio.run(run())
    assert sink.model_calls[-1].status == "provider_error"
    assert sink.model_calls[-1].usage is None


def test_c3_is_always_denied() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            gateway = ModelGateway(provider, ConsentGate([_consent()]), InMemoryAuditSink())
            with pytest.raises(ConsentRequired):
                await gateway.complete(_request(), data_class="C3")

    asyncio.run(run())
    assert called is False


def test_jsonl_audit_sink_contains_no_content_or_secret(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    audit_path = tmp_path / "model-audit.jsonl"

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            gateway = ModelGateway(
                provider,
                ConsentGate([_consent()]),
                JsonlAuditSink(audit_path),
            )
            await gateway.complete(_request())

    asyncio.run(run())
    text = audit_path.read_text(encoding="utf-8")
    assert "Hello EIS" not in text
    assert FAKE_KEY not in text
    assert "egress_decision" in text
    assert "model_call" in text


class _CapturingProvider:
    name = "opencode"

    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(text="ok", model="test-model", provider="opencode")


class _ExplodingProvider:
    name = "opencode"

    def __init__(self, message: str) -> None:
        self._message = message

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise RuntimeError(self._message)


class _MetadataProvider:
    name = "opencode"

    def __init__(self, finish_reason: str, provider_request_id: str) -> None:
        self._finish_reason = finish_reason
        self._provider_request_id = provider_request_id

    @property
    def model(self) -> str:
        return "test-model"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            text="ok",
            model="test-model",
            provider="opencode",
            finish_reason=self._finish_reason,
            provider_request_id=self._provider_request_id,
        )


def test_gateway_redacts_before_provider_request() -> None:
    token = "A" * 24
    jwt = "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10
    provider = _CapturingProvider()
    sink = InMemoryAuditSink()
    gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)
    request = CompletionRequest(
        messages=(
            ChatMessage(role="system", content="Be concise."),
            ChatMessage(role="user", content=f"goal Bearer {token} {jwt}"),
        ),
        max_tokens=16,
    )

    response = asyncio.run(gateway.complete(request))

    assert response.text == "ok"
    sent = provider.requests[0].messages[-1].content
    assert token not in sent
    assert jwt not in sent
    assert "[REDACTED:bearer_token]" in sent
    assert "[REDACTED:json_web_token]" in sent
    assert sink.model_calls[-1].request_hash == request.request_hash
    assert token not in str(sink.model_calls[-1].as_dict())
    assert jwt not in str(sink.model_calls[-1].as_dict())


def test_gateway_redacts_unterminated_private_key_body_before_provider() -> None:
    begin = "-----BEGIN " + "RSA PRIVATE KEY-----"
    sentinel = "PEM-BODY-SENTINEL-" + "x" * 20
    provider = _CapturingProvider()
    sink = InMemoryAuditSink()
    gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)
    request = CompletionRequest(
        messages=(ChatMessage(role="user", content=f"goal\n{begin}\n{sentinel}\nmore-body"),),
        max_tokens=16,
    )

    asyncio.run(gateway.complete(request))

    sent = provider.requests[0].messages[-1].content
    assert begin not in sent
    assert sentinel not in sent
    assert "more-body" not in sent
    assert "[REDACTED:private_key]" in sent
    assert sink.model_calls[-1].request_hash == request.request_hash
    assert sentinel not in str(sink.model_calls[-1].as_dict())


def test_provider_http_error_body_is_not_echoed() -> None:
    secret = "AK" + "IA" + "A" * 16

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server", "credential": secret})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenCodeProvider(
                _config("opencode", "https://opencode.ai/inference/openai/v1"),
                client=client,
            )
            sink = InMemoryAuditSink()
            gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)
            with pytest.raises(ProviderError) as raised:
                await gateway.complete(_request())
            return raised.value, sink

    error, sink = asyncio.run(run())
    assert secret not in str(error)
    assert secret not in repr(error)
    assert sink.model_calls[-1].status == "provider_error"
    assert secret not in str(sink.model_calls[-1].as_dict())


def test_unexpected_provider_exception_is_sanitized_without_a_cause() -> None:
    secret = "AK" + "IA" + "A" * 16
    provider = _ExplodingProvider("boom " + secret)
    sink = InMemoryAuditSink()
    gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)

    with pytest.raises(ProviderError) as raised:
        asyncio.run(gateway.complete(_request()))

    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert sink.model_calls[-1].status == "provider_error"
    assert secret not in str(sink.model_calls[-1].as_dict())


def test_audit_drops_credential_shaped_provider_metadata() -> None:
    secret = "AK" + "IA" + "A" * 16
    provider = _MetadataProvider(
        finish_reason=secret,
        provider_request_id="Bearer " + "A" * 24,
    )
    sink = InMemoryAuditSink()
    gateway = ModelGateway(provider, ConsentGate([_consent()]), sink)

    asyncio.run(gateway.complete(_request()))

    audit = sink.model_calls[-1]
    assert audit.finish_reason is None
    assert audit.provider_request_id is None
    assert secret not in str(audit.as_dict())
    assert "Bearer" not in str(audit.as_dict())
