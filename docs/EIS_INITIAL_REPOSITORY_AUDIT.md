# EIS Initial Repository Audit

Status: Complete for the audit scope (pre-M0.1)
Audit date: 2026-11-20
Auditor role: Principal Architect / Technical Lead
Scope: `<EIS_ROOT>` only. No file outside this directory was modified. Absolute paths are redacted in this document per the project's own privacy policy; the owner has the resolved path in the audit record.
Related: [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md), [EIS_IMPLEMENTATION_ROADMAP.md](EIS_IMPLEMENTATION_ROADMAP.md), [PUBLIC_RELEASE_CHECKLIST.md](../PUBLIC_RELEASE_CHECKLIST.md)

---

## 1. Method and Evidence

| Check | Command / action | Result |
|---|---|---|
| Recursive content listing | `Get-ChildItem -Force -Recurse` | Exactly one file |
| Git work tree | `git rev-parse --is-inside-work-tree` | `fatal: not a git repository` |
| Git status / branch | `git status --porcelain=v1 -b` | Failed: no repository |
| Git remotes | `git remote -v` | Failed: no repository |
| Parent directory context | `Get-ChildItem <projects-root>` (names only) | 20 sibling project directories and 4 archives |
| Toolchain probe | `python/node/npm/git/docker/ollama/podman --version` | See §3 |
| PAM integration feasibility | Top-level directory names of the sibling PAM project only; no source read, no code copied | PAM is a standalone Python application with its own `.git`, `app/`, `docs/`, `vault/`, `.obsidian/` |

No application code, tests, configuration, schemas or CI were found, therefore none were executed. This audit makes **no claim that any test passed**.

## 2. Current Repository State

| Attribute | Observed value |
|---|---|
| Path | `<EIS_ROOT>` |
| Git repository | **No** |
| Branch | None |
| Remotes | None |
| Commits | None |
| Tracked files | None |
| Files on disk | 1 |
| Total size | 35,067 bytes |
| Hidden files / directories | None |
| Submodules | None (no `.gitmodules`) |
| CI configuration | None |
| Dependency manifests | None |
| Virtual environments | None inside EIS |

The only file is `EIS_Complete_Project_Vision_and_Technical_Plan.txt` (804 lines, vision v1.0, status "Consolidated planning baseline").

## 3. Environment (host, not project configuration)

| Tool | Version | Implication |
|---|---|---|
| Python (default) | 3.14.6 | Too new to assume universal wheel availability for the full stack |
| Python (installed) | 3.13, 3.12.13 (uv-managed) | 3.12 available and suitable as the pinned baseline |
| Node.js | 24.18.0 | Current; suitable for Vite/React |
| npm | 12.0.1 | Suitable |
| Git | 2.55.0.windows.2 | Suitable |
| Docker | 29.9.1 | T2 isolation available |
| Podman | Not installed | Docker is the only current T2 backend |
| Ollama | 0.40.16 | Local model provider available |

## 4. Existing Implementation

| Area | State |
|---|---|
| Product requirements | Present as a single narrative document, not decomposed into testable requirements |
| Application code | None |
| API | None |
| Agent runtime | None |
| Memory / database | None |
| Skills | None |
| Security controls | None |
| Tests | None |
| Documentation | Vision only; no README, architecture, threat model or contributor docs |
| Packaging / install | None |
| CI/CD | None |
| Licence | None |

## 5. Missing Components (against the vision)

Everything from M1 onward is missing. The material gaps that block safe implementation are:

1. **No repository boundary.** The project is an ordinary folder inside a directory containing private repositories and archives. There is no `.gitignore`, no local exclusion rules and no automated check that would stop an accidental `git init` at `<projects-root>`.
2. **No data classification or runtime-data location.** The vision requires runtime data outside the repository but does not fix a platform-aware path or a classification scheme.
3. **No requirements traceability.** Vision objectives are not mapped to requirement IDs, acceptance criteria or tests.
4. **No security model as code.** The permission categories in vision §18 are prose; there is no capability vocabulary, resource-scoping syntax or approval-binding rule.
5. **No agent runtime contract.** No session, task, delegation or tool-call schema exists.
6. **No skill package contract.** `SKILL.md` is named but EIS-specific manifest, permission and evaluation fields are undefined.
7. **No persistence schema.** Incident, finding, run, approval and evidence records are described but not modelled.
8. **No API contract.** Interface domains are listed but no resources, versioning or error model exist.
9. **No release or privacy gates.** No secret scanning, history scanning, export preview or public-release checklist.
10. **No PAM adapter contract.** The vision mandates an adapter but PAM's current transport surface is unconfirmed; EIS must not import PAM internals.

