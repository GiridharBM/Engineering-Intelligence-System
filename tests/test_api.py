# SPDX-License-Identifier: Apache-2.0

"""API import and generated-OpenAPI baseline tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from eis import __version__
from eis.api import app, create_app

FAKE_KEY = "test-only-secret-value"
OFFICIAL_BASE = "https://opencode.ai/inference/openai/v1"


def _client(application=app) -> TestClient:
    return TestClient(application, base_url="http://127.0.0.1")


def test_health_endpoint() -> None:
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "name": "EIS", "version": __version__}
    assert len(response.headers["x-correlation-id"]) == 32


def test_request_correlation_id_is_preserved_if_valid() -> None:
    response = _client(create_app()).get("/health", headers={"X-Correlation-ID": "a" * 32})
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "a" * 32


def test_request_correlation_id_is_replaced_if_invalid() -> None:
    response = _client(create_app()).get("/health", headers={"X-Correlation-ID": "private"})
    assert len(response.headers["x-correlation-id"]) == 32
    assert response.headers["x-correlation-id"] != "private"


def test_meta_endpoint_reports_safe_defaults() -> None:
    response = _client(create_app()).get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "single-user"
    assert body["model_provider"] is None
    assert body["model_configured"] is False
    assert body["model"] is None
    assert body["project_roots"] == 0
    assert body["allow_cloud"] is False
    assert body["policy_default"] == "deny"
    assert body["policy_version"] == "2026.11-m1a"
    assert len(body["policy_hash"]) == 64


def test_meta_endpoint_reports_configured_provider_without_the_key(monkeypatch) -> None:
    monkeypatch.setenv("EIS_MODEL_PROVIDER", "opencode")
    monkeypatch.setenv("EIS_OPENCODE_BASE_URL", OFFICIAL_BASE)
    monkeypatch.setenv("EIS_OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", FAKE_KEY)

    response = _client(create_app()).get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["model_provider"] == "opencode"
    assert body["model_configured"] is True
    assert body["model"] == "test-model"
    assert body["allow_cloud"] is True
    assert FAKE_KEY not in response.text


def test_meta_endpoint_returns_safe_configuration_error(monkeypatch) -> None:
    monkeypatch.setenv("EIS_MODEL_PROVIDER", "opencode")
    monkeypatch.setenv("EIS_OPENCODE_BASE_URL", "not-a-url")
    monkeypatch.setenv("EIS_OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", FAKE_KEY)

    response = _client(create_app()).get("/api/v1/meta")
    assert response.status_code == 503
    assert response.json()["detail"] == "configuration_unavailable"
    assert FAKE_KEY not in response.text


def test_openapi_is_generated_from_the_application() -> None:
    schema = create_app().openapi()
    assert schema["info"]["title"] == "EIS"
    assert schema["info"]["version"] == __version__
    assert "/health" in schema["paths"]
    assert "/api/v1/meta" in schema["paths"]


def test_openapi_document_is_served() -> None:
    response = _client().get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "EIS"
