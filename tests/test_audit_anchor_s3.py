# SPDX-License-Identifier: Apache-2.0

"""S3 Object Lock anchor sink tests with injected fake AWS SDK clients.

No live AWS call, bucket, credential or network access is used. These tests
prove adapter behaviour against mocked S3 responses; they are not evidence of
real independent retention.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from eis.cli import app
from eis.config import ConfigurationError
from eis.core.audit_anchor import (
    AnchorUnavailable,
    AnchorVerificationError,
    AuditAnchor,
    AuditAnchorService,
    new_anchor,
)
from eis.core.audit_anchor_s3 import (
    S3ObjectLockAnchorSink,
    S3ObjectLockConfig,
    load_s3_object_lock_config,
)
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase

PRIVATE_MARKER = "private-project-content-marker"
runner = CliRunner()


class FakeClientError(Exception):
    """Minimal botocore ClientError shape read by the adapter."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)


@dataclass
class StoredVersion:
    version: str
    body: bytes
    metadata: dict[str, str]
    last_modified: datetime
    mode: str
    retain_until: datetime


@dataclass
class FakeS3:
    """In-memory S3 subset with owner, versioning and Object Lock checks."""

    bucket_owner: str = "123456789012"
    versioning_status: str = "Enabled"
    object_lock_enabled: bool = True
    beginning: datetime = field(default_factory=lambda: datetime.now(UTC))
    objects: dict[str, list[StoredVersion]] = field(default_factory=dict)
    put_kwargs: dict[str, Any] = field(default_factory=dict)
    put_calls: int = 0
    get_calls: int = 0
    fail_operation: str | None = None
    fail_code: str = "InternalError"
    fail_message: str = ""
    retention_override: tuple[str, datetime] | None = None
    retention_missing: bool = False

    def _owner(self, kwargs: dict[str, Any]) -> None:
        owner = kwargs.get("ExpectedBucketOwner")
        if owner is not None and owner != self.bucket_owner:
            raise FakeClientError("AccessDenied", "expected owner mismatch")

    def _raise_if_failing(self, operation: str) -> None:
        if self.fail_operation == operation:
            raise FakeClientError(self.fail_code, self.fail_message)

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self._raise_if_failing("head_bucket")
        self._owner(kwargs)
        return {}

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        self._raise_if_failing("get_bucket_versioning")
        self._owner(kwargs)
        return {"Status": self.versioning_status} if self.versioning_status else {}

    def get_object_lock_configuration(self, **kwargs: Any) -> dict[str, Any]:
        self._raise_if_failing("get_object_lock_configuration")
        self._owner(kwargs)
        if not self.object_lock_enabled:
            raise FakeClientError("ObjectLockConfigurationNotFoundError")
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls += 1
        self.put_kwargs = dict(kwargs)
        self._raise_if_failing("put_object")
        self._owner(kwargs)
        body = kwargs["Body"]
        checksum = kwargs.get("ChecksumSHA256")
        if checksum is not None:
            expected = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
            if checksum != expected:
                raise FakeClientError("BadDigest")
        key = kwargs["Key"]
        versions = self.objects.get(key, [])
        if kwargs.get("IfNoneMatch") == "*" and versions:
            raise FakeClientError("PreconditionFailed")
        version = f"v{len(versions) + 1}"
        stored = StoredVersion(
            version=version,
            body=body,
            metadata={str(k).lower(): str(v) for k, v in kwargs.get("Metadata", {}).items()},
            last_modified=self.beginning,
            mode=kwargs["ObjectLockMode"],
            retain_until=kwargs["ObjectLockRetainUntilDate"],
        )
        self.objects.setdefault(key, []).append(stored)
        return {"VersionId": version, "ChecksumSHA256": checksum}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls += 1
        self._raise_if_failing("get_object")
        self._owner(kwargs)
        versions = self.objects.get(kwargs["Key"])
        if not versions:
            raise FakeClientError("NoSuchKey")
        wanted = kwargs.get("VersionId")
        stored = next((item for item in versions if item.version == wanted), None)
        if stored is None:
            if wanted is not None:
                raise FakeClientError("NoSuchVersion")
            stored = versions[-1]
        return {
            "VersionId": stored.version,
            "Body": FakeBody(stored.body),
            "Metadata": dict(stored.metadata),
            "LastModified": stored.last_modified,
        }

    def get_object_retention(self, **kwargs: Any) -> dict[str, Any]:
        self._raise_if_failing("get_object_retention")
        self._owner(kwargs)
        versions = self.objects.get(kwargs["Key"])
        if not versions:
            raise FakeClientError("NoSuchKey")
        wanted = kwargs.get("VersionId")
        stored = next((item for item in versions if item.version == wanted), None)
        if stored is None:
            raise FakeClientError("NoSuchVersion")
        if self.retention_missing:
            return {}
        mode, until = (
            self.retention_override
            if self.retention_override is not None
            else (stored.mode, stored.retain_until)
        )
        return {"Retention": {"Mode": mode, "RetainUntilDate": until}}


