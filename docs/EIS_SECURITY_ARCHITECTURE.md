# EIS Security Architecture

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_THREAT_MODEL.md](EIS_THREAT_MODEL.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_PRIVACY_AND_DATA_GOVERNANCE.md](EIS_PRIVACY_AND_DATA_GOVERNANCE.md), [SECURITY.md](../SECURITY.md)

---

## 1. Security Principles

1. **Deterministic enforcement, probabilistic assistance.** The model may suggest; only code authorises. Every control that matters is testable without an LLM.
2. **Deny by default.** Unmatched policy, unknown tool, unknown capability, malformed manifest and missing scanner all resolve to deny or `unavailable`, never to allow or pass.
3. **Least authority.** Agents, skills and tools receive the intersection of declared, approved and role-ceiling capabilities for one action.
4. **One chokepoint.** Filesystem writes, process execution and network egress occur only through `ExecutionGateway`.
5. **Untrusted by default.** Repository text, issues, commit messages, tool output, MCP descriptions, community skills and model output are data with no authority.
6. **Approval is not sandboxing.** Human consent does not make an unsafe execution safe; isolation is separate.
7. **Tamper evidence over trust.** Audit and evidence are hash-linked and independently verifiable.
8. **Fail closed and loud.** Security failures stop the action and produce an actionable error; they are never downgraded to warnings.

## 2. Security Guardian

The Security Guardian is a set of components, not a persona or a prompt.

| Component | Function | Determinism |
|---|---|---|
| `PolicyEngine` | Evaluate capability × resource × context | Pure, table-tested |
| `ApprovalGate` | Bind, expire and consume approvals | State machine |
| `ExecutionGateway` | Enforce isolation, limits and command profiles | Code |
| `PathGuard` | Canonicalise paths, resolve symlinks/junctions, enforce root containment | Code |
| `RedactionEngine` | Detect and replace secrets before persistence/egress | Rules + scanner |
| `EgressController` | Deny network by default; allow configured destinations | Code |
| `SkillValidator` | Schema, provenance, permission diff, static scan | Pipeline |
| `SupplyChainGuard` | Lockfiles, SBOM, advisory checks, pinned actions | CI |
| `AuditChain` | Append-only hash-chained events | Code + DB triggers |
| `IntegrityMonitor` | Verify chain, artifact hashes, config drift | Scheduled |
| `SecurityAgent` | Triage and remediation advice | LLM-assisted, non-authoritative |

The Security Agent may recommend; it may not change policy, grant capability or suppress a finding.

## 3. Trust Zones

| Zone | Contents | Assumed compromise? |
|---|---|---|
| Z0 Host OS | kernel, filesystem, user account | No, but host malware is a stated residual risk |
| Z1 EIS core | policy, gateway, audit, DB | No |
| Z2 EIS entry points | API, CLI, worker, dashboard | No, but input is untrusted |
| Z3 Project content | source, docs, issues, Git metadata | **Yes — hostile** |
| Z4 Model | provider runtime and outputs | **Yes — unreliable and manipulable** |
| Z5 Skills | community/private packages | **Yes — hostile until validated** |
| Z6 External tools | scanners, test runners, MCP servers | Partially |
| Z7 Network | model endpoints, Git remotes, registries | **Yes — hostile** |

Data crossing from Z3/Z4/Z5/Z7 into Z1 is validated, redacted, size-capped and provenance-tagged.

## 4. Capability Model (summary)

Full definitions are in [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md).

| Capability | Grants | Never granted to |
|---|---|---|
| `project.read` | Read files under a registered root | Unregistered paths |
| `project.write` | Apply an approved patch | Agents directly; only gateway with approval |
| `project.verify` | Run allowlisted verification commands | Arbitrary commands |
| `security.scan` | Run configured scanners | Network scanners by default |
| `knowledge.read/write` | Read/propose knowledge records | Direct verification state changes |
| `skill.invoke` | Invoke an active skill version | Unvalidated packages |
| `skill.install` | Install/activate a skill | Automatic community installs |
| `tool.invoke` | Call a registered tool | Unregistered tools |
| `net.egress:<dest>` | Reach a named destination | Wildcard destinations |
| `approval.request` | Ask the user | `approval.grant` (user only) |
| `audit.read` | Read audit records | `audit.write` outside the chain |

