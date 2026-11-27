# EIS API Specification

Version: 1.0 (M0 baseline)
Status: Baseline; `/api/v1` is frozen for additive changes only (ADR-028)
Related: [EIS_TECHNICAL_DESIGN.md](EIS_TECHNICAL_DESIGN.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_AGENT_RUNTIME_DESIGN.md](EIS_AGENT_RUNTIME_DESIGN.md)

---

## 1. Conventions

| Aspect | Rule |
|---|---|
| Base path | `/api/v1` |
| Transport | HTTP/1.1 over loopback; JSON UTF-8; SSE for streams |
| Auth | `Authorization: Bearer <local-token>` on every endpoint except `GET /health` |
| IDs | ULID strings |
| Timestamps | ISO-8601 UTC with `Z` |
| Content type | `application/json` unless downloading an artifact |
| Versioning | Additive within v1; breaking changes require `/api/v2` |
| Unknown fields | Rejected on requests, ignored on responses |
| OpenAPI | Generated from code; committed artifact checked in CI |

### 1.1 Authentication bootstrap

The SPA is served from the same origin. On first load it calls `GET /api/v1/session/bootstrap` with the `Host` header validated and the long-lived bearer token; the server returns a short-lived (15 min) access token that the SPA holds **in memory** and sends as `Authorization: Bearer`. No cookie is used for API authentication, so there is no CSRF surface. CLI clients use the long-lived token directly. No token is ever placed in a URL, `localStorage` or a log.

### 1.2 Error envelope

```json
{
  "error": {
    "code": "policy_denied",
    "message": "project.write is not permitted for agent:eis.debugging",
    "details": {"rule_id": "role-ceiling", "capability": "project.write"},
    "correlation_id": "err_01J...",
    "retryable": false
  }
}
```

Codes are the runtime taxonomy: `policy_denied`, `approval_required`, `approval_invalid`, `schema_invalid`, `not_found`, `conflict`, `storage_busy`, `model_unavailable`, `model_protocol_error`, `tool_timeout`, `tool_failed`, `limit_exceeded`, `isolation_unavailable`, `integrity_alert`, `unavailable`, `cancelled`.

Status mapping: 400 schema, 401 auth, 403 policy, 404 missing, 409 conflict/state, 412 approval/ETag mismatch, 422 semantic validation, 429 rate limit, 503 dependency unavailable, 500 internal.

### 1.3 Pagination

`?limit=50&cursor=<opaque>`; default 50, max 200. Response: `{"items":[...], "next_cursor": null|"..."}`. Cursors are opaque, signed and expire in 24 h.

### 1.4 Idempotency and concurrency

- `Idempotency-Key` is required on POSTs that create side effects; replay returns the original response for 24 h.
- Mutable resources expose `ETag`; `If-Match` is required for `PATCH`.
- State transitions return `409` with the current state when invalid.

### 1.5 Long-running operations

Actions that can exceed 2 s return `202 Accepted` with `{"task_id": "...", "events_url": "/api/v1/tasks/{id}/events"}`. Clients subscribe to SSE or poll the task.

### 1.6 SSE

`GET /api/v1/events/stream?since=<seq>` streams `text/event-stream`:

```
id: 1042
event: tool.completed
data: {"task_id":"tsk_...","tool":"pytest.default","status":"completed"}
```

Heartbeat every 15 s. Reconnect with `Last-Event-ID`. Authorisation is re-checked on connect; a revoked token closes the stream.

## 2. Endpoints

