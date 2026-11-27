# EIS Technical Design

Version: 1.0 (M0 baseline)
Status: Baseline
Related: [EIS_SYSTEM_ARCHITECTURE.md](EIS_SYSTEM_ARCHITECTURE.md), [EIS_API_SPECIFICATION.md](EIS_API_SPECIFICATION.md), [EIS_DATABASE_SCHEMA.md](EIS_DATABASE_SCHEMA.md), [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md)

---

## 1. Technology Evaluation

| Area | Proposed | Verdict | Rationale | Alternatives considered |
|---|---|---|---|---|
| Backend | Python 3.12 + FastAPI | **Adopt** | Typed contracts, async, generated OpenAPI, strong ecosystem for scanners/Git | Django (heavier, ORM-centric), Flask (weaker contracts), Go (better runtime, weaker AI/tooling ecosystem) |
| Frontend | React 19 + TypeScript + Vite | **Adopt** | Mature, static build, no server runtime | Next.js (unneeded SSR), Svelte (smaller ecosystem), HTMX (weaker complex UI) |
| CLI | Typer | **Adopt** | Type-hint driven, good help output, same models as API | Click (more boilerplate), argparse (poor DX) |
| Metadata | SQLite + SQLAlchemy 2 + Alembic | **Adopt** | Local-first, transactional, FTS5, easy backup | PostgreSQL (server dependency), DuckDB (analytics, weaker OLTP), raw sqlite3 (manual migrations) |
| Retrieval | FTS5 + optional PAM adapter | **Adopt** | Works offline, explainable, no duplicate vector store | Embedded vector DB now (unjustified), cloud embeddings (privacy) |
| Docs projection | Separate Obsidian vault | **Adopt with constraints** | Human navigation without coupling | Obsidian as primary store (rejected) |
| Models | Provider-neutral text gateway; vendor protocols; Ollama optional | **Adopt** | OpenCode/OpenAI/xAI Chat Completions, Gemini generateContent, Anthropic Messages; explicit consent | Vendor SDK coupling (rejected), one assumed protocol (rejected) |
| Security scanners | Gitleaks, Semgrep, OSV-Scanner, Trivy, ZAP optional | **Adopt as adapters** | Established, local, normalizable | Single commercial scanner (lock-in), custom scanners (scope creep) |
| Testing | pytest + adapters | **Adopt** | Standard; project-specific runners remain external | Unified test framework (impossible across stacks) |
| CI | GitHub Actions | **Adopt** | Public repo, matrix builds, security integrations | Self-hosted (operational cost), GitLab (not the chosen host) |
| Packaging | uv + hatchling; pipx/uv tool install | **Adopt** | Reproducible, simple native install | Poetry (slower, redundant), conda (heavy) |
| Isolation | T1 subprocess; T2 Docker/Podman | **Adopt** | Honest tiering; container optional | Always-on containers (install friction), Windows AppContainer (complex, deferred) |
| Messaging | In-process bus + outbox | **Adopt** | No broker, durable enough for one machine | RabbitMQ/Redis (unnecessary), Kafka (absurd for v1) |
| Graph | Typed edges in SQLite | **Adopt** | Explainable, no new store | Neo4j (operational cost, no measured need) |
| Deployment | Native; optional container | **Adopt** | Local-first requirement | Kubernetes (explicit non-goal) |

**Rejected infrastructure:** microservices, Kubernetes, dedicated graph database, mandatory vector database, message broker, cloud database, serverless. Each would add operational surface without a demonstrated requirement. Re-evaluation requires an ADR with measurements.

## 2. Package Layout

```
eis/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE                  # Apache-2.0 (ADR-021)
├── NOTICE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── CODE_OF_CONDUCT.md
├── SUPPORT.md
├── PUBLIC_RELEASE_CHECKLIST.md
├── src/eis/
│   ├── __init__.py
│   ├── config/              # settings, precedence, validation
│   ├── domain/              # entities, enums, value objects, errors
│   │   ├── contracts/       # DTOs and port types
│   │   └── invariants/
│   ├── core/
│   │   ├── agent_runtime/   # orchestrator, session, context, limits
│   │   ├── policy/          # engine, rules, capabilities
│   │   ├── execution/       # gateway, backends (T0/T1/T2), profiles
│   │   ├── evidence/        # artifacts, hashes, redaction
│   │   └── events/          # bus, outbox
│   ├── modules/
│   │   ├── project_registry/
│   │   ├── project_analyzer/
│   │   ├── knowledge/
│   │   ├── experience_graph/
│   │   ├── skills/
│   │   ├── security_engine/
│   │   ├── verification/
│   │   ├── dependency_engine/
│   │   ├── automation/
│   │   ├── report_engine/
│   │   └── agents/          # specialist role policies and prompts
│   ├── adapters/
│   │   ├── models/          # chat completions, gemini, anthropic, registry
│   │   ├── pam/
│   │   ├── obsidian/
│   │   ├── git/
│   │   ├── scanners/
│   │   ├── test_runners/
│   │   ├── sandbox/
│   │   └── mcp/
│   ├── api/                 # FastAPI app, routes, schemas, SSE
│   ├── cli/                 # Typer app
│   ├── worker/              # job runner
│   └── storage/             # SQLAlchemy models, repositories, migrations
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
├── skills/builtin/
├── schemas/                 # JSON Schema exports; bundled into the wheel
├── policies/                # default policy rules, versioned; bundled into the wheel
├── examples/                # synthetic projects
├── scripts/                 # repository verification tools (wheel, privacy)
├── tests/                   # M0.2: flat modules; split into suites as they grow
├── docs/
└── .github/workflows/
```

Rationale for deviation from vision §25: one importable package (`eis`), one dependency graph, no `apps/` vs `core/` dual roots (ADR-002). Module names are preserved.

### 2.1 Import rules

- `domain` imports stdlib and Pydantic only.
- `core` imports `domain`, never `adapters`, `api`, `cli`.
- `modules` import `domain`, `core` contracts; never another module's `internal`.
- `adapters` implement ports; they never import `modules` or entry points.
- `api`, `cli`, `worker` call application services; they contain no policy logic.
- Only `core/execution/backends` may import `subprocess`/`os.system` equivalents.

Enforced by `import-linter` plus a custom AST check.

## 3. Configuration

Precedence: defaults < `config.toml` (runtime dir) < `.eis/config.toml` (project, non-secret) < `EIS_*` environment < CLI flags (ADR-027).

```toml
[app]
name = "EIS"
data_dir = ""                 # empty = platform default
log_level = "info"

[server]
host = "127.0.0.1"
port = 8765
allowed_hosts = ["127.0.0.1", "localhost"]

[projects]
roots = []                    # runtime config, never committed
max_projects = 200
exclude_defaults = [
  "**/.git/objects/**", "**/node_modules/**", "**/.venv/**",
  "**/dist/**", "**/build/**", "**/target/**", "**/.env", "**/*.pem"
]

[models]
default_provider = ""        # empty = disabled; "opencode" or optional "ollama"
allow_cloud = false          # derived true only when an external provider is explicitly configured
# Provider secrets are environment-only and are never valid TOML values.
# OpenCode settings: EIS_OPENCODE_BASE_URL, EIS_OPENCODE_MODEL, OPENCODE_API_KEY
# See docs/EIS_MODEL_PROVIDER_CONFIGURATION.md.

[execution]
default_tier = "T1"
max_output_bytes = 2097152
container_runtime = "auto"    # auto|docker|podman|none

[privacy]
telemetry = false
redact_logs = true
external_embeddings = false
```

Validation: unknown keys fail; secret-shaped keys rejected; paths normalised and checked for repository containment; invalid policy fails closed.

## 4. Error Handling

