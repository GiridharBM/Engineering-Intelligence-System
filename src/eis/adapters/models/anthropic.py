# SPDX-License-Identifier: Apache-2.0

"""Anthropic Claude Messages adapter.

Verified against official Anthropic documentation on 2026-11-20:
https://docs.anthropic.com/en/api/overview
https://platform.claude.com/docs/en/api/messages/create

Endpoint: POST https://api.anthropic.com/v1/messages
Auth: x-api-key plus anthropic-version: 2023-06-01
"""

from __future__ import annotations

from typing import Any

import httpx

from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    TokenUsage,
)
from eis.config import ProviderConfig

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        config: ProviderConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    @property
    def model(self) -> str:
        return self._config.model

    def _endpoint(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/messages"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.max_tokens is None:
            raise ProviderError("anthropic requires max_tokens for this request")
        if request.temperature is not None and request.temperature > 1.0:
            raise ProviderError("anthropic temperature must be between 0 and 1")
        messages, system = _anthropic_messages(request.messages)
        if not messages:
            raise ProviderError("anthropic requires at least one user or assistant message")
        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        try:
            response = await self._client.post(
                self._endpoint(),
                headers={
                    "x-api-key": self._config.api_key.get_secret_value(),
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("anthropic request failed before a response") from exc

        if response.status_code >= 400:
            raise ProviderError(f"anthropic returned HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("anthropic returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError("anthropic returned an unexpected response shape")

        return CompletionResponse(
            text=_anthropic_text(body),
            model=_string_or(body.get("model"), self._config.model),
            provider=self.name,
            finish_reason=_map_finish_reason(body.get("stop_reason")),
            usage=_anthropic_usage(body.get("usage")),
            provider_request_id=_optional_string(body.get("id")),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _anthropic_messages(
    messages: tuple[ChatMessage, ...],
) -> tuple[list[dict[str, str]], str]:
    result: list[dict[str, str]] = []
    system_parts: list[str] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        result.append({"role": message.role, "content": message.content})
    return result, "\n\n".join(system_parts)


def _anthropic_text(body: dict[str, Any]) -> str:
    content = body.get("content")
    if not isinstance(content, list):
        raise ProviderError("anthropic response did not contain content blocks")
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
    if not texts:
        raise ProviderError("anthropic returned no text content")
    return "".join(texts)


def _anthropic_usage(value: object) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    input_tokens = _optional_int(value.get("input_tokens"))
    output_tokens = _optional_int(value.get("output_tokens"))
    total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
    )


def _map_finish_reason(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    mapping = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
    }
    return mapping.get(value, value.lower())


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback
