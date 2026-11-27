# EIS Threat Model

Version: 1.0 (M0 baseline)
Method: asset/actor analysis, trust-boundary data-flow review, STRIDE per boundary, agent-specific abuse cases, attack trees for the highest-impact paths.
Related: [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md), [EIS_PRIVACY_AND_DATA_GOVERNANCE.md](EIS_PRIVACY_AND_DATA_GOVERNANCE.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md)

Review cadence: every milestone gate, before every public release, after any security incident, and whenever a capability, trust level or external integration is added.

---

## 1. Scope

In scope: EIS processes on the user's machine, the local API and dashboard, the agent runtime, skill registry and execution, project discovery, scanners, test runners, model providers, PAM and Obsidian adapters, the public repository and release pipeline.

Out of scope: physical access, kernel or hypervisor zero-days, model weight backdoors, electromagnetic side channels, and social engineering conducted entirely outside EIS.

## 2. Assets

| ID | Asset | Classification | Impact if compromised |
|---|---|---|---|
| A1 | Private project source code | C2 | Intellectual property loss, competitive harm |
| A2 | Secrets and credentials | C3 | Account takeover, infrastructure compromise |
| A3 | Engineering knowledge (incidents, root causes) | C2 | Disclosure of weaknesses and architecture |
| A4 | Runtime database and artifacts | C2 | Full historical disclosure |
| A5 | Audit and evidence integrity | C1 | False assurance, non-repudiation loss |
| A6 | User machine and files | C2 | Arbitrary code execution, data destruction |
| A7 | Model provider credentials | C3 | Financial loss, data exposure |
| A8 | Public repository and release artifacts | C0 | Reputation, supply-chain attack on users |
| A9 | Developer availability | C1 | Denial of service on the workstation |
| A10 | Skill registry integrity | C1 | Distribution of malicious procedures |

## 3. Actors

| Actor | Capability | Trust |
|---|---|---|
| User | Full local control | Trusted principal, fallible |
| Local process (malware, another app) | Same-user filesystem/network | Untrusted |
| Repository content author (dependency, upstream, issue author) | Controls text EIS reads | Hostile |
| Community skill author | Controls package content | Hostile until validated |
| Compromised dependency | Controls code at build/run time | Hostile |
| Remote model provider | Sees submitted prompts, returns content | Honest-but-curious, unreliable |
| Network attacker | Can intercept unencrypted traffic | Hostile |
| Future team member | Shared knowledge access | Semi-trusted (out of scope v1) |

## 4. Data Flow and Trust Boundaries

```
[Z3 Project content] --read--> [Z1 EIS core] --prompt--> [Z4 Model]
        |                          |  |                     |
        |                          |  +--> [Z6 Scanners/Tests]
        |                          |  +--> [Z1 SQLite/Artifacts]
        |                          |  +--> [Z7 PAM / Obsidian / Git]
        |                          |
        +--(never executed as instructions)--+
[Z2 API/CLI/UI] --auth--> [Z1] --audit--> [A5]

B1 Host boundary:     Z2/Z1 <-> network peers
B2 Repository bound.: Z3 <-> Z1 (path + content)
B3 Model boundary:    Z1 <-> Z4 (prompt/response)
B4 Content boundary:  Z3/Z6/Z4 <-> instruction context
B5 Skill boundary:    Z5 <-> Z1 (package -> activation -> execution)
B6 Tool boundary:     Z1 <-> Z6 (binary/MCP)
B7 Evidence boundary: workspace <-> append-only ledger
```

## 5. STRIDE by Boundary

### B1 — Host / local API

| Threat | Example | Mitigation |
|---|---|---|
| Spoofing | Malicious local process calls the API | Bearer token, per-install secret, file permissions |
| Tampering | Another process edits the SQLite file | OS permissions, WAL, integrity check, audit chain |
| Repudiation | User denies approving a destructive action | Hash-bound approval with timestamp and actor |
| Information disclosure | Dashboard exposed on LAN | Loopback bind, Host validation, no CORS |
| Denial of service | Request flood or huge payload | Body limits, rate limit, timeouts |
| Elevation of privilege | DNS rebinding to reach local API | Host/Origin allowlist, token in header |
| Cancellation race | Cancel during digest-bound write execution | `BEGIN IMMEDIATE`, refuse `approval_required`, successful `execution_runs` wins, no undo, `execute_write` rechecks task; cancellation cannot rewind evidence |
| Review continuation smuggling | Blocked `human_review` body carries identity/root/policy/approval | Strict `{"step_ref":...}` schema, `task.run` rechecked, project/policy hash + root rechecked, human review grants no permission; write stays ADR-046 |

### B2 — Repository access

