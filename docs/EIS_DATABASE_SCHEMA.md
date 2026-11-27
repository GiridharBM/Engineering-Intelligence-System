# EIS Database Schema

Version: 1.0 (M0 baseline)
Status: Baseline; migrations require an ADR when breaking
Related: [EIS_MEMORY_ARCHITECTURE.md](EIS_MEMORY_ARCHITECTURE.md), [EIS_AGENT_RUNTIME_DESIGN.md](EIS_AGENT_RUNTIME_DESIGN.md), [EIS_TECHNICAL_DESIGN.md](EIS_TECHNICAL_DESIGN.md)

---

## 1. Conventions

| Convention | Rule |
|---|---|
| Engine | SQLite â‰¥ 3.40, WAL mode, foreign keys ON, `busy_timeout=5000` |
| Tables | `STRICT` where column types allow; `snake_case`; singular entity name in plural form |
| IDs | ULID stored as `TEXT(26)`; sortable by creation time |
| Timestamps | `INTEGER` epoch milliseconds UTC; API renders ISO-8601 |
| Booleans | `INTEGER` with `CHECK (x IN (0,1))` |
| JSON | `TEXT` with `CHECK (json_valid(col))`; schema validated in code |
| Enums | `TEXT` with `CHECK (col IN (...))`; values are part of the contract |
| Soft delete | `deleted_at INTEGER NULL`; queries exclude by default |
| Migrations | Alembic, forward-only; destructive migrations require a verified backup |
| Write discipline | Short transactions; `BEGIN IMMEDIATE` for read-modify-write; one worker writer |

No table stores secrets (C3). No table stores absolute project paths except `project_roots`, which is local-only and excluded from exports.

## 2. Entity Map

```
project_roots 1--n projects 1--n project_snapshots
                         |            |
                         |            +--n project_dependencies
                         +--n incidents 1--n root_causes
                         |        |     +--n solutions 1--n verification_runs
                         |        +--n incident_evidence --n artifacts
                         +--n security_findings
                         +--n verification_runs
sessions 1--n messages
sessions 1--n tasks 1--n task_steps 1--n tool_calls
tasks 1--n model_calls
tasks 1--n delegations (as parent/child)
approvals 1--1 policy_decisions (decision may exist without approval)
skills 1--n skill_versions 1--n skill_evaluations
                          |--n skill_executions
                          |--n skill_proposals

edges: typed relations across all entities
outbox, audit_events, jobs, job_runs, exports, consents, settings, integrity_anchors
```

## 3. Core and Configuration

### `settings`

| Column | Type | Constraints |
|---|---|---|
| `key` | TEXT | PK, `^[a-z0-9_.]+$` |
| `value_json` | TEXT | NOT NULL, json_valid |
| `updated_at` | INTEGER | NOT NULL |

Only allowlisted keys are writable through the API; secrets are rejected by key prefix (`secret.`, `token.`, `key.`).

### `consents`

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `scope` | TEXT | NOT NULL, CHECK IN (`model_provider`,`git_remote`,`skill_registry`,`mcp_server`,`scanner_update`,`export`) |
| `subject` | TEXT | NOT NULL (provider name, remote name, server id) |
| `endpoint` | TEXT | NULL |
| `data_classes` | TEXT | NOT NULL, json_valid (array of C0..C2) |
| `purpose` | TEXT | NOT NULL |
| `retention_unknown` | INTEGER | NOT NULL DEFAULT 1, CHECK 0/1 |
| `granted_at` | INTEGER | NOT NULL |
| `expires_at` | INTEGER | NULL |
| `revoked_at` | INTEGER | NULL |
| `evidence_note` | TEXT | NULL |

Index: `(scope, subject, revoked_at)`.

### `project_roots`

Implemented in migration `0005_project_roots` as the M1 minimal durable root registry.

| Column | Type | Constraints |
|---|---|---|
| `project_id` | TEXT | PK, validated project identifier |
| `root_path` | TEXT | NOT NULL, normalized canonical absolute path, UNIQUE, local-only, never exported |
| `root_identity` | TEXT | NOT NULL, length 64, SHA-256 of platform `st_dev:st_ino` |
| `created_at` | INTEGER | NOT NULL |
| `updated_at` | INTEGER | NOT NULL |

