# EIS Agent Architecture

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_AGENT_RUNTIME_DESIGN.md](EIS_AGENT_RUNTIME_DESIGN.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_SKILL_SYSTEM.md](EIS_SKILL_SYSTEM.md)

---

## 1. Model

EIS uses a **hybrid multi-agent** design: one primary agent owns the user relationship and plan; six specialist roles perform bounded analysis. All roles run in the same runtime, share one policy engine, one execution gateway and one evidence ledger. A specialist is a **configured role**, not an autonomous authority (ADR-011).

```
User
  |
  v
Primary EIS Agent  <-->  Session / Context / Model gateway
  |                        |
  |-- plan, scope, approvals, synthesis
  |
  +--> Debugging Agent
  +--> Security Agent
  +--> Testing Agent
  +--> Architecture Agent
  +--> Release Agent
  +--> Knowledge Agent
          |
          v
   Policy Engine -> Execution Gateway -> Evidence Ledger
```

## 2. Agent Identity

Every agent instance has a stable identity record:

```yaml
agent_id: eis.primary            # or eis.debugging, eis.security, ...
role: primary
runtime_version: 1.0.0
policy_version: 2026-11-20.1
capability_ceiling: [project.read, knowledge.read, tool.invoke, approval.request]
model_profile: default
```

Identity is descriptive, not a credential. Authority comes only from the policy engine evaluating a capability against a scoped resource. An agent cannot mint a new identity or change its ceiling at runtime.

## 3. Role Registry

| Role | Purpose | Capability ceiling | Typical outputs | Max steps |
|---|---|---|---|---|
| `eis.primary` | Understand request, plan, delegate, synthesise, request approval | read, knowledge.read, project.propose, tool.invoke, approval.request, skill.invoke | answer, plan, approval request, report | 40 |
| `eis.debugging` | Failure investigation and root cause | project.read, project.propose, knowledge.read, tool.invoke(read/verify) | finding, hypothesis, proposed patch | 25 |
| `eis.security` | Scanner orchestration, triage, remediation proposal | project.read, project.propose, security.scan, knowledge.read | finding set, risk assessment, remediation | 25 |
| `eis.testing` | Test selection, execution, failure analysis | project.read, project.propose, project.verify, knowledge.read | run summary, regression finding | 20 |
| `eis.architecture` | Structure, dependencies, contracts, impact | project.read, project.propose, knowledge.read | component map, impact analysis, ADR draft | 20 |
| `eis.release` | Readiness assessment and release gates | project.read, project.verify, security.scan, report.write | readiness report, gate list | 25 |
| `eis.knowledge` | Capture, extract, link, propose skill changes | knowledge.read, knowledge.write(proposal), project.propose, skill.propose | incident record, pattern, skill proposal | 20 |

`modify` is never in an agent ceiling. Modification is a gateway action executed under the user's delegated authority after an approval bound to a diff; the agent may only **propose** it (`project.propose`) and request approval.

## 4. Responsibilities

### 4.1 Primary agent

1. Parse the request and identify projects, commit scope and constraints.
2. Retrieve project profiles and prior knowledge before planning.
3. Produce an explicit plan; mark steps requiring approval.
4. Select and invoke skills by name and version, not by free-form imitation.
5. Delegate to specialists with typed inputs and budgets.
6. Enforce global limits and stop conditions.
7. Synthesise results, separating fact, hypothesis and recommendation.
8. Present approvals with action, target, diff, risks and reversibility.
9. Record verified outcomes and trigger knowledge capture.

The primary agent MUST NOT claim a fact without an evidence reference. If evidence is missing, it says so.

### 4.2 Debugging agent

Inputs: symptom, error text (redacted), project scope, commit, environment. Process: search incidents by error signature and stack; compare versions/frameworks; inspect relevant files; form hypotheses; propose the smallest discriminating check; propose a fix and a regression test. Output: ranked hypotheses with discriminating evidence, root-cause statement (state `hypothesised` unless proven), proposed patch, verification plan.

