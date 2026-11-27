# Codex ↔ OpenCode Handoff

**Status:** Temporary coordination file. Not a specification and not a source of truth.
**Created:** 2026-11-20
**Lifecycle:** The project owner deletes this file when the EIS project is finished. Do not delete it now. Do not rename it without owner approval.

## Purpose

Codex and OpenCode may work on this repository in separate sessions. This file is a shared, append-only place to record:

- what was read or changed;
- what was actually verified;
- open questions;
- suggestions for the next agent.

It prevents two agents from silently undoing each other's work. It does **not** grant authority, replace an ADR, or override the code and specifications.

## Protocol

1. **Read this file before contributing.** Also read the relevant source, tests and specs.
2. **Append a new dated entry at the end.** Never edit, reorder or delete another agent's entry.
3. **Label the status** of each entry:
   - `verified` — checked against code, tests or a command;
   - `suggestion` — a proposal, not a fact;
   - `question` — needs an owner or maintainer answer;
   - `blocked` — cannot proceed without a decision or environment fix.
4. **Cite evidence.** Include file paths and the exact command or test when claiming verification.
5. **Separate facts from proposals.** A suggestion must not be written as if it already exists.
6. **Never put secrets or private project data here.** No tokens, credentials, private source, incident content, absolute personal paths, sibling project names or PAM internals. This file may become public.
7. **Respect ownership.** The project owner decides scope and approvals. A note here is not an approval.
8. **Resolve conflicts by authority:** current code and tests, then accepted ADRs, then specifications, then notes. If they disagree, record the conflict and ask the owner.
9. **Keep it short.** Link to files instead of pasting large content.
10. **Do not use this file for durable decisions.** Record those in `docs/` as an ADR or specification update.

## Entry format

```markdown
---

## YYYY-MM-DD — Agent name

**Status:** verified | suggestion | question | blocked

**Context:** one or two lines.

**Files:**
- `path/to/file`

**Notes:**
- Fact or proposal, clearly labelled.

**Questions:**
- Specific question, if any.

**Next:**
- Suggested next action, if any.
```

## Notes

---

## 2026-11-20 — OpenCode

**Status:** verified

**Context:** Added the `learning/` guide set and linked it from the root README. No M1 implementation work was started.

**Files:**
- `learning/README.md`
- `learning/01-python-fundamentals.md`
- `learning/02-tooling-tests-ci.md`
- `learning/03-apis-fastapi-cli.md`
- `learning/04-sqlite-and-memory.md`
- `learning/05-llms-and-ollama.md`
- `learning/06-retrieval-evidence-provenance.md`
- `learning/07-security-and-privacy.md`
- `learning/08-agent-runtime-and-skills.md`
- `learning/09-architecture-map.md`
- `learning/10-glossary.md`
- `learning/CODEX_OPENCODE_HANDOFF.md`
- `README.md`

**Notes:**
- The guides use current M0.2 code for Python, tooling, API/CLI, policy and privacy examples.
- SQLite, retrieval, LLM calls, approvals, sandboxing, specialists and skill evolution are labelled **specified/planned**, not implemented.
- No PAM source, sibling project, Obsidian vault path, project root or model tag was touched.
- No commit, remote or push was created.
- The repository has no commits; files are untracked.

**Questions:**
- None blocking. The owner still needs to decide when to authorise the first commit, remote, CI run and branch protection.

**Next:**
- Read `docs/M0.2_DECISIONS.md` and `learning/README.md` before adding M1 claims.
- Keep the implemented/planned distinction in any new guide.

---

## 2026-11-20 — OpenCode (verification)

**Status:** verified

**Context:** Post-creation checks for the learning set.

**Commands and results:**
- Markdown link check across the repository: 42 Markdown files, 0 broken relative links.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv run pytest -q`: 37 passed.
- `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy src`: pass.
- Mermaid structural check: 9 fenced diagrams, all start with a recognised Mermaid diagram type. A full Mermaid renderer (`mmdc`) is not installed in this environment, so visual rendering was not verified.

**Notes:**
- `pyproject.toml` was updated only to add `learning` to Ruff's Markdown exclusion list; no source code, tests, policies or CI files were changed by the learning-set task.
- No commit, remote or push was created.

**Questions:**
- None.

---

## 2026-11-20 — OpenCode (M1 policy slice)

**Status:** verified

**Context:** Implemented the first M1 slice: a typed, deterministic, deny-by-default policy engine and decision audit contract. No execution gateway, approval workflow, database, model provider or agent loop was started.

**Files:**
- `src/eis/policy.py`
- `src/eis/cli.py`
- `src/eis/api.py`
- `schemas/policy.schema.json`
- `schemas/policy-decision.schema.json`
- `policies/default.json`
- `tests/test_policy_baseline.py`
- `tests/test_policy_engine.py`
- `docs/EIS_POLICY_ENGINE.md`
- `docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md`
- `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `docs/EIS_API_SPECIFICATION.md`
- `README.md`, `CHANGELOG.md`
- `learning/` guides, glossary and README
- `pyproject.toml`, `uv.lock`

**Verified:**
- `uv run pytest -q`: 60 passed on Python 3.12.13; 60 passed on Python 3.11 against the freshly built wheel.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped because it is not on PyPI.
- `uv run eis policy-check`: `policy_version=2026.11-m1a`, `probe=deny`, `reason=default_deny`.
- `uv build` and `scripts/verify_wheel.py`: pass; policy and schemas are packaged.
- `uv run eis privacy-check`: `privacy: ok`.

**Spec note:** This slice intentionally supports only `allow`/`deny`. The full execution model's `require_approval`, obligations, path resolution, gateway, approval binding and persisted hash-chained audit remain deferred. No model or network dependency exists in the policy module.

**Questions:**
- The next slice needs an owner decision on resource registration semantics (how project IDs and roots are represented) before path resolution is implemented.

**Follow-ups:**
- Implement the resource resolver and decision obligations next.
- Then approval-required decisions with action/diff binding.
- Persist audit records only after the SQLite and chain contracts are designed.

---

## 2026-11-20 — OpenCode (provider configuration slice)

**Status:** verified

**Context:** Implemented explicit OpenCode-compatible model-provider configuration and a Chat Completions client. No agent loop, model gateway audit, approvals, database or execution gateway was started. The policy engine remains unchanged: model output cannot authorize actions.

**Files:**
- `src/eis/config.py`
- `src/eis/cli.py`
- `src/eis/api.py`
- `src/eis/adapters/__init__.py`
- `src/eis/adapters/models/__init__.py`
- `src/eis/adapters/models/base.py`
- `src/eis/adapters/models/opencode.py`
- `.env.example`
- `tests/test_config.py`, `tests/test_opencode_provider.py`, `tests/test_api.py`, `tests/test_cli.py`, `tests/conftest.py`
- `docs/EIS_MODEL_PROVIDER_CONFIGURATION.md` (new)
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-033), `docs/EIS_TECHNICAL_DESIGN.md`, `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_PRODUCT_SPEC.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_SYSTEM_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`
- `learning/README.md`, `01`, `02`, `03`, `04`, `05`, `07`, `09`, `10`
- `pyproject.toml`, `uv.lock`

**Sources consulted (2026-11-20):**
- `https://opencode.ai/docs/console/models/`
- `https://opencode.ai/docs/zen`
- `https://opencode.ai/docs/models`

**Verified:**
- `uv run pytest -q`: 80 passed on Python 3.12.13; 80 passed on Python 3.11 against the freshly built wheel.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped because it is not on PyPI.
- `uv run eis provider-check` with local fake settings: `provider=opencode configured=true model=... base_url=... api_key=set`; no key printed.
- Missing-key configuration: exit 1 with a variable-name-only error; no key printed.
- `uv build` and `scripts/verify_wheel.py`: pass.
- `uv run eis privacy-check`: `privacy: ok`.

**Spec note:** The adapter uses only OpenAI-compatible Chat Completions: `POST {base_url}/chat/completions`, Bearer auth, JSON messages, non-streaming. Official docs list some models only on `/responses`, `/messages`, image or system-one endpoints; those are not implemented. The base URL is required configuration, not a default.

**Questions:**
- Which exact OpenCode model/plan and base URL the owner will use is unknown; configuration is intentionally left local.
- Whether the `x-opencode-session` routing header is required for the owner's plan is unresolved; it was not added without official Chat Completions confirmation.
- Native `/responses` and `/messages` support may be needed if the chosen model is not a Chat Completions model.

**Follow-ups:**
- Add model-gateway call records and egress consent after the gateway contract.
- Add provider adapters only for documented protocols the owner selects.
- Keep API authentication unresolved until its M1 slice; `/api/v1/meta` is currently unauthenticated.

---

## 2026-11-20 — OpenCode (provider-neutral gateway)

**Status:** verified

**Context:** Implemented a provider-neutral text gateway for OpenCode, OpenAI, Google Gemini, Anthropic Claude and xAI Grok, with a typed registry, consent gate and content-free audit records. No agent loop, tool execution, streaming or execution gateway was started.

**Files:**
- `src/eis/config.py`
- `src/eis/cli.py`, `src/eis/api.py`
- `src/eis/adapters/models/{base,chat_completions,opencode,openai,xai,gemini,anthropic,registry}.py`
- `src/eis/core/{consent,audit,model_gateway}.py`
- `.env.example`
- `tests/{test_config,test_opencode_provider,test_provider_adapters,test_api,test_cli,test_import_contracts}.py`, `tests/conftest.py`
- `docs/EIS_MODEL_PROVIDER_CONFIGURATION.md`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-034)
- `docs/EIS_TECHNICAL_DESIGN.md`, `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_PRODUCT_SPEC.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_SYSTEM_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_API_SPECIFICATION.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`, `docs/M0.2_DECISIONS.md`
- `README.md`, `CHANGELOG.md`
- `learning/README.md`, `03`, `04`, `05`, `07`, `09`, `10`
- `pyproject.toml`, `uv.lock`

**Sources consulted (2026-11-20):**
- OpenCode: `https://opencode.ai/docs/console/models/`, `https://opencode.ai/docs/zen`
- OpenAI: `https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create`, `https://developers.openai.com/api/reference/overview`
- Gemini: `https://ai.google.dev/api/generate-content`, `https://ai.google.dev/api`
- Anthropic: `https://docs.anthropic.com/en/api/overview`, `https://platform.claude.com/docs/en/api/messages/create`
- xAI: `https://docs.x.ai/developers/rest-api-reference/inference`, `https://docs.x.ai/developers/model-capabilities/legacy/chat-completions`

**Verified:**
- `uv run pytest -q`: 107 passed on Python 3.12.13; 107 passed on Python 3.11 against the freshly built wheel.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped because it is not on PyPI.
- `uv run eis provider-check` with fake OpenAI settings: `provider=openai configured=true model=test-model base_url=https://api.openai.com/v1 api_key=set consent=C0,C1`; no key printed.
- Adapter tests use `httpx.MockTransport`; no live call and no real key.
- `uv build` + wheel resource verification: pass.
- `uv run eis privacy-check`: `privacy: ok`.

**Spec note:** Chat Completions is used for OpenCode/OpenAI/xAI; Gemini uses `generateContent`; Anthropic uses Messages. Text only, no streaming/tools. Consent defaults to no call; C3 is always denied. Audit records contain no request content, response text or keys. No persistent consent store or audit hash chain.

**Questions:**
- Exact owner model/plan and whether the chosen models use Chat Completions, Responses, Interactions or another API version.
- Whether `x-opencode-session` is required for the owner's OpenCode plan.
- OpenAI/xAI Responses, Gemini Interactions and newer Anthropic API versions remain unimplemented.

**Follow-ups:**
- Persistent consent store and hash-chained audit persistence.
- Agent-loop integration and streaming after the execution policy slice.
- API authentication for `/api/v1/meta`.

---

## 2026-11-21 — OpenCode (durable SQLite consent and audit)

**Status:** verified

