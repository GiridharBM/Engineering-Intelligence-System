# EIS Memory Architecture

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_DATABASE_SCHEMA.md](EIS_DATABASE_SCHEMA.md), [EIS_PRIVACY_AND_DATA_GOVERNANCE.md](EIS_PRIVACY_AND_DATA_GOVERNANCE.md), [EIS_SYSTEM_ARCHITECTURE.md](EIS_SYSTEM_ARCHITECTURE.md), [EIS_RESEARCH_PLAN.md](EIS_RESEARCH_PLAN.md)

---

## 1. Memory Layers

| Layer | Purpose | Store | Lifetime | Authority |
|---|---|---|---|---|
| Session memory | Current request, plan, temporary tool results | SQLite (`sessions`, `messages`, `tasks`) | Session + 7 days (ephemeral class) | Working state only |
| Engineering memory | Projects, incidents, root causes, solutions, findings, patterns | SQLite + artifact store | Until user deletes | Authoritative for EIS knowledge |
| Operational memory | Executions, approvals, jobs, skill performance, audit | SQLite | 1 year default (audit retains chain) | Authoritative for what happened |
| Retrieval index | Lexical index; optional semantic index | SQLite FTS5; PAM externally | Rebuildable | Derived, never authoritative |
| Human documentation | Notes, maps, ADR summaries | Separate Obsidian vault (managed folder) | User-managed | Derived projection |

### 1.1 Source-of-truth boundaries

| Question | Authoritative source |
|---|---|
| What is the code at this commit? | The project's Git repository |
| What did EIS observe and conclude? | EIS SQLite |
| What evidence supports a claim? | EIS artifact store, referenced by hash |
| What is semantically similar? | PAM (optional capability), never a fact source |
| What should a human read? | Obsidian projection, marked generated |
| What did the agent execute? | Run ledger and audit chain |

Conflicting sources are never merged silently. A stale Obsidian note is reported as drift; it does not overwrite the database.

## 2. Memory Write Policy

Writes are explicit operations, not side effects of conversation:

| Write | Trigger | Required state |
|---|---|---|
| Session turn | User/agent message | Session active |
| Task and step | Runtime | Task exists |
| Incident | Knowledge agent proposal or user creation | `hypothesised` until verified |
| Root cause | Incident analysis | `hypothesised` or `verified` with evidence |
| Solution | Proposal + patch artifact | `hypothesised` until a verification run passes in that project |
| Finding | Scanner normalization | `observed` |
| Verification run | Adapter completion | Immutable once closed |
| Skill execution | Skill invocation | Links to task and optional run |
| Pattern | Knowledge agent after ≥ 2 verified incidents | `hypothesised` |
| Audit event | Every state change of consequence | Append-only |

Rules:

1. No record is created from model text alone without a provenance field naming the task and model call.
2. A `verified` state requires at least one `verification_run_id` whose project and commit match the claim scope.
3. A fix verified in project A is `verified` only for project A. Reuse in project B starts as `hypothesised`.
4. Deletion is soft first (`deleted_at`), then purged by retention policy; references become `tombstoned` rather than dangling.
5. Secrets are never written (ADR-024).

## 3. Incident Model

An incident is the atomic unit of reusable engineering experience.

| Field | Type | Notes |
|---|---|---|
| `id` | ULID | Stable, sortable |
| `project_id` | FK | Required |
| `commit_sha` | text | Required when available |
| `title` | text | Human summary |
| `symptom` | text | Redacted observable failure |
| `error_signature` | text | Normalized, indexable |
| `environment` | JSON | OS, runtime, versions, container |
| `root_cause_id` | FK | Nullable until analysed |
| `solution_ids` | relation | One or more candidate solutions |
| `affected_components` | JSON | Paths/modules, project-relative |
| `state` | enum | observed / hypothesised / verified / superseded |
| `confidence` | integer 0-100 | Computed, see §4 |
| `provenance` | JSON | Task, model call, tool, timestamps |
| `applicability` | JSON | Stack, version range, constraints |
| `limitations` | text | Explicit non-applicability |
| `created_at`, `updated_at` | timestamps | UTC |

### 3.1 Error signature normalization

To retrieve similar failures without embeddings, EIS normalizes:

- lowercase; collapse whitespace;
- replace UUIDs, hex hashes, timestamps, ports, absolute paths and memory addresses with typed placeholders;
- keep exception class, error code, failing symbol and framework markers;
- compute `signature_hash = SHA-256(normalized_text)`.

The raw error is stored as redacted evidence; the signature is stored as the retrieval key. Normalization rules are versioned so signatures can be recomputed.

## 4. Evidence Confidence Score (ECS)

ECS is a transparent 0-100 score, **not a probability** and **not model-generated**.

```
ECS = 100 * (0.45*V + 0.25*E + 0.20*A + 0.10*R)
```

| Component | Values |
|---|---|
| V — verification | verified with matching run 1.0; reproduced manually 0.9; observed only 0.5; hypothesised 0.2; superseded 0.0 |
| E — evidence quality | artifact + tool output 1.0; tool output 0.8; user statement 0.6; model reasoning 0.3; none 0.0 |
| A — applicability | exact stack and version 1.0; same stack, different version 0.7; analogous stack 0.4; unknown 0.2 |
| R — recency | `exp(-age_days / 365)` |

Rules:

- Scores are stored with their component breakdown so a report can explain them.
- A verified state with V < 1.0 is a data integrity error and is rejected.
- ECS is used for ranking and display, never as a substitute for evidence.
- Calibration is evaluated in M12 against human applicability judgements; until then, the weights are a documented heuristic.

## 5. Retrieval Pipeline

```
query
  -> normalize (terms, error signature, stack filters)
  -> candidate generation
       a) SQLite FTS5 (bm25) over incidents, solutions, findings
       b) structured filters (project, stack, version, state, date)
       c) optional PAM semantic search
  -> merge with Reciprocal Rank Fusion (k=60)
  -> deduplicate (signature_hash, solution hash, near-duplicate title)
  -> rerank: 0.6*text_score + 0.4*(ECS/100)
  -> applicability filter (hard constraints: language, framework major)
  -> top_k (default 10, max 50) with provenance
```

Rules:

- Lexical retrieval is always available and is the baseline.
- PAM results are candidates, not facts. A PAM-only hit is labeled `semantic_only`.
- Hard applicability filters never drop a result silently; excluded results are counted and reported.
- Retrieval logs record query hash, filters, index version and latency, but not raw private content in audit payloads.
- Empty results are a valid outcome; the agent must not invent a precedent.

## 6. Cross-Project Adaptation

When reusing knowledge from A in B, EIS produces an adaptation record:

| Check | Question |
|---|---|
| Language/runtime | Same language and major runtime? |
| Framework | Same framework and compatible major version? |
| Dependency graph | Are the implicated dependencies present with compatible versions? |
| Architecture | Is the failing component analogous (same role, not same name)? |
| Environment | OS, container, database, deployment differences? |
| Error signature | Same normalized signature, or merely similar text? |
| Fix preconditions | Does the fix assume files, configs or permissions that B lacks? |

Output is a `ReuseAssessment` with `applicable | partially_applicable | not_applicable | insufficient_evidence`, a difference list and required verification. "Partially applicable" requires an adapted proposal and new verification; it never inherits the source's verified state.

## 7. PAM Retrieval Adapter

PAM remains independent (ADR-012). EIS defines the contract; PAM may implement it in a separate, approved task.

```http
GET /v1/health
GET /v1/capabilities
POST /v1/search
{
  "query": "sqlite foreign key constraint failed on migration",
  "filters": {"project_ids": ["prj_..."], "kinds": ["incident", "solution"]},
  "top_k": 20,
  "index_version": "optional"
}
-> 200
{
  "index_version": "2026-11-01",
  "results": [
    {"document_id": "pam:...", "score": 0.83, "snippet": "...", "metadata": {"kind": "incident"}}
  ],
  "truncated": false
}
```

