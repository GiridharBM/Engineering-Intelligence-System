# Changelog

All notable changes to EIS are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- CI typecheck portability: replaced direct `subprocess.CREATE_NEW_PROCESS_GROUP` and `signal.CTRL_BREAK_EVENT` references with `getattr(..., default)`, so Ubuntu Mypy no longer requires Windows-only names while preserving Windows process-group behavior; no Mypy checks were weakened.

### Added

- **M1 fail-closed crash recovery (ADR-049):** mapped durable boundaries for task, plan steps, tool invocation, approval and execution. Consumed approvals without successful `execution_runs` evidence now fail terminally as `execution_unverified` via CLI/API retry instead of repeating a write or claiming completion; migration `0010` adds DB transition guards. Added fault-injection tests for side effect before evidence, audit failure during read-only recovery, repeated resume, stale policy/root and no duplicate side effects. API spec corrected to mark `GET /api/v1/audit/events` implemented.
- **M1 authenticated audit-event browsing API:** `GET /api/v1/audit/events` returns bounded pages of content-free metadata only (seq, timestamp, type, actor, subject, decision, chain hashes). Unknown/repeated query fields and malformed/tampered cursors are rejected; signed cursor binds `after_seq`/`head_seq`/`head_hash` for stable paging; full chain and requested prefix are verified and a corruption returns 503. Added `tests/test_api_audit_events.py` (8 tests). API spec, roadmap, learning guide and handoff updated.
- **M1 external audit-anchor interface (ADR-048):** added content-free `AuditAnchor`/`AnchorReceipt`, `AuditAnchorSink` protocol, `AuditAnchorService` with strict up-to-seq/head-hash binding, idempotent retries, receipt verification, fail-closed chain checks and `audit_anchor_submitted` reference events. Added `AuditChainStore` helpers for historical prefix verification and event lookup. No real external backend is configured; a fake independently controlled sink covers adversarial tests (tampered/truncated chains, stale/replayed/forged checkpoints, external failure, audit failure, restart/retry, no backend).
- **M1 atomic task cancellation and human-review continuation (ADR-047):** migration `0009_task_cancellation_review`; `cancel_task` `task.cancel` + atomic task/plan/step + chained event updates; never cancels terminal/`approval_required` tasks or tasks with successful `execution_runs`; successful execution evidence wins; `execute_write` rechecks task state. Strict `continue_plan_review`/`continue_human_review` for `human_review` with `task.run` rechecked, policy hash/session/project/root checks, no permission/approval/root/model grant, atomic blockedâ†’running + event, resume only unexecuted ready steps. CLI `eis task cancel`, `eis plan continue-review`; API `POST /api/v1/tasks/{id}/cancel`, `POST /api/v1/tasks/{id}/review/continue` with auth + strict schema. Tests: `tests/test_task_cancellation_review.py`, `tests/test_api_approvals.py`, `tests/test_api_auth.py`.
- **M1 approval route tests and execute retry recovery:** added `tests/test_api_approvals.py` (TestClient route-level authentication, bad digest, schema rejection, default-deny policy, rejected approval, successful task/plan completion with durable evidence, and retry/recovery without duplicate writes). `POST /api/v1/approvals/{id}/execute` now recovers an already-consumed digest-bound approval from durable `execution_runs` evidence instead of repeating the write, still requiring the exact digest and `complete_task_after_approval`; retry after success returns the same run and completed state. Added `ApprovalStore.successful_execution`. Fixed two `human_review`/blocked-step regressions: `PlanStore.transition_step` only restricts `running -> blocked` to write proposals, and plan-recovery tests no longer require empty argument JSON.
- **M1 local runtime API and CLI:** added loopback-only `eis serve` with Uvicorn, a generated/rotatable bearer token stored in the OS config directory, loopback Host validation, authenticated session/task, digest-bound approval decision/execution, pending approval review and audit-verification routes with strict request schemas. Approval execution resolves roots from the registry and rechecks policy through the existing gateway. Added `eis api-token` and `eis ask --fake` deterministic smoke operation. Health/metadata remain public; the wider API design remains pending.
- **M1 task-bound plan write approval (ADR-046):** strict model plans may end with one typed project-write proposal. The proposal is bound to its task; task, plan and proposal step enter approval-wait atomically with chained audit events. Human grant and execution stay in the existing approval CLI; successful execution evidence atomically completes the step/plan/task, and rejection atomically fails them. Added migration `0008_task_approval_link` and a fake-provider end-to-end acceptance test. Approval creation is idempotently reused after a crash before the block transaction.
- **M1 provider-egress redaction and correlation-safe request logs:** added common credential-pattern redaction before task goals and read-only tool feedback are sent to model providers, and a structured logger that accepts only safe scalar metadata. HTTP responses now carry validated/generated correlation IDs; logs contain route metadata and status only, never request bodies or credentials. These pattern rules are defense in depth and do not guarantee detection of arbitrary secrets.
- **M1 explicit plan recovery (ADR-045):** migration `0007_plan_recovery` stores validated read-only step arguments immutably in local SQLite and records a guarded 0â€“2 attempt count. `eis plan resume <task_id>` and `eis task resume <task_id>` explicitly resume after rechecking policy, session and root. Only an in-flight read-only step can be retried once; completed steps are skipped and no process-start auto-resume occurs. Legacy 0006 plans without saved arguments fail closed.

- **M1 fail-closed T2 Linux-container backend (ADR-044):**
  - Added `eis.core.sandbox.SandboxBackend` and `LinuxContainerBackend`. `ExecutionGateway` routes T2 profiles and untrusted projects through it only after the existing `project.verify` policy decision and audit-integrity gates; missing or partial protections return `isolation_unavailable` before any execution.
  - Fixed container argv: immutable application-supplied image digest, `--pull=never`, `--network=none`, read-only rootfs, non-root user, dropped capabilities, no-new-privileges, explicit seccomp profile, CPU/memory/PID limits, bounded output cap and timeout, filtered disposable workspace (read-only or writable-and-discarded), and forced container cleanup.
  - `CommandProfile` now validates an application-defined environment allowlist (non-secret keys, capped values) and requires T2 for writable workspaces. No executable, image, mount, argument or isolation setting can come from model output.
  - Capability probe fails closed on non-Linux hosts; T2 is not claimed on Windows. T1 is still a same-user process and not a Windows security boundary.
  - Added fake-backend tests for configuration, policy/audit denial, untrusted routing, fixed argv protections, declared-only environment, timeout/truncation mapping, cleanup and misconfigured/unavailable backends in `tests/test_t2_sandbox.py`.
- **M1 durable task plans and bounded step lifecycle (ADR-043):**
  - Added migration `0006_task_plans` and `eis.storage.PlanStore`: one plan per task bound to the project, policy version/hash, plan hash and creation time, with ordered steps, guarded state transitions and content-free chained audit events written in the same transaction as each row change.
  - Added `eis.core.model_plan`: strict plan envelope with at most six steps using only registered read-only tools or `human_review`. Duplicate keys, extra fields, oversized plans, invalid tools, forbidden identity/root/policy/command/write/delete/network/approval instructions, missing or cyclic dependencies and duplicate steps are rejected with content-free codes.
  - `SessionTaskRuntime.run_plan` persists a valid plan, then runs dependency-ready steps serially through the existing `PolicyService` and project-root registry. It stops on denial, audit failure, provider/consent failure, malformed output, repeated calls, missing roots or a human-review step; `resume_plan` marks interrupted plans failed and never auto-resumes.
  - Added `eis plan run|show|steps`. Default output shows only IDs, states, hashes and sanitized statuses; plan text is shown only with `--show-proposal` inside `model_generated_plan_begin/end` labels. No plan arguments, goals, file contents or tool results are stored in the plan tables or audit payloads.
  - Added migration/upgrade, transition-guard, schema, dependency, policy/audit denial, repetition, interruption, privacy and CLI tests in `tests/test_task_plans.py`.
- **M0.2 reproducibility and CI acceptance work:**
  - Verified a clean workspace copy (no `.git`, `.venv`, caches, runtime data, local secrets or vision document): `uv lock --check`, `uv sync --locked --all-extras` and the full suite pass offline from the lockfile on Python 3.12.13 (217 passed, 6 skipped) and Python 3.11.15 (217 passed, 6 skipped), plus Ruff, Mypy and `privacy-check`.
  - Verified the built wheel in a fresh Python 3.11.15 environment with no credentials: `eis` resolves from `site-packages`, and `version`, `doctor`, `policy-check`, `provider-check`, `privacy-check` and `audit verify` all pass from installed package files with no network access.
  - Reviewed and hardened `.github/workflows/ci.yml`: `uv lock --check`, `--locked` on every `uv sync`/`uv run`, `persist-credentials: false`, per-job `timeout-minutes`, unchanged full-SHA pins; still `permissions: contents: read` with no repository secrets beyond the default `GITHUB_TOKEN`.
  - Added `tests/test_ci_workflow.py` asserting those workflow properties, and made the git-dependent repository-privacy tests skip outside the EIS work tree so clean copies and sdist extracts pass.
  - Exact commands and results: [docs/M0.2_DECISIONS.md](docs/M0.2_DECISIONS.md). GitHub-hosted execution and branch protection remain unverified; no remote, commit or push was created.

- **M1 strict-envelope bounded model-to-tool loop (ADR-042):**
  - Added `eis.core.model_envelope`; a response must be exactly `{"kind":"answer","content":...}` or `{"kind":"tool_call","tool":...,"arguments":{...}}`. Duplicate JSON keys, extra/missing fields, unknown kinds, blank answers and responses over 8192 bytes stop the run with a content-free error code.
  - Forbidden argument keys (`project_id`, `principal`, `root`, `root_path`, `policy`, `session_id`, `task_id`, `capability`, `approval_id`, ...) are rejected; session, task, principal and project ID are injected from durable runtime state and never read from model output.
  - `SessionTaskRuntime` now runs a bounded loop â€” at most 3 completions and 2 read-only tool calls, 60 s wall clock, 32 KiB input cap, 4000-character tool feedback â€” through `ModelGateway` and the existing typed registry/`PolicyService`; policy or audit failure before work prevents execution.
  - Stops on malformed output, denial, consent/provider failure, a repeated identical tool call or limit exhaustion; an interrupted `running` task is never auto-resumed.
  - Run counts merge into the existing `usage_json`/audit `usage` payload; prompts, responses, file contents and tool results stay in memory. The CLI prints `answer=untrusted_model_generated_no_action_taken` by default and shows a labeled `model_generated_answer` block only with `--show-proposal`.
  - Added envelope, identity-field, policy/audit-before-work, consent/provider, limit, repetition, truncation, privacy and CLI/resume tests in `tests/test_model_loop.py`.

- **M1 durable project-root registry and race-resistant reads:**
  - Added migration `0005_project_roots` and `ProjectRootStore` to register, list and remove canonical roots with filesystem identity and timestamps; no implicit backfill.
  - Root registration is an explicit local-user CLI action (`eis project register|list|remove`) and rejects missing paths, files, filesystem roots, runtime data/config locations, duplicate/overlapping roots and symlink/junction components.
  - `task invoke-tool` now resolves the session project ID through the registry; a caller-supplied root path is no longer accepted, and moved or replaced roots fail closed.
  - POSIX reads/listings use a verified descriptor walk with `O_NOFOLLOW`; Windows checks links/reparse points and opened-handle identity, then re-checks the root. The residual Windows swap window is documented, not claimed as solved.
  - Added content-free `project_root_registered`/`project_root_removed` chain events and registry/race/migration tests.

