# EIS Open Source Governance

Version: 1.0 (M0 baseline)
Status: Baseline; Apache-2.0 adopted 2026-11-20 (ADR-021)
Related: [CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md), [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md), [LICENSE_RECOMMENDATION.md](../LICENSE_RECOMMENDATION.md), [PUBLIC_RELEASE_CHECKLIST.md](../PUBLIC_RELEASE_CHECKLIST.md)

---

## 1. Goals

EIS must be independently usable, inspectable and extensible by developers who have no relationship with the author. Governance exists to keep the project trustworthy as contributions grow: clear ownership, explicit review criteria, privacy gates that cannot be bypassed by a well-meaning PR, and a predictable release process.

## 2. Licence

| Item | Decision |
|---|---|
| Code licence | Apache License 2.0 - **adopted 2026-11-20** |
| Rationale | Patent grant, trademark clause, compatible with the intended plugin/registry ecosystem |
| NOTICE | Required; retains copyright and attribution |
| SPDX headers | Required on new source files (`SPDX-License-Identifier: Apache-2.0`) |
| Skill licences | Each package declares a licence; registry rejects unlicensed packages |
| Dependency licences | Allowlist; GPL/AGPL require an ADR and legal review |
| Documentation | CC BY 4.0 unless a file states otherwise |
| Name and logo | Trademark review is a pre-public task; the licence does not grant trademark rights |

Apache-2.0 was approved on 2026-11-20; `LICENSE` and `NOTICE` are present. Dependency-licence review remains a release gate. See [LICENSE_RECOMMENDATION.md](../LICENSE_RECOMMENDATION.md).

## 3. Repository Model

- One public repository for the core platform, adapters, built-in skills, docs and tests.
- Separate repositories, if ever needed, for a community registry service and sample projects; not for core code.
- No submodules. No vendored private repositories. No generated code committed without provenance.
- `main` is protected: no direct pushes, required checks, required review, linear history, signed commits recommended.
- Release tags are signed and immutable.

## 4. Roles

| Role | Responsibilities | Granted by |
|---|---|---|
| User | Uses EIS, reports issues | — |
| Contributor | Submits PRs, skills, docs | First merged contribution |
| Reviewer | Reviews PRs in an area | Maintainer nomination |
| Maintainer | Merge rights, release, security triage | Consensus of existing maintainers |
| Security maintainer | Handles private reports, coordinates fixes | Maintainer appointment |
| Registry curator | Reviews community skills | Maintainer appointment |

Bus factor is addressed by requiring at least two maintainers with merge rights before v1.0 public release. If that is not achievable, the release is labelled single-maintainer and the risk is disclosed.

## 5. Contribution Workflow

1. Open an issue for non-trivial work; use the feature or bug template.
2. Fork, branch, implement with tests and docs.
3. Sign off commits (`git commit -s`) under the Developer Certificate of Origin 1.1. No CLA.
4. Open a PR using the template: summary, risk, privacy impact, tests run, rollback.
5. CI runs lint, types, tests, security, privacy, contract and package jobs.
6. A reviewer checks architecture fit, tests, security and privacy.
7. Security-sensitive paths require a security maintainer review.
8. Squash merge; the commit message follows Conventional Commits.

### 5.1 Review criteria

| Criterion | Question |
|---|---|
| Scope | Does it solve the stated problem without unrelated changes? |
| Architecture | Does it respect layer and module contracts? Is an ADR needed? |
| Tests | Are failure paths, denials and edge cases covered? |
| Security | Does it add a capability, egress, execution or parsing of untrusted input? |
| Privacy | Could it write private data into the repo, logs, artifacts or prompts? |
| Compatibility | Does it break an API, schema, event or skill contract? |
| Operability | Can it be diagnosed and rolled back? |
| Docs | Are user-facing changes documented? |

### 5.2 Path-based review requirements

| Path | Additional review |
|---|---|
| `src/eis/core/policy/**` | Security maintainer + ADR |
| `src/eis/core/execution/**` | Security maintainer + sandbox tests |
| `src/eis/core/evidence/**` | Security maintainer |
| `src/eis/adapters/models/**` | Privacy review |
| `src/eis/adapters/pam/**`, `obsidian/**` | Privacy review |
| `policies/**` | Security maintainer |
| `skills/**` | Registry validation + curator |
| `.github/workflows/**` | Maintainer; actions pinned by SHA |
| `docs/EIS_SECURITY_ARCHITECTURE.md`, `EIS_THREAT_MODEL.md` | Security maintainer |

## 6. Security Disclosure

