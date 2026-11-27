# EIS Skill Evolution

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_SKILL_SYSTEM.md](EIS_SKILL_SYSTEM.md), [EIS_EXECUTION_AND_PERMISSION_MODEL.md](EIS_EXECUTION_AND_PERMISSION_MODEL.md), [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md)

---

## 1. Principle

EIS may improve validated skills automatically **only inside the existing approved permission and security boundary**. Capability expansion, executable code, external data sharing and security-policy changes require explicit human approval (vision §15, ADR-014).

Evolution is a controlled release process, not self-modification. Every candidate is a versioned artifact with a diff, evaluations, a decision and a rollback reference.

## 2. What May Change

| Change class | Examples | Permission impact | Activation |
|---|---|---|---|
| **E0 — Editorial** | Clarify steps, fix typos, improve examples, tighten output format | None | Automatic if evaluation non-inferior |
| **E1 — Retrieval/selection** | Better trigger description, improved query hints, reordered diagnostic steps | None | Automatic if evaluation non-inferior |
| **E2 — Behavioural within grant** | Use an already-granted read-only tool in a new step; change check order | None (tool already granted, no write) | Approval by default; automatic only with explicit opt-in |
| **E3 — Grant-adjacent** | Use a newly granted tool, add a dependency, add network destination, add write step | Yes | **Human approval (A5)** |
| **E4 — Executable** | Add or modify `scripts/`, binary, install hook | Yes | **Human approval (A5) + T2** |
| **E5 — Policy/security** | Change approval rules, capability definitions, redaction, limits | Yes | **Human approval + ADR** |

An E0-E2 candidate that accidentally requires a new capability is reclassified, not silently accepted. Classification is computed from the diff and the tool bindings, not declared by the generator.

## 3. Triggers

| Trigger | Signal | Minimum evidence |
|---|---|---|
| Repeated verification failure | Same skill version fails verification in ≥ 3 of 10 executions | Execution records + run IDs |
| Repeated human correction | User edits the skill's output on ≥ 3 occasions | Correction records |
| Missed retrieval | A relevant prior incident was not retrieved in ≥ 3 tasks | Retrieval logs + user confirmation |
| Slow execution | p95 duration > 1.5× baseline for 20 runs | Metrics |
| Explicit user request | User asks to improve a skill | User instruction |
| Regression after activation | Watchdog detects a drop | Metrics |

Triggers without sufficient evidence create an `observation`, not a candidate. The system never fabricates a rationale to justify a change.

## 4. Candidate Generation

Inputs: active version, execution records, evaluation results, correction history, retrieved incidents, user request.

Output: a candidate package containing:

- a unified diff against the active version (instructions, manifest metadata, evaluation cases);
- a change-class justification;
- a permission diff (must be empty for automatic activation);
- a risk statement and expected effect;
- references to the triggering evidence.

Constraints:

- The candidate cannot add tools, capabilities, dependencies, network access or files outside `SKILL.md`, `references/`, `evaluations/` and `changelog.md` for automatic classes.
- The candidate cannot modify the evaluation suite's pass criteria or delete failing cases.
- The candidate cannot modify another skill.
- The candidate cannot change its own activation thresholds.
- Generation is bounded: ≤ 3 candidates per skill per week, ≤ 1 active proposal at a time.

## 5. Evaluation

### 5.1 Suites

| Suite | Purpose | Visibility |
|---|---|---|
| `tests/` | Structural and safety checks | Author-visible |
| `evaluations/` | Behavioural cases with expected properties | Author-visible |
| Held-out set | Unseen cases maintained by EIS | Not author-visible |
| Regression set | Previously failed cases and critical scenarios | Not modifiable by candidate |
| Historical replay | Real recorded tasks, redacted, replayed deterministically | Local only |

### 5.2 Metrics

| Metric | Definition | Gate for auto-activation |
|---|---|---|
| Task success | Completed with required verification | Not worse than active (non-inferiority) |
| Verification rate | Passing verification / executions | ≥ active |
| Critical-case pass | Safety and correctness cases | 100% |
| Human intervention | Corrections per execution | ≤ active + 10% |
| Duration p95 | Wall clock | ≤ active × 1.20 |
| Token cost p95 | Model tokens | ≤ active × 1.25 |
| Retrieval quality | Recall@5 on the skill's cases | ≥ active |
| Policy denials | Unexpected denials | No increase |

### 5.3 Statistical rule

Point estimates are insufficient. For a metric with `n` observations:

- compute a 95% Wilson interval;
- require the lower bound of the candidate's success rate ≥ the active rate − 0.05 (non-inferiority margin);
- require `n ≥ 30` per arm for automatic activation; below that, require human approval.

This prevents "2/2 passed, activate" behaviour.

### 5.4 Critical failures

Any of the following blocks activation regardless of aggregate score: policy bypass, secret exposure, destructive action attempt, fabricated evidence, verification claimed without a run, or a critical-case failure.

## 6. Activation Matrix

