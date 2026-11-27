# Support

EIS is at M0.2 (repository foundation and package skeleton). The M1 agent runtime has not started and there is no published release yet. You can install the local package from source with `uv sync --locked --all-extras` and run `uv run eis doctor`.

## Before asking

1. Read [README.md](README.md) and the relevant document in [docs/](docs/).
2. Search existing issues and discussions.
3. Run `eis doctor` (from M0.2 onward) and include its redacted output.
4. Check [docs/EIS_INITIAL_REPOSITORY_AUDIT.md](docs/EIS_INITIAL_REPOSITORY_AUDIT.md) for known environment limitations.

## Where to ask

| Topic | Channel |
|---|---|
| Usage question or idea | GitHub Discussions |
| Reproducible bug | GitHub Issue (bug template) |
| Feature request | GitHub Issue (feature template) |
| Documentation problem | GitHub Issue (docs label) |
| Security vulnerability | **Private report only** — see [SECURITY.md](SECURITY.md) |
| Code of conduct report | Private maintainer contact — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

## What to include in a bug report

- EIS version or commit.
- OS and version, Python version, Node version (if relevant).
- Which model provider and whether it is local.
- Whether Docker/Podman is available (for isolation issues).
- Exact command or API request.
- Expected and actual behaviour.
- Redacted logs with the correlation ID.
- A minimal reproduction using **synthetic data only**.

Do not paste: real source code, secrets, tokens, absolute personal paths, database files or private incident content. Replace paths with `<project>` and values with `<redacted>`. If the bug cannot be reproduced without private data, reduce it to a synthetic case first.

## What we cannot support

- Recovering data you deleted without a backup.
- Debugging a modified fork as if it were upstream (report the difference, then reproduce on a clean release).
- Cloud provider account, billing or quota issues.
- Third-party scanner installation problems (we can help with detection and `unavailable` handling).
- Requests to weaken a security control for convenience.
- Private project consulting.

## Response expectations

Volunteer maintainers aim to triage issues within 7 days. Security reports follow the targets in [SECURITY.md](SECURITY.md). No SLA is offered for community support.

## Commercial and research use

EIS is intended to be usable and extensible without a commercial relationship. If you use EIS in research, cite the repository and record the version and configuration used. A `CITATION.cff` will be added at public release.
