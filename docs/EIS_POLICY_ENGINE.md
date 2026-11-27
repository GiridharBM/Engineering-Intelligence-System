# EIS Policy Engine and Decision Audit Contract

Version: 1.2 (projection, approvals and bounded execution)
Status: Implemented: typed, schema-validated, deterministic deny-by-default evaluation, chained policy-decision persistence, a queryable projection, digest-bound A3 project-write approvals and bounded T1 execution for fixed profiles. Not implemented: a policy `require_approval` effect, obligations, broad resource resolution, broader approval actions and a real external audit-anchor deployment (an optional S3 adapter is mocked-tested only). Decisions evaluated directly through `PolicyEngine` remain pure and unaudited.
Related: [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md), [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md) (ADR-008, ADR-037)

---

## 1. Scope

This slice answers one question deterministically:

> Given a typed principal, action, scoped resource and context, does the loaded policy explicitly allow it?

The answer is `allow` or `deny`. There is no `require_approval` policy effect yet. Delegated `project.write` instead requires the allow decision plus a separate A3 approval bound to the exact diff, target, root, content hashes, policy decision and expiry. Broader approval classes and obligations are future work.

## 2. Typed inputs

| Type | Purpose | Validation |
|---|---|---|
| `Principal` | Who is asking | `user`, `system`, `agent:<role>`, `skill:<name>@<semver>`, `job:<id>`; no wildcards |
| `Action` | What is requested | lowercase dotted name such as `project.read`; no wildcards |
| `Resource` | Scoped target | canonical forms listed below; no wildcards, traversal or absolute paths |
| `PolicyContext` | Deterministic context | source enum, optional task ID, sorted string attributes |
| `PolicyRequest` | Complete input | rejects missing, unknown or malformed fields |

Canonical resource forms:

```text
global
project:<project-id>
path:<project-id>/<relative-path>
tool:<name>@<version>
host:<scheme://host:port>
skill:<name>@<major.minor.patch>
artifact:<id>
record:<id>
```

`path` resources reject empty, `.` and `..` segments, backslashes, absolute paths, drive-letter prefixes and wildcards. This is **syntax validation**, not filesystem containment. The execution gateway resolves approved writes against registered roots and rejects symlinks, junctions, reserved paths and escapes.

## 3. Decision algorithm

```text
1. Validate the policy against schemas/policy.schema.json.
2. Require policy.default == "deny".
3. Validate every rule (principal, action, resource, effect, unique ID).
4. Reject wildcard capability/resource grants.
5. Validate the typed request; reject malformed input.
6. Evaluate all matching explicit DENY rules; the first match denies.
7. Evaluate matching ALLOW rules; the first exact match allows.
8. Otherwise deny with reason "default_deny".
```

Properties:

- **Deny by default.** No implicit allow exists.
- **Explicit deny wins.** A deny rule is checked before any allow rule.
- **Exact match.** There is no wildcard expansion, no pattern matching and no model interpretation.
- **Fail closed.** A malformed policy is rejected; a malformed request raises `PolicyInputError`; neither can produce an allow decision.
- **Pure.** Evaluation reads only the in-memory policy and request. It makes no model, network, clock or filesystem call.
- **Versioned.** Every decision reports the policy version and policy document hash.

## 4. Determinism and identifiers

The engine canonicalises JSON with sorted keys and compact separators.

```text
policy_hash    = SHA-256(canonical policy document)
inputs_hash    = SHA-256(canonical request + policy version + policy hash)
decision_id    = "pol_" + first 24 hex characters of inputs_hash
```

The same request against the same policy produces the same decision ID and hashes. A changed context, policy version or policy content changes the hash. No timestamp is included in the decision, because it would make the decision non-deterministic; the chained audit event adds record time.

## 5. Decision output

`PolicyDecision` contains:

| Field | Meaning |
|---|---|
| `decision_id` | Deterministic identifier |
| `effect` | `allow` or `deny` |
| `reason` | `explicit_allow`, `explicit_deny` or `default_deny` |
| `rule_id` | Matched rule, or `default_deny` |
| `policy_version` | Version from the policy file |
| `policy_hash` | Hash of the validated policy document |
| `inputs_hash` | Hash of request + policy identity |
| `principal` | Typed principal |
| `action` | Typed action |
| `resource` | Typed scoped resource |
| `context` | Source, task ID and sorted attributes |

`as_dict()` returns the JSON-safe decision.

## 6. Decision audit contract

`PolicyAuditRecord` is the JSON-safe record that the chained sink persists:

```json
{
  "decision_id": "pol_...",
  "effect": "deny",
  "reason": "default_deny",
  "rule_id": "default_deny",
  "policy_version": "2026.11-m1a",
  "policy_hash": "<64 hex>",
  "inputs_hash": "<64 hex>",
  "principal": "agent:eis.primary",
  "action": "project.write",
  "resource": "project:unregistered",
  "context": {"source": "cli", "task_id": null, "attributes": {}}
}
```

`DecisionSink` is a protocol with one method:

```python
class DecisionSink(Protocol):
    def record(self, record: PolicyAuditRecord) -> None: ...
```

`record_decision(decision, sink)` hands the record to a sink. `PolicyService` is the service boundary: it evaluates with the pure engine, calls the sink and returns the decision only after the sink succeeds. `SqlitePolicyDecisionSink` appends a content-free `policy_decision` event to the audit chain. Its event ID is the deterministic `decision_id`, so retrying the same request and policy returns the existing event rather than appending a duplicate; a different event under the same ID fails closed.

The chained payload includes `decision_id`, effect, reason, rule ID, policy version/hash, inputs hash, principal, action, canonical resource and context source. Context attributes and task IDs are omitted, and absolute or drive-letter paths are rejected; `inputs_hash` still binds the full request. `eis policy-evaluate` uses this service. `eis policy-check` remains a read-only diagnostic and does not record an event.

## 7. Policy file example

```json
{
  "version": "2026.11-m1a",
  "default": "deny",
  "rules": [
    {
      "id": "allow-primary-read",
      "principal": "agent:eis.primary",
      "action": "project.read",
      "resource": "project:demo",
      "effect": "allow"
    }
  ]
}
```

The default bundled policy has no rules, so every request is denied.

## 8. Security limits of this slice

These limits are deliberate and must not be described as implemented:

1. **The engine does not execute.** A decision does not run anything; `ExecutionGateway` is the only supported write/profile boundary.
2. **No `require_approval` policy effect.** A1–A5 classes are not attached to decisions. Only A3 `project.write` has a separate digest-bound approval.
3. **No broad resource resolution.** The engine validates resource syntax; `ExecutionGateway` resolves approved write paths against one registered root.
4. **No obligations.** Limits, redaction, isolation tier and expiry are not attached to decisions yet.
5. **No model authority.** The engine imports no model provider and accepts no free-form "model says allow" input. A model cannot construct an allowed action without a typed request and an explicit matching policy rule.
6. **No database or agent loop.** The engine is in-memory and synchronous; persistence lives in `PolicyService` and the storage layer.

Direct engine calls are pure and unaudited; production paths must use `PolicyService`. A caller must treat `deny` and any `PolicyInputError` as a stop condition. A3 approval never turns a deny into an allow and cannot bypass policy or limits.

## 9. Specs and deferrals

- ADR-008 requires deterministic, deny-by-default evaluation; this slice implements it.
- The execution model's full algorithm includes `require_approval`, obligations, broad resource resolution and many approval classes. Those are deferred, not contradicted. Persisted decisions and A3 write approvals are implemented.
- The existing policy schema allowed only `allow` and `deny`; this slice preserves that and adds resource-scope validation.
- `Policy.evaluate` changed from string arguments to a typed `PolicyRequest`. This is an internal M0.2-to-M1 contract change; the CLI and tests were updated. No external API compatibility is claimed.
- The API `/api/v1/meta` response now reports `policy_version` and `policy_hash`; this is additive within v1.

## 10. Tests and commands

```powershell
uv run pytest tests/test_policy_baseline.py tests/test_policy_engine.py tests/test_policy_persistence.py tests/test_approval_execution.py -q
uv run eis policy-check
uv run eis policy-evaluate --principal user --action project.read --resource project:example
uv run eis audit verify
uv run mypy src
```

Covered: allow, explicit deny, default deny, deny precedence, unknown action/resource, malformed policy, malformed/model-style input, wildcard rejection, invalid path scope, policy-version/hash reporting, deterministic decision IDs, context sensitivity, the audit sink contract, chained decisions, fail-closed audit errors, corrupted-chain blocking, idempotent retries and sensitive-value exclusion.

## 11. Next slice

The next smallest M1 slice is **broader resource resolution and approval actions**, with T2 still deferred:

- resolve project IDs and relative paths beyond the single approved-write root;
- attach limits, isolation tier and redaction obligations to decisions;
- add `require_approval` outcomes for actions beyond A3 project writes.

The A3 gateway and approval lifecycle exist for `project.write` and fixed T1 profiles. The provider consent/egress/model-call audit chain and service-evaluated policy decisions are already persisted.