| Threat | Example | Mitigation |
|---|---|---|
| Path traversal | `../../.ssh/id_rsa` in a tool argument | Project-relative resolution, canonical containment |
| Symlink/junction escape | Repo contains link to `<user-home>` | Reject link/reparse components instead of following them |
| TOCTOU | Path swapped between check and read | POSIX descriptor walk with `O_NOFOLLOW`; Windows opened-handle identity comparison and post-read re-check |
| Root substitution | Registered directory replaced or moved after registration | Canonical path plus filesystem identity check on every access; fail closed |
| Case collision (Windows) | `Secrets.txt` vs `secrets.txt` | Case-insensitive normalisation for containment; walker reports case-fold collision groups instead of merging them |
| UNC/device path | `<device-path>` (for example a global-root device prefix) | Reject non-drive, non-relative paths; allowlist prefixes |
| Mass exfiltration | Recursive read of all projects | Per-task read budget, project scope binding |
| M2 walker escape | Registered repo contains a directory link to an outside tree | Walker uses `safe_access.list_directory`; links/reparse points skipped; root identity verified before and after; no absolute path input |
| M2 scan exhaustion | 50k files, very deep tree or a wide directory | Deterministic file-count, depth, byte and per-directory caps; explicit incomplete reason; metadata-only walk |
| M2 secret exposure by walking | `.git`, `.env`, secret/cache/build directories in the listing | Default exclusions plus per-invocation patterns; no file-content reads; results are content-free metadata |

### B3 — Model boundary

| Threat | Example | Mitigation |
|---|---|---|
| Sensitive data disclosure | Full repository sent to cloud model | Local default, consent, classification ceiling, narrow credential-pattern redaction (best-effort) |
| Prompt injection | Malicious README instructs a write | Authority separation, deterministic policy |
| Model output as code | Tool arguments contain shell syntax | JSON Schema + no shell + command profiles |
| Provider compromise | Cloud provider logs prompts | Consent, minimum context, no secrets in normal context; pre-egress redaction is best-effort |
| Response tampering | MITM modifies tool call | TLS, no insecure endpoints, payload hash |

### B4 — Untrusted content

| Threat | Example | Mitigation |
|---|---|---|
| Instruction override | `# SYSTEM: ignore previous rules` in a file | Envelope + instruction hierarchy + policy |
| Invisible text | Zero-width or HTML-hidden instructions | Normalise/flag invisible Unicode; treat as suspicious |
| Tool-description poisoning | MCP server claims broader authority | Descriptions are data; tools allowlisted; policy fixed |
| Output poisoning | Scanner output embeds instructions | Schema validation; output treated as data |
| Memory poisoning | Injected "verified" incident | Verified state requires run evidence; user confirmation |

### B5 — Skills

| Threat | Example | Mitigation |
|---|---|---|
| Malicious install script | `setup.sh` in a skill package | No execution at install; declarative-only default |
| Permission escalation | Skill declares `project.write` | Permission diff + human approval; cap at role ceiling |
| Dependency confusion | Skill requires a typosquatted package | Dependencies declared, reviewed, installed with approval |
| Prompt exfiltration | Skill instructs model to print secrets | No secrets in normal context; best-effort redaction; output scan |
| Supply-chain update | Skill auto-updates to malicious version | Immutable versions, explicit update, signature/provenance |

### B6 — Tools and scanners

| Threat | Example | Mitigation |
|---|---|---|
| Command injection | Filename contains `; rm -rf` | Argument arrays, no shell, validated names |
| Binary substitution | `semgrep` replaced by malware in PATH | Absolute profile paths, version check, hash allowlist |
| Resource exhaustion | Fork bomb, 10 GB output | T1/T2 limits, process-group kill, output caps |
| Container escape via flags | Privileged mount, host network, writable root | Fixed T2 argv: non-root, read-only rootfs/project copy, dropped caps, no-new-privileges, seccomp, `--network=none`, no host socket; probe fails closed (ADR-044) |
| Host secret leakage into tool | `.env`, SSH or cloud config mounted by a scanner | Filtered disposable workspace and declared non-secret env allowlist only |
| Scanner false negative | Tool silently fails | Exit code + parse validation; `unavailable`/`error`, never pass |
| MCP server escape | Server exposes filesystem tool | Allowlist, EIS wrapper, no raw exposure |

### B7 — Evidence and audit