## 6. Architectural Problems and Risks

| ID | Problem | Severity | Consequence |
|---|---|---|---|
| A-01 | Vision document sits at the repository root and names private projects and prior internal work | Medium | Would be published verbatim if the repo is initialised here without review |
| A-02 | Parent `<projects-root>` contains private repositories and archives, including a `.zip` of PAM | High | A mistaken `git init` one level up would create a catastrophic privacy boundary violation |
| A-03 | Default Python is 3.14 while many ecosystem packages still lag | Medium | Reproducibility and CI failures if the interpreter is unpinned |
| A-04 | Vision mixes "security foundations from M1" with a discrete M6 security milestone | Medium | Risk that sandboxing is deferred past the point where tools first execute |
| A-05 | Vision proposes `apps/` + `core/` top-level packages without an import/package strategy | Low | Import ambiguity and duplicated packaging metadata if copied literally |
| A-06 | No decision on how PAM is reached (HTTP, CLI, library) | Medium | Adapter could accidentally couple EIS to PAM internals |
| A-07 | No decision on Obsidian write scope | Medium | EIS could overwrite human-authored notes |
| A-08 | No decision on Windows sandbox strength | High | A subprocess "sandbox" on Windows is not a security boundary; untrusted skills could execute with user rights |
| A-09 | No decision on local API authentication | High | A loopback web app is reachable by any local process and by malicious web pages via DNS rebinding |
| A-10 | "Confidence" is described as evidence-based but no formula exists | Low | LLM-invented percentages could leak into reports |

No code-level architectural conflicts exist because there is no code.

## 7. Conflicts Between Existing Content and the Product Vision

| ID | Conflict | Resolution |
|---|---|---|
| C-01 | Vision §25 proposes `apps/` and `core/` at repository root; a single installable Python package is simpler | Refined to `src/eis/` with `api`, `cli`, `worker`, `core`, `modules`, `adapters` subpackages. Recorded as ADR-002; module names preserved |
| C-02 | Vision §31 places "Security and Execution" at M6 but requires security foundations from M1 | Split: policy engine, capability model, audit chain, execution gateway and approval primitives land in M1; sandbox hardening, network control and recovery land in M6. Recorded as ADR-009 and in the roadmap |
| C-03 | Vision proposes Apache-2.0 "subject to final review" while §29 expects a `LICENSE` file | No `LICENSE` is created in M0.1. A recommendation document is provided instead. Recorded as ADR-021 |
| C-04 | Vision requires "automatic skill improvement" but also human approval for executable changes | Resolved by a trust and change-class matrix: declarative content changes may auto-activate; any new executable code, permission change, network use or security-policy change requires human approval. See [EIS_SKILL_EVOLUTION.md](EIS_SKILL_EVOLUTION.md) |
| C-05 | Vision says "all projects under a configured Projects root" while privacy rules forbid committing personal paths | The root is runtime configuration stored outside the repository; committed code contains no absolute paths and tests use a temporary synthetic root |

## 8. Security Concerns

| ID | Concern | Required control |
|---|---|---|
| S-01 | Accidental publication of sibling projects | Separate repo, `.gitignore` deny-by-default, pre-commit path guard, history secret scan before first push |
| S-02 | Secret leakage into SQLite, logs, artifacts or model prompts | Secret scanning at ingest, redaction before persistence and egress, C3 classification never stored |
| S-03 | Prompt injection from repository files | Repository content is untrusted data; tool authority is deterministic and outside the model |
| S-04 | Malicious community skill | Declarative-only by default; executable skills require validation, sandboxing and approval |
| S-05 | Path traversal outside registered roots | Resource resolution by project ID + relative path; canonicalisation and containment check in the gateway |
| S-06 | Unrestricted command execution | Command profiles with allowlists, no shell interpolation, per-run limits |
| S-07 | Silent data egress to a cloud model | Local-only default; provider consent record; egress policy per call; redaction; test |
| S-08 | Tampering with audit/evidence | Hash-chained append-only audit events; read-only artifact store; integrity hashes on runs |
| S-09 | Local web attack surface | Loopback bind, bearer token, Host/Origin validation, no CORS by default |
| S-10 | Dependency compromise | Lockfiles, pinned versions, OSV/pip-audit/npm audit in CI, SBOM on release |

## 9. Recommendations

Ordered by risk reduction per unit of effort:

1. **Create the repository boundary before any code.** `git init` in the EIS directory only, commit `.gitignore` and the privacy guard first. This is a consequential action and requires explicit approval (see §10).
2. **Adopt the M0 package as the baseline.** Requirements, capabilities, trust boundaries and acceptance criteria must exist before runtime code.
3. **Pin Python 3.12** via `requires-python = ">=3.11,<3.13"` and a committed lockfile (ADR-032); 3.13/3.14 support requires a later compatibility task and evidence.
4. **Treat the vision text as a planning source, not a public artifact.** Move it to `docs/planning/` and require privacy review before publication, or keep it untracked.
5. **Define PAM as a versioned HTTP/JSON contract owned by EIS**, with a null adapter fallback. Do not inspect or import PAM internals during M1-M3.
6. **Make the Obsidian adapter write only inside a managed subfolder** with generated-file markers.
7. **Require T2 container isolation for any untrusted executable skill on Windows**, or disable execution. Do not describe T1 as a sandbox on Windows.
8. **Add the privacy regression suite in M1, not M11**, because every later module increases leak surface.

## 10. Actions Requiring Explicit Approval

| Action | Why approval is required |
|---|---|
| `git init` and first commit | Creates the public-boundary repository; user prohibited unapproved commits |
| Adding a remote or pushing | Explicitly prohibited without authorisation |
| Moving the vision `.txt` into `docs/planning/` | Alters existing user file location |
| Creating `LICENSE` | Licence choice is not final |
| Installing dependencies or creating a virtual environment | Prohibited in this task |
| Reading PAM source beyond top-level names | Crosses a private project boundary |

## 11. Audit Verdict

The directory is a clean, empty project folder with a single planning document. There is **no legacy implementation to preserve, migrate or reconcile**. The main risks are procedural and privacy-related, not code-related: the absence of a repository boundary inside a directory full of private projects, and the absence of a deterministic permission model before the first tool executes.

M0.1 (specification package) can proceed safely. M0.2 (repository bootstrap: Git init, packaging, CI, schemas as code, baseline tests) must not begin until the user approves the actions in §10.

## 12. Post-Audit State (M0.1 Writes)

The audit above is a snapshot taken **before** any M0.1 file was created. This section records what changed during the M0.1 documentation delivery, for transparency.

| Added | Type | Notes |
|---|---|---|
| `docs/EIS_*.md` (19 documents) | Specification | Product, architecture, runtime, memory, skills, security, threat, privacy, data, API, execution, governance, research, roadmap, ADRs, audit |
| `docs/INITIAL_REPOSITORY_AUDIT.md` | Pointer | Redirects to the canonical audit |
| `README.md` | Documentation | Status and document index |
| `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, `CHANGELOG.md` | Governance | Community baseline |
| `LICENSE_RECOMMENDATION.md` | Legal decision | Apache-2.0 adopted 2026-11-20; `LICENSE` and `NOTICE` present |
| `PUBLIC_RELEASE_CHECKLIST.md` | Release gate | Privacy and supply-chain checks |
| `.gitignore` | Privacy hygiene | Deny-by-default baseline; not a security boundary |

Unchanged: the vision `.txt` remains at the repository root and was not moved, edited or deleted. No application code, tests, dependencies, Git repository, remote, commit or push was created. No file outside `<EIS_ROOT>` was modified. The actions in §10 remain open and require explicit approval.

## 13. M0.1 Verification Performed

These are documentation checks, not application tests. No application test suite exists, and none is claimed to have run.

| Check | Method | Result |
|---|---|---|
| Required files present | Script compares the 19 requested documents and 7 governance files against disk | All present |
| Markdown links resolve | Relative-link extraction across 28 Markdown files | 0 broken links |
| Personal/absolute paths in docs | Pattern scan for drive letters, home directories, usernames | 0 matches after redaction |
| Placeholder markers | Scan for `TODO`, `TBD`, `FIXME`, lorem ipsum | 0 matches |
| Git state | `git rev-parse`, `git status`, `git remote -v` | Not a repository; no branch, commits or remotes |
| Outside modifications | All writes confined to `<EIS_ROOT>` | Confirmed by the operations performed |

Limitations: link checking does not validate anchor fragments, and the consistency review is manual. No runtime, security or privacy test has been executed because no runtime exists yet.