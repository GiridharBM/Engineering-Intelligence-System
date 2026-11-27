# EIS Agent Runtime Design

Version: 1.5 (cancellation + human-review continuation)
Status: The baseline session/task runtime, registered read-only tools, durable bounded plans and explicit read-only recovery are implemented (ADR-039 through ADR-045). A plan may end with one task-bound write proposal; it blocks for human approval and completes only after recorded execution success (ADR-046). Atomic task cancellation and strict human-review continuation are implemented (ADR-047). Delegation, broader authenticated API resources, broader approvals and full plan/act behavior remain planned.
Related: [EIS_AGENT_ARCHITECTURE.md](EIS_AGENT_ARCHITECTURE.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_API_SPECIFICATION.md](EIS_API_SPECIFICATION.md), [EIS_DATABASE_SCHEMA.md](EIS_DATABASE_SCHEMA.md)

---

## 1. Runtime Composition

The runtime is a set of collaborating components behind explicit interfaces. It has no global mutable state; a `RuntimeContext` is injected into every task.

| Component | Responsibility | Persisted state |
|---|---|---|
| `SessionManager` | Create, resume, close sessions; conversation turns | `sessions`, `messages` |
| `Orchestrator` | Task lifecycle, plan validation, step scheduling, synthesis | `tasks`, `task_steps` |
| `ContextManager` | Budgeted context assembly and provenance envelopes | None (derived) |
| `ModelGateway` | Provider selection, egress checks, call records, retries | `model_calls` |
| `SkillManager` | Resolve active skill version, render instructions, record execution | `skills`, `skill_versions`, `skill_executions` |
| `ToolManager` | Registry, schema validation, capability mapping | Registry loaded from code/manifests |
| `SpecialistManager` | Spawn role-scoped child tasks, enforce ceilings | `tasks` (parent/child) |
| `PolicyEngine` | Deterministic decision on (principal, action, resource) | `policy_decisions` |
| `ApprovalGate` | Bind, persist, expire, consume approvals | `approvals` |
| `ExecutionGateway` | Isolated invocation, limits, output capture | `tool_calls` |
| `EvidenceManager` | Store artifacts, hashes, classifications, links | `artifacts` |
| `LimitEnforcer` | Steps, tokens, wall clock, tool calls, cost | In task record |
| `RecoveryManager` | Lease expiry, resume, orphan detection | `tasks`, `jobs` |
| `EventBus` | In-process pub/sub with outbox durability | `outbox`, `audit_events` |

## 2. Turn Loop

**Current implementation (ADR-039 through ADR-046).** `SessionTaskRuntime` applies `task.run` policy and supports bounded strict-envelope task and plan runs. Plans persist up to six dependency-gated steps and can explicitly resume interrupted read-only work after policy/session/root rechecks; one final `project.propose_write` step may request a task-bound A3 approval. The model cannot grant or execute it. Approval-wait state for task, plan and proposal step commits atomically with chained events. The existing CLI handles human review and execution; successful execution evidence completes the blocked plan/task, and rejection fails them. The simple task envelope also accepts a standalone `write_proposal` that enters the same task-bound approval path. Redaction before provider egress is pattern-based defense in depth. Human-review continuation and automatic provider/model resumption are not implemented.

The CLI entry point is `eis task invoke-tool <session_id> --call-json ...`.
The session's project ID is resolved through `project_roots` at invocation time;
a caller-supplied root path is not accepted. Roots are registered only by the
explicit local-user `eis project register|list|remove` commands, which reject
missing paths, files, reserved runtime locations, overlaps and link/junction
components. A missing, moved or replaced root fails closed. The CLI prints only
IDs, status, byte count and hashes; read results remain in memory. The action
schema rejects extra/duplicate fields, unknown tools, traversal, absolute paths
and known secret-file paths. File listing, text reads and Git status have fixed
limits; Git uses a fixed argument vector with no shell. POSIX access uses a
descriptor walk with `O_NOFOLLOW`; Windows checks links/reparse points and opened
handle identity, with a documented residual swap window.

The target loop below remains the design contract for the full plan/act loop. Current plans allow the four registered read-only tools, a `human_review` marker, or one `project.propose_write` final step. The final write step only creates a digest-bound approval request; it does not itself write. Read-only interruptions can be explicitly resumed once per in-flight step, while completed steps are skipped. Human-review continuation, delegation and broader action types remain future work.

A task runs as a bounded loop. Each iteration produces exactly one typed action.