- **M1 bounded read-only project tools:**
  - Added strict typed calls for `project.list`, `project.list_files`, `project.read_text`, and `git.status`.
  - Added explicit `task invoke-tool` dispatch through the durable runtime; model output was not parsed at that slice (superseded by the ADR-042 envelope loop above).
  - Every invocation gets a `PolicyService` decision and content-free chained denied/started/completed/failed events.
  - Added path traversal, absolute-path, secret-file, symlink/junction, list/read/Git output and timeout limits.
  - CLI output includes invocation/task IDs, status and hashes only; file content and raw Git output remain in memory.
  - Added policy, corruption, path, size, Git failure, CLI privacy and session/task regression tests.

- **M1 minimal session/task runtime slice:**
  - Migration `0004_sessions_tasks` with explicit session/task lifecycle states, timestamps, project association and state-transition triggers.
  - `SessionTaskStore` persists lifecycle rows with content-free chained audit events; title/goal hashes are audited, goals stay private local data.
  - `SessionTaskRuntime` creates/resumes sessions and tasks, evaluates `task.run` through `PolicyService`, requests one completion through `ModelGateway`, and stops at `human_review_required`.
  - Model output is untrusted in-memory proposal text only: it is never persisted, parsed into tools, executed, approved or printed by default.
  - Restart/resume handles created tasks; interrupted running tasks fail closed without a second provider call.
  - CLI `session create/list/show/resume/close` and `task submit/resume/status/list`, with content-free output and opt-in sanitized proposal preview.
  - Tests for migration, lifecycle, restart/resume, policy/consent denial, chain failure, privacy and CLI behavior.
- **M1 policy projection, approval and bounded execution slice:**
  - Added migration `0003_approvals_policy_projection` with the append-only `policy_decisions` projection (including backfill), digest-bound private A3 approvals and immutable execution-run records.
  - Added `eis policy-decisions` and `eis approval propose-write|list|show|grant|reject|execute` for explicit user review and exact-digest confirmation.
  - Added guarded delegated file writes with path/root and content-hash checks, short-lived single-use approval, audit-chain gating, backup and atomic replacement.
  - Added fixed command profiles with no shell, reduced environment, timeout and output cap; T2 and untrusted project runs are refused until an isolation backend is available.
  - Added approval, policy projection, file-write, path-boundary and audit-failure tests.

- **M1 policy-decision chain slice - audited decisions:**
  - `PolicyService` records each decision through a sink before returning it; sink failure or uncertain chain integrity blocks the caller.
  - `SqlitePolicyDecisionSink` appends content-free `policy_decision` events to `audit_events`, including allow, explicit deny and default deny.
  - Payloads carry decision ID, effect, reason, rule ID, policy version/hash, inputs hash, principal, action, canonical resource and context source; context attributes/task IDs are omitted and absolute or drive-letter paths are rejected.
  - Deterministic decision IDs double as event IDs, so retrying the same evaluation returns the existing event instead of appending a duplicate.
  - `eis policy-evaluate` exercises the audited service; `policy-check` remains a read-only diagnostic.
  - Tests for allow, explicit deny, default deny, provenance, audit failure, corrupted-chain blocking, idempotent retries and sensitive-value exclusion.
- **M1 audit-chain slice - tamper-evident append-only audit:**
  - `0002_audit_chain` migration creating `audit_events` with canonical SHA-256 hashes and append-only/continuity triggers.
  - `AuditChainStore` append/verify API and `AuditIntegrityGate` interface for future approval paths.
  - Consent grant/revoke, egress decisions and model calls append content-free chain events in the same transaction as their projection rows.
  - Verification detects altered payloads/hashes, deleted or reordered events, sequence gaps and broken links; integrity failure blocks appends and provider calls.
  - `eis audit verify` reports pass/failure without printing payloads.
  - Tests for valid and restarted chains, append-only enforcement, tampering, integrity blocking and secret-free payloads.

