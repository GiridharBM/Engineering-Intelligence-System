# Public Release Checklist

Use this checklist for every public release, and in full for the first public release. A release is blocked if any mandatory item is unchecked. Record evidence (link, command output, hash) next to each item; "looks fine" is not evidence.

Related: [docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md](docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md), [docs/EIS_OPEN_SOURCE_GOVERNANCE.md](docs/EIS_OPEN_SOURCE_GOVERNANCE.md), [SECURITY.md](SECURITY.md)

---

## 0. Approvals

- [ ] Project owner approved the release scope and date.
- [ ] Licence decision recorded and `LICENSE` matches it (see [LICENSE_RECOMMENDATION.md](LICENSE_RECOMMENDATION.md)).
- [ ] Security maintainer sign-off for security-relevant changes.
- [ ] Privacy review sign-off for any change touching egress, redaction, export or adapters.

## 1. Repository Boundary

- [ ] Git root is the EIS directory, not a parent (`git rev-parse --show-toplevel`).
- [ ] No `.gitmodules` and no submodules.
- [ ] No other project directory is referenced as a tracked path.
- [ ] `git status` is clean; no untracked runtime data.
- [ ] `.gitignore` covers databases, artifacts, logs, caches, tokens and local config.
- [ ] The privacy test `test_repo_boundary` passes.

## 2. Secret and History Scan

- [ ] Working-tree secret scan passes (Gitleaks + pattern rules).
- [ ] **Full-history** secret scan passes.
- [ ] Commit messages contain no secrets or private paths.
- [ ] No credentials in fixtures, notebooks, screenshots or docs.
- [ ] Any historical secret found has been revoked, not just removed.
- [ ] Signed commits verified where policy requires it.

## 3. Private Data Review

- [ ] No real project names, paths, incidents, logs or architecture details.
- [ ] No personal drive letters, home directories, UNC paths or machine names.
- [ ] No runtime database, artifact, log, backup or export.
- [ ] The planning document and personal notes are excluded or explicitly approved.
- [ ] Examples are synthetic or permissively licensed with attribution.
- [ ] Screenshots are redacted.
- [ ] `docs/` cross-references do not point to private resources.

## 4. Code and Tests

- [ ] CI green on Windows, Ubuntu and macOS for supported Python versions.
- [ ] Unit, integration, contract, security and privacy suites pass.
- [ ] Privacy suite fails when a canary secret/path is introduced (negative test).
- [ ] Policy matrix covers every capability.
- [ ] No skipped tests without a documented reason.
- [ ] Coverage meets thresholds and did not decrease.
- [ ] Migration test from the previous release passes.
- [ ] Clean install from wheel/sdist succeeds and `eis doctor` reports ready.

## 5. Security

- [ ] Threat model reviewed for this release.
- [ ] No new accepted risk without a register entry and expiry.
- [ ] Policy files validated; no wildcard or reserved capability grants.
- [ ] Approval binding tests pass (mutation, replay, expiry).
- [ ] Sandbox tests pass; T2 profiles validated (non-root, no network, read-only mounts).
- [ ] Egress ledger shows no unconsented call in the test run.
- [ ] Redaction corpus produces no literal secret in logs, artifacts, reports or payloads.
- [ ] Audit chain verifies; tamper test blocks approvals.
- [ ] Dependency vulnerability scan has no unresolved critical/high finding.
- [ ] Scanner binaries are detected and version-checked; missing tools report `unavailable`.

## 6. Privacy

- [ ] Data map updated for new data types.
- [ ] No telemetry code or endpoint exists.
- [ ] Model provider consent is required and tested.
- [ ] Export preview and approval tested; counts match.
- [ ] Deletion test confirms no retrievable traces remain.
- [ ] PAM and Obsidian adapters respect scope and do not write outside managed paths.
- [ ] Runtime paths resolve outside the repository on all supported OSes.

## 7. Legal and Licensing

- [ ] Dependency licence report reviewed and signed off.
- [ ] No GPL/AGPL in distributed runtime dependencies without an ADR.
- [ ] `NOTICE` updated with third-party attributions.
- [ ] SPDX headers present on source files.
- [ ] Skill packages declare compatible licences.
- [ ] Trademark and name check completed; no confusingly similar project.
- [ ] Contribution terms (DCO) and `CONTRIBUTING.md` are current.

## 8. Supply Chain

- [ ] Lockfiles committed and verified (`uv lock --check`, `npm ci`).
- [ ] GitHub Actions pinned by commit SHA.
- [ ] Container base images digest-pinned and scanned.
- [ ] SBOM generated (CycloneDX) and attached to the release.
- [ ] Release artifacts have SHA-256 checksums.
- [ ] Tag is signed; build provenance recorded.
- [ ] No build script downloads and executes unverified remote code.

## 9. Documentation

- [ ] README states status, scope, privacy posture and licence accurately.
- [ ] Install instructions tested on each OS.
- [ ] Upgrade and migration notes present.
- [ ] Known limitations and residual risks documented.
- [ ] Security and privacy docs match the implementation.
- [ ] `CHANGELOG.md` complete with breaking changes and security fixes.
- [ ] Links resolve; no dead internal references.
- [ ] API docs generated and diffed; no unintended contract change.
- [ ] `CITATION.cff` present if research use is claimed.

## 10. Repository Configuration

- [ ] `main` protected: no direct pushes, required checks, required review, linear history.
- [ ] `CODEOWNERS` enforced for security-sensitive paths.
- [ ] Issue and PR templates present.
- [ ] Private vulnerability reporting enabled.
- [ ] Branch protection applies to administrators.
- [ ] Release environment requires approval.
- [ ] Dependabot or equivalent alerts enabled.

## 11. Release Mechanics

- [ ] Version bumped consistently (package, API, docs).
- [ ] Changelog dated and linked.
- [ ] Tag created from the release commit.
- [ ] Wheel, sdist and container published with checksums.
- [ ] Release notes include breaking changes, security fixes and known issues.
- [ ] Upgrade from the previous minor tested on a clean machine.
- [ ] Rollback procedure documented and tested.

## 12. Post-Release

- [ ] Monitoring of issues and security reports assigned.
- [ ] Advisory process ready if a defect is found.
- [ ] Documentation site deployed.
- [ ] Release announcement reviewed for private data.
- [ ] Retrospective recorded with deviations and debt.

## 13. First Public Release Only

- [ ] Name and trademark search completed.
- [ ] Domain and package names reserved (PyPI, npm if applicable).
- [ ] At least two maintainers with merge rights, or single-maintainer risk disclosed.
- [ ] Initial governance decisions recorded as accepted ADRs.
- [ ] Sample repositories and benchmark data are synthetic or licensed.
- [ ] Full-history scan completed on the entire history, not only the release branch.
- [ ] A dry-run clone of the public repository installs and runs the demo.

## Sign-off

| Role | Name | Date | Evidence |
|---|---|---|---|
| Release owner | | | |
| Security maintainer | | | |
| Privacy reviewer | | | |
| Project owner | | | |

**Any unchecked mandatory item blocks the release.** Exceptions require a written ADR, a compensating control and an expiry date.