The M2 project-registry design adds labels, exclusions, scan timestamps and nested projects. That expansion must use a new migration and preserve existing registrations; it must not assume this minimal shape. Registration and removal are explicit local-user actions; no migration backfills roots from session project IDs.

### `projects`

Planned for M2; not implemented. The table below is the target shape and will reference the extended root registry.

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `root_id` | TEXT | FK extended root registry (M2), ON DELETE RESTRICT |
| `relative_path` | TEXT | NOT NULL, POSIX separators |
| `slug` | TEXT | NOT NULL, UNIQUE |
| `display_name` | TEXT | NOT NULL |
| `kind` | TEXT | CHECK IN (`git`,`directory`) |
| `git_remote_display` | TEXT | NULL, credentials stripped |
| `default_branch` | TEXT | NULL |
| `first_seen_at` | INTEGER | NOT NULL |
| `last_seen_at` | INTEGER | NOT NULL |
| `deleted_at` | INTEGER | NULL |

Unique: `(root_id, relative_path)`.

### `project_snapshots`

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `project_id` | TEXT | FK projects(id) ON DELETE RESTRICT |
| `commit_sha` | TEXT | NULL |
| `branch` | TEXT | NULL |
| `dirty` | INTEGER | NOT NULL DEFAULT 0 |
| `profile_json` | TEXT | NOT NULL, json_valid |
| `profile_version` | INTEGER | NOT NULL |
| `evidence_json` | TEXT | NOT NULL, json_valid (path + hash + confidence) |
| `captured_at` | INTEGER | NOT NULL |
| `is_current` | INTEGER | NOT NULL DEFAULT 1 |

Partial unique index: one `is_current=1` per project.

### `project_dependencies`

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `snapshot_id` | TEXT | FK project_snapshots(id) ON DELETE RESTRICT |
| `ecosystem` | TEXT | NOT NULL (`pypi`,`npm`,`pub`,`maven`,`nuget`,`cargo`,`go`,`system`) |
| `name` | TEXT | NOT NULL |
| `version_spec` | TEXT | NULL |
| `resolved_version` | TEXT | NULL |
| `scope` | TEXT | CHECK IN (`runtime`,`dev`,`build`,`optional`) |
| `manifest_path` | TEXT | NOT NULL |
| `direct` | INTEGER | NOT NULL DEFAULT 1 |

Index: `(name, ecosystem)`, `(snapshot_id)`.

## 4. Sessions, Tasks, Execution

### `sessions`

Implemented in migration `0004_sessions_tasks`.

`sessions` stores `id`, `title`, `status` (`active|idle|closed`), optional validated `project_id`, `created_at`, `updated_at` and `closed_at`. Title is private local input; audit events carry `title_sha256`.

### `messages`

Specified, not implemented. The minimal runtime persists no conversation content.

`id`, `session_id` FK, `role` (`user|assistant|system|tool`), `content_redacted` TEXT, `content_hash`, `token_count`, `model_call_id` NULL, `supersedes_id` NULL, `created_at`.

Index: `(session_id, created_at)`.

### `tasks`

Implemented in migration `0004_sessions_tasks`.

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `session_id` | TEXT | NOT NULL, FK sessions(id) |
| `state` | TEXT | CHECK IN (`created`,`running`,`blocked`,`completed`,`failed`,`cancelled`) |
| `principal` | TEXT | NOT NULL, agent principal |
| `goal` | TEXT | NOT NULL, private local input |
| `goal_hash` | TEXT | NOT NULL, length 64 |
| `policy_decision_id` | TEXT | NULL, FK policy_decisions(decision_id) |
| `policy_version` | TEXT | NOT NULL |
| `runtime_version` | TEXT | NOT NULL |
| `stop_reason` | TEXT | NULL |
| `request_hash` | TEXT | NULL, length 64 |
| `response_hash` | TEXT | NULL, length 64 |
| `finish_reason` | TEXT | NULL |
| `usage_json` | TEXT | NULL, JSON object (token usage plus run `completions`/`tool_calls` counts) |
| `created_at`, `started_at`, `ended_at` | INTEGER | |

Index: `(session_id, created_at)`, `(state)`. A trigger enforces valid task transitions; a separate session trigger enforces valid session transitions. `goal` is local private data and never appears in audit payloads or CLI output; `goal_hash` does.

### `task_plans` and `task_plan_steps` (implemented)

Implemented in migrations `0006_task_plans` and `0007_plan_recovery` (ADR-043/045), separate from the broader planned `task_steps` table below.

`task_plans`: `id` PK, `task_id` FK (unique), `project_id` NULL, `state` (`created|running|blocked|completed|failed|cancelled`), `policy_version`, `policy_hash` (64 hex), `plan_hash` (64 hex), `stop_reason` NULL, `created_at`, `updated_at`. One plan per task, bound to the project resolved from the session and to the policy in force at creation.

`task_plan_steps`: `id` PK, `plan_id` FK, `step_index`, `step_ref` (short model step ID), `tool` (registered read-only tool or `human_review`), `arguments_json` nullable immutable local-only JSON object for new plans, `depends_on_json` (array of step refs), `status` (`pending|running|completed|failed|blocked|skipped`), `attempt_count` (0â€“2), `policy_decision_id` NULL, `output_sha256` NULL, `error_code` NULL, `created_at`, `updated_at`. Unique on `(plan_id, step_index)` and `(plan_id, step_ref)`.

Triggers reject invalid plan (`created -> running -> {blocked, completed, failed, cancelled}`) and step transitions. A running read-only step can return to pending only with the content-free `interrupted_retry` reason, and at most two attempts may start. Step arguments are stored in the private local database only to support explicit crash recovery; they are immutable, never included in audit payloads or default CLI output, and old migration-0006 rows have no recoverable arguments. Goals, plan text, file contents and tool results are not stored in plan tables or audit events.

### `task_steps`

Specified, not implemented in the minimal runtime.

`id`, `task_id` FK, `step_index` INTEGER, `action_type` (`plan|tool_call|delegate|ask_user|request_approval|final`), `status` (`pending|running|completed|failed|skipped`), `tool_name`, `tool_version`, `arguments_hash`, `policy_decision_id` NULL, `approval_id` NULL, `attempt` INTEGER DEFAULT 1, `started_at`, `ended_at`, `error_code`, `error_detail_redacted`.

Unique: `(task_id, step_index, attempt)`.

### `tool_calls`

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `task_id` | TEXT | FK tasks(id) |
| `step_id` | TEXT | FK task_steps(id) |
| `tool_name` | TEXT | NOT NULL |
| `tool_version` | TEXT | NOT NULL |
| `arguments_redacted_json` | TEXT | NOT NULL, json_valid |
| `arguments_hash` | TEXT | NOT NULL |
| `idempotency_key` | TEXT | NOT NULL, UNIQUE |
| `principal` | TEXT | NOT NULL (`user`,`agent:<role>`,`skill:<name>@<ver>`,`job:<id>`) |
| `on_behalf_of` | TEXT | NULL (agent that proposed a user-delegated action) |
| `policy_decision_id` | TEXT | NOT NULL, FK policy_decisions(id) |
| `approval_id` | TEXT | NULL, FK approvals(id) |
| `isolation_tier` | TEXT | CHECK IN (`T0`,`T1`,`T2`) |
| `status` | TEXT | CHECK IN (`requested`,`running`,`completed`,`failed`,`denied`,`timeout`,`truncated`) |
| `exit_code` | INTEGER | NULL |
| `output_artifact_id` | TEXT | NULL, FK artifacts(id) |
| `started_at`, `ended_at` | INTEGER | |
| `error_code` | TEXT | NULL |

**Invariant:** every `tool_calls` row has a `policy_decision_id`; a trigger rejects inserts with NULL.

### `model_calls`

M1 provider slice:

`id`, `task_id` NULL, `provider`, `model`, `purpose`, `request_hash`, `response_hash` NULL, `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_ms`, `egress_consent_id` NULL, `status` (`success|provider_error|consent_denied`), `created_at`, `finish_reason` NULL, `provider_request_id` NULL, `usage_json` NULL. No prompt or response content is stored.

Index: `(provider, created_at)`.

### `egress_decisions`

M1 provider slice:

`id`, `provider`, `model`, `data_class` (`C0..C3`), `decision` (`allow|deny`), `reason`, `consent_id` NULL, `occurred_at`. No request or response content is stored.

Invariant: an `allow` decision must reference a consent row.

Index: `(provider, occurred_at)`.

These provider audit tables are queryable projections; each write is paired
with an `audit_events` chain event in the same transaction.

### `artifacts`

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `kind` | TEXT | CHECK IN (`log`,`report`,`patch`,`scanner_output`,`test_output`,`note`,`snapshot`,`export`) |
| `sha256` | TEXT | NOT NULL, UNIQUE with size |
| `size_bytes` | INTEGER | NOT NULL |
| `media_type` | TEXT | NOT NULL |
| `storage_relpath` | TEXT | NOT NULL (relative to data root) |
| `classification` | TEXT | CHECK IN (`C0`,`C1`,`C2`) |
| `redacted` | INTEGER | NOT NULL DEFAULT 1 |
| `retention_class` | TEXT | CHECK IN (`ephemeral`,`operational`,`engineering`,`evidence`,`export`) |
| `created_at` | INTEGER | NOT NULL |
| `deleted_at` | INTEGER | NULL |

### `delegations`

`id`, `parent_task_id`, `child_task_id`, `role`, `objective`, `input_hash`, `output_hash`, `status`, `created_at`, `ended_at`. Unique child.

## 5. Policy and Approvals

### `policy_decisions` (append-only projection)

Implemented in migration `0003_approvals_policy_projection`. The shipped projection
stores `decision_id`, policy version/hash, inputs hash, principal, action, canonical
resource, effect, reason, rule ID, context source and creation timestamp. It is
append-only and backfilled from existing `policy_decision` chain events. The chain
event remains the integrity source. The broader target model below remains a design
sketch and is not the shipped schema.

```sql
CREATE TABLE policy_decisions (
  id              TEXT PRIMARY KEY,
  principal       TEXT NOT NULL,
  on_behalf_of    TEXT NULL,
  action          TEXT NOT NULL,
  resources_json  TEXT NOT NULL CHECK (json_valid(resources_json)),
  decision        TEXT NOT NULL CHECK (decision IN ('allow','deny','require_approval')),
  rule_id         TEXT NOT NULL,
  approval_class  TEXT NULL,
  policy_version  TEXT NOT NULL,
  inputs_hash     TEXT NOT NULL,
  obligations_json TEXT NOT NULL DEFAULT '{}',
  task_id         TEXT NULL,
  decided_at      INTEGER NOT NULL
) STRICT;
CREATE INDEX idx_policy_task ON policy_decisions(task_id, decided_at);
```

Immutable via triggers (`BEFORE UPDATE`/`DELETE` â†’ `RAISE(ABORT)`).

### `approvals`

Migration `0003_approvals_policy_projection` currently supports digest-bound A3
`project.write` approvals with local private diff/content, root and content hashes,
policy provenance, TTL, immutable bindings, rejection notes and one-time
consumption. Expired approvals are refused and hidden from the pending list;
rows are not transitioned to an `expired` state in this slice. The broader
approval model shown below is the future target, not the current table definition.
Migration `0008_task_approval_link` adds nullable `task_id` for legacy compatibility,
a unique partial index for one task-bound approval per task, and an immutable binding
trigger. Model-originated write proposals use this binding; grant/reject/execute remain
explicit user CLI actions. Transition triggers require linked approval state and
successful `execution_runs` evidence before task completion.
Migration `0009_task_cancellation_review` recreates transition triggers for `cancelled`
tasks/plans, `blocked -> running`, `human_review` `blocked -> completed`, `blocked -> skipped`,
and `NOT EXISTS` successful-evidence guards for `cancelled`.

```sql
CREATE TABLE approvals (
  id              TEXT PRIMARY KEY,
  policy_decision_id TEXT NOT NULL REFERENCES policy_decisions(id),
  action_type     TEXT NOT NULL,
  action_digest   TEXT NOT NULL,
  summary         TEXT NOT NULL,
  diff_artifact_id TEXT NULL REFERENCES artifacts(id),
  risk_labels_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(risk_labels_json)),
  approval_class  TEXT NOT NULL CHECK (approval_class IN ('A1','A2','A3','A4','A5')),
  status          TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','expired','consumed')),
  requested_at    INTEGER NOT NULL,
  expires_at      INTEGER NOT NULL,
  decided_at      INTEGER NULL,
  decided_by      TEXT NULL,
  decision_note   TEXT NULL
) STRICT;
CREATE UNIQUE INDEX idx_approval_digest ON approvals(action_digest, status) WHERE status IN ('pending','approved');
CREATE INDEX idx_approval_status ON approvals(status, expires_at);
```

`execution_runs` is also created in migration 0003. It stores append-only execution
status, action/resource, profile, timing, exit code, output hash, sanitized error code
and optional backup ID. Raw process output and proposed file content are excluded.

## 6. Knowledge

### `incidents`

```sql
CREATE TABLE incidents (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES projects(id),
  commit_sha        TEXT NULL,
  title             TEXT NOT NULL,
  symptom_redacted  TEXT NOT NULL,
  error_signature   TEXT NOT NULL,
  signature_hash    TEXT NOT NULL,
  signature_version INTEGER NOT NULL,
  environment_json  TEXT NOT NULL CHECK (json_valid(environment_json)),
  state             TEXT NOT NULL CHECK (state IN ('observed','hypothesised','verified','superseded')),
  confidence        INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
  confidence_json   TEXT NOT NULL CHECK (json_valid(confidence_json)),
  applicability_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(applicability_json)),
  limitations       TEXT NULL,
  provenance_json   TEXT NOT NULL CHECK (json_valid(provenance_json)),
  superseded_by_id  TEXT NULL REFERENCES incidents(id),
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  deleted_at        INTEGER NULL
) STRICT;
CREATE INDEX idx_incident_sig ON incidents(signature_hash);
CREATE INDEX idx_incident_project ON incidents(project_id, state);
CREATE INDEX idx_incident_confidence ON incidents(confidence DESC);
```

### `root_causes`

`id`, `incident_id` FK, `statement`, `state` (`hypothesised|verified`), `confidence`, `confidence_json`, `evidence_artifact_id` NULL, `created_at`, `verified_by_run_id` NULL FK verification_runs(id).

### `solutions`

`id`, `incident_id` FK, `root_cause_id` NULL FK, `description`, `patch_artifact_id` NULL, `applicability_json`, `state` (`hypothesised|verified|superseded`), `confidence`, `confidence_json`, `verified_by_run_id` NULL, `adapted_from_id` NULL FK solutions(id), `created_at`, `updated_at`, `deleted_at`.

**Invariant:** `state='verified'` requires a non-null `verified_by_run_id` whose project matches the incident project. Enforced by a trigger plus service check.

### `incident_evidence`

`incident_id`, `artifact_id`, `relation` (`symptom|log|scanner|test|patch|note`), `note`, `created_at`. PK `(incident_id, artifact_id, relation)`.

### `verification_runs`

| Column | Type | Constraints |
|---|---|---|
| `id` | TEXT | PK |
| `project_id` | TEXT | FK |
| `snapshot_id` | TEXT | NULL FK |
| `commit_sha` | TEXT | NULL |
| `kind` | TEXT | CHECK IN (`test`,`lint`,`typecheck`,`build`,`scan`,`container`,`custom`) |
| `adapter` | TEXT | NOT NULL |
| `adapter_version` | TEXT | NOT NULL |
| `command_display` | TEXT | NOT NULL (no secrets) |
| `environment_json` | TEXT | NOT NULL, json_valid |
| `status` | TEXT | CHECK IN (`passed`,`failed`,`error`,`unavailable`,`cancelled`,`timeout`) |
| `exit_code` | INTEGER | NULL |
| `summary_json` | TEXT | NOT NULL, json_valid |
| `log_artifact_id` | TEXT | NULL FK artifacts(id) |
| `integrity_hash` | TEXT | NOT NULL |
| `started_at`, `ended_at` | INTEGER | |
| `created_by` | TEXT | NOT NULL (`user`,`agent:<role>`,`job:<id>`) |

Index: `(project_id, started_at DESC)`, `(status)`.

### `security_findings`

`id`, `project_id` FK, `snapshot_id` NULL FK, `scanner` (`gitleaks|semgrep|osv|trivy|zap`), `scanner_version`, `rule_id`, `category` (CWE/OWASP/other), `severity` (`critical|high|medium|low|info`), `title`, `file_path` (project-relative), `line`, `component`, `component_version`, `fingerprint` UNIQUE with project, `status` (`open|fixed|accepted_risk|false_positive|superseded`), `remediation_state`, `accepted_risk_json` NULL, `first_seen_at`, `last_seen_at`, `artifact_id`.

### `edges`

```sql
CREATE TABLE edges (
  id              TEXT PRIMARY KEY,
  src_type        TEXT NOT NULL,
  src_id          TEXT NOT NULL,
  rel             TEXT NOT NULL,
  dst_type        TEXT NOT NULL,
  dst_id          TEXT NOT NULL,
  weight          REAL NOT NULL DEFAULT 1.0,
  provenance_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(provenance_json)),
  created_at      INTEGER NOT NULL
) STRICT;
CREATE UNIQUE INDEX idx_edge_identity ON edges(src_type, src_id, rel, dst_type, dst_id);
CREATE INDEX idx_edge_src ON edges(src_type, src_id, rel);
CREATE INDEX idx_edge_dst ON edges(dst_type, dst_id, rel);
```

Edges are rebuildable projections; entity tables remain authoritative.

## 7. Skills

### `skills`

`id`, `name` UNIQUE, `category`, `origin` (`builtin|community|private|experimental`), `trust_class` (`builtin|verified|private|community|experimental`), `status` (`candidate|validated|approved|disabled|deprecated|revoked|quarantined`), `current_version_id` NULL, `evolution_state` (`enabled|frozen`), `created_at`, `updated_at`, `revoked_reason`.

### `skill_versions`

`id`, `skill_id`, `version` SemVer, `manifest_json`, `permissions_json`, `content_hash` UNIQUE, `package_relpath`, `state` (`candidate|validated|approved|revoked|rolled_back`), `signature`, `provenance_json`, `created_at`, `activated_at`, `supersedes_id`.

Unique: `(skill_id, version)`.

### `skill_validations`

`id`, `skill_version_id`, `stage` (`V1..V9`), `status` (`pass|fail|skip`), `findings_json`, `tool_versions_json`, `artifact_id`, `created_at`.

### `skill_evaluations`

`id`, `skill_version_id`, `suite`, `case_count`, `passed`, `failed`, `metrics_json`, `intervals_json`, `artifact_id`, `created_at`.

### `skill_executions`

`id`, `skill_version_id`, `task_id`, `project_id`, `status` (`completed|failed|cancelled`), `verification_run_id` NULL, `duration_ms`, `tool_call_count`, `human_intervention` INTEGER, `outcome_json`, `created_at`.

Index: `(skill_version_id, created_at DESC)`.

### `skill_grants`

`id`, `skill_version_id`, `capability`, `scope_json`, `approved_by`, `approved_at`, `approval_id`, `revoked_at`. Unique active grant per capability/scope.

### `skill_proposals`

`id`, `skill_id`, `base_version_id`, `candidate_version_id` NULL, `change_class` (`E0..E5`), `trigger`, `evidence_json`, `diff_artifact_id`, `permission_diff_json`, `decision` (`pending|auto_activated|approved|rejected|rolled_back`), `decided_at`, `decision_reason`, `created_at`.

