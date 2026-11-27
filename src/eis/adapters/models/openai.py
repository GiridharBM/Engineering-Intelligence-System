# SPDX-License-Identifier: Apache-2.0

"""OpenAI Chat Completions adapter."""

from __future__ import annotations

from eis.adapters.models.chat_completions import ChatCompletionsProvider


class OpenAIProvider(ChatCompletionsProvider):
    name = "openai"
