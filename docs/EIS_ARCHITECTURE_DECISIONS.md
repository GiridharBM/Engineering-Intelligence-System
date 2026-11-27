# EIS Architecture Decisions

Status: Living record. Decisions below are binding for M0-M2 unless superseded by a later ADR.
Date: 2026-11-20
Owners: EIS maintainers
Related: [EIS_SYSTEM_ARCHITECTURE.md](EIS_SYSTEM_ARCHITECTURE.md), [EIS_TECHNICAL_DESIGN.md](EIS_TECHNICAL_DESIGN.md), [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md)

An ADR is required for any change that alters a module boundary, a persisted schema, a capability, a trust level, a public API version or a privacy control. ADRs are append-only; superseding is recorded, never by rewriting history.

## Index

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Independent repository, no submodules, no parent repository | Accepted |
| ADR-002 | Modular monolith with `src/eis` layout | Accepted |
| ADR-003 | Python 3.12 baseline, uv, hatchling | Superseded for support range by ADR-032 |
| ADR-004 | FastAPI + Pydantic v2 as the contract layer | Accepted |
| ADR-005 | SQLite as authoritative metadata store, SQLAlchemy 2 + Alembic | Accepted |
| ADR-006 | Experience graph as typed edge table, not a graph database | Accepted |
| ADR-007 | Provider-independent model gateway, local-first | Superseded for provider default/list by ADR-033 |
| ADR-008 | Deterministic deny-by-default policy engine | Accepted |
| ADR-009 | Single execution gateway with tiered isolation | Accepted |
| ADR-010 | Typed tool contract; MCP adapted, never passed through | Accepted |
| ADR-011 | Specialist agents as roles in one runtime | Accepted |
| ADR-012 | Layered memory with explicit source-of-truth boundaries | Accepted |
| ADR-013 | Agent Skills-compatible packages plus EIS manifests | Accepted |
| ADR-014 | Bounded skill evolution without permission expansion | Accepted |
| ADR-015 | Hash-bound, single-use approvals | Accepted |
| ADR-016 | Hash-chained audit log (tamper-evident) | Accepted |
| ADR-017 | Loopback API with per-install bearer token | Accepted |
| ADR-018 | No telemetry; explicit egress consent | Accepted |
| ADR-019 | React + TypeScript + Vite dashboard | Accepted |
| ADR-020 | Native install first, containers optional | Accepted |
| ADR-021 | Apache-2.0 licence | Accepted (2026-11-20) |
| ADR-022 | Layered privacy boundary, not `.gitignore` alone | Accepted |
| ADR-023 | Reproducible research harness with synthetic/public data | Accepted |
| ADR-024 | Secret handling and redaction pipeline | Accepted |
| ADR-025 | In-process event bus with transactional outbox | Accepted |
| ADR-026 | SQLite-backed job queue, one worker process | Accepted |
| ADR-027 | TOML configuration with environment overrides | Accepted |
| ADR-028 | Additive `/api/v1` versioning | Accepted |
| ADR-029 | Test strategy with privacy and sandbox regression suites | Accepted |
| ADR-030 | Retention and deletion classes | Accepted |
| ADR-031 | Team mode and multi-user auth out of scope for v1 | Accepted |
| ADR-032 | Supported Python range narrowed to 3.11–3.12 | Accepted |
| ADR-033 | OpenCode Chat Completions provider; no default provider | Accepted; provider scope expanded by ADR-034 |
| ADR-034 | Provider-neutral text gateway with consent and audit | Accepted |
| ADR-035 | SQLite consent, egress and model-call persistence | Accepted |
| ADR-036 | Tamper-evident append-only audit chain | Accepted |
| ADR-037 | Chained policy-decision persistence | Accepted |
| ADR-038 | Queryable decisions and bounded delegated writes | Accepted |
| ADR-039 | Minimal durable session/task runtime | Accepted |
| ADR-040 | Explicit policy-gated read-only project tools | Accepted |
| ADR-041 | Durable project-root registry and race-resistant reads | Accepted |
| ADR-042 | Strict model envelope with a bounded read-only tool loop | Accepted |
| ADR-043 | Durable task plans with a bounded step lifecycle | Accepted |
| ADR-044 | Fail-closed T2 Linux-container backend for registered profiles | Accepted |
| ADR-045 | Explicit, bounded recovery for durable read-only plans | Accepted |
| ADR-046 | Bind model-proposed plan writes to human approval and execution evidence | Accepted |
| ADR-047 | Atomic task cancellation and human-review continuation | Accepted |
| ADR-048 | External audit-chain anchor interface | Accepted |
| ADR-049 | Fail-closed recovery for consumed approvals | Accepted |

---

## ADR-001 — Independent repository, no submodules, no parent repository

**Context.** EIS lives inside a directory that contains private projects and archives. Vision §28 forbids publishing the parent directory and forbids submodules.

**Decision.** EIS is its own Git repository rooted exactly at the EIS directory. `.gitmodules` is forbidden. The parent `<projects-root>` is never a Git work tree for EIS. Private projects are accessed only as external local paths at runtime.

**Consequences.** Project discovery reads registered roots; it never vendors, copies or checks out other repositories. A CI guard fails if the repository root resolves to a parent directory or if any submodule file exists.

**Alternatives rejected.** Monorepo over `<projects-root>` (catastrophic leak risk); Git submodules (would embed private repositories); subtree copies (forbidden by the brief).

## ADR-002 — Modular monolith with `src/eis` layout

**Context.** Vision §25 proposes `apps/` and `core/` top-level packages; the product is local-first and single-user.

**Decision.** One installable Python distribution named `eis`, using a `src/` layout. Modules communicate through typed interfaces and a shared event/evidence layer. Process entry points are thin: `eis.api`, `eis.cli`, `eis.worker`. Vision module names are preserved as subpackage names (`core.agent_runtime`, `modules.project_registry`, ...).

**Consequences.** One dependency graph, one version, one migration path, simple debugging. Module boundaries are enforced by import-linter contracts in CI, not by network boundaries.

**Alternatives rejected.** Microservices and separate packages per module (operational overhead with no demonstrated need); literal `apps/`+`core/` roots (two import roots, ambiguous packaging).

## ADR-003 — Python 3.12 baseline, uv, hatchling

**Status:** Accepted 2026-11-20; support range superseded by ADR-032.

**Context.** The host default is Python 3.14.6; ecosystem support for 3.14 is incomplete. The stack is FastAPI/Pydantic/SQLAlchemy.

**Decision.** `requires-python = ">=3.11,<3.14"`; CI matrix 3.11 and 3.12; developer baseline 3.12 (available via uv). Use `uv` for environment and lockfile, `hatchling` for builds. Runtime dependencies are pinned by a committed `uv.lock`.

**Consequences.** 3.13 is allowed but not the reference; 3.14 support is a tracked compatibility task, not an assumption. Users on 3.14 are told to install 3.12 rather than receiving an unexplained failure.

**Alternatives rejected.** Unpinned latest Python (non-reproducible); minimum 3.14 (excludes most users and CI images).

**Superseded support range:** ADR-032 narrows the supported range to 3.11–3.12.

## ADR-004 — FastAPI + Pydantic v2 as the contract layer

**Decision.** FastAPI serves `/api/v1`; Pydantic v2 models are the single source for request/response and internal DTO validation. OpenAPI is generated, never hand-maintained. JSON Schema exports feed tool argument validation and skill manifests.

**Consequences.** Contract drift is a test failure, not a documentation problem. Pydantic models do not replace domain entities: persistence models are separate.

**Alternatives rejected.** Flask (weaker typed contracts); GraphQL (overkill for a local single-user tool).

## ADR-005 — SQLite as authoritative metadata store

**Decision.** SQLite in WAL mode is authoritative for projects, incidents, findings, runs, approvals, jobs, skills and audit. Use SQLAlchemy 2.x for mapping and Alembic for migrations. Enable `foreign_keys=ON`, `busy_timeout=5000`, `journal_mode=WAL`, `synchronous=NORMAL`. Writes use `BEGIN IMMEDIATE` where read-modify-write occurs.

