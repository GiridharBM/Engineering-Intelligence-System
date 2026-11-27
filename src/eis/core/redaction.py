# SPDX-License-Identifier: Apache-2.0

"""Content redaction for values that may cross a provider boundary."""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\s*([=:])\s*(['\"]?)[^\s,'\";]+\3"
        ),
    ),
)


def redact_text(value: str) -> str:
    """Replace common credential patterns with stable, content-free markers.

    Patterns are deliberately conservative and deterministic. This is a safety
    layer, not a guarantee that arbitrary secrets or personal data are detected.
    """
    result = value
    for label, pattern in _PATTERNS:
        result = pattern.sub(f"[REDACTED:{label}]", result)
    return result