Adapter requirements:

- HTTP/JSON only; no Python imports, no shared database, no shared files.
- Timeout 3 s, one retry on connection error, circuit breaker after 5 consecutive failures (60 s open).
- Failure returns `unavailable`; the task continues with lexical retrieval.
- EIS stores only the mapping `pam_document_id <-> eis_entity_id`; it does not copy PAM's model or embeddings.
- PAM receives only the query and filters needed; no environment dumps, secrets or full files.
- Version negotiation is explicit; an unsupported major version disables the adapter.

A `NullRetrievalAdapter` is the default when PAM is not configured.

## 8. Obsidian Projection

The EIS vault is separate from any other vault.

| Rule | Detail |
|---|---|
| Write scope | Only `EIS/` inside the configured vault |
| Generated marker | YAML frontmatter `eis_generated: true`, `eis_id`, `eis_content_hash`, `eis_version` |
| Direction | One-way: SQLite → Markdown |
| Content | Project maps, incident summaries, ADR summaries, readiness reports; redacted, no secrets |
| Drift | If a generated file's hash differs from the recorded hash, EIS reports drift and refuses to overwrite without confirmation |
| Deletion | Removing an entity archives its note; it does not delete user notes |
| Authority | None. Edits in Obsidian do not mutate EIS records |

File naming is deterministic (`incident-<ulid>.md`) to avoid collisions and duplicate notes.

## 9. Deduplication and Supersession

- Incident duplicate candidates: same `signature_hash` plus same project and overlapping component set.
- Solution duplicate candidates: same patch hash or ≥ 0.9 textual similarity plus same root cause.
- Supersession is explicit: `superseded_by_id`, reason and approver (user or verified evidence).
- Superseded records remain retrievable but rank lower and are labelled.
- Deleting a record does not delete its evidence until references are resolved.

## 10. Graph Projection

The experience graph is a projection over entity tables plus an `edges` table (ADR-006). Canonical relationships:

`project HAS_INCIDENT incident`, `incident HAS_ROOT_CAUSE root_cause`, `incident RESOLVED_BY solution`, `solution VERIFIED_BY verification_run`, `project USES dependency`, `project HAS_FINDING finding`, `skill APPLIED_TO incident`, `skill_version PRODUCED skill_execution`, `incident SIMILAR_TO incident`, `solution ADAPTED_FROM solution`, `decision GOVERNS module`.

Edges carry provenance and are rebuilt idempotently from source records; an edge is never the only home of a fact.

## 11. Retention and Deletion

| Class | Default | Deletion |
|---|---|---|
| `ephemeral` | 7 days | Automatic purge |
| `operational` | 365 days | Purge after audit retention; chain preserved |
| `engineering` | Until user deletes | Soft delete → purge with reference tombstones |
| `evidence` | While referenced + 90 days | Purge only when no live reference |
| `export` | User-managed | Outside EIS control |

Deletion requests record an audit event with scope and counts. A "delete project" operation removes profiles, incidents and artifacts for that project but preserves anonymized aggregate metrics only if the user opts in; the default is full removal.

## 12. Backup and Recovery

- Database backup uses the SQLite backup API or a checkpointed file copy.
- Artifacts are backed up with their SHA-256 manifest.
- Restore verifies the audit chain, foreign keys and artifact hashes before serving traffic.
- A monthly restore drill is an M6 acceptance item; a failure blocks release.

## 13. Acceptance Criteria

1. A project scan produces profile evidence with file hashes; deleting the project removes it from retrieval.
2. A `verified` incident without a matching verification run is rejected by a database constraint or service invariant.
3. Retrieval returns ≤ 50 results with provenance and a stated exclusion count.
4. PAM down → lexical results within the same request, no error surfaced to the user beyond a notice.
5. Obsidian drift is detected and not overwritten.
6. ECS component breakdown is present on every scored record.
7. Deleting a project leaves no retrievable content from it.