**Context:** Added durable SQLite persistence behind the existing provider gateway interfaces. No agent loop, execution gateway, approvals or GUI work was started.

**Files:**
- `src/eis/storage/{__init__,db,consents,audit,gateway}.py`
- `src/eis/storage/migrations/{env.py,script.py.mako,versions/0001_provider_persistence.py}`
- `src/eis/core/consent.py`, `src/eis/config.py`, `src/eis/cli.py`
- `tests/test_storage.py`
- `pyproject.toml`, `uv.lock`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-035), `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_MODEL_PROVIDER_CONFIGURATION.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`, `learning/{README,02,04,05,07,09}.md`

**Notes:**
- Alembic baseline creates `consents` (scope/subject/endpoint/data classes/purpose/retention flag/expiry/revocation), `egress_decisions` and content-free `model_calls`, using WAL, foreign keys, `busy_timeout=5000`, `synchronous=NORMAL` and `STRICT` tables.
- `ConsentStore` persists grants, environment bootstrap, active lookup, revocation and listing. `EIS_MODEL_CONSENT_CLASSES` is insert-only by consent ID, so a revoked environment consent is not silently re-granted on restart.
- `SqliteAuditSink` implements the existing `AuditSink` contract. `build_sqlite_gateway` wires SQLite consent and audit; the JSONL sink remains for diagnostics.
- `eis consent grant|list|revoke` manages durable consent and prints no keys.
- Audit rows exclude prompts, response text, keys and raw provider bodies; a raw-bytes test enforces this.
- The provider tables are **not** hash-chained; `audit_events` and `ExecutionGateway` remain planned. Hash chaining is still required before claiming tamper evidence.

**Commands and results:**
- `uv run pytest -q`: 115 passed on Python 3.12.13.
- `uv run pytest tests/test_storage.py -q`: 8 passed.
- Consent CLI smoke test in a temporary data directory: grant and list returned the new `cns_...` ID; no key printed.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run eis privacy-check`: `privacy: ok`.
- Migration, restart, revocation, expiry, data-class scope, C3 denial, denial-before-network and redaction tests use temporary SQLite files and `httpx.MockTransport` only; no live call and no real key.

**Questions:**
- M0.2 clean-clone install, GitHub CI and branch protection remain unverified; no remote and no commits exist.

**Next:**
- Add hash-chained `audit_events` and SQLite backup/retention policy before claiming tamper evidence.
- Then the execution gateway and approval workflow, keeping the policy engine authoritative.

---

## 2026-11-21 — OpenCode (tamper-evident audit chain)

**Status:** verified

**Context:** Added the append-only, hash-linked `audit_events` chain over consent, egress and model-call writes. No agent loop, execution gateway, approvals or GUI work was started.

**Files:**
- `src/eis/core/audit_chain.py`, `src/eis/storage/audit_chain.py`
- `src/eis/storage/migrations/versions/0002_audit_chain.py`
- `src/eis/storage/{audit,consents,db}.py`, `src/eis/cli.py`
- `tests/test_audit_chain.py`, `tests/test_storage.py`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-036), `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_MODEL_PROVIDER_CONFIGURATION.md`, `docs/EIS_POLICY_ENGINE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`, `scripts/verify_wheel.py`, `learning/{README,01,02,04,05,07,09,10}.md`

**Notes:**
- `audit_events` follows the documented schema: `seq`, stable `id`, `occurred_at`, `actor`, `event_type`, `subject_type`, `subject_id`, `decision`, `payload_json`, `prev_hash`, `hash`.
- Canonical algorithm v1 is documented in code and docs: UTF-8 sorted-key compact JSON; `SHA-256(prev_hash || canonical event without hash/prev_hash)`; genesis is 64 zeros; `seq` starts at 1.
- Database triggers reject updates, deletes, sequence gaps and broken previous-hash links.
- Chain boundaries: `consent_granted`, `consent_revoked`, every `egress_decision` and every `model_call`. Projection rows and chain events use one SQLite transaction with `BEGIN IMMEDIATE`.
- `AuditIntegrityGate.require_intact()` is the interface future approval paths must call. Every append also re-verifies the chain, so a broken chain blocks new audit writes and therefore provider calls.
- `eis audit verify` prints `audit_chain=ok events=N` or a failure reason/sequence without payloads.
- This is tamper evidence, not tamper-proof storage. Triggers can be dropped by a direct database actor, tail truncation is undetectable without an external anchor, and `integrity_anchors` is not implemented.
- No API keys, prompts or response text are stored in payloads; tests query all SQLite text columns and raw DB/WAL bytes.

**Commands and results:**
- `uv run pytest -q`: 127 passed on Python 3.12.13.
- Python 3.11 from the freshly built wheel in an isolated uv environment: 127 passed.
- `uv run pytest tests/test_audit_chain.py -q`: 12 passed.
- CLI smoke test in a temporary data directory: `audit_chain=ok events=0`, then `consent grant` and `audit_chain=ok events=1`; no key printed.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run pip-audit`: no known vulnerabilities; local `eis` is skipped because it is not on PyPI.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv build` + `scripts/verify_wheel.py`: pass; migration `0002_audit_chain` is packaged.
- Markdown link check: 44 files, 0 broken relative links.
- No commit, remote, push, live provider call or `.env` read.

**Questions:**
- M0.2 clean-clone install, GitHub CI and branch protection remain unverified; no remote and no commits exist.

**Next:**
- Add `integrity_anchors` / external checkpointing and a startup or scheduled integrity check with alerting.
- Then execution gateway and approval paths, which must call `require_intact()` first.

---

## 2026-11-21 — OpenCode (chained policy decisions)

**Status:** verified

**Context:** Persisted deterministic policy decisions in the existing audit chain through a new service boundary. The policy evaluator remains pure. No approvals, execution gateway or agent loop work was started.

**Files:**
- `src/eis/core/policy_service.py`, `src/eis/storage/policy_decisions.py`
- `src/eis/storage/audit_chain.py` (idempotent caller-supplied event IDs)
- `src/eis/policy.py` (reject drive-letter path prefixes)
- `src/eis/cli.py` (`policy-evaluate`)
- `tests/test_policy_persistence.py`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-037), `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_POLICY_ENGINE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`, `learning/{README,01,02,03,04,07,09,10}.md`

**Notes:**
- `PolicyService` evaluates with `PolicyEngine`, calls a `DecisionSink`, and returns the decision only after the sink succeeds. Sink failure propagates, so callers cannot use an unaudited decision.
- `SqlitePolicyDecisionSink` appends one `policy_decision` event for allow, explicit deny and default deny.
- Event ID is the deterministic decision ID. Repeating the same request/policy returns the existing event and does not append a duplicate; a different event under the same ID fails closed.
- Payload: decision ID, effect, reason, rule ID, policy version/hash, inputs hash, principal, action, canonical resource and context source. Context attributes and task IDs are omitted; `inputs_hash` still binds them.
- Absolute POSIX paths, UNC paths and drive-letter prefixes are rejected before evaluation. Relative path resources are stored as the canonical policy reference.
- `eis policy-evaluate` uses the audited service; `eis policy-check` remains a read-only diagnostic and creates no event.
- A queryable `policy_decisions` projection, approvals and execution outcomes remain pending. Only service-evaluated decisions are chained; direct `PolicyEngine.evaluate` calls stay pure and unaudited.

**Commands and results:**
- `uv run pytest -q`: 134 passed on Python 3.12.13.
- `uv run pytest tests/test_policy_persistence.py -q`: 7 passed.
- Python 3.11 from the freshly built wheel in an isolated uv environment: 134 passed.
- CLI smoke in a temporary data directory: two identical `policy-evaluate` calls returned the same decision/event ID; `audit verify` reported `events=1`. A subsequent `policy-check` added no event.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped because it is not on PyPI.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv build` + `scripts/verify_wheel.py`: pass.
- Markdown links: 44 files, 0 broken. `uv lock --check`: pass.
- No commit, remote, push, live provider call or `.env` read.

**Questions:**
- M0.2 clean-clone install, GitHub CI and branch protection remain unverified; no remote and no commits exist.

**Next:**
- Add a queryable `policy_decisions` projection and the resource resolver, keeping the chain event as the integrity source.
- Approval/execution paths must call `AuditIntegrityGate.require_intact()` and use `PolicyService`, not the raw engine.

## 2026-11-21 — Codex (projection, approvals and bounded execution)

**Status:** implemented; focused checks pass; full-suite verification blocked by a reproducible hang in the existing audit/provider tests on this workstation.

**Context:** User asked Codex to complete the items identified as skipped in OpenCode's M1 policy persistence report, then provide the next OpenCode prompt.

**Files added/changed:** `src/eis/storage/migrations/versions/0003_approvals_policy_projection.py`, `src/eis/storage/policy_decision_store.py`, `src/eis/core/approvals.py`, `src/eis/storage/approvals.py`, `src/eis/core/execution.py`, CLI approval and policy-decision commands, `tests/test_approval_execution.py`, ADR-038, roadmap and permission-model docs.

**Implementation:** Queryable append-only policy decision projection with legacy-event backfill; content-free audit events for proposal/grant/reject/consume/execution; immutable digest-bound A3 file-write proposals with TTL and one-time use; policy-checked project writes with path guards, backup and atomic replace; fixed-profile T1 runner for trusted projects only. T2/untrusted runs fail closed. CLI can list/show/propose/grant/reject/execute; grant confirms the exact shown digest.

**Verification:** `uv run pytest tests/test_approval_execution.py tests/test_policy_persistence.py -q`: 13 passed. `uv run ruff check .`: pass. `uv run ruff format --check .`: pass. `uv run mypy src`: pass. CLI approval and policy-decision help commands render. Full pytest stalls at `tests/test_audit_chain.py::test_integrity_failure_blocks_audit_and_provider_operations` (also when isolated), and a run excluding that case stalls later in the audit-chain suite; investigate before claiming full-suite pass. No commit, remote, push or `.env` read.

**OpenCode next:** Review the Codex changes and first diagnose/fix the audit/provider test hang; run the complete suite, security/docs/wheel checks, fix regressions without reverting the intended scope, audit approval digest and CLI project-root binding behavior, test command-profile output/timeout caps, then report remaining M1 gaps. Do not claim M1 complete: no session/task agent loop, T2 sandbox, external integrity anchor, broad resource resolver or M0.2 clean-clone/CI verification exists.

---

## 2026-11-21 — OpenCode (review of projection, approvals and bounded execution)

**Status:** verified

**Context:** Reviewed Codex's migration 0003, approval/execution gateway, CLI flows and tests. The reported full-suite hang was not reproducible on this workstation. Fixed provable binding, redaction, expiry, path and profile-launch defects without reverting the feature scope.

**Files:**
- `src/eis/core/approvals.py`, `src/eis/storage/approvals.py`
- `src/eis/core/execution.py`
- `src/eis/storage/migrations/versions/0003_approvals_policy_projection.py`
- `src/eis/cli.py`
- `tests/conftest.py`, `tests/test_approval_execution.py`, `tests/test_storage.py`
- `README.md`, `CHANGELOG.md`, `scripts/verify_wheel.py`
- `docs/EIS_POLICY_ENGINE.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md`, `docs/EIS_ARCHITECTURE_DECISIONS.md`
- `learning/{README,01,02,03,04,05,07,08,09,10}.md`

**Findings and fixes:**
- **Hang:** not reproducible. `socket.socketpair()`, default `asyncio.run(asyncio.sleep(0))` and the isolated audit/provider tests all completed. The full suite passed repeatedly. This is consistent with a restricted Windows sandbox blocking the Proactor loop's socket pair, not a regression. Added a Windows selector-loop test fixture as a reproducible workaround and documented it.
- **Migration test:** stale expectation `0002_audit_chain`; updated to `0003_approvals_policy_projection`, new tables/triggers and migration 0003 in wheel verification. Added a real 0002→0003 backfill test.
- **Approval binding:** digest now includes `policy_decision_id` as well as version, target, root, content hashes, diff, summary and expiry.
- **Secret handling:** approval rejection notes are stored in the private `approvals.decision_note`; only `reason_sha256` enters the audit chain.
- **Expiry/state:** execute checks status/expiry before policy/path work; expired approvals are hidden from the pending list; expiry and one-way transition tests added.
- **Root/path handling:** root hashes normalize path case on Windows; extra reserved secret/config paths (`.ssh`, `.aws`, `.gnupg`, `keys`, `secrets`, `.env.*`) are denied; root binding and atomic-replace tests added.
- **Profiles:** launch `OSError` becomes a recorded `executable_unavailable`; output cap reports `output_truncated`; success, cap, timeout, missing profile/executable, T2 and untrusted tests added.
- **CLI:** added propose/show/list/grant/wrong-root/execute/reject flow coverage; invalid principals/project IDs fail with an approval error rather than a traceback.
- **Audit/run records:** tests assert approval, execution-start/completion and policy events, plus append-only execution rows; execution payloads exclude raw diff/content.

**Commands and results:**
- `uv run pytest -q`: **155 passed** on Python 3.12.13.
- Python 3.11 from the freshly built wheel: **155 passed**.
- Isolated `tests/test_audit_chain.py tests/test_provider_adapters.py tests/test_opencode_provider.py`: **35 passed**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped as unpublished.
- `uv build` + `scripts/verify_wheel.py`: pass; migration 0003 packaged.
- Markdown links: 44 files, 0 broken. `uv lock --check`: pass.
- `socket.socketpair()` + default asyncio and explicit selector-loop checks: both pass on this Windows host.
- No commit, remote, push, live provider call or `.env` read.

**Questions:**
- The sandbox-specific Proactor stall cannot be reproduced here; the selector-loop fixture is a test workaround, not a product guarantee.
- Expired approvals remain `pending` in storage (hidden from the list) until an expiry transition is designed.

**Next:**
- Add an expiry transition/job, broader approval actions, the session/task agent loop, T2 isolation and external audit anchoring.
- M0.2 clean-clone install, GitHub CI execution and branch protection remain unverified; no remote or commits exist.

---

## 2026-11-21 — OpenCode (minimal session/task runtime)

**Status:** verified

**Context:** Added durable sessions/tasks and a minimal single-agent runtime that uses the existing policy service, model gateway and audit chain. The runtime performs one completion and stops; no tool dispatch or approval expansion was added.

**Files:**
- `src/eis/core/runtime.py`, `src/eis/storage/sessions.py`
- `src/eis/storage/migrations/versions/0004_sessions_tasks.py`
- `src/eis/cli.py` (`session`, `task`)
- `tests/test_runtime.py`, `tests/test_storage.py`
- `scripts/verify_wheel.py`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-039), `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`, `learning/{README,01,02,03,04,05,07,08,09,10}.md`

**Notes:**
- Migration 0004 adds `sessions` and `tasks` with explicit states, timestamps, optional project association and transition triggers.
- Task goals and session titles are private local input data; audit events carry only hashes. No message history or response text is persisted.
- `SessionTaskRuntime` creates/resumes sessions and tasks, evaluates `task.run` through `PolicyService`, calls `ModelGateway` once, and leaves successful output `blocked` at `human_review_required`.
- Model output is returned only as an in-memory untrusted proposal. It is not parsed, persisted, executed, approved or printed by default; `--show-proposal` is an opt-in sanitized preview.
- Consent denial, policy denial and provider errors transition the task to `failed` with content-free reasons. Corrupted chains block before the model call.
- Restart/resume loads SQLite state. A `created` task can run; an interrupted `running` task becomes `failed` with `interrupted` and is not re-sent.
- **Security finding:** `.env.example` contained a real-looking credential-shaped value. It was replaced with a placeholder. No credential value is reproduced in docs or this handoff.

**Commands and results:**
- `uv run pytest -q`: **163 passed** on Python 3.12.13.
- Python 3.11 from the freshly built wheel: **163 passed**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped as unpublished.
- `uv build` + `scripts/verify_wheel.py`: pass; migration 0004, runtime and session storage are packaged.
- Markdown links: 44 files, 0 broken. `uv lock --check`: pass.
- Full suite completed with the Windows selector-loop test fixture; the earlier sandbox Proactor stall was not reproducible.
- No commit, remote, push, live provider call or `.env` read.

**Questions:**
- The runtime proposal is not durable after restart; a future message/proposal record must define privacy and retention.
- Task goals are private local rows, not content-free; they need retention and backup handling.

**Next:**
- Add task steps/action schemas, a bounded plan/act loop, durable proposal review, T2 isolation and external audit anchoring.
- M0.2 clean-clone install, GitHub CI execution and branch protection remain unverified; no remote or commits exist.

---

## 2026-11-21 — OpenCode (runtime review)

**Status:** verified

**Context:** Reviewed the minimal session/task runtime against its migration, lifecycle, failure, privacy and CLI requirements. Fixed one reproducible lifecycle defect and added missing failure/privacy evidence.

**Files:**
- `src/eis/storage/sessions.py`
- `tests/test_runtime.py`
- `README.md`, `CHANGELOG.md`, `learning/README.md`

**Findings and fixes:**
- **Fixed:** resuming an interrupted `running` task whose session had already been closed raised an invalid session-transition error and left the task `running`. Task transitions now leave a `closed` session closed while still recording the task failure. Added a regression test.
- **Verified:** migration 0004 session/task triggers reject invalid transitions; task/session state changes and audit events commit or roll back together.
- **Verified:** restart/resume runs `created` tasks and marks interrupted `running` tasks `failed` with `interrupted` without a second provider call.
- **Added:** provider-failure test (`provider_error`, no response hash, one model-call record); corrupted-chain task/session rollback test; task-transition trigger test; closed-session interrupted-task test.
- **Verified:** policy denial, consent denial and corrupted-chain behavior make no provider call and persist content-free reasons.
- **Verified:** CLI default `task submit` and `task status` never print task goals or responses; `--show-proposal` is opt-in, sanitizes control characters, caps the preview and performs no action. Model output is not parsed or dispatched; approval/execution tables remain empty.
- **Verified:** `.env.example` now contains placeholders only; no credential value is reproduced.

**Commands and results:**
- `uv run pytest -q`: **167 passed** on Python 3.12.13.
- Python 3.11 from the freshly built wheel: **167 passed**.
- `uv run pytest tests/test_runtime.py -q`: **12 passed**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped as unpublished.
- `uv build` + `scripts/verify_wheel.py`: pass; migration 0004 and runtime modules packaged.
- Markdown links: 44 files, 0 broken. `uv lock --check`: pass.
- No commit, remote, push, live provider call or `.env` read.

**Questions:**
- Proposals remain intentionally non-durable; a future message/proposal record needs explicit privacy and retention rules.
- Task goals remain private local rows, not content-free.

**Next:**
- Add task steps/action schemas, durable proposal review, broader approvals, T2 isolation and external audit anchoring.
- M0.2 clean-clone install, GitHub CI execution and branch protection remain unverified; no remote or commits exist.

---

## 2026-11-21 — OpenCode (read-only project tools)

**Status:** verified

**Context:** Added four explicitly invoked read-only project tools to the durable task runtime. Model output remains unparsed and cannot dispatch actions.

**Files:**
- `src/eis/core/project_tools.py`
- `src/eis/core/runtime.py`, `src/eis/cli.py`
- `tests/test_project_tools.py`, `tests/test_runtime.py`
- `scripts/verify_wheel.py`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-040), `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`, `learning/{README,02,03,04,05,07,08,09}.md`

**Notes:**
- Registry: `project.list`, `project.list_files`, `project.read_text`, fixed `git.status`.
- Strict Pydantic tool/argument schemas reject unknown names/fields, wrong types, duplicate JSON keys, absolute/traversal/drive-letter paths and known secret-file paths.
- The runtime principal is injected by `SessionTaskRuntime`; it is not a tool-call field. Each call uses `PolicyService` and canonical `global`, `project`, `path` resources before execution.
- Read-only work starts only after the policy decision and `tool_invocation_started` event are persisted. Denied/started/completed/failed tool events contain IDs, hashes, status, byte counts and sanitized error codes only. Decision/start/outcome audit failure blocks work or withholds the result.
- Root/path checks reject symlink/junction components and enforce containment. Lists are limited to 100 results and 5000 scanned entries; reads are limited to 32 KiB; Git status uses fixed no-shell args, 5s timeout, 64 KiB cap and returns branch/status counts only.
- CLI entry is `eis task invoke-tool <session_id> <root> --call-json ...`; it prints hashes/status only. No file contents or raw Git output are persisted or printed by default. The root is bound ephemerally to the session project ID; no durable multi-project registry was added.
- Model completion text is not parsed into tool calls. The explicit CLI/runtime path accepts validated calls only; no writes, deletes, arbitrary commands, network, approval, or provider-native tool calling was connected.

**Commands and results:**
- `uv run pytest -q`: **178 passed** on Python 3.12.13.
- Python 3.11 from the freshly built wheel: **178 passed**.
- `uv run pytest tests/test_project_tools.py tests/test_runtime.py -q`: **23 passed**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped as unpublished.
- `uv build` + `scripts/verify_wheel.py`: pass; project-tools module and migration 0004 are packaged.
- Markdown links: 44 files, 0 broken. `uv lock --check`: pass.
- Tests cover strict schema, allow/deny, chain corruption, audit-write failure before work, traversal, symlink, secret-file exclusion, bounded listing/read/Git output, Git failure/timeout, CLI privacy and model-output non-dispatch.
- No `.env` read, live provider call, external service, commit, remote or push.

**Questions / remaining risks:**
- File contents are returned in memory to the explicit caller; future model integration must continue treating tool output as untrusted and must not persist it as messages without privacy/retention design.
- Filesystem checks have ordinary local TOCTOU limits; this is read-only and not T2 isolation.
- The Git result omits paths and content; only status counts and branch are returned.
- M1 remains incomplete: model-driven tool dispatch, broader approvals, T2 isolation and external audit anchoring remain pending. M0.2 clean-clone, GitHub CI and branch-protection evidence remain unverified.

**Next:**
- Design task-step/action schemas and human-reviewed proposal persistence before any model-to-tool bridge.
- Add durable project-root registration and T2 isolation before broadening access.

---

## 2026-11-22 — OpenCode (durable project roots and race-resistant reads)

**Status:** verified

**Context:** Replaced the invocation-time root argument with a durable, explicit local-user project-root registry, and moved read/list access onto race-resistant primitives. The session project ID is now the only root selector; model output and tool-call JSON cannot name a filesystem root.

**Files:**
- `src/eis/core/project_roots.py` (new): root record/protocol, canonical path, filesystem identity, link/junction/reserved/overlap validation and status classification.
- `src/eis/core/safe_access.py` (new): POSIX descriptor walk with `O_NOFOLLOW`/`dir_fd`; Windows component checks, opened-handle identity comparison and post-read re-check; bounded reads/listings.
- `src/eis/storage/migrations/versions/0005_project_roots.py` (new): `project_roots(project_id PK, root_path UNIQUE, root_identity, created_at, updated_at)`.
- `src/eis/storage/project_roots.py` (new): register/get/list/remove with idempotent same-path re-registration, overlap/duplicate checks and content-free root events.
- `src/eis/core/project_tools.py`: registry-backed project resolution and safe-access reads/listings; sanitized `SafeAccessError` outcomes.
- `src/eis/cli.py`: `project register|list|remove`; `task invoke-tool` no longer accepts a root argument.
- `tests/test_project_roots.py` (new), `tests/test_project_tools.py`, `tests/test_storage.py`.
- `scripts/verify_wheel.py`: migration 0005 and new modules.
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-041), `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_THREAT_MODEL.md`, `docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`.
- `README.md`, `CHANGELOG.md`, `learning/{README,07,08,09}.md`.

**Notes:**
- Registration rejects missing paths, files, filesystem roots, CLI-configured data/config locations, duplicate/overlapping roots, invalid project IDs and any symlink/junction component. Registration is idempotent only for the same project + canonical path + identity; changing a project's root requires explicit removal first.
- Each access resolves the session project ID through `project_roots`, re-checks the root path/identity, and fails closed on missing (`project_unavailable`) or replaced (`project_root_replaced`) roots. Tests cover moved, restored and replaced roots.
- POSIX: root and every child are opened relative to the verified root descriptor with `O_NOFOLLOW`; containment and the read refer to the same opened objects. Windows: every component is checked for links/reparse points, the opened handle identity is compared with the pre-open identity, the read is bounded, and the root/path are re-checked afterwards. The residual Windows check/open swap window is documented; this is not claimed as complete TOCTOU protection.
- Reads remain capped at 32 KiB, listings at 100 results/5000 scanned entries, Git at a fixed no-shell command with 5 s timeout and 64 KiB output. Failure codes are stable and sanitized (`symlink_denied`, `path_escape_denied`, `path_changed`, `not_a_regular_file`, `not_a_directory`, `file_too_large`, etc.).
- Root registration/removal appends `project_root_registered`/`project_root_removed` with project ID, canonical-path SHA-256 and identity hash only. CLI `project list` prints hashes/status, never the absolute path. Policy decisions, audit-before-work and content-free tool events are unchanged; default policy remains deny-by-default.
- No model-to-tool bridge, writes, commands, network, approvals, T2 isolation or external audit anchoring was added.

**Commands and results:**
- `uv run pytest -q`: **189 passed, 1 skipped** (POSIX-only symlink-swap test skipped on Windows) on Python 3.12.13.
- Fresh wheel on isolated Python 3.11: **189 passed, 1 skipped**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: pass.
- `uv run eis privacy-check`: `privacy: ok`.
- `uv run pip-audit`: no known vulnerabilities; local `eis` skipped as unpublished.
- `uv build` + `scripts/verify_wheel.py`: pass; migration 0005, `project_roots.py`, `safe_access.py` and storage module packaged.
- Markdown link check: 43 project Markdown files, 0 broken relative links. `uv lock --check`: pass.
- Tests cover registry persistence/restart, idempotent registration, missing/file/reserved/filesystem-root/duplicate/overlap rejection, symlink/junction rejection, moved/replaced/unavailable roots, registry audit failure rollback, migration 0004 → 0005 without fabricated roots, file growth during read, Windows replacement between stat/open and POSIX symlink swap during open, plus the existing policy/audit/CLI privacy tests.
- No `.env` read, live provider call, external service, commit, remote or push.

**Questions / remaining risks:**
- Windows retains a narrow local swap window because the Python standard library has no root-relative `openat`; a local actor with write access to the root is outside this slice's security boundary. POSIX descriptor walks do not have this specific gap.
- Replacement detection depends on stable `st_dev`/`st_ino`; filesystems that report zero or unstable identities weaken it.
- `project_roots` is intentionally minimal; M2 must extend it by migration rather than assuming the planned label/exclusion/scan shape.
- M1 remains incomplete: model-driven tool dispatch, broader approvals, T2 isolation and external audit anchoring remain pending. M0.2 clean-clone, GitHub CI and branch-protection evidence remain unverified.

**Next:**
- Design task-step/action schemas and human-reviewed proposal persistence before any model-to-tool bridge.
- Add T2 isolation and external audit anchoring; extend the root registry only through a new migration.

---

## 2026-11-23 — OpenCode (strict-envelope bounded model-to-tool loop)

**Status:** verified

**Context:** Connected the durable session/task runtime to the registered, policy-gated read-only project tools through a bounded, strictly typed model-to-tool loop (ADR-042). `ModelGateway` stays text-only and model output stays untrusted; identity, root and policy fields never come from the model.

**Files:**
- `src/eis/core/model_envelope.py` (new), `src/eis/core/runtime.py`, `src/eis/cli.py`
- `tests/test_model_loop.py` (new), `tests/test_runtime.py`, `tests/test_project_tools.py`
- `scripts/verify_wheel.py`
- `docs/EIS_ARCHITECTURE_DECISIONS.md` (ADR-042 plus index rows for ADR-041/042), `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md`, `docs/EIS_SECURITY_ARCHITECTURE.md`, `docs/EIS_MODEL_PROVIDER_CONFIGURATION.md`, `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_PRIVACY_AND_DATA_GOVERNANCE.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`
- `README.md`, `CHANGELOG.md`, `learning/{README,05,07,08,09,10}.md`

**Notes:**
- Envelope accepts only `{"kind":"answer","content":...}` or `{"kind":"tool_call","tool":...,"arguments":{...}}`: exact key sets, duplicate JSON keys rejected, 8192-byte cap, blank answers rejected, and forbidden argument keys (`project_id`, `principal`, `root`, `root_path`, `policy`, `session_id`, `task_id`, `capability`, `approval_id`, ...) rejected with content-free stop codes.
- Session, task, principal and project ID are injected from durable runtime state; the project ID resolves through the ADR-041 root registry. No authority or filesystem root is read from model output.
- Loop bounds per run: 3 completions, 2 tool calls, 60 s wall clock (`asyncio.wait_for`), 32 KiB input, 4000-character tool feedback. Stops on malformed output, policy denial, consent/provider failure, a repeated identical tool call, or limit exhaustion. An interrupted `running` task is never auto-resumed: `run_task` refuses it and `resume_task` records `interrupted`.
- Tool requests reuse the existing typed registry and `PolicyService`; the decision and `tool_invocation_started` are persisted before filesystem/Git work, and a policy or audit failure prevents execution. `ToolRequestError` messages are mapped to a fixed code set before becoming stop reasons so no input text reaches audit or CLI.
- Privacy: prompts, responses, file contents and tool results stay in memory; run counts merge into the existing `usage_json`/audit `usage` payload (no migration). Default CLI prints `answer=untrusted_model_generated_no_action_taken`; a labeled `model_generated_answer_begin/end` block appears only with `--show-proposal`.
- Environment note: this machine's Application Control intermittently blocked `.venv`/site-packages DLL loads (mypy's `librt`, occasionally `python.exe` itself); retries succeeded. Not a repository issue.

**Commands and results:**
- `.venv\Scripts\python.exe -m pytest -q`: **216 passed, 1 skipped** (POSIX openat test) on Python 3.12.13.
- Fresh wheel on isolated Python 3.11.15 (`uv venv --python 3.11`, wheel + pytest installed offline): **216 passed, 1 skipped**; `eis` imported from site-packages, not `src/`.
- `ruff check .`: pass. `ruff format --check .`: 76 files already formatted.
- `mypy`: `Success: no issues found in 47 source files`.
- `eis privacy-check`: `privacy: ok`. `pip-audit`: no known vulnerabilities; local `eis` skipped as unpublished.
- `uv build` + `scripts/verify_wheel.py`: pass; the wheel now also asserts `eis/core/model_envelope.py` and `eis/core/model_gateway.py` are packaged.
- Markdown link check: 43 files, 244 relative links, 0 broken. `uv lock --check`: pass.
- `tests/test_model_loop.py` covers valid answer/call envelopes; duplicate-key, malformed, oversized, unknown-kind and blank-answer responses; identity/root field rejection; unknown/unsafe tool requests stopping before work; policy denial and audit failure before any filesystem work (via an `_execute` spy); consent and provider failure mid-loop; the 3-completion/2-call limits; repeated identical calls; wall-time stop and feedback truncation; private-content exclusion from the database, audit payloads and default CLI output; CLI opt-in answer output; and interrupted-task non-resume.
- No `.env` read, live provider call, external service, commit, remote or push.

**Questions / remaining risks:**
- M1 remains incomplete: plans/delegation/approvals/recovery, step/limit persistence, T2 isolation, external audit anchoring, startup/scheduled integrity checks and broader approvals are pending. M0.2 clean-clone, GitHub CI and branch-protection evidence remain unverified (no remote exists).
- There is no envelope repair attempt: a second malformed response just stops the run. A model that cannot emit the envelope cannot complete tasks.
- Stop reasons are free strings spread across the loop; a shared registry would keep tests and docs in sync.
- Live provider behavior remains unverified by design (no keys, no calls).

**Next:**
- Design task-step/action schemas and human-reviewed proposal persistence for the full plan/act loop, keeping envelope validation as the only model boundary.
- Add T2 isolation and external audit anchoring; extend the root registry only through a new migration.

## 2026-11-23 — OpenCode (M0.2 reproducibility and CI acceptance evidence)

### Done

- **Clean-copy verification.** A fresh workspace copy was created with `robocopy`, excluding `.git`, `.venv`, `.uv-cache`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, `__pycache__`, runtime data (`*.sqlite3`, `*.db`, `*.log`), the local secrets file and the vision document (126 files copied, 0 failed; no `.git` and no secrets file in the copy). Inside the copy, all offline (`--offline`, no network):
  - `uv lock --check` → exit 0 (64 packages).
  - `uv sync --locked --all-extras` → exit 0, 64 packages installed from the lockfile only.
  - `uv run --locked pytest -q` on **Python 3.12.13** → **217 passed, 6 skipped**; same on **Python 3.11.15** (`UV_PYTHON=3.11`) → **217 passed, 6 skipped**.
  - `ruff check .` pass, `ruff format --check .` pass (77 files), `mypy src` pass (47 files), `eis privacy-check` → `privacy: ok`.
  - `uv build --offline` (wheel built from sdist) + `scripts/verify_wheel.py` → packaged resources ok.
- **Fresh-environment wheel verification.** The clean-copy wheel was installed with `uv pip install --offline` into a brand-new Python 3.11.15 venv (fresh `uv venv`) and exercised from a neutral directory with no local secrets file, `EIS_DISABLE_DOTENV=1`, no provider and no network: `import eis` resolves from `site-packages` (not `src/`), `eis version`, `eis policy-check` (packaged policy `2026.11-m1a`, `probe=deny`), `eis provider-check` (`provider=none configured=false`), `eis doctor` (exit 0, `runtime_outside_repository=true`), `eis privacy-check` (`privacy: ok`), and `eis audit verify` against an isolated temp data dir (`audit_chain=ok events=0` — migrations and schema built from installed package files). The CI smoke-test shape `uv run --isolated --no-project --with <wheel> eis version` reproduced locally on Python 3.11 (`eis 0.1.0a0`).
- **CI workflow review and hardening.** `.github/workflows/ci.yml`: kept full-SHA pins (`actions/checkout` v6, `actions/setup-python` v6, `astral-sh/setup-uv` v7, `gitleaks/gitleaks-action` v2), `permissions: contents: read`, `push`/`pull_request` triggers only; added `uv lock --check` (lint job), `persist-credentials: false` on every checkout, and `timeout-minutes` on all six jobs (lint 10, typecheck 10, test 30, security 15, privacy 15, package 15). No `pull_request_target`/`workflow_run`; only the default `GITHUB_TOKEN` (gitleaks); no `.env`, provider or `EIS_*` state; every `uv sync`/`uv run` is `--locked`. Workflow parses as valid YAML (jobs, permissions, timeouts verified with a YAML parser).
- **New guard tests.** `tests/test_ci_workflow.py` asserts those workflow invariants (read-only permissions, full-SHA pins with version comments, no untrusted triggers, required jobs/checks, deterministic locked sync, bounded jobs, no local state). `tests/test_repository_privacy.py`: the five git-work-tree assertions now **skip** when the tree is not the EIS work tree (clean copies, sdist extracts, copies inside another repo); in a real checkout (CI) they still run.
- **Docs updated.** `docs/M0.2_DECISIONS.md` (clean-copy and wheel evidence tables, workflow review, stale "M1 has not started" fixed); roadmap M0.2 section (local evidence, remaining acceptance); `README.md` (222 tests, clean-copy wording); `CHANGELOG.md` (M0.2 reproducibility entry, Notes); `CONTRIBUTING.md` (status line + `uv lock --check`).

### Verify

- Main workspace (Windows, Python 3.12.13): `pytest -q` → **222 passed, 1 skipped** (POSIX `openat`), 1 Starlette deprecation warning; `ruff check .` pass; `ruff format --check .` pass (77 files); `mypy` pass (47 files); `eis privacy-check` → `privacy: ok`; `pip-audit` → no known vulns (`eis` skipped, unpublished); `uv build` + `verify_wheel.py` pass; Markdown links → 43 files / 247 relative links / 0 broken; `uv lock --check` pass.
- Clean copy: see the command/result table in `docs/M0.2_DECISIONS.md` (217 passed / 6 skipped on both 3.12.13 and 3.11.15).

### Risks / next steps

- **M0.2 is NOT complete:** GitHub-hosted CI execution and branch protection remain unverified — no remote exists, no commits were made, nothing was pushed. Linux/macOS CI legs untestable locally (Windows only).
- Dependency licence review beyond pip-audit remains a release task.
- The offline 3.12 `uv run --isolated --with <wheel>` smoke test needs registry metadata in the local cache (networked CI unaffected).
- M1 outstanding items unchanged from the previous entry: T2 isolation, external audit anchoring, plans/delegation, step/limit persistence, broader approvals.

## 2026-11-23 — OpenCode (M1 durable task plans and bounded step lifecycle, ADR-043)

### Done

- **Storage/migration.** Added `0006_task_plans`: `task_plans` (one per task, bound to the session project, policy version/hash, plan hash and creation time) and `task_plan_steps` (ordered, tool or `human_review`, dependencies, guarded status, policy decision ID, output hash, error code) with SQL transition triggers. Added `eis.storage.PlanStore`; each transition persists its row change and content-free chained audit event atomically. Events: `plan_created`, `plan_step_created`, `plan_state_changed`, `plan_step_state_changed`.
- **Strict plan schema.** `eis.core.model_plan.parse_plan_response` accepts exactly one `{"kind":"plan","steps":[...]}` object, 1–6 steps, registered read-only tools or `human_review`, exact step keys, unique short IDs, dependencies on existing steps and no cycles. Duplicate JSON keys, extra fields, oversized plans, unknown tools, forbidden identity/root/policy/command/write/delete/network/approval tokens, forbidden argument keys, invalid arguments and duplicate steps fail with fixed content-free codes. The project is injected at execution from durable session state; plan validation uses a placeholder project ID through the existing typed tool registry.
- **Runtime.** `SessionTaskRuntime.run_plan` runs the same `task.run` policy gate as ADR-042, takes one plan completion, persists it, then executes only dependency-ready steps serially through `ReadOnlyProjectTools`/`PolicyService`. It stops on denial, audit failure, provider/consent failure, malformed output, repeated identical calls, missing root or a `human_review` step. `resume_plan` fails a running plan/step/task with `interrupted` and never auto-resumes. No parallel steps, retries, writes, deletes, shell, network, delegation or approval expansion.
- **CLI.** Added `eis plan run|show|steps`. Default output shows IDs, states, hashes and sanitized statuses only; plan text is printed only with `--show-proposal` between `model_generated_plan_begin/end`, after `plan=untrusted_model_generated_no_action_taken`.
- **Tests.** New `tests/test_task_plans.py` (32 tests): migration from the previous revision without data loss, restart reload, trigger/state guards, strict-schema rejection corpus, dependency ordering and cycles, human-review stop, policy denial before reads, audit failure propagation, repeated calls, interruption, privacy (no goal/model text/file content/arguments in plan tables, audit payloads or default CLI output) and CLI opt-in labeling. Updated `tests/test_storage.py` for the new head revision, tables and triggers. No `.env` read, live provider call, remote, commit or push.
- **Docs.** New ADR-043 + index row; runtime design v1.3; execution/permission model, database schema, roadmap M1 + M0.2 evidence, README, CHANGELOG, M0.2 decisions; learning guides 04/05/07/08/09. Wheel verification now asserts the new migration and modules.

### Verify

- Main workspace (Windows, Python 3.12.13): `pytest -q` → **254 passed, 1 skipped** (POSIX `openat`), 1 Starlette deprecation warning; `ruff check .` pass; `ruff format --check .` pass (81 files); `mypy` → `Success: no issues found in 50 source files`; `eis privacy-check` → `privacy: ok`; `pip-audit` → no known vulnerabilities (`eis` skipped as unpublished); `uv build` + `verify_wheel.py` pass; Markdown links → 43 files / 247 relative links / 0 broken; `uv lock --check --offline` pass.
- Fresh wheel smoke (Python 3.11.15 venv installed from the rebuilt wheel with `--no-deps --offline`, neutral directory, no provider): `import eis` from `site-packages`, `0006_task_plans.py` present in site-packages, `eis plan --help` exit 0, `eis plan show`/`steps` on a missing ID exit 1 with `plan_error: not found`.

### Risks / next steps

- M0.2 remains incomplete: GitHub-hosted CI execution and branch protection still have no evidence (no remote exists). M1 remains incomplete: delegation, broader approvals, blocked-plan recovery beyond interruption marking, step/limit persistence beyond plans, T2 isolation, external audit anchoring, redaction and API flows are outstanding.
- A rejected plan is not repaired; a human-review step stops the plan permanently in this slice. Step arguments are deliberately not recoverable from storage, so `eis plan show` cannot reconstruct the proposal.
- The pre-existing `tasks.goal` column and policy-decision resource strings still hold local goal/path text; this slice adds no such fields to plan tables or audit payloads. Shell command usage in `project_tools._git_status` is unchanged from ADR-040 (`shell=False`, fixed argv).
- Clean-copy reproducibility and the 3.11/3.12 verification belong to the earlier M0.2 evidence snapshot; this slice was verified in the main workspace plus a fresh wheel install on 3.11.

## 2026-11-24 — OpenCode (fail-closed T2 Linux-container backend, ADR-044)

### Done

- **`eis.core.sandbox`.** Added a narrow `SandboxBackend` protocol (`capabilities` + `run(SandboxRequest)`), `SandboxCapabilities`, `LinuxContainerBackend`, immutable-image check, environment-allowlist validation and a conservative Linux probe. `ExecutionGateway` takes an optional backend; T2 profiles and any profile on an untrusted project route through it only after `project.verify` policy evaluation and audit-integrity checks. No backend or insufficient capabilities → `isolation_unavailable` before execution and before an execution-start event.
- **Fixed container contract.** Application-supplied image digest and seccomp profile; no model/caller-supplied executable, image, mount or isolation flag. Every run uses `--pull=never`, `--network=none`, `--read-only`, non-root `10001:10001`, `--cap-drop=ALL`, `no-new-privileges`, explicit seccomp, CPU/memory/PID limits, output cap, timeout, a filtered disposable project copy (`:ro`, or `:rw` only for a registered writable profile), a unique container name and forced `rm --force` cleanup in `finally`. Only declared non-secret env keys plus `PYTHONDONTWRITEBYTECODE` are passed; `.git`, `.eis`, secret files, credential directories, backups and SQLite/DB files are excluded from the copy. No host socket, EIS data dir or other project roots are mounted.
- **Fail-closed platform detection.** `probe_linux_container` returns insufficient capabilities off Linux or when the engine/seccomp profile/digest/probe container cannot be verified. On this Windows host T2 is unavailable; T1 is still a same-user subprocess, not a security boundary. No real Docker/Podman call was made here.
- **Profile validation.** `CommandProfile` now validates `env_allowlist` (non-secret uppercase keys, ≤16, values capped at 512 chars) and requires `minimum_tier="T2"` for `needs_writable_workspace`.
- **Tests.** New `tests/test_t2_sandbox.py` uses a fake backend for configuration, policy denial, audit-integrity denial, untrusted routing, declared-only environment, timeout/truncation mapping, backend `OSError`, fixed argv protections and disposable-copy cleanup; updated the two stale T2 expectations in `tests/test_approval_execution.py`. No Docker/Podman required. No `.env` read, live provider call, remote, commit or push.
- **Docs.** ADR-044 + index; security architecture, execution/permission model, threat model, runtime design, roadmap, README, CHANGELOG, learning guides 07/08/09/README. `verify_wheel.py` now asserts `eis/core/sandbox.py`.

### Verify

- Main workspace (Windows, Python 3.12.13): `pytest -q` → **263 passed, 1 skipped** (POSIX `openat`), 1 Starlette deprecation warning; `ruff check .` pass; `ruff format --check .` pass (83 files); `mypy` → `Success: no issues found in 51 source files`; `eis privacy-check` → `privacy: ok`; `pip-audit` → no known vulnerabilities (`eis` skipped as unpublished); `uv build` + `verify_wheel.py` pass; Markdown links → 43 files / 247 relative links / 0 broken; `uv lock --check --offline` pass.
- Fresh Python 3.11.15 venv installed from the rebuilt wheel with `--no-deps --offline`: `eis.core.sandbox` imported from `site-packages`; the host capability probe reported `sufficient=False`, matching the documented Windows refusal.

### Risks / next steps

- **No T2 guarantee is claimed on this host.** The Linux probe has not been executed against a real Docker/Podman daemon; it requires a Linux host, an immutable digest image containing `/bin/sh`, an application-supplied seccomp profile and a successful flag probe. A missing image must fail with `--pull=never` rather than downloading.
- Kernel or daemon compromise, a local actor with daemon access, and residual container escape paths remain outside this control. Cleanup is best-effort: if `engine rm --force` fails silently, a container could leak until engine reaping.
- T1 on Windows remains explicitly not a security boundary; T2 is refused there. Broader approvals, delegation, blocked-plan recovery, redaction, external audit anchoring and API flows remain incomplete. M0.2 GitHub-hosted CI and branch protection remain unverified (no remote).

## 2026-11-24 — Codex review and M1 safety foundation

### Done

- Added `eis.core.redaction.redact_text` with deterministic credential-pattern replacements for private-key blocks, AWS/GitHub/Slack token formats, bearer tokens and common credential assignments. Task goals and read-only tool feedback are redacted before provider egress; model answer/plan previews are redacted before being returned to the caller. The detector is defense in depth and does not cover arbitrary secrets or all personal data.
- Added `eis.core.structured_logging` with a correlation context, request ID validation/generation, and safe scalar-only JSON logging. The API adds `X-Correlation-ID` and records method/path/status only; request bodies, goals, model output and secrets are omitted.
- Updated the API spec, roadmap and changelog to reflect the new behavior and explicitly list remaining M1 acceptance gaps.

### Verify

- `tests/test_redaction.py`: **3 passed**.
- Ruff check on changed Python files: pass; Ruff format applied to changed files; Mypy: **53 source files, 0 issues**.
- API `TestClient` smoke could not produce a result in this Windows sandbox; the focused health test remained stalled without output and was interrupted. Do not treat this as a passing API verification.
- Full suite, wheel build, privacy gate and dependency audit have not yet been run for this Codex change set.

### Remaining M1 work and acceptance

- The central roadmap acceptance remains unmet: a fake-provider task cannot yet produce a write-bound approval, receive human approval, execute the approved write, and complete with evidence. The existing approval CLI flow is still separate from task plans.
- Authenticated runtime API endpoints, token bootstrap/enforcement and `eis serve` are not implemented. Existing `/health` and `/api/v1/meta` stay unauthenticated; correlation IDs are not authentication.
- Durable explicit plan recovery requires a migration for private step arguments and careful retry semantics; this was not added. Delegation and external audit anchoring also remain open.
- M0.2 GitHub CI/branch protection and real Linux T2 container evidence require external environments unavailable in this workspace. No `.env` was read, no live provider was called, and no remote/commit/push was used.

## 2026-11-24 — Codex M1 runtime continuation

### Done

- Added ADR-046 and migration `0008_task_approval_link`. Strict plans can end with one task-bound project write proposal. Approval creation is reused after a crash; the write step, plan and task block in one audited SQLite transaction. If approval execution finishes before that block commits, completion recovers from the successful execution row without repeating the write. The existing user approval flow executes the exact digest; successful execution evidence completes step/plan/task atomically, while rejection fails them atomically.
- Added strict local API token storage/rotation, constant-time bearer validation, loopback Host validation, `eis api-token`, loopback-only `eis serve`, authenticated session/task routes and `eis ask --fake`. Uvicorn is declared and locked. Health and metadata remain public. Fake `ask` requires a policy file that allows `task.run` for its scope.
- Updated roadmap, API spec, runtime/security/permission/schema docs, README, changelog and learning guides to reflect the implemented subset and remaining gaps.

### Verify

- Ruff check: pass; Mypy: **57 source files, 0 issues**.
- Focused acceptance/recovery/API-auth/approval tests: **29 passed**.
- `uv lock --check --offline`: pass; `uvicorn 0.54.0` present in the locked environment.
- No `.env` read, live provider call, GitHub CI, Linux container probe, commit, remote or push.
- A `eis serve` process started, but the Windows sandbox denied loopback socket access (`An attempt was made to access a socket in a way forbidden by its access permissions`). Auth dependency and OpenAPI contracts were tested directly; real HTTP route verification remains pending.

### Remaining M1 and external acceptance

- M1 remains incomplete: authenticated API currently covers sessions/tasks only; approval/audit/project APIs, browser bootstrap, SSE/cancellation, delegation and human-review continuation remain open. Recovery is read-only plan-step recovery; broader step/limit recovery and comprehensive redaction are not implemented. External audit anchoring remains open.
- M0.2 still needs a GitHub-hosted CI run and protected branch. T2 still needs a real Linux host/container engine/image/seccomp probe. These cannot be produced on the current Windows host without a remote or external Linux environment.

### Follow-up verification — 2026-11-24

- Closed an approval recovery edge case: if the human approval is executed while the task/plan is still running because the process stopped before its approval-wait transaction, completion now reconstructs the blocked step/plan/task from the successful execution row in one audited transaction. It never repeats the write.
- Authenticated API reads now include pending approval list/detail and audit-chain verification. The approval detail endpoint intentionally returns the local diff only after bearer authentication; approve/reject/execute remain CLI actions.
- Authenticated API now also has digest-bound approve/reject and execute routes. They resolve project roots from the durable registry, use the existing execution gateway, and reconcile linked task state from durable approval/execution evidence. `EIS_POLICY_FILE` selects an explicit policy; absent it, the default deny policy applies.
- Earlier pre-API-decision verification: **30 passed** across API auth contracts, task-bound plan-write acceptance (normal and crash-recovery variants), read-only plan recovery and approval/execution tests. Ruff, Mypy (57 source files), privacy check, offline lock check, offline build and wheel-content verification passed at that point.
- After adding the API decision/execution routes, `tests/test_api_auth.py` plus `tests/test_task_approval_loop.py` → **8 passed**; `tests/test_approval_execution.py` → **20 passed**. Ruff check/format, Mypy (57 files), privacy, offline lock check, offline build and wheel-content checks pass. API auth tests assert both new routes are authenticated and their generated schemas require strict digest fields.
- The plan suite and FastAPI TestClient/loopback HTTP tests stall in this Windows sandbox during asyncio/socket setup, including with the repository's selector-loop fixture. Re-run these on an unrestricted local shell/CI runner before treating HTTP behavior and full plan regression as verified. `pip-audit` also could not reach pypi.org due DNS/network restrictions.
- `pip-audit` could not reach `pypi.org` because DNS/network access is unavailable. The real loopback HTTP smoke was blocked by the Windows sandbox socket permission error; `eis serve` did start, but HTTP requests could not connect. `eis ask --fake` CLI smoke also stalled in `asyncio` socket-pair initialization in this sandbox; the same runtime path passes the direct fake-provider integration tests.
- No `.env` read, live model request, commit, remote or push. M0.2 hosted CI/branch protection and real Linux T2 capability evidence remain external acceptance requirements.

---

## 2026-11-27 — OpenCode (approval-route recovery tests + Windows stall investigation)

**Status:** verified

**Context:** Reviewed the Codex `POST /api/v1/approvals/{id}/decision` and `.../execute` routes, `ExecutionGateway`, `ApprovalStore`, `SessionTaskStore.complete_task_after_approval`, `fail_task_after_approval_rejection`, `PlanStore`, task-approval tests, `tests/conftest.py`, and the Windows asyncio/FastAPI stall notes. Re-read `EIS_API_SPECIFICATION.md`, roadmap M1, ADR-041/042/043/044/045/046. No `.env`, no live provider, no remote/commit/push.

**Files:**
- `src/eis/api.py`, `src/eis/storage/approvals.py`, `src/eis/storage/plans.py`
- `tests/test_api_approvals.py`, `tests/test_api.py`, `tests/test_task_plans.py`, `tests/test_api_auth.py`
- `docs/EIS_API_SPECIFICATION.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`, `CHANGELOG.md`, `learning/03-apis-fastapi-cli.md`, `learning/04-sqlite-and-memory.md`, `learning/07-security-and-privacy.md`, `learning/08-agent-runtime-and-skills.md`

**Notes:**
- **Route-level tests added** (`tests/test_api_approvals.py`, TestClient, no Docker/provider): bearer authentication (missing + wrong token) for both routes; `409` on bad digest; `422` on schema-invalid digest; default-deny policy blocks execution with no `execution_runs` row and no completed task; rejected approval closes task/plan/write-step `failed` and cannot execute; no write occurs on rejection; successful approve→execute yields `task_state=completed`, task/plan/step `completed`, `execution_runs.status='success'` with `output_sha256`, and `task_approved_write_completed`; no project root or executable is accepted from the request.
- **Execute retry recovery added.** `POST .../execute` with an already-`consumed` approval and exact digest no longer calls `gateway.execute_write`; it requires `ApprovalStore.successful_execution` (durable `execution_runs.status='success'`) plus matching `task_id`, rechecks `complete_task_after_approval`, and returns the same `run_id`. Wrong digest on retry is `409`. No duplicate `execution_runs`, no second write, no task completion without evidence. `complete_task_after_approval` remains idempotent for already-completed tasks.
- **Two pre-existing Windows-suite blockers fixed** (fail-closed, not event-loop changes): `tests/test_api.py::test_meta_endpoint_returns_safe_configuration_error` used a non-loopback `TestClient` host and expected a `configuration_unavailable` body to name `EIS_OPENCODE_BASE_URL`; now uses `base_url=http://127.0.0.1` and asserts sanitized `configuration_unavailable`. `PlanStore.transition_step` rejected `pending -> blocked` for `human_review`; restricted `running -> blocked` to `project.propose_write`, matching DB triggers and ADR-043/046. `tests/test_task_plans.py` no longer asserts empty argument JSON after ADR-045 stores validated args. Two test mismatches against current API were corrected.
- **Windows stall investigation.** Production `eis serve` calls `uvicorn.run(...)` with no event-loop policy change; `asyncio.run` is only used for CLI task commands; the only workaround is test-only `conftest.windows_selector_event_loop`, which sets `asyncio.WindowsSelectorEventLoopPolicy()` on `win32`. In this session `socket.socketpair()` succeeds, `TestClient` approval/api/task tests run (**76 passed** focused, **284 passed, 1 skipped** full), and the standalone FastAPI/plan tests are no longer stalled. The earlier `test_task_plans`/`TestClient` stalls and `eis serve`/`eis ask --fake` socket-pair failures are **not reproducible here**; evidence points to the restricted Windows sandbox denying loopback socketpair/portal creation, not production logic. No production event-loop behavior was changed. Real loopback HTTP against `eis serve` remains **unverified** in this sandbox.
- Authorization stays fail-closed: strict `StrictModel`, exact 64-hex digest, integrity `require_intact`, policy recheck, project-root registry `root_status`, content-hash check, single-use consumed state, task/plan/step binding, `_chain` writes in one `BEGIN IMMEDIATE` transaction. T1 is still not a Windows security boundary; T2 unchanged (ADR-044). **M1 not complete; M0.2 not complete.**

**Verify:**
- Focused: `python -m pytest -q tests/test_api_approvals.py tests/test_task_plans.py tests/test_api.py tests/test_api_auth.py tests/test_task_approval_loop.py tests/test_approval_execution.py` → **76 passed**; `tests/test_api_approvals.py tests/test_task_plans.py tests/test_api.py tests/test_api_auth.py` → **51 passed** earlier iteration.
- Full: `python -m pytest -q` → **284 passed, 1 skipped** (POSIX `openat`), 1 `StarletteDeprecationWarning`.
- `ruff check .` → **All checks passed!**; `ruff format --check .` → **93 files already formatted**.
- `mypy` → **Success: no issues found in 57 source files**.
- `eis privacy-check` → **privacy: ok**.
- `uv lock --check --offline` → **Resolved 66 packages** exit 0.
- `uv build --offline` → **eis-0.1.0a0.tar.gz + .whl built**; `scripts/verify_wheel.py` → **packaged resources ok**.
- `pip-audit` (PIP_NO_INDEX=1, PIP_DISABLE_PIP_VERSION_CHECK=1, local cache) → **No known vulnerabilities found**; `eis` skipped (not on PyPI).
- Markdown-link check → **43 files, 249 relative links, 0 broken**.
- No `.env` read, live provider call, remote, commit or push.

**Questions:** none.

**Next:** Run on an unrestricted Linux/Windows shell to capture a real loopback `eis serve` smoke; add route test for `running`-task `complete_task_after_approval` recovery if not already covered at route level; evaluate whether `approval.grant` should be evaluated via `PolicyService`. M1/M0.2 external gaps unchanged.

---

## 2026-11-27 — OpenCode (atomic task cancellation + human-review continuation, ADR-047)

**Status:** verified

**Context:** Continued M1 after reading `CODEX_OPENCODE_HANDOFF.md`, roadmap M1, `EIS_API_SPECIFICATION.md`, ADR-041/042/043/044/045/046, runtime/security/permission/threat designs, `ExecutionGateway`, `ApprovalStore`, `SessionTaskStore`, `PlanStore`, `SessionTaskRuntime`, CLI, API, approval routes, migration 0008, tests. Inspected Git state before editing. No `.env`, no live provider, no real container probe.

**Files:**
- `src/eis/core/runtime.py`, `src/eis/core/execution.py`, `src/eis/storage/sessions.py`, `src/eis/storage/plans.py`, `src/eis/storage/approvals.py`, `src/eis/api.py`, `src/eis/cli.py`
- `src/eis/storage/migrations/versions/0009_task_cancellation_review.py` (new; old migrations unchanged)
- `tests/test_task_cancellation_review.py`, `tests/test_api_approvals.py`, `tests/test_storage.py`, `tests/test_task_plans.py`, `scripts/verify_wheel.py`
- `docs/EIS_ARCHITECTURE_DECISIONS.md`, `docs/EIS_AGENT_RUNTIME_DESIGN.md`, `docs/EIS_API_SPECIFICATION.md`, `docs/EIS_DATABASE_SCHEMA.md`, `docs/EIS_EXECUTION_AND_PERMISSION_MODEL.md`, `docs/EIS_THREAT_MODEL.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`, `CHANGELOG.md`, `README.md`, `learning/03-apis-fastapi-cli.md`, `learning/04-sqlite-and-memory.md`, `learning/08-agent-runtime-and-skills.md`

