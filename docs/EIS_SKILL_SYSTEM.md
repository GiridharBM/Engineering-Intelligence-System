# EIS Skill System

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_SKILL_EVOLUTION.md](EIS_SKILL_EVOLUTION.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md), [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md)

---

## 1. Definition

A skill is a versioned, validated procedure that teaches EIS how to perform an engineering task. A skill is **not** a tool: tools perform side effects under policy; skills describe how to reason and which tools to request. Skills never receive direct authority.

## 2. Package Format

```
skills/<category>/<name>/
  SKILL.md                 # required; Agent Skills-compatible frontmatter + instructions
  manifest.yaml            # required; EIS identity, provenance, risk, compatibility
  permissions.yaml         # required; declared capabilities (may be empty)
  tests/                   # required; at least one validation case
  evaluations/             # required; behavioural and regression cases
  changelog.md             # required; human-readable history
  references/              # optional; documentation loaded on demand
  scripts/                 # optional; executable content (raises trust class)
  assets/                  # optional; templates and static data
```

Rules:

- Directory name equals skill `name`; names are lowercase, hyphenated, ≤ 64 characters.
- Packages are immutable once versioned; updates create a new version directory.
- No symlinks, hard links, device files or paths escaping the package root.
- Total uncompressed size ≤ 25 MB; ≤ 500 files; any single file ≤ 5 MB.
- Executable files are detected by extension and content, not by folder name.

## 3. SKILL.md (Agent Skills compatibility)

```markdown
---
name: root-cause-analysis
description: Investigate a failure to a verified root cause using evidence, prior
  incidents and discriminating checks. Use when a build, test or runtime error
  has an unclear cause.
license: Apache-2.0
compatibility: Requires project.read and project.verify capabilities.
metadata:
  author: eis-maintainers
  version: "2.1.0"
  category: engineering
allowed-tools: Read Grep RunTests
---

# Root Cause Analysis

## When to use
...

## Procedure
1. Restate the symptom and scope.
2. Retrieve incidents with a matching error signature.
3. Compare environment, versions and architecture.
4. Propose the smallest discriminating check.
5. State the root cause as a hypothesis until a check confirms it.
6. Propose a fix and a regression test.

## Output
A structured claim list with evidence references; no unsupported facts.

## Limitations
Does not prove absence of other causes.
```

Compatibility rules:

- `name` and `description` follow the Agent Skills standard; `description` states what the skill does and when to use it.
- Optional standard fields (`license`, `compatibility`, `metadata`, `allowed-tools`) are honoured.
- EIS-specific data (version source, risk, evaluation thresholds, tool bindings) lives in `manifest.yaml`, never in non-standard top-level frontmatter keys.
- `allowed-tools` is advisory. The policy engine remains authoritative.
- Body content uses progressive disclosure: short core procedure; detail in `references/`.

## 4. manifest.yaml

```yaml
schema_version: 1
name: root-cause-analysis
version: 2.1.0
title: Root Cause Analysis
description: Evidence-driven failure investigation with cross-project retrieval.
category: engineering            # engineering|security|testing|architecture|release|knowledge
license: Apache-2.0
authors: ["eis-maintainers"]
source:
  type: builtin                  # builtin|community|private|experimental
  origin: "https://github.com/eis-project/eis"
  commit: "abc1234"
  content_hash: "sha256:..."
created_at: 2026-11-20T00:00:00Z
requires:
  eis: ">=1.0,<2.0"
  python: null
  tools: ["project.read", "knowledge.search", "project.verify"]
  skills: []
risk:
  class: R1                       # R0 instructions | R1 read | R2 propose/write | R3 execute/network
  rationale: Reads project files and runs approved verification profiles.
  network: false
  executable: false
permissions_file: permissions.yaml
evaluation:
  suite: evaluations/v1.yaml
  minimum_pass_rate: 0.90
  regression_suite: evaluations/regression.yaml
  baseline_metrics: {task_success: 0.82, verification_rate: 0.75}
provenance:
  signature: null                 # required for verified-publisher status
  reviewed_by: null
  reviewed_at: null
```

Validation: unknown keys fail; `version` is SemVer; `content_hash` covers the package excluding the manifest hash field itself; `requires.eis` must match the running major version.

## 5. permissions.yaml

```yaml
schema_version: 1
skill: root-cause-analysis
version: 2.1.0
capabilities:
  - capability: project.read
    scope: invoking_project
    justification: Read failing files and configuration.
  - capability: knowledge.read
    scope: global
    justification: Retrieve prior incidents with matching signatures.
  - capability: project.verify
    scope: invoking_project
    profiles: ["pytest.default"]
    justification: Run the smallest discriminating check.
denied:
  - project.write
  - net.egress
  - git.write
limits:
  max_tool_calls: 20
  max_runtime_seconds: 600
  max_tokens: 60000
```

