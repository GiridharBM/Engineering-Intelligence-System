# EIS Model Provider Configuration and Gateway

Version: 4.1 (strict-envelope bounded model loop)
Status: Text-only provider gateway, durable SQLite consent, and content-free hash-chained audit records implemented. The gateway itself stays text-only: it never dispatches tools. The runtime consumes its text through the ADR-042 JSON envelope and runs a bounded read-only tool loop; no streaming or execution gateway exists.
Related: [EIS_ARCHITECTURE_DECISIONS.md](EIS_ARCHITECTURE_DECISIONS.md) (ADR-033), [EIS_SECURITY_ARCHITECTURE.md](EIS_SECURITY_ARCHITECTURE.md), [EIS_PRIVACY_AND_DATA_GOVERNANCE.md](EIS_PRIVACY_AND_DATA_GOVERNANCE.md)

---

## 1. Scope

EIS supports a provider-neutral text completion path for:

- OpenCode-compatible endpoints
- OpenAI
- Google Gemini
- Anthropic Claude
- xAI Grok

Each vendor uses its own documented protocol where the request/response shape differs. No provider is enabled by default, and no live API call is made by tests or diagnostics.

## 2. Official sources checked

Checked on 2026-11-20:

| Provider | Official sources |
|---|---|
| OpenCode | <https://opencode.ai/docs/console/models/> and <https://opencode.ai/docs/zen> |
| OpenAI | <https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create> and <https://developers.openai.com/api/reference/overview> |
| Google Gemini | <https://ai.google.dev/api/generate-content> and <https://ai.google.dev/api> |
| Anthropic | <https://docs.anthropic.com/en/api/overview> and <https://platform.claude.com/docs/en/api/messages/create> |
| xAI | <https://docs.x.ai/developers/rest-api-reference/inference> and <https://docs.x.ai/developers/model-capabilities/legacy/chat-completions> |

These sources establish the endpoints, auth headers, request fields and response fields used below. Model IDs are **user-supplied**; EIS does not hardcode or guess them.

## 3. Common selection and environment

| Variable | Required | Meaning |
|---|---|---|
| `EIS_MODEL_PROVIDER` | No | `opencode`, `openai`, `gemini`, `anthropic`, `xai`, `ollama`, or unset |
| `EIS_MODEL` | Yes for external providers | Direct model ID |
| `EIS_MODEL_TIMEOUT_SECONDS` | No | Request timeout, 1–300; default 60 |
| `EIS_MODEL_CONSENT_CLASSES` | No | Explicit consent, e.g. `C0,C1`; absent means no external call |
| `EIS_MODEL_CONSENT_PURPOSE` | No | Purpose label stored in the audit record |
| `EIS_OPENCODE_BASE_URL` | Yes for OpenCode | Documented base URL; OpenCode endpoints differ |
| `EIS_OPENCODE_MODEL` | Backward-compatible alias | Used only for OpenCode when `EIS_MODEL` is unset |

Provider-specific credentials:

| Provider | Credential variable(s) | Fixed base URL |
|---|---|---|
| OpenCode | `OPENCODE_API_KEY` or `EIS_OPENCODE_API_KEY` | Configured by `EIS_OPENCODE_BASE_URL` |
| OpenAI | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com/v1beta` |
| Anthropic | `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1` |
| xAI | `XAI_API_KEY` | `https://api.x.ai/v1` |

Ollama is an optional label only in this slice; no Ollama client exists.

## 4. Protocols implemented

### 4.1 OpenAI-compatible Chat Completions

Used for OpenCode, OpenAI and xAI:

```http
POST {base_url}/chat/completions
Authorization: Bearer <provider-key>
Content-Type: application/json
```

```json
{
  "model": "<model-id>",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": false,
  "temperature": 0.1,
  "max_tokens": 256
}
```

Normalized response fields come from `id`, `choices[0].message.content`, `choices[0].finish_reason`, and `usage.prompt_tokens`/`completion_tokens`/`total_tokens`.

OpenAI and xAI use their fixed official bases. OpenCode requires `EIS_OPENCODE_BASE_URL` because official docs list different bases for different plans/model families.

### 4.2 Google Gemini generateContent

```http
POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
x-goog-api-key: <provider-key>
Content-Type: application/json
```

```json
{
  "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
  "systemInstruction": {"parts": [{"text": "Be concise"}]},
  "generationConfig": {"temperature": 0.1, "maxOutputTokens": 256}
}
```

Normalized fields come from `candidates[0].content.parts[*].text`, `candidates[0].finishReason`, `usageMetadata`, `responseId` and `modelVersion`.

### 4.3 Anthropic Messages

```http
POST https://api.anthropic.com/v1/messages
x-api-key: <provider-key>
anthropic-version: 2023-06-01
Content-Type: application/json
```

```json
{
  "model": "<model-id>",
  "max_tokens": 256,
  "messages": [{"role": "user", "content": "Hello"}],
  "system": "Be concise",
  "temperature": 0.1,
  "stream": false
}
```

Normalized fields come from `content[*].text`, `stop_reason`, `usage.input_tokens`/`output_tokens`, `id` and `model`. Anthropic requires `max_tokens`; EIS rejects a request without it instead of inventing a default.

### 4.4 Finish reasons

Adapters normalize common stop reasons:

- `stop` — natural stop, end turn or stop sequence
- `length` — token limit reached
- `content_filter` — safety/content block
- `tool_calls` — model requested tools natively; EIS never executes provider-native tool calls. The runtime parses only `response.text` through its own JSON envelope, so a response with no envelope text stops the run as malformed.
- other values are lowercased and passed through

## 5. Durable consent

External calls fail closed unless an active, provider-scoped consent record covers the request's data class.

```text
EIS_MODEL_CONSENT_CLASSES=C0,C1
```

- Absent or empty: no consent, no network call.
- `C0`/`C1`: allowed only when listed.
- `C2`: allowed only when explicitly listed.
- `C3`: always rejected.
- Consent is stored in the SQLite `consents` table with a reference such as `cns_<16 hex>`.
- `EIS_MODEL_CONSENT_CLASSES` is an environment bootstrap: the gateway inserts it only if that consent ID does not already exist. A revoked row is not silently re-granted.
- Expired (`expires_at <= now`) and revoked records are ignored on the next call.

Manage durable consent without exposing keys:

```powershell
uv run eis consent grant --provider openai --classes C0,C1 --purpose "code review" --expires-days 30
uv run eis consent list
uv run eis consent revoke <consent-id>
```

The database path is the platform runtime directory plus `db/eis.sqlite3`.

The gateway records an egress decision (`allow` or `deny`) before any network attempt. A denied call produces no HTTP request and records a `consent_denied` model-call audit entry with no consent reference.

## 6. Durable model-call audit

`ModelGateway` records a content-free `ModelCallAudit`:

```json
{
  "type": "model_call",
  "provider": "openai",
  "model": "example-model-id",
  "request_hash": "<64 hex>",
  "started_at": "2026-11-20T00:00:00+00:00",
  "latency_ms": 123.4,
  "status": "success",
  "finish_reason": "stop",
  "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
  "provider_request_id": "req_...",
  "consent_id": "cns_..."
}
```

Records are persisted in SQLite tables `egress_decisions` and `model_calls`. They never store request messages, response text, prompts, API keys or raw provider bodies. Each projection row is committed with a content-free `audit_events` chain event. An egress allow is chained before the network attempt; the model-call outcome is chained after it, so a crash between them leaves a visible allow without an outcome rather than a silent call. The JSONL sink remains available for diagnostics but is not chained; the durable gateway factory uses SQLite. The chain is tamper-evident, not tamper-proof; an optional S3 Object Lock anchor adapter exists but no real bucket is configured or verified.

## 7. Secret handling and `.env`

- Keys are read from the process environment only and held as `SecretStr`.
- The `eis` console entry point loads a local `.env` from the current directory, without printing it. Tests disable this with `EIS_DISABLE_DOTENV=1`.
- `.env` is ignored by Git; `.env.example` contains placeholders only.
- Errors and `provider-check` name a missing/malformed variable, never its value.
- `provider-check` validates configuration without making a network request. It does not prove that a key is valid with the provider.

## 8. Supported, deferred and unverified

| Item | Status |
|---|---|
| Text requests/responses, non-streaming | Supported |
| Normalized usage, finish reason, request ID | Supported where vendor supplies them |
| Provider registry and vendor-specific adapters | Supported |
| Consent gate and egress/model-call audit records | Supported; persisted in SQLite |
| OpenAI/xAI/OpenCode Chat Completions | Supported; xAI documents this as legacy |
| Gemini `generateContent` | Supported; Google recommends its newer Interactions API for new projects |
| Anthropic Messages `2023-06-01` | Supported; newer API versions exist |
| Streaming | Deferred |
| Tool calling / execution | Deferred; policy engine remains authoritative |
| Images, audio, embeddings | Deferred |
| OpenAI Responses API, xAI Responses API, Gemini Interactions, Anthropic newer versions | Not implemented |
| Persistent consent store with revoke/expiry | Supported in SQLite |
| Hash-chained audit persistence | Supported; tamper-evident, optional S3 anchor adapter mocked-tested, no real external anchor |
| Live provider verification with real keys | Not performed by design |
| `x-opencode-session` routing header | Unverified; not added |

## 9. Tests

```powershell
uv run pytest tests/test_config.py tests/test_opencode_provider.py tests/test_provider_adapters.py tests/test_storage.py tests/test_audit_chain.py -q
uv run eis provider-check
uv run eis consent list
uv run eis audit verify
```

All adapter tests use `httpx.MockTransport`; no network call and no real key. Tests cover request mapping, response normalization, usage/finish reason/request ID, malformed responses, sanitized HTTP errors, missing configuration, consent denial with no HTTP call, allowed calls with mock consent, audit records, migration, restart, revocation, expiry, content-free SQLite storage, valid and broken chains, append-only triggers, integrity blocking and secret-free payloads.

## 10. Unresolved provider questions

- Which exact model and plan the owner will use is local configuration.
- OpenAI/xAI Chat Completions vs Responses and Gemini generateContent vs Interactions: only the documented legacy/current endpoints above are implemented.
- Anthropic API version choice: `2023-06-01` is used; newer versions are not verified.
- Whether `x-opencode-session` is required for the owner's OpenCode plan is unresolved.
- `.env` loading is CLI-only; a future `eis serve` entry point must load it explicitly.
- `/api/v1/meta` remains unauthenticated.