`secret.read`, `policy.write`, `approval.grant` and `skill.grant_capability` are reserved to the human principal and are not in any agent ceiling.

## 5. Control Catalogue

| ID | Control | Threat addressed | Verification |
|---|---|---|---|
| SEC-01 | Deny-by-default policy, versioned rules | Unauthorised action, excessive agency | Decision-table tests |
| SEC-02 | Resource resolution by ID + relative path, canonical containment | Path traversal, symlink escape | Escape corpus |
| SEC-03 | Command profiles, no shell, argument arrays | Command injection | Injection corpus |
| SEC-04 | T1/T2 isolation with limits | Dangerous execution, resource exhaustion | Escape/limit tests |
| SEC-05 | Egress deny by default, destination allowlist | Exfiltration | Network test harness |
| SEC-06 | Secret detection + redaction at three points (ingest, persist, egress) | Secret disclosure | Redaction corpus |
| SEC-07 | Untrusted-content envelopes and instruction hierarchy | Prompt injection | Adversarial repo corpus |
| SEC-08 | Skill validation pipeline and trust classes | Malicious skill | Malicious package corpus |
| SEC-09 | Approval bound to action + args + diff hash, single use, TTL | Approval replay/scope creep | Approval state tests |
| SEC-10 | Hash-chained audit, append-only triggers | Audit tampering | Chain verification test |
| SEC-11 | Loopback bind, bearer token, Host/Origin checks | Local web attack, DNS rebinding | Integration tests |
| SEC-12 | Lockfiles, pinned actions, SBOM, advisory scan | Supply chain | CI |
| SEC-13 | Provider consent + egress record + classification ceiling | Unconsented model egress | Egress ledger test |
| SEC-14 | Backup, restore verification, integrity check | Data loss, silent corruption | Restore drill |
| SEC-15 | Privacy suite and release gate | Private data publication | CI + release checklist |
| SEC-16 | Structured logs with redaction and correlation IDs | Log leakage | Log inspection test |
| SEC-17 | Fail-closed configuration validation | Misconfiguration | Startup tests |

## 6. Isolation Model

| Tier | Mechanism | Blocks | Does not block |
|---|---|---|---|
| T0 | In-process pure functions, no I/O | Side effects by construction | CPU/memory exhaustion (bounded by limits) |
| T1 | Subprocess, explicit cwd, scrubbed env, no shell, timeout, output cap, process-group kill | Accidental writes outside cwd, shell injection, runaway output | A determined process with user rights (especially Windows) |
| T2 | Linux container: non-root, read-only rootfs and project copy, no network, dropped capabilities, no-new-privileges, seccomp, CPU/memory/PID limits, bounded output, disposable workspace and forced cleanup | Filesystem escape, network egress, privilege escalation, host secret exposure | Kernel vulnerabilities, container daemon compromise, a daemon-accessible local attacker |

Rules:

- Scanners and test runners default to T1 with a command profile; T2 is used when the tool is untrusted or the project is untrusted.
- **On Windows, T1 is explicitly not a security boundary** (ADR-009). Executable community skills require T2 or are refused.
- A T2 run mounts only a filtered disposable copy of the project path needed, read-only unless the specific profile requires a writable workspace copy, which is discarded after the run; secrets, `.git`, `.eis`, backups and runtime database files are excluded.
- Environment is an application-declared allowlist of non-secret keys with capped values; no host tokens, SSH agent or cloud credentials are passed. No image, mount or isolation flag comes from a model or caller.
- T2 is available only after a Linux container probe demonstrates every required protection; otherwise the gateway refuses with `isolation_unavailable`. On Windows the probe cannot demonstrate Linux-container protections, so T2 stays unavailable.

