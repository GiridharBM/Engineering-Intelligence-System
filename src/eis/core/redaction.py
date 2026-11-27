# SPDX-License-Identifier: Apache-2.0

"""Best-effort credential redaction for content crossing an egress boundary.

The rules target common credential shapes only: bearer/basic tokens, JWTs,
PEM private-key blocks (unterminated blocks are redacted through end-of-message),
well-known API-key prefixes, environment-style assignments and credential-bearing
URLs. They are deterministic and
narrow to limit false positives, but they are **not** complete secret detection:
novel, encoded, split, truncated or otherwise unusual secrets can pass through.
Consent, data-class limits and the decision to trust a provider remain the
primary controls; this module is defense in depth.
"""

from __future__ import annotations

import re

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
# An unterminated BEGIN marker is treated as extending to end-of-message so no
# key body survives. Complete blocks are redacted first by the pattern above.
_PRIVATE_KEY_UNTERMINATED = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*\Z")

# A URL with userinfo (scheme://user:password@host) in any common scheme.
_URL_CREDENTIALS = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://[^\s/?#@]+:[^\s/?#@]+@[^\s<>\"']+")
# Credential-shaped query parameters; bare `key` is deliberately excluded.
_URL_QUERY_CREDENTIAL = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|signature|sig)=)[^&#\s]+"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", _PRIVATE_KEY_BLOCK),
    ("private_key", _PRIVATE_KEY_UNTERMINATED),
    (
        "json_web_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    (
        "basic_auth",
        re.compile(
            r"\bBasic\s+(?=[A-Za-z0-9+/=]{12,})(?=[A-Za-z0-9+/=]*[A-Z])"
            r"[A-Za-z0-9+/=]{12,}={0,2}"
        ),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?<![?&])\b[A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|id[_-]?token|session[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|secret[_-]?access[_-]?key|private[_-]?key|"
            r"password|passwd|secret)\s*([=:])\s*(['\"]?)[^\s,'\";]+\2"
        ),
    ),
    ("url_credentials", _URL_CREDENTIALS),
)


def redact_text(value: str) -> str:
    """Replace recognized credential shapes with stable, content-free markers.

    Unknown or malformed shapes are returned unchanged rather than guessed at.
    Redaction is idempotent: an existing ``[REDACTED:...]`` marker is stable.
    """
    result = value
    for label, pattern in _PATTERNS:
        result = pattern.sub(f"[REDACTED:{label}]", result)
    return _URL_QUERY_CREDENTIAL.sub(_redact_url_query, result)


def _redact_url_query(match: re.Match[str]) -> str:
    return match.group(1) + "[REDACTED:url_query_credential]"