- **M1 storage slice â€” durable SQLite consent and audit:**
  - Alembic migration creating `consents`, `egress_decisions` and content-free `model_calls` tables.
  - `ConsentStore` with grant, environment bootstrap, active lookup, expiry, revocation and listing.
  - `SqliteAuditSink` implementing the existing `AuditSink` contract.
  - Durable gateway factory using SQLite consent and audit by default.
  - CLI `consent grant`, `consent revoke` and `consent list`; no keys are printed.
  - Tests for migration, reload, revocation, expiry, data-class scope, C3 denial, denial-before-network and content-free audit storage.
- **M1 provider slice â€” provider-neutral text gateway:**
  - Typed `ModelProvider` contract and registry for OpenCode, OpenAI, Gemini, Anthropic and xAI.
  - Vendor-specific adapters: Chat Completions for OpenCode/OpenAI/xAI, `generateContent` for Gemini, Messages for Anthropic.
  - Common `EIS_MODEL_PROVIDER`/`EIS_MODEL` selection; provider-specific credential variables; OpenCode base URL remains explicit.
  - Validated base URL/model/key; no secret values in errors, logs, API metadata or examples.
  - Provider-scoped consent gate: no network call without explicit `EIS_MODEL_CONSENT_CLASSES`; C3 always denied.
  - Content-free egress decisions and model-call audit records; JSONL sink available.
  - Local `.env` loading in the CLI entry point; `.env` ignored and `.env.example` placeholders only.
  - Deterministic `httpx.MockTransport` tests; no network or real API key.
  - `eis provider-check` validates configuration without a network call.
  - No provider enabled by default; Ollama optional and not required.
- **M1 first slice â€” deterministic policy engine and decision audit contract:**
  - Typed `Principal`, `Action`, `Resource`, `PolicyContext`, `PolicyRequest`, `PolicyRule`, `PolicyDecision` and `PolicyAuditRecord`.
  - Schema-validated policy loading with wildcard, malformed-input and invalid-resource-scope rejection.
  - Explicit deny precedence, default deny, deterministic decision IDs and SHA-256 policy/input hashes.
  - `DecisionSink` protocol and `record_decision` helper; no persistence or hash chain yet.
  - Policy version and hash reporting in the CLI and `/api/v1/meta`.
  - Tests for allow, deny, default deny, deny precedence, unknown action/resource, malformed policy/input, wildcard rejection, invalid path scope, policy version, determinism and audit records.
- M0.2 bootstrap foundation:
  - Python 3.12 baseline package with a committed `uv.lock` and hatchling build.
  - `src/eis` skeleton: configuration defaults, deny-by-default policy loader/evaluator, privacy guard, FastAPI factory, Typer CLI and `python -m eis`.
  - `schemas/policy.schema.json` and `schemas/policy-decision.schema.json`.
  - `policies/default.json` with no allow rules.
  - Privacy guard covering absolute drive/home/UNC paths, runtime data files and secret-shaped values, wired into CLI, pre-commit and CI.
  - CI jobs for lint, types, cross-platform tests, dependency audit, privacy and package build with SHA-pinned GitHub Actions.
  - 37 baseline tests at M0.2; 178 total after the policy, provider, storage, audit-chain, policy-persistence, approval/execution, runtime and read-only-tool slices.
- Apache-2.0 `LICENSE` and `NOTICE`.
- Local Git repository initialised with no remote and no commits.
- M0.2 decisions and evidence recorded in `docs/M0.2_DECISIONS.md`.

- M0.1 specification package:
  - Product specification, system architecture, technical design and architecture decision records.
  - Agent architecture and runtime design.
  - Memory architecture, database schema and API specification.
  - Skill system and controlled skill evolution.
  - Security architecture, threat model, execution and permission model.
  - Privacy and data governance.
  - Open source governance, research plan and implementation roadmap.
  - Initial repository audit.
