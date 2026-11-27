# EIS Privacy and Data Governance

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md), [EIS_THREAT_MODEL.md](EIS_THREAT_MODEL.md), [PUBLIC_RELEASE_CHECKLIST.md](../PUBLIC_RELEASE_CHECKLIST.md), [EIS_MEMORY_ARCHITECTURE.md](EIS_MEMORY_ARCHITECTURE.md)

---

## 1. Position

EIS is local-first and privacy-preserving by architecture, not by policy statement. It does not collect telemetry, does not require an account, and does not transmit project content by default. The public repository contains generic code and synthetic examples only.

This document defines classification, storage, egress, retention, user rights and the controls that keep private engineering data out of public artifacts. It does not claim legal compliance with any regime; it provides the technical basis for a compliance review.

## 2. Data Classification

| Class | Definition | Examples | Storage | Model egress |
|---|---|---|---|---|
| **C0 Public** | Safe to publish | Built-in skills, schemas, synthetic fixtures | Repository, DB | Allowed |
| **C1 Internal** | Non-sensitive operational data | Audit events, job status, tool versions | DB, logs | Allowed with consent |
| **C2 Confidential** | Private project or engineering data | Source excerpts, incidents, findings, reports | DB, artifacts, local only | Denied by default; explicit opt-in per provider |
| **C3 Secret** | Credentials and secret material | API keys, tokens, private keys, DB URLs with passwords | Never persisted in clear; env/keyring only | **Never** |

Rules:

- Classification is assigned at the boundary (ingest, tool output, user input) and stored on the artifact/record.
- The highest classification in a payload governs the whole payload.
- C3 is blocked structurally: it is not placed in a prompt and then filtered; it is excluded from context assembly.
- Reclassification upward is automatic; downward requires explicit user action and is audited.
- Public examples must be C0 and synthetic; a real repository may only be used with a permissive licence and recorded approval.

## 3. Data Map

| Data | Class | Store | Retention | Exportable |
|---|---|---|---|---|
| Project roots and paths | C2 | SQLite, local config | Until removed | No (local only) |
| Project profiles and snapshots | C2 | SQLite | Until project deleted | Redacted report only |
| Source excerpts in evidence | C2 | Artifacts | While referenced | With approval + redaction |
| Incidents, root causes, solutions | C2 | SQLite | Until deleted | With approval |
| Security findings | C2 | SQLite | Until project deleted | Redacted report |
| Verification logs | C2 | Artifacts | 90 days after last reference | With approval |
| Audit events | C1 | SQLite | 365 days (chain retained) | Summaries |
| Model call metadata | C1 | SQLite | 90 days | Aggregate |
| API token | C3 | Runtime file, user-only | Until rotated | Never |
| Provider credentials | C3 | Env/keyring | User-managed | Never |
| Session messages | C2 | SQLite | 7 days after close (default) | With approval |
| Obsidian notes | C2 | Separate vault | User-managed | User-managed |
| Telemetry | — | None | — | — |

## 4. Runtime Storage Locations

EIS never writes runtime data inside the repository. Locations follow platform conventions and are configurable.

| OS | Data root | Config root |
|---|---|---|
| Windows | `%LOCALAPPDATA%\EIS` | `%APPDATA%\EIS` |
| Linux | `$XDG_DATA_HOME/eis` (default `~/.local/share/eis`) | `$XDG_CONFIG_HOME/eis` |
| macOS | `~/Library/Application Support/EIS` | `~/Library/Application Support/EIS` |

```
<data root>/
  db/eis.sqlite3
  artifacts/<yy>/<mm>/<sha256-prefix>/...
  logs/eis.log (rotated, redacted)
  backups/
  cache/
  keys/api-token            (user-only permissions)
  quarantine/skills/        (untrusted packages, no execution)
```

Verification: `eis doctor` prints resolved paths and permission checks; `eis config show --redacted` shows configuration without secrets.

## 5. Repository Separation and Path Hygiene

| Rule | Enforcement |
|---|---|
| EIS is its own repository | CI check: repo root must equal the EIS directory; no `.gitmodules` |
| Parent directory is never a repo for EIS | Onboarding refuses to initialise if the target is a parent of other projects |
| No private project paths in tracked files | Privacy test scans for drive-letter paths, home directories, UNC paths and configured roots |
| No private source copied | Review rule + CI scan for large binaries and source signatures; adapters read at runtime |
| No runtime DB, logs or artifacts tracked | `.gitignore` + privacy test + release checklist |
| No submodules | CI fails on `.gitmodules` |
| Synthetic examples only | Example fixture policy; provenance recorded in `examples/README` |

`.gitignore` is a convenience layer, not a control (ADR-022). The privacy test suite fails the build independently of ignore rules.

## 6. Egress Governance

### 6.1 Default posture

