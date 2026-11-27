# EIS Implementation Roadmap

Version: 1.0 (M0 baseline)
Status: Baseline; milestone scope changes require an ADR
Related: [EIS_PRODUCT_SPEC.md](EIS_PRODUCT_SPEC.md), [EIS_SYSTEM_ARCHITECTURE.md](EIS_SYSTEM_ARCHITECTURE.md), [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md), [EIS_RESEARCH_PLAN.md](EIS_RESEARCH_PLAN.md)

---

## 0. Delivery Rules

1. A milestone is complete only when its acceptance criteria are demonstrated with evidence (test output, report, recorded run), not when tasks are marked done.
2. Security foundations land from M1. M6 hardens them; it does not introduce them.
3. Each milestone ends with a retrospective note recording deviations, debt and decisions.
4. No milestone may start work that depends on an ungated capability. If a dependency is missing, the milestone is blocked, not approximated.
5. Every milestone keeps `main` releasable: migrations, tests, docs and changelog updated.

### Definition of Done (applies to every milestone)

- All tasks complete or explicitly deferred with an approved ADR.
- New code has tests covering success, denial, failure and edge cases.
- Security and privacy checks pass; no new accepted risk without a recorded entry.
- Migrations tested from the previous release.
- Documentation updated: user docs, architecture docs, ADR if a decision changed.
- `CHANGELOG.md` updated.
- Demo script runs from a clean install.
- No known critical defect; known limitations listed.

### Dependency Map

```
M0.1 Specs ──► M0.2 Repo bootstrap ──► M1 Secure runtime
                                          │
        ┌─────────────────────────────────┼──────────────┐
        ▼                                 ▼              ▼
       M2 Registry ──► M3 Memory ──► M5 Specialists   M6 Hardening
                          │                │              │
                          ▼                ▼              ▼
                         M4 Skills ─────► M8 Evolution  M7 Scanners/Verification
                                                            │
                                                            ▼
                                                          M9 Automation
                                                            │
                                                            ▼
                                                         M10 Interfaces
                                                            │
                                                            ▼
                                                         M11 Beta
                                                            │
                                                            ▼
                                                         M12 Research
```

Hard dependencies: M1 → everything; M2 → M3; M3 → M5; M4 → M8; M6 → M7 (untrusted scanning); M7 → M9 (automated verification); M10 → M11.

---

## M0 — Architecture and Foundation

### M0.1 Specification package (this delivery)

**Objective.** Establish requirements, architecture, security, privacy, data, API and governance contracts before code.

**Deliverables.** `docs/` package (19 documents), root governance files, audit, licence recommendation, release checklist. See [README](../README.md).

**Acceptance.** Every document exists, has technical decisions and acceptance criteria, cross-references resolve, and the audit records the actual repository state.

**Status.** Complete (see the final review checklist in the delivery response). No application code.

### M0.2 Repository bootstrap (approved; implemented locally, acceptance pending)

**Objective.** Create a safe, reproducible repository and CI baseline.

**Status.** Foundation implemented on 2026-11-20; clean-copy reproducibility and a CI workflow review verified locally on 2026-11-23. M0.2 is **not complete**: branch protection remains outstanding. Hosted CI run `45958723773` for `df5847e` reached `conclusion=success`; clean-copy reproducibility was verified locally on 2026-11-23.

**Depends on.** M0.1; approvals for Git initialisation and the Apache-2.0 licence were granted on 2026-11-20.

**Implemented.** Git repository initialised; `origin` on `main`; `.gitignore` and `.gitattributes`; `pyproject.toml`, `.python-version`, `uv.lock`, `LICENSE`, `NOTICE`; `src/eis` skeleton with configuration, deny-by-default policy loader/evaluator, privacy guard, API factory, CLI and `python -m eis`; `schemas/` policy and decision schemas; `policies/default.json`; CI jobs for lint, types, cross-platform tests, security audit, privacy and package build with SHA-pinned actions; baseline tests for repository boundary, vision exclusion, runtime-data exclusion, policy, OpenAPI, imports and configuration.

**Local evidence.** `uv lock --check`, `uv sync --locked --all-extras`, `pytest` (352 passed, 1 POSIX-only skip on Windows), Ruff, Mypy, `pip-audit`, `uv build`, wheel-resource verification and an installed-wheel smoke test all pass on Python 3.12.13. A clean workspace copy (no `.git`, no `.venv`, no caches, no runtime data, no local secrets, no vision document) reproduces the full suite offline from the lockfile: 217 passed / 6 skipped on Python 3.12.13 and 217 passed / 6 skipped on Python 3.11.15, plus Ruff, Mypy, privacy-check, `uv build` and wheel verification. The built wheel was installed into a fresh Python 3.11.15 environment with no credentials and no local secrets file: `version`, `doctor`, `policy-check`, `provider-check`, `privacy-check` and `audit verify` all pass from installed package files only. Exact results and commands are recorded in [M0.2_DECISIONS.md](M0.2_DECISIONS.md).