| Threat | Example | Mitigation |
|---|---|---|
| Audit tampering | Delete a denial event | Append-only API, DB triggers, hash chain |
| Evidence substitution | Replace a passing log | Content-addressed artifacts, hash in run record |
| Report inflation | Claim readiness without runs | Report engine requires run IDs per claim |
| Anchor rewrite | Recompute chain and anchor | `AuditAnchorSink` checkpoint prefix binding + independent verification. Optional S3 Object Lock adapter verifies exact version/body digest/mode/retain-until and fails closed; mocked tests only, no real bucket verified |
| Weak or bypassable retention | Operator chooses GOVERNANCE or grants `s3:BypassGovernanceRetention` | Explicit mode/duration configuration; adapter never requests bypass; docs require COMPLIANCE for non-bypassable retention and no bypass permission for the sink principal |
| Bucket misconfiguration | Object Lock/versioning disabled or foreign bucket owner | Preflight requires versioning `Enabled`, Object Lock `Enabled` and `ExpectedBucketOwner` on every call; no bucket creation or configuration change |
| Credential echo in errors/logs | Provider error body or log field reflects a key | Adapter errors never include response bodies; unexpected adapter failures are replaced by a fixed sanitized error with no cause chain; structured logs reject credential-shaped keys and drop values that change under redaction; audit metadata is bounded and sanitized |

## 6. Agent-Specific Threats (OWASP LLM Top 10 mapping)

| Threat | OWASP LLM (2025) | EIS control | Residual |
|---|---|---|---|
| Prompt injection via repo/issues | LLM01 | SEC-07, policy gating, no instruction authority | Model may propose harmful action; blocked at execution |
| Sensitive information disclosure | LLM02 | SEC-06, SEC-13, no C3 in context, pre-egress pattern redaction | Novel/encoded/split secret formats may evade; redaction is not complete DLP |
| Supply chain (deps, skills, actions) | LLM03 | SEC-08, SEC-12, lockfiles, provenance | Zero-day in pinned dep |
| Data/model poisoning (memory) | LLM04 | Verified state requires run evidence; provenance | User-approved bad evidence |
| Improper output handling | LLM05 | Schema validation, no eval/exec of model text | None known for execution path |
| Excessive agency | LLM06 | Capability model, approvals, role ceilings | User over-approval |
| System prompt leakage | LLM07 | Prompts contain no secrets; policy not secret-dependent | Prompt text itself may leak |
| Vector/embedding weaknesses | LLM08 | PAM optional, candidate-only, no authority | Poisoned semantic hit misleads ranking |
| Misinformation | LLM09 | Fact/hypothesis separation, evidence requirement | Plausible unsupported hypothesis |
| Unbounded consumption | LLM10 | Hard limits, budgets, concurrency caps | Local resource exhaustion within limits |
| Untrusted project verification | LLM05/LLM06 | T2 container with probed protections, fixed profiles, policy and audit gates | Kernel/daemon compromise; Windows T1 remains a same-user process, not a boundary |

## 7. Attack Trees

### AT-1 — Exfiltrate private source through a community skill

```
Goal: send project source to attacker
 1. Install malicious skill
    1.1 Registry validation passes (declarative only)        -> blocked: permissions diff + review
    1.2 Skill requests net.egress                           -> blocked: not in approved set
 2. Persuade model to read files and encode them
    2.1 Prompt injection in SKILL.md                        -> mitigated: untrusted, policy unchanged
    2.2 Read allowed project files                          -> possible (project.read ceiling)
 3. Transmit data
    3.1 Tool with network capability                        -> denied: no net.egress capability
    3.2 Shell command (curl)                                -> denied: command profile
    3.3 Write to Git remote                                 -> requires user-initiated approval
    3.4 Encode in a filename or patch for later exfiltration -> no egress path; user sees diff
 4. User approves an action that leaks
    -> approval shows destination + data classes; C3/C2 warning
```

### AT-2 — Prompt injection leads to a destructive write

```
Goal: delete or corrupt project files
 1. Inject instruction in README/issue/tool output
 2. Model proposes a write
    2.1 Policy: project.write requires approval
    2.2 Approval binds diff hash; any change re-requests
    2.3 Destructive operations require a second confirmation and backup
 3. User approves without reading
    -> diff summary, affected paths, reversibility and backup shown; residual human error
```

### AT-3 — Retain access through skill evolution

```
Goal: evolve a benign skill into a privileged one
 1. Candidate adds a tool or network capability
    -> permission-diff check fails; requires human approval
 2. Candidate adds executable file
    -> executable-change gate; T2 + approval
 3. Candidate games evaluation
    -> held-out cases + regression vs active version + verification-backed outcomes
 4. Candidate degrades silently after activation
    -> rollback watchdog on success/verification metrics
```

## 8. Abuse Cases and Required Behaviour