**Consequences.** Single-writer discipline is mandatory; only the worker performs long writes, and the API uses short transactions. FTS5 provides lexical retrieval. Backup is a consistent file copy after `wal_checkpoint(TRUNCATE)` or the SQLite backup API.

**Alternatives rejected.** PostgreSQL (server dependency, against local-first); document store (no relational integrity for evidence chains).

## ADR-006 — Experience graph as typed edge table

**Decision.** The cross-project experience graph is a `edges` table (`src_type`, `src_id`, `rel`, `dst_type`, `dst_id`, provenance, weight) plus entity tables. Recursive CTEs answer multi-hop queries.

**Consequences.** No Neo4j or graph service. Traversal depth is capped (default 3) and query time budgeted. If measured queries exceed the budget at realistic scale, an ADR may introduce a specialised index; a graph database remains a last resort.

**Alternatives rejected.** Graph database (operational and backup cost, no measured need); embedding-only similarity (no explainable provenance).

## ADR-007 — Provider-independent model gateway, local-first

**Status:** Accepted 2026-11-20; provider default/list superseded by ADR-033.

**Decision.** A `ModelProvider` protocol covers chat completion with structured tool calls. Providers: `ollama` (default), `openai_compatible` (explicit configuration), `fake` (deterministic, tests only). Every call passes the egress policy: provider consent record, data classification ceiling, redaction, and a recorded `model_call`. Without a configured provider the system runs in degraded deterministic mode.

**Consequences.** No provider SDK leaks into agent code. Cloud use is impossible until a consent record exists. Model output is untrusted input.

**Alternatives rejected.** Direct SDK calls from agents (couples runtime to vendors); mandatory cloud (violates privacy).

## ADR-008 — Deterministic deny-by-default policy engine

**Decision.** Authority is represented as capabilities over scoped resources, evaluated by a pure function of (principal, action, resource, context, policy version). The model may propose a tool call; it cannot authorise one. Default decision is DENY; unmatched rules never fall through to allow.

**Consequences.** Policy is unit-testable without an LLM. New capabilities require an ADR and a policy file change. The policy version is recorded on every decision.

**Alternatives rejected.** Instruction-only guardrails ("the prompt says don't"); allow-by-default with denylist (fails open).

## ADR-009 — Single execution gateway with tiered isolation

**Decision.** All side effects go through `ExecutionGateway`. Isolation tiers:
- **T0** in-process, pure computation, no filesystem or network.
- **T1** constrained subprocess: explicit `cwd` inside a registered project, scrubbed environment, timeout, output cap, no shell.
- **T2** container (Docker/Podman): read-only project mount by default, no network, CPU/memory/PID limits, non-root user.

On Windows, T1 is **not** a security boundary and is labelled as such; untrusted or executable community skills require T2 or are refused.

**Consequences.** Honest security claims. Scanner and test adapters declare a minimum tier. If no T2 backend is available, privileged execution is disabled rather than silently downgraded.

**Alternatives rejected.** Claiming subprocess isolation is a sandbox on Windows; mandatory containers for all operations (breaks the simple local install).

## ADR-010 — Typed tool contract; MCP adapted, never passed through

**Decision.** Every tool declares a name, version, JSON Schema input/output, required capabilities, side-effect class (`pure`, `read`, `write`, `execute`, `network`), timeout and isolation tier. MCP servers are wrapped by an EIS adapter that exposes only allowlisted tools, rewrites descriptions as untrusted text, and enforces EIS policy. MCP resources are ingested as untrusted data.

**Consequences.** MCP cannot widen authority. Tool output is schema-validated and size-capped before entering context.

**Alternatives rejected.** Direct MCP tool exposure (bypasses policy, trusts remote descriptions); free-form function calling without schemas.

## ADR-011 — Specialist agents as roles in one runtime

**Decision.** Six specialist roles (Debugging, Security, Testing, Architecture, Release, Knowledge) are configurations of one runtime: distinct system policies, allowed skills and output schemas; shared session, policy, gateway, evidence and model gateway. They are not processes or services.

**Consequences.** Delegation is a function call with a task record, not a network hop. A specialist cannot escalate privileges because it has no separate identity with broader rights. Concurrency is bounded by the orchestrator.

**Alternatives rejected.** Separate agent services (operational cost, inconsistent policy); single monolithic prompt (no specialisation or evidence separation).

## ADR-012 — Layered memory with explicit source-of-truth boundaries

**Decision.** Session memory is ephemeral and stored with the session record. Engineering and operational memory live in SQLite. PAM is an optional retrieval capability behind a versioned adapter; EIS never imports PAM code. Obsidian is a one-way projection into a managed folder. Git remains authoritative for source; artifacts are authoritative evidence.

**Consequences.** Deleting PAM does not destroy EIS knowledge. Obsidian edits do not silently mutate EIS state; conflicts are reported, not merged.

**Alternatives rejected.** PAM as primary store (couples lifecycles); Obsidian as database (no transactions or referential integrity).

## ADR-013 — Agent Skills-compatible packages plus EIS manifests

**Decision.** Every skill is a directory containing `SKILL.md` with Agent Skills-compatible frontmatter (`name`, `description`; optional `license`, `compatibility`, `metadata`, `allowed-tools`), plus EIS files: `manifest.yaml`, `permissions.yaml`, `tests/`, `evaluations/`, `changelog.md`. EIS-specific data lives in the manifest, never in non-standard top-level frontmatter keys. Skills are declarative-first; executable content is a separate, higher trust class.

**Consequences.** Skills remain portable to other agent tools while EIS carries provenance, permission and evaluation metadata. `allowed-tools` is advisory; the EIS policy engine remains authoritative.

**Alternatives rejected.** Proprietary skill format (no ecosystem reuse); raw prompt files (no permissions or evaluation).

## ADR-014 — Bounded skill evolution without permission expansion

**Decision.** Evolution may change instructions, examples, retrieval hints and evaluation cases. It may not add tools, capabilities, network access, dependencies or executable files. Candidate activation requires: static validation, no permission diff, evaluation pass ≥ active version, regression pass, and recorded evidence. Permission or code changes require human approval. Any activation is reversible by immutable version reference.

**Consequences.** Automatic improvement cannot become privilege escalation. Evaluation gaming is limited by held-out cases and by requiring verification-backed outcomes.

**Alternatives rejected.** Fully autonomous self-modification (violates the guiding principle); no evolution (forfeits the product differentiator).

## ADR-015 — Hash-bound, single-use approvals

**Decision.** An approval binds `action_type`, canonicalised arguments, the target resource set and a diff hash (when a modification is proposed). Changing any bound input invalidates the approval. Approvals expire (default 30 minutes), are single-use, and are recorded with the deciding actor and policy version.

**Consequences.** "Approve once, apply anything" is impossible. Re-planning after approval requires re-approval.

**Alternatives rejected.** Session-level blanket approval; approval by natural-language description only.

## ADR-016 — Hash-chained audit log

**Decision.** `audit_events` is append-only with `seq`, `prev_hash`, `hash = SHA-256(prev_hash || canonical event JSON)`. ADR-036 specifies the exact canonicalization. A daily anchor digest is written to a separate file; the anchor is not implemented yet. Application roles cannot update or delete audit rows; enforcement is by triggers plus repository API.

**Consequences.** Tampering is detectable, not impossible. The threat model states this limitation explicitly. Verification tooling recomputes the chain.

**Alternatives rejected.** Plain logs (no tamper evidence); external append-only service (cloud dependency).

## ADR-017 — Loopback API with per-install bearer token

**Decision.** API binds `127.0.0.1` by default. A 256-bit token is generated at first run into the runtime directory with user-only permissions. Browser requests require the token via same-origin session bootstrap plus `Origin` and `Host` validation; CORS is disabled by default. No multi-user accounts in v1.

**Consequences.** Local malware running as the same user can still read the token; this is stated as a residual risk. The design blocks network peers and DNS-rebinding pages, not a compromised host.