### 2.1 System

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/health` | Liveness, version only (no auth, no internals) | — |
| GET | `/api/v1/meta` | Implemented metadata: mode, provider, policy version and hash | — |
| GET | `/health/ready` | DB, artifact store, provider readiness | `diagnostics.run` |
| GET | `/metrics` | Local counters and histograms (no project names or paths) | `diagnostics.run` |
| GET | `/session/bootstrap` | Exchange the local token for a short-lived same-origin session | — (token required) |
| GET | `/version` | Build, schema, policy versions | `diagnostics.run` |
| GET | `/capabilities` | Tools, providers, scanners, isolation tiers available | `diagnostics.run` |
| GET | `/doctor` | Resolved paths, permissions, missing tools | `diagnostics.run` |

> **Implementation status (2026-11-27):** `/health`, `/api/v1/meta` and the static `/api/v1/openapi.json` contract are public. `/api/v1/sessions` (create/list/show/close), `/api/v1/sessions/{id}/tasks` (bounded plan submission), `/api/v1/tasks/{id}`, `/api/v1/approvals` (pending list/detail), `/api/v1/approvals/{id}/decision`, `/api/v1/approvals/{id}/execute` and `/api/v1/audit/verify` require a local bearer token. Approval detail includes the private diff and must only be requested by the local user. Decisions require an exact 64-character digest from approval detail; execution resolves the project only from the durable root registry and returns status/evidence IDs, not output. Set `EIS_POLICY_FILE` to an explicit local policy file for approval operations; absent it, the default deny policy applies. `eis serve` binds only to loopback; `eis api-token` creates or rotates the token in the OS config directory. The token is never returned by HTTP or logged. Host headers are restricted to loopback names. Responses carry a validated/generated `X-Correlation-ID`; request logs contain method, path, status and correlation ID only. Audit event browsing, bootstrap, SSE, cancellation, settings updates and other listed endpoints remain planned.

`/api/v1/meta` reports `model_provider`, `model_configured` and the configured model ID. It never reports an API key or provider secret. See [EIS_MODEL_PROVIDER_CONFIGURATION.md](EIS_MODEL_PROVIDER_CONFIGURATION.md).

### 2.2 Settings and consent

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/settings` | Redacted configuration | `diagnostics.run` |
| PATCH | `/settings` | Update allowlisted keys | `policy.write` (user) |
| GET | `/providers` | Configured providers and consent state | `diagnostics.run` |
| POST | `/providers/{name}/consent` | Grant scoped consent | `policy.write` (user) |
| DELETE | `/providers/{name}/consent` | Revoke consent | `policy.write` (user) |
| GET | `/egress/events` | Egress ledger | `audit.read` |

`POST /providers/{name}/consent` body: `{data_classes, purpose, expires_at}`. C2 requires `{confirm_source_leaves_machine: true}`. Response includes the consent ID.

### 2.3 Project roots and projects

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/project-roots` | List roots | `project.list` |
| POST | `/project-roots` | Add a root | `policy.write` (user, A5) |
| PATCH | `/project-roots/{id}` | Enable/disable, exclusions | `policy.write` (user) |
| DELETE | `/project-roots/{id}` | Remove root and its projects (A4) | `project.delete` |
| GET | `/projects` | List projects | `project.list` |
| GET | `/projects/{id}` | Profile and last snapshot | `project.read` |
| POST | `/projects/{id}/scan` | Trigger scan (202) | `project.profile` |
| GET | `/projects/{id}/snapshots` | Snapshot history | `project.read` |
| GET | `/projects/{id}/findings` | Findings for the project | `security.scan` |

`POST /project-roots` validates that the path exists, is not a parent of the EIS repository, and is not the EIS repository itself. The response warns when the root is a home directory or contains more than the configured project limit.

### 2.4 Sessions and tasks

| Method | Path | Purpose | Capability |
|---|---|---|---|
| POST | `/sessions` | Create session | `project.read` |
| GET | `/sessions` | List sessions | `knowledge.read` |
| GET | `/sessions/{id}` | Session with recent messages | `knowledge.read` |
| POST | `/sessions/{id}/messages` | Send a message; creates a task (202) | `tool.invoke` |
| POST | `/sessions/{id}/close` | Close session | `knowledge.read` |
| GET | `/tasks/{id}` | Task state, plan, usage | `knowledge.read` |
| GET | `/tasks/{id}/steps` | Step records with policy decisions | `knowledge.read` |
| POST | `/tasks/{id}/cancel` | `POST /api/v1/tasks/{id}/cancel` implemented: atomic cancellation; `task.cancel` policy + bearer; never cancels successful-execution tasks | `task.cancel` |
| POST | `/api/v1/tasks/{id}/review/continue` | Continue a blocked `human_review` step | `task.run` (rechecked; grants no approval) |
| GET | `/tasks/{id}/events` | SSE stream | `knowledge.read` |

Message body:

```json
{
  "content": "Why does the migration fail on SQLite?",
  "project_ids": ["prj_01J..."],
  "constraints": {"read_only": true, "max_steps": 20}
}
```

Constraints may only tighten runtime limits. A request for a higher limit is rejected with `422`.

### 2.5 Approvals

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/approvals?status=pending` | Pending and recent approvals | `approval.request` |
| GET | `/approvals/{id}` | Full approval with diff and risk labels | `approval.request` |
| POST | `/approvals/{id}/decision` | Approve or reject | `approval.grant` (user) |
| POST | `/api/v1/approvals/{id}/execute` | Execute an approved write and reconcile its linked task | `project.write` (user) |

