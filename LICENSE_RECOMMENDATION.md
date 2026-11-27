# Licence Decision

Status: **Apache-2.0 adopted on 2026-11-20.** `LICENSE` and `NOTICE` are present. A full dependency-licence review remains a release gate.
Decision owner: project owner
Related: [docs/EIS_OPEN_SOURCE_GOVERNANCE.md](docs/EIS_OPEN_SOURCE_GOVERNANCE.md), [docs/EIS_ARCHITECTURE_DECISIONS.md](docs/EIS_ARCHITECTURE_DECISIONS.md) (ADR-021)

---

## 1. Adopted licence

Adopt **Apache License 2.0** for the EIS source code, with a `NOTICE` file and SPDX headers.

Documentation may be licensed CC BY 4.0. Skill packages declare their own compatible licence. The project name and logo are not licensed as trademarks.

## 2. Why Apache-2.0

| Criterion | Assessment |
|---|---|
| Patent grant | Explicit grant and termination clause; important for a security tool that may implement patented techniques |
| Trademark | Preserves the project name and marks; prevents confusing forks |
| Compatibility | Compatible with the intended permissive ecosystem; easy for companies to adopt |
| Contribution model | Section 5 covers contributions unless explicitly stated otherwise |
| Community norms | Widely understood, accepted by corporate legal review |
| Security positioning | Encourages audit and redistribution with notice preservation |
| Copyleft pressure | None; allows proprietary integrations through adapters |

## 3. Alternatives Considered

| Licence | Pros | Cons | Verdict |
|---|---|---|---|
| **MIT** | Shortest, most permissive, lowest friction | No explicit patent grant, no trademark clause | Viable fallback; weaker for a security product |
| **Apache-2.0** | Patent grant, trademark clause, corporate-friendly | Slightly more obligations (NOTICE, changed-file notices) | **Recommended** |
| **AGPL-3.0** | Strong reciprocity, protects hosted derivatives | Deters adoption, conflicts with some dependency and distribution models, hard to combine with a plugin ecosystem | Rejected |
| **BSL / SSPL** | Commercial protection | Not OSI-approved; contradicts "open-source" positioning | Rejected |
| **Dual licence** | Revenue option | Governance complexity, contributor consent, community trust cost | Deferred; can be revisited with an ADR |
| **Public domain / CC0** | No friction | No patent or trademark protection, unclear corporate acceptance | Rejected |

## 4. Dependency Licence Review (required before adoption)

Apache-2.0 is compatible with most of the planned stack, but the release must verify:

1. **Python runtime dependencies** — permissive licences (MIT, BSD, Apache-2.0, PSF) are acceptable. GPL/AGPL dependencies must be excluded or isolated behind a documented optional boundary.
2. **Frontend dependencies** — MIT/ISC/Apache-2.0 expected; check bundled fonts, icons and images for separate licences.
3. **Scanner binaries** — Gitleaks (MIT), Semgrep (LGPL-2.1 for the engine; check the CLI and rules licences), OSV-Scanner (Apache-2.0), Trivy (Apache-2.0), OWASP ZAP (Apache-2.0). These are invoked as external tools, not linked, but their licences and redistribution rules must be documented.
4. **Model runtimes and weights** — Ollama (MIT) and model weights carry separate licences. EIS must not redistribute weights.
5. **Container base images** — record licences and digests.
6. **Skills** — each package declares a licence; the registry rejects unlicensed packages and non-commercial restrictions.

Tooling: generate a dependency licence report in CI (`pip-licenses` or equivalent, `license-checker` for npm, SBOM with CycloneDX). A reviewer signs off the report for each release.

## 5. Obligations if Apache-2.0 Is Adopted

- Include a copy of the licence.
- Preserve copyright, patent, trademark and attribution notices.
- Include a `NOTICE` file if the work includes notices.
- State significant modifications to licensed files.
- Do not use the project name or marks to imply endorsement.
- Provide the licence to recipients of object code or containers.

## 6. Contribution Licensing

- Inbound contributions are licensed under the project licence via the Developer Certificate of Origin 1.1 (`git commit -s`). No CLA.
- Contributors confirm they have the right to submit the code.
- AI-generated contributions follow the same rule; the contributor is responsible.
- A contribution that cannot be licensed compatibly is rejected.

## 7. What Must Be Decided

| ID | Decision | Recommendation |
|---|---|---|
| L-01 | Code licence | Apache-2.0 |
| L-02 | Documentation licence | CC BY 4.0 |
| L-03 | Skill package licence policy | Require a declared OSI-approved licence; allow CC BY for docs-only skills |
| L-04 | Trademark policy | Reserve the name; publish a short usage policy at release |
| L-05 | CLA vs DCO | DCO 1.1, no CLA |
| L-06 | Dual licensing | Not now; requires an ADR and contributor consent |
| L-07 | Commercial support | Separate from licensing; no code-licence change |

## 8. Risks of Not Deciding

- The repository cannot be legally published.
- Contributors do not know the terms of their contribution.
- A dependency with an incompatible licence may be introduced unnoticed.
- A later licence change requires consent from every contributor, which is impractical after a community forms.

## 9. Action Plan

1. ✅ Project owner approved Apache-2.0 on 2026-11-20.
2. ⏳ Run the dependency licence report on the release candidate.
3. ✅ Added `LICENSE` (full Apache-2.0 text) and `NOTICE`; SPDX headers apply to new source files.
4. ⏳ Add the full licence check to CI and the public-release checklist.
5. ⏳ Publish a short trademark and name-usage note at public release.
6. ✅ Recorded as accepted ADR-021.

## 10. Decision Record

| Field | Value |
|---|---|
| Adopted | Apache-2.0 |
| Decided | **Apache-2.0 approved 2026-11-20; see repository LICENSE and NOTICE** |
| Decided by | Project owner |
| Date | 2026-11-20 |
| Dependency review | Vulnerability audit passed; full licence review pending before public release |
| Blocking | Public-release dependency-licence review and first commit |
