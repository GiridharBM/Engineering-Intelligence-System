# SPDX-License-Identifier: Apache-2.0

"""M1 acceptance: a real process kill mid-plan resumes without a duplicate read.

The first worker runs the plan from the durable task, reaches the read-only
tool and blocks. The test kills that OS process. A second worker resumes the
interrupted plan and completes it. The tool counter proves the read body ran
exactly once; a third resume is a no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from eis.config import ConsentRecord
from eis.core.policy_service import PolicyService
from eis.core.project_tools import ReadOnlyProjectTools
from eis.core.runtime import SessionTaskRuntime
from eis.policy import Policy, PolicyEngine
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.plans import PlanStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

POLICY = {
    "version": "m1-process-kill-v1",
    "default": "deny",
    "rules": [
        {
            "id": "run",
            "principal": "agent:eis.primary",
            "action": "task.run",
            "resource": "project:demo",
            "effect": "allow",
        },
        {
            "id": "read",
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "path:demo/app.py",
            "effect": "allow",
        },
    ],
}

WORKER = r"""
import asyncio
import json
import os
import time
from pathlib import Path

from eis.adapters.models.base import CompletionResponse, TokenUsage
from eis.core.consent import ConsentGate
from eis.core.model_gateway import ModelGateway
from eis.core.policy_service import PolicyService
from eis.core.project_tools import ReadOnlyProjectTools
from eis.core.runtime import SessionTaskRuntime
from eis.policy import Policy, PolicyEngine
from eis.storage.audit import SqliteAuditSink
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.plans import PlanStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

PLAN = json.dumps(
    {
        "kind": "plan",
        "steps": [
            {
                "id": "read",
                "tool": "project.read_text",
                "arguments": {"path": "app.py"},
                "depends_on": [],
            }
        ],
    },
    separators=(",", ":"),
)

class FakeProvider:
    name = "openai"
    model = "test-model"

    async def complete(self, request):
        return CompletionResponse(
            text=PLAN,
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(1, 1, 1),
        )

database = SqliteDatabase(Path(os.environ["M1_DB"]))
sessions = SessionTaskStore(database)
roots = ProjectRootStore(database)
policy = PolicyService(
    PolicyEngine(
        Policy.from_dict(
            json.loads(Path(os.environ["M1_POLICY"]).read_text(encoding="utf-8"))
        )
    ),
    SqlitePolicyDecisionSink(database),
)
audit = AuditChainStore(database)
tools = ReadOnlyProjectTools(project_roots=roots, policy=policy, audit=audit)
plans = PlanStore(database)
gateway = ModelGateway(
    FakeProvider(),
    ConsentGate(lookup=ConsentStore(database)),
    SqliteAuditSink(database),
)
runtime = SessionTaskRuntime(
    sessions, policy, gateway, project_tools=tools, plans=plans
)

original_invoke = tools.invoke
blocking = os.environ.get("M1_BLOCKING") == "1"
marker = Path(os.environ["M1_MARKER"])
counter = Path(os.environ["M1_COUNTER"])

def counted_invoke(*args, **kwargs):
    if blocking:
        marker.write_text("started", encoding="utf-8")
        time.sleep(60)
    with counter.open("a", encoding="utf-8") as handle:
        handle.write("invoke\n")
    return original_invoke(*args, **kwargs)

tools.invoke = counted_invoke
if os.environ["M1_MODE"] == "run":
    asyncio.run(runtime.run_plan(os.environ["M1_TASK"]))
else:
    asyncio.run(runtime.resume_plan(os.environ["M1_TASK"]))
"""


def _fixture(tmp_path: Path) -> tuple[SqliteDatabase, SessionTaskStore, PlanStore, str, str]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("stable-content\n", encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    roots = ProjectRootStore(database)
    roots.register("demo", root, actor="test")
    ConsentStore(database).grant(
        ConsentRecord(
            consent_id="cns_process_kill",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),
            purpose="m1 process kill",
        ),
        actor="test",
    )
    policy = PolicyService(
        PolicyEngine(Policy.from_dict(POLICY)), SqlitePolicyDecisionSink(database)
    )
    audit = AuditChainStore(database)
    tools = ReadOnlyProjectTools(project_roots=roots, policy=policy, audit=audit)
    plans = PlanStore(database)
    runtime = SessionTaskRuntime(sessions, policy, None, project_tools=tools, plans=plans)
    session = runtime.create_session(title="process-kill", project_id="demo")
    task = runtime.create_task(session.session_id, "read app.py")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
    return database, sessions, plans, task.task_id, str(policy_path)


def _wait_for_marker(marker: Path, process: subprocess.Popen[bytes], *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return
        if process.poll() is not None:
            raise AssertionError(f"worker exited before the marker with code {process.returncode}")
        time.sleep(0.05)
    raise AssertionError("worker did not reach the tool invocation before the timeout")


def _worker_env(
    database: SqliteDatabase,
    policy_path: str,
    task_id: str,
    marker: Path,
    counter: Path,
    *,
    mode: str,
    blocking: bool,
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "M1_DB": str(database.path),
            "M1_POLICY": policy_path,
            "M1_TASK": task_id,
            "M1_MARKER": str(marker),
            "M1_COUNTER": str(counter),
            "M1_MODE": mode,
            "M1_BLOCKING": "1" if blocking else "0",
        }
    )
    return env


def test_real_process_kill_then_resume_completes_without_duplicate_read(
    tmp_path: Path,
) -> None:
    database, sessions, plans, task_id, policy_path = _fixture(tmp_path)
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    marker = tmp_path / "marker"
    counter = tmp_path / "counter"

    blocking = subprocess.Popen(
        [sys.executable, str(worker)],
        cwd=tmp_path,
        env=_worker_env(
            database,
            policy_path,
            task_id,
            marker,
            counter,
            mode="run",
            blocking=True,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_marker(marker, blocking, timeout=15.0)
    finally:
        blocking.kill()
        blocking.wait(timeout=5)
    assert blocking.poll() is not None
    assert not counter.exists()

    resume_env = _worker_env(
        database,
        policy_path,
        task_id,
        marker,
        counter,
        mode="resume",
        blocking=False,
    )
    resumed = subprocess.run(
        [sys.executable, str(worker)],
        cwd=tmp_path,
        env=resume_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert counter.read_text(encoding="utf-8").splitlines() == ["invoke"]

    task = sessions.get_task(task_id)
    plan = plans.get_plan_for_task(task_id)
    assert task is not None and task.state == "completed"
    assert plan is not None and plan.state == "completed"
    assert plans.list_steps(plan.plan_id)[0].status == "completed"
    assert AuditChainStore(database).status().ok
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM egress_decisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1

    repeated = subprocess.run(
        [sys.executable, str(worker)],
        cwd=tmp_path,
        env=resume_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert counter.read_text(encoding="utf-8").splitlines() == ["invoke"]
    assert sessions.get_task(task_id).state == "completed"  # type: ignore[union-attr]
