# SPDX-License-Identifier: Apache-2.0

"""Local API token storage and runtime route contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from eis.api import create_app
from eis.core.api_auth import load_or_create_token, rotate_token, token_matches


def test_api_token_is_created_stably_and_can_be_rotated(tmp_path: Path) -> None:
    first = load_or_create_token(tmp_path)
    assert len(first) >= 40
    assert load_or_create_token(tmp_path) == first
    second = rotate_token(tmp_path)
    assert second != first
    assert load_or_create_token(tmp_path) == second
    assert token_matches(second, second)
    assert not token_matches(second, first)
    assert not token_matches(second, None)


def test_invalid_token_file_fails_closed(tmp_path: Path) -> None:
    token_path = tmp_path / "api-token"
    token_path.write_text("short\n", encoding="ascii")
    with pytest.raises(ValueError, match="token file is invalid"):
        load_or_create_token(tmp_path)


def test_runtime_api_routes_are_authenticated_and_strict() -> None:
    app = create_app(token_provider=lambda: "T" * 43)
    schema = app.openapi()
    assert "/api/v1/sessions" in schema["paths"]
    assert "/api/v1/sessions/{session_id}/tasks" in schema["paths"]
    create_schema = schema["components"]["schemas"]["SessionCreate"]
    assert create_schema["additionalProperties"] is False
    secured_paths = {
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}",
        "/api/v1/sessions/{session_id}/close",
        "/api/v1/sessions/{session_id}/tasks",
        "/api/v1/tasks/{task_id}",
        "/api/v1/approvals",
        "/api/v1/approvals/{approval_id}",
        "/api/v1/approvals/{approval_id}/decision",
        "/api/v1/approvals/{approval_id}/execute",
        "/api/v1/audit/verify",
    }
    for route in app.routes:
        if getattr(route, "path", None) not in secured_paths:
            continue
        dependency = route.dependant.dependencies[0].call
        with pytest.raises(HTTPException) as denied:
            dependency(None)
        assert denied.value.status_code == 401
        dependency("Bearer " + "T" * 43)


def test_approval_api_routes_have_strict_digest_bound_contracts() -> None:
    app = create_app(token_provider=lambda: "T" * 43)
    schema = app.openapi()
    assert "/api/v1/approvals/{approval_id}/decision" in schema["paths"]
    assert "/api/v1/approvals/{approval_id}/execute" in schema["paths"]
    decision = schema["components"]["schemas"]["ApprovalDecision"]
    execute = schema["components"]["schemas"]["ApprovalExecution"]
    assert decision["additionalProperties"] is False
    assert execute["additionalProperties"] is False
    assert decision["properties"]["digest"]["pattern"] == "^[0-9a-f]{64}$"
    assert execute["properties"]["digest"]["pattern"] == "^[0-9a-f]{64}$"
