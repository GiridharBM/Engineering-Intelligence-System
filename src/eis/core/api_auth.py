# SPDX-License-Identifier: Apache-2.0

"""Local API bearer-token storage and constant-time authentication helpers."""

from __future__ import annotations

import hmac
import os
import re
import secrets
from pathlib import Path

TOKEN_BYTES = 32
TOKEN_FILENAME = "api-token"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,128}$")


def load_or_create_token(config_dir: Path) -> str:
    """Load the local token or atomically create one with restrictive permissions."""
    config_dir.mkdir(parents=True, exist_ok=True)
    token_path = config_dir / TOKEN_FILENAME
    if token_path.is_symlink():
        raise ValueError("local API token file is invalid")
    read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(token_path, read_flags)
    except FileNotFoundError:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        try:
            descriptor = os.open(
                token_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            descriptor = os.open(token_path, read_flags)
            with os.fdopen(descriptor, "r", encoding="ascii") as stream:
                token = stream.read().strip()
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(token + "\n")
    else:
        with os.fdopen(descriptor, "r", encoding="ascii") as stream:
            token = stream.read().strip()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("local API token file is invalid")
    if os.name != "nt":
        token_path.chmod(0o600)
    return token


def rotate_token(config_dir: Path) -> str:
    """Replace the local API token without printing or logging the previous value."""
    config_dir.mkdir(parents=True, exist_ok=True)
    token_path = config_dir / TOKEN_FILENAME
    token = secrets.token_urlsafe(TOKEN_BYTES)
    temporary = config_dir / f".{TOKEN_FILENAME}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(token + "\n")
    os.replace(temporary, token_path)
    if os.name != "nt":
        token_path.chmod(0o600)
    return token


def token_matches(expected: str, supplied: str | None) -> bool:
    """Compare a supplied bearer token without timing-sensitive string equality."""
    return supplied is not None and hmac.compare_digest(expected, supplied)
