# SPDX-License-Identifier: Apache-2.0

"""Minimal structured logging primitives with request correlation context."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from collections.abc import Mapping
from typing import Any, cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "eis_correlation_id", default=""
)
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/ -]{0,160}$")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def safe_log_event(event: str, fields: Mapping[str, Any] | None = None) -> str:
    """Serialize only short, explicitly safe scalar fields; drop all other values."""
    record: dict[str, str | int | bool] = {"event": _safe_string(event, 80)}
    correlation = correlation_id_var.get()
    if correlation:
        record["correlation_id"] = correlation
    for key, value in (fields or {}).items():
        if not _SAFE_KEY.fullmatch(key) or key in {"content", "prompt", "response", "secret"}:
            continue
        if isinstance(value, bool | int) or isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
            record[key] = value
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def log_event(logger: logging.Logger, event: str, fields: Mapping[str, Any] | None = None) -> None:
    logger.info(safe_log_event(event, fields))


class CorrelationLoggingMiddleware:
    """Pure ASGI middleware that logs only route metadata and a correlation ID."""

    def __init__(self, app: ASGIApp, logger: logging.Logger) -> None:
        self._app = app
        self._logger = logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"x-correlation-id", b"").decode("ascii", errors="ignore")
        correlation_id = (
            supplied if re.fullmatch(r"[a-f0-9]{32}", supplied) else new_correlation_id()
        )
        token = correlation_id_var.set(correlation_id)
        status = 500

        async def send_with_correlation(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = cast(int, message["status"])
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-correlation-id"
                ]
                response_headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self._app(scope, receive, send_with_correlation)
        finally:
            log_event(
                self._logger,
                "http_request",
                {
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status,
                },
            )
            correlation_id_var.reset(token)


def _safe_string(value: str, limit: int) -> str:
    return "".join(char for char in value[:limit] if char.isalnum() or char in "_.-")
