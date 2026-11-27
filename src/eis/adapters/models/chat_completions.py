# SPDX-License-Identifier: Apache-2.0

"""Shared OpenAI-compatible Chat Completions adapter.

Only providers whose official documentation exposes this shape use it:
OpenAI, xAI (legacy Chat Completions) and OpenCode's compatible base.
"""

from __future__ import annotations

from typing import Any

import httpx

from eis.adapters.models.base import (
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    TokenUsage,
)
from eis.config import ProviderConfig


class ChatCompletionsProvider:
    name = "chat_completions"

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

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def _endpoint(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        try:
            response = await self._client.post(
                self._endpoint(),
                headers=self._headers(),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} request failed before a response") from exc

        if response.status_code >= 400:
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"{self.name} returned an unexpected response shape")

        text = _extract_text(body, self.name)
        return CompletionResponse(
            text=text,
            model=_string_or(body.get("model"), self._config.model),
            provider=self.name,
            finish_reason=_optional_string(_first_choice(body).get("finish_reason")),
            usage=_chat_usage(body.get("usage")),
            provider_request_id=_optional_string(body.get("id")),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _first_choice(body: dict[str, Any]) -> dict[str, Any]:
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("provider response did not contain choices[0]") from exc
    if not isinstance(choice, dict):
        raise ProviderError("provider response choice was not an object")
    return choice


def _extract_text(body: dict[str, Any], provider: str) -> str:
    choice = _first_choice(body)
    try:
        message = choice["message"]
        content = message["content"]
    except (KeyError, TypeError) as exc:
        raise ProviderError(
            f"{provider} response did not contain choices[0].message.content"
        ) from exc
    if not isinstance(content, str) or not content:
        raise ProviderError(f"{provider} returned empty or non-text message content")
    return content


def _chat_usage(value: object) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    return TokenUsage(
        input_tokens=_optional_int(value.get("prompt_tokens")),
        output_tokens=_optional_int(value.get("completion_tokens")),
        total_tokens=_optional_int(value.get("total_tokens")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback
