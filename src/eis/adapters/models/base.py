# SPDX-License-Identifier: Apache-2.0

"""Provider-neutral text completion contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError(f"unknown message role: {self.role}")
        if not self.content.strip():
            raise ValueError("message content must not be empty")


@dataclass(frozen=True)
class CompletionRequest:
    messages: tuple[ChatMessage, ...]
    temperature: float | None = 0.1
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("messages must not be empty")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

    @property
    def request_hash(self) -> str:
        payload = {
            "messages": [
                {"role": message.role, "content": message.content} for message in self.messages
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def as_dict(self) -> dict[str, int]:
        result: dict[str, int] = {}
        if self.input_tokens is not None:
            result["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            result["output_tokens"] = self.output_tokens
        if self.total_tokens is not None:
            result["total_tokens"] = self.total_tokens
        return result


@dataclass(frozen=True)
class CompletionResponse:
    text: str
    model: str
    provider: str
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("completion text must not be empty")
        if not self.model or not self.provider:
            raise ValueError("model and provider must not be empty")


class ProviderError(RuntimeError):
    """A sanitized provider failure; callers must not execute on failure."""


class ModelProvider(Protocol):
    name: str

    @property
    def model(self) -> str: ...

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