- Domain exceptions inherit `EisError(code, message, details, retryable)`.
- Adapters translate vendor exceptions to domain errors; no vendor type escapes.
- No bare `except`; no swallowing of `CancelledError`.
- Errors carry a correlation ID and are logged once at the boundary.
- User-facing messages are actionable and contain no stack traces, SQL or absolute paths.
- Retries only where documented; every retry has a bound and jitter.

## 5. Logging and Observability

| Aspect | Rule |
|---|---|
| Format | JSON lines to file; human-readable only in a TTY |
| Fields | timestamp, level, logger, message, correlation_id, task_id, session_id, tool, duration_ms |
| Redaction | Applied at the sink; secret corpus test |
| Rotation | Size-based 10 MB × 5, plus daily retention job |
| Levels | `debug` never enabled by default; `info` for lifecycle; `warning` for degraded; `error` for failed actions |
| Metrics | Local `/metrics` (counters/histograms) without labels containing project names or paths |
| Tracing | Correlation ID propagation; no external collector in v1 |

No prompt or response content is logged by default; hashes and token counts only.

## 6. Testing Strategy

| Level | Scope | Tooling | Gate |
|---|---|---|---|
| Unit | Domain invariants, scoring, policy, redaction | pytest | PR |
| Contract | Pydantic schemas, OpenAPI, event schemas | pytest + schemathesis | PR |
| Integration | SQLite, outbox, jobs, gateway with a fake runner | pytest | PR |
| Adapter | Each scanner/runner against a synthetic fixture | pytest, skip if binary absent | PR (detected set) |
| Security | Policy matrix, escape corpus, approval binding, sandbox limits | pytest | PR/nightly |
| Privacy | Paths, secrets, egress, artifacts, logs | pytest | PR |
| E2E | CLI and API happy paths with the fake provider | pytest + httpx | PR |
| Performance | Scan 50k files, API p95, policy p99 | benchmark script | Nightly |
| Migration | Empty → head; previous release → head | pytest | PR |
| Restore | Backup, corrupt, restore, verify | pytest | Release |

Coverage: ≥ 90% for `core/policy`, `core/execution`, `core/evidence`, `modules/skills`; ≥ 80% overall; no decrease on PR.

## 7. Coding Standards

- Python 3.11+ syntax; `ruff` for lint/format (line length 100); `mypy --strict` for `domain`, `core`, `modules`; `pyright` optional.
- Public functions and ports have docstrings stating contract, errors and idempotency.
- No `Any` at module boundaries; use typed DTOs.
- Prefer immutable dataclasses/Pydantic models for value objects.
- Time: UTC only; store epoch ms; render ISO-8601.
- IDs: ULID via one helper; never random UUIDs in domain records.
- JSON: canonical serialisation for hashing (sorted keys, no whitespace).
- No `print`; use the logger. No `eval`, `exec`, `pickle` on untrusted data.
- No direct `subprocess`, `requests`, `httpx`, `socket` or file writes outside designated adapters/gateway.
- Comments explain *why*, not *what*; security-relevant decisions link to an ADR.
- Frontend: TypeScript strict, no `any`, accessible components, no business logic in components.

## 8. Dependency Policy

1. Every dependency needs a justification in the PR description.
2. Prefer stdlib and existing dependencies over new ones.
3. Runtime dependencies are few and pinned; dev dependencies are isolated.
4. Licences must be on the allowlist (Apache-2.0, MIT, BSD, ISC, PSF, MPL-2.0). GPL/AGPL require an ADR and legal review.
5. Cloud SDKs are optional extras so a local-only install ships no cloud client.
6. `uv lock --check`, `pip-audit`/OSV and `npm audit` run in CI.
7. No dependency may execute code at import time beyond normal initialisation; no post-install scripts for runtime packages.
8. Transitive licence and vulnerability reports are generated per release.

## 9. Build, Packaging and Release

- Build: `uv build` (wheel + sdist); reproducible from a clean checkout with the lockfile.
- Install: `uv tool install eis` or `pipx install eis`; `eis doctor` verifies.
- Frontend: `npm ci && npm run build`; assets bundled into the wheel under `eis/api/static`.
- Container: multi-stage build, non-root, digest-pinned base, no shell in the final image where feasible.
- Release: tag `vX.Y.Z`, signed; checksums; SBOM; changelog; migration notes; upgrade test from the previous minor.
- Versioning: SemVer for the package and the API; skill versions independent.