**Notes:**
- **Cancellation.** `cancel_task` requires `task.cancel`, checks `PolicyService` + `AuditIntegrity`, and `SessionTaskStore.cancel_task` does `BEGIN IMMEDIATE` task + plan + steps + content-free chained events. `created`, `running`, `blocked human_review_required` cancel; `approval_required` cannot (use digest rejection); terminal cannot. `pending→skipped`, blocked human_review→skipped, running→failed `error_code=cancelled`, completed unchanged. Cancelled is terminal; no auto-resume.
- **Approval race.** `_has_successful_execution` + DB `NOT EXISTS` guards; `successful_execution_evidence_exists` refuses cancellation; `ExecutionGateway.execute_write` rechecks `ApprovalStore.task_is_terminal`; `complete_task_after_approval` + retry recovery remain. No write undone, no duplicate side effect. Failure before evidence returns to blocked, not cancelled.
- **Human review.** `continue_plan_review(task_id, step_ref)` + `continue_human_review`. Strict `[A-Za-z0-9_-]{1,24}`, no extra fields; target `human_review` blocked step; `task.run`, policy version/hash, session, project identity/root rechecked. `blocked→running`, `human_review→completed`, session idle→active + events atomically. `_run_plan_steps` skips completed, no new model call, no duplicate reads, no permission grant, no `approval.grant`, no root/policy change, no write bypass. `approval_required` not continuable. Audit failure rolls back. `resume_plan` remains read-only recovery.
- **Migration.** `0009_task_cancellation_review` recreates `tasks_state_transition`, `task_plans_state_transition`, `task_plan_steps_status_transition`; no edits to 0001–0008. `task.cancel` added docs; default deny.
- **CLI/API.** `eis task cancel`, `eis plan continue-review`; `POST /api/v1/tasks/{id}/cancel`, `POST /api/v1/tasks/{id}/review/continue`. Bearer + strict schema + policy. No root/executable/identity/policy/approval grant accepted. API spec/ADR updated. Human review is not write approval.
- **Privacy.** No goal/model text/file contents/tool results added; audit payloads ids/states/tool/step_ref/error codes; `approval_required` digest checks unchanged. No `.env`, live provider, external audit anchoring/T2 probe.
- **Windows.** No production event-loop change; existing test-only `WindowsSelectorEventLoopPolicy` remains. Focused tests and full suite run here.

**Verify:**
- `tests/test_task_cancellation_review.py tests/test_api_approvals.py` → **13 passed**; `tests/test_task_cancellation_review.py` → **7 passed**; cancellation-race added to `test_api_approvals.py`.
- Full `python -m pytest -q` → **292 passed, 1 skipped** (POSIX `openat`), 1 Starlette warning.
- `ruff check .` → **All checks passed!**; `ruff format --check .` → **95 files already formatted**.
- `mypy` → **Success: no issues found in 58 source files**.
- `eis privacy-check` → **privacy: ok**.
- `uv lock --check --offline` → exit 0; `uv build --offline` → built; `verify_wheel.py` → **packaged resources ok** (incl. `0009`).
- `pip-audit` (PIP_NO_INDEX=1, cache) → **No known vulnerabilities found**; `eis` skipped.
- Markdown links → **43 files, 249 relative links, 0 broken**. No live model calls.

**Risks / remaining:** `approval_required` cancellation intentionally not added; use digest-bound reject. `task.cancel` must be explicitly granted; default deny. No T2 real Linux host proof; no external audit anchoring; no delegation; M1 not complete; M0.2 GitHub CI + branch protection not verified.

---

## 2026-11-27 — OpenCode (GitHub CI + branch protection check after push)

**Status:** blocked

**Notes:** Pushed `main` to `https://github.com/GiridharBM/Engineering-Intelligence-System`. Commit `2ba8404` CI run `45956728610` completed **failure**. `lint`, `package`, `privacy`, 8/8 test jobs passed; `typecheck` failed on `uv run --locked mypy src` (not reproducible locally on Windows; local `uv run --locked mypy src` → `Success: no issues found in 58 source files`); `security` failed on `gitleaks/gitleaks-action` `Leaks detected`. Full job logs require admin; `gh` CLI absent. Branch-protection endpoint returned **401** without auth; branch protection cannot be verified/configured here. No `.env`, provider call, or secret disclosure.

---

## 2026-11-27 — OpenCode (CI blocker diagnosis + portability fix; ADR-047 status correction)

**Status:** verified

**Context:** Read handoff, roadmap M0.2/M1, latest CI evidence; inspected `ExecutionGateway`, `CommandProfile`, process cleanup, `_terminate_process`, migration state, Git `main`, origin. Re-checked hosted run `45956728610` (`security` job `140364814927`, `typecheck` job `140364814729`) and later `45957327115`. No `.env`, live model calls, secret output, weakening of Mypy/Gitleaks.

**Priority 1 — CI blockers:**
- **Gitleaks.** Old `security` job failed on `gitleaks/gitleaks-action@dcedce43...` with `Leaks detected`. Hosted full job logs endpoint returned **403 `Must have admin rights to Repository`**; `gh` CLI is **not installed**; no `gitleaks` binary; no `.gitleaks.toml`. Exact old finding could **not** be retrieved and is **not claimed**. No real credential was printed or added; no broad allowlist/suppression; `.env` not tracked/read; local `eis privacy-check` → `privacy: ok`; `git ls-files` shows no `.env`. On retry run `45958458885`, `gitleaks-action` succeeded and `security` (`pip-audit`) succeeded. No history rewrite/revocation performed; if a real credential were identified it must be revoked/rotated.
- **Ubuntu 3.12 typecheck.** Old `uv run --locked mypy src` failed; not reproducible on Windows. Root cause fixed: direct `subprocess.CREATE_NEW_PROCESS_GROUP` → `getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)`; direct `signal.CTRL_BREAK_EVENT` → `getattr(signal, "CTRL_BREAK_EVENT", None)` + fallback `terminate`. Windows behavior preserved; no Mypy strictness reduced. On retry run `45958458885`, **`typecheck` succeeded**. `lint`, `package`, `privacy`, `security` succeeded; test matrix was in progress at check time.
- **Commits:** `935f6d4 Fix portable process-group typing and correct status docs` on `main`, pushed `origin/main`.

**Priority 2 — stale ADR-047 status corrected:** removed claims that task cancellation / human-review continuation / cancellation routes are pending in `README.md`, `docs/EIS_IMPLEMENTATION_ROADMAP.md`, `docs/EIS_API_SPECIFICATION.md`, `learning/README.md`, `learning/08-agent-runtime-and-skills.md`; corrected `M0.2` `origin`/`main` and CI status; `CHANGELOG` `Fixed`. Old append-only handoff entries are not rewritten; this entry supersedes. Kept delegation, broader approvals, broader blocked-plan recovery, external audit anchoring, real Linux T2, M1, M0.2 pending. Did not implement external audit anchoring/delegation; no broad feature without tests/recovery. (Prior entries still mention historical blockers; ignore in favor of latest.)

**Verify:** `uv run --locked pytest -q` → **292 passed, 1 skipped**; `uv run --locked ruff check .` → **All checks passed!**; `ruff format --check .` → **95 files already formatted**; `uv run --locked mypy src` → **58 source files, 0 issues**; `uv run --locked eis privacy-check` → **privacy: ok**; `uv lock --check --offline` → exit 0; `uv build --offline` → built; `uv run --locked python scripts/verify_wheel.py` → **packaged resources ok**; Markdown links → **43 files, 248 relative links, 0 broken**. Retry CI jobs `typecheck`/`security`/`lint`/`package`/`privacy` **passed**.

**Risks:** full retry run not observed to `conclusion=success`; branch protection **unverified/needs GitHub admin**; hosted logs unavailable; old Gitleaks exact finding unknown; `gitleaks` not run locally; no revocation; M0.2 not complete; M1 not complete; no real Linux T2 evidence; no external audit anchoring; delegation not implemented.

---

## 2026-11-27 — OpenCode (full CI success + external audit-anchor interface; ADR-048)

**Status:** verified

**Context:** Read latest handoff, M0.2/M1 roadmap, ADR-036/041–047, audit-chain implementation, threat model, schema and audit tests. Queried GitHub Actions for latest pushed commit. No `.env`, live model calls, credentials, or secret output.

**CI:** Latest pushed commit `df5847e` complete workflow run `45958723773` reached **`conclusion=success`** (full workflow, not just jobs). Branch protection endpoint still returns 401 without admin; protection remains unverified/needs GitHub settings.

**Implemented — external audit anchoring interface (ADR-048):**
- `src/eis/core/audit_anchor.py`: content-free `AuditAnchor`, `AnchorReceipt`, `AuditAnchorSink`, `AuditAnchorService`, and `AnchorEventStore`. `eis-audit-anchor-v1` binds `up_to_seq`, `head_hash`, `created_at_ms`, algorithm into `anchor_id`/`anchor_hash`; `receipt_hash` binds anchor to the external acknowledgement. No secret, credential, project content or payload text in anchors/receipts.
- `create` verifies the full local chain, refuses empty chain, submits to the external sink, validates receipt, appends one content-free `audit_anchor_submitted` reference event with deterministic ID, and is idempotent on immediate replay. `verify` rechecks full chain, exactly the anchored prefix, head hash, anchor hash, receipt hash, local reference and external sink verification. Missing sink, external failure, receipt mismatch, chain mismatch, tampering/truncation all fail closed.
- `AuditChainStore`: added `events_up_to`, `status_for`, `event_by_id`; `ChainStatus.head_time_ms` added with default 0. A local event is a receipt/reference, **not** external anchoring.
- No real external service/backends configured; the interface is implemented/tested with an in-memory fake independently controlled sink. Real external retention and independent verification remain unverified.

**Adversarial tests:** `tests/test_audit_anchor.py` (9 passed): creation/verification + content-free event, idempotent replay/forged receipt/mismatched anchor, stale anchor after later events, tampered chain, truncated chain, external submit failure, external verify failure, audit append failure + restart/retry idempotence, no backend refusal. No real service used.

**Docs:** ADR-048 + index; roadmap M0.2/M1 status corrected (full CI success, ADR-047 cancellation/review no longer pending, ADR-048 interface vs unverified backend); security architecture, threat model (R-06 open), database schema (no local table called external anchor), learning 04/07/10, `README`, `CHANGELOG`; `verify_wheel.py` asserts `eis/core/audit_anchor.py`. Old append-only handoff entries were not rewritten.

**Verify:** `uv run --locked pytest -q` → **301 passed, 1 skipped** (POSIX `openat`), 1 Starlette warning; `uv run --locked ruff check .` → **All checks passed!**; `uv run --locked ruff format --check .` → **97 files already formatted**; `uv run --locked mypy src` → **59 source files, 0 issues**; `uv run --locked eis privacy-check` → **privacy: ok**; `uv lock --check --offline` → exit 0; `uv build --offline` → built; `uv run --locked python scripts/verify_wheel.py` → **packaged resources ok**; Markdown links → **43 files, 248 relative links, 0 broken**.

**Risks / remaining:** no configured/real independently controlled external anchor backend; no external retention evidence; branch protection unverified; no real Linux T2 container evidence; delegation and broader recovery not implemented; M0.2 not complete without verified branch protection despite green CI; M1 not complete without all acceptance criteria.

**Follow-up CI result:** full workflow run `45962384940` for commit `7608b1a` reached **`conclusion=success`** (`status=completed`), not merely individual jobs passing.

---

## 2026-11-27 — OpenCode (authenticated audit-event browsing API)

**Status:** verified

**Context:** Read latest handoff, M1 roadmap, API spec, `audit_chain` core/storage, ADR-048, and existing API/storage patterns. No `.env`, live model calls, credentials or raw payload output.

**Implemented:** `GET /api/v1/audit/events` requires the local bearer token and rejects unknown/repeated query fields (422). Only `limit` (1–100, default 50) and opaque `cursor` are accepted. Response items contain only `seq`, `occurred_at_ms`, `event_type`, `actor`, `subject_type`, `subject_id`, `decision`, `prev_hash`, `event_hash`; no payloads, paths, prompts, diffs, contents or credentials. Cursor is HMAC-signed with the local token and binds `after_seq`/`head_seq`/`head_hash`; tampered/malformed cursors return 422, cursors past the chain 409. Paging is stable while events are appended because the bound prefix is re-verified. Full chain status and requested prefix are verified on every page; corruption returns 503 and no page. Integrity status is included in every response. Added `tests/test_api_audit_events.py` (8 tests): auth, strict query/cursor validation, empty history, safe metadata, stable paging, tampered/stale cursor, corrupted full chain and corrupted prefix. API spec, roadmap, changelog, learning 03, README updated; old handoff entries not rewritten.