| Destination | Default | Consent required |
|---|---|---|
| Loopback Ollama | Allowed | No |
| Configured external provider (OpenCode/OpenAI/Gemini/Anthropic/xAI) | Denied | Yes: explicit provider, model, consent classes and environment key |
| Cloud model provider | Denied | Yes, per provider and data class |
| Git remote | Denied | Yes, per operation |
| Skill registry | Denied | Yes, per install/update |
| MCP remote server | Denied | Yes, per server |
| Scanner updates | Denied | Yes, per action |
| Telemetry/analytics | Never implemented | n/a |

### 6.2 Consent record

```yaml
consent_id: cns_01J...
scope: model_provider
provider: opencode
endpoint: https://opencode.ai/inference/openai/v1
data_classes: [C0, C1]          # C2 requires a separate, explicit grant
purpose: "assist with code review"
retention_unknown: true
granted_at: 2026-11-20T10:00:00Z
expires_at: 2027-02-20T10:00:00Z
revoked_at: null
```

Rules:

- Consent is explicit, revocable and scoped; there is no global "allow all".
- C2 egress requires a separate consent naming the data class and a warning that source content leaves the machine.
- Revocation takes effect on the next call; in-flight calls are not retroactively unsent.
- Every egress attempt, allowed or denied, is recorded with destination class, purpose, payload hash and policy version. Payload content is not stored.
- A provider change (endpoint or account) invalidates consent.

**Implementation status (M1).** The SQLite `consents` table stores `scope`, `subject` (provider), `endpoint` (currently NULL), `data_classes`, `purpose`, `retention_unknown`, `granted_at`, `expires_at`, `revoked_at` and `evidence_note`. `egress_decisions` records provider, model, class, allow/deny, reason, consent reference and timestamp. `model_calls` records the request hash, latency, status, usage, finish reason and provider request ID. Neither table stores prompts, response text or keys. Every consent, egress and model-call write also appends a content-free `audit_events` row in the same transaction. Service-evaluated policy decisions are chained too, with decision ID, effect, reason/rule, policy version/hash, inputs hash, principal, action, canonical resource and context source; context attributes and task IDs are omitted, and absolute or drive-letter paths are rejected. Session/task audit events contain IDs, states, project ID, principal, policy version, goal/title hashes, request/response hashes, finish reason and usage. Session titles and task goals remain private local rows; no message history or response text is persisted by the minimal runtime. Project-root rows keep the canonical absolute path locally; registration/removal events carry only the project ID, canonical-path SHA-256 and filesystem identity hash. That chain is tamper-evident, not tamper-proof: deletion or rewriting is detectable, but a direct database actor can drop triggers and truncate the tail, and external anchoring is not implemented. Policy-version stamping and endpoint- or account-change invalidation are not implemented yet.

**Read-only tools (M1 bounded slice).** Only the explicit built-ins `project.list`, `project.list_files`, `project.read_text`, and `git.status` are registered. Calls require typed schema validation and an allow decision through `PolicyService` before tool work. The session's project ID is resolved through the durable root registry; a caller-supplied root path is not accepted. Roots are registered only by explicit local-user CLI actions that reject missing paths, files, reserved runtime locations, overlaps and symlink/junction components. Paths are project-relative; traversal, absolute paths, known secret files/directories, symlink/junction components and oversized requests are rejected. POSIX reads use a descriptor walk with `O_NOFOLLOW`; Windows checks links/reparse points and opened handle identity, with a documented residual swap window. Listings and reads are bounded; Git uses one fixed `git status` argument vector with no shell, timeout and output cap. Audit events contain IDs, hashes, status, byte counts and sanitized error codes, never file text, raw Git output or absolute paths. The default CLI prints only invocation metadata and hashes. Results are returned in memory to the explicit caller and are not persisted. Model response text reaches tool work only through the strict ADR-042 JSON envelope, which rejects duplicate keys, extra fields and identity/root/policy argument keys; prompts, responses, file contents and tool results stay in memory, while persisted state and audit rows keep only hashes, statuses, counts and sanitized error codes.

### 6.3 Egress redaction

Before egress: secret scan, absolute path normalisation to project-relative form, removal of environment dumps, truncation of unrelated files, and classification check. If the payload's class exceeds consent, the call is denied.

## 7. User Rights and Control

EIS is a local tool; the user is the data controller in practice.

| Right | Implementation |
|---|---|
| Access | `eis export --all --preview`; SQLite is readable by the user |
| Correction | Edit records through CLI/API; corrections are audited |
| Deletion | `eis project delete`, `eis knowledge delete`, `eis data purge`; two-step with counts |
| Portability | JSON/Markdown export with manifest and hashes |
| Restriction | Disable a provider, skill, automation or project root |
| Transparency | Activity timeline, policy decisions, egress ledger |

Deletion semantics: soft delete → purge after confirmation; audit chain remains with redacted payloads; evidence referenced by retained records is tombstoned, not silently orphaned.