**Remaining acceptance evidence.** Branch protection still needs repository admin; dependency licence review is still a release task. Full hosted CI has passed once (`45958723773`).

**Tests.** Repository boundary test (skips outside the EIS work tree); privacy path/secret/runtime-data test; deny-by-default policy and schema tests; generated OpenAPI test; CLI/API import-contract tests; CI workflow guard test (`tests/test_ci_workflow.py`: read-only permissions, full-SHA pins, locked sync, required jobs, no `.env`/secret/provider state).

**Security.** No secrets, no private paths, no runtime data; pinned actions with `persist-credentials: false`; least-privilege `contents: read`; per-job timeouts; privacy gate in pre-commit and CI; vision document ignored.

**Acceptance.** A clean clone installs and runs `pytest` with ≥ 1 passing test; CI blocks on privacy failure; no `.gitmodules`; repo root guard passes.

**DoD.** Standard DoD plus branch protection configured before the first public push.

---

## M1 — Secure Agent Runtime

**Slices implemented (2026-11-20/27).** Typed policy requests and decision records; provider-neutral text gateway; durable SQLite consent/audit; tamper-evident append-only `audit_events`; chained decisions with idempotent retries and a queryable projection; digest-bound A3 project-write approvals; guarded, backed-up project-file writes; limited T1 fixed-profile verification for trusted projects; durable sessions/tasks and project-root registry; policy-gated read-only project tools; strict-envelope bounded model-to-tool and task-plan loops (ADR-042/043); fail-closed Linux-container T2 backend (ADR-044) with hosted real-container verification against a digest-pinned image; expanded provider-egress and structured-log credential redaction (bearer/basic tokens, JWTs, PEM blocks/headers, API-key prefixes, prefixed assignments and credential-bearing URLs) applied at the gateway before transport, with documented non-completeness limits; content-free structured HTTP request logging with correlation IDs; explicit, bounded read-only plan recovery (ADR-045); and task-bound final-plan-step write proposals that require human approval, execute through the existing gateway, and complete task/plan state only from successful execution evidence (ADR-046); and atomic task cancellation plus strict human-review continuation with `task.cancel`/`task.run` policy, migration `0009`, no permission grant and no cancellation when successful execution evidence exists (ADR-047); and a tested external audit-anchor interface with strict prefix/hash binding and fail-closed verification, with no real external backend configured (ADR-048), plus an optional S3 Object Lock adapter with explicit COMPLIANCE/GOVERNANCE mode and duration, exact-version/digest/retention verification, operator-triggered `eis anchor create`/`eis anchor verify` commands, mocked AWS-client tests and no live bucket (ADR-050); authenticated, content-free audit-event browsing with strict query validation and signed stable cursors; and fail-closed recovery for consumed approvals without execution evidence (ADR-049). The task, plan and proposal-step block atomically with chained events after the durable approval request. The fake-provider read→plan→approval→execute→complete path is covered locally. Session project IDs resolve through the registry; POSIX reads use descriptor walks with `O_NOFOLLOW`; Windows uses link/reparse checks plus opened-handle identity comparison with a documented residual swap window. **M1 remains in progress.** A loopback-only `eis serve` exposes public health/meta and bearer-authenticated session/task endpoints. `eis api-token` creates or rotates the local token; `eis ask --fake` provides a deterministic local smoke response. Authenticated API supports session/task operations, pending approval list/detail, digest-bound approval/rejection and execute routes, and audit-chain verification; `POST /approvals/{id}/execute` retry recovers from durable `execution_runs` evidence without repeating a consumed write and still requires the exact digest; approval routes resolve project roots from the registry and recheck policy/content hashes through the existing execution gateway. Authenticated `GET /api/v1/audit/events` provides bounded, signed-cursor paging of content-free event metadata with integrity status; short-lived browser tokens and SSE remain open; delegation remains planned. Cancellation and `human_review` continuation are implemented (ADR-047); `approval_required` uses digest-bound rejection/execute recovery. Recovery covers interrupted read-only plan steps, strict human review, successful consumed-write reconciliation, and fail-closed `execution_unverified` for consumed approvals without success evidence; broader recovery remains open. External anchoring has a tested interface plus an optional S3 Object Lock adapter, but no real bucket is configured and its mocked tests are not independent-retention evidence (ADR-048/ADR-050); a dedicated Ubuntu job now exercises the real Linux T2 backend against the digest-pinned image and checked-in seccomp profile, proving the runner protections for that image/profile at CI time (ADR-044). Broad integration/security acceptance and other deployment-specific T2 evidence remain open. M0.2 hosted CI run `45958723773` reached `conclusion=success`; branch protection still needs GitHub admin. See ADR-038 through ADR-050 in [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md).

