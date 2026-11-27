# SPDX-License-Identifier: Apache-2.0

"""Verify that the built wheel contains the bundled policy and schemas."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "eis/_resources/policies/default.json",
    "eis/_resources/schemas/policy.schema.json",
    "eis/_resources/schemas/policy-decision.schema.json",
    "eis/storage/migrations/versions/0001_provider_persistence.py",
    "eis/storage/migrations/versions/0002_audit_chain.py",
    "eis/storage/migrations/versions/0003_approvals_policy_projection.py",
    "eis/storage/migrations/versions/0004_sessions_tasks.py",
    "eis/storage/migrations/versions/0005_project_roots.py",
    "eis/storage/migrations/versions/0006_task_plans.py",
    "eis/storage/migrations/versions/0007_plan_recovery.py",
    "eis/storage/migrations/versions/0008_task_approval_link.py",
    "eis/storage/migrations/versions/0009_task_cancellation_review.py",
    "eis/core/project_roots.py",
    "eis/core/safe_access.py",
    "eis/core/project_tools.py",
    "eis/core/sandbox.py",
    "eis/core/runtime.py",
    "eis/core/model_envelope.py",
    "eis/core/model_plan.py",
    "eis/core/model_gateway.py",
    "eis/core/api_auth.py",
    "eis/core/task_approval.py",
    "eis/api.py",
    "eis/cli.py",
    "eis/storage/project_roots.py",
    "eis/storage/sessions.py",
    "eis/storage/plans.py",
)


def main() -> int:
    wheels = sorted(Path("dist").glob("*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel in dist/, found {len(wheels)}")
        return 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        print(f"{wheels[0].name}: missing packaged resources: {missing}")
        return 1
    print(f"{wheels[0].name}: packaged resources ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