**Verify:** focused `pytest -q tests/test_api_audit_events.py` → **8 passed**; full `uv run --locked pytest -q` → **309 passed, 1 skipped** (POSIX `openat`), 1 Starlette warning; `uv run --locked ruff check .` → **All checks passed!**; `uv run --locked ruff format --check .` → **98 files already formatted**; `uv run --locked mypy src` → **59 source files, 0 issues**; `uv run --locked eis privacy-check` → **privacy: ok**; `uv lock --check --offline` → exit 0; `uv build --offline` → built; `uv run --locked python scripts/verify_wheel.py` → **packaged resources ok**; Markdown links → **43 files, 248 relative links, 0 broken**. External anchor backend remains interface-only; T2 Linux evidence, delegation, branch protection and full M1 acceptance remain open.

---

## 2026-11-27 — OpenCode (fail-closed crash recovery; ADR-049)

**Status:** verified

**Context:** Read latest handoff, M1 roadmap, API spec, runtime/plan/approval/execution/audit/recovery code. No `.env`, live model calls, credentials or payload exposure.

**API correction:** removed stale “audit event browsing remains planned” from `docs/EIS_API_SPECIFICATION.md`; `GET /api/v1/audit/events` is implemented and authenticated.

**Durable boundary map:** task/plan creation and approval wait are atomic with chained events; read-only in-flight steps may be explicitly retried once (ADR-045); write proposals bind task/approval digest and block atomically (ADR-046); execution consumes single-use approval before writing and only a successful `execution_runs` row permits completion; consumed approval without success evidence was a stranded boundary. API/CLI retries now reconcile successful evidence idempotently, and a consumed approval with no success evidence fails closed as `execution_unverified` (ADR-049, migration `0010`). No auto-resume after restart; stale policy/root and audit failures fail closed; no write is repeated and no task is completed/cancelled without matching evidence.

**Fault tests:** `tests/test_m1_crash_recovery.py` covers file side effect before evidence persistence, terminal `execution_unverified`, repeated no-op recovery, no duplicate write, and no content in audit payloads. `tests/test_task_cancellation_review.py` adds audit failure rollback during read-only recovery, repeated resume idempotence, stale policy and stale root fail-closed. No duplicate tool invocations.

**Verify:** focused `tests/test_m1_crash_recovery.py tests/test_task_cancellation_review.py` → **10 passed**; full `uv run --locked pytest -q` → **312 passed, 1 skipped** (POSIX `openat`), 1 Starlette warning; `ruff check .` → **All checks passed!**; `ruff format --check .` → **100 files already formatted**; `mypy src` → **60 source files, 0 issues**; `privacy-check` → **privacy: ok**; `uv lock --check --offline` → exit 0; `uv build --offline` + `verify_wheel.py` → **packaged resources ok**; Markdown links → **43 files, 248 relative links, 0 broken**.

**Risks / remaining:** external anchor backend, real Linux T2 evidence, branch protection, delegation, broader recovery and full M1 acceptance remain unverified/incomplete; M0.2 still needs verified branch protection despite green CI.

---

## 2026-11-27 — OpenCode (CLI consumed-approval digest validation fix)

**Status:** verified

**Context:** Codex review identified that `eis approval execute` consumed-approval recovery did not validate the supplied digest before completing or failing linked state. Inspected `src/eis/cli.py`, API retry path (unchanged), approval store and recovery tests. No `.env`, live model calls or credential output.

**Fix:** in the `record.status == "consumed"` branch, validate the digest format and compare it to `record.request_digest` with `hmac.compare_digest` before any recovery action. Wrong digest exits `execution_error: digest_mismatch` without touching task/plan/step or `execution_runs`, whether successful evidence exists or not. Correct digest retains idempotent completion from successful evidence and fail-closed `execution_unverified` when evidence is absent; no write is repeated. API retry behavior unchanged (already digest-bound). Added CLI regression tests in `tests/test_m1_crash_recovery.py`: wrong digest with evidence, wrong digest without evidence (asserts no `execution_unverified`) and correct-digest idempotent retry. Updated CHANGELOG, README, roadmap, handoff append-only.

**Verify:** focused `tests/test_m1_crash_recovery.py` → **4 passed**; full `uv run --locked pytest -q` → **315 passed, 1 skipped** (POSIX `openat`), 1 Starlette warning; `ruff check .` → **All checks passed!**; `ruff format --check .` → **100 files already formatted**; `mypy src` → **60 source files, 0 issues**; `privacy-check` → **privacy: ok**; `uv lock --check --offline` → exit 0; `uv build --offline` + `verify_wheel.py` → **packaged resources ok**; Markdown links → **43 files, 248 links, 0 broken**.

**Risks / remaining:** external anchor backend, real Linux T2 evidence, branch protection, delegation and full M1 acceptance remain open; M0.2 still needs verified branch protection despite green CI.

---

## 2026-11-27 — OpenCode (real Linux T2 verification in hosted CI)

**Status:** verified locally; hosted result appended after push

**Context:** Read latest handoff, ADR-044, sandbox backend/probe, CI workflow and guard tests. No `.env`, live model calls or credentials.

**Implemented:** strengthened `probe_linux_container` to require evidence from inside the container: non-root, read-only root/workspace, zero `CapEff`, `NoNewPrivs=1`, active seccomp, loopback-only network, and bounded pids/CPU/memory cgroups. Added checked-in `policies/t2-seccomp-probe.json` (probe profile) and `scripts/verify_t2_linux.py`, which exercises the real `LinuxContainerBackend` with digest-pinned `docker.io/library/alpine@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf`: read-only execution, disposable writable copy, output cap, timeout, cleanup, and fail-closed missing capability/mutable image. Added dedicated `t2-linux` Ubuntu job that pulls the image by digest (only network use), then runs the script. CI-workflow guard tests allow only the two non-secret `EIS_T2_*` inputs, require the job/script/digest and reject mutable tags. ADR-044, roadmap, security/threat docs, learning 07, CHANGELOG, README, wheel verification and handoff updated. The hosted probe proves runner protections at a point in time; it is not protection against a compromised runner, daemon or kernel.

**Verify (local):** focused `tests/test_ci_workflow.py tests/test_t2_sandbox.py` → **23 passed**; full `pytest -q` → **323 passed, 1 skipped**; `ruff check .` → All checks passed; `ruff format --check .` → 101 files; `mypy src` → 60 files, 0 issues; `privacy: ok`; `uv lock --check` exit 0; `uv build` + `verify_wheel.py` pass (now asserts the seccomp profile); Markdown links 43 files / 248 links / 0 broken.

**Risks / remaining:** real external audit-anchor retention and GitHub branch protection remain open; delegation belongs to M5; broader integration/security acceptance remains. M0.2/M1 not complete.

**Hosted result (appended after push):** runs `45980458006` and `45981628387` failed during iteration; self-diagnosing `::error::` annotations isolated three causes — a relative seccomp path (now resolved absolute), a mutable-image tag raising `SandboxError` instead of yielding insufficient capabilities (now caught as fail-closed), and a 0700 temp fixture whose mode `copytree` preserved on the mounted copy, making it unreadable to container uid 10001 (harness fixture now chmod 0777). Final run `45983527849` for commit `a31787d` reached `status=completed`, **`conclusion=success`**: all 12 jobs passed, including `T2 Linux container (real backend)` with zero failure annotations. The digest-pinned image pull was the only network use; container executions used the backend's fixed `--network=none`, read-only, non-root, dropped-capability, seccomp and cgroup-limited flags, with output/timeout caps, disposable copy, cleanup and fail-closed checks all passing on a real Ubuntu runner. Still point-in-time runner evidence, not protection against a compromised runner, daemon or kernel.

---

## 2026-11-27 — OpenCode (optional S3 Object Lock audit-anchor sink)

**Status:** verified locally with mocked AWS clients; hosted result appended after push

**Context:** Read ADR-048, `eis.core.audit_anchor`, the security/threat docs and the latest handoff. Checked AWS official documentation for Object Lock, retention modes, versioning, required permissions, `PutObjectRetention`/`GetObjectRetention`, conditional writes and boto3 `put_object`. No AWS resource was created, no live AWS call was made, no credential was read or used, and `.env` was not read.

**AWS facts recorded:** Object Lock requires S3 Versioning; Object Lock and versioning cannot be disabled after Object Lock is enabled; it is not supported on directory buckets or Outposts. COMPLIANCE retention cannot be shortened or deleted before expiry by any user, including the account root user; deleting the AWS account is the only early removal. GOVERNANCE retention can be bypassed by a principal with `s3:BypassGovernanceRetention` and the explicit bypass header. Uploading with retention requires `Content-MD5` or `x-amz-sdk-checksum-algorithm`. `IfNoneMatch="*"` returns 412 when the key exists and 409 on a conflicting request.

**Implemented:** optional `eis[s3]` extra (`boto3`). `eis.core.audit_anchor_s3` provides `S3ObjectLockConfig`, explicit `EIS_ANCHOR_S3_*` loading, and `S3ObjectLockAnchorSink` using boto3's default credential provider chain (no credential arguments/storage/logging). Preflight requires `ExpectedBucketOwner`, versioning `Enabled` and Object Lock `Enabled`. Uploads use a deterministic `<prefix><anchor_id>.json` key, canonical content-free checkpoint JSON, content-free `eis-*` metadata, SHA-256 checksum, `ObjectLockMode`/`ObjectLockRetainUntilDate` from explicit config, and `IfNoneMatch="*"`. A 412 reloads and verifies the existing latest version; other service errors fail closed. A receipt is accepted only after `GetObject(VersionId)` verifies body and metadata, and `GetObjectRetention(VersionId)` confirms the configured mode and a retain-until date at least the configured duration after `LastModified` (5-minute tolerance) and still in the future. Extended `AnchorReceipt` with opaque `external_version` bound by `receipt_hash` and stored in the content-free anchor event. Added `eis anchor status` (non-secret config + fail-closed preflight), wheel assertion, ADR-050 (and ADR-048 amendment), security/threat/schema/roadmap/README/CHANGELOG and learning updates. No retention period was selected and no bucket was created.

**Tests added:** `tests/test_audit_anchor_s3.py` with injected fake S3 clients covers round-trip, deterministic content-free body, idempotent resubmit (single version, same receipt), altered body/metadata/mode/retention, wrong receipt version, wrong bucket owner, wrong configured bucket, missing Object Lock, suspended versioning, insufficient/missing retention, service errors with a secret-shaped marker and secret-free logs, config validation, `AnchorReceipt.external_version` service integration, and CLI status fail-closed/safe output. No live AWS test or credential.

**Verify (local):** focused `tests/test_audit_anchor.py tests/test_audit_anchor_s3.py` → **38 passed**; full `pytest -q` → **352 passed, 1 skipped**; `ruff check .` → All checks passed; `ruff format --check .` → 103 files; `mypy src` → 61 files, 0 issues; `privacy: ok`; `pip-audit` → no known vulnerabilities (local `eis` skipped as not on PyPI); `uv lock --check` exit 0; `uv build` + `verify_wheel.py` pass (now asserts `eis/core/audit_anchor_s3.py`); Markdown links 43 files / 248 links / 0 broken.

**Risks / remaining:** this is mocked-adapter evidence, not real independent-retention evidence. A real deployment still needs an operator to provision an Object Lock bucket, choose COMPLIANCE or GOVERNANCE and an explicit duration, grant only the documented permissions (not `s3:BypassGovernanceRetention`), run `eis anchor status`, and create/independently verify a real anchor. GOVERNANCE is bypassable; COMPLIANCE is irreversible until expiry and is lost if the AWS account or KMS keys are lost. GitHub branch protection still needs admin; delegation belongs to M5; M0.2/M1 remain incomplete.