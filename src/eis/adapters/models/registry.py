# SPDX-License-Identifier: Apache-2.0

"""Registry mapping configured provider names to typed adapters."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from eis.adapters.models.anthropic import AnthropicProvider
from eis.adapters.models.base import ModelProvider
from eis.adapters.models.gemini import GeminiProvider
from eis.adapters.models.openai import OpenAIProvider
from eis.adapters.models.opencode import OpenCodeProvider
from eis.adapters.models.xai import XAIProvider
from eis.config import ConfigurationError, ModelProviderName, ProviderConfig

ProviderFactory = Callable[[ProviderConfig, "httpx.AsyncClient | None"], ModelProvider]


def _openai_factory(
    config: ProviderConfig, client: httpx.AsyncClient | None = None
) -> ModelProvider:
    return OpenAIProvider(config, client=client)


def _opencode_factory(
    config: ProviderConfig, client: httpx.AsyncClient | None = None
) -> ModelProvider:
    return OpenCodeProvider(config, client=client)


def _gemini_factory(
    config: ProviderConfig, client: httpx.AsyncClient | None = None
) -> ModelProvider:
    return GeminiProvider(config, client=client)


def _anthropic_factory(
    config: ProviderConfig, client: httpx.AsyncClient | None = None
) -> ModelProvider:
    return AnthropicProvider(config, client=client)


def _xai_factory(config: ProviderConfig, client: httpx.AsyncClient | None = None) -> ModelProvider:
    return XAIProvider(config, client=client)


_DEFAULT_FACTORIES: dict[ModelProviderName, ProviderFactory] = {
    "openai": _openai_factory,
    "opencode": _opencode_factory,
    "gemini": _gemini_factory,
    "anthropic": _anthropic_factory,
    "xai": _xai_factory,
}


class ProviderRegistry:
    """Small explicit registry; no auto-detection and no network access."""

    def __init__(
        self,
        factories: dict[ModelProviderName, ProviderFactory] | None = None,
    ) -> None:
        self._factories = dict(_DEFAULT_FACTORIES)
        if factories:
            self._factories.update(factories)

    @property
    def supported(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def create(
        self,
        config: ProviderConfig,
        client: httpx.AsyncClient | None = None,
    ) -> ModelProvider:
        if config.provider == "ollama":
            raise ConfigurationError("the Ollama client is not implemented in this slice")
        factory = self._factories.get(config.provider)
        if factory is None:
            raise ConfigurationError(f"unsupported provider: {config.provider}")
        provider = factory(config, client)
        if provider.name != config.provider:
            raise ConfigurationError(
                f"provider adapter mismatch: {provider.name} != {config.provider}"
            )
        return provider


def build_provider(
    config: ProviderConfig,
    client: httpx.AsyncClient | None = None,
) -> ModelProvider:
    return ProviderRegistry().create(config, client)
