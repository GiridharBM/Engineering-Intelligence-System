# SPDX-License-Identifier: Apache-2.0

"""Approval value objects and canonical action binding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

ApprovalStatus = Literal["pending", "approved", "rejected", "consumed"]


class ApprovalError(RuntimeError):
    """Raised when a proposal, approval, or approval consumption is invalid."""


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    task_id: str | None
    action: str
    on_behalf_of: str
    resource: str
    project_root_hash: str
    policy_decision_id: str
    policy_version: str
    expected_sha256: str | None
    new_sha256: str
    diff_sha256: str
    diff_text: str
    new_content: str
    summary: str
    risk_class: str
    request_digest: str
    status: ApprovalStatus
    requested_at_ms: int
    expires_at_ms: int
    decided_at_ms: int | None = None
    consumed_at_ms: int | None = None
    decision_note: str | None = None


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def approval_digest(
    *,
    on_behalf_of: str,
    resource: str,
    project_root_hash: str,
    expected_sha256: str | None,
    new_sha256: str,
    policy_decision_id: str,
    policy_version: str,
    expires_at_ms: int,
    summary: str,
    diff_text: str,
    task_id: str | None = None,
) -> str:
    """Bind the approval to the exact user action, target, diff and expiry."""
    body = {
        "action": "project.write",
        "actor": "user",
        "expected_sha256": expected_sha256,
        "expires_at_ms": expires_at_ms,
        "new_sha256": new_sha256,
        "diff_sha256": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
        "on_behalf_of": on_behalf_of,
        "policy_decision_id": policy_decision_id,
        "policy_version": policy_version,
        "project_root_hash": project_root_hash,
        "resource": resource,
        "risk_class": "A3",
        "summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
    }
    if task_id is not None:
        body["task_id"] = task_id
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