- Root governance files: README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, SUPPORT, licence recommendation, public release checklist.
- Privacy-first `.gitignore` baseline.

### Fixed

- Replaced a real-looking credential in `.env.example` with a placeholder; the template now contains placeholders only.
- Runtime review fixed interrupted-task handling when its session is already closed; task failure is now recorded without an invalid session transition.
- Added coverage for provider failure, lifecycle rollback on a broken chain, task/session transition triggers, interrupted closed-session tasks and opt-in proposal-preview sanitization.
- Audit review fixed migration coverage, approval binding and execution edge cases:
  - Migration test now expects `0003_approvals_policy_projection`, new tables and triggers; wheel verification includes migration 0003.
  - Approval digest now binds the policy decision ID as well as version, target, root, content hashes, diff, summary and expiry.
  - Rejection reasons stay in the private approval row; only a SHA-256 hash enters the audit chain.
  - Project-root hashes normalize path case on Windows.
  - Additional reserved secret/config paths are denied by the write gateway.
  - Profile launch failures are recorded as unavailable instead of escaping without an execution record; output truncation uses `output_truncated`.
  - Expired approvals are hidden from the pending list and rejected before policy checks in execute.
  - Added expiry, state-transition, policy-change, root-binding, digest-tamper, profile, CLI-flow and legacy-backfill tests.

### Changed

- Supported Python range narrowed to 3.11â€“3.12 (ADR-032); Python 3.13 and 3.14 are refused until tested.
- Policy is JSON with a strict schema; wildcard grants are rejected by both the loader and the schema.
- Model provider default changed: no provider is enabled by default; OpenCode/OpenAI/Gemini/Anthropic/xAI are explicit, Ollama optional (ADR-033, ADR-034).

### Security

- Dependency audit passes with no known vulnerabilities; the local `eis` package is skipped because it is not published on PyPI.
- Secret scanning and the privacy gate are configured in CI; the vision document is ignored and not tracked.
- No T2 sandbox or full plan/act agent loop is implemented. A3 approvals, a bounded T1 `ExecutionGateway` and a durable session/task runtime with a strict-envelope bounded read-only tool loop exist; provider consent/egress/model-call events, policy decisions, approval transitions, execution outcomes and session/task lifecycle events are hash-chained. Approval rejection reasons are hashed in the chain and stored only locally.
- The four read-only project tools are strictly typed and policy/audit gated; the model can reach them only through the ADR-042 envelope with injected identity and bounded calls/completions. File/Git results are never persisted or printed by default.
- The policy engine has no model or network dependency; model output cannot authorize an action.
- Provider API keys are read from the environment only, masked as `SecretStr`, and excluded from errors, logs, API responses and the handoff file. `.env.example` contains placeholders; `.env` is ignored.

### Notes

- M0.2 acceptance is **not complete**. GitHub-hosted CI has not run, branch protection cannot be configured without a remote and first push, and dependency licence review is outstanding. Clean-copy installation and fresh-wheel verification are now evidenced locally on Windows with Python 3.11 and 3.12.
- M1 policy, provider, storage, audit-chain, policy-persistence, approval/execution, runtime, read-only-tool, bounded model-loop and durable-plan checks pass on Python 3.12.13 and Python 3.11 (fresh wheel): 312 tests locally (1 POSIX-only skip on Windows), 217 passed / 6 skipped in a clean copy on each supported Python (at the M0.2 verification point), Ruff, Ruff format, Mypy, privacy-check, pip-audit, build, wheel verification and Markdown links.
- Local M0.2 checks on Python 3.12.13 and Python 3.11 previously passed with 37 tests.
- GitHub Actions are pinned to full commit SHAs verified against their release tags; CI itself has not run because no remote exists.
- No remote, push or commit was created.

## Release conventions

- `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.
- Security entries name the affected versions and the fixed version.
- Breaking changes are called out at the top of the release section.
- Every release links to its milestone acceptance evidence.