**Alternatives rejected.** No auth on loopback (any local process or malicious page can act); full OAuth (disproportionate for v1).

## ADR-018 — No telemetry; explicit egress consent

**Decision.** EIS sends no telemetry, crash reports or usage data. Network egress occurs only for: a configured model provider with a consent record, a user-initiated Git remote operation, or a user-installed scanner that requires updates (explicitly triggered). Every egress is recorded with destination class, purpose and payload hash.

**Consequences.** Product analytics are unavailable by design. Debugging relies on local logs the user chooses to share.

**Alternatives rejected.** Opt-out telemetry (violates privacy-first positioning).

## ADR-019 — React + TypeScript + Vite dashboard

**Decision.** React 19 + TypeScript + Vite, TanStack Query for server state, React Router, Tailwind CSS with Radix primitives for accessible components. No SSR, no Next.js. The dashboard is a static build served by FastAPI or the Vite dev server.

**Consequences.** One deployable API, no Node runtime in production. Accessibility and keyboard navigation are acceptance criteria, not polish.

**Alternatives rejected.** Next.js (server runtime and complexity with no need); component megaframework (bundle and upgrade weight).

## ADR-020 — Native install first, containers optional

**Decision.** Primary install: `uv tool install eis` or `pipx install eis`, plus a Node build only for contributors. Docker/Podman images are provided for isolation and reproducibility, not required. No Kubernetes, no cloud dependencies.

**Consequences.** The tool works on a developer laptop with Ollama. Container users get stronger isolation for untrusted skills.

## ADR-021 — Apache-2.0 licence (accepted)

**Status:** Accepted 2026-11-20 by the project owner. `LICENSE` and `NOTICE` are present. Dependency-licence review remains a release gate.

**Decision.** Adopt Apache License 2.0 for the EIS codebase, with a `NOTICE` file and SPDX headers. Skill packages may carry their own compatible licences; the registry rejects unlicensed packages.

**Consequences.** Patent grant and trademark clause suit a security-oriented developer tool. GPL dependencies would be incompatible with the intended distribution model and must be excluded or isolated.

**Alternatives rejected.** MIT (simpler, no explicit patent grant); AGPL (stronger reciprocity but deters commercial adoption and complicates the registry).

## ADR-022 — Layered privacy boundary

**Decision.** Privacy is enforced by six independent layers: (1) separate repository root; (2) deny-by-default ignore rules; (3) runtime data outside the repository; (4) data classification with egress checks; (5) secret scanning/redaction; (6) automated privacy regression tests and release gates. No single layer is trusted.

**Consequences.** `.gitignore` is a convenience, not a control. Tests fail the build if a private path, secret-shaped string or runtime database appears in tracked files.

**Alternatives rejected.** Ignore-file-only protection (explicitly rejected by vision §28).

## ADR-023 — Reproducible research harness with synthetic/public data

**Decision.** Benchmarks use synthetic repositories generated from templates and permissively licensed public repositories pinned by commit. Private projects may be used only for internal evaluation with explicit approval, and never published. Every experiment records model, prompt hash, skill version, tool versions, seed and dataset digest.

**Consequences.** Published results are reproducible from the artifact package. Internal results on private data may be reported only in aggregate with approval.

## ADR-024 — Secret handling and redaction pipeline

**Decision.** Secrets are classified C3 and are never persisted in logs, artifacts, model prompts or the database. Detection uses pattern rules plus Gitleaks when available. Redaction happens before persistence and before egress; detected values are replaced with stable placeholders (`<SECRET:type:hash8>`), with the mapping held only in memory for the active operation. Environment variables are the default secret source; an OS keyring is optional.

**Consequences.** Some debugging context is lost; this is accepted. Redaction is best-effort, so C3 content is also blocked by policy from entering model context at all.

## ADR-025 — In-process event bus with transactional outbox

**Decision.** Domain events are written to an `outbox` table in the same transaction as state changes and dispatched in-process to subscribers. The worker polls the outbox. No message broker.

**Consequences.** At-least-once delivery; subscribers must be idempotent. Events survive crashes. Event schema is versioned.

## ADR-026 — SQLite-backed job queue, one worker process

**Decision.** Jobs are rows with status, attempts, `run_after` and a lease. One worker process claims jobs with `BEGIN IMMEDIATE` and a lease timeout. Scheduled jobs use a cron-like expression evaluated by the worker. Retries use exponential backoff with jitter and a dead-letter state.

**Consequences.** Simple and durable for a single machine. Multiple workers are not supported in v1; scaling requires an ADR.

## ADR-027 — TOML configuration with environment overrides

**Decision.** Configuration precedence: built-in defaults < user config TOML in the runtime directory < project-local `.eis/config.toml` (non-secret only) < environment variables (`EIS_*`) < CLI flags. Secrets are only from the environment or keyring, never TOML. Unknown keys are errors, not ignored.

**Consequences.** Misconfiguration fails loudly. Config is inspectable with `eis config show --redacted`.

## ADR-028 — Additive `/api/v1` versioning

**Decision.** Breaking changes require `/api/v2`; additive fields and endpoints are allowed within v1. Clients must ignore unknown fields. Deprecations are announced one minor release ahead and recorded in `CHANGELOG.md`.

## ADR-029 — Test strategy with privacy and sandbox regression suites

**Decision.** Required suites: unit, schema/contract, integration with a temporary SQLite database, golden tests for reports, policy decision tables, privacy regression, prompt-injection corpus, sandbox escape attempts, and end-to-end CLI/API smoke tests. Coverage is reported but thresholds apply to security-critical modules (policy, execution, redaction, approvals).

**Consequences.** Security tests are release blockers, not best-effort.

## ADR-030 — Retention and deletion classes

**Decision.** Classes: `ephemeral` (session scratch, 7 days), `operational` (audit, 1 year default), `engineering` (incidents, until deleted by user), `evidence` (artifacts, retained while referenced), `export` (user-managed). Deletion is a two-step soft-delete then purge, except C3 which is never stored. Audit rows are retained; payloads may be redacted but the chain remains.

## ADR-031 — Team mode and multi-user auth out of scope for v1

**Decision.** Schema includes `actor_id` for forward compatibility, but v1 has one local principal and no roles. Team mode requires a separate ADR covering identity, authorisation and shared-knowledge consent.

## ADR-032 — Supported Python range narrowed to 3.11–3.12

**Context.** ADR-003 allowed Python 3.13 and set an upper bound below 3.14. The M0.2 owner decision is a Python 3.12 baseline with support for 3.11–3.12. Python 3.13 and 3.14 are not tested against the M0.2 foundation.

**Decision.** Set `requires-python = ">=3.11,<3.13"`, pin the developer baseline to 3.12 via `.python-version`, and run the CI test matrix on 3.11 and 3.12 only. Ruff and mypy target 3.11 so code remains compatible with the lowest supported interpreter.

**Consequences.** Users on 3.13 or 3.14 receive an explicit install refusal instead of an untested environment. Re-adding either version requires an ADR with test evidence.

**Supersedes.** ADR-003 support range only; the 3.12 baseline, uv and hatchling decisions remain in force.

## ADR-033 — OpenCode Chat Completions provider; no default provider

**Status:** Accepted 2026-11-20.

**Context.** M0.2 recorded Ollama as the default provider class, but no provider client existed. The owner requested an explicitly configured OpenCode-compatible provider using a local API key and model access. Official OpenCode docs were checked on 2026-11-20.

**Decision.**
- No model provider is enabled by default. `EIS_MODEL_PROVIDER` selects `opencode` or `ollama`, or is unset for disabled mode.
- The first provider is an OpenCode-compatible OpenAI Chat Completions adapter. It requires an explicit base URL, direct model ID, and API key from environment variables.
- The request is `POST {base_url}/chat/completions` with `Authorization: Bearer <key>`, `Content-Type: application/json`, and a JSON body containing `model`, `messages`, `stream: false` and optional `temperature`/`max_tokens`.
- Only models documented for OpenAI-compatible Chat Completions are supported. Native `/responses`, `/messages`, image and system-one endpoints are future adapters.
- Ollama remains an optional provider label and is not required when OpenCode is configured.
- Secrets are read at request time from the environment; they are not written to configuration files, logs, errors, API responses or examples.
- The policy engine remains authoritative. Provider output is untrusted data and can never authorize an action.

