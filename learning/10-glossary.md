# 10. Glossary

Short definitions for terms used across EIS. Each entry says where the concept appears.

## A–C

**ADR (Architecture Decision Record).** A short, dated record of a decision and its consequences. EIS keeps 32 in [`docs/EIS_ARCHITECTURE_DECISIONS.md`](../docs/EIS_ARCHITECTURE_DECISIONS.md). ADRs are append-only; a later ADR can supersede one.

**API (Application Programming Interface).** A contract for one program to call another. EIS's HTTP API is described in [`docs/EIS_API_SPECIFICATION.md`](../docs/EIS_API_SPECIFICATION.md); public health/meta plus bearer-authenticated session/task routes are implemented today; see the API status note for the exact scope.

**Artifact.** A stored output such as a log, report, patch or scanner result. Planned artifacts are content-addressed by SHA-256 and linked to evidence.

**Audit chain.** An append-only event log where each entry includes the previous entry's hash. Tampering becomes detectable. It is tamper-evident, not tamper-proof. Implemented as `audit_events` with `eis audit verify`; a tested external-anchor interface and an optional S3 Object Lock adapter exist, but no real backend is verified. See [security architecture](../docs/EIS_SECURITY_ARCHITECTURE.md).

**Capability.** A permission over a typed resource, such as `project.read` on a specific project. The current policy engine evaluates typed principal/action/resource requests with exact-match rules; the full capability registry, obligations and approval effects are planned for later M1 slices.

**C0–C3 (data classification).** Public, internal, confidential and secret. C3 values must never enter logs, prompts, artifacts or the database. See [privacy](../docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md).

**CLI (Command-Line Interface).** A terminal interface. `eis doctor`, `eis policy-check`, `eis policy-evaluate`, `eis policy-decisions`, `eis approval`, `eis audit verify` and `eis consent` are current commands.

**Context window.** The maximum number of tokens a model can process at once. EIS plans a context budget rather than dumping files into the prompt.

## D–G

**Deny-by-default.** An action is refused unless an explicit rule allows it. Current behavior is proven by `eis policy-check`.

**Decision (policy).** The result of evaluating a principal, action and resource: allow or deny. A `require_approval` policy effect is not implemented; A3 project writes instead require a separate digest-bound approval after an allow. The current result includes a rule ID, reason, policy version and deterministic hashes. Service-evaluated decisions are recorded as `policy_decision` audit-chain events; direct engine calls stay pure and unaudited.

**ECS (Evidence Confidence Score).** A transparent 0–100 ranking score computed from verification, evidence quality, applicability and recency. It is not a probability and is not model-generated. Planned.

**Evidence.** The artifact or run that supports a claim. A fact without evidence is not a fact in EIS.

**FTS5.** SQLite's full-text search extension. It builds an inverted index and ranks matches. Planned for incidents, solutions and findings.

**Git.** The version-control system that holds source history. EIS reads project repositories without copying them into its own repository.

## I–M

**Inference.** Running a trained model to produce output. EIS performs inference through a provider; it does not train models.

**JSON (JavaScript Object Notation).** A structured text format. EIS policy files, API payloads and schemas use JSON.

**JSON Schema.** A standard for describing valid JSON. EIS uses it for policies and policy decisions.

**LLM (Large Language Model).** A model that predicts tokens. It can propose actions but has no authority to execute them.

**MCP (Model Context Protocol).** A protocol for exposing tools and context to models. EIS plans an adapter that wraps MCP tools under its own policy; MCP cannot grant capabilities.

**Model provider.** The component that serves a model. No provider is enabled by default. EIS supports OpenCode, OpenAI, Gemini, Anthropic and xAI through one typed gateway with vendor-specific adapters; Ollama is optional.

## O–R

**Ollama.** A local model runtime. EIS treats it as optional, not the default, and does not implement an Ollama client yet.

**Consent gate.** A fail-closed check that requires explicit, provider-scoped permission for a data class before an external model call. C3 is never allowed.

**Egress decision.** A content-free audit record showing whether an external call was allowed or denied, for which provider/model/data class and consent reference.

**Model-call audit.** A content-free record of provider, model, request hash, timestamp, latency, status, usage, finish reason, provider request ID and consent reference. It never stores prompts or response text.

**PAM.** A separate personal memory/retrieval project. EIS plans an optional HTTP adapter for semantic retrieval. PAM results are candidates, never facts.

**Policy engine.** The deterministic component that decides whether an action is allowed. The current implementation uses typed, schema-validated exact-match rules and produces a deterministic decision/audit record. `PolicyService` records the decision before returning it. A3 approvals and a narrow execution gateway exist; broader approvals, obligations and execution are planned.

**Principal.** The actor requesting an action: the user, an agent role, a skill or a job. The planned model evaluates a principal against a capability.

**Provenance.** The origin of a claim: project, commit, file, tool, execution, timestamp and verification. Provenance makes knowledge auditable.

**RRF (Reciprocal Rank Fusion).** A method for merging ranked lists. Each result contributes `1 / (k + rank)`, with `k = 60` in the EIS design. Planned for combining FTS5 and PAM results.

**Session/task runtime.** Durable `sessions` and `tasks` with explicit states; the current runtime performs one `task.run` policy decision and a bounded loop of at most three model completions and two read-only tool calls, driven by a strict JSON envelope, then stops with an untrusted human-review proposal or a content-free failure code. An interrupted `running` task is never auto-resumed.

## S–Z

**Skill.** A versioned procedure that teaches EIS how to perform a task. It is not a tool and has no independent authority. Planned package: `SKILL.md`, manifest, permissions, tests, evaluations and changelog.

**Specialist.** One of six planned agent roles: Debugging, Security, Testing, Architecture, Release, Knowledge. They share one runtime and one policy engine.

**T1/T2 isolation.** T1 is a constrained subprocess; T2 is a container. T2 is required for untrusted execution. On Windows, T1 is not a security boundary. T1 fixed profiles exist for trusted projects; T2 is planned.

**Token.** A piece of text processed by a model. Token limits drive context budgeting and cost.

**Tool call.** A structured request from the model to run a registered tool. EIS validates the arguments and routes the request through policy; the model cannot execute it directly.

**ULID.** A sortable identifier used for records. Planned for database rows, so creation order is visible.

**WAL (Write-Ahead Logging).** A SQLite mode that improves read/write concurrency. Planned database setting.

**YAML.** A human-readable data format used for planned skill manifests and permissions. EIS policy files are currently JSON.

## Where to go next

Return to the [learning README](README.md), or read the [architecture map](09-architecture-map.md) for how these terms fit together.