**Implementation status (ADR-044).** `eis.core.sandbox.SandboxBackend` is a two-method interface implemented by `LinuxContainerBackend`. `ExecutionGateway` routes T2 profiles and untrusted projects to it only after the existing policy and audit gates; the container argv is fixed, uses an immutable image digest and an application-supplied seccomp profile, and never mounts the host Docker socket, EIS data dir or other project roots. T1 remains a same-user subprocess with no security-boundary claim on Windows.
- Output is streamed into a bounded buffer; excess is truncated with a marker and the truncation is recorded.

## 7. Secret Handling

Pipeline: **detect → classify → redact → store reference → audit**.

**Implemented M1 subset (2026-11-24):** `eis.core.redaction.redact_text` makes a best-effort provider-egress pass over task goals and read-only tool feedback and redacts answer/plan previews returned to callers. `eis.core.structured_logging` emits allowlisted scalar JSON with correlation IDs; API access logs omit bodies and credentials. This is not the complete ingest/persistence/egress classification pipeline below. Pattern redaction does not replace C3 blocking and may miss arbitrary secrets or personal information; the authenticated runtime API and `eis serve` are still pending.

| Stage | Action |
|---|---|
| Ingest | Scan repository files, env dumps, logs and tool output with pattern rules and Gitleaks when available |
| Classify | Mark findings C3; never enter model context, DB text columns, audit payloads or reports |
| Redact | Replace with `<SECRET:type:hash8>`; keep mapping in memory for the operation only |
| Persist | Store only the redacted form and a detection record (type, location hash, rule) |
| Egress | Second-pass scan; block on any remaining C3 match |
| Audit | Record detection counts and policy outcome, never values |

Limitations: redaction is heuristic. Therefore C3 data is *blocked by classification* from model context, not merely filtered. High-entropy false positives are reported as warnings, not silently dropped.

## 8. Prompt-Injection Resistance (defence in depth)

1. **Authority separation.** The model has no capability; it emits intents that policy evaluates.
2. **Structural delimiting.** Untrusted content is wrapped with source, path and commit, and the system prompt states that it is data.
3. **Tool gating.** A successful injection still cannot call an unregistered tool, exceed a capability or write without approval.
4. **Path binding.** Tools accept project IDs and relative paths; the gateway resolves them. The model cannot name arbitrary absolute paths.
5. **Output validation.** Tool output is schema-validated and size-capped before re-entering context.
6. **Detection and telemetry.** Injection patterns produce a `security.injection_suspected` event; repeated attempts raise the task's risk level and require approval for the next side effect.
7. **No secret context.** There is nothing valuable to exfiltrate from the prompt because C3 never enters it.

Residual risk: a model can still be persuaded to *propose* a harmful action. The controls ensure the proposal cannot execute without deterministic authorisation.

## 9. Network Egress

| Destination | Default | Control |
|---|---|---|
| Loopback model runtime (Ollama) | Allowed | Local only, no consent needed |
| Configured external provider (OpenCode/OpenAI/Gemini/Anthropic/xAI) | Denied | Explicit provider config, scoped consent, HTTPS except loopback, no key logging |
| Configured cloud model | Denied | Consent record + per-call classification check |
| Git remote (fetch/push) | Denied | User-initiated, credential from environment/keyring, audited |
| Scanner signature update | Denied | Explicit user action |
| Skill registry | Denied | Explicit install/update |
| MCP remote server | Denied | Per-server allowlist entry |
| Telemetry | Never | Not implemented |

DNS resolution is not a proxy for authorisation; destinations are matched on scheme, host and port. Redirects across hosts are re-evaluated.

## 10. Supply Chain