**Sources.** `https://opencode.ai/docs/console/models/` and `https://opencode.ai/docs/zen` (checked 2026-11-20). They document the OpenAI-compatible base `https://opencode.ai/inference/openai/v1`, the `/chat/completions` path for compatible models, `OPENCODE_API_KEY`, and Bearer authentication. The Zen docs also document `https://opencode.ai/zen/v1/chat/completions`; the base URL is therefore configuration, not a hardcoded default.

**Consequences.** Users must configure a base URL and model; EIS does not guess them. Models that require another protocol fail clearly until an adapter exists. Egress consent and audit persistence remain later slices.

**Supersedes.** ADR-007's Ollama-default and provider-list portions only.

## ADR-034 — Provider-neutral text gateway with consent and audit

**Status:** Accepted 2026-11-20.

**Context.** The OpenCode-only adapter proved configuration and request mapping but did not cover the requested provider set. Providers differ in endpoints, authentication and response shapes, so a single assumed Chat Completions implementation would be incorrect.

**Decision.**
- Support OpenCode, OpenAI, Google Gemini, Anthropic Claude and xAI Grok behind one typed `ModelProvider` contract and an explicit `ProviderRegistry`.
- Use vendor-specific adapters: OpenAI-compatible Chat Completions for OpenCode/OpenAI/xAI; `generateContent` for Gemini; Messages for Anthropic.
- Keep OpenCode's base URL required configuration. Use fixed official endpoints for the other providers.
- Require a provider-neutral model ID via `EIS_MODEL`; keep `EIS_OPENCODE_MODEL` only as an OpenCode compatibility alias.
- Treat text-only, non-streaming completion as the supported slice. Defer streaming, tools, images and audio.
- Require explicit provider-scoped consent for the request data class. Default to no network call. C3 is never allowed.
- Emit an egress decision and a content-free model-call audit record. Never store request content, response text, secrets or raw bodies.
- Load a local ignored `.env` only in the CLI entry point. Tests disable this and use fake settings and mock transports.
- Keep the policy engine authoritative; model output cannot authorize actions.

**Sources.** OpenAI Chat Completions, Gemini `generateContent`, Anthropic Messages, xAI REST and OpenCode Console/Zen documentation, checked 2026-11-20. Links are recorded in [EIS_MODEL_PROVIDER_CONFIGURATION.md](EIS_MODEL_PROVIDER_CONFIGURATION.md).

**Consequences.** Models must match the supported protocol for their provider. OpenAI/xAI Responses, Gemini Interactions, native tool calls and streaming require later adapters. Consent is in-process until the SQLite consent store exists.

**Supersedes.** ADR-033's provider scope only.

## ADR-035 — SQLite consent, egress and model-call persistence

**Status:** Accepted 2026-11-21.

**Context.** The provider slice used an in-process consent gate and optional JSONL audit output. Consent must be revocable and auditable across restarts, and provider calls require durable evidence without storing prompts or secrets.

**Decision.**
- Add an Alembic-managed SQLite schema with `consents`, `egress_decisions` and `model_calls`.
- Use WAL, foreign keys, `busy_timeout=5000`, `synchronous=NORMAL`, `STRICT` tables and epoch-millisecond timestamps (ADR-005).
- `consents` stores a `model_provider` scope, provider subject, data classes, purpose, grant time, optional expiry, revocation time and evidence note. Revocation is a timestamp update, never a deletion.
- The environment consent (`EIS_MODEL_CONSENT_CLASSES`) is an insert-only bootstrap keyed by its deterministic consent ID. A revoked ID is not re-granted on restart.
- `egress_decisions` and `model_calls` store only metadata: provider, model, request hash, timestamps/latency, status, usage, finish reason, provider request ID and consent reference. No prompts, response text, keys or raw bodies.
- The durable gateway uses SQLite-backed consent and audit by default; in-memory/JSONL sinks remain for tests and diagnostics.
- Hash-chained `audit_events` and SQLite backup policy remain future work.

**Consequences.** Consent survives restarts and revocation takes effect on the next lookup. At the time of this ADR the audit tables were not hash-chained; ADR-036 now chains consent, egress and model-call events.

**Supersedes.** The in-process-only consent/audit portion of ADR-034. The hash-chain limitation is superseded by ADR-036.

## ADR-036 — Tamper-evident append-only audit chain

**Status:** Accepted 2026-11-21.

**Context.** Consent, egress and model-call projection rows were durable but mutable. ADR-016 requires a hash-linked audit log, and future approval-sensitive paths need a deterministic integrity check before acting.

**Decision.**
- Add a `0002_audit_chain` Alembic migration creating `audit_events` with `seq`, stable `id`, `occurred_at`, `actor`, `event_type`, `subject_type`, `subject_id`, `decision`, content-free `payload_json`, `prev_hash` and `hash`.
- Canonical event JSON is UTF-8 with sorted keys, `","`/`":"` separators and non-ASCII preserved. `hash = SHA-256(prev_hash ASCII || canonical_json(fields excluding hash and prev_hash))`. Genesis `prev_hash` is 64 zeros; `seq` starts at 1 and is contiguous in append order.
- Database triggers reject updates, deletes, sequence gaps and inserts whose previous hash does not match. Application code also recomputes the full chain before every append.
- Chain boundaries: consent grant/revoke, every egress allow/deny, and every model-call outcome. Each projection row and its chain event commit in one SQLite transaction.
- `AuditChainStore.status()` and `require_intact()` implement the `AuditIntegrityGate` interface. `eis audit verify` reports pass/failure without payloads. A broken chain blocks appends and therefore provider calls; future approval paths must call `require_intact` before granting.
- This provides tamper evidence, not tamper-proof storage. A direct database actor can drop triggers and rewrite or truncate the chain. External `integrity_anchors` checkpoints and tail-truncation detection remain future work.

**Consequences.** Audit rows cannot be corrected in place; corrections require a new event. Every append scans the chain, which is acceptable for M1 but needs checkpointing as volume grows. Integrity checks are local and do not prove that a remote copy is unchanged. Policy decisions are now chained through ADR-037; approvals remain outside the chain because they are not implemented.

**Supersedes.** ADR-035's statement that audit tables are not hash-chained; ADR-016 remains the governing requirement.

## ADR-037 — Chained policy-decision persistence

**Status:** Accepted 2026-11-21.

**Context.** The policy engine produced deterministic `PolicyAuditRecord` values, but they were not persisted or chained. Policy decisions must be auditable without making the pure evaluator write to storage or depend on a model.

**Decision.**
- Keep `PolicyEngine.evaluate` deterministic and side-effect free.
- Add `PolicyService`, which evaluates through the engine, calls a `DecisionSink`, and returns the decision only after the sink succeeds. Sink failure or uncertain chain integrity propagates and blocks the caller.
- Add `SqlitePolicyDecisionSink`, which appends one `policy_decision` event to `audit_events`. The event ID is the deterministic decision ID, so retrying the same request and policy returns the existing event instead of appending a duplicate. A different event under the same ID fails closed.
- The payload stores decision ID, effect, reason, rule ID, policy version/hash, inputs hash, principal, action, canonical resource and context source. Context attributes and task IDs are omitted; absolute POSIX paths and drive-letter paths are rejected, so only validated relative path references can be stored.
- Add `eis policy-evaluate` to exercise the service. `policy-check` remains a read-only diagnostic and does not record an event.
- At the time of ADR-037, a queryable `policy_decisions` projection remained planned; ADR-038 adds it while retaining the chain event as the integrity source.