```
run_task(task_id):
  task = load(task_id)
  while task.state not terminal:
     enforce_limits(task)
     ctx = context.assemble(task)
     response = model.complete(ctx, schemas=action_schemas)
     action = parse_and_validate(response)
     persist(model_call, action)
     dispatch(action):
        PLAN        -> validate plan -> state=planned / awaiting_approval
        TOOL_CALL   -> policy -> approval? -> gateway -> evidence -> state=running
        DELEGATE    -> create child task -> await -> merge results
        ASK_USER    -> state=blocked, persist question
        REQUEST_APPROVAL -> approval row -> state=awaiting_approval
        FINAL       -> verification selected? -> verifying -> completed
     checkpoint(task)
```

Invariants:

- Exactly one action per model turn; no parallel unvalidated actions.
- A plan is persisted before any step executes.
- A tool call is persisted before execution and updated after, so a crash is observable.
- The loop never re-enters with a broader capability set.

## 3. Typed Model Actions

The model must return JSON conforming to one of the registered action schemas. One repair attempt is allowed; a second failure ends the task with `model_protocol_error`.

```python
class PlanAction(BaseModel):
    kind: Literal["plan"]
    steps: list[PlanStep]          # 1..20
    approvals_required: list[str]  # step ids

class ToolCallAction(BaseModel):
    kind: Literal["tool_call"]
    tool: str
    tool_version: str
    arguments: dict[str, Any]
    purpose: str

class DelegateAction(BaseModel):
    kind: Literal["delegate"]
    role: Role
    objective: str
    inputs: dict[str, Any]
    budget: Budget

class FinalAction(BaseModel):
    kind: Literal["final"]
    summary: str
    claims: list[Claim]            # evidence required for kind=fact
    limitations: list[str]
```

`PlanStep` carries `id`, `description`, `expected_effect` (`none|read|write|execute|network`), `requires_approval`, `verification` and `depends_on`.

Model output is untrusted: it is validated, size-capped and never used to construct a shell command. Tool arguments are passed as structured data to the gateway.

## 4. Model Gateway

```python
class ModelProvider(Protocol):
    name: str
    async def capabilities(self) -> ModelCapabilities: ...
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

class CompletionRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSchema] = []
    response_schema: dict | None = None
    temperature: float = 0.1
    max_tokens: int
    timeout_seconds: float = 120
    metadata: dict[str, str] = {}

class CompletionResponse(BaseModel):
    text: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    finish_reason: Literal["stop", "length", "tool_call", "error"]
    provider_request_id: str | None
    payload_hash: str
```

Rules:

- Provider selection is deterministic from config (`model_profile`), with an explicit fallback only if configured.
- `ModelGateway` performs egress policy checks **before** dispatch and records `model_calls` after.
- Timeouts are enforced by the gateway, not trusted to the provider SDK.
- Retries: at most 2 on transport errors, never on a successful-but-invalid response without a repair prompt.
- Streaming is a transport detail for the UI; the runtime consumes the final validated object.
- Provider-specific features (vision, embeddings) are capability-flagged, never assumed.

Providers in v1: `opencode`, `openai`, `gemini`, `anthropic`, `xai` and `fake`; optional `ollama`. The fake provider is deterministic and used in tests and CI.

## 5. Session Lifecycle

**Current implementation:** sessions support `active`, `idle` and `closed` with persisted timestamps and optional project association. Creating a task is refused after close. No message history or conversation summary is persisted in this slice.

```
create -> active -> idle -> closed
             |        |
             +--------+-> archived (retention)
```

- One active task per session; additional requests queue or open a new task.
- Messages are append-only; edits create a new message with `supersedes_id`.
- Session close does not delete evidence; it stops new turns.
- Resume reconstructs state from SQLite, not from model memory.
- Conversation summary is generated deterministically from stored turns; it is never the only record.

## 6. Task and Step Persistence

Every task row stores: goal, plan JSON, state, role, parent, limits, usage, stop reason, timestamps, `policy_version`, `runtime_version`, `idempotency_key`.

**Current implementation:** tasks store goal, goal hash, state, agent principal, policy decision/version, runtime version, stop reason, request/response hashes, finish reason, usage and timestamps. `usage` also carries the run's `completions` and `tool_calls` counts (merged into `usage_json`, no migration). Plan JSON, parent/role, limits, leases and delegation are not implemented yet.

