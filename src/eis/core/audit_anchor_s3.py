# SPDX-License-Identifier: Apache-2.0

"""Optional S3 Object Lock backend for external audit-chain checkpoints.

This adapter stores only the canonical, content-free checkpoint body and
content-free metadata in a pre-existing, Object Lock enabled bucket. It never
creates a bucket, chooses a retention period, or reads/stores credentials:
the boto3 default provider chain resolves credentials, and every S3 call is
sent with ``ExpectedBucketOwner`` so a bucket owned by another account fails
closed.

Retention mode and duration are explicit operator configuration. COMPLIANCE
retention cannot be shortened or deleted before expiry by any user, including
the AWS account root user; GOVERNANCE retention can be bypassed by a principal
holding ``s3:BypassGovernanceRetention``. The adapter never requests a bypass.

The bucket must already have versioning enabled and Object Lock enabled.
Preflight, upload verification and receipt verification fail closed on any
missing or mismatched protection. Tests use injected fake clients; this module
has no live AWS test and no live credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

from eis.config import ConfigurationError
from eis.core.audit_anchor import (
    AnchorReceipt,
    AnchorUnavailable,
    AnchorVerificationError,
    AuditAnchor,
    compute_anchor_hash,
    compute_receipt_hash,
)

DEFAULT_KEY_PREFIX = "eis/audit-anchors/"
MAX_RETENTION_DAYS = 36_500
_MAX_OBJECT_BYTES = 65_536
_RETENTION_TOLERANCE = timedelta(minutes=5)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_ACCOUNT_RE = re.compile(r"^[0-9]{12}$")
_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class S3ObjectLockConfig:
    """Explicit, non-secret Object Lock sink configuration.

    ``account_id`` is the expected bucket owner. ``retention_days`` has no
    default: the operator must choose a duration. ``key_prefix`` is normalized
    to a single trailing slash.
    """

    bucket: str
    account_id: str
    region: str
    retention_mode: Literal["COMPLIANCE", "GOVERNANCE"]
    retention_days: int
    key_prefix: str = DEFAULT_KEY_PREFIX

    def __post_init__(self) -> None:
        mode = self.retention_mode.upper()
        if mode not in {"COMPLIANCE", "GOVERNANCE"}:
            raise ConfigurationError(
                "EIS_ANCHOR_S3_RETENTION_MODE must be COMPLIANCE or GOVERNANCE"
            )
        object.__setattr__(self, "retention_mode", mode)
        if not _valid_bucket(self.bucket):
            raise ConfigurationError(
                "EIS_ANCHOR_S3_BUCKET is not a valid general purpose bucket name"
            )
        if not _ACCOUNT_RE.fullmatch(self.account_id):
            raise ConfigurationError("EIS_ANCHOR_S3_ACCOUNT_ID must be a 12-digit AWS account ID")
        if not _REGION_RE.fullmatch(self.region):
            raise ConfigurationError("EIS_ANCHOR_S3_REGION is not a valid AWS region")
        if not 1 <= self.retention_days <= MAX_RETENTION_DAYS:
            raise ConfigurationError(
                f"EIS_ANCHOR_S3_RETENTION_DAYS must be between 1 and {MAX_RETENTION_DAYS}"
            )
        prefix = self.key_prefix.strip().strip("/")
        if not prefix or len(prefix) > 200 or ".." in prefix or not _PREFIX_RE.fullmatch(prefix):
            raise ConfigurationError("EIS_ANCHOR_S3_KEY_PREFIX is not a safe object key prefix")
        object.__setattr__(self, "key_prefix", prefix + "/")


class S3Client(Protocol):
    """Subset of the boto3 S3 client used by this adapter."""

    def head_bucket(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_bucket_versioning(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object_lock_configuration(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def put_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object_retention(self, **kwargs: Any) -> Mapping[str, Any]: ...


class S3ObjectLockAnchorSink:
    """Retain and verify checkpoints in a pre-existing S3 Object Lock bucket."""

    def __init__(self, *, config: S3ObjectLockConfig, s3_client: S3Client) -> None:
        self._config = config
        self._s3 = s3_client

    def preflight(self) -> None:
        """Fail closed unless the bucket is versioned, locked and owned as expected."""
        self._preflight()

    def submit(self, anchor: AuditAnchor) -> AnchorReceipt:
        if compute_anchor_hash(anchor.body()) != anchor.anchor_hash:
            raise AnchorVerificationError("s3_anchor_hash_mismatch")
        self._preflight()
        body = _canonical_bytes(_checkpoint(anchor))
        metadata = _checkpoint_metadata(anchor, body)
        key = self._key(anchor)
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        retain_until = datetime.now(UTC) + timedelta(days=self._config.retention_days)
        try:
            response = self._s3.put_object(
                Bucket=self._config.bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata=metadata,
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=checksum,
                ObjectLockMode=self._config.retention_mode,
                ObjectLockRetainUntilDate=retain_until,
                IfNoneMatch="*",
                ExpectedBucketOwner=self._config.account_id,
            )
        except AnchorUnavailable:
            raise
        except Exception as exc:
            code = _error_code(exc)
            if code in {"preconditionfailed", "conditionalrequestconflict"}:
                return self._receipt_from_existing(anchor, key, body, metadata)
            raise AnchorUnavailable(f"s3_anchor_put_failed:{code}") from None
        version = _text(response.get("VersionId"))
        if not version or version == "null":
            raise AnchorUnavailable("s3_anchor_version_missing")
        returned_checksum = _text(response.get("ChecksumSHA256"))
        if returned_checksum and not hmac.compare_digest(returned_checksum, checksum):
            raise AnchorVerificationError("s3_anchor_checksum_mismatch")
        return self._receipt_for_version(anchor, key, body, metadata, version)

    def verify(self, anchor: AuditAnchor, receipt: AnchorReceipt) -> bool:
        try:
            if compute_anchor_hash(anchor.body()) != anchor.anchor_hash:
                return False
            self._preflight()
            key = self._key(anchor)
            body = _canonical_bytes(_checkpoint(anchor))
            expected_ref = self._external_ref(
                anchor_hash=anchor.anchor_hash,
                key=key,
                version=receipt.external_version,
            )
            if not hmac.compare_digest(expected_ref, receipt.external_ref):
                return False
            fresh = self._receipt_for_version(
                anchor, key, body, _checkpoint_metadata(anchor, body), receipt.external_version
            )
            return fresh == receipt
        except Exception:
            return False

    def _preflight(self) -> None:
        try:
            self._s3.head_bucket(
                Bucket=self._config.bucket, ExpectedBucketOwner=self._config.account_id
            )
            versioning = self._s3.get_bucket_versioning(
                Bucket=self._config.bucket, ExpectedBucketOwner=self._config.account_id
            )
            if _text(versioning.get("Status")) != "Enabled":
                raise AnchorUnavailable("s3_anchor_versioning_not_enabled")
            lock = self._s3.get_object_lock_configuration(
                Bucket=self._config.bucket, ExpectedBucketOwner=self._config.account_id
            )
            configuration = lock.get("ObjectLockConfiguration")
            if (
                not isinstance(configuration, Mapping)
                or _text(configuration.get("ObjectLockEnabled")) != "Enabled"
            ):
                raise AnchorUnavailable("s3_anchor_object_lock_not_enabled")
        except AnchorUnavailable:
            raise
        except Exception as exc:
            code = _error_code(exc)
            if code == "objectlockconfigurationnotfounderror":
                raise AnchorUnavailable("s3_anchor_object_lock_not_enabled") from None
            raise AnchorUnavailable(f"s3_anchor_preflight_failed:{code}") from None

    def _receipt_from_existing(
        self,
        anchor: AuditAnchor,
        key: str,
        body: bytes,
        metadata: Mapping[str, str],
    ) -> AnchorReceipt:
        try:
            response = self._s3.get_object(
                Bucket=self._config.bucket,
                Key=key,
                ExpectedBucketOwner=self._config.account_id,
            )
        except Exception as exc:
            raise AnchorUnavailable(f"s3_anchor_get_failed:{_error_code(exc)}") from None
        version = _text(response.get("VersionId"))
        if not version or version == "null":
            raise AnchorUnavailable("s3_anchor_version_missing")
        return self._receipt_for_version(anchor, key, body, metadata, version)

    def _receipt_for_version(
        self,
        anchor: AuditAnchor,
        key: str,
        body: bytes,
        metadata: Mapping[str, str],
        version: str,
    ) -> AnchorReceipt:
        try:
            response = self._s3.get_object(
                Bucket=self._config.bucket,
                Key=key,
                VersionId=version,
                ExpectedBucketOwner=self._config.account_id,
            )
        except Exception as exc:
            raise AnchorUnavailable(f"s3_anchor_get_failed:{_error_code(exc)}") from None
        if _text(response.get("VersionId")) != version:
            raise AnchorVerificationError("s3_anchor_version_mismatch")
        reader = getattr(response.get("Body"), "read", None)
        if not callable(reader):
            raise AnchorVerificationError("s3_anchor_object_invalid")
        stored = reader(_MAX_OBJECT_BYTES + 1)
        if not isinstance(stored, bytes) or len(stored) > _MAX_OBJECT_BYTES:
            raise AnchorVerificationError("s3_anchor_object_invalid")
        if not hmac.compare_digest(stored, body):
            raise AnchorVerificationError("s3_anchor_object_mismatch")
        observed_metadata = _metadata(response.get("Metadata"))
        for name, expected in metadata.items():
            if observed_metadata.get(name) != expected:
                raise AnchorVerificationError("s3_anchor_metadata_mismatch")
        last_modified = _utc(response.get("LastModified"))
        if last_modified is None:
            raise AnchorVerificationError("s3_anchor_last_modified_missing")
        retention = self._retention(key, version)
        if _text(retention.get("Mode")) != self._config.retention_mode:
            raise AnchorVerificationError("s3_anchor_retention_mode_mismatch")
        retain_until = _utc(retention.get("RetainUntilDate"))
        if retain_until is None:
            raise AnchorVerificationError("s3_anchor_retention_missing")
        required = (
            last_modified + timedelta(days=self._config.retention_days) - _RETENTION_TOLERANCE
        )
        if retain_until < required:
            raise AnchorVerificationError("s3_anchor_retention_insufficient")
        if retain_until <= datetime.now(UTC):
            raise AnchorVerificationError("s3_anchor_retention_expired")
        received_at_ms = int(last_modified.timestamp() * 1000)
        external_ref = self._external_ref(anchor_hash=anchor.anchor_hash, key=key, version=version)
        return AnchorReceipt(
            external_ref=external_ref,
            received_at_ms=received_at_ms,
            receipt_hash=compute_receipt_hash(
                anchor_hash=anchor.anchor_hash,
                external_ref=external_ref,
                received_at_ms=received_at_ms,
                external_version=version,
            ),
            external_version=version,
        )

    def _retention(self, key: str, version: str) -> Mapping[str, Any]:
        try:
            response = self._s3.get_object_retention(
                Bucket=self._config.bucket,
                Key=key,
                VersionId=version,
                ExpectedBucketOwner=self._config.account_id,
            )
        except Exception as exc:
            code = _error_code(exc)
            if code in {"nosuchobjectlockconfiguration", "nosuchkey", "nosuchversion"}:
                raise AnchorVerificationError("s3_anchor_retention_missing") from None
            raise AnchorUnavailable(f"s3_anchor_retention_failed:{code}") from None
        retention = response.get("Retention")
        if not isinstance(retention, Mapping):
            raise AnchorVerificationError("s3_anchor_retention_missing")
        return retention

    def _key(self, anchor: AuditAnchor) -> str:
        return f"{self._config.key_prefix}{anchor.anchor_id}.json"

    def _external_ref(self, *, anchor_hash: str, key: str, version: str) -> str:
        digest = hashlib.sha256(
            _canonical_bytes(
                {
                    "anchor_hash": anchor_hash,
                    "bucket": self._config.bucket,
                    "key": key,
                    "version": version,
                }
            )
        ).hexdigest()
        return f"ext_{digest[:32]}"


def load_s3_object_lock_config(
    environ: Mapping[str, str] | None = None,
) -> S3ObjectLockConfig | None:
    """Read explicit S3 sink settings; ``None`` means no backend is configured.

    Credentials are deliberately not read here. boto3's default provider chain
    resolves them when the sink is built.
    """
    env = os.environ if environ is None else environ
    backend = env.get("EIS_ANCHOR_BACKEND", "").strip().lower()
    if backend in {"", "none"}:
        return None
    if backend != "s3":
        raise ConfigurationError("EIS_ANCHOR_BACKEND must be 'none' or 's3'")
    prefix = env.get("EIS_ANCHOR_S3_KEY_PREFIX", DEFAULT_KEY_PREFIX)
    raw_days = _required(env, "EIS_ANCHOR_S3_RETENTION_DAYS")
    try:
        days = int(raw_days)
    except ValueError:
        raise ConfigurationError("EIS_ANCHOR_S3_RETENTION_DAYS must be an integer") from None
    return S3ObjectLockConfig(
        bucket=_required(env, "EIS_ANCHOR_S3_BUCKET"),
        account_id=_required(env, "EIS_ANCHOR_S3_ACCOUNT_ID"),
        region=_required(env, "EIS_ANCHOR_S3_REGION"),
        retention_mode=cast(
            Literal["COMPLIANCE", "GOVERNANCE"],
            _required(env, "EIS_ANCHOR_S3_RETENTION_MODE"),
        ),
        retention_days=days,
        key_prefix=prefix,
    )


def build_s3_object_lock_sink(config: S3ObjectLockConfig) -> S3ObjectLockAnchorSink:
    """Create the sink using boto3's default credential provider chain.

    The optional ``s3`` dependency must be installed. No access key, secret or
    session token is accepted as an argument or written to output.
    """
    try:
        boto3 = cast(Any, importlib.import_module("boto3"))
    except ImportError:
        raise AnchorUnavailable("s3_sdk_unavailable") from None
    try:
        session = boto3.Session(region_name=config.region)
        client = session.client("s3")
    except Exception as exc:
        raise AnchorUnavailable(f"s3_client_unavailable:{_error_code(exc)}") from None
    return S3ObjectLockAnchorSink(config=config, s3_client=cast(S3Client, client))


def _checkpoint(anchor: AuditAnchor) -> dict[str, object]:
    return {
        "algorithm": anchor.algorithm,
        "anchor_hash": anchor.anchor_hash,
        "anchor_id": anchor.anchor_id,
        "created_at_ms": anchor.created_at_ms,
        "head_hash": anchor.head_hash,
        "up_to_seq": anchor.up_to_seq,
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _checkpoint_metadata(anchor: AuditAnchor, body: bytes) -> dict[str, str]:
    return {
        "eis-algorithm": anchor.algorithm,
        "eis-anchor-hash": anchor.anchor_hash,
        "eis-anchor-id": anchor.anchor_id,
        "eis-body-sha256": hashlib.sha256(body).hexdigest(),
        "eis-created-at-ms": str(anchor.created_at_ms),
        "eis-head-hash": anchor.head_hash,
        "eis-up-to-seq": str(anchor.up_to_seq),
    }


def _valid_bucket(bucket: str) -> bool:
    if not _BUCKET_RE.fullmatch(bucket):
        return False
    if ".." in bucket or ".-" in bucket or "-." in bucket:
        return False
    return re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}", bucket) is None


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required when EIS_ANCHOR_BACKEND=s3")
    return value


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _metadata(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            result[key.lower()] = item
    return result


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            code = error.get("Code")
            if isinstance(code, str) and _ERROR_CODE_RE.fullmatch(code):
                return code.lower().replace(".", "_").replace("-", "_")
    return "service_error"
