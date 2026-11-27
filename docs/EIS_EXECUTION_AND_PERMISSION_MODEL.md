# EIS Execution and Permission Model

Version: 1.0 (M0 baseline)
Status: Baseline and normative
Related: [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md), [EIS_AGENT_RUNTIME_DESIGN.md](EIS_AGENT_RUNTIME_DESIGN.md), [EIS_THREAT_MODEL.md](EIS_THREAT_MODEL.md), [EIS_SKILL_SYSTEM.md](EIS_SKILL_SYSTEM.md)

---

## 1. Purpose

This document is the normative definition of authority in EIS. If code and this document disagree, the code is wrong. Every capability, resource scope, approval rule and isolation tier is defined here.

> **Implementation status (M1 partial):** typed policy decisions, chained persistence, digest-bound A3 approvals, restricted project writes, durable project roots and explicit read-only tools are implemented. Strict bounded task/plan envelopes expose four read-only tools; a plan may end with one task-bound `project.propose_write`. That step only creates a request: the model cannot grant or execute. Task, plan and proposal step enter approval-wait atomically; successful execution evidence is required to complete them, and rejection fails them atomically. Plan recovery is explicit and limited to interrupted read-only work after policy/session/root rechecks. Provider egress redaction uses credential patterns as defense in depth. T1 is not a Windows security boundary; T2 is fail-closed but has not been verified on a real Linux host. Authenticated runtime API, delegation, human-review continuation, broader approvals, comprehensive redaction and external audit anchoring remain incomplete. See ADR-038 through ADR-046 in [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md).

## 2. Principals

| Principal | Description | Can hold capabilities |
|---|---|---|
| `user` | The human at the machine | All, including reserved |
| `agent:<role>` | Primary or specialist runtime role | Subset by role ceiling |
| `skill:<name>@<version>` | A validated skill during invocation | Declared ∩ approved ∩ invoking agent ceiling |
| `job:<id>` | An automation job | Declared ∩ approved |
| `system` | Startup, migration, integrity | Internal maintenance only |

A principal is resolved from the authenticated request, never from model output. There is exactly one human principal in v1 (ADR-031).

**User-delegated writes.** Capabilities marked *user-delegated only* are not present in any agent or skill ceiling. An agent generates a proposal under `project.propose`; the primary agent requests approval (`approval.request`); on approval the gateway executes the exact bound action under `principal=user` with `on_behalf_of=<agent>` recorded on the decision and tool call. Approval never adds a capability to an agent — it authorises the user's own capability for one bound action.

## 3. Capabilities

Capabilities are strings `domain.action`, granted per principal with a resource pattern. Grants are concrete; wildcards are not permitted in stored grants, only in policy rule matching.

| Capability | Allowed action | Resource scope | Approval |
|---|---|---|---|
| `project.list` | Enumerate registered projects | none | No |
| `project.read` | Read files and metadata | `project:<id>` + relative path | No |
| `project.profile` | Run analysis/detection | `project:<id>` | No |
| `project.verify` | Run allowlisted verification profile | `project:<id>` + `profile:<id>` | No (first use may prompt) |
| `task.run` | Run/continue a durable task or human-reviewed plan | `project:<id>` or `global` | No |
| `task.cancel` | Cancel task/plan/steps atomically | `project:<id>` or `global` | No |
| `project.propose` | Generate a patch or diff without applying it | `project:<id>` | No |
| `project.write` | Apply an approved patch (**user-delegated only**) | `project:<id>` + diff | **A3** |
| `project.delete` | Remove project data (**user-delegated only**) | `project:<id>` | **A4** |
| `git.read` | Status, log, diff, show | `project:<id>` | No |
| `git.write` | Commit, branch, stage (**user-delegated only**) | `project:<id>` | **A3** |
| `git.push` | Push to a configured remote (**user-delegated only**) | `project:<id>` + `remote:<name>` | **A4** |
| `security.scan` | Run a scanner profile | `project:<id>` + `scanner:<id>` | No |
| `knowledge.read` | Retrieve incidents and findings | `project:<id>` or `global` | No |
| `knowledge.write` | Propose records | `project:<id>` | No for `hypothesised`; A3 to change `verified` |
| `skill.invoke` | Execute an active skill | `skill:<name>@<version>` | Per skill risk |
| `skill.install` | Install/activate a skill (**user-delegated only**) | `skill:<name>` | **A5** for executable |
| `skill.revoke` | Disable a skill version (**user-delegated only**) | `skill:<name>` | **A3** |
| `skill.evolve` | Create a candidate | `skill:<name>` | No |
| `tool.invoke` | Call a registered tool | `tool:<name>@<version>` | Per tool |
| `net.egress` | Reach a destination | `host:<scheme://host:port>` | **A4** unless loopback local |
| `artifact.read` | Read evidence | `artifact:<id>` + class ceiling | No if class ≤ granted |
| `artifact.export` | Export evidence (**user-delegated only**) | `artifact:<id>` | **A4** |
| `report.generate` | Create a report | `project:<id>` | No |
| `automation.manage` | Create/enable jobs | `job:<id>` | **A5** |
| `approval.request` | Ask the user | none | n/a |
| `policy.read` | Read policy and decisions | none | No |
| `audit.read` | Read audit events | none | No |
| `secret.read` | **Reserved** — never granted | n/a | n/a |
| `policy.write` | **Reserved** — human only | n/a | n/a |
| `approval.grant` | **Reserved** — human only | n/a | n/a |
| `skill.grant_capability` | **Reserved** — human only | n/a | n/a |
| `diagnostics.run` | Health and self-test | none | No |