ADR-043/045 add `task_plans` and `task_plan_steps` in migrations `0006_task_plans` and `0007_plan_recovery`: one plan per task, bound to the project, policy version/hash, plan hash and creation time, with 1–6 ordered steps. Validated step arguments are stored immutably in the private local SQLite database for explicit recovery; they are not audited or shown by default. A read-only in-flight step can be retried once after a process interruption. Recovery is an explicit CLI action, rechecks policy and root registration, and never runs at startup. `plan_created`, `plan_state_changed`, `plan_step_created` and `plan_step_state_changed` are appended in the same transaction as the row change.

Every step stores: index, action type, tool name/version, arguments hash (not raw secrets), policy decision ID, approval ID, status, attempt, start/end, error code, artifact IDs.

Unique constraint: `(task_id, step_index, attempt)` and `(idempotency_key)` on `tool_calls` for non-idempotent operations.

## 7. Execution Path (single chokepoint)

```python
async def invoke(task, call):
    tool = registry.get(call.tool, call.tool_version)
    args = tool.input_schema.validate(call.arguments)
    resource = resource_resolver.resolve(task.principal, args)
    # Write-class actions execute under the approving user's delegated authority.
    principal = resolve_effective_principal(task, tool)   # agent, or user + on_behalf_of
    decision = policy.evaluate(principal, tool.action, resource, task.context)
    if decision == DENY: raise PolicyDenied(decision.rule_id)
    approval = None
    if decision == REQUIRE_APPROVAL:
        approval = approval_gate.require_or_consume(task, tool, args, resource)
        if approval is None: raise ApprovalRequired(...)
    record = tool_calls.begin(task, tool, args, decision, approval, principal)
    try:
        result = await gateway.execute(tool, args, resource, approval)
        artifact = evidence.store(result, classification=tool.output_class)
        tool_calls.complete(record, artifact)
    except Exception as exc:
        tool_calls.fail(record, exc)
        raise
```

`resolve_effective_principal` returns the agent principal for read/verify/propose actions and `user` with `on_behalf_of=<agent>` for approved write-class actions. The approval digest covers the effective principal, so it cannot be replayed by a different agent.

No module may call `subprocess`, open project files or make network requests outside this path. A static check (`ruff` custom rule or import-linter plus a grep gate) enforces the absence of direct `subprocess` use in `modules/` and `core/` except in `execution/backends/`.

**T2 backend (ADR-044).** `ExecutionGateway` routes T2 profiles and profiles on untrusted projects through `SandboxBackend` only after the policy and integrity gates. `LinuxContainerBackend` requires a probed engine, an immutable image digest and an application-supplied seccomp profile; it uses a fixed argv with no network, read-only rootfs, non-root user, dropped capabilities, no-new-privileges, CPU/memory/PID limits, a filtered disposable project copy and forced container cleanup. If any protection is unavailable the result is `isolation_unavailable`; on Windows T2 is not claimed. T1 on Windows remains explicitly not a security boundary.

## 8. Limits and Budgets

| Limit | Default | Scope | Behaviour on breach |
|---|---|---|---|
| Max steps | 40 primary / 25 specialist | Task | Stop with `limit_exceeded` |
| Max tool calls | 60 | Task | Stop |
| Max model tokens | 200k | Task | Stop before next call |
| Wall clock | 15 min interactive, 60 min job | Task | Cancel and checkpoint |
| Output bytes per tool | 2 MB | Tool call | Truncate with marker |
| Context tokens | Model-specific, 8k reserve for output | Turn | Drop low-priority content |
| Concurrent model calls | 2 | Runtime | Queue with deadline |
| Concurrent tool calls | 4 | Runtime | Queue with deadline |
| Delegation depth | 1 | Task tree | Refuse |

Limits are enforced by `LimitEnforcer` before dispatch, not by prompt instruction. Configuration may lower, never raise beyond a hard ceiling compiled into the release.

## 9. Recovery

- **Lease**: a running task holds a lease (default 90 s) renewed per step. Expired leases are reclaimable.
- **Checkpoint**: task state, step record and evidence reference commit in one transaction.
- **Resume**: starts from the first non-terminal step. Completed non-idempotent steps are not repeated; the task pauses for confirmation if replay would be unsafe.
- **Orphans**: artifacts without a completed `tool_call` are marked `orphaned` and excluded from reports.
- **Crash during approval**: approval remains valid only if the bound action hash is unchanged and the approval has not expired.
- **Database busy**: bounded exponential backoff (5 attempts, 50-800 ms), then `storage_busy`.
- **Integrity alert**: audit chain mismatch blocks new approvals and writes until an operator acknowledges and the incident is recorded.

