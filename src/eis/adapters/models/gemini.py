# SPDX-License-Identifier: Apache-2.0

"""Google Gemini generateContent adapter.

Verified against official Google AI documentation on 2026-11-20:
https://ai.google.dev/api/generate-content
https://ai.google.dev/api

Endpoint: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
Auth: x-goog-api-key
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    TokenUsage,
)
from eis.config import ProviderConfig


class GeminiProvider:
    name = "gemini"

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
        model = quote(self._config.model, safe="")
        return f"{self._config.base_url.rstrip('/')}/models/{model}:generateContent"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        contents, system_instruction = _gemini_contents(request.messages)
        payload: dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        generation_config: dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if generation_config:
            payload["generationConfig"] = generation_config

        try:
            response = await self._client.post(
                self._endpoint(),
                headers={
                    "x-goog-api-key": self._config.api_key.get_secret_value(),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("gemini request failed before a response") from exc

        if response.status_code >= 400:
            raise ProviderError(f"gemini returned HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("gemini returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError("gemini returned an unexpected response shape")

        text = _gemini_text(body)
        candidate = _first_candidate(body)
        return CompletionResponse(
            text=text,
            model=_string_or(body.get("modelVersion"), self._config.model),
            provider=self.name,
            finish_reason=_map_finish_reason(candidate.get("finishReason")),
            usage=_gemini_usage(body.get("usageMetadata")),
            provider_request_id=_optional_string(body.get("responseId")),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _gemini_contents(
    messages: tuple[ChatMessage, ...],
) -> tuple[list[dict[str, Any]], str]:
    contents: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        role = "model" if message.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message.content}]})
    return contents, "\n\n".join(system_parts)


def _first_candidate(body: dict[str, Any]) -> dict[str, Any]:
    try:
        candidate = body["candidates"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("gemini response did not contain candidates[0]") from exc
    if not isinstance(candidate, dict):
        raise ProviderError("gemini candidate was not an object")
    return candidate


def _gemini_text(body: dict[str, Any]) -> str:
    candidate = _first_candidate(body)
    content = candidate.get("content")
    if not isinstance(content, dict):
        raise ProviderError("gemini response did not contain candidate content")
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ProviderError("gemini response did not contain content parts")
    texts = [
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]
    ]
    if not texts:
        prompt_feedback = body.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            raise ProviderError("gemini prompt was blocked")
        raise ProviderError("gemini returned no text content")
    return "".join(texts)


def _gemini_usage(value: object) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    return TokenUsage(
        input_tokens=_optional_int(value.get("promptTokenCount")),
        output_tokens=_optional_int(value.get("candidatesTokenCount")),
        total_tokens=_optional_int(value.get("totalTokenCount")),
    )


def _map_finish_reason(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "BLOCKLIST": "content_filter",
        "PROHIBITED_CONTENT": "content_filter",
        "SPII": "content_filter",
    }
    return mapping.get(value, value.lower())


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback
