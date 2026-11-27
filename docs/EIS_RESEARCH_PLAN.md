# EIS Research Plan

Version: 1.0 (M0 baseline)
Status: Plan; hypotheses are not findings
Working title: *A Privacy-Preserving Cross-Project Engineering Intelligence Framework for Reusable Debugging, Security and Software Verification*
Related: [EIS_PRODUCT_SPEC.md](EIS_PRODUCT_SPEC.md), [EIS_MEMORY_ARCHITECTURE.md](EIS_MEMORY_ARCHITECTURE.md), [EIS_IMPLEMENTATION_ROADMAP.md](EIS_IMPLEMENTATION_ROADMAP.md)

---

## 1. Position

EIS is an engineering system with a research programme attached. This document defines what would be measured, how, and what would count as evidence. It does **not** claim novelty. Novelty must be established by a literature review and by results, not by assertion (vision §30).

## 2. Research Questions

| ID | Question | Primary metric |
|---|---|---|
| RQ1 | Can cross-project retrieval of verified incidents improve debugging outcomes compared with project-local context? | Time to correct fix; fix acceptance |
| RQ2 | Does evidence-linked retrieval improve precision over undifferentiated semantic retrieval? | Applicability precision; Recall@5 |
| RQ3 | Can a local-first architecture reuse engineering knowledge without transmitting repository content? | Privacy leak rate; task success under local-only models |
| RQ4 | Does structured evidence improve the reliability of security and readiness reporting? | Claim-to-evidence coverage; false-pass rate |
| RQ5 | Can skill evolution improve task success without permission expansion or safety regression? | Success delta; policy violations; rollback rate |
| RQ6 | Are deterministic policy controls sufficient to prevent unauthorised agent actions under adversarial repository content? | Unauthorised action rate; approval correctness |

### 2.1 Hypotheses

- H1: EIS cross-project retrieval reduces median time to a correct fix by ≥ 20% versus project-local context on matched tasks.
- H2: Evidence-linked ranking increases applicability precision by ≥ 15 percentage points versus embedding-only ranking.
- H3: A local-only configuration achieves ≥ 80% of the cloud-assisted success rate on the benchmark, with zero unconsented egress.
- H4: Evidence-gated reports reduce false "pass" claims to zero on a seeded-defect benchmark.
- H5: Automatic evolution (E0-E2) yields a non-inferior success rate and zero permission expansions over 100 simulated activations.
- H6: With the policy gateway, adversarial repository content produces zero unauthorised writes across the attack corpus.

These are directional and falsifiable. A negative result is publishable and must be reported.

## 3. Related Work (areas to review, not conclusions)

The literature review must cover, at minimum:

1. **Mining software repositories** — defect prediction, duplicate bug detection, fix patterns, change-impact analysis.
2. **Case-based reasoning and experience reuse** — incident reuse, analogical repair, adaptation.
3. **Retrieval-augmented generation** — dense/sparse retrieval, reranking, provenance, hallucination measurement.
4. **Agent architectures** — tool use, planning, multi-agent delegation, memory, skill libraries.
5. **Agent security** — prompt injection, excessive agency, sandboxing, capability-based security.
6. **Software supply chain and security orchestration** — scanner normalisation, vulnerability triage, false positives.
7. **Reproducibility** — benchmark design, artifact evaluation, threats to validity.
8. **Privacy-preserving ML/IR** — local inference, data minimisation, leakage measurement.

Standards and prior art to position against: Agent Skills (agentskills.io), Model Context Protocol, Hermes Agent (MIT), OWASP LLM Top 10 (2025), OWASP ASVS, SLSA/Sigstore for supply chain.

The review must record search strings, databases, inclusion/exclusion criteria and a PRISMA-style flow if the paper claims a systematic review. A narrative review must be labelled as such.

## 4. Potential Contributions (to be validated)

| ID | Potential contribution | Evidence required |
|---|---|---|
| C1 | Structured retrieval and adaptation of verified incidents across repositories | RQ1/RQ2 results with baselines |
| C2 | Local-first architecture with measured privacy properties | RQ3 leakage and utility results |
| C3 | Unified evidence linking incidents, findings, tests and readiness | RQ4 coverage and false-pass results |
| C4 | Controlled skill evolution with evaluation gates and rollback | RQ5 activation and regression results |
| C5 | Deterministic policy governance for engineering agents | RQ6 adversarial results |

Each contribution is conditional. If the evidence does not support it, the claim is removed from the paper.

## 5. Experimental Design

### 5.1 Conditions

| Condition | Context available | Tools | Permissions |
|---|---|---|---|
| A — Baseline assistant | Task prompt only | Same | Same |
| B — Project-local | Target repository files and history | Same | Same |
| C — EIS cross-project | B + retrieved incidents, solutions, findings, skills | Same | Same |

Controls: same model and version, same temperature and token budget, same task time limit, same tool set, same evaluator, randomised task order, no human hints. Where a condition cannot run locally, the cloud model configuration is recorded and held constant across conditions.

### 5.2 Tasks

- **Debugging:** 40 matched incident pairs across Python, TypeScript, Java, Flutter and containers. Each pair has a source incident (with a verified fix) and a target repository with an analogous but not identical failure.
- **Security:** 30 seeded vulnerabilities across OWASP categories, plus 20 clean controls.
- **Verification:** 25 repositories with known test regressions and 15 with flaky tests.
- **Evolution:** 20 skills × 5 candidate changes, including 5 deliberately degraded candidates.
- **Adversarial:** 60 prompt-injection and policy-bypass cases.

Tasks are authored from synthetic projects and permissively licensed public repositories pinned by commit. Provenance and licence are recorded per fixture.

### 5.3 Procedure

1. Pre-register the protocol, metrics and analysis plan in the repository before running.
2. Freeze the EIS version, skill versions, prompts, model versions and dataset digest.
3. Run each condition 3 times per task (variance from sampling); report all runs.
4. Two independent raters judge fix correctness and applicability; disagreements resolved by a third.
5. Blind raters to condition where feasible (condition labels removed from transcripts).
6. Record wall-clock, token usage, tool calls, approvals and verification outcomes.

## 6. Metrics

### 6.1 Retrieval

| Metric | Definition |
|---|---|
| Recall@K | Relevant source incidents retrieved in top K |
| MRR | Mean reciprocal rank of the first relevant incident |
| nDCG@10 | Graded relevance with applicability levels |
| Applicability precision | Retrieved incidents judged applicable / retrieved |
| Adaptation accuracy | Proposed adaptation judged correct for the target context |

### 6.2 Debugging

| Metric | Definition |
|---|---|
| Time to correct fix | Wall clock until a fix passes the target test suite |
| Fix acceptance | Raters judge the fix correct and minimal |
| Root-cause accuracy | Stated cause matches the seeded cause |
| Regression rate | Passing tests broken by the fix |
| Attempts | Tool/verify cycles to success |

### 6.3 Security and verification

| Metric | Definition |
|---|---|
| Finding precision/recall | Against seeded vulnerabilities |
| False-pass rate | Reports claiming pass where a seeded defect exists |
| Claim-evidence coverage | Claims with a resolvable artifact/run link |
| Remediation verification | Rescans/tests confirming the fix |
| Coverage disclosure | Reports stating untested areas |

### 6.4 Skill evolution

| Metric | Definition |
|---|---|
| Activation success | Activations with non-inferior metrics and no permission delta |
| Regression detection | Degraded candidates correctly blocked |
| Rollback correctness | Regressions rolled back within the watchdog window |
| Permission expansion | Must be zero |
| Cost | Tokens and time per candidate evaluation |

### 6.5 Privacy and policy

| Metric | Definition |
|---|---|
| Leak rate | Private/secret strings found in egress, logs, artifacts or public output |
| Unauthorised action rate | Actions executed without a valid capability/approval |
| Approval correctness | Approvals matching the executed action digest |
| False denial rate | Legitimate actions blocked |
| Local-only utility ratio | Condition C success with local model / with cloud model |

## 7. Analysis Plan

- Report medians and distributions, not only means.
- Paired comparisons use the Wilcoxon signed-rank test; independent comparisons use Mann-Whitney U.
- Effect sizes: Cliff's delta or rank-biserial correlation.
- Confidence intervals via bootstrap (10,000 resamples), reported alongside p-values.
- Multiple comparisons: Holm-Bonferroni within a metric family.
- Missing/failed runs are reported as failures, not excluded, unless a pre-registered exclusion applies.
- Qualitative analysis: thematic coding of failure reasons with a codebook and inter-rater agreement (Cohen's κ).

## 8. Ablations

| Ablation | Question |
|---|---|
| Remove cross-project retrieval | Is the gain from reuse or from more context? |
| Replace evidence ranking with embedding-only | Does provenance improve precision? |
| Remove confidence scoring | Does ECS change ranking or trust calibration? |
| Remove verification gating | How many unverified claims become "verified"? |
| Lexical only vs lexical + PAM | Does semantic retrieval add value beyond FTS5? |
| Disable policy gateway (sandbox only) | How many unauthorised actions occur? |
| Evolution with permission checks disabled | Does the gate prevent escalation? |

## 9. Threats to Validity

| Type | Threat | Mitigation |
|---|---|---|
| Construct | "Fix acceptance" is subjective | Two blind raters, codebook, agreement statistic |
| Internal | Model version drift between runs | Pinned versions, recorded hashes, single run window |
| Internal | Evaluator leakage between conditions | Separate workspaces, no shared cache, blinded transcripts |
| External | Synthetic tasks may not represent real projects | Include pinned public repositories; report composition |
| External | Single developer, single machine | Disclose; invite replication; provide container |
| Conclusion | Small n, multiple metrics | Pre-registration, CIs, effect sizes, correction |
| Privacy | Private data in evaluation | Synthetic/public only; internal runs aggregated and approved |
| Reproduction | Model nondeterminism | Temperature 0 where supported; 3 runs; report variance |

## 10. Reproducibility Package

- Dataset manifest with licences, commits and SHA-256 digests.
- Fixture generator and seeds.
- Prompt templates with hashes.
- EIS version, policy version, skill versions.
- Model identifiers, quantisation and runtime versions.
- Hardware profile and OS.
- Analysis notebooks/scripts producing every table and figure.
- Container image for the harness.
- A `REPRODUCE.md` with exact commands and expected outputs.

The package must not contain private project data, personal paths or credentials.

## 11. Ethics and Privacy

- No human-subject data without informed consent and an ethics review where required.
- No private repository content in public datasets.
- Internal evaluation on private projects is permitted only with the owner's approval and only aggregate results are reported.
- Security experiments are conducted on owned or permitted infrastructure; no scanning of third-party systems.
- Disclosure of vulnerabilities found in public repositories follows responsible disclosure.

## 12. Publication Plan

| Stage | Output |
|---|---|
| M12.1 | Pre-registration and benchmark definition |
| M12.2 | Internal pilot (n=10 tasks) and metric calibration |
| M12.3 | Full experiment run and artifact freeze |
| M12.4 | Paper draft: abstract, intro, related work, architecture, method, results, threats, limitations, conclusion |
| M12.5 | Artifact evaluation submission and replication package |
| M12.6 | Camera-ready with negative results included |

Target venue class: software engineering or usable security/ML venues with artifact tracks. Venue choice follows the results, not the reverse.

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Benchmark too easy or too synthetic | No measurable effect | Pilot, difficulty calibration, public repo tasks |
| Model capability ceiling | Local model fails everything | Report local-only ratio as a finding, not a failure |
| Rater fatigue | Noisy labels | Bounded task count, training, rotation |
| Scope creep | No publication | Freeze protocol at pre-registration |
| Privacy incident during evaluation | Serious | Synthetic-first, redaction, no private data in artifacts |

## 14. Acceptance Criteria

1. Literature review recorded with search strategy and inclusion criteria.
2. RQs mapped to metrics, tasks and analysis methods.
3. Pre-registration committed before the full run.
4. Baselines A/B/C executed with identical controls.
5. Every table reproducible from the artifact package.
6. Privacy review of the dataset and results completed.
7. Negative or null results reported, not suppressed.
8. No novelty claim appears without supporting evidence.
