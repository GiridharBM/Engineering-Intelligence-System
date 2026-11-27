# EIS System Architecture

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_PRODUCT_SPEC.md](EIS_PRODUCT_SPEC.md), [EIS_AGENT_ARCHITECTURE.md](EIS_AGENT_ARCHITECTURE.md), [EIS_AGENT_RUNTIME_DESIGN.md](EIS_AGENT_RUNTIME_DESIGN.md), [EIS_DATABASE_SCHEMA.md](EIS_DATABASE_SCHEMA.md), [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md)

---

## 1. Architectural Style

EIS is a **modular monolith** deployed on one machine, with one installable Python distribution and up to two long-running processes (API and worker). It is organised as:

- a **domain core** with no I/O;
- **application modules** that own use cases and state transitions;
- **ports** for every external dependency;
- **adapters** that implement ports (models, scanners, test runners, PAM, Obsidian, Git, MCP);
- **entry points** (FastAPI, Typer, worker) that contain no business logic.

The architecture is deliberately boring: one database, one event bus, one policy chokepoint, explicit interfaces. Complexity is spent on trust boundaries, not distribution.

```
+----------------------------- Entry points ------------------------------+
|  Web dashboard (React)   CLI (Typer)   OpenCode/IDE   Future MCP        |
+----------------+----------------+----------------+----------------------+
                 |  HTTP/JSON      | in-process     | HTTP (adapter)
+----------------v----------------v----------------v----------------------+
|                    Application service layer                            |
|  SessionService  TaskService  ProjectService  KnowledgeService          |
|  SkillService    ApprovalService  ReportService  AutomationService      |
+---------------------------------+---------------------------------------+
                                  |
+---------------------------------v---------------------------------------+
|                          Agent runtime                                  |
|  Orchestrator | Session | Context | Model gateway | Tool registry       |
|  Specialist manager | Evidence manager | Limits/recovery                |
+---------------------------------+---------------------------------------+
                                  |
+---------------------------------v---------------------------------------+
|                     Policy and execution chokepoint                     |
|  PolicyEngine -> ApprovalGate -> ExecutionGateway -> Isolation tier     |
+---------------------------------+---------------------------------------+
                                  |
+---------------------------------v---------------------------------------+
|                       Shared intelligence modules                        |
|  ProjectRegistry | Analyzer | Knowledge/Incident | ExperienceGraph       |
|  SkillManager/Registry/Evolution | SecurityEngine | VerificationEngine  |
|  DependencyEngine | Automation | EvidenceLedger | ReportEngine           |
+---------------------------------+---------------------------------------+
                                  |
+---------------------------------v---------------------------------------+
|  Persistence (SQLite)  |  Artifact store (filesystem)  |  Outbox/events  |
+--------------------------------------------------------------------------------+
        Adapters: OpenCode/OpenAI/Gemini/Anthropic/xAI, optional Ollama | PAM | Obsidian | Git | scanners
                  test runners | sandbox backends | MCP servers
```

## 2. Layer Rules

| Layer | May depend on | Must not depend on |
|---|---|---|
| `domain` | stdlib, Pydantic | SQLAlchemy, FastAPI, adapters, modules |
| `core` (runtime, policy, execution, evidence) | domain, ports | concrete adapters, API, CLI |
| `modules` | domain, ports, core contracts | API, CLI, other modules' internals |
| `adapters` | domain, ports | modules, API, CLI |
| `api`, `cli`, `worker` | application services, modules | adapter internals, raw SQL |

Cross-module calls use published service interfaces or domain events. `import-linter` enforces these contracts in CI. A module may not import another module's `internal` package.

## 3. Runtime Processes

| Process | Command | Responsibility | Concurrency |
|---|---|---|---|
| API | `eis serve` | HTTP API, SSE, dashboard hosting, short interactive tasks | asyncio, bounded worker pool |
| Worker | `eis worker` | Durable jobs, scheduled scans, long verification, automations | one process, bounded task pool |
| CLI | `eis <command>` | In-process use of the same application services; no daemon required | single command |
| Frontend | static build | UI only; no privileged logic | n/a |

The CLI does not require the API to be running. Both CLI and API call the same service layer, so policy and evidence cannot diverge. Only one worker process is supported in v1 (ADR-026).

## 4. Component Responsibilities

| Component | Owns | Must not do |
|---|---|---|
| Project Registry | roots, exclusions, project identity, snapshots, profile evidence | read outside roots; trust folder names |
| Project Analyzer | language/framework/build detection, dependency extraction, entry points | execute project code |
| Knowledge Engine | incidents, root causes, solutions, states, confidence | mark unverified content verified |
| Experience Graph | typed edges and traversal | store authoritative facts outside entity tables |
| Skill Manager | lifecycle states, activation, rollback | bypass policy at execution |
| Skill Registry | package storage, provenance, versions, revocation | execute packages at install time |
| Skill Evolution | candidate generation, evaluation, activation gates | expand permissions |
| Security Engine | scanner orchestration, normalisation, dedup, coverage | equate "no findings" with "secure" |
| Verification Engine | adapter selection, run ledger, summaries | report a pass without a run record |
| Dependency Engine | manifests, lockfiles, vulnerability context, impact | auto-upgrade dependencies |
| Automation Engine | schedules, triggers, job definitions | auto-approve consequential actions |
| Evidence Ledger | artifact metadata, hashes, retention, linkage | store secrets |
| Report Engine | security/readiness/project reports | omit limitations or assessed commit |
| Policy Engine | capability evaluation, rule version | call a model to decide |
| Execution Gateway | tool invocation, isolation, limits, output capture | allow direct subprocess calls elsewhere |
| Model Gateway | provider abstraction, egress checks, call records | pass secrets or unredacted C3 data |
| Session Manager | conversation and task state, resumption | hold authority |
| Context Manager | budgeted assembly, provenance envelopes | silently truncate evidence |
| Orchestrator | planning, delegation, synthesis, limits | execute tools directly |

## 5. Primary Data Flows

### 5.1 Interactive engineering task

1. Client creates a session and posts a message.
2. Orchestrator classifies intent and scope; opens a task record.
3. Context Manager retrieves project profile, prior incidents and candidate skills (lexical + optional PAM).
4. Model Gateway produces a plan with typed steps; consequential steps are marked.
5. For each step: tool proposal → schema validation → policy decision → approval if required → execution gateway → evidence.
6. Specialists receive child tasks with bounded inputs; results return as typed findings.
7. Verification Engine runs approved checks; results attach to the task.
8. Knowledge Engine proposes an incident record; it stays `hypothesised` until verification evidence exists.
9. Report Engine renders the answer with facts, hypotheses, recommendations and evidence links.

### 5.2 Project scan

Roots → candidate directories → exclusion filter → manifest/profile extraction → snapshot record → optional baseline security scan → registry update → event `project.profiled`.

### 5.3 Skill installation

Package received → quarantine → schema validation → static scan → permission diff → provenance check → declarative activation or (executable) sandboxed evaluation + approval → registry activation event.

### 5.4 Skill evolution

Outcome metrics → proposal trigger → candidate generation → static checks → held-out evaluation → regression comparison → permission-diff check → auto-activate or approval request → version activation → rollback watchdog.

### 5.5 Automation

Schedule/event → job row → worker claim → policy evaluation → permitted steps execute → approval-required steps pause → resumption on approval → run record and audit.

## 6. Trust Boundaries

| Boundary | Inside | Outside | Control |
|---|---|---|---|
| B1 Host | EIS processes, runtime directory | Network peers | loopback bind, bearer token |
| B2 Repository | registered project roots | everything else | canonical path containment |
| B3 Model | EIS core | model provider and model output | egress policy, untrusted output handling |
| B4 Content | EIS instructions | repository text, issues, docs, tool output | provenance envelopes, no instruction authority |
| B5 Skill | validated skill | community/untrusted package | validation pipeline, T2 isolation |
| B6 Tool | trusted adapter | external binary/MCP server | allowlist, schema, timeout, sandbox |
| B7 Evidence | append-only ledger | mutable workspace | hashes, retention, audit chain |

Repository content never crosses from "data" to "instruction". Tool descriptions from MCP or skills are data; the policy engine is the only authority.

## 7. Deployment Topology

**Default (developer laptop).** `eis serve` on `127.0.0.1:8765`, SQLite and artifacts under the platform runtime directory. No model provider is enabled by default. Explicitly configured external providers use outbound HTTPS; optional Ollama uses loopback `127.0.0.1:11434`. No inbound network exposure.

**Isolated (optional).** Docker/Podman container for EIS with a bind-mounted project root (read-only where possible) and no network except an explicit model endpoint. Used for untrusted skill execution or CI.

**CI.** Headless `eis` commands against synthetic fixtures; no model calls; deterministic providers only.

Explicitly excluded: Kubernetes, managed databases, message brokers, serverless, multi-tenant hosting.

## 8. Module Interface Conventions

- Interfaces are Python protocols/ABCs in `ports/`, with DTOs in `domain/contracts/`.
- Every port method documents failure modes and idempotency.
- Adapters return domain errors, never raise vendor exceptions across the boundary.
- All external calls carry a deadline and a bounded payload.
- Events are immutable dataclasses with `event_type`, `version`, `occurred_at`, `payload`.

## 9. Failure Modes and Behaviour

| Failure | Behaviour |
|---|---|
| Model provider unavailable | Degrade to deterministic tools; task explains the limitation |
| PAM unavailable | Lexical retrieval only; no task failure |
| Scanner binary missing | `unavailable` status in report; never a pass |
| Container runtime missing | T2 operations refused with actionable message |
| SQLite busy | bounded retry with jitter, then explicit `storage_busy` error |
| Worker crash | lease expiry returns job to queue; idempotency keys prevent duplicate effects |
| Audit chain mismatch | system enters `integrity_alert`; approvals and writes blocked pending review |
| Policy file invalid | fail closed: all non-read actions denied |
| Disk full | execution aborts before write; partial artifacts marked orphaned |

## 10. Scalability and Limits (v1)

Designed envelope: up to 200 projects, 100k incidents/findings, 1M audit events, 10 GB artifacts. Beyond these, the documented path is archival, then an ADR for a different store. No horizontal scaling claim is made.

## 11. Extensibility Points

| Extension | Contract | Trust requirement |
|---|---|---|
| Model provider | `ModelProvider` | configured + consented |
| Scanner | `ScannerAdapter` | allowlisted binary + version check |
| Test runner | `TestRunnerAdapter` | command profile + T1/T2 |
| Knowledge retriever | `RetrievalAdapter` (PAM) | read-only, versioned |
| Documentation projector | `DocumentationAdapter` (Obsidian) | managed folder, redaction |
| Skill | package format | validation + trust class |
| MCP server | `McpServerAdapter` | explicit allowlist + policy |
| Report section | `ReportSection` | read-only over evidence |

## 12. Architecture Acceptance Criteria

1. An import-contract test proves the layer rules in §2.
2. A route-inventory test proves every mutating API path reaches the policy engine.
3. A chaos test proves job recovery after worker termination mid-run.
4. A restore test proves database and artifact recovery from backup.
5. The trust-boundary table is reflected one-to-one in the threat model.
