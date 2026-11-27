# SPDX-License-Identifier: Apache-2.0

"""xAI Grok Chat Completions adapter.

The official xAI documentation marks Chat Completions as a legacy endpoint and
recommends its Responses API for new features. This adapter uses the documented
Chat Completions shape and is explicitly marked legacy in the provider docs.
"""

from __future__ import annotations

from eis.adapters.models.chat_completions import ChatCompletionsProvider


class XAIProvider(ChatCompletionsProvider):
    name = "xai"
