# SPDX-License-Identifier: Apache-2.0

"""Model provider contracts, adapters and registry."""

from eis.adapters.models.anthropic import AnthropicProvider
from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ModelProvider,
    ProviderError,
    TokenUsage,
)
from eis.adapters.models.gemini import GeminiProvider
from eis.adapters.models.openai import OpenAIProvider
from eis.adapters.models.opencode import OpenCodeProvider
from eis.adapters.models.registry import ProviderRegistry, build_provider
from eis.adapters.models.xai import XAIProvider

__all__ = [
    "AnthropicProvider",
    "ChatMessage",
    "CompletionRequest",
    "CompletionResponse",
    "GeminiProvider",
    "ModelProvider",
    "OpenAIProvider",
    "OpenCodeProvider",
    "ProviderError",
    "ProviderRegistry",
    "TokenUsage",
    "XAIProvider",
    "build_provider",
]