Rules:

- Every capability needs a justification; empty justification is a validation error.
- Wildcards (`*`, `project:*`, `net.egress:*`) are rejected.
- The effective grant is `declared ∩ approved ∩ invoking-agent ceiling`.
- `project.write` requires risk class R2+ and an A3 approval at invocation; skills cannot commit or push.
- `net.egress` requires R3, an explicit host, and A5 at install.
- Limits in the skill may be lower than runtime defaults, never higher.

## 6. Trust Classes

| Class | Source | Installation | Execution | Update |
|---|---|---|---|---|
| Built-in | Shipped with EIS | Enabled | Declarative; T1 for bundled scripts | Release-bound |
| Verified | Curated registry, signed | A5 once | Declarative; T2 for scripts | Signature required |
| Private | User-authored | Enabled after validation | Declarative; T2 for scripts | User approval |
| Community | Public contribution | Disabled by default | Declarative only unless elevated | A5 per version |
| Experimental | Flagged by author | Disabled by default | T2 only; no auto-activation | A5 per version |

"Declarative" means the skill contributes instructions and tool intents only. Any `scripts/` content moves the skill to the executable class, which requires static analysis, T2 execution and explicit approval.

## 7. Lifecycle

```
candidate -> validated -> approved(active) -> deprecated -> revoked
     |            |              |
     |            +--> rejected  +--> disabled (user)
     +--> quarantined (failed integrity/security)
```

| State | Meaning | Retrievable | Executable |
|---|---|---|---|
| `candidate` | Imported, not validated | No | No |
| `validated` | Passed automated validation | Metadata only | No |
| `approved` | Activated by user/policy | Yes | Yes, within grant |
| `disabled` | User-disabled | Metadata | No |
| `deprecated` | Superseded, still usable | Yes (lower rank) | Yes if already approved |
| `revoked` | Security or quality revocation | No | No |
| `quarantined` | Failed integrity/security | No | No |

Transitions are explicit service methods with audit events. A revoked version cannot be re-activated; a fixed version must be installed.

## 8. Discovery and Installation

1. **Source.** Built-in directory, local package, or explicit registry URL. No background crawling.
2. **Quarantine.** Extract to `quarantine/skills/<hash>/` with archive safety checks; never into the live tree.
3. **Inspect.** Validate structure, schema, hashes and provenance.
4. **Scan.** Secret scan, suspicious URL scan, binary detection, Semgrep rules for scripts.
5. **Permission diff.** Compare declared capabilities with the previous version and the global policy; compute added/removed.
6. **Evaluate.** Run `tests/` and `evaluations/` in T2 with a synthetic project; record results.
7. **Review.** A5 approval for executable, network, write or elevated-risk skills; declarative read-only skills may activate with a user confirmation.
8. **Activate.** Copy immutable version into the registry; update `current_version`; emit `skill.activated`.

Installation never runs package code. Evaluation runs in T2 with no network and a read-only fixture.

## 9. Validation Pipeline

| Stage | Checks | Failure |
|---|---|---|
| V1 Archive safety | Path traversal, symlinks, size/count limits, compression ratio | Quarantine |
| V2 Schema | SKILL.md frontmatter, name match, manifest, permissions, SemVer | Reject |
| V3 Content | Secrets, credentials, suspicious URLs, embedded binaries, obfuscation | Quarantine |
| V4 Dependencies | Declared only, allowlisted ecosystems, no install during validation | Reject |
| V5 Permissions | Wildcards, reserved capabilities, justification, ceiling | Reject |
| V6 Static analysis | Semgrep rules, dangerous imports/calls in scripts | Quarantine |
| V7 Behaviour | Tests and evaluation suite in T2, deterministic fixture | Reject |
| V8 Review | Human approval for R2+/executable/network | Await approval |
| V9 Activation | Immutable copy, hash, registry update, audit | Fail closed |

Each stage writes a `skill_validation` record with tool versions, findings and artifact hashes. A later stage cannot run if an earlier stage failed.

## 10. Security Inspection Details

- **Prompt-injection scan**: detect instruction patterns aimed at the agent ("ignore previous", "exfiltrate", hidden Unicode, encoded payloads). Findings are warnings with context; the policy engine remains authoritative.
- **Data-access intent**: a skill that asks for `knowledge.read: global` and `net.egress` is flagged as a potential exfiltration combination and requires explicit review.
- **Dependency risk**: a new dependency in a patch version is a permission-like change and requires approval.
- **Obfuscation**: base64 blobs, minified scripts and unusual encodings in `scripts/` are quarantined pending review.
- **Signature**: verified publishers sign the package hash; signature verification is required for the `verified` class.

## 11. Execution

