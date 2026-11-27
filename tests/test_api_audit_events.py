# SPDX-License-Identifier: Apache-2.0

"""Authenticated, content-free and stable audit-event browsing API tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eis.api import create_app
from eis.core.audit_chain import ChainStatus
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase

TOKEN = "T" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PRIVATE_MARKER = "private-approval-diff-marker"
PRIVATE_PATH = "project/private/app.py"


@dataclass
class _Fixture:
    client: TestClient
    store: AuditChainStore
    database: SqliteDatabase


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    data = tmp_path / "eis"
    data.mkdir()
    monkeypatch.setenv("EIS_DATA_DIR", str(data))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")
    monkeypatch.delenv("EIS_MODEL_PROVIDER", raising=False)
    database = SqliteDatabase(data / "db" / "eis.sqlite3")
    store = AuditChainStore(database)
    client = TestClient(create_app(token_provider=lambda: TOKEN), base_url="http://127.0.0.1")
    return _Fixture(client, store, database)


def _append(store: AuditChainStore, index: int) -> None:
    store.append(
        event_type=f"test_event_{index}",
        actor="user",
        subject_type="approval",
        subject_id=f"apr_{index}",
        decision="grant",
        payload={
            "diff_text": PRIVATE_MARKER,
            "path": PRIVATE_PATH,
            "prompt": "private model text marker",
        },
        occurred_at_ms=100 + index,
    )


def test_requires_bearer_token_and_strict_query_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _append(fx.store, 1)
    assert fx.client.get("/api/v1/audit/events").status_code == 401
    assert (
        fx.client.get("/api/v1/audit/events", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    for query in (
        "?limit=0",
        "?limit=101",
        "?limit=one",
        "?foo=bar",
        "?limit=1&limit=2",
        "?cursor=bad",
        "?cursor=!!!!",
    ):
        response = fx.client.get(f"/api/v1/audit/events{query}", headers=AUTH)
        assert response.status_code == 422, (query, response.text)


def test_empty_history_returns_ok_integrity_without_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    response = fx.client.get("/api/v1/audit/events", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None
    assert body["integrity"] == {
        "ok": True,
        "event_count": 0,
        "failed_seq": None,
        "failure": None,
    }


def test_event_list_contains_only_safe_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _append(fx.store, 1)
    response = fx.client.get("/api/v1/audit/events", headers=AUTH)
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert set(item) == {
        "seq",
        "occurred_at_ms",
        "event_type",
        "actor",
        "subject_type",
        "subject_id",
        "decision",
        "prev_hash",
        "event_hash",
    }
    assert item["seq"] == 1
    assert item["event_type"] == "test_event_1"
    assert item["decision"] == "grant"
    for marker in (PRIVATE_MARKER, PRIVATE_PATH, "payload", "private model text marker"):
        assert marker not in response.text


def test_cursor_pages_are_stable_while_new_events_are_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    for index in range(3):
        _append(fx.store, index)
    first = fx.client.get("/api/v1/audit/events?limit=2", headers=AUTH)
    assert first.status_code == 200, first.text
    body = first.json()
    assert [item["seq"] for item in body["items"]] == [1, 2]
    cursor = body["next_cursor"]
    assert cursor and "." in cursor
    _append(fx.store, 99)

    second = fx.client.get(
        "/api/v1/audit/events", params={"cursor": cursor, "limit": 10}, headers=AUTH
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert [item["seq"] for item in body2["items"]] == [3]
    assert body2["next_cursor"] is None
    assert body2["integrity"]["event_count"] == 3


def test_tampered_cursor_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _append(fx.store, 1)
    _append(fx.store, 2)
    first = fx.client.get("/api/v1/audit/events?limit=1", headers=AUTH).json()
    cursor = first["next_cursor"]
    body, signature = cursor.split(".", 1)
    tampered = f"{body[:-1]}A.{signature}" if body[-1] != "A" else f"{body[:-1]}B.{signature}"
    response = fx.client.get("/api/v1/audit/events", params={"cursor": tampered}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_cursor"


def test_cursor_pointing_past_current_chain_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _append(fx.store, 1)
    _append(fx.store, 2)
    cursor = fx.client.get("/api/v1/audit/events?limit=1", headers=AUTH).json()["next_cursor"]
    monkeypatch.setattr(
        AuditChainStore, "status", lambda self: ChainStatus(True, 1, "b" * 64, None, None)
    )
    response = fx.client.get("/api/v1/audit/events", params={"cursor": cursor}, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"] == "stale_cursor"


def test_corrupted_full_chain_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _append(fx.store, 1)
    monkeypatch.setattr(
        AuditChainStore,
        "status",
        lambda self: ChainStatus(False, 1, "b" * 64, "event hash mismatch", 1),
    )
    response = fx.client.get("/api/v1/audit/events", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "audit_chain_invalid"


def test_corrupted_requested_prefix_is_not_hidden_by_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _append(fx.store, 1)
    _append(fx.store, 2)
    first = fx.client.get("/api/v1/audit/events?limit=1", headers=AUTH).json()
    cursor = first["next_cursor"]
    monkeypatch.setattr(
        AuditChainStore,
        "status_for",
        staticmethod(
            lambda events: ChainStatus(False, len(events), "b" * 64, "prefix mismatch", 1)
        ),
    )
    response = fx.client.get("/api/v1/audit/events", params={"cursor": cursor}, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "audit_chain_invalid"