def _config(**overrides: object) -> S3ObjectLockConfig:
    values: dict[str, object] = {
        "bucket": "eis-anchor-test",
        "account_id": "123456789012",
        "region": "us-east-1",
        "retention_mode": "COMPLIANCE",
        "retention_days": 30,
    }
    values.update(overrides)
    return S3ObjectLockConfig(**values)  # type: ignore[arg-type]


def _anchor() -> AuditAnchor:
    return new_anchor(up_to_seq=3, head_hash="a" * 64, created_at_ms=1_700_000_000_000)


def test_submit_stores_only_content_free_checkpoint_and_verifies() -> None:
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    anchor = _anchor()

    receipt = sink.submit(anchor)

    assert receipt.external_ref.startswith("ext_")
    assert receipt.external_version == "v1"
    assert sink.verify(anchor, receipt) is True
    stored = fake.objects["eis/audit-anchors/" + anchor.anchor_id + ".json"][0]
    body = json.loads(stored.body)
    assert body == {
        "algorithm": "eis-audit-anchor-v1",
        "anchor_hash": anchor.anchor_hash,
        "anchor_id": anchor.anchor_id,
        "created_at_ms": anchor.created_at_ms,
        "head_hash": anchor.head_hash,
        "up_to_seq": anchor.up_to_seq,
    }
    assert PRIVATE_MARKER not in stored.body.decode("utf-8")
    assert "secret" not in stored.body.decode("utf-8").lower()
    assert stored.mode == "COMPLIANCE"
    assert stored.retain_until >= stored.last_modified + timedelta(days=30) - timedelta(minutes=5)
    assert stored.metadata["eis-body-sha256"] == hashlib.sha256(stored.body).hexdigest()
    assert stored.metadata["eis-anchor-hash"] == anchor.anchor_hash
    assert fake.get_calls >= 2  # upload verification reads object and retention
    assert fake.put_kwargs["IfNoneMatch"] == "*"
    assert fake.put_kwargs["ChecksumAlgorithm"] == "SHA256"
    assert fake.put_kwargs["ExpectedBucketOwner"] == "123456789012"


def test_idempotent_resubmit_reuses_the_same_version() -> None:
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    anchor = _anchor()

    first = sink.submit(anchor)
    second = sink.submit(anchor)

    assert second == first
    assert fake.put_calls == 2  # second write was rejected by IfNoneMatch
    assert len(fake.objects["eis/audit-anchors/" + anchor.anchor_id + ".json"]) == 1


@pytest.mark.parametrize("field_name", ["body", "metadata", "mode", "retention"])
def test_altered_object_or_retention_is_rejected(field_name: str) -> None:
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    anchor = _anchor()
    receipt = sink.submit(anchor)
    stored = fake.objects["eis/audit-anchors/" + anchor.anchor_id + ".json"][0]

    if field_name == "body":
        stored.body = stored.body.replace(b"eis-audit-anchor-v1", b"eis-audit-anchor-v2")
    elif field_name == "metadata":
        stored.metadata["eis-anchor-hash"] = "b" * 64
    elif field_name == "mode":
        stored.mode = "GOVERNANCE"
    else:
        stored.retain_until = stored.last_modified + timedelta(days=1)

    assert sink.verify(anchor, receipt) is False


