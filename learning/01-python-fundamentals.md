# 1. Python Fundamentals Through EIS

**Status:** Concept guide with implemented M0.2 examples.
**Code:** [`src/eis/policy.py`](../src/eis/policy.py), [`src/eis/privacy.py`](../src/eis/privacy.py), [`src/eis/config.py`](../src/eis/config.py)

You do not need advanced Python to read EIS. You need to recognise the building blocks below and know where they appear.

## Variables and types

A variable is a name bound to a value. Python is dynamically typed: the interpreter does not require you to declare a type, but EIS adds type hints so tools and humans can check assumptions.

```python
SESSION_RETENTION_DAYS = 7
DEFAULT_MODEL_PROVIDER = None
allow_cloud = False
```

- `7` is an integer.
- `None` means "no provider is enabled by default".
- `False` is a boolean.
- A tuple such as `()` means "no project roots configured".
- A string such as `"opencode"` would select an explicitly configured provider.

EIS keeps configuration values in [`src/eis/config.py`](../src/eis/config.py). Constants are uppercase by convention; the values are plain Python, not magic.

## Conditionals

A conditional chooses a path with `if`, `elif` and `else`.

```python
if default != "deny":
    raise PolicyError("policy.default must be deny")
```

This line is important: a policy that does not explicitly say `deny` is rejected rather than silently accepted. The same "fail closed" idea appears throughout the security design.

Boolean expressions can be combined with `and`, `or` and `not`. Comparisons return `True` or `False`.

## Loops

A loop repeats work. `for` iterates over a collection.

```python
for rule in self.rules:
    if rule.effect == "deny" and _matches(rule, principal, action, resource):
        return self._decision(request, "deny", "explicit_deny", rule.id)
```

EIS's current policy evaluator uses two loops: first it looks for a matching deny rule, then for a matching allow rule. If neither matches, it returns `default_deny`.

`enumerate()` gives both the index and the value, which is useful when reporting which rule is malformed:

```python
for index, item in enumerate(raw_rules):
    rules.append(_rule(item, index))
```

## Functions

A function groups reusable behaviour. It has a name, parameters, a body and usually a return value.

```python
def load_policy(path: Path) -> Policy:
    """Load and validate a policy file, failing closed on any error."""
    ...
```

Functions in EIS are small and have one job:

- `load_policy(path)` reads and validates a file.
- `load_default_policy()` loads the bundled policy.
- `find_violations(root)` scans a repository.
- `is_within(path, root)` checks path containment.

The docstring explains the contract. The annotation `-> Policy` says what comes back.

## Modules and packages

A module is a `.py` file. A package is a directory of modules with an `__init__.py`.

EIS uses a `src/` layout:

```
src/
└── eis/
    ├── __init__.py
    ├── api.py
    ├── cli.py
    ├── config.py
    ├── policy.py
    └── privacy.py
```

Imports connect modules:

```python
from eis.policy import load_default_policy
```

The package is installed in editable mode by `uv sync`, so `import eis` works from the project environment without copying files around.

## Exceptions

An exception signals that an operation cannot continue. EIS defines its own exception type for policy errors:

```python
class PolicyError(ValueError):
    """Raised when a policy is malformed; callers must fail closed."""
```

Catching exceptions is deliberate:

```python
try:
    raw = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise PolicyError(f"cannot read policy {path}: {exc}") from exc
```

- `OSError` covers file and permission problems.
- `json.JSONDecodeError` covers malformed JSON.
- Re-raising as `PolicyError` gives callers one error type to handle.
- `from exc` preserves the original cause for debugging.

Do not write a bare `except:`; it hides bugs and can turn a failure into a false success.

## Type hints

Type hints are optional labels that tools such as Mypy check. They do not change runtime behaviour.

```python
from typing import Literal

Effect = Literal["allow", "deny"]
Reason = Literal["explicit_allow", "explicit_deny", "default_deny"]

def evaluate(self, request: PolicyRequest) -> PolicyDecision:
    ...
```

Useful forms in EIS:

| Hint | Meaning |
|---|---|
| `str` | Text |
| `int` | Integer |
| `bool` | True/False |
| `Path` | Filesystem path |
| `str \| None` | Text or nothing |
| `tuple[Rule, ...]` | Immutable sequence of rules |
| `Literal["deny"]` | Exactly the string `"deny"` |
| `dict[str, object]` | String keys, values of unknown type |

`from __future__ import annotations` at the top of EIS modules makes annotations lazy, which keeps older Python versions happy and avoids some import cycles.

## Dataclasses

A dataclass reduces boilerplate for data-holding classes.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Rule:
    id: str
    principal: str
    action: str
    resource: str
    effect: Effect
```

- `@dataclass` generates `__init__`, equality and a readable representation.
- `frozen=True` makes instances immutable; a rule cannot be changed after creation.
- The fields are typed.

`PolicyDecision`, `Policy`, `Settings` and `Violation` are also dataclasses. Immutability makes policy decisions easier to reason about and test.

## Reading the code in order

1. [`src/eis/config.py`](../src/eis/config.py) — plain values and defaults.
2. [`src/eis/policy.py`](../src/eis/policy.py) — dataclasses, validation, loops, exceptions.
3. [`src/eis/privacy.py`](../src/eis/privacy.py) — regular expressions, filesystem walking, git subprocess.
4. [`src/eis/api.py`](../src/eis/api.py) — FastAPI and Pydantic models.
5. [`src/eis/cli.py`](../src/eis/cli.py) — Typer commands.

## Try it

```powershell
uv run eis policy-check
uv run eis privacy-check
uv run pytest tests/test_policy_baseline.py -q
```

Read [`tests/test_policy_baseline.py`](../tests/test_policy_baseline.py). It shows how a policy is built as a dictionary, validated, evaluated and checked against a JSON Schema.

## Implemented vs planned

| Concept | Today |
|---|---|
| Dataclasses, type hints, exceptions, loops | Implemented in `src/eis/` |
| Policy evaluation | Implemented as a typed, schema-validated exact-match evaluator with deterministic decision records |
| Chained policy decisions | Implemented through `PolicyService` before a decision is returned |
| A3 approvals and bounded execution gateway | Implemented for project writes and fixed T1 profiles; broader actions/T2 planned |
| Persisted audit chain | Implemented as append-only hash-linked `audit_events` |
| Agent-loop model calls and agent runtime | Planned; see [architecture map](09-architecture-map.md) |

Next: [Tooling, tests and CI](02-tooling-tests-ci.md).