## 8. Operations

### `jobs`

`id`, `kind`, `payload_json`, `schedule` (cron or NULL), `status` (`pending|running|succeeded|failed|dead_letter|cancelled`), `attempts`, `max_attempts`, `run_after`, `lease_owner`, `lease_until`, `last_error`, `created_at`, `updated_at`.

Index: `(status, run_after)`.

### `job_runs`

`id`, `job_id`, `started_at`, `ended_at`, `status`, `result_json`, `artifact_id`.

### `outbox`

`id`, `event_type`, `event_version`, `payload_json`, `occurred_at`, `dispatched_at` NULL, `attempts`.

Index: `(dispatched_at, occurred_at)`.

### `audit_events`

Implemented in migration `0002_audit_chain`.

```sql
CREATE TABLE audit_events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  id           TEXT NOT NULL UNIQUE,
  occurred_at  INTEGER NOT NULL,
  actor        TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id   TEXT NOT NULL,
  decision     TEXT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)
                                    AND json_type(payload_json) = 'object'),
  prev_hash    TEXT NOT NULL,
  hash         TEXT NOT NULL
) STRICT;

CREATE TRIGGER audit_chain_insert BEFORE INSERT ON audit_events
WHEN NEW.seq <> (SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events)
  OR NEW.prev_hash <> COALESCE(
       (SELECT hash FROM audit_events WHERE seq = NEW.seq - 1),
       '0000000000000000000000000000000000000000000000000000000000000000')
BEGIN SELECT RAISE(ABORT, 'audit_events chain violation'); END;

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
```

Hash algorithm v1:

```text
canonical = UTF-8 JSON with sorted keys, separators "," and ":",
            non-ASCII preserved, over id, seq, occurred_at, actor,
            event_type, subject_type, subject_id, decision and payload
hash      = lowercase hex SHA-256(prev_hash ASCII bytes || canonical)
```

`prev_hash` is 64 zeros for `seq = 1`; every later event names the previous
`hash`. `seq` is append order, not necessarily timestamp order.

Chained event types: `session_created`, `session_state_changed`, `task_created`,
`task_state_changed`, `project_root_registered`, `project_root_removed`,
`policy_decision`, `consent_granted`, `consent_revoked`,
`egress_decision`, `model_call`, `approval_requested`, `approval_granted`,
`approval_rejected`, `approval_consumed`, `execution_started`,
`execution_completed`, `execution_failed`, `tool_invocation_denied`,
`tool_invocation_started`, `tool_invocation_completed` and
`tool_invocation_failed`, `plan_created`, `plan_state_changed`,
`plan_step_created` and `plan_step_state_changed`. There is no separate `tool_calls` projection in this
slice; the chain events are the durable tool invocation record. Root events
contain the project ID, canonical-path SHA-256 and identity hash only; the
absolute path stays in the local `project_roots` row. `egress_decisions` and
`model_calls` are queryable projections written in the same transaction as their
chain event, so a projection cannot be committed without its audit event. Policy
decisions are stored as a chain event plus a `policy_decisions` projection; the
event ID is the deterministic decision ID, so a retry returns the existing event
instead of appending a duplicate.

Provider payloads contain provider/model identifiers, data class,
decision/status, reason, request hash, latency, usage, finish reason, provider
request ID and consent reference only. Policy payloads contain decision ID,
effect, reason, rule ID, policy version/hash, inputs hash, principal, action,
canonical resource and context source. Approval payloads contain approval and
policy IDs, resource/root/diff hashes, request digest, risk class, expiry and a
rejection-reason hash; private diff/content and the plain rejection note stay in
the local approval row. Execution payloads contain run/approval IDs, action,
resource, profile, status, exit code, output hash, error code and backup ID.
Session/task payloads contain IDs, status/state, project ID, principal, policy
version, goal/title hashes, model request/response hashes, finish reason and
usage. Task goals and session titles stay in their private local rows; model
response text is not persisted. Context attributes/task IDs are omitted, and
absolute or drive-letter paths are rejected. No payload contains prompts,
response text, API keys, raw source or raw process output.