**Objective.** A working, policy-governed single-agent loop with persistence, evidence and audit.

**Depends on.** M0.2.

**Tasks.**
- Domain entities: project, session, task, step, tool call, artifact, policy decision, approval, audit event.
- SQLite schema v1, Alembic baseline, repositories, ULID/timestamp utilities.
- Capability model and policy engine with versioned rule files.
- Execution gateway: T0 and T1, command profiles, path guard, output caps, timeouts.
- Approval gate with digest binding, TTL and single use.
- Redaction engine and secret detection (pattern rules + Gitleaks optional).
- Audit chain with append-only triggers and verification command.
- Session/task runtime with plan/act/observe loop and hard limits.
- Provider-neutral gateway with `fake` and OpenCode/OpenAI/Gemini/Anthropic/xAI adapters; optional Ollama; consent and audit checks.
- API skeleton (`/health`, `/sessions`, `/tasks`, `/approvals`, `/audit`) and CLI skeleton.
- Structured logging with correlation IDs.

**Deliverables.** Runnable `eis serve`, `eis ask` (fake provider), policy files, schema v1, tests.

**Tests.** Policy matrix; path escape; approval binding/replay/expiry; audit chain; limit enforcement; crash/resume with fake provider; redaction corpus.

**Security.** SEC-01, SEC-02, SEC-03 (partial), SEC-06, SEC-07, SEC-09, SEC-10, SEC-11, SEC-13, SEC-17. No tool call without a decision row.

**Acceptance.** A scripted fake-provider task plans, calls a read-only tool, requests approval for a write, is approved, executes, records evidence and completes. Kill the process mid-task; resume completes without duplicate side effects.

---

## M2 — Project Registry

**Objective.** Safe discovery and evidence-based profiling of local repositories.

**Depends on.** M1.

**M1 predecessor.** Migration `0005_project_roots` already provides explicit root registration, canonical path/identity persistence, overlap/reserved/link validation and content-free audit events. M2 extends that minimal registry with labels, exclusions, nested projects, scanning and profiles through a new migration; it does not replace registrations implicitly.

**Tasks.** Root configuration and validation; exclusion engine; Git adapter (read-only); directory walker; language/framework/build detection; manifest and lockfile parsers; snapshot model; incremental rescan; project CLI/API; root symlink/junction protections.

**Deliverables.** `eis project add/list/scan`, profiles with provenance, snapshot history.

**Tests.** Fixture monorepo; symlink escape; case collision; 50k-file performance; profile accuracy on 10 synthetic projects; exclusion effectiveness.

**Security.** No reads outside roots; `.git` internals excluded except via Git adapter; credentials in remotes stripped; scan budget.

**Acceptance.** Profiles are derived from content, every field has evidence, and a removed file marks the profile stale rather than deleting history. Scan of the 50k fixture meets NFR-01.

---

## M3 — Memory and Knowledge

**Objective.** Incidents, provenance, confidence and cross-project retrieval.

**Depends on.** M2.

**Tasks.** Incident/root cause/solution schemas; normalization and signature versioning; FTS5 index and sync triggers; ECS scoring; retrieval pipeline with RRF and applicability filters; reuse assessment; PAM adapter (HTTP contract + null adapter); Obsidian projection with drift detection; knowledge CLI/API.

**Deliverables.** `eis knowledge search`, incident capture/verify, reuse assessment, PAM adapter, Obsidian export.

**Tests.** Signature normalization fixtures; retrieval Recall@5 on synthetic set; ECS invariants; verified-without-run rejection; PAM timeout/circuit breaker; Obsidian scope/drift; deletion completeness.

**Security.** Untrusted content enveloped; no secrets in FTS; C2 not sent to cloud without consent; PAM read-only.

**Acceptance.** Given a target failure and a synthetic source incident, EIS returns the source incident in the top 3, states three concrete differences, and labels the reused fix as a hypothesis requiring verification.

---

## M4 — Skill Infrastructure

**Objective.** Install, validate, version and execute skills safely.

**Depends on.** M1, M3 (for evaluation context).