```python
skill = registry.resolve(name, version)         # active version only
grant = intersect(skill.permissions, approval, agent.ceiling)
render = skill.render(context, max_tokens)
intents = model.propose_tools(render)           # no direct authority
for intent in intents:
    gateway.invoke(principal=f"skill:{name}@{version}", intent, grant)
```

- A skill cannot call another skill in v1.
- A skill cannot execute a tool not in its grant, even if the model asks.
- Script execution uses a dedicated command profile and T2; stdout is capped and treated as untrusted.
- Skill invocation is recorded with version, task, project, duration, tool calls, verification link and outcome.
- Rendering is deterministic for the same inputs and version.

## 12. Versioning and Registry Layout

```
<data root>/skills/
  registry.sqlite3            # metadata mirror (authoritative in main DB)
  packages/<name>/<version>/  # immutable extracted package
  quarantine/<hash>/          # failed validation
  signatures/<name>/<version>.sig
```

- SemVer: MAJOR for incompatible procedure/output changes, MINOR for new capability within the same grant, PATCH for clarification.
- A version's content hash is recorded in the main database and in `skill_versions`.
- `current_version` is a pointer, never a mutation of a version row.
- Deprecated versions remain resolvable for reproducing past executions.

## 13. Performance Tracking

| Metric | Definition | Minimum sample |
|---|---|---|
| Task success | Task completed with required verification | 20 executions |
| Verification rate | Executions with a passing verification run | 20 |
| Human intervention | Approvals or corrections per execution | 20 |
| Regression rate | Post-activation failures attributable to the skill | 10 |
| Duration | Median and p95 wall clock | 20 |
| Coverage | Distinct project types exercised | 5 |

Metrics attach to `skill_version_id` and execution context. A persuasive answer is not success; success requires a defined result and evidence (vision §16). Metrics with fewer than the minimum sample are shown as `insufficient_data`, never as a score.

## 14. Revocation

Triggers: confirmed malicious behaviour, secret leakage, policy bypass, repeated verification failure, licence violation, upstream compromise.

Actions: set version `revoked`, block execution immediately, roll back `current_version` to the last non-revoked version, invalidate cached renders, record an audit event with reason and evidence, notify the user in the UI/CLI.

Revocation does not delete execution history; past runs remain for audit but cannot be replayed.

## 15. Built-in Skill Catalogue (initial)

| Category | Skill | Risk |
|---|---|---|
| Engineering | root-cause-analysis | R1 |
| Engineering | cross-project-debugging | R1 |
| Engineering | dependency-conflict-investigation | R1 |
| Engineering | change-impact-analysis | R1 |
| Engineering | architecture-review | R1 |
| Security | security-audit | R1 |
| Security | owasp-top-10-review | R1 |
| Security | api-security-assessment | R1 |
| Security | secret-detection | R1 |
| Security | dependency-vulnerability-audit | R1 |
| Security | docker-security-review | R1 |
| Security | auth-review | R1 |
| Security | threat-modeling | R1 |
| Testing | test-strategy-generation | R1 |
| Testing | unit-test-analysis | R1 |
| Testing | integration-test-analysis | R1 |
| Testing | regression-testing | R1 |
| Testing | coverage-review | R1 |
| Testing | performance-testing | R2 |
| Testing | failure-investigation | R1 |
| Architecture | dependency-mapping | R1 |
| Architecture | api-contract-review | R1 |
| Architecture | database-design-review | R1 |
| Architecture | technical-debt-analysis | R1 |
| Architecture | adr-generation | R1 |
| Release | production-readiness-assessment | R1 |
| Release | docker-deployment-audit | R1 |
| Release | ci-cd-review | R1 |
| Release | backup-recovery-validation | R1 |
| Release | logging-monitoring-review | R1 |
| Release | rollback-planning | R1 |
| Knowledge | incident-capture | R2 |
| Knowledge | verified-fix-extraction | R2 |
| Knowledge | cross-project-retrieval | R1 |
| Knowledge | documentation-generation | R2 |
| Knowledge | knowledge-quality-review | R1 |

All built-in skills are declarative in v1. A built-in skill with scripts is still subject to the executable class.

## 16. Acceptance Criteria

1. A package with a path traversal, symlink or oversized file is quarantined before extraction completes.
2. A manifest with a wildcard capability or reserved capability is rejected.
3. A skill cannot obtain a capability absent from its approved grant, proven by policy tests.
4. Installation executes no package code, proven by a canary file and process monitor.
5. Evaluation runs in T2 with no network, proven by a connection-attempt test.
6. Revocation blocks execution within one request and rolls back the active pointer.
7. Metrics show `insufficient_data` below the minimum sample.
8. A skill with `project.write` cannot commit; the gateway denies `git.write`.
9. Agent Skills-compatible packages load, with EIS metadata read from the manifest.
10. Every lifecycle transition produces an audit event with actor and reason.
