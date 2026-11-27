# 3. APIs, FastAPI, CLI, JSON and Configuration

**Status:** API factory and CLI skeleton implemented; authenticated local M1 subset implemented (see `tests/test_api.py`, `tests/test_api_auth.py`, `tests/test_api_approvals.py`); remaining API design planned.
**Files:** [`src/eis/api.py`](../src/eis/api.py), [`src/eis/cli.py`](../src/eis/cli.py), [`schemas/`](../schemas/), [`docs/EIS_API_SPECIFICATION.md`](../docs/EIS_API_SPECIFICATION.md)

## What an API is

An API is a contract for one program to ask another to do something. EIS's HTTP API will eventually expose projects, sessions, approvals, findings and reports.

An HTTP request has:

- a **method** such as `GET` or `POST`;
- a **path** such as `/api/v1/meta`;
- optional **headers**, including authentication;
- an optional **body**, usually JSON;
- a **response** with a status code and body.

JSON is a text format for structured data:

```json
{
  "name": "EIS",
  "mode": "single-user",
  "policy_default": "deny",
  "policy_version": "2026.11-m1a",
  "policy_hash": "<64 hex characters>"
}
```

JSON values are objects, arrays, strings, numbers, booleans and `null`. EIS policy files and API payloads are JSON.

## FastAPI

FastAPI maps Python functions to HTTP endpoints and generates an OpenAPI description from them.

```python
def create_app() -> FastAPI:
    app = FastAPI(
        title="EIS",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(name="EIS", version=__version__)
```

Key ideas:

- `create_app()` is a factory. Tests can create isolated applications.
- `@app.get(...)` registers a route.
- `response_model` validates and documents the response shape.
- `docs_url=None` disables the interactive docs page in M0.2.
- OpenAPI remains available at `/api/v1/openapi.json`.

### Current endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness and version |
| `GET` | `/api/v1/meta` | Single-user mode, provider name, cloud flag, root count, policy default |

The meta endpoint returns **metadata only**. It does not start a model, read a project, or prove that an agent runtime exists.

### Pydantic models

```python
class MetaResponse(BaseModel):
    name: str
    version: str
    mode: Literal["single-user"]
    model_provider: str
    allow_cloud: bool
    project_roots: int
    policy_default: Literal["deny"]
```

Pydantic checks types at the boundary. If a handler returns the wrong shape, the response model catches it. This is why the API can be tested without a database.

### Test the API in-process

```powershell
uv run python -c "from fastapi.testclient import TestClient; from eis.api import create_app; print(TestClient(create_app(), base_url='http://127.0.0.1').get('/api/v1/meta').json())"
```

No server, port or network listener is required for this check.

## OpenAPI

OpenAPI is a machine-readable description of the API. FastAPI generates it from routes and Pydantic models.

```python
schema = create_app().openapi()
assert "/health" in schema["paths"]
assert "/api/v1/meta" in schema["paths"]
```

`tests/test_api.py` asserts that the document is generated and served. In M1, the OpenAPI document will be committed and diffed in CI so contract changes are visible.

## CLI design

A CLI is an API for humans at a terminal. EIS uses Typer, which builds commands from typed Python functions.

```python
app = typer.Typer(no_args_is_help=True, help="Local-first engineering intelligence.")

@app.command("policy-check")
def policy_check() -> None:
    policy = load_default_policy()
    request = PolicyRequest.from_mapping(
        {
            "principal": "agent:eis.primary",
            "action": "project.write",
            "resource": "project:unregistered",
            "context": {"source": "cli"},
        }
    )
    decision = PolicyEngine(policy).evaluate(request)
    typer.echo(f"probe={decision.effect} reason={decision.reason}")
```

Commands should be:

- **predictable** — one verb or noun per command;
- **scriptable** — stable output and non-zero exit codes on failure;
- **safe by default** — read-only commands do not write;
- **explainable** — errors say what to do next.

Current commands:

