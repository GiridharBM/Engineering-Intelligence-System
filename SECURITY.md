# Security Policy

EIS executes code, reads private repositories and handles engineering knowledge. Security reports are taken seriously and handled privately.

## Reporting a vulnerability

**Do not open a public issue, discussion or pull request for a vulnerability.**

Once the repository is published, use GitHub's private vulnerability reporting (Security → Advisories → Report a vulnerability). Before publication, contact the maintainer through the private channel identified in the repository profile.

Include:

- affected version or commit;
- a description and impact assessment;
- reproduction steps or a proof of concept using synthetic data;
- whether the issue requires local access, a malicious repository, a malicious skill or a model provider;
- any suggested fix or mitigation;
- whether you intend to disclose publicly.

Do not include real credentials or private source code. Redact logs.

## Response targets

| Stage | Target |
|---|---|
| Acknowledgement | 72 hours |
| Initial assessment and severity | 7 days |
| Fix or mitigation plan | 30 days for high/critical |
| Coordinated disclosure | 90 days, or sooner by agreement |
| Credit | Offered unless declined |

These are targets, not guarantees. If a target is missed, the reporter is told why.

## Scope

In scope:

- policy bypass, capability escalation or approval replay;
- path traversal, symlink/junction escape or unauthorised filesystem access;
- command injection or sandbox escape (T1/T2);
- secret disclosure through logs, artifacts, reports, prompts or exports;
- prompt injection that causes an unauthorised action;
- malicious skill installation or execution;
- unconsented network egress or provider data leakage;
- audit or evidence tampering;
- local API authentication bypass, CSRF or DNS rebinding;
- supply-chain issues in EIS dependencies or release artifacts;
- privacy leaks in the public repository or release artifacts.

Out of scope:

- vulnerabilities requiring physical access or a compromised host with the user's full rights (documented residual risks);
- social engineering outside EIS;
- denial of service by the user against their own machine within configured limits;
- model hallucinations without a policy or data impact;
- issues in third-party scanners or model runtimes (report upstream, but tell us if EIS's integration amplifies the impact);
- findings that require disabling a documented security control.

## Supported versions

Until v1.0, only the latest release receives security fixes. After v1.0, the latest minor and the previous minor receive fixes for high/critical issues.

## Safe harbour

Good-faith research that follows this policy, uses synthetic data and does not access other users' data will not be pursued. Do not test against third-party systems. Do not exfiltrate data beyond what is needed to demonstrate the issue.

## Security design (summary)

EIS does not rely on model instructions for security:

- deterministic, deny-by-default capability policy;
- one execution gateway for filesystem, process and network side effects;
- hash-bound, single-use approvals;
- T1 subprocess isolation and T2 container isolation (T1 is not a security boundary on Windows);
- secret detection and redaction before persistence and egress;
- untrusted-content enveloping for repository text and tool output;
- hash-chained append-only audit events;
- loopback-only API with a per-install bearer token;
- no telemetry and explicit consent for external providers.

Details: [docs/EIS_SECURITY_ARCHITECTURE.md](docs/EIS_SECURITY_ARCHITECTURE.md) and [docs/EIS_THREAT_MODEL.md](docs/EIS_THREAT_MODEL.md).

## Known residual risks

Documented in the threat model, including: a compromised host can read runtime data; T1 on Windows is not a security boundary; redaction can miss novel secret formats; a privileged actor can rewrite the local audit chain; a malicious dependency can be introduced before an advisory exists.

## Hardening guidance for users

- Run with a dedicated OS account where practical.
- Keep the API bound to `127.0.0.1`; never expose it on a LAN.
- Use T2 (Docker/Podman) for untrusted repositories and any executable community skill.
- Grant external model consent only for data classes you accept leaving the machine.
- Review approval diffs; do not approve truncated diffs.
- Keep `EIS_*` secrets in the environment or OS keyring, never in TOML.
- Back up the runtime directory and test a restore.
- Update promptly when an advisory is published.

## Disclosure policy

We publish an advisory with affected versions, impact, fixed version, workaround and credit after a fix is available. Reporters are consulted before publication. We do not pay bounties.