Decision body: `{"decision":"approve","digest":"<request_digest>","note":"..."}`. Approval requires the exact `request_digest` echoed from the GET; a mismatch is rejected. Approvals expire; expired rows are read-only. Execute body is `{"digest":"<request_digest>"}` and still rechecks policy, project-root identity, target content hash and single-use approval state. The API never accepts a project path or command from the caller. Rejection closes the linked task/plan/write step atomically; approval requires audit-chain integrity.

**Task cancellation and review continuation.** `POST /api/v1/tasks/{id}/cancel` requires `task.cancel` through `PolicyService`, bearer auth, audit integrity and atomic task/plan/step updates; terminal tasks and `approval_required` are not cancellable, and successful `execution_runs` evidence prevents cancellation. `POST /api/v1/tasks/{id}/review/continue` accepts only `{"step_ref":"..."}` (unknown fields rejected), rechecks `task.run`, policy version/hash, session, project identity and registered root, completes a `human_review` step, and resumes unexecuted read-only/write-proposal steps. It does not grant `approval.grant`, does not approve a write, does not accept a root/executable/model output, and does not substitute for `POST /api/v1/approvals/{id}/decision`. See ADR-047.

**Retry and recovery.** `POST /api/v1/approvals/{id}/execute` is digest-bound. A retry with the exact digest on an already-consumed approval does **not** repeat the write; it requires durable successful `execution_runs` evidence and reconciles `complete_task_after_approval`, returning the same `run_id`. An already-completed task returns success again; a consumed approval without successful execution evidence, a rejected/expired approval, a changed target, policy denial or audit failure is `409`, and task/plan state is never completed without a successful execution row. Route-level tests in `tests/test_api_approvals.py` cover authentication, bad digest, schema rejection, default-deny policy, rejection, successful task/plan completion and retry/recovery.

### 2.6 Verification and findings

| Method | Path | Purpose | Capability |
|---|---|---|---|
| POST | `/verifications` | Run a verification profile (202) | `project.verify` |
| GET | `/verifications` | List runs | `project.read` |
| GET | `/verifications/{id}` | Run detail and summary | `project.read` |
| GET | `/verifications/{id}/log` | Download log artifact | `artifact.read` |
| GET | `/findings` | Query findings | `security.scan` |
| GET | `/findings/{id}` | Finding detail | `security.scan` |
| POST | `/findings/{id}/risk-acceptance` | Record accepted risk (A4) | `security.scan` + approval |

Verification body: `{project_id, profile, commit_sha?}`. Unknown profile → `422`. Missing tool → the run is created with `status=unavailable`, not an error response.

### 2.7 Knowledge

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/incidents` | Filter by project, state, signature | `knowledge.read` |
| POST | `/incidents` | Create incident (hypothesised) | `knowledge.write` |
| GET | `/incidents/{id}` | Incident with evidence links | `knowledge.read` |
| PATCH | `/incidents/{id}` | Edit redacted fields | `knowledge.write` |
| POST | `/incidents/{id}/verify` | Attach a verification run and set verified | `knowledge.write` (A3) |
| GET | `/incidents/{id}/reuse-assessment` | Assess reuse in a target project | `knowledge.read` |
| GET | `/knowledge/search` | FTS + optional PAM retrieval | `knowledge.read` |

Search parameters: `q`, `project_id`, `state`, `language`, `framework`, `top_k` (max 50). Response includes `exclusions` count and `pam_status` (`used|unavailable|disabled`).

### 2.8 Skills

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/skills` | List with state and trust class | `skill.invoke` |
| POST | `/skills/install` | Upload or reference a package (202) | `skill.install` |
| GET | `/skills/{name}` | Skill detail | `skill.invoke` |
| GET | `/skills/{name}/versions` | Versions and states | `skill.invoke` |
| POST | `/skills/{name}/validate` | Re-run validation | `skill.install` |
| POST | `/skills/{name}/activate` | Activate a validated version | `skill.install` (A5 if executable) |
| POST | `/skills/{name}/rollback` | Roll back to previous approved | `skill.revoke` |
| POST | `/skills/{name}/revoke` | Revoke a version | `skill.revoke` (A3) |
| GET | `/skills/{name}/executions` | Metrics source records | `skill.invoke` |