**Tasks.** Package format; `SKILL.md` parser (Agent Skills compatible); manifest/permissions schemas; quarantine extraction; validation stages V1-V9; trust classes; registry storage and version pointers; skill grants; execution recording; built-in skills v1 (declarative); CLI/API.

**Deliverables.** `eis skill install/list/activate/revoke`, 5 built-in skills, validation reports.

**Tests.** Malicious archive corpus; wildcard/reserved capability rejection; no execution at install; T2 evaluation; grant intersection; revocation; Agent Skills compatibility fixture.

**Security.** SEC-08; executable skills require T2 + A5; dependency changes require approval.

**Acceptance.** A package with path traversal is quarantined; a skill requesting `project.write` cannot write; revocation takes effect on the next request; a standard Agent Skills package loads with EIS metadata.

---

## M5 — Specialist Agents

**Objective.** Role-scoped delegation with typed outputs and evidence aggregation.

**Depends on.** M1, M3.

**Tasks.** Role registry and ceilings; delegation protocol; specialist prompts/policies; output envelope validation; conflict resolution; evidence aggregation; Debugging, Security, Testing, Architecture, Release and Knowledge roles; delegation persistence and UI timeline.

**Deliverables.** `eis ask --role debugging` and automatic delegation; six role configs; typed outputs.

**Tests.** Ceiling escape attempts; delegation depth; fact-without-evidence rejection; dissent recorded; budget enforcement; role-specific golden cases.

**Security.** Specialists share the gateway; no capability expansion; prompt injection corpus per role.

**Acceptance.** A debugging task delegates, returns ranked hypotheses with discriminating checks, and the primary synthesises without hiding disagreements.

---

## M6 — Security and Execution Hardening

**Objective.** Harden isolation, approvals, network, backup and recovery.

**Depends on.** M1-M5 (incremental); independent of M7.

**Tasks.** T2 container backend (Docker/Podman); T1 hardening (process groups, env scrub, limits); network egress controller and ledger; destructive operation flow with backups; approval UX (CLI/API); backup/restore tooling; integrity monitor; incident response runbook; Windows limitation warnings.

**Deliverables.** T2 profiles, restore command, egress ledger, security runbook.

**Tests.** Sandbox limit suite; escape corpus; egress denial; DNS rebinding; restore drill; audit tamper detection; backup integrity; T2 non-root/no-network validation.

**Security.** SEC-04, SEC-05, SEC-14; fail closed when T2 unavailable.

**Acceptance.** A T2 run cannot reach the network or write outside its mount; a simulated compromise is contained; restore reproduces the database and artifact hashes; the integrity monitor blocks approvals after tampering.

---

## M7 — Security and Verification Tools

**Objective.** Scanner and test-runner adapters with normalized evidence and reports.

**Depends on.** M2, M3, M6.

**Tasks.** Adapter interface and detection; Gitleaks, Semgrep, OSV-Scanner, Trivy adapters; ZAP optional; Python/Node/Flutter/Java/container runners; normalization and fingerprinting; deduplication; run ledger; coverage disclosure; security report; readiness report; accepted-risk workflow; export preview.

**Deliverables.** `eis verify`, `eis findings`, `eis report security|readiness`, run ledger, reports.

**Tests.** Seeded vulnerability fixture precision/recall; missing-tool → unavailable; unparsable output → error; dedup; report claim-evidence coverage; export redaction.

**Security.** Scanner binaries verified; T1/T2; no silent success; C2 export requires approval.

**Acceptance.** On a seeded fixture, the report finds the planted issues, states coverage and untested areas, and never claims a pass without a run record.

---

## M8 — Controlled Skill Evolution

**Objective.** Evaluate and activate skill improvements within fixed permissions, with rollback.

**Depends on.** M4, M5 (outcomes), M7 (verification signals).

**Tasks.** Outcome metrics; trigger detection; candidate generation; diff and change classification; permission-diff gate; held-out and regression suites; Wilson interval comparison; activation matrix; approvals; watchdog; rollback; evolution audit; kill switch and freeze.

**Deliverables.** `eis skill evolve/proposals/rollback`, evolution dashboard data, evaluation harness.

**Tests.** Permission expansion blocked; executable change blocked; degraded candidate mutation test; small-sample block; rollback on regression; freeze/kill switch.

**Security.** No capability change without A5; evaluation in T2; no private data egress.

**Acceptance.** A simulated skill regression rolls back within the watchdog window; no candidate with a permission delta auto-activates; all decisions have evidence.

---

## M9 — Automation

**Objective.** Durable scheduled and event-driven workflows under the same policy.

**Depends on.** M7, M8.