| Command | What it does |
|---|---|
| `eis version` | Prints the installed version |
| `eis doctor` | Prints resolved runtime paths and safe defaults; fails if runtime data is inside the repository |
| `eis policy-check` | Proves an unlisted action is denied; read-only diagnostic |
| `eis policy-evaluate` | Evaluates one typed request and records the decision in the audit chain |
| `eis policy-decisions` | Lists the queryable policy-decision projection |
| `eis approval ...` | Proposes, reviews, grants/rejects and executes digest-bound A3 writes |
| `eis session ...` | Creates, lists, resumes or closes durable sessions |
| `eis task ...` | Submits/resumes a one-completion task and shows content-free status |
| `eis task invoke-tool ... --call-json ...` | Explicitly invokes one strict, registered read-only project tool through policy; output defaults to hashes/status only |
| `eis consent list` / `grant` / `revoke` | Manages durable provider consent |
| `eis audit verify` | Verifies the audit chain without printing payloads |
| `eis privacy-check` | Scans candidate repository files for private paths, runtime data and secret-shaped values |

## JSON Schemas

A JSON Schema describes what a valid JSON document looks like. EIS uses schemas so a malformed policy fails before it can influence a decision.

```json
{
  "type": "object",
  "required": ["version", "default", "rules"],
  "properties": {
    "default": {"const": "deny"},
    "rules": {"type": "array", "items": {"$ref": "#/$defs/rule"}}
  }
}
```

Files:

- [`schemas/policy.schema.json`](../schemas/policy.schema.json) — policy structure, exact-match rules, no wildcards.
- [`schemas/policy-decision.schema.json`](../schemas/policy-decision.schema.json) — decision output.
- [`policies/default.json`](../policies/default.json) — deny by default, no rules.

The Python loader and the JSON Schema both reject wildcards. Two checks are intentional: a schema catches bad external data; the loader protects the runtime even if the schema is skipped.

## Configuration

Configuration changes behavior without code changes. EIS currently supports:

- platform defaults for data and config directories;
- `EIS_DATA_DIR` and `EIS_CONFIG_DIR` environment overrides;
- provider selection via `EIS_MODEL_PROVIDER` and `EIS_MODEL`, with provider-specific keys: `OPENCODE_API_KEY` (or `EIS_OPENCODE_API_KEY`), `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` or `XAI_API_KEY`;
- explicit consent via `EIS_MODEL_CONSENT_CLASSES`, required before any external model call;
- optional `EIS_MODEL_TIMEOUT_SECONDS`.

```powershell
uv run eis doctor
uv run eis provider-check
```

`.env.example` contains placeholders; `.env` is ignored by Git. The `eis` CLI loads `.env` from the current directory. Tests disable this and use fake settings.

The full design adds a TOML file and precedence rules (defaults < user config < project config < environment < CLI). That is specified in ADR-027 but **not implemented** yet. Project roots are intentionally empty; they are local runtime configuration in M2 and are never committed.

The full design adds a TOML file and precedence rules (defaults < user config < project config < environment < CLI). That is specified in ADR-027 but **not implemented** yet. Project roots are intentionally empty; they are local runtime configuration in M2 and are never committed.

Secrets do not belong in configuration files. Provider keys come from environment variables only; the provider configuration and official protocol are documented in [`docs/EIS_MODEL_PROVIDER_CONFIGURATION.md`](../docs/EIS_MODEL_PROVIDER_CONFIGURATION.md).

## Implemented vs planned

| Feature | Status |
|---|---|
| FastAPI app factory, public `/health` and `/api/v1/meta` | Implemented |
| Local bearer token, loopback `eis serve`, authenticated session/task routes | Implemented (M1 subset) |
| `eis ask --fake` deterministic smoke task | Implemented; requires explicit allow policy to run |
| Pydantic response models and generated OpenAPI | Implemented |
| Typer CLI commands | Implemented |
| JSON Schemas for policy and decisions | Implemented |
| Environment-based runtime paths | Implemented |
| TOML configuration precedence | Planned (ADR-027) |
| Authenticated approval decision/execute + pending review + audit-verification routes | Implemented (M1 subset); digest-bound, fail-closed, retry recovers from durable `execution_runs` without repeating a write; route-level tests in `tests/test_api_approvals.py` |
| Authenticated task cancellation + human-review continuation | `POST /api/v1/tasks/{id}/cancel`, `POST /api/v1/tasks/{id}/review/continue`; strict schema + policy; `tests/test_task_cancellation_review.py` |
| Authenticated audit-event browsing | `GET /api/v1/audit/events`; bounded metadata-only pages, signed stable cursor, integrity status; `tests/test_api_audit_events.py` |
| Authenticated project APIs, bootstrap and SSE | Planned (M1+) |
| Dashboard and IDE integration | Planned (M10) |

Next: [SQLite and the memory model](04-sqlite-and-memory.md).