Tool invocation payloads contain invocation/task IDs, registered tool name,
project ID, policy decision ID, argument/resource/output hashes, status, output
byte count and a sanitized error code. They never include file content, raw Git
output, raw arguments or absolute paths. Read-only tool results are returned in
memory only and are not persisted as messages or artifacts.

Verification recomputes from `seq = 1`, checks sequence continuity, each
canonical hash and every previous-hash link, and reports the first failure.
`eis audit verify` prints only the result, count, sequence and reason.

Limitation: this is **tamper-evident, not tamper-proof**. An actor with direct
database access can drop the triggers and rewrite or truncate the chain.
Tail truncation is undetectable without the external `integrity_anchors`
checkpoint, which is not implemented.

### `integrity_anchors`

Specified, not implemented.

`id`, `up_to_seq`, `hash`, `created_at`, `external_ref` NULL.

### `exports`

`id`, `kind`, `filters_json`, `artifact_id`, `preview_hash`, `approved_by`, `created_at`.

## 9. Full-Text Search

```sql
CREATE VIRTUAL TABLE incidents_fts USING fts5(
  title, symptom_redacted, error_signature, limitations,
  content='incidents', content_rowid='rowid', tokenize='unicode61'
);
```

Sync triggers on insert/update/delete. Query uses `bm25(incidents_fts)` with filters joined to the base table. Equivalent tables: `solutions_fts`, `findings_fts`.

Redaction runs before insert, so the index never contains secrets.

## 10. Indexes and Query Budgets

| Query | Index | Budget |
|---|---|---|
| Incidents by signature | `idx_incident_sig` | < 5 ms |
| Project findings by severity | `(project_id, severity, status)` | < 10 ms |
| Recent runs | `(project_id, started_at DESC)` | < 10 ms |
| Pending approvals | `idx_approval_status` | < 5 ms |
| Due jobs | `(status, run_after)` | < 5 ms |
| Edge traversal (depth â‰¤ 3) | `idx_edge_src`/`idx_edge_dst` | < 50 ms |
| Audit chain verify | sequential scan | < 2 s per 1M rows |

Missing indexes are a performance bug, not a tuning preference.

## 11. Migrations

- One Alembic head; no branch merges in released versions.
- Every migration has an upgrade and a documented downgrade or an explicit `irreversible` flag with a backup requirement.
- CI runs migrations on an empty database and on a fixture at the previous release.
- A destructive migration requires a verified backup and a recorded approval (A5).
- Data backfills are batched and restartable.

## 12. Backup and Integrity

1. `PRAGMA wal_checkpoint(TRUNCATE)` or the SQLite backup API.
2. Copy the database and artifact manifest.
3. Record `schema_version`, `up_to_seq`, artifact count and total bytes.
4. Restore verifies foreign keys, FTS consistency, audit chain and artifact hashes.
5. A failed verification aborts startup with `integrity_alert`.

## 13. Retention Jobs

| Job | Query | Action |
|---|---|---|
| Purge ephemeral | `sessions.closed_at < now-7d` | Delete messages, keep audit |
| Purge operational | `model_calls.created_at < now-90d` | Delete rows, keep aggregates |
| Purge logs | `artifacts.retention_class='operational' AND created_at < now-365d` | Delete file + mark row |
| Purge orphans | artifacts with no reference and age > 90d | Delete after confirmation |
| Verify chain | daily | Compare anchor; alert on mismatch |

## 14. Acceptance Criteria

1. `PRAGMA foreign_key_check` returns no rows after the integration suite.
2. Every `tool_calls` row references a policy decision (trigger test).
3. A `verified` solution without a matching run is rejected (trigger test).
4. Updating or deleting an audit row fails; the chain verifies after 10k events.
5. FTS returns the expected incident for a normalized signature fixture.
6. Migration from the previous release fixture succeeds and preserves counts.
7. Restore from backup reproduces row counts, hashes and chain head.
8. No column in any table stores a secret value (schema review + fixture scan).