Reserved capabilities are not representable in an agent, skill or job grant. The policy schema rejects them at load time.

## 4. Resource Descriptors

Every action resolves to one or more typed resources:

```python
class Resource(BaseModel):
    kind: Literal["project", "path", "command", "host", "skill", "artifact", "record"]
    project_id: str | None
    path: str | None            # project-relative, POSIX separators
    command_profile: str | None
    host: str | None            # scheme://host:port
    skill: str | None           # name@version
    record_id: str | None
```

Resolution rules:

- `path` is always project-relative and normalised (`..` removed, NFC, case-folded for comparison on Windows).
- The root is selected by the session's project ID through the durable registry; neither the model nor the tool-call JSON supplies a root path.
- The gateway verifies the registered root's canonical path and filesystem identity, then enforces containment **without following links**. A missing, moved or replaced root is denied, even if the capability exists.
- POSIX access walks from a verified root descriptor with `O_NOFOLLOW` and `dir_fd`; Windows checks each component and the opened handle identity, with a documented residual swap window.
- A resource not resolvable to a registered root is denied, even if the capability exists.
- Commands resolve through a profile ID; raw command strings are never a resource.
- Hosts require an explicit scheme, host and port; IP literals are rejected except loopback.

### Root registration

`eis project register <project_id> <root>` is the only way to add a root. It rejects missing paths, files, filesystem roots, runtime data/config locations, duplicate or overlapping roots, and any path that resolves through a symlink or junction. `eis project remove` deletes the registration, not any files. Registration and removal append content-free chain events; the absolute path is stored only in the local `project_roots` row and never in audit or default CLI output.

## 5. Policy Evaluation Algorithm

```python
def evaluate(principal, action, resources, context) -> Decision:
    if principal.disabled: return DENY("principal_disabled")
    tool = registry.get(action.tool, action.version)          # unknown -> DENY
    required = tool.capabilities                              # never empty
    if not principal.ceiling.contains(required): return DENY("role_ceiling")
    if principal.kind == "skill" and not principal.grant.contains(required):
        return DENY("skill_permission_missing")
    for r in resources:
        if not scope_allows(principal, r): return DENY("resource_out_of_scope")
    rules = policy.versioned_rules_for(action.tool)
    for rule in rules.deny:                                   # deny first
        if rule.matches(principal, resources, context): return DENY(rule.id)
    for rule in rules.allow:
        if rule.matches(principal, resources, context):
            decision = ALLOW with obligations(rule)
            if rule.approval_class: return REQUIRE_APPROVAL(rule, decision)
            return decision
    return DENY("default_deny")
```

Properties:

- Pure: same inputs and policy version produce the same decision.
- Deny rules are evaluated before allow rules.
- No rule can match a reserved capability.
- Obligations (limits, redaction, isolation tier, expiry) travel with the decision and are enforced by the gateway.
- `inputs_hash = SHA-256(canonical(principal, action, resources, context_subset, policy_version))` is persisted.
- Policy files are validated at startup; invalid policy fails closed.

### 5.1 Decision record

```json
{
  "decision_id": "pol_01J...",
  "principal": "agent:eis.debugging",
  "action": "project.write",
  "resources": [{"kind": "project", "project_id": "prj_01J...", "path": "src/db/migrate.py"}],
  "decision": "require_approval",
  "rule_id": "write-source-requires-approval",
  "approval_class": "A3",
  "policy_version": "2026-11-20.1",
  "inputs_hash": "sha256:...",
  "obligations": {"isolation": "T1", "output_cap_bytes": 2097152, "expires_in": 1800}
}
```

## 6. Approval Model

### 6.1 Classes

| Class | Meaning | Examples | Confirmation |
|---|---|---|---|
| A1 | Safe read/analysis | Read file, list projects | None |
| A2 | Propose only | Generate a patch, draft a report | None to generate; A3 to apply |
| A3 | Modify | Apply patch, commit, change verified state | Single approval, diff-bound |
| A4 | Destructive or external | Delete, push, export C2, new network destination | Approval + typed confirmation |
| A5 | Security-relevant | Executable skill, new permission, policy change, automation | Approval + second confirmation + reason |