## 10. Database and Migration Operations

- Alembic autogenerate is reviewed, never trusted blindly.
- Migration tests run against a fixture from the previous release.
- Long migrations are batched and resumable; the app refuses to start with a pending destructive migration without a backup.
- Downgrades are documented; irreversible migrations are marked and require approval.
- Backups are verified by restore, not by file existence.

## 11. Performance Design

| Concern | Approach |
|---|---|
| Project scan | Streaming directory walk, ignore-pruned, parallel hashing bounded by CPU, no file content retained |
| FTS | Pre-computed signatures, indexed filters, `bm25` ordering |
| API reads | Indexed queries, pagination, no N+1; compiled Pydantic models |
| Agent turns | Context budget, summary of old turns, tool output truncation |
| Worker | Bounded pool, lease-based jobs, backoff |
| Model calls | Concurrency cap, timeout, no retry on semantic failure |
| Artifacts | Content-addressed, streamed writes, size caps |

Targets are in NFR-01…NFR-14. A regression > 20% on a tracked benchmark fails the nightly job.

## 12. Frontend Design

| Page | Purpose |
|---|---|
| Overview | Project health, recent runs, pending approvals, integrity status |
| Projects | Registry, profile evidence, scan history |
| Session | Conversation, plan, step timeline, approvals inline |
| Findings | Filter by severity/status, evidence links |
| Knowledge | Incidents, reuse assessment, search |
| Skills | Installed versions, validation results, metrics, evolution proposals |
| Approvals | Pending with diff and risk labels |
| Runs | Verification history and logs |
| Settings | Roots, providers, consent, retention, diagnostics |

Rules: server state via TanStack Query; the short-lived access token is held in memory only, never in `localStorage` or a URL; optimistic updates only for reversible UI state; every destructive action shows impact and requires confirmation; keyboard navigation and focus management are tested; empty, loading, error and permission-denied states are designed, not afterthoughts.

## 13. Development Workflow

1. Create a branch from `main`.
2. `uv sync --all-extras` and `npm ci`.
3. Run `uv run pytest`, `uv run ruff check`, `uv run mypy`, `npm run test`, `npm run build`.
4. Add tests and docs with the change; update the ADR if a decision changes.
5. Commit with a Conventional Commit message and a DCO sign-off.
6. PR: template asks for risk, privacy impact, tests and rollback.
7. CI must pass; one maintainer review; security review for flagged paths.
8. Squash merge; `main` protected.

## 14. CI Pipeline

| Job | Checks |
|---|---|
| `lint` | ruff, format, import contracts, no forbidden imports |
| `typecheck` | mypy strict on core modules |
| `test` | pytest on Windows, Ubuntu, macOS × Python 3.11/3.12 |
| `frontend` | eslint, tsc, vitest, build, accessibility lint |
| `security` | Gitleaks, Semgrep, OSV/pip-audit, npm audit, Trivy (image) |
| `privacy` | privacy suite, repo boundary, path/secret scan |
| `contract` | OpenAPI diff, schema validation |
| `package` | build wheel/sdist, install in a clean venv, smoke test |
| `licence` | dependency licence allowlist |
| `docs` | link check, required doc presence, ADR index consistency |

Failures are blocking. Security findings are triaged with a documented exception only for accepted risk with an expiry.

## 15. Acceptance Criteria

1. A clean checkout installs and runs the test suite with one documented command per OS.
2. Import contracts and forbidden-import checks pass.
3. The CI matrix is green on all supported platforms.
4. Generated OpenAPI and committed schema artifacts are identical.
5. A release candidate produces an SBOM, checksums and a successful upgrade test.
6. No runtime dependency is added without a licence and vulnerability check.
7. The frontend builds to static assets with no Node runtime required in production.