**Consequences.** Only decisions evaluated through `PolicyService` are chained. Code that calls the engine directly stays pure and unaudited, so execution and approval paths must use the service. Retrying a request records one decision event with the first timestamp. Approvals and execution outcomes were future work at adoption and are bounded by ADR-038.

---

## ADR-038 — Queryable decisions and bounded delegated writes

**Status:** Accepted 2026-11-21.

**Context.** Policy decisions were chained but hard to query. The M1 permission model also requires exact-diff human approval before a delegated project write, plus audit visibility into execution outcomes.

**Decision.**
- Add an append-only `policy_decisions` projection populated in the same transaction as its authoritative `policy_decision` chain event; existing events are backfilled by migration 0003.
- Persist A3 file-write approvals with private local diff/content, immutable action/resource/root/content/policy binding, a short expiry, explicit grant/reject transitions and one-time consumption. Rejection notes stay in the local approval row; the audit chain stores hashes and content-free metadata only.
- Require project proposal permission for the agent and current user read/write allows through `PolicyService`. The approval digest binds the target, canonical project-root hash, original/new content hashes, diff, summary, policy decision/version and expiry.
- Permit execution only through `ExecutionGateway`: registered project root, relative path, reserved secret/config paths, no symlink/junction target, unchanged target hash, backup before atomic replacement, and rechecked policy and chain integrity.
- Support only fixed application-registered command profiles. They use argument arrays, no shell, explicit project cwd, a reduced environment, timeout, output cap and process termination. T1 on Windows is not a security boundary; untrusted projects and T2 profiles are refused until a container backend exists.
- Provide CLI review that displays the exact diff and requires the user to supply its digest and confirm before granting. A grant is bound to one proposal and is single-use.

**Consequences.** Approval content is private local SQLite data while chain events remain content-free. Expiry is enforced, but expired rows remain in `pending` and are hidden from the pending list until a future expiry transition exists. The implementation covers project-file writes and fixed-profile verification only; delete, Git mutations, network actions, generic obligations, T2 isolation, agent-loop integration and external audit anchors remain future work. Filesystem race resistance and Windows isolation need platform-specific hardening before stronger security claims.

## ADR-039 — Minimal durable session/task runtime

**Status:** Accepted 2026-11-21.

**Context.** M1 needed durable session/task identity and a single-agent path through the existing model gateway without creating a second policy, audit or execution authority. Model output is untrusted and no action parser exists yet.

**Decision.**
- Add migration `0004_sessions_tasks` with explicit session states (`active`, `idle`, `closed`) and task states (`created`, `running`, `blocked`, `completed`, `failed`, `cancelled`), timestamps and an optional validated project identifier.
- Persist session title and task goal as private local input data; audit only their SHA-256 hashes. Task goals are never placed in audit payloads or CLI output.
- Add `SessionTaskRuntime`. It creates/resumes sessions, creates tasks, evaluates `task.run` through `PolicyService`, requests exactly one completion through `ModelGateway`, and records task transitions through `SessionTaskStore`.
- A task completion does not execute anything. Successful model output leaves the task `blocked` with `stop_reason=human_review_required`; the response text is returned only in memory as an untrusted proposal and is not persisted or printed by default.
- Consent/egress/model-call checks remain in the existing gateway. Consent denial, policy denial and provider errors transition the task to `failed` with content-free reasons.
- Restart/resume loads SQLite state. A `created` task can run; a `running` task left by a crash becomes `failed` with `interrupted` and is not re-sent to the provider.
- CLI adds session create/list/show/resume/close and task submit/resume/status/list. `--show-proposal` is opt-in and sanitizes control characters; no action is taken.

**Consequences.** There are no persisted messages or conversation history, no plan/step loop, no model-originated tool dispatch and no approval expansion. Explicit read-only project tools are bounded by ADR-040. A blocked proposal cannot be recovered after restart. Task goals are private local data and require the existing retention/backup policy. This does not satisfy the M1 acceptance scenario for a full policy-governed tool loop; that remains future work.

---

## ADR-040 — Explicit policy-gated read-only project tools

**Status:** Accepted 2026-11-21.

**Context.** The minimal session/task runtime intentionally stops after one model completion. A bounded read-only tool slice can provide useful project inspection while preserving the rule that model output cannot autonomously select or execute actions.

**Decision.**
- Register only four built-ins: `project.list`, `project.list_files`, `project.read_text`, and `git.status`. Do not accept executable names, shell text, arbitrary arguments, writes, deletes, network actions or provider-native tool calls.
- Parse explicit tool requests with strict Pydantic schemas (`extra="forbid"`, strict types, duplicate JSON keys rejected) and exact registered names. Model response text is never fed to this parser; invocation is a separate explicit runtime/CLI operation.
- Inject the runtime principal and project-root registry from trusted application construction. Never accept principal or absolute root paths from a tool-call payload. The CLI binds its explicitly supplied root to the session's project ID for that invocation.
- Before reading the filesystem or invoking Git, evaluate the tool's typed action/resource through `PolicyService`. Persist a `tool_invocation_started` event after an allow decision and before work. Denials append `tool_invocation_denied`; outcomes append `tool_invocation_completed` or `tool_invocation_failed`. Any decision/start/outcome audit failure prevents work or withholds the result.
- Tool events contain invocation/task IDs, tool name, project ID, policy decision ID, argument/resource/output hashes, status, byte count and sanitized error codes only. File contents and raw Git output are never persisted.
- Normalize relative POSIX paths; reject absolute, traversal, drive-letter, secret-file and oversized paths. Reject symlink/junction components and enforce root containment. Exclude secret files and links from listings.
- Bound listings to 100 results and 5000 scanned entries; text reads to 32 KiB; Git status to a fixed no-shell command, 5-second timeout and 64 KiB output ceiling. Git output is reduced to branch and status counts, not file names.
- Keep the stop point at human review. Read results are returned only in memory to an explicit caller; default CLI output shows IDs, status, hashes and byte counts, not file content.

**Consequences.** The invocation-time root selection described here is superseded by ADR-041, which persists explicit registrations and resolves the session project ID through that registry. The tools are read-only but expose local content to the caller that explicitly invokes them. Windows filesystem races and T2 isolation are not solved by this slice. Model proposals remain unparsed and unexecuted.

---

## ADR-041 — Durable project-root registry and race-resistant reads

**Status:** Accepted 2026-11-22.

**Context.** ADR-040 bound a caller-supplied directory to the session project ID for one CLI invocation. That made the invocation itself the root authority, kept no durable root identity, and used path-based `stat`/`open` calls that could be redirected by a link or replacement between validation and access.

**Decision.**
- Add migration `0005_project_roots` with one row per project: `project_id` primary key, normalized canonical `root_path`, `root_identity` (SHA-256 of the platform `st_dev:st_ino` identity), `created_at` and `updated_at`. The canonical path is unique. No root is created or backfilled implicitly.
- Registration is an explicit local-user CLI action (`eis project register|list|remove`). Validation rejects missing paths, non-directories, filesystem roots, runtime data/config locations, overlapping or duplicate roots, and any path that resolves through a symlink or junction. A model cannot register, move or remove a root.
- `task invoke-tool` accepts only a session ID and a strict call JSON. The session's project ID is resolved through the registry at invocation time; a caller-supplied root path is no longer accepted. Missing, moved, renamed or replaced roots fail closed with a sanitized error code.
- POSIX reads and listings walk from a verified root descriptor with `O_NOFOLLOW` and `dir_fd`, so containment and the read refer to the same opened objects. Windows checks every component for links/reparse points, compares the opened handle identity with the pre-open identity, bounds the read, and re-checks the root and path after the read. A narrow path-swap window remains on Windows because the Python standard library has no `openat`/root-directory-relative handle API; this is documented rather than claimed as complete TOCTOU protection.
- Root registration/removal appends content-free `project_root_registered`/`project_root_removed` events containing the project ID, canonical-path SHA-256 and identity hash. No absolute path enters the chain or default CLI output.
- Existing policy, audit-before-work and content-free audit rules remain unchanged; the default policy still denies reads and Git unless an explicit policy file allows them.

**Consequences.** A moved or renamed root must be removed and re-registered; a replaced root is rejected by identity. Identity detection is only as strong as the filesystem's `st_dev`/`st_ino`; filesystems that report zero or unstable IDs weaken replacement detection. Windows retains a residual swap window and is not a security boundary against a local attacker with write access to the root. The M2 project-registry work must extend this minimal table through a new migration rather than assuming the planned `project_roots` shape. T2 isolation, model-to-tool bridging and external audit anchoring remain unimplemented.

---

## ADR-042 — Strict model envelope with a bounded read-only tool loop

**Status:** Accepted 2026-11-23.

**Context.** ADR-039 kept model output as an unparsed in-memory proposal and ADR-040/041 made tool invocation a separate explicit operator action. That left the runtime unable to use its read-only tools during a task run. This slice connects them while keeping the model untrusted: every response must be a strictly validated JSON envelope, authority stays derived from durable runtime state, and the loop is bounded in calls, completions and wall time. It supersedes the "no model-originated tool dispatch" consequence of ADR-039/040 for the four read-only tools only; plans, delegation, approvals and execution remain outside this loop.

**Decision.**
- `ModelGateway` stays text-only. Every response text is parsed by `eis.core.model_envelope.parse_model_response`, which accepts exactly two shapes: `{"kind": "answer", "content": str}` and `{"kind": "tool_call", "tool": str, "arguments": object}`. Key sets must match exactly; duplicate JSON keys, unknown keys, unknown kinds, non-object arguments, blank answers and responses over 8192 bytes are rejected with a content-free error code that stops the run.
- Model output can never carry authority or filesystem roots. Keys such as `project_id`, `principal`, `root`, `root_path`, `policy`, `session_id`, `task_id`, `capability` and `approval_id` are forbidden inside `arguments` and rejected as `unexpected_field`. The session, task, principal and project ID are injected from durable runtime state: the project ID comes from the session row and is resolved through the ADR-041 root registry.
- Tool requests go through the existing typed registry and `PolicyService` unchanged: schema/registration validation first, then a persisted policy decision and `tool_invocation_started` audit event before any filesystem or Git access. A policy or audit failure prevents execution and ends the run.
- The loop is bounded per task run: at most 3 model completions and 2 tool calls, a 60-second wall clock, a 32 KiB input cap and a 4000-character cap on tool feedback re-entering the prompt. It stops on malformed output, policy denial, consent/provider failure, a repeated identical tool call, or limit exhaustion. An interrupted `running` task is never auto-resumed; `run_task` refuses it and `resume_task` records `interrupted`.
- Prompts, responses, file contents and tool results stay in memory. Persisted state and audit payloads carry hashes, statuses, counts and sanitized error codes only; run counts merge into the existing `usage_json`/audit `usage` payload with no migration. The final answer reaches `TaskOutcome.proposal_text` for the explicit caller only; the CLI prints `answer=untrusted_model_generated_no_action_taken` by default and shows labeled `model_generated_answer_begin/end` content only with `--show-proposal`.
- No shell, write, delete, network, approval grant or model-controlled policy change exists in this loop. It is not T2 isolation.

**Consequences.** A malformed or hostile response now fails the task instead of being ignored, so provider formatting drift becomes an operational stop reason (`malformed_response`, `unexpected_field`, `unknown_kind`, `response_too_large`). The bounded loop can exhaust on long reads and must be re-run explicitly; there is no auto-resume. The envelope contract is enforced by code, not by prompt wording, but a model that cannot emit the envelope cannot complete tasks. T2 isolation, external audit anchoring, startup/scheduled integrity checks and broader approvals remain unimplemented; M1 is not complete.

---

## ADR-043 — Durable task plans with a bounded step lifecycle

**Status:** Accepted 2026-11-23.

**Context.** ADR-042 bounded model output to one answer or one read-only tool call per completion and persisted no plan state. That avoided authority leaks but left no durable, inspectable ordering: a multi-step read could not be reviewed, interrupted or audited as one unit. The full plan/act loop needs plans, but model-generated plans are untrusted text and must not become a capability expansion.

**Decision.**
- Migration `0006_task_plans` adds `task_plans` (one per task, bound to the task, session project ID, policy version, policy hash, plan hash and creation time) and `task_plan_steps` (ordered by `step_index`, with step reference, registered tool or `human_review`, dependency IDs, status, policy decision ID, output hash and error code). SQL triggers enforce the plan state graph `created -> running -> {blocked, completed, failed, cancelled}` and step graph `pending -> {running, blocked, skipped}`, `running -> {completed, failed, skipped}`; terminal states are immutable.
- `eis.core.model_plan.parse_plan_response` accepts only one JSON object with exact keys and 1–6 steps. Duplicate keys, unknown fields, oversized responses, invalid step IDs, missing or cyclic dependencies, duplicate step IDs, unregistered tool names, forbidden argument/identity keys and instruction tokens (identity, root, policy, command, write, delete, network, approval and similar) fail with a fixed, content-free code before anything is persisted.
- A plan may contain only the four registered read-only tools or a `human_review` marker. Arguments are validated through the existing typed tool registry with a placeholder project; the real project ID is injected from the durable session at execution. There is no parallel execution, no retry, no write/delete/network/shell tool and no model-controlled approval or policy field.
- `PlanStore` persists the plan, its steps and content-free `plan_created`/`plan_step_created` audit events in one transaction; every later state change persists the row update and its `plan_state_changed`/`plan_step_state_changed` event in the same transaction. Only hashes, statuses, counts and sanitized codes are stored. Goals, plan text, arguments, file contents and tool results are not stored in the plan tables or the audit payloads.
- `SessionTaskRuntime.run_plan` evaluates the same `task.run` policy decision as the ADR-042 loop, generates one plan completion, persists the plan, then runs only dependency-ready steps through `ReadOnlyProjectTools` and `PolicyService`. It stops on policy denial, audit failure, provider/consent failure, malformed output, repeated identical calls, a missing root or a `human_review` step. A `running` plan found after a restart is failed with `interrupted` by `resume_plan`; nothing auto-resumes.
- `eis plan run|show|steps` exposes IDs, states, hashes and sanitized statuses. Plan text appears only on explicit `--show-proposal`, inside `model_generated_plan_begin/end` labels; the default output states that the plan is untrusted and no action was taken from it.

**Consequences.** A task can be inspected after interruption, but a rejected plan is not repaired: the operator must submit a new task. A plan that needs human review stops permanently in this slice; approval/resume of blocked plans is future work. The step arguments are not retrievable from storage by design, so `plan show` cannot reconstruct what was proposed. T2 isolation, external audit anchoring and broader approvals remain unimplemented; M1 is not complete.

---

## ADR-044 — Fail-closed T2 Linux-container backend for registered profiles

**Status:** Accepted 2026-11-24.

**Context.** ADR-038 allowed only T1 fixed profiles and explicitly refused T2 until a container backend existed. Untrusted projects (Z3, assumed hostile) must not be verified by a same-user subprocess, especially on Windows where T1 is not a security boundary. Model output is untrusted and must never select an executable, image, mount, argument or isolation setting.

**Decision.**
- Add a narrow `SandboxBackend` protocol (`capabilities` plus one `run(request)`) and `LinuxContainerBackend`. `ExecutionGateway` accepts an optional backend; T2 is used only for application-registered profiles with `minimum_tier="T2"` and automatically for any profile run against a project not in the trusted set. If no backend can demonstrate the required protections, the run is refused with the sanitized code `isolation_unavailable` before execution and before an execution-start event.
- Profile identity, argv, timeout, output cap and environment allowlist are application-defined only. `CommandProfile` rejects secret-shaped or non-uppercase environment keys, more than 16 keys, and a writable workspace without T2. Model output has no field that can reach a container request.
- The backend requires an immutable application-supplied image digest (`name@sha256:<64 hex>`), an application-supplied seccomp profile path, and a capability probe. Every run uses a fixed argv: `--pull=never`, `--network=none`, `--read-only`, non-root UID/GID, `--cap-drop=ALL`, `no-new-privileges`, an explicit seccomp profile, CPU/memory/PID limits, a bounded output cap and a wall-clock timeout. No privileged flag, host network, Docker socket mount or environment passthrough is used.
- Each run copies only the registered project into a temporary disposable workspace, excluding `.git`, `.eis`, secret names/suffixes, credential directories, backups and runtime database files. Read-only profiles mount that copy `:ro`; writable profiles mount the disposable copy `:rw` and discard it when the run ends. A container with a unique name is force-removed in a `finally` path, including on timeout.
- Only declared non-secret environment keys are read from the host and capped in length, plus a fixed `PYTHONDONTWRITEBYTECODE=1`. Host credentials, API keys, SSH/cloud configuration, the EIS database, backups and other project roots are never mounted or passed.
- Capability detection is conservative. On non-Linux hosts the probe reports insufficient capabilities and T2 is refused; a Windows container engine is not claimed to provide Linux-container protections. On Linux the probe must actually run the full fixed flag set with `--pull=never` and confirm non-root plus read-only root/workspace; any missing or refused protection means T2 stays unavailable.
- Existing policy (`project.verify` through `PolicyService`), audit-integrity and execution-run recording gates stay ahead of backend dispatch. Denials, audit failures, backend errors, timeouts and truncation yield sanitized statuses/errors; no container output, image path or host path is added to audit payloads or default output.

**Consequences.** T2 can be configured only on Linux with a verified engine, immutable image and seccomp profile; on this development host (Windows) T2 remains unavailable and untrusted projects are refused. The probe uses a fixed shell in the configured image, so a minimal image without `/bin/sh` fails closed. Container engines can still be compromised through kernel or daemon vulnerabilities, and a local attacker with daemon access can bypass these flags; these are documented residual risks, not claims of a security boundary. T1 on Windows remains explicitly not a security boundary. Delegation, broad resource resolution, redaction, external audit anchoring and M0.2 GitHub evidence remain outstanding.

---

## ADR-045 — Explicit, bounded recovery for durable read-only plans

**Status:** Accepted 2026-11-24.

**Context.** ADR-043 deliberately omitted step arguments from persistence, so a process interruption could only fail the plan. The M1 acceptance requires a plan to continue after restart without repeating non-idempotent side effects. Current plan steps are restricted to registered read-only tools or a terminal human-review marker; read-only calls may safely repeat if interruption occurs between tool completion and its durable completion record.

**Decision.**
- Add migration `0007_plan_recovery` with immutable `arguments_json` and a guarded `attempt_count` (0–2) per plan step. The arguments remain private local SQLite data and are excluded from the audit chain, plan display, and default CLI output. Existing `0006` rows retain `NULL` arguments and cannot be resumed.
- An explicit `eis plan resume <task_id>` / `eis task resume <task_id>` rechecks current policy version/hash, session state and registered project root. There is no automatic startup recovery.
- A running read-only step is returned to `pending` only with the content-free `interrupted_retry` marker. The step can start at most twice. Completed steps are skipped; an in-flight read-only step may be repeated because it has no write side effect. Audit events for recovery remain content-free and transactional with the status update.
- A changed policy, closed session, missing root, legacy plan without arguments, or exhausted retry limit fails closed and marks the task/plan failed.

**Consequences.** New read-only plans can continue explicitly after an interrupted process without regenerating a plan or replaying completed steps. The database now stores validated tool arguments locally, which requires normal local database access controls and backup retention. This does not recover human-review-blocked plans or write approvals, and it does not satisfy the end-to-end M1 approval/write acceptance by itself.

---

## ADR-046 — Bind model-proposed plan writes to human approval and execution evidence

**Status:** Accepted 2026-11-24.

**Context.** The bounded plan runtime could read registered project files but was disconnected from the existing digest-bound approval/execution path. M1 acceptance calls for a fake-provider task to read evidence, request approval for a write, wait for a human, execute the exact approved diff and complete with durable evidence.

**Decision.** A strict plan may contain at most one `project.propose_write` step, only as its final step. Its path/content/summary are validated as untrusted data and passed through the existing `ExecutionGateway` proposal path with the originating task ID bound into the approval digest. Model output cannot grant, execute, choose a root or change policy. Approval creation is idempotent by task. After the approval row exists, one `BEGIN IMMEDIATE` transaction verifies task/plan/step/approval binding and blocks the proposal step, plan and task while idling the session and appending content-free chained events. A crash before this transaction leaves a running plan and pending step; explicit resume rechecks policy/session/root and reuses the same approval. Existing CLI commands remain the only grant/reject/execute path. Successful execution evidence completes the blocked step/plan/task atomically; rejection fails them atomically. Migration `0008_task_approval_link` adds the task binding and transition guards.

**Consequences.** The fake-provider read→plan→approval→execute→complete flow is locally testable without a live provider. Proposal content and diffs remain private local approval data, excluded from audit payloads and default output. Authenticated runtime API/`eis serve`, delegation, human-review continuation, broader approval types, arbitrary tool execution and provider/model recovery are not implemented. Redaction remains pattern-based defense in depth; real Linux T2 host evidence is still required. M1 is not complete.

---

## ADR-047 — Atomic task cancellation and human-review continuation

**Status:** Accepted 2026-11-27.

**Context.** Blocked durable plans (ADR-043) could be created, recovered from interruption (ADR-045) and used for digest-bound writes (ADR-046), but a `human_review` step had no continuation path and there was no uniform cancellation semantics for tasks, plans and steps. Cancelling must not undo work or race a successful approval execution.

**Decision.**
- **Cancellation.** `SessionTaskRuntime.cancel_task` requires `task.cancel` through `PolicyService`, uses the existing audit sink and `AuditChainStore`, and `SessionTaskStore.cancel_task` performs task + plan + step updates + content-free chained events in one `BEGIN IMMEDIATE` transaction. Allowed sources are `created`, `running`, `blocked` `human_review_required`. Terminal tasks cannot be cancelled. A blocked `approval_required` task cannot be cancelled; use the digest-bound reject path instead. `cancelled` is terminal for tasks/plans.
- **Steps.** `pending` and blocked `human_review` steps become `skipped` with `error_code='cancelled'`; an in-flight `running` step becomes `failed` with `error_code='cancelled'`; `completed` steps are unchanged; `failed`/`blocked` `project.propose_write` approval state is not rewound. No automatic resume.
- **Successful evidence wins.** Cancellation is refused with `successful_execution_evidence_exists` whenever a linked `consumed` approval has `execution_runs.status='success'`. `ExecutionGateway.execute_write` refuses terminal tasks, and `SessionTaskStore.complete_task_after_approval` remains the only atomic completion path. A cancellation cannot mark a task `cancelled` when durable successful execution evidence exists; an already-`consumed` approval with successful evidence can still reconcile completion via existing retry recovery. No completed write is undone.
- **Approval race.** Cancellation uses `BEGIN IMMEDIATE`. `approval_required` is not cancellable, and `execute_write` rechecks task state plus `_approvals.consume`. If cancellation wins before consumption, execution fails; if execution has produced durable successful evidence, cancellation is refused.
- **Human-review continuation.** `SessionTaskRuntime.continue_plan_review` accepts only a strict `step_ref` (`[A-Za-z0-9_-]{1,24}`). The target must be the blocked `human_review` step in a blocked `human_review_required` plan on a running-recoverable task. It rechecks session, project identity/root, policy version/hash and `task.run` before `SessionTaskStore.continue_human_review`. That method completes the `human_review` step, moves plan/task `blocked -> running`, reopens session `idle -> active`, and appends content-free events atomically; then `_run_plan_steps` runs only unexecuted dependency-ready steps. No new model completion, no permission grant, no `approval.grant`, no project/root change, no policy bypass, no write approval. `project.propose_write` remains gated by ADR-046. Failed audit/policy checks leave state unchanged. `resume_plan` cannot resume `human_review`; use `continue_plan_review`.
- **Migration.** `0009_task_cancellation_review` (old migrations unchanged) recreates `tasks_state_transition`, `task_plans_state_transition`, `task_plan_steps_status_transition` to allow `cancelled`, `blocked -> running`, `human_review` `blocked -> completed`, `blocked -> skipped`, preserve ADR-046 evidence requirements, and add `NOT EXISTS` successful-evidence guards for `cancelled`.
- **Interfaces.** CLI `eis task cancel <task_id> [--policy-file ...]`, `eis plan continue-review <task_id> <step_ref> [--policy-file ...]`; API `POST /api/v1/tasks/{id}/cancel`, `POST /api/v1/tasks/{id}/review/continue` with strict body `{"step_ref":"..."}` and bearer auth. No request accepts project root, executable, policy, capability, approval digest bypass or model-controlled identity. Human continuation does not substitute for `POST /approvals/{id}/decision`.
- **Privacy.** Events contain IDs, states, tool names, `step_ref`, `error_code`, hashes; no goal, model text, plan arguments, file contents, tool results, note, root path, secret or digest exposure beyond existing approval digest fields.