### 6.2 Binding

```
action_digest = SHA-256(canonical_json({
  action_type, tool, tool_version, arguments,
  resources, diff_hash, policy_version, principal, expires_at
}))
```

- An approval row stores the digest, a human summary, the diff artifact, risk labels, expiry (default 30 min; A4/A5 10 min) and the deciding actor.
- Consumption is atomic: `UPDATE approvals SET status='consumed' WHERE id=? AND action_digest=? AND status='approved' AND expires_at>now()`.
- Zero rows updated means the approval is invalid, expired or already used; the action is denied.
- Any change to arguments, resources or diff invalidates the digest and requires a new approval.
- Rejecting an approval records a reason and terminates the task branch.
- Approval never changes the isolation tier or bypasses limits.

**Current A3 implementation.** The shipped approval binds action, actor, resource, project-root hash, original/new content hashes, diff hash, summary hash, policy decision/version and expiry. Diff and new content are stored locally in the approval row; the audit chain receives hashes and content-free metadata only. A rejection note stays local and its SHA-256 hash enters the chain. Expiry is enforced on grant and consumption; expired rows remain `pending` but are hidden from the pending list until an expiry transition is added.

### 6.3 Presentation requirements

The approval UI/CLI must show: action, exact target paths, a diff (for writes), data classes leaving the machine (for egress), reversibility, backup status, risk labels, expiry and the consequence of denial. Truncated diffs are not approvable.

## 7. Isolation Tiers

| Tier | Requirements | Used for | Enforcement |
|---|---|---|---|
| T0 | Pure function, no I/O, bounded CPU | Parsing, scoring, formatting | In-process, no adapters |
| T1 | Subprocess, explicit cwd, env allowlist, no shell, timeout, output cap, process-group kill | Trusted scanners, project tests | `asyncio.create_subprocess_exec` with limits |
| T2 | Linux container: non-root, read-only rootfs and filtered project copy, no network, CPU/mem/PID caps, dropped capabilities, no-new-privileges, explicit seccomp, immutable image digest, disposable workspace and forced cleanup | Untrusted skills, untrusted projects, risky builds | Docker/Podman with a fixed, probed profile (ADR-044) |

Rules:

- A tool declares its minimum tier; the gateway may raise, never lower it.
- T1 on Windows is **not** a security boundary (ADR-009); the UI states this where relevant.
- T2 requires a container backend that has demonstrated every required protection; absence or partial configuration means the operation is refused with `isolation_unavailable`. On Windows the Linux-container probe cannot demonstrate these protections, so T2 remains unavailable.
- T2 mounts only a filtered disposable copy of the registered project, read-only by default; profiles needing writes mount that copy read-write and it is discarded after the run. `.git`, `.eis`, backups, secret files and runtime database files are excluded.
- Environment for T1/T2 is an application-declared allowlist of non-secret keys with capped values. `HOME`, cloud credentials, SSH agent and tokens are removed; no model-supplied key is accepted.
- T2 containers always run with `--network=none`; this slice grants no destinations. A T4 network grant would require a future ADR.

**Implementation status.** `ExecutionGateway` routes T2 profiles and any profile run against an untrusted project through `SandboxBackend` only after `project.verify` policy evaluation and audit-integrity checks. The Linux backend builds a fixed argv with no image, mount, executable, argument or isolation setting taken from model output; the image is an application-supplied immutable digest and a seccomp profile path is mandatory. Profile fields (`minimum_tier`, `needs_writable_workspace`, `env_allowlist`) are application-defined and validated at construction.

## 8. Command Profiles

A command profile is the only way to execute a binary. It is versioned and reviewed like code.

```yaml
id: pytest.default
version: 1
executable: "<detected-venv>/Scripts/pytest.exe"
args:
  allow: ["-q", "-x", "--json-report", "--cov", "tests/", "src/"]
  deny: ["--pdb", "-s", "--capture=no"]
cwd: project_root
env:
  allow: ["PYTHONPATH", "EIS_TEST_*"]
  set: {"PYTHONDONTWRITEBYTECODE": "1"}
timeout_seconds: 900
tier: T1
output_cap_bytes: 2097152
network: none
```

Rules:

- Executables are absolute and verified by hash when the profile declares one.
- Arguments are passed as an array; no shell, no string concatenation, no glob expansion by EIS.
- User-supplied test filters are validated against a restricted grammar and length.
- A profile cannot be defined by a skill or a model; only built-in or user-approved profiles.
- Missing executable → `unavailable`; non-zero exit is a result, not an exception.
- Output is captured to an artifact; only a summary enters the model context.

