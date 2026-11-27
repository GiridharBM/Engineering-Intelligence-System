# Contributing to EIS

Thank you for considering a contribution. EIS handles private source code, so contribution rules include privacy requirements that are not negotiable.

Read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md) and [docs/EIS_ARCHITECTURE_DECISIONS.md](docs/EIS_ARCHITECTURE_DECISIONS.md) before a substantial change.

## Current state

EIS is at M0.2 with the M1 runtime slices implemented: policy, provider gateway, audit chain, approvals, the durable session/task runtime, read-only project tools and the bounded model loop. The repository is initialised locally with no remote and no commits; the CI workflow exists but has not run on GitHub. Until a remote is configured, open an issue or send a patch by another agreed channel rather than assuming a pull-request workflow.

## Ways to contribute

- Bug reports with a minimal, synthetic reproduction.
- Documentation corrections and clarifications.
- Architecture review comments on the M0 package.
- Adapters and skills that do not require private data.
- Tests, including adversarial and privacy cases.
- Accessibility and usability improvements.

## Development setup

```bash
# Python 3.12 baseline; Python 3.11 is also supported
uv lock --check
uv sync --locked --all-extras
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run eis privacy-check

# Frontend (M10; not present yet)
cd frontend
npm ci
npm run test
npm run build
```

Do not add the original vision `.txt` to Git; it is intentionally ignored pending publication review.

Never commit a virtual environment, lockfile from another tool, runtime database, artifact, log or token.

## Contribution workflow

1. Open an issue for non-trivial work. State the problem, the proposed change and the security/privacy impact.
2. Fork and branch from `main`: `feat/...`, `fix/...`, `docs/...`, `security/...`.
3. Write tests first where practical. Every behaviour change needs a test that fails before and passes after.
4. Update documentation and `CHANGELOG.md`.
5. Sign off every commit under the Developer Certificate of Origin 1.1 (`git commit -s`). No CLA.
6. Open a pull request using the template. Include: summary, risk, privacy impact, tests run (with real output), and rollback.
7. Address review comments. Do not force-push after review without a note; reviewers need to see the delta.

## Commit messages

Conventional Commits:

```
feat(projects): add exclusion glob validation
fix(policy): deny reserved capability in skill grants
docs(security): clarify T1 limitation on Windows
test(privacy): cover UNC path escape
```

The body explains *why*; the diff explains *what*.

## What reviewers look for

| Area | Expectation |
|---|---|
| Scope | One problem per PR; no unrelated refactors |
| Architecture | Respects layer and module rules; ADR if a decision changes |
| Tests | Success, denial, failure and edge cases; no skipped tests without a reason |
| Security | New capabilities, egress, execution, parsing of untrusted input are justified |
| Privacy | No private paths, data, logs or secrets in code, tests, fixtures or docs |
| Compatibility | API, schema, event and skill contracts are versioned |
| Operability | Errors are actionable; the change can be rolled back |
| Docs | User-facing changes documented |

Security-sensitive paths require a security maintainer review:

- `src/eis/core/policy/**`
- `src/eis/core/execution/**`
- `src/eis/core/evidence/**`
- `src/eis/adapters/models/**`
- `src/eis/adapters/pam/**`, `obsidian/**`
- `policies/**`
- `.github/workflows/**`
- the security architecture and threat model documents

## Privacy rules for contributions

1. **No real private project content.** Use synthetic fixtures. A bug that needs real data must be reduced to a synthetic reproduction.
2. **No personal paths.** No drive letters, home directories, UNC paths or machine names.
3. **No secrets.** Not in code, tests, fixtures, screenshots, logs or commit messages. Revoke any secret you accidentally exposed; assume it is public.
4. **No runtime data.** Databases, artifacts, logs, caches and tokens are ignored and must stay out of Git.
5. **Provenance for examples.** Public repository snippets require a permissive licence and attribution.
6. **Screenshots** must be redacted.

The privacy suite runs in CI and fails the build. Do not attempt to bypass it; discuss the case in an issue.

## Adding a skill

A skill package must contain `SKILL.md`, `manifest.yaml`, `permissions.yaml`, `tests/`, `evaluations/` and `changelog.md`. See [docs/EIS_SKILL_SYSTEM.md](docs/EIS_SKILL_SYSTEM.md).

Rules:

- Declarative skills are preferred; executable content needs a strong justification.
- Request the minimum capabilities with a written justification for each.
- Wildcard and reserved capabilities are rejected.
- Include at least one negative test (what the skill must refuse to do).
- Community skills are untrusted until validated; installation does not execute code.

## Adding an adapter

- Implement the port; do not modify core policy or the gateway.
- Translate vendor errors into domain errors.
- Declare the minimum isolation tier and the required capabilities.
- Add detection and an `unavailable` path; a missing binary is not a failure.
- Do not add a runtime dependency without a licence and vulnerability check.

## Architectural decisions

If your change alters a module boundary, persisted schema, capability, trust level, public API version or privacy control, add an ADR to `docs/EIS_ARCHITECTURE_DECISIONS.md` before implementation. Supersede rather than rewrite.

## AI-assisted contributions

Allowed, with conditions:

- Disclose that AI assistance was used in the PR description.
- You are responsible for correctness, tests, licence compliance and security.
- Do not paste private code or secrets into an external model.
- Generated code must follow the same review standard; "the model wrote it" is not a defence.

## Review timeline

Maintainers aim to respond within 7 days. Security reports follow [SECURITY.md](SECURITY.md), not this timeline.

## Recognition

Contributors are credited in release notes. Significant contributions may be invited to reviewer or maintainer roles under [docs/EIS_OPEN_SOURCE_GOVERNANCE.md](docs/EIS_OPEN_SOURCE_GOVERNANCE.md).