def test_wrong_receipt_version_is_rejected() -> None:
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    anchor = _anchor()
    receipt = sink.submit(anchor)
    forged = type(receipt)(
        external_ref=receipt.external_ref,
        received_at_ms=receipt.received_at_ms,
        receipt_hash=receipt.receipt_hash,
        external_version="v2",
    )
    assert sink.verify(anchor, forged) is False


def test_missing_object_lock_fails_closed_without_writing() -> None:
    fake = FakeS3(object_lock_enabled=False)
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    with pytest.raises(AnchorUnavailable, match="object_lock_not_enabled"):
        sink.submit(_anchor())
    assert fake.put_calls == 0
    assert fake.objects == {}


def test_disabled_versioning_fails_closed_without_writing() -> None:
    fake = FakeS3(versioning_status="Suspended")
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    with pytest.raises(AnchorUnavailable, match="versioning_not_enabled"):
        sink.submit(_anchor())
    assert fake.put_calls == 0


def test_wrong_bucket_owner_fails_closed_without_writing() -> None:
    fake = FakeS3(bucket_owner="999999999999")
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    with pytest.raises(AnchorUnavailable, match="preflight_failed:accessdenied"):
        sink.submit(_anchor())
    assert fake.put_calls == 0


def test_wrong_bucket_configuration_rejects_an_existing_receipt() -> None:
    fake = FakeS3()
    first = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    anchor = _anchor()
    receipt = first.submit(anchor)
    other = S3ObjectLockAnchorSink(config=_config(bucket="other-anchor-bucket"), s3_client=fake)
    assert other.verify(anchor, receipt) is False


def test_insufficient_retention_is_rejected() -> None:
    fake = FakeS3()
    fake.retention_override = ("COMPLIANCE", datetime.now(UTC) + timedelta(days=1))
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    with pytest.raises(AnchorVerificationError, match="retention_insufficient"):
        sink.submit(_anchor())


def test_missing_retention_is_rejected() -> None:
    fake = FakeS3(retention_missing=True)
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    with pytest.raises(AnchorVerificationError, match="retention_missing"):
        sink.submit(_anchor())


def test_governance_mode_is_recorded_and_verified() -> None:
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(retention_mode="GOVERNANCE"), s3_client=fake)
    anchor = _anchor()
    receipt = sink.submit(anchor)
    stored = fake.objects["eis/audit-anchors/" + anchor.anchor_id + ".json"][0]
    assert stored.mode == "GOVERNANCE"
    assert sink.verify(anchor, receipt) is True


def test_service_errors_fail_closed_without_leaking_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "AK" + "IA" + "A" * 16
    fake = FakeS3(fail_operation="put_object", fail_message=f"credential {marker}")
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    with caplog.at_level("DEBUG"), pytest.raises(AnchorUnavailable) as raised:
        sink.submit(_anchor())
    text = str(raised.value) + caplog.text
    assert marker not in text
    assert "internalerror" in str(raised.value)


def test_verify_returns_false_when_the_service_fails() -> None:
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    anchor = _anchor()
    receipt = sink.submit(anchor)
    fake.fail_operation = "get_object"
    assert sink.verify(anchor, receipt) is False