Baseline profiles: `pytest`, `ruff`, `mypy`, `coverage`, `npm test`, `vitest`, `eslint`, `tsc`, `flutter test`, `flutter analyze`, `mvn test`, `gradle test`, `gitleaks`, `semgrep`, `osv-scanner`, `trivy fs`, `docker build`.

## 9. Filesystem Policy

| Operation | Rule |
|---|---|
| Read project file | Within registered root; excludes `.git/` internals except via Git adapter; excludes secret-like files by default; rejects symlink/junction components rather than following them |
| Read runtime artifact | By ID; classification ≤ principal ceiling |
| Write patch | Only through a workspace copy or an approved diff; backup created first |
| Write report | Runtime artifacts directory only |
| Write Obsidian | Only `<vault>/EIS/`; generated marker required |
| Delete | A4; two-step (soft delete, then purge); never recursive by default |
| Traverse symlink/junction | Denied for read-only project tools; no link component is followed |
| Access parent of root | Denied |
| Access home/SSH/cloud config | Denied by default; not in any allowlist |

## 10. Network Policy

- Default deny, including DNS.
- Allowed: loopback model endpoint and loopback PAM, when configured.
- `net.egress` grants are per `scheme://host:port`; TLS required for non-loopback.
- Redirects are re-evaluated per hop; cross-host redirects need a grant for the new host.
- IP literals are rejected (except loopback) to reduce rebinding and SSRF.
- Request size, response size, timeout and redirect count are capped.
- Proxy environment variables are ignored unless explicitly configured.

## 11. Git Operations

| Operation | Class | Notes |
|---|---|---|
| status, log, diff, show, rev-parse | A1 | Read-only |
| fetch | A4 | Network; credentials from env/keyring |
| stage, commit | A3 | Diff-bound; hook execution disabled (`--no-verify` only with explicit consent) |
| branch, tag | A3 | Local |
| push | A4 | Per remote; force push requires A4 + typed confirmation |
| history rewrite, reset --hard, clean -fd | A4 | Backup required; forbidden in automation |

Git remotes may embed credentials; EIS sanitises remote URLs before storing or displaying them and never passes them to a model.

## 12. Resource Limits (enforced by gateway)

| Resource | Default | Hard ceiling |
|---|---|---|
| Wall clock per tool | 300 s | 3600 s |
| CPU per T2 run | 2 cores | 8 cores |
| Memory per T2 run | 2 GB | 8 GB |
| PIDs per T2 run | 256 | 1024 |
| Output bytes | 2 MB | 32 MB |
| Open files (T2) | 1024 | 4096 |
| Disk write per run | 500 MB | 5 GB |
| Concurrent T2 runs | 1 | 4 |

Hard ceilings are compiled into the release; configuration may lower them.

## 13. Failure Semantics

| Condition | Result |
|---|---|
| Capability missing | `policy_denied`, no execution, audit event |
| Approval missing/expired/changed | `approval_required` / `approval_invalid` |
| Resource outside root | `path_outside_root` |
| Unknown tool/version | `unknown_tool` |
| Isolation unavailable | `isolation_unavailable` |
| Timeout | `tool_timeout`, process tree killed |
| Output cap exceeded | `output_truncated`, artifact capped |
| Policy invalid at startup | fail closed, `policy_invalid` |

## 14. Worked Examples

**Example 1 — Apply a fix.**
Principal `agent:eis.debugging` proposes a patch (`project.propose`) for `src/db/migrate.py` with diff hash `d1`. Policy denies `project.write` for the debugging ceiling, as designed. The primary agent requests approval (A3) with the diff; the user approves digest `d1`. The gateway executes the write under `principal=user`, `on_behalf_of=agent:eis.primary`, consuming the approval atomically, applies the patch through a workspace copy, runs `project.verify`, and records the run. The audit shows the user as actor and the agent as proposer.

**Example 2 — Read an incident from another project.**
`knowledge.read` with scope `global` → allowed. Results include only projects the user has registered. C2 content is shown locally, never sent to a cloud provider without C2 consent.

**Example 3 — A skill wants network access.**
Skill manifest declares `net.egress:https://api.example.com:443`. Install → A5 approval; the grant is recorded on that skill version. Invocation → policy allows only that host, no redirects to other hosts, 10 s timeout, 64 KB response. Absent approval, the skill is installed disabled.

## 15. Acceptance Criteria

1. The policy decision table covers every capability with allow, deny and approval cases.
2. Reserved capabilities cannot be represented in a grant (schema test).
3. Every `tool_calls` row references a `policy_decisions` row; a database assertion enforces it.
4. Approval digest mutation tests fail closed for each bound field.
5. A command containing shell metacharacters never reaches a process as a shell string.
6. T2 profiles are validated (non-root, no network, read-only mounts) before first use.
7. A path escaping the root by any mechanism in the escape corpus is denied.