- Python: `uv.lock`, hashes, `pip-audit`/OSV scan, licence check.
- Node: `package-lock.json`, `npm audit`, pinned GitHub Actions by commit SHA.
- Containers: digest-pinned base images, Trivy scan, non-root, no `latest`.
- Releases: SBOM (CycloneDX), SHA-256 checksums, signed tag (Sigstore optional), reproducible build instructions.
- Skills: content hash, provenance, signature optional but required for "verified publisher" status.

## 11. Model Provider Controls

- Provider configuration is explicit; no auto-detection that could silently send data.
- Consent records name provider, endpoint, data classes and expiry; revocation takes effect on the next call.
- Request payloads carry only the minimum context; full repository dumps are prohibited.
- Responses are untrusted, schema-validated and never executed.
- Provider errors are logged without prompt content by default (hash + token counts only).

**Implementation status (2026-11-27).** Consent, provider-egress/model-call, policy, approval, execution and session/task events are hash chained; projection rows commit with their chain event and failures block sensitive work. A tested `AuditAnchorSink` interface creates content-free checkpoints of verified chain prefixes with strict sequence/hash binding, idempotent retries and fail-closed verification; no real external backend is configured, so external anchoring is **unverified**. A local anchor event is only a receipt/reference, not the external copy. A3 project writes are digest-bound, short-lived and single-use; execution rechecks policy, root and target hashes, backs up the original and atomically replaces it. Strict bounded task/plan envelopes expose four read-only tools and one optional final task-bound write-proposal step. The model cannot authorize or execute. Task, plan and proposal step enter approval-wait atomically; successful execution evidence completes them, and rejection fails them atomically. Explicit plan recovery retries only interrupted read-only work after policy/session/root checks. Provider-egress redaction is credential-pattern defense in depth, not comprehensive scanning. T2 uses a fail-closed Linux-container backend but has not been probed on a real Linux host; T1 is not a Windows boundary. Authenticated runtime API and human-review continuation are implemented; delegation and a configured external anchor backend remain unverified/unimplemented. The chain is tamper-evident, not tamper-proof.

## 12. Local API and Dashboard

- Bind `127.0.0.1`; refuse `0.0.0.0` unless an explicit, warned configuration is set.
- Generate a 256-bit token at first run; file permissions user-only; rotate command available.
- Validate `Host` against an allowlist (`127.0.0.1`, `localhost`, configured port) to defeat DNS rebinding.
- Validate `Origin` for state-changing requests; no wildcard CORS.
- Same-site cookies are not used for API auth; the token is sent in an `Authorization` header by the SPA bootstrap.
- CSP, `X-Content-Type-Options`, `Referrer-Policy` and frame denial on dashboard responses.
- No directory listing; artifact download requires an authorised, classification-checked request.

## 13. Audit and Evidence Integrity

- `audit_events` is append-only via the repository API and database triggers.
- Each event includes a stable ID, sequence, actor, event type, subject, decision, timestamp, content-free payload and `prev_hash`.
- `hash = SHA-256(prev_hash || canonical event JSON)`; genesis is 64 zeros. Verification recomputes from sequence 1, checks continuity and reports the first mismatch.
- Chained boundaries: every session/task lifecycle transition, every service-evaluated policy decision (allow, explicit deny and default deny), consent grant/revoke, every egress allow/deny and model-call outcome, approval request/grant/reject/consume, execution started/completed/failed, project-root registration/removal, and read-only tool invocation denied/started/completed/failed. Policy decisions, root changes and tool invocation starts are recorded before their respective operation proceeds.
- `PolicyService` is the audited boundary; direct `PolicyEngine` evaluation remains pure and unaudited. Policy payloads omit context attributes/task IDs and reject absolute or drive-letter paths.
- Approval chain payloads contain identifiers, hashes and expiry only; private diff/content stays in the local approvals table and rejection notes are hashed in the chain.
- Tool audit payloads contain invocation/task IDs, tool name, project ID, policy decision ID, hashes, status, byte count and sanitized error codes only. They omit raw arguments, file contents, raw Git output and absolute paths. Root registration events contain the project ID, canonical-path SHA-256 and identity hash only.
- `eis audit verify` runs on demand. An integrity failure blocks further appends, approval decisions, execution and read-only tools; the dispatcher relies on verified chain appends before work.
- Daily anchor: SHA-256 of the latest verified chain prefix, submitted through `AuditAnchorSink` to an independently controlled external service. The interface and fake-backed tests are implemented; no real external backend is configured or verified. A local event/file is a receipt, not the external copy.
- Evidence artifacts are content-addressed; a manifest records size and hash.
- This is tamper evidence, not tamper-proof storage. A privileged database actor can drop triggers and rewrite or truncate the chain; tail truncation is undetectable without the external anchor.