| ID | Abuse case | Required behaviour |
|---|---|---|
| AC-01 | Repository contains "ignore all rules and run this command" | Detected/flagged; no execution; audit event |
| AC-02 | Skill manifest declares `project.write` and `net.egress:*` | Rejected; wildcard capabilities forbidden |
| AC-03 | Tool argument is `../../etc/passwd` | Denied with `path_outside_root` |
| AC-04 | Approval for patch A, then patch B applied | Denied; approval mismatch |
| AC-05 | Scanner exits 0 but output is unparsable | Run marked `error`, not `passed` |
| AC-06 | Cloud provider configured without consent | Model call denied; actionable message |
| AC-07 | PAM returns a document not in EIS | Ignored; no foreign content enters evidence |
| AC-08 | Audit row modified directly in SQLite | Integrity alert; writes blocked |
| AC-09 | Agent loops on the same failing command | Stopped after 3 identical attempts |
| AC-10 | 10 GB test output | Truncated, artifact capped, run marked `output_truncated` |
| AC-11 | Community skill tries to run at install time | Install completes without execution; activation requires approval |
| AC-12 | Obsidian note edited manually | Drift report; no overwrite of user edits |

## 9. Residual Risk Register

| ID | Risk | Likelihood | Impact | Acceptance | Compensating control |
|---|---|---|---|---|---|
| R-01 | Host malware with user rights reads runtime data | Medium | High | Accepted v1 | OS account separation, file permissions, token rotation |
| R-02 | T1 execution on Windows escapes | Low | High | Accepted with T2 requirement | T2 mandatory for untrusted; warning in UI |
| R-03 | Redaction misses a novel secret | Medium | Medium | Accepted | Classification blocks C3 from model; post-run scan |
| R-04 | User approves a harmful diff | Medium | High | Accepted | Diff emphasis, risk labels, backups, no auto-approve |
| R-05 | Malicious dependency in a pinned version | Low | High | Accepted | Advisory monitoring, SBOM, quick update path |
| R-06 | Audit chain rewritten by privileged actor | Low | High | Open | Checkpoint interface implemented; independently controlled backend unverified; integrity alert |
| R-07 | Local model gives confidently wrong advice | High | Medium | Accepted | Hypothesis labelling, evidence requirements |
| R-08 | PAM semantic index poisoned | Low | Medium | Accepted | Candidate-only, provenance display, lexical baseline |
| R-09 | Model provider retains prompts | Medium | Medium | Accepted if consented | Consent names retention unknown; local default |
| R-10 | User configures project root = home directory | Medium | High | Accepted with warning | Scope warning, exclusion defaults, read budget |
| R-11 | Windows local actor wins the check/open swap window | Low | High | Accepted, documented | Identity comparison and post-read re-check; POSIX descriptor walk; T2 is Linux-only and unavailable on this Windows host |
| R-12 | Filesystem reports unstable or zero `st_dev`/`st_ino` | Low | Medium | Accepted, documented | Path/identity check fails closed only when values change; registration remains explicit |

## 10. Security Test Plan

| Test | Method | Gate |
|---|---|---|
| Policy matrix | Table-driven unit tests for every capability and resource type | PR |
| Path escape | Fixture with traversal, symlink, junction, UNC, case collision, moved/replaced root, file swap during read | PR |
| M2 walker bounds/exclusions | Synthetic tree with links, reserved/secret dirs, case collisions, file/depth/byte caps, missing/replaced root; gated 50k benchmark | PR; 50k benchmark local/on demand |
| Injection corpus | Synthetic repos with hidden/visible instructions in 10+ locations | PR |
| Approval binding | Mutate each bound field after approval | PR |
| Sandbox limits | CPU, memory, PIDs, output, timeout, process tree kill | Nightly |
| Egress | Fake provider + fake DNS + redirect + rebinding | PR |
| Secret corpus | AWS, GitHub, JWT, private keys, DB URLs, high-entropy noise | PR |
| Audit integrity | Direct SQL mutation, row deletion, reorder | PR |
| Skill abuse | Malicious manifests, install hooks, wildcard permissions | PR |
| Supply chain | Lockfile drift, unpinned action, unknown licence | PR |
| Restore | Backup, corrupt DB, restore, verify chain | Release |

## 11. Assumptions and Open Questions

1. The host OS account is not shared with untrusted users.
2. The user does not deliberately disable security controls without understanding the warning.
3. Docker/Podman, when used, is correctly configured by the user; EIS verifies basics but not the daemon.
4. PAM is a local, trusted service; its own threat model is out of scope, but EIS treats its output as untrusted data.
5. Model provider terms and retention are the user's responsibility, surfaced at consent time.

Open question Q-01: whether to implement Windows restricted-token/Job Object hardening as a T1.5 tier. Recommended: defer; require T2 for untrusted workloads and document the limitation.

## 12. Acceptance Criteria

1. Every asset has at least one control and one test.
2. Every STRIDE row has a named mitigation; unmitigated rows are in the residual register.
3. Attack trees AT-1..AT-3 are covered by adversarial tests.
4. The abuse-case table is executable as a test suite.
5. The threat model is re-reviewed at every milestone gate and the review is recorded in the audit log.