**ADR-047 (cancellation + human review).** `cancel_task` requires `task.cancel`, is atomic across task/plan/steps plus chained events, never rewinds completed work, refuses `approval_required`, and refuses when successful `execution_runs` evidence exists. `pending -> skipped`, `running -> failed` `cancelled`, blocked `human_review -> skipped`; task/plan `cancelled` terminal. `continue_plan_review(task_id, step_ref)` validates strict `step_ref`, rechecks policy/session/project/policy hash/root, completes `human_review`, moves blocked->running, and runs only unexecuted ready steps; it grants no permission and never approves a write. `approval_required` remains digest-bound (ADR-046).

**ADR-049 (consumed-approval recovery).** A consumed approval with no successful `execution_runs` row is terminal `execution_unverified`; it is never retried, completed or cancelled. CLI/API retries reconcile successful executions idempotently; read-only in-flight steps may be explicitly retried once; all recovery rechecks policy/session/root/audit integrity.

## 10. Cancellation and Shutdown

Cancellation is cooperative: no new step starts; a running tool receives a termination signal in T1/T2; partial output is preserved as evidence. Shutdown drains in-flight steps for up to 30 s, checkpoints, releases leases and closes the database cleanly.

## 11. Events

Event names are `domain.entity.action`, versioned. Minimum set:

`session.created`, `session.closed`, `task.created`, `task.planned`, `plan.approved`, `step.started`, `tool.requested`, `policy.decided`, `approval.requested`, `approval.decided`, `tool.completed`, `tool.failed`, `delegation.created`, `verification.completed`, `incident.proposed`, `incident.verified`, `skill.activated`, `skill.rolled_back`, `audit.integrity_alert`.

The explicit read-only project-tool slice records `tool_invocation_denied`,
`tool_invocation_started`, `tool_invocation_completed` and
`tool_invocation_failed` in `audit_events`. Root registration and removal append
`project_root_registered` and `project_root_removed`. The plan slice appends
`plan_created`, `plan_state_changed`, `plan_step_created` and
`plan_step_state_changed`. Payloads contain hashes, IDs and sanitized
status/error metadata only; they do not contain arguments, absolute paths,
goals or results.

Consumers: SSE stream, audit writer, Knowledge agent trigger, automation triggers, metrics. Delivery is at-least-once via outbox (ADR-025); consumers must be idempotent by `event_id`.

## 12. Error Taxonomy

| Code | Retryable | User action |
|---|---|---|
| `policy_denied` | No | Request capability change or different scope |
| `approval_required` | After approval | Review action and diff |
| `approval_expired` | Yes (re-request) | Re-approve |
| `schema_invalid` | Repair once | Fix input or report bug |
| `model_unavailable` | Yes (backoff) | Configure an external provider or optional Ollama |
| `model_protocol_error` | Once with repair | Report bug |
| `tool_timeout` | Bounded retry | Increase limit or investigate |
| `tool_failed` | Depends on tool | Inspect evidence |
| `limit_exceeded` | No (new task) | Narrow scope |
| `storage_busy` | Yes | Retry |
| `integrity_alert` | No | Investigate audit chain |
| `cancelled` | No | n/a |

Errors are typed domain exceptions; adapters translate vendor errors. No raw stack traces reach the model or the UI; they reach the local log with a correlation ID.

## 13. Observability

Each task produces a trace: `task_id`, `session_id`, `step_index`, `tool`, `policy_rule`, `approval_id`, `model_call_id`, `artifact_ids`, durations and token usage. Logs are structured JSON, redacted at the sink. The activity timeline in the dashboard is a projection of these records, not a separate log.

## 14. Runtime Configuration

```toml
[runtime]
max_steps = 40
max_tool_calls = 60
task_wall_clock_seconds = 900
checkpoint_every_step = true

[models]
default_profile = "local"
allow_cloud = false

[concurrency]
model_calls = 2
tool_calls = 4
```

Unknown keys fail startup. Secrets are not permitted in this file (ADR-027).

## 15. Acceptance Criteria

1. A crash injected after every step leaves a resumable task; no completed tool call repeats.
2. A tool call with an unregistered tool or invalid schema never reaches the gateway.
3. Every `tool_calls` row has a matching `policy_decisions` row (database assertion).
4. Cancellation during a T1 command terminates the process tree within 2 s.
5. The fake provider can drive a full plan/tool/verify/completion cycle deterministically in CI.
6. Limit breaches stop before the next model or tool call, with the reason persisted.