Install accepts `multipart/form-data` (≤ 25 MB) or `{"source":"registry","url":"..."}` which requires consent. The package is quarantined; no code runs.

### 2.9 Evidence, reports, exports

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/artifacts/{id}` | Metadata and classification | `artifact.read` |
| GET | `/artifacts/{id}/content` | Download; classification-checked | `artifact.read` |
| POST | `/reports` | Generate security/readiness report (202) | `report.generate` |
| GET | `/reports/{id}` | Report metadata | `report.generate` |
| POST | `/exports/preview` | Compute scope, counts, redactions | `artifact.export` |
| POST | `/exports` | Create export artifact (A4) | `artifact.export` |
| GET | `/exports/{id}` | Export manifest | `artifact.export` |

Artifact responses set `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` and a CSP that denies script execution. HTML artifacts are never rendered inline.

### 2.10 Automation

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/jobs` | List jobs | `automation.manage` |
| POST | `/jobs` | Create a job (A5) | `automation.manage` |
| PATCH | `/jobs/{id}` | Enable/disable, schedule | `automation.manage` |
| POST | `/jobs/{id}/run` | Run now | `automation.manage` |
| GET | `/jobs/{id}/runs` | Run history | `automation.manage` |

### 2.11 Audit

| Method | Path | Purpose | Capability |
|---|---|---|---|
| GET | `/audit/events` | Filter by type, actor, subject, time | `audit.read` |
| GET | `/audit/verify` | Recompute chain to an anchor | `audit.read` |

Audit payloads are redacted; the chain fields are always present.

## 3. Cross-Cutting Behaviour

| Concern | Rule |
|---|---|
| Rate limit | 120 req/min per token; 10 concurrent agent tasks; `429` with `Retry-After` |
| Body limit | 1 MB JSON; 25 MB multipart skill upload |
| Timeouts | Request 30 s; streaming idle 60 s; tool per profile |
| CORS | Disabled by default; same-origin SPA only |
| Compression | gzip for JSON > 1 KB; not for SSE |
| Headers | `X-Content-Type-Options`, `Referrer-Policy`, `Content-Security-Policy`, `Cache-Control: no-store` for secrets-adjacent responses |
| Logging | Method, path, status, duration, correlation ID; never bodies or tokens |
| Tracing | `X-Correlation-ID` accepted (validated) or generated; returned |
| Deprecation | `Deprecation` and `Sunset` headers plus changelog entry |

## 4. CLI Mapping

| CLI | API |
|---|---|
| `eis project add <path>` | `POST /project-roots` |
| `eis project list` | `GET /projects` |
| `eis project scan <id>` | `POST /projects/{id}/scan` |
| `eis ask "..."` | `POST /sessions` + `POST /sessions/{id}/messages` |
| `eis task show <id>` | `GET /tasks/{id}` |
| `eis approvals` | `GET /approvals?status=pending` |
| `eis approve <id>` | `POST /approvals/{id}/decision` |
| `eis verify <project> --profile pytest` | `POST /verifications` |
| `eis findings <project>` | `GET /findings` |
| `eis knowledge search "..."` | `GET /knowledge/search` |
| `eis skill install <path>` | `POST /skills/install` |
| `eis skill rollback <name>` | `POST /skills/{name}/rollback` |
| `eis report readiness <project>` | `POST /reports` |
| `eis export --preview` | `POST /exports/preview` |
| `eis audit verify` | `GET /audit/verify` |

The CLI calls the application services in-process; the mapping above documents semantic equivalence, not a required network hop.

## 5. Compatibility and Testing

- A generated OpenAPI document is committed and diffed in CI; an unintended change fails the build.
- Contract tests use schemathesis or an equivalent property-based checker against the OpenAPI document.
- Every endpoint has tests for: success, auth failure, policy denial, schema error, not found, and rate limit where applicable.
- A route inventory test asserts every mutating endpoint maps to a capability and reaches the policy engine.
- SSE is tested for reconnect with `Last-Event-ID` and for token revocation.

## 6. Acceptance Criteria

1. OpenAPI generation is deterministic and committed.
2. No endpoint outside `/health` is reachable without a bearer token.
3. Every mutating endpoint has a documented capability and an approval class where required.
4. Error responses never include stack traces, SQL or file system paths.
5. Artifact downloads enforce classification and never render active content.
6. SSE reconnect from a given `seq` yields no duplicates and no gaps.
7. A replayed `Idempotency-Key` returns the original result without a second side effect.