## 8. PAM Boundary

- PAM is a separate private project with its own repository, storage and lifecycle.
- EIS reaches PAM only through the versioned retrieval adapter (§7 of the memory architecture).
- EIS does not import PAM classes, read its database, share its config or write to its vault.
- Queries sent to PAM are minimal and classified C2; PAM is treated as a local processor, not an external provider.
- If PAM is unavailable, EIS works with lexical retrieval.
- PAM's own data governance remains PAM's responsibility; the adapter contract is documented on both sides.

## 9. Obsidian Boundary

- A separate EIS vault, never the PAM-related vault.
- Writes are restricted to an `EIS/` managed folder.
- Generated notes carry `eis_generated: true` and a content hash.
- Notes are derived, redacted and one-way; editing them does not change EIS state.
- Secrets and raw source dumps are never written.
- The vault path is runtime configuration, never committed.

## 10. Privacy Regression Suite

| Test | Assertion | Gate |
|---|---|---|
| `eis privacy-check` / `tests/test_privacy.py` | No drive-letter/home/UNC paths, runtime data files or secret-shaped values in candidate files | PR + release |
| `tests/test_repository_privacy.py` | Repo root is exactly EIS; no submodules; vision document ignored and untracked; runtime paths ignored | PR + release |
| `test_no_secrets_in_repo` | Gitleaks/pattern scan of work tree and history | PR + release |
| `test_no_runtime_data_tracked` | No `*.sqlite3`, `artifacts/`, `logs/`, `keys/` | PR |
| `test_logs_redacted` | Secret corpus never appears in log fixtures | PR |
| `test_artifacts_redacted` | C3 corpus never stored in artifact fixtures | PR |
| `test_model_payload_redacted` | Fake provider captures payload; no C3, no absolute paths | PR |
| `tests/test_model_loop.py` | Goal, prompt, response, file contents and tool feedback never reach SQLite, audit payloads or default CLI output; an answer prints only via `--show-proposal` | PR |
| `test_egress_requires_consent` | No call without consent; revocation blocks | PR |
| `test_export_preview` | Export requires preview and approval; counts match | PR |
| `test_deletion_complete` | Deleted project yields no retrieval hits or artifacts | PR |
| `test_obsidian_scope` | Writes cannot leave `EIS/`; drift not overwritten | PR |
| `test_public_examples_synthetic` | Example provenance markers present | Release |

The path, runtime-data and secret-shaped checks are implemented in M0.2. The remaining rows are M1+ gates; the full suite is a release blocker.

## 11. Public Release Data Handling

1. Freeze the release branch.
2. Run full-history secret scan (not just the working tree).
3. Run the privacy suite and dependency licence check.
4. Generate an SBOM and verify no private package or path appears.
5. Review `CHANGELOG.md`, docs and examples for private references.
6. Confirm the planning document and any personal notes are excluded or approved.
7. Tag and sign; publish checksums.
8. Record the release audit in the repository.

Any confirmed leak triggers the incident response below and, if published, history rewriting plus user notification.

## 12. Privacy Incident Response

| Step | Action |
|---|---|
| Detect | Release gate, user report or integrity monitor |
| Contain | Stop egress, revoke provider consent, quarantine artifacts |
| Assess | Identify data classes, subjects, scope and whether publication occurred |
| Eradicate | Purge local copies, rotate secrets, rewrite public history if needed |
| Recover | Restore from clean backup, re-run gates |
| Record | Local incident record with timeline and actions |
| Notify | User decides on external notification; EIS does not notify third parties automatically |

## 13. Data Governance Roles

| Role | Responsibility |
|---|---|
| User | Owns data, grants consent, approves exports and deletions |
| EIS maintainers | Enforce repository separation, review privacy-impacting changes |
| Security reviewer | Reviews egress, skills and dependencies before release |
| Future team admin | Would own shared knowledge policy (out of scope v1) |

## 14. Third-Party and Model Provider Governance

- Providers are listed with endpoint, data classes, retention statement (if known) and consent status.
- Unknown retention is recorded as `retention_unknown: true` and shown at consent time.
- No provider is enabled by default.
- Provider SDKs are optional extras so a local-only install ships no cloud client.
- Model outputs are not used to train or improve EIS; no feedback loop exists.
- PAM and Obsidian are local processors; their governance is documented separately and linked, not duplicated.

## 15. Acceptance Criteria

1. Every data item in §3 has a class, store, retention and export rule.
2. Runtime paths resolve outside the repository on all three platforms and are verified by a test.
3. No model call occurs without a consent record; the egress ledger proves it.
4. The privacy suite passes in CI and blocks release on failure.
5. Deleting a project removes retrievable traces and leaves an audit record without private payload.
6. A release rehearsal produces zero findings in the public-release checklist.
