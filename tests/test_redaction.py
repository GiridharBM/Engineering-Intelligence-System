# SPDX-License-Identifier: Apache-2.0

"""Credential redaction and safe structured logging tests.

All secret-shaped values are synthetic and built at runtime so the repository
privacy guard never sees a real credential.
"""

from __future__ import annotations

import json

import pytest

from eis.core.redaction import redact_text
from eis.core.runtime import _tool_feedback
from eis.core.structured_logging import correlation_id_var, safe_log_event


def _fake_aws_access_key() -> str:
    return "AK" + "IA" + "A" * 16


def _fake_github_token() -> str:
    return "gh" + "p_" + "A" * 40


def _fake_private_key_block() -> str:
    begin = "-----BEGIN " + "RSA PRIVATE KEY-----"
    end = "-----END " + "RSA PRIVATE KEY-----"
    return f"{begin}\nMIIEexamplebody\n{end}"


def _fake_jwt() -> str:
    return "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10


def test_redacts_common_secret_shapes_without_echoing_values() -> None:
    secret = _fake_github_token()
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


def test_redacts_jwt_private_key_and_credential_urls() -> None:
    jwt = _fake_jwt()
    private_key = _fake_private_key_block()
    user = "u53r-marker"
    password = "p4ss-marker"
    text = (
        f"jwt={jwt}\n{private_key}\n"
        f"db=postgres://{user}:{password}@db.internal/app\n"
        "callback=https://example.com/cb?access_token=opaque123&state=ok"
    )

    result = redact_text(text)

    assert jwt not in result
    assert "MIIEexamplebody" not in result
    assert user not in result
    assert password not in result
    assert "opaque123" not in result
    assert "[REDACTED:json_web_token]" in result
    assert "[REDACTED:private_key]" in result
    assert "[REDACTED:url_credentials]" in result
    assert "access_token=[REDACTED:url_query_credential]" in result
    assert "&state=ok" in result


def test_redacts_prefixed_assignments_and_token_prefixes() -> None:
    openai_key = "sk-" + "A" * 24
    aws_secret = "b" * 40
    google_key = "AIza" + "B" * 35
    github_pat = "github_pat_" + "C" * 24
    text = (
        f"OPENAI_API_KEY={openai_key}\n"
        f"AWS_SECRET_ACCESS_KEY={aws_secret}\n"
        f"google={google_key}\n"
        f"github={github_pat}"
    )

    result = redact_text(text)

    for value in (openai_key, aws_secret, google_key, github_pat):
        assert value not in result
    assert "[REDACTED:credential_assignment]" in result
    assert "[REDACTED:google_api_key]" in result
    assert "[REDACTED:github_token]" in result


def test_redacts_bearer_and_basic_headers_without_prose_false_positive() -> None:
    bearer = "A" * 24
    basic = "dXNlcjpwYXNz"  # synthetic base64 user:pass
    result = redact_text(f"Bearer {bearer} Basic {basic}")
    assert bearer not in result
    assert basic not in result
    assert "[REDACTED:bearer_token]" in result
    assert "[REDACTED:basic_auth]" in result
    assert redact_text("Basic authentication is explained here.") == (
        "Basic authentication is explained here."
    )


@pytest.mark.parametrize(
    "label",
    [
        "RSA PRIVATE KEY",
        "EC PRIVATE KEY",
        "OPENSSH PRIVATE KEY",
        "PRIVATE KEY",
    ],
)
def test_unterminated_private_key_redacts_through_end_of_message(label: str) -> None:
    begin = "-----BEGIN " + label + "-----"
    sentinel = "BODY-SENTINEL-" + label.replace(" ", "-")
    text = f"ordinary prose before\n{begin}\n{sentinel}\nmore-key-body"

    result = redact_text(text)

    assert "ordinary prose before" in result
    assert begin not in result
    assert sentinel not in result
    assert "more-key-body" not in result
    assert result.endswith("[REDACTED:private_key]")


def test_complete_private_key_block_preserves_following_prose() -> None:
    block = _fake_private_key_block()
    result = redact_text(f"before\n{block}\nafter")

    assert "before" in result
    assert "after" in result
    assert "MIIEexamplebody" not in result
    assert "[REDACTED:private_key]" in result


def test_malformed_and_unrecognized_inputs_do_not_raise() -> None:
    truncated_pem = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    malformed_jwt = "eyJ" + "a" * 10 + "." + "b" * 10
    opaque_token = "opaque-token-" + "z" * 24

    assert redact_text(truncated_pem) == "[REDACTED:private_key]"
    # Two-segment or novel opaque values may be missed; that is a documented limit.
    assert redact_text(malformed_jwt) == malformed_jwt
    assert redact_text(opaque_token) == opaque_token
    assert redact_text("") == ""
    assert isinstance(redact_text("x" * 10_000), str)


def test_mixed_content_preserves_ordinary_text() -> None:
    token = _fake_github_token()
    result = redact_text(f"Fix the bug; token={token}; see https://example.com/page")
    assert token not in result
    assert result == ("Fix the bug; token=[REDACTED:github_token]; see https://example.com/page")


def test_false_positive_prone_ordinary_text_is_preserved() -> None:
    ordinary = (
        "Basic authentication is explained here. "
        "sk-learn tutorial. "
        "https://example.com/docs?key=value "
        "API key rotation policy. "
        "ordinary project text"
    )
    assert redact_text(ordinary) == ordinary


def test_structured_logs_redact_or_drop_credential_fields() -> None:
    jwt = _fake_jwt()
    token = correlation_id_var.set("a" * 32)
    try:
        parsed = json.loads(
            safe_log_event(
                "http_request",
                {
                    "status": 200,
                    "path": "/cb/" + jwt,
                    "api_key": _fake_aws_access_key(),
                    "authorization": "Bearer " + "A" * 24,
                    "note": "ok",
                },
            )
        )
    finally:
        correlation_id_var.reset(token)
    assert parsed == {
        "correlation_id": "a" * 32,
        "event": "http_request",
        "note": "ok",
        "status": 200,
    }
    assert jwt not in json.dumps(parsed)


def test_safe_log_event_drops_credential_shaped_string_values() -> None:
    token = "A" * 24
    parsed = json.loads(
        safe_log_event(
            "provider_event",
            {"detail": "Bearer " + token, "provider": "opencode", "status": "ok"},
        )
    )
    assert "detail" not in parsed
    assert parsed["provider"] == "opencode"
    assert parsed["status"] == "ok"
    assert token not in json.dumps(parsed)


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


def test_tool_feedback_redacts_before_prompt_reentry() -> None:
    token = "A" * 24
    result = _tool_feedback({"text": "Authorization: Bearer " + token}, 1000)
    assert token not in result
    assert "[REDACTED:bearer_token]" in result
