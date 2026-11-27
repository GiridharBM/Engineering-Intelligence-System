# SPDX-License-Identifier: Apache-2.0

"""OpenCode-compatible Chat Completions adapter.

OpenCode's documented endpoints differ by plan and model family, so the base
URL is required configuration and is never defaulted.
"""

from __future__ import annotations

from eis.adapters.models.chat_completions import ChatCompletionsProvider


class OpenCodeProvider(ChatCompletionsProvider):
    name = "opencode"
