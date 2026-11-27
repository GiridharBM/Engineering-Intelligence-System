# M1 Secure Agent Runtime — Acceptance Matrix

Date: 2026-11-27
Source: [EIS_IMPLEMENTATION_ROADMAP.md](EIS_IMPLEMENTATION_ROADMAP.md#m1--secure-agent-runtime)
Scope rule: only M1 acceptance criteria and the deliverables/tests/security controls explicitly listed for M1 are gates. Delegation is **M5**; SSE, dashboard and UI work are **M10**; evidence-artifact persistence is later **M4/M7**. Operator/admin-owned evidence is listed separately and is not silently counted as verified.

## Status legend

- **Verified** — implemented and exercised by automated tests or hosted CI evidence.
- **Externally unverified** — implemented, but requires operator/admin action this repository cannot perform.
- **Incomplete** — not implemented or not demonstrated.
- **Later milestone** — explicitly outside M1.

## 1. M1 acceptance criteria

| ID | Criterion | Status | Evidence |
|---|---|---|---|
| A1 | A scripted fake-provider task plans, calls a read-only tool, requests approval for a write, is approved, executes, records evidence and completes. | **Verified** | [`tests/test_task_approval_loop.py`](../tests/test_task_approval_loop.py) `test_fake_provider_read_propose_grant_execute_and_complete` runs normal and interrupted-before-block variants against real SQLite policy/tools/approval/execution; asserts the write, completed task/plan and valid audit chain, with no content in audit payloads. ADR-046; handoff 2026-11-27. |
| A2 | Kill the process mid-task; resume completes without duplicate side effects. | **Verified** | [`tests/test_m1_process_kill.py`](../tests/test_m1_process_kill.py) starts the real plan in a child process, blocks inside the read-only tool, kills that OS process, then resumes in a new process; the read runs once, the task/plan/step complete, and a third resume is a no-op. Complementary durable-boundary tests: [`tests/test_m1_crash_recovery.py`](../tests/test_m1_crash_recovery.py) and [`tests/test_task_cancellation_review.py`](../tests/test_task_cancellation_review.py). ADR-045/ADR-049. |

## 2. Deliverables

| ID | Deliverable | Status | Evidence |
|---|---|---|---|
| D1 | Runnable `eis serve` | **Verified** | Real hosted Ubuntu `serve-smoke` job: public `/health`, bearer-authenticated sessions route, 401 without token, non-loopback rejection, child cleanup. [`tests/test_serve_smoke.py`](../tests/test_serve_smoke.py); hosted run `46007376737` (13/13 jobs). |
| D2 | `eis ask` with fake provider | **Verified** | [`tests/test_cli.py`](../tests/test_cli.py) `test_ask_fake_runs_durable_plan_without_a_provider` runs the real CLI path with a temporary DB/policy and asserts the durable blocked review state without printing the goal; [`tests/test_model_loop.py`](../tests/test_model_loop.py) covers the bounded fake-provider loop. |
| D3 | Policy files | **Verified** | `policies/default.json`; [`tests/test_policy_baseline.py`](../tests/test_policy_baseline.py), [`tests/test_policy_engine.py`](../tests/test_policy_engine.py), [`tests/test_policy_persistence.py`](../tests/test_policy_persistence.py). |
| D4 | Schema v1 / migrations | **Verified** | `src/eis/storage/migrations/versions/0001`–`0010`; [`tests/test_storage.py`](../tests/test_storage.py); wheel content check. |
| D5 | Tests | **Verified** | Full local suite: **392 passed, 2 skipped**; hosted CI: **13/13 jobs** (see handoff for exact run IDs). |

## 3. Required M1 tests

| ID | Area | Status | Evidence |
|---|---|---|---|
| T1 | Policy matrix | **Verified** | [`tests/test_policy_engine.py`](../tests/test_policy_engine.py), [`tests/test_policy_baseline.py`](../tests/test_policy_baseline.py), [`tests/test_policy_persistence.py`](../tests/test_policy_persistence.py). |
| T2 | Path escape | **Verified** | [`tests/test_project_roots.py`](../tests/test_project_roots.py), [`tests/test_project_tools.py`](../tests/test_project_tools.py). |
| T3 | Approval binding/replay/expiry | **Verified** | [`tests/test_approval_execution.py`](../tests/test_approval_execution.py), [`tests/test_task_approval_loop.py`](../tests/test_task_approval_loop.py), [`tests/test_api_approvals.py`](../tests/test_api_approvals.py). |
| T4 | Audit chain | **Verified** | [`tests/test_audit_chain.py`](../tests/test_audit_chain.py); append-only triggers, hash verification and tamper tests. |
| T5 | Limit enforcement | **Verified** | [`tests/test_model_loop.py`](../tests/test_model_loop.py) (completion/tool/wall-time/input/feedback limits), [`tests/test_runtime.py`](../tests/test_runtime.py). |
| T6 | Crash/resume with fake provider | **Verified** | [`tests/test_m1_process_kill.py`](../tests/test_m1_process_kill.py) (real OS kill), [`tests/test_m1_crash_recovery.py`](../tests/test_m1_crash_recovery.py), [`tests/test_task_cancellation_review.py`](../tests/test_task_cancellation_review.py). |
| T7 | Redaction corpus | **Verified** | [`tests/test_redaction.py`](../tests/test_redaction.py), [`tests/test_provider_adapters.py`](../tests/test_provider_adapters.py), [`tests/test_privacy.py`](../tests/test_privacy.py); documented non-completeness limits. |

## 4. Required M1 security controls

| Control | Status | Evidence |
|---|---|---|
| SEC-01 deny-by-default, versioned rules | **Verified** | Policy tests listed in T1; policy hash/version is stored in decisions and plans. |
| SEC-02 resource resolution by ID + relative path, containment | **Verified** | Root registry and read-only tool tests in T2; `O_NOFOLLOW` descriptor walk on POSIX and reparse/identity checks on Windows are documented. |
| SEC-03 command profiles, no shell, argument arrays (partial as scoped) | **Verified (partial scope)** | Fixed T1/T2 argv and no-shell tests in [`tests/test_t2_sandbox.py`](../tests/test_t2_sandbox.py); the roadmap already marks SEC-03 partial. |
| SEC-06 secret detection + redaction | **Verified** | T7; C3 is structurally blocked by classification, not only filtered. |
| SEC-07 untrusted-content envelopes and instruction hierarchy | **Verified** | Strict model/plan envelopes in [`tests/test_model_loop.py`](../tests/test_model_loop.py) and [`tests/test_task_approval_loop.py`](../tests/test_task_approval_loop.py); identity/root/policy fields rejected. |
| SEC-09 approval binding, single use, TTL | **Verified** | T3; digest-bound grant/execute/reject and replay/expiry tests. |
| SEC-10 hash-chained append-only audit | **Verified** | T4; database triggers and chain verification. |
| SEC-11 loopback bind, bearer token, Host checks | **Verified** | [`tests/test_api_auth.py`](../tests/test_api_auth.py), [`tests/test_api.py`](../tests/test_api.py), [`tests/test_serve_smoke.py`](../tests/test_serve_smoke.py); hosted `serve-smoke` job. |
| SEC-13 provider consent + egress record + classification ceiling | **Verified** | [`tests/test_provider_adapters.py`](../tests/test_provider_adapters.py), [`tests/test_storage.py`](../tests/test_storage.py), [`tests/test_config.py`](../tests/test_config.py). |
| SEC-17 fail-closed configuration validation | **Verified** | [`tests/test_config.py`](../tests/test_config.py), [`tests/test_cli.py`](../tests/test_cli.py), [`tests/test_serve_smoke.py`](../tests/test_serve_smoke.py). |

## 5. Definition of Done

| Item | Status | Note |
|---|---|---|
| All tasks complete or explicitly deferred with an approved ADR | **Verified for M1 gates** | Delegation is M5; SSE/dashboard is M10; evidence artifacts are later M4/M7; Ollama is optional. ULID-specific IDs are not used (prefixed UUID/hex IDs are the implementation); this is not an M1 acceptance dependency. |
| Tests cover success, denial, failure and edge cases | **Verified** | Full suite and the test areas above. |
| Security/privacy checks pass | **Verified** | Ruff, Mypy, privacy-check, pip-audit and hosted CI; no new accepted risk beyond documented limits. |
| Migrations tested from previous release | **Verified** | Migration chain `0001`–`0010`; storage tests and clean build. |
| Documentation, ADRs and changelog updated | **Verified** | ADR-038–ADR-050 and linked docs; this matrix; CHANGELOG. |
| Demo script from a clean install | **Verified** | Hosted `serve-smoke` and `eis ask --fake`; clean-copy install evidence remains in `M0.2_DECISIONS.md`. |
| No known critical defect; limitations listed | **Verified** | Known limits are documented in security/privacy docs and the handoff. |

## 6. Scope reconciliation

- **Delegation** is M5; it is not an M1 acceptance criterion and is not counted as an M1 blocker.
- **SSE, dashboard and browser tokens** are M10 interface work; not M1 acceptance blockers.
- **Evidence/artifact persistence** is used by later M4/M7 work; M1 persists content-free hashes/statuses and keeps file/prompt content in memory.
- **Ollama** remains optional; the five external adapters and the fake provider satisfy the M1 provider-neutral gateway deliverable.
- **Broader recovery and hardening** beyond the M1 scripted flow and real process-kill/resume test are M6/later hardening, not M1 acceptance gates.

## 7. External / operator-owned requirements

| Requirement | Status | Owner |
|---|---|---|
| Real S3 Object Lock retention | **Externally unverified** | Operator must provision/designate a bucket, enable Versioning + Object Lock, choose COMPLIANCE or GOVERNANCE and a duration, grant the documented least-privilege IAM actions without `s3:BypassGovernanceRetention`, run `eis anchor status/create/verify`, and independently retain the evidence. The repository's mocked adapter tests are not real retention evidence. |
| GitHub branch protection | **Externally unverified (M0.2)** | GitHub repository admin must configure and verify it; this audit did not change protection settings. |
| Real T2 Linux evidence | **Verified (hosted, point-in-time)** | Hosted Ubuntu T2 job against the digest-pinned image/profile; this is point-in-time runner evidence, not protection against a compromised runner/kernel. |
| Real external audit anchor backend | **Externally unverified** | Optional S3 adapter exists; no live bucket or credentials were used. |

## 8. Verdict

- **M1 acceptance criteria A1 and A2: PASS** with local/CI test evidence, including a real OS process kill and resume without a duplicate read.
- **M1 milestone:** acceptance criteria are verified. The milestone is **not declared fully complete** in this audit while real external S3 Object Lock retention remains operator-unverified and M0.2 branch protection remains admin-unverified; both are external requirements, not M1 acceptance failures. Delegation (M5) and SSE/dashboard (M10) are out of M1 scope.
- **No known incomplete M1 acceptance criterion.** Known limitations and external decisions are listed above and in the handoff.