**Tasks.** Job model and worker; cron and event triggers; retry/backoff/dead-letter; approval pause/resume; automation definitions; project health schedule; pre-release readiness job; run records; notifications (local only).

**Deliverables.** `eis worker`, `eis job`, automation templates.

**Tests.** Kill worker mid-job; lease recovery; duplicate suppression; approval pause; dead-letter; schedule evaluation; no auto-approval.

**Security.** Jobs inherit least capability; network denied by default; consequential steps pause.

**Acceptance.** A nightly scan job survives a worker restart, produces one run record, and pauses for approval before any write.

---

## M10 — Dashboard, CLI and Integrations

**Objective.** Usable interfaces over the complete capability set.

**Depends on.** M1-M9 (read paths can start earlier).

**Tasks.** React dashboard (overview, projects, session timeline, approvals, findings, knowledge, skills, runs, settings); SSE; accessibility pass; CLI completion and shell completion; OpenCode/IDE integration via public API; activity timeline; error/empty/loading states.

**Deliverables.** Static dashboard, CLI parity, integration guide.

**Tests.** Component tests, E2E with fake provider, keyboard navigation, axe checks, API contract tests, SSE reconnect.

**Security.** Loopback origin checks, CSP, no token in URL, no secrets in UI storage.

**Acceptance.** A new user can add a root, scan, ask a question, approve a diff, run verification and export a report without the CLI; WCAG 2.2 AA on primary flows.

---

## M11 — Open-Source Beta

**Objective.** Public, reproducible, contributor-ready release.

**Depends on.** M10.

**Tasks.** Documentation site; install guides per OS; sample synthetic projects; container image; contributor workflow; issue/PR templates; CODEOWNERS; branch protection; release automation; SBOM; full-history secret scan; privacy rehearsal; name/trademark check; licence finalisation.

**Deliverables.** Tagged release, docs, samples, registry seed, security policy.

**Tests.** Clean install on three OS; upgrade from previous; privacy suite; release checklist; contributor dry run.

**Security.** No private data; signed artifacts; vulnerability disclosure live.

**Acceptance.** An external developer installs, profiles a sample project and runs a verification in under 30 minutes using public docs; the release checklist has zero unresolved items.

---

## M12 — Research and Evaluation

**Objective.** Reproducible empirical evaluation and publication.

**Depends on.** M11 (or a frozen release candidate).

**Tasks.** Literature review; pre-registration; benchmark fixtures; baseline harness A/B/C; experiment runs; analysis; ablations; threats to validity; artifact package; paper.

**Deliverables.** Benchmark, harness, dataset manifest, results, paper draft, replication package.

**Tests.** Reproduce every table; dataset digest; privacy review; rater agreement.

**Security/Privacy.** Synthetic/public data only in public artifacts; internal runs aggregated with approval.

**Acceptance.** An independent run reproduces the headline result within the reported confidence interval, or the discrepancy is documented.

---

## Release Mapping

| Release | Content | Milestones |
|---|---|---|
| v0.1.0 | Project discovery and profiles | M1, M2 |
| v0.2.0 | Knowledge retrieval | M3 |
| v0.3.0 | Cross-project incident intelligence | M3, M5 (debugging) |
| v0.4.0 | Security assessments | M6, M7 (security) |
| v0.5.0 | Testing and verification | M7 |
| v0.6.0 | Approval-based automation | M8, M9 |
| v1.0.0 | Integrated local-first platform | M10, M11 |

Each release includes install instructions, docs, tests, security results, known limitations and reproducible examples.

## Cross-Milestone Risk Register

| Risk | Milestone | Mitigation |
|---|---|---|
| Scope creep into a general agent | All | Non-goals enforced in product spec |
| Security deferred under delivery pressure | M1-M7 | Security tasks are acceptance criteria, not backlog |
| PAM coupling | M3 | HTTP contract, null adapter, no imports |
| Windows isolation overclaimed | M6 | T2 requirement, explicit warning |
| Skill ecosystem abuse | M4, M8, M11 | Validation, trust classes, revocation, curator review |
| Benchmark too weak to detect regression | M8, M12 | Mutation checks, held-out sets |
| Single-maintainer bus factor | M11 | Disclose; seek co-maintainers before public release |

## Milestone Acceptance Policy

A milestone is marked complete in `CHANGELOG.md` only after:

1. the demo script runs on a clean install;
2. the security and privacy suites for its scope pass;
3. the acceptance criteria are demonstrated with linked evidence;
4. the retrospective note is committed;
5. no new critical defect is open.

Claims of completion without evidence are treated as defects.