def test_service_integration_records_content_free_external_version(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    store = AuditChainStore(database)
    for index in range(2):
        store.append(
            event_type="test_event",
            actor="test",
            subject_type="test",
            subject_id=f"t{index}",
            payload={"hash": hashlib.sha256(str(index).encode()).hexdigest()},
            occurred_at_ms=100 + index,
        )
    fake = FakeS3()
    service = AuditAnchorService(
        events=store, sink=S3ObjectLockAnchorSink(config=_config(), s3_client=fake)
    )

    submission = service.create(actor="test")

    assert service.verify(submission) is True
    event = store.event_by_id(f"anchor_{submission.anchor.anchor_id}")
    assert event is not None
    assert event.payload["external_version"] == submission.receipt.external_version
    assert PRIVATE_MARKER not in str(event.payload)
    assert store.status().ok


def test_config_loader_is_explicit_and_has_no_default_retention() -> None:
    assert load_s3_object_lock_config({}) is None
    assert load_s3_object_lock_config({"EIS_ANCHOR_BACKEND": "none"}) is None
    config = load_s3_object_lock_config(
        {
            "EIS_ANCHOR_BACKEND": "s3",
            "EIS_ANCHOR_S3_BUCKET": "eis-anchor-test",
            "EIS_ANCHOR_S3_ACCOUNT_ID": "123456789012",
            "EIS_ANCHOR_S3_REGION": "us-east-1",
            "EIS_ANCHOR_S3_RETENTION_MODE": "compliance",
            "EIS_ANCHOR_S3_RETENTION_DAYS": "30",
            "EIS_ANCHOR_S3_KEY_PREFIX": "/eis/audit-anchors/",
        }
    )
    assert config is not None
    assert config.retention_mode == "COMPLIANCE"
    assert config.retention_days == 30
    assert config.key_prefix == "eis/audit-anchors/"

    missing_days = {
        "EIS_ANCHOR_BACKEND": "s3",
        "EIS_ANCHOR_S3_BUCKET": "eis-anchor-test",
        "EIS_ANCHOR_S3_ACCOUNT_ID": "123456789012",
        "EIS_ANCHOR_S3_REGION": "us-east-1",
        "EIS_ANCHOR_S3_RETENTION_MODE": "COMPLIANCE",
    }
    with pytest.raises(ConfigurationError, match="RETENTION_DAYS"):
        load_s3_object_lock_config(missing_days)


@pytest.mark.parametrize(
    "overrides",
    [
        {"bucket": "Uppercase-Bucket"},
        {"bucket": "bad..bucket"},
        {"account_id": "123"},
        {"region": "not-a-region"},
        {"retention_mode": "PERMANENT"},
        {"retention_days": 0},
        {"retention_days": 36_501},
        {"key_prefix": "../escape"},
    ],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        _config(**overrides)


def test_cli_status_fails_closed_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EIS_ANCHOR_BACKEND", raising=False)
    result = runner.invoke(app, ["anchor", "status"])
    assert result.exit_code == 1
    assert "anchor_backend=none" in result.output


def test_cli_status_reports_safe_fields_and_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EIS_ANCHOR_BACKEND", "s3")
    monkeypatch.setenv("EIS_ANCHOR_S3_BUCKET", "eis-anchor-test")
    monkeypatch.setenv("EIS_ANCHOR_S3_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("EIS_ANCHOR_S3_REGION", "us-east-1")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_MODE", "COMPLIANCE")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_DAYS", "30")
    monkeypatch.setenv("EIS_ANCHOR_S3_KEY_PREFIX", "eis/audit-anchors/")
    fake = FakeS3()
    sink = S3ObjectLockAnchorSink(config=_config(), s3_client=fake)

    def build(config: S3ObjectLockConfig) -> S3ObjectLockAnchorSink:
        assert config.bucket == "eis-anchor-test"
        return sink

    monkeypatch.setattr("eis.cli.build_s3_object_lock_sink", build)
    result = runner.invoke(app, ["anchor", "status"])

    assert result.exit_code == 0
    assert "anchor_backend=s3 configured=true" in result.output
    assert "retention_mode=COMPLIANCE" in result.output
    assert "retention_days=30" in result.output
    assert "preflight=ok" in result.output
    assert "AKIA" not in result.output


def test_cli_status_fails_closed_on_preflight_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIS_ANCHOR_BACKEND", "s3")
    monkeypatch.setenv("EIS_ANCHOR_S3_BUCKET", "eis-anchor-test")
    monkeypatch.setenv("EIS_ANCHOR_S3_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("EIS_ANCHOR_S3_REGION", "us-east-1")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_MODE", "COMPLIANCE")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_DAYS", "30")

    def build(config: S3ObjectLockConfig) -> S3ObjectLockAnchorSink:
        raise AnchorUnavailable("s3_anchor_object_lock_not_enabled")

    monkeypatch.setattr("eis.cli.build_s3_object_lock_sink", build)
    result = runner.invoke(app, ["anchor", "status"])

    assert result.exit_code == 1
    assert "preflight=failed reason=s3_anchor_object_lock_not_enabled" in result.output