| Change class | Permission diff | Eval non-inferior | Critical cases | Human approval | Watchdog |
|---|---|---|---|---|---|
| E0 | none | required | required | No | 20 executions |
| E1 | none | required | required | No | 20 executions |
| E2 | none (read-only tools) | required | required | Yes by default; auto only with `allow_e2_auto = true` | 50 executions |
| E3 | non-empty | required | required | **Yes** | 50 executions |
| E4 | executable | required | required | **Yes + T2** | 50 executions |
| E5 | policy | required | required | **Yes + ADR** | permanent |

Additional gates:

- The skill must have ≥ 20 recorded executions before any automatic activation.
- The user may freeze a skill (`evolution: frozen`), disabling all automatic changes.
- A global kill switch disables evolution entirely; default is enabled only for built-in E0/E1.

## 7. Activation Procedure

1. Persist the candidate and its diff.
2. Run static validation (V1-V6 from the skill system).
3. Run the evaluation suites in T2, deterministic provider.
4. Compute metrics and intervals; store artifacts.
5. Classify the change; check the permission diff.
6. If automatic: record decision, activate, start watchdog.
7. If approval required: create an A5 approval with diff, permissions, eval summary and rollback version.
8. On approval: activate; on rejection: mark `rejected` with reason.
9. Emit `skill.activated` with previous and new version IDs.

Activation is a pointer change; the previous version remains immutable and resolvable.

## 8. Watchdog and Automatic Rollback

After activation, the watchdog observes the next N executions (matrix above) and compares against the active baseline.

| Condition | Action |
|---|---|
| Verification success drops > 10 percentage points | Roll back immediately |
| Any critical failure | Roll back immediately + revoke candidate |
| Policy denial spike > 2× baseline | Roll back and flag for review |
| Human correction rate > 2× baseline | Roll back |
| p95 duration > 2× baseline | Roll back |
| Integrity alert | Roll back all recent activations |

Rollback: set `current_version` to the previous version, mark the candidate `rolled_back`, preserve execution evidence, emit `skill.rolled_back`, and create an evolution observation so the same candidate is not regenerated unchanged.

A rollback is not a deletion; the candidate and its evidence remain for analysis.

## 9. Anti-Gaming Controls

1. **Held-out cases** are stored outside the skill package and are not visible to the generator.
2. **Evaluation suites are versioned**; changing expectations requires a separate approval and is audited.
3. **Historical replay** uses recorded tasks the candidate could not have seen during generation.
4. **Property-based assertions** (for example, "no claim without evidence") resist wording tricks better than exact-match tests.
5. **Mutation checks**: a deliberately degraded candidate must fail the suite; if it passes, the suite is rejected as too weak.
6. **No self-reported scores**: metrics come from run records, not from the skill's output.
7. **Rate limits** prevent iterative probing of held-out cases.

## 10. Human Approval Content

An E3-E5 approval must show: what changes, why (triggering evidence), permission diff, new dependencies or destinations, evaluation summary with intervals, critical-case results, rollback version, and the exact diff. Approval is hash-bound; editing the candidate invalidates it.

## 11. Data and Privacy

- Evolution uses local execution records; no private content leaves the machine.
- Replay uses redacted artifacts; the original is referenced by ID.
- Candidate generation with a cloud model requires C2 consent and is disabled by default.
- Evaluation artifacts are retained under the evidence policy and excluded from public releases.

## 12. Audit Requirements

Every evolution event is recorded: trigger, evidence references, generator (model and version), candidate hash, diff, validation results, evaluation metrics and intervals, classification, permission diff, decision, approver (if any), activation time, watchdog outcome, rollback reason.

The audit chain proves the sequence; the version registry proves the content.

## 13. Failure Handling

| Failure | Behaviour |
|---|---|
| Candidate generation invalid | Discard, record reason |
| Evaluation infrastructure down | No activation; mark `blocked` |
| Metrics insufficient | No automatic activation; optional human review |
| Approval expired | Candidate remains pending until re-requested or discarded |
| Rollback target missing | Disable skill; create incident |
| Watchdog cannot observe | Disable automatic evolution for that skill |

## 14. Configuration

```toml
[skill_evolution]
enabled = true
automatic_classes = ["E0", "E1"]
allow_e2_auto = false                          # explicit opt-in; E2 is approval-gated by default
require_human_for = ["E2", "E3", "E4", "E5"]   # tightening is allowed by config
min_executions_before_auto = 20
min_sample_per_arm = 30
max_candidates_per_skill_per_week = 3
watchdog_executions = { E0 = 20, E1 = 20, E2 = 50, E3 = 50, E4 = 50 }
```

Configuration may tighten; it may not loosen the hard gates in §6 or the critical-failure rule.

## 15. Acceptance Criteria

1. A candidate that adds any capability is classified E3+ and cannot auto-activate.
2. A candidate that adds `scripts/` is classified E4 and requires T2 evaluation plus approval.
3. A deliberately degraded candidate fails the mutation check.
4. Automatic activation is refused with fewer than 30 samples per arm.
5. Any critical failure triggers rollback within one execution.
6. The previous version remains resolvable and restorable after rollback.
7. Every decision has a complete audit trail with hashes.
8. A frozen skill accepts no automatic changes.
9. The kill switch stops all evolution within one scheduling boundary.
10. No evaluation case can be deleted by a candidate.
