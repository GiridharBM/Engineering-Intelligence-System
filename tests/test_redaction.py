# SPDX-License-Identifier: Apache-2.0

"""Credential redaction and safe structured logging tests."""

from __future__ import annotations

import json

from eis.core.redaction import redact_text
from eis.core.structured_logging import correlation_id_var, safe_log_event


def test_redacts_common_secret_shapes_without_echoing_values() -> None:
    secret = "ghp_" + "A" * 40
    text = f"token={secret}; Authorization: Bearer abcdefghijklmnopqrstuvwxyz123"
    result = redact_text(text)
    assert secret not in result
    assert "abcdefghijklmnopqrstuvwxyz123" not in result
    assert "[REDACTED:github_token]" in result
    assert "[REDACTED:bearer_token]" in result


def test_redaction_is_stable_and_preserves_nonsecret_text() -> None:
    assert redact_text("hello API_KEY=abc123456789012345 world") == (
        "hello [REDACTED:credential_assignment] world"
    )
    assert redact_text("ordinary project text") == "ordinary project text"


def test_structured_logs_drop_untrusted_content_and_include_correlation() -> None:
    token = correlation_id_var.set("a" * 32)
    try:
        parsed = json.loads(
            safe_log_event(
                "http_request",
                {"status": 200, "path": "/health", "prompt": "private", "detail": object()},
            )
        )
    finally:
        correlation_id_var.reset(token)
    assert parsed == {
        "correlation_id": "a" * 32,
        "event": "http_request",
        "path": "/health",
        "status": 200,
    }