**Consequences.** This closes the local cancellation and `human_review` continuation gaps without expanding capabilities. Policy `task.cancel` and `task.run` must explicitly allow; default deny applies. API policy configuration is deployment-specific. No `task.cancel` reserved capability is added. M1 remains incomplete: delegation, broader approvals, write-proposal cancellation semantics beyond existing `approval_required`, T2 real Linux evidence, external audit anchoring, broad redaction and full recovery remain outstanding.

---

## ADR-048 — External audit-chain anchor interface

**Status:** Accepted 2026-11-27.

**Context.** ADR-036/ADR-038 made the local `audit_events` chain tamper-evident, but a privileged actor can drop triggers, rewrite the chain or truncate its tail. A local SQLite row or separate local file is not independent evidence. The project has no external anchoring service or credentials, so deployment must remain explicitly unverified while the interface and checkpoint semantics are testable.

**Decision.**
- Add `eis.core.audit_anchor`: a content-free `AuditAnchor` (`anchor_id`, `up_to_seq`, `head_hash`, `created_at_ms`, `anchor_hash`, algorithm `eis-audit-anchor-v1`) and `AnchorReceipt` (`external_ref`, `received_at_ms`, `receipt_hash`). `anchor_id` and `anchor_hash` bind the checkpoint prefix; `receipt_hash` binds the anchor to the external acknowledgement. Bodies are canonical JSON, SHA-256, sorted keys, no non-ASCII escaping. No secret, credential, project content, prompt, payload text or raw path appears in an anchor or receipt.
- `AuditAnchorSink` is a narrow protocol for a separately controlled external system: `submit(anchor) -> AnchorReceipt` and `verify(anchor, receipt) -> bool`. No production backend is configured; `AuditAnchorService` fails closed with `anchor_backend_unavailable` when the sink is missing, on external submit/verify failure, on receipt mismatch or on any local chain defect.
- `AuditAnchorService.create` verifies the entire local chain, refuses an empty chain, builds a checkpoint from `event_count`/`head_hash`, submits it, validates the receipt hash, and appends one `audit_anchor_submitted` event with a deterministic `event_id`. The local event is only a reference/receipt; the authoritative checkpoint must be retained by the external system. A committed anchor is idempotent: an immediate retry returns the same submission without resubmitting or appending a second event. A failed append leaves no local event; retry may resubmit the same anchor and returns the same external receipt.
- `AuditAnchorService.verify` rechecks the whole local chain, verifies exactly the anchored prefix (`events_up_to(up_to_seq)`), compares its head hash to `head_hash`, recomputes `anchor_hash` and `receipt_hash`, requires the local reference event, and then requires the external sink to verify the receipt. Any mismatch raises `AuditIntegrityError`/`AnchorVerificationError`.
- Tests use only an in-memory fake external sink. They cover creation/verification, content-free events, idempotent replay, forged receipts, mismatched anchors, historical anchors after later events, tampered/truncated chains, external failure, audit append failure + restart/retry and no-backend refusal.
- No real external service is integrated or verified in this slice, and no local file or DB row is described as external anchoring. Deployment requires an independently controlled backend with its own retention and verification evidence.

**Consequences.** Local tail truncation after an anchored prefix is detectable if the external service retains the checkpoint; before the first anchor it remains undetectable. A sink that does not enforce independent retention or replay protection gives no security benefit. The anchor event changes the chain head, so anchors are prefixes, not the current tip. External audit anchoring remains an unverified external requirement; M1 is not complete.

---

## ADR-049 — Fail-closed recovery for consumed approvals

**Status:** Accepted 2026-11-27.

**Context.** M1 acceptance requires a task interrupted mid-run to resume without duplicate side effects. Mapping the durable boundaries showed one stranded state that prior recovery rules did not close: `ExecutionGateway.execute_write` consumes a single-use approval before writing the project file and recording `execution_runs`. A crash between the file replacement and the success row leaves the approval `consumed`, the task/plan `blocked approval_required`, and no durable execution evidence. Repeating `execute_write` is unsafe because the approval is consumed; marking the task complete or cancelled would either hide a possible write or contradict the evidence rule.

**Decision.**
- Read-only boundaries remain safe to retry explicitly: ADR-045 requeues an in-flight registered read-only step at most once after policy/session/root rechecks, and no model completion is re-sent. A completed read-only step is never repeated.
- Write boundaries remain evidence-bound: the digest-bound approval is created before any write; the human grant/reject decision is single-use; `project.write` rechecks policy, root identity, target hash and audit integrity before consumption; completion requires `approvals.status='consumed'` plus `execution_runs.status='success'`. A successful execution is reconciled idempotently by `complete_task_after_approval` from CLI/API retry.
- Add `SessionTaskStore.fail_task_after_approval_without_evidence(task_id, approval_id)` for a consumed linked approval with **no** successful execution row. It atomically moves the write step, plan and task to `failed` with `error_code`/`stop_reason='execution_unverified'` and appends content-free chained events. No write is attempted, no target is changed, and the terminal result is idempotent on repeated calls. CLI and API retries invoke it when they encounter this state.
- Migration `0010_approval_execution_recovery` adds database transition guards for `execution_unverified` on task/plan/step; older migrations are unchanged. Terminal `completed` requires successful evidence; `cancelled` remains barred when successful evidence exists.
- Recovery never auto-runs after restart. Explicit resume still rechecks policy version/hash, session, registered project root, `task.run`, audit integrity and strict step-binding. Audit failures roll back and leave states unchanged. Stale policy/root and retry-limit breaches fail closed.
- No audit payload, CLI output or event contains file contents, approval diffs, prompts, credentials or project paths.

**Consequences.** A write whose success evidence was never persisted is treated as unverified and fails the task; it is never retried and never claimed complete. This closes the stranded consumed-approval boundary without weakening approval digest or execution-evidence rules. Recovery is not general: delegation, broader step/limit recovery, external anchor backend, real Linux T2 evidence and full M1 acceptance remain open.

---

## Prior Art and Standards Position

EIS borrows patterns, not code or identity:

- **Agent Skills (agentskills.io).** EIS adopts the folder + `SKILL.md` format and progressive disclosure, but keeps permissions, provenance and evaluation in EIS manifests. `allowed-tools` is treated as advisory because implementations differ and because EIS policy must be authoritative.
- **Model Context Protocol.** EIS supports MCP through an adapter (ADR-010). MCP tool descriptions and resources are untrusted, and MCP cannot grant capabilities.
- **Hermes Agent (Nous Research, MIT).** Studied as a reference for persistent memory, a skills hub and local-first operation. EIS deliberately differs in its evidence-first knowledge model, deterministic policy gateway and approval-bound evolution. No Hermes code is copied.
- **OWASP LLM Top 10 (2025).** Used as a threat taxonomy input in [EIS_THREAT_MODEL.md](EIS_THREAT_MODEL.md).

No claim of novelty is made for these patterns; novelty claims are confined to the research plan and remain subject to literature review.