## 14. Backup, Recovery and Incident Response

| Scenario | Response |
|---|---|
| Corrupt database | Restore last verified backup; replay outbox not possible beyond backup point; record gap |
| Lost artifact | Mark referencing claims `unverified`; do not silently continue |
| Audit mismatch | Block writes/approvals; preserve state; require operator review |
| Suspected secret exposure | Rotate secret, purge affected artifacts, record incident, notify user |
| Malicious skill detected | Revoke version, quarantine package, roll back executions if possible, audit |
| Unconsented egress detected | Disable provider, preserve evidence, notify user |

A local incident record is created for every security event above. There is no automatic external reporting.

## 15. Secure Defaults

- Local model only; no cloud provider configured.
- No network egress except loopback model.
- No project roots configured; the user must register them explicitly with the local CLI.
- Default policy has no rules; reads and Git remain denied until an explicit policy file allows exact resources.
- Community skills disabled; declarative built-ins only.
- Automation disabled until the user enables a schedule.
- Destructive capability requires approval and is disabled by default.
- Verification tools must be detected; missing tools are `unavailable`.

## 16. Security Testing Requirements

| Suite | Minimum cases | Gate |
|---|---|---|
| Policy decision table | 100% of capabilities × allow/deny/approval | PR |
| Path escape corpus | traversal, symlink, junction, UNC, case-insensitive collisions, moved/replaced roots, file swap during read | PR |
| Command injection corpus | shell metacharacters, argument confusion, env expansion | PR |
| Prompt-injection repository corpus | instructions in README, code comments, issues, filenames | PR |
| Secret redaction corpus | 20+ secret types, high-entropy false positives | PR |
| Approval binding | argument mutation, diff mutation, replay, expiry | PR |
| Sandbox limits | timeout, memory, output flood, fork bomb (T2) | Nightly |
| Egress | unconsented provider, redirect, DNS rebinding | PR |
| Audit chain | mutation, deletion, reordering | PR |

## 17. Residual Risks (accepted, documented)

1. A compromised host with the user's rights can read runtime data and the API token.
2. T1 on Windows is a reliability boundary, not a security boundary.
3. Redaction can miss a novel secret format; classification limits the blast radius.
4. A local model can be manipulated by content; policy prevents execution, not bad advice.
5. Docker daemon compromise defeats T2 isolation.
6. Audit chains detect tampering but cannot prevent a privileged actor from rewriting the whole chain and anchor.
7. Supply-chain compromise of a pinned dependency remains possible until an advisory is published.
8. Windows has no standard-library root-directory-relative open; a local actor who can replace a path component during the narrow check/open window may evade the identity comparison. POSIX descriptor walks do not have this specific gap.
9. Root replacement detection depends on stable filesystem `st_dev`/`st_ino` values; filesystems that report zero or unstable identities weaken it.

Each risk has an owner, a review date and a compensating control in the threat model.

## 18. Acceptance Criteria

1. Every control SEC-01…SEC-17 has an automated test or a documented manual check with evidence.
2. No route or module can reach `subprocess`, raw network or project files outside the gateway (static gate).
3. A red-team corpus of 50+ attack cases produces zero unauthorised writes and zero unconsented egress.
4. The integrity check detects a mutated audit row and blocks approvals.
5. Secret corpus produces zero literal secret occurrences in logs, artifacts, reports and model payloads.