- `SECURITY.md` defines private reporting, response targets and scope.
- Do not open a public issue for a vulnerability.
- A confidential advisory is prepared; a fix is developed on a private branch; a release is cut; the advisory is published with credit unless the reporter declines.
- Target: acknowledge within 72 hours, initial assessment within 7 days, coordinated disclosure within 90 days or sooner.
- No bounty is promised; credit is given.

## 7. Skill Registry Governance

| Rule | Detail |
|---|---|
| Review | Every community skill is reviewed by a curator before listing |
| Validation | Automated pipeline must pass; a curator reviews permissions and intent |
| Licence | Declared and compatible; no unlicensed packages |
| Provenance | Source URL, commit and content hash recorded |
| Permissions | Least privilege; wildcard and reserved capabilities rejected |
| Executable content | Separate review, T2 requirement, explicit user approval |
| Updates | New version = new review; no silent auto-update |
| Revocation | Curator or security maintainer can revoke; reason published |
| Delisting | Malicious or abandoned packages are delisted, not deleted from history |

Registry policy is enforced by tooling, not only by reviewer diligence.

## 8. Release Process

| Step | Requirement |
|---|---|
| Freeze | Release branch; no new features |
| Gates | CI green; privacy suite; full-history secret scan; dependency licences; SBOM |
| Upgrade test | Install previous minor, upgrade, run smoke tests |
| Docs | Changelog, migration notes, known limitations |
| Approval | Maintainer sign-off; security sign-off for security-relevant changes |
| Tag | Signed `vX.Y.Z` |
| Publish | Wheel, sdist, container image, checksums, SBOM |
| Announce | Release notes with breaking changes and security fixes |

Cadence: milestone-driven, not time-driven. Patch releases for security within 7 days of a verified fix.

## 9. Versioning and Deprecation

- SemVer for the package; `/api/v1` additive-only; database migrations forward-only.
- Deprecation requires a changelog entry, a runtime warning where possible, and one minor release of overlap.
- Removing a capability or tightening a permission is a breaking change and requires an ADR.
- Skill format and event schemas are versioned independently; the compatibility matrix is documented.

## 10. Decision Making

- Lazy consensus for routine changes: a maintainer approval with no objection within 72 hours.
- An ADR is required for architecture, schema, security, privacy and public API decisions.
- Disagreements are resolved by the maintainers; security and privacy concerns have veto weight.
- A maintainer with a conflict of interest recuses from the decision.
- Decisions and rationale are recorded in the repository, not in private chat.

## 11. Community Standards

- `CODE_OF_CONDUCT.md` applies to all project spaces.
- Enforcement is handled by maintainers; reports are confidential.
- Contributions that require private data to demonstrate a bug are not accepted; a synthetic reproduction is required.
- AI-generated contributions are allowed but must be reviewed, tested and disclosed; the contributor is responsible for correctness and licence compliance.

## 12. Privacy Rules for Contributions

1. No real private repository content, paths, logs, incidents or credentials in issues, PRs, tests, fixtures or docs.
2. Examples must be synthetic or permissively licensed with attribution.
3. A PR that adds a fixture resembling real data must state its provenance.
4. Screenshots must be redacted.
5. CI blocks on the privacy suite; a maintainer cannot merge around it without an ADR and a recorded exception.

## 13. Naming and Branding

- "EIS" and "Engineering Intelligence System" are working names.
- Before public branding: trademark search, domain and handle availability, and a check for confusingly similar projects.
- The licence grants no trademark rights; community forks must not imply endorsement.
- A name change, if required, is a documentation and packaging change, not an architectural one.

## 14. Sustainability

- Maintainer time is the bottleneck; features are prioritized by user value and security.
- No paid tier is planned for v1; if one is added, core privacy guarantees remain in the open-source edition.
- Funding, if any, is disclosed and does not influence security decisions.
- Abandonment plan: the repository remains Apache-2.0; a fork is permitted; users are warned in the README if maintenance stops.

## 15. Governance Metrics

| Metric | Target |
|---|---|
| PR first response | ≤ 7 days |
| Security acknowledgement | ≤ 72 hours |
| Review coverage of security paths | 100% |
| Releases with complete checklist | 100% |
| Open critical security issues at release | 0 |
| Bus factor (maintainers with merge rights) | ≥ 2 |

## 16. Acceptance Criteria

1. `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md` and `CHANGELOG.md` exist and are linked from `README.md`.
2. Branch protection and required checks are configured before the first public push.
3. The DCO check rejects unsigned commits.
4. Path-based review rules are enforced by `CODEOWNERS`.
5. A release rehearsal completes the public-release checklist with zero unresolved items.
6. The licence decision is recorded and the `LICENSE` file matches it.
7. The registry can revoke a skill and the revocation takes effect without a release.
