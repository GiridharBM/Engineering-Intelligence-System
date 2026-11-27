# EIS Product Specification

Version: 1.0 (M0 baseline)
Status: Approved baseline for M0; changes require an ADR where they alter scope or contracts.
Related: [EIS_SYSTEM_ARCHITECTURE.md](EIS_SYSTEM_ARCHITECTURE.md), [EIS_IMPLEMENTATION_ROADMAP.md](EIS_IMPLEMENTATION_ROADMAP.md), [EIS_PRIVACY_AND_DATA_GOVERNANCE.md](EIS_PRIVACY_AND_DATA_GOVERNANCE.md)

---

## 1. Product Definition

The Engineering Intelligence System (EIS) is a local-first, privacy-preserving, open-source engineering agent. It helps a developer understand, maintain, secure, test and prepare multiple software projects for deployment by combining:

- a primary coordinating agent with six specialist roles;
- persistent, provenance-linked engineering knowledge;
- cross-project retrieval and adaptation of verified solutions;
- validated, versioned skills with bounded evolution;
- deterministic security and execution controls;
- evidence-backed verification and readiness reporting.

EIS is not a chatbot, a project tracker, a scanner wrapper or an autonomous computer-use agent. Its differentiator is **verified engineering experience reused across projects under explicit permissions**.

Guiding principle: *become more capable through verified experience, not more powerful through unrestricted permissions.*

Tagline (working): *Build once. Learn everywhere. Ship with confidence.*

## 2. Target Users

| Persona | Primary need | v1 support |
|---|---|---|
| Solo developer with many repositories (primary) | Stop re-solving the same class of problem; get evidence-backed fixes | Full |
| Developer preparing a release | Know what is verified, what is untested and what blocks release | Full |
| Security-conscious developer | Run scanners and tests without leaking source or secrets | Full |
| Open-source contributor | Add a skill, adapter or scanner without touching core | Full (from M4/M11) |
| Small team | Shared knowledge, roles, approvals | Out of scope for v1 (ADR-031) |
| Researcher | Reproducible evaluation of cross-project intelligence | Harness from M12 |

## 3. Jobs To Be Done

1. "When a failure appears in project B, show me whether I solved the same failure in project A, why the fix may or may not apply, and what evidence supports it."
2. "Tell me what is actually verified in this repository at this commit, not what a checklist claims."
3. "Assess this repository's security and architecture without uploading my code."
4. "Prepare this project for release and list the unresolved risks with evidence."
5. "Reuse a proven procedure across projects without giving an agent unlimited permissions."
6. "Improve a skill from verified outcomes, and roll it back automatically if it regresses."

## 4. Scope

### 4.1 In scope for v1.0

- Local project discovery under explicitly configured roots, with exclusions.
- Project profiling from evidence (languages, frameworks, manifests, entry points, tests, CI).
- Session-based agent interaction with plan/approve/execute/verify loop.
- Six specialist roles with shared policy and evidence.
- Engineering memory: incidents, root causes, solutions, findings, runs, skills.
- Cross-project retrieval with lexical search and optional PAM semantic retrieval.
- Adaptation analysis: applicability comparison and explicit limitations.
- Skill registry with built-in, private, community and experimental classes.
- Skill validation, versioning, evaluation, controlled evolution and rollback.
- Deterministic policy engine, approval workflow, sandboxed execution, audit chain.
- Scanner adapters (Gitleaks, Semgrep, OSV-Scanner, Trivy; ZAP optional).
- Test-runner adapters for Python, Node/TypeScript, Flutter and container builds; extensible.
- Run ledger, evidence store, security and readiness reports.
- Automation jobs (scheduled and event-driven) under the same policy gateway.
- Web dashboard, CLI, and an OpenCode/IDE integration target.
- Public repository, documentation, CI and privacy gates.

### 4.2 Out of scope

Replacing PAM or Obsidian; general computer-use automation; mandatory cloud; Kubernetes/microservices; graph database; automatic production deployment; automatic permission grants; publishing private knowledge; exhaustive language support at launch; multi-user RBAC; mobile clients; real-time collaboration.

### 4.3 Supported platforms

Windows 11 (primary development host), Linux (x86_64), macOS (Apple Silicon and Intel). Python 3.11-3.12. Docker or Podman optional; absence of a container runtime disables T2-only operations with an explicit message.

## 5. Functional Requirements

Each requirement is testable and mapped to a milestone (M). "MUST" is normative.

### 5.1 Project Registry (FR-PROJ)

| ID | Requirement | M |
|---|---|---|
| FR-PROJ-01 | The system MUST discover Git and non-Git project directories under configured roots only. | M2 |
| FR-PROJ-02 | Every root MUST support include/exclude globs; secrets, build outputs, virtual environments and dependency caches MUST be excluded by default. | M2 |
| FR-PROJ-03 | A project profile MUST be derived from evidence (manifest content, lockfiles, CI files, source extensions), not from folder names. | M2 |
| FR-PROJ-04 | Each profile field MUST carry provenance (file path + hash) and a confidence basis. | M2 |
| FR-PROJ-05 | The registry MUST record Git remote (sanitised), branch, commit, dirty state and last analysis time. | M2 |
| FR-PROJ-06 | Re-scan MUST be incremental and MUST mark removed evidence as stale rather than silently deleting history. | M2 |
| FR-PROJ-07 | Access outside a registered root MUST be denied by the policy engine, including symlink and junction escapes. | M2 |

### 5.2 Agent and Sessions (FR-AGENT)

| ID | Requirement | M |
|---|---|---|
| FR-AGENT-01 | A session MUST persist messages, plan, task state and evidence references; resuming after a crash MUST not duplicate completed side effects. | M1 |
| FR-AGENT-02 | The primary agent MUST produce a plan before consequential actions and MUST mark steps that need approval. | M1 |
| FR-AGENT-03 | Every tool call MUST pass through the execution gateway and record a policy decision. | M1 |
| FR-AGENT-04 | The agent MUST distinguish fact, hypothesis and recommendation in output, with evidence links for facts. | M1 |
| FR-AGENT-05 | The system MUST enforce per-task limits (steps, tokens, wall clock, tool calls) and stop with a recoverable state. | M1 |
| FR-AGENT-06 | Delegation to a specialist MUST create a child task with a typed input and output schema. | M5 |
| FR-AGENT-07 | A specialist MUST NOT be able to expand its capabilities or bypass the gateway. | M5 |
| FR-AGENT-08 | Cancellation MUST stop further tool calls within one scheduling boundary and preserve partial evidence. | M1 |

### 5.3 Knowledge and Memory (FR-KNOW)

| ID | Requirement | M |
|---|---|---|
| FR-KNOW-01 | Incidents MUST record project, commit, symptom, environment, root cause, solution, affected components, verification and provenance. | M3 |
| FR-KNOW-02 | Knowledge MUST carry a state: observed, hypothesised, verified or superseded. | M3 |
| FR-KNOW-03 | Confidence MUST be computed from evidence quality, verification status, applicability and recency, never from a model-generated percentage. | M3 |
| FR-KNOW-04 | Retrieval MUST combine lexical search (FTS5) with filters (stack, version, error signature) and optionally semantic results from PAM. | M3 |
| FR-KNOW-05 | Reuse MUST include an applicability comparison and MUST state differences and limitations. | M3 |
| FR-KNOW-06 | A fix verified in one project MUST NOT be marked verified in another without verification evidence there. | M3 |
| FR-KNOW-07 | Obsidian export MUST be one-way into a managed folder, redacted, and never authoritative. | M3 |
| FR-KNOW-08 | PAM MUST be optional; when unavailable, retrieval MUST degrade to lexical search without failing the task. | M3 |

### 5.4 Skills (FR-SKILL)

| ID | Requirement | M |
|---|---|---|
| FR-SKILL-01 | A skill package MUST contain `SKILL.md`, `manifest.yaml`, `permissions.yaml`, `tests/`, `evaluations/`, `changelog.md` and a version. | M4 |
| FR-SKILL-02 | The loader MUST validate schema, name/directory match, permissions, provenance and content hashes before activation. | M4 |
| FR-SKILL-03 | Community skills MUST be declarative-only by default; executable content MUST require explicit approval and T2 isolation. | M4 |
| FR-SKILL-04 | The registry MUST support install, update, disable, revoke and rollback, with an audit record for each transition. | M4 |
| FR-SKILL-05 | Skill execution MUST record version, project, task, outcome and verification link. | M4 |
| FR-SKILL-06 | A skill MUST NOT receive capabilities beyond its declared and approved permission set. | M4 |
| FR-SKILL-07 | Evolution candidates MUST be evaluated against held-out cases and MUST NOT expand permissions or add executable code without approval. | M8 |
| FR-SKILL-08 | Activation MUST be reversible by immutable version reference; rollback MUST be automatic on defined regression thresholds. | M8 |

### 5.5 Security and Execution (FR-SEC)

| ID | Requirement | M |
|---|---|---|
| FR-SEC-01 | Policy decisions MUST be deterministic and deny by default. | M1 |
| FR-SEC-02 | Every approval MUST bind the action, arguments, resources and diff hash; any change MUST invalidate it. | M1 |
| FR-SEC-03 | Filesystem access MUST resolve through project IDs and relative paths, with canonical containment checks. | M1 |
| FR-SEC-04 | Command execution MUST use profiles with allowlisted executables, no shell interpolation and resource limits. | M6 |
| FR-SEC-05 | Untrusted execution MUST use T2 isolation where available; otherwise it MUST be refused. | M6 |
| FR-SEC-06 | Network egress MUST be denied by default and allowed only for configured purposes. | M6 |
| FR-SEC-07 | Secrets MUST be detected and redacted before persistence, logging and model egress. | M1 |
| FR-SEC-08 | Repository content and tool output MUST be treated as untrusted data and delimited as such in prompts. | M1 |
| FR-SEC-09 | Audit events MUST be hash-chained and append-only through the application interface. | M1 |
| FR-SEC-10 | A secret or private path found in a tracked file MUST fail the privacy test suite. | M1 |

### 5.6 Verification and Evidence (FR-VERIFY)

| ID | Requirement | M |
|---|---|---|
| FR-VERIFY-01 | Verification runs MUST record project, commit, tool and version, command, environment, timestamps, exit status, summary, artifacts and integrity hash. | M7 |
| FR-VERIFY-02 | Missing scanner or test tooling MUST be reported as `unavailable`, never as a pass. | M7 |
| FR-VERIFY-03 | Scanner findings MUST normalize to a common schema with fingerprint, severity, component and evidence reference. | M7 |
| FR-VERIFY-04 | Reports MUST state coverage, limitations, untested areas and the assessed commit. | M7 |
| FR-VERIFY-05 | Readiness items MUST use the states not-assessed, in-progress, passed, failed and accepted-risk. | M7 |
| FR-VERIFY-06 | Accepted risk MUST record rationale, approver and expiry. | M7 |

### 5.7 Automation (FR-AUTO)

| ID | Requirement | M |
|---|---|---|
| FR-AUTO-01 | Scheduled and event-driven jobs MUST run through the same policy gateway as interactive actions. | M9 |
| FR-AUTO-02 | Jobs MUST be durable, retry with backoff, and enter a dead-letter state after the attempt limit. | M9 |
| FR-AUTO-03 | Consequential automated actions MUST request approval and pause, never auto-approve. | M9 |
| FR-AUTO-04 | Every job run MUST emit audit events and a run record. | M9 |

### 5.8 Interfaces (FR-UI)

| ID | Requirement | M |
|---|---|---|
| FR-UI-01 | The CLI MUST expose project, session, run, finding, skill, approval and report operations. | M10 |
| FR-UI-02 | The dashboard MUST show activity timeline, approvals, project health, findings and evidence. | M10 |
| FR-UI-03 | The API MUST be versioned, documented by generated OpenAPI, and authenticated. | M1/M10 |
| FR-UI-04 | The dashboard MUST meet WCAG 2.2 AA for keyboard navigation, focus, contrast and labels. | M10 |
| FR-UI-05 | An OpenCode/IDE integration MUST use the public API, not internal imports. | M10 |
| FR-UI-06 | A future MCP interface MUST be read-mostly and policy-wrapped. | Post-v1 |

### 5.9 Privacy (FR-PRIV)

