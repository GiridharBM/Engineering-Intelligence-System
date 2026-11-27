# Engineering Intelligence System (EIS)

**Status: M0.2 — foundation implemented locally. M0.2 acceptance is pending CI and clean-clone evidence. M1 is in progress.**

EIS is a local-first, privacy-preserving, open-source engineering agent. It helps a developer understand, maintain, secure, test and prepare multiple software projects for deployment, and it reuses **verified** engineering experience across projects instead of re-solving the same class of problem.

> Become more capable through verified experience, not more powerful through unrestricted permissions.

## What EIS is

- A hybrid multi-agent system: one primary agent plus six specialist roles (Debugging, Security, Testing, Architecture, Release, Knowledge).
- A cross-project engineering memory: incidents, root causes, solutions, findings and verification evidence, linked by provenance.
- A secure skill platform: built-in, private, community and experimental skills, with validation, versioning, evaluation and rollback.
- A deterministic security layer: capability-based policy, approval-bound modifications, sandboxed execution and a tamper-evident audit chain.
- Local-first: no provider enabled by default, five explicit external provider adapters, Ollama optional, no telemetry, runtime data outside the repository.

## What EIS is not

A chatbot, a project tracker, a scanner wrapper, a general computer-use agent, or a system that deploys to production without explicit approval.

## Repository status

This repository contains the M0 specification package, the M0.2 bootstrap foundation and the first M1 policy slice:

| Area | State |
|---|---|
| Product, architecture, security, privacy, data, API specs | Complete (`docs/`) |
| Governance files | Complete |
| Application code | M0.2 skeleton plus policy/provider/storage/audit, digest-bound approvals, bounded execution, durable session/task and project-root registry, strict read-only model loop and plans, explicit interrupted-plan recovery, fail-closed T2 Linux backend, and task-bound model write proposals that stop for human approval and complete only with execution evidence |
| Tests and CI | 292 tests passing locally (1 POSIX-only test skipped on Windows); clean-copy install verified offline on Python 3.11 and 3.12; CI configured but not yet executed |
| Licence | Apache-2.0 adopted; `LICENSE` and `NOTICE` present |
| Git repository | Initialised locally; no remote and no commits |

M0.2 is **not** marked complete: GitHub-hosted CI execution and branch protection remain outstanding (no remote exists). A clean-copy install and fresh-wheel verification are recorded locally in [docs/M0.2_DECISIONS.md](docs/M0.2_DECISIONS.md). M1 is **not** complete: model plans can read through four registered tools and end with one task-bound write proposal, but every write stops for explicit human approval; delegation, human-review continuation, broader API resources, broad recovery/limits, comprehensive redaction and external audit anchoring remain pending. The loopback API now supports authenticated sessions/tasks, approval review, and audit verification; `eis ask --fake` provides a deterministic local smoke run. T2 is available only where a Linux probe demonstrates every protection and is unavailable on this Windows host. See [docs/M0.2_DECISIONS.md](docs/M0.2_DECISIONS.md), [docs/EIS_POLICY_ENGINE.md](docs/EIS_POLICY_ENGINE.md) and [docs/EIS_MODEL_PROVIDER_CONFIGURATION.md](docs/EIS_MODEL_PROVIDER_CONFIGURATION.md).

The initial audit is at [docs/EIS_INITIAL_REPOSITORY_AUDIT.md](docs/EIS_INITIAL_REPOSITORY_AUDIT.md).

## Documentation

**Product and architecture**
- [Product specification](docs/EIS_PRODUCT_SPEC.md)
- [System architecture](docs/EIS_SYSTEM_ARCHITECTURE.md)
- [Technical design](docs/EIS_TECHNICAL_DESIGN.md)
- [Policy engine and decision contract](docs/EIS_POLICY_ENGINE.md)
- [Model provider configuration](docs/EIS_MODEL_PROVIDER_CONFIGURATION.md)
- [Architecture decisions](docs/EIS_ARCHITECTURE_DECISIONS.md)

**Agent and knowledge**
- [Agent architecture](docs/EIS_AGENT_ARCHITECTURE.md)
- [Agent runtime design](docs/EIS_AGENT_RUNTIME_DESIGN.md)
- [Memory architecture](docs/EIS_MEMORY_ARCHITECTURE.md)

**Skills**
- [Skill system](docs/EIS_SKILL_SYSTEM.md)
- [Skill evolution](docs/EIS_SKILL_EVOLUTION.md)

**Security and privacy**
- [Security architecture](docs/EIS_SECURITY_ARCHITECTURE.md)
- [Threat model](docs/EIS_THREAT_MODEL.md)
- [Execution and permission model](docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md)
- [Privacy and data governance](docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md)

**Contracts and delivery**
- [Database schema](docs/EIS_DATABASE_SCHEMA.md)
- [API specification](docs/EIS_API_SPECIFICATION.md)
- [Implementation roadmap](docs/EIS_IMPLEMENTATION_ROADMAP.md)
- [Research plan](docs/EIS_RESEARCH_PLAN.md)
- [Open source governance](docs/EIS_OPEN_SOURCE_GOVERNANCE.md)

**Project governance**
- [M0.2 bootstrap decisions](docs/M0.2_DECISIONS.md)
- [Repository audit](docs/EIS_INITIAL_REPOSITORY_AUDIT.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Support](SUPPORT.md)
- [Changelog](CHANGELOG.md)
- [Licence decision](LICENSE_RECOMMENDATION.md)
- [Public release checklist](PUBLIC_RELEASE_CHECKLIST.md)

## Learning

New to Python, FastAPI, SQLite or agent security? Start with [learning/README.md](learning/README.md). The guides separate M0.2 implemented behavior from M1+ specifications, and include component, request-flow, privacy and learning-roadmap diagrams.

## Privacy commitments

- EIS is its own repository. It is never initialised at a parent directory and never contains other projects as submodules or copies.
- Runtime data (database, artifacts, logs, tokens) lives in the platform application-data directory, never in the repository.
- No telemetry. External model providers require explicit, scoped consent.
- Private project source and engineering knowledge are not published by default.
- Privacy regression tests and public-release checks are mandatory, not advisory.

## Stack

Python 3.12 baseline (Python 3.11–3.12 supported) + FastAPI · SQLite (WAL, FTS5) in M1 · React + TypeScript + Vite in M10 · Typer · provider-neutral text gateway with OpenCode/OpenAI/Gemini/Anthropic/xAI adapters and optional Ollama · Docker/Podman for optional isolation · GitHub Actions.

## Getting started

Use Python 3.12 (Python 3.11 is also supported) and [uv](https://docs.astral.sh/uv/):

```powershell
uv sync --locked --all-extras
uv run eis version
uv run eis doctor
uv run eis provider-check
uv run eis policy-check
uv run eis policy-evaluate --principal user --action project.read --resource project:example
uv run eis policy-decisions
uv run eis session create --title "Example" --project example
uv run eis session list
uv run eis privacy-check
uv run eis audit verify
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Local check results on 2026-11-22 (Python 3.12.13):

| Check | Result |
|---|---|
| `uv run pytest -q` | 189 passed, 1 skipped |
| `uv run ruff check .` | pass |
| `uv run ruff format --check .` | pass |
| `uv run mypy src` | pass |
| `uv run pip-audit` | no known vulnerabilities |
| `uv build` + wheel smoke test | pass |
| `uv run eis privacy-check` | `privacy: ok` |
| `uv run eis audit verify` | `audit_chain=ok` |

Python 3.11 also passed the same 189 tests (1 POSIX-only test skipped on Windows) from the freshly built wheel in an isolated uv environment. CI has **not** run on GitHub yet; no remote exists and no push has been authorised. Green CI is therefore not claimed.

## Model provider (optional)

EIS enables no model provider by default. Supported text protocols: OpenCode-compatible Chat Completions, OpenAI Chat Completions, Google Gemini `generateContent`, Anthropic Messages, and xAI Chat Completions.

1. Copy `.env.example` to `.env` locally and fill in the placeholders. Never commit `.env` or paste a key into chat.
2. The `eis` CLI loads `.env` from the current directory. Set `EIS_MODEL_PROVIDER`, `EIS_MODEL`, the provider key and, for external calls, `EIS_MODEL_CONSENT_CLASSES`.
3. Run `uv run eis provider-check` to validate settings without a network request.

External calls fail closed without provider-scoped consent; C3 is never allowed. Consent, egress/model-call projections and the append-only hash-chained audit events are stored in SQLite; manage consent with `eis consent grant`, `eis consent list` and `eis consent revoke`, and verify integrity with `eis audit verify`. Official endpoints, protocol shapes, consent/audit behavior and deferred features are in [docs/EIS_MODEL_PROVIDER_CONFIGURATION.md](docs/EIS_MODEL_PROVIDER_CONFIGURATION.md). Ollama remains optional and is not required.

Runtime data belongs in the platform application-data directory, never in this repository. Registered project roots are empty by default; add one explicitly with `eis project register <project_id> <directory>`. The M2 project registry extends this minimal registry. No hardware, model tag or Obsidian path is chosen here.

The original vision document remains in the workspace but is ignored by Git pending publication review.

## Licence

Licensed under Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Dependency licence review remains part of the release process.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Contributions must not contain private project data, credentials or personal paths.