### 4.3 Security agent

Inputs: project scope, scanner policy, prior findings. Process: run configured scanners, normalize, deduplicate by fingerprint, assess exploitability context, map to OWASP/CWE where possible, propose remediation and rescan. Output: findings with severity and evidence, coverage statement, remediation plan, residual risk. It must never state that a project is "secure".

### 4.4 Testing agent

Inputs: project profile, changed files, test configuration. Process: choose adapters, run approved commands, parse results, detect regressions against a baseline, capture logs. Output: run records, failure clusters, regression candidates, coverage gaps.

### 4.5 Architecture agent

Inputs: repository, dependency manifests, API definitions. Process: build a component/import graph, identify cycles and hotspots, map API contracts and data flows, evaluate change impact for a diff. Output: component map with evidence, dependency risks, impact report, ADR draft.

### 4.6 Release agent

Inputs: project, target environment, verification results. Process: evaluate build reproducibility, tests, security findings, configuration, backup/rollback, observability and documentation; mark each gate. Output: readiness report with pass/fail/not-assessed and the assessed commit. It never deploys.

### 4.7 Knowledge agent

Inputs: completed tasks, verification runs, user confirmation. Process: extract incident, root cause, solution, applicability and limitations; link evidence; detect duplicate incidents; propose a pattern or skill change. Output: incident record in `hypothesised` or `verified` state, links, skill proposal. It cannot mark its own output verified.

## 5. Delegation Protocol

```json
{
  "delegation_id": "dlg_01J...",
  "parent_task_id": "tsk_01J...",
  "role": "eis.debugging",
  "objective": "Determine why migration 0042 fails on SQLite 3.45",
  "project_scope": ["prj_01J..."],
  "inputs": {
    "symptom": "...",
    "error_signature": "sqlite.constraint.foreign_key",
    "commit": "abc1234",
    "prior_incident_ids": ["inc_01J..."]
  },
  "constraints": {
    "read_only": true,
    "max_steps": 15,
    "max_tokens": 60000,
    "deadline_seconds": 300,
    "allowed_skills": ["root-cause-analysis@2.1.0"]
  },
  "expected_output_schema": "delegation.debugging.v1"
}
```

Rules:

1. A child task inherits the intersection of the parent's capabilities and the role ceiling. It can never gain capability.
2. Budgets are hard limits enforced by the runtime, not requests to the model.
3. A specialist may return `blocked` with a required capability or approval; the primary decides whether to request it.
4. Specialists cannot delegate further in v1. Depth is capped at 1 to bound cost and complexity.
5. Every delegation is persisted with input hash, output hash and usage.

### 5.1 Standard output envelope

```json
{
  "status": "completed",
  "summary": "...",
  "claims": [
    {
      "kind": "fact",
      "statement": "The migration fails because ...",
      "evidence_refs": ["art_01J...", "run_01J..."],
      "confidence_basis": {"verification": "reproduced", "evidence_count": 2}
    }
  ],
  "hypotheses": [],
  "proposed_actions": [],
  "limitations": ["Only SQLite was tested"],
  "usage": {"steps": 9, "tokens": 21400, "tool_calls": 6}
}
```

`kind` is one of `fact`, `hypothesis`, `recommendation`. A `fact` without evidence is a schema violation and is rejected by the runtime.

## 6. Task State Machine

```
created -> planned -> awaiting_approval -> running -> verifying -> completed
   |          |              |               |          |
   |          |              |               |          +-> failed
   |          |              |               +------------> cancelled
   |          |              +----------------------------> rejected
   |          +-------------------------------------------> blocked
   +------------------------------------------------------> failed
```

| State | Meaning | Allowed transitions |
|---|---|---|
| `created` | Record exists, no plan | planned, cancelled |
| `planned` | Plan validated, no side effects | running, awaiting_approval, blocked |
| `awaiting_approval` | Paused on a bound approval | running, rejected, cancelled |
| `running` | Executing permitted steps | verifying, failed, cancelled, awaiting_approval |
| `verifying` | Running checks | completed, failed |
| `completed` | Result and evidence recorded | terminal |
| `failed` | Recoverable error recorded | resumed to running (new attempt) |
| `blocked` | Missing capability or input | planned (after change) |
| `rejected` | User declined | terminal |
| `cancelled` | User or limit stopped execution | terminal |

Transitions are validated in the domain layer; illegal transitions are programming errors, not user errors.

## 7. Context Assembly

Context is budgeted and ordered by authority and relevance:

| Priority | Content | Budget share (default) |
|---|---|---|
| 1 | System policy and role instructions | 8% |
| 2 | Task goal, constraints, plan state | 7% |
| 3 | Project profile and snapshot facts | 15% |
| 4 | Prior incidents and verified knowledge | 25% |
| 5 | Selected skill instructions (latest version only) | 15% |
| 6 | Conversation summary and recent turns | 15% |
| 7 | Retrieved file excerpts and tool output | 15% |

Rules:

- Raw file content is included only when the task needs it, with path, commit and line range.
- Every untrusted block is wrapped: `<untrusted source="repo" path="..." commit="...">...</untrusted>`.
- Tool output is truncated deterministically (head + tail + marker), never mid-token silently.
- Secrets are redacted before assembly (ADR-024).
- If the budget is exceeded, lower-priority content is dropped and the omission is stated in the task trace.

## 8. Tool and Skill Use

1. The model emits a tool intent against a registered tool name and schema.
2. The runtime validates the arguments against JSON Schema; unknown fields are rejected.
3. The policy engine returns ALLOW, DENY or REQUIRE_APPROVAL with a rule ID.
4. On approval, the gateway re-validates the exact arguments and diff hash.
5. Execution is bounded by timeout, output size and isolation tier.
6. The result is recorded as evidence and returned to the model as untrusted data.

Skills are invoked by `name@version`; the runtime loads only the active version from the registry. A skill cannot call tools directly; it declares intent through the same tool contract.

## 9. Conflict and Disagreement Handling

Specialists may disagree (for example, Security says a dependency is exploitable; Architecture says it is unreachable). Resolution rules, in order:

1. **Evidence quality**: a reproduction or scanner artifact outranks reasoning.
2. **Verification**: a verified run outranks an unverified claim.
3. **Specificity**: a claim scoped to the exact commit and configuration outranks a general one.
4. **Recency**: newer evidence at the same quality wins, unless it is superseded.

The primary agent must present the disagreement and the deciding evidence. It must not average confidence scores or hide dissent. If evidence is insufficient, the result is `hypothesis`, not a forced conclusion.

## 10. Stop Conditions

The runtime stops a task when any limit is reached, a denial is final, an approval is rejected or expired, the same tool/arguments repeat three times without new evidence, or the audit chain reports an integrity alert. Stop reason is recorded and shown to the user.

## 11. Prompt and Instruction Policy

- System instructions are versioned files in the repository, not database rows edited at runtime.
- User instructions may shape goals but cannot alter policy, capabilities or evidence rules.
- Repository text, issue bodies, commit messages and tool output are data. The prompt labels them and instructs the model to ignore embedded instructions.
- Detected injection attempts are recorded as a security event, not silently dropped.
- The model never sees raw secrets, bearer tokens or full environment dumps.

## 12. Acceptance Criteria

1. A role cannot exceed its capability ceiling in the policy test suite.
2. A `fact` claim without evidence is rejected by schema validation.
3. Delegation depth > 1 is refused.
4. Cancellation within a scheduling boundary leaves a resumable task.
5. An injected instruction in a repository file cannot cause a write, proven by an adversarial test.
6. Conflict handling produces a visible dissent record, not a single averaged answer.