| ID | Requirement | M |
|---|---|---|
| FR-PRIV-01 | Runtime data MUST live outside the repository in a platform-aware directory. | M1 |
| FR-PRIV-02 | No telemetry, analytics or crash upload MUST occur. | M1 |
| FR-PRIV-03 | External model use MUST require an explicit consent record naming the provider and data classes. | M1 |
| FR-PRIV-04 | Export MUST support preview, redaction and explicit approval. | M7 |
| FR-PRIV-05 | Public artifacts MUST contain no private paths, secrets, runtime databases or private incidents. | M0+ |
| FR-PRIV-06 | Privacy regression tests MUST run in CI and fail closed. | M1 |

## 6. Non-Functional Requirements

| ID | Category | Requirement | Verification |
|---|---|---|---|
| NFR-01 | Performance | Cold project scan of a 50k-file repository ≤ 60 s on a 4-core laptop, excluding dependency downloads | Benchmark fixture |
| NFR-02 | Performance | API p95 latency ≤ 200 ms for read endpoints at 1k projects, excluding agent/model time | Load test |
| NFR-03 | Reliability | No completed side effect may be re-executed after a crash; resumption is idempotent | Fault-injection test |
| NFR-04 | Reliability | SQLite backup and restore verified by an automated restore test | Integration test |
| NFR-05 | Security | 100% of tool calls have a policy decision; 0 bypass paths in the route inventory test | Static + integration |
| NFR-06 | Security | Policy decision p99 ≤ 5 ms | Microbenchmark |
| NFR-07 | Privacy | 0 secret-shaped strings in logs/artifacts in the redaction corpus | Privacy suite |
| NFR-08 | Privacy | 0 egress events without a consent record | Egress test |
| NFR-09 | Portability | Install and smoke test on Windows, Ubuntu LTS and macOS in CI | CI matrix |
| NFR-10 | Accessibility | WCAG 2.2 AA on primary flows | Automated + manual audit |
| NFR-11 | Maintainability | Module import contracts enforced; no cycles | import-linter |
| NFR-12 | Observability | Every task traceable end-to-end by `task_id` | Trace test |
| NFR-13 | Compatibility | Python 3.11-3.12; Node 22+ for frontend builds | CI matrix |
| NFR-14 | Resource use | Idle footprint ≤ 300 MB RSS for API; worker ≤ 500 MB excluding model runtime | Measurement |
| NFR-15 | Data integrity | Foreign keys enforced; migrations reversible or explicitly irreversible with backup requirement | Migration test |

## 7. Success Metrics

Product success, measured at v1.0 beta:

| Metric | Target | Method |
|---|---|---|
| Cross-project reuse acceptance | ≥ 40% of retrieved incidents judged applicable by the user | In-product rating + study |
| Retrieval quality | Recall@5 ≥ 0.70 on the synthetic benchmark | Benchmark harness |
| Verification integrity | 100% of reported passes linked to a run record | Audit query |
| Policy enforcement | 0 unauthorised writes in the red-team suite | Adversarial suite |
| Privacy | 0 private artifacts in the public repo across 3 release rehearsals | Release gate |
| Skill safety | 0 auto-activations with permission delta | Registry audit |
| Time to first value | A new user profiles a repository and gets a report in ≤ 10 minutes | Usability test |

Research success criteria are defined separately in [EIS_RESEARCH_PLAN.md](EIS_RESEARCH_PLAN.md) and are not claimed here.

## 8. Constraints and Assumptions

- Single local user; one machine; no server deployment in v1.
- The user supplies model provider access (explicit OpenCode/OpenAI/Gemini/Anthropic/xAI configuration or optional Ollama) and scanner binaries; EIS detects and reports absence.
- Private repositories are external inputs, never copied into EIS.
- PAM is optional and independently versioned.
- Internet access may be absent; core analysis must still work.
- Windows is a first-class target but T1 is not a security boundary there (ADR-009).

## 9. Acceptance Criteria for the Product Baseline

The product specification is considered implemented for v1.0 when:

1. All FR items marked M1-M10 have an automated test or a documented manual test with evidence.
2. All NFR items have a measurement recorded in CI or a release report.
3. Every report produced by EIS names the commit, the tools, the coverage and the limitations.
4. A red-team run demonstrates no unauthorised modification, no unconsented egress and no privilege expansion.
5. A new contributor can install, run the test suite and add a declarative skill using only public documentation.
