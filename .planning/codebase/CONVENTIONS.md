# Coding Conventions

**Analysis Date:** 2026-06-18

## Naming Patterns

**Files:**
- Lowercase with underscores: `main.py`, `test_api.py`, `safe_logger.py`
- Test files: `test_*.py` or module suffixed with `_test.py` (e.g., `test_cache.py`)
- Package inits: `__init__.py` (minimal; just imports)
- No camelCase or mixed case in filenames

**Functions:**
- Lowercase with underscores: `resolve_principal()`, `normalize_question()`, `log_access_decision()`
- Private functions: prefix with single underscore `_salt()`, `_bool()`, `_FakeResponse`
- Async functions: same naming, prefixed `async def` keyword
- Helper/internal utilities: `_configure_logging()`, `_build_doc_acl()`

**Variables:**
- Lowercase with underscores: `principal`, `group_ids`, `upstream_timeout`
- Constants: UPPERCASE with underscores: `PROMPT_PATH`, `DATASET_PATH`, `API_KEY`
- Class instances/modules: lowercase: `cache`, `client`, `settings`
- Exceptions: CamelCase, suffix `-Error`: `IdentityError`, `AccessDenied`, `GraphError`

**Types & Classes:**
- CamelCase: `Principal`, `Settings`, `_FakeResponse`
- Dataclasses: `@dataclass(frozen=True)` for immutable value objects
- Frozen dataclasses used for configuration and identity types

## Code Style

**Formatting:**
- No explicit formatter config (no `.prettierrc`, `.flake8`, or `ruff.toml`); style is enforced by convention and manual review
- Line length: default Python convention (≈80-100 chars, inferred from codebase)
- Blank lines: 2 between top-level functions/classes, 1 between methods
- Indentation: 4 spaces (PEP 8 standard)

**Linting:**
- Tool: `bandit` (security linting — gate in CI via `make test`)
- Severity enforced: medium+ (no trivial warnings)
- Tool: `gitleaks` (secret detection — gate in CI, 0 secrets allowed)
- Tool: `pip-audit --strict` (dependency CVE scanning — gate in CI, 0 CVE allowed)

**Import Organization:**
Order (strictly enforced):
1. `from __future__ import annotations` (PEP 563 postponed evaluation — ALWAYS first)
2. Standard library (`json`, `os`, `sys`, `logging`, `asyncio`, `re`, `dataclasses`, etc.)
3. Third-party packages (`fastapi`, `httpx`, `pydantic`, `redis`, `pytest`)
4. Local imports (relative: `from . import config`, `from .app import main`)

No wildcard imports (`from x import *`). Explicit imports only.

**Path Aliases:**
Not used. All imports are explicit relative or absolute (stdlib → third-party → local).

## Error Handling

**Patterns:**
- **Custom exceptions**: Define at module/class level; inherit from standard exception types
  - Example: `class IdentityError(RuntimeError)` in `access-gateway/app/identity.py`
  - Example: `class AccessDenied` in `access-gateway/app/onyx_proxy.py`
- **Fail-safe philosophy**: Operations that may fail (e.g., cache, metrics, logging) catch all exceptions and degrade gracefully
  - Pattern: `try: ... except Exception: # pragma: no cover — best-effort cleanup`
  - Never let optional subsystems (Redis, metrics) crash the main request
- **Fail-closed philosophy**: Security decisions (auth, authorization, DLP) raise HTTPException if validation fails
  - Pattern: `if not authorized: raise HTTPException(status_code=403, detail="...")`
- **Context managers**: Use `with/async with` for resource cleanup (HTTP clients, file handles)
- **Logging on errors**: Always log errors before raising (except in tests)
  - Pattern: `logger.error("message %s", detail); raise CustomError(...)`

**Exception safety annotations:**
- Code that never raises is documented: `# exception-safe` in docstrings (cache, metrics)
- Code that catches everything: `except Exception as e: ...` with pragmatic pragma markers for test coverage

## Logging

**Framework:** `logging` (Python stdlib)

**Patterns:**
- Logger name: always hierarchical, prefixed with "onix": `logging.getLogger("onix.gateway")`, `logging.getLogger("onix.actions")`
- Levels: DEBUG (dev/trace), INFO (normal operation), WARNING (degradation/security issue), ERROR (failure)
- Format: `"%(levelname)s:%(name)s:%(message)s"` (no timestamps; container/systemd adds them)
- Configuration: only if no handlers exist; idempotent (checks `if not logger.handlers`)
- **PII redaction** (onix-actions only): `safe_logger.install("onix.actions")` filters all logs through `redact()` before emission
- **Structured logging**: Not used; format strings with `%s` substitution

**Log forging prevention** (onix-actions):
- CRLF injection: redaction escapes `\n` and `\r` to literal `\\n`, `\\r`
- No secrets in logs: PII filter removes JWT, IBAN, NIR, email, Bearer tokens before any log hits disk

## Comments

**When to Comment:**
- Above non-obvious algorithm or security decision
- Explaining WHY (not WHAT — code should be self-explanatory)
- Marking temporary workarounds: `# TODO`, `# FIXME`, `# HACK`
- Documenting edge cases or failure modes
- Over-commenting is discouraged; clean code > comments

**Style:**
- In English or French (codebase uses French heavily)
- Single-line: `# ...` (space after hash)
- Block: `"""..."""` (docstrings, never `'''`)

**JSDoc/Docstrings:**
- Used: module-level docstrings (every `.py` file) and function docstrings (public functions, classes)
- Format: Google-style docstrings (optional; implicit in codebase)
  - Example: `"""Parse the en-tête X-OIDC-Claims (JSON). ..."""`
- Type hints: Always present on function signatures (PEP 484)
- Return type hints: `-> SomeType` on function definitions

**Example docstring (from codebase):**
```python
"""identity — résout l'identité et les GROUPES Entra de l'appelant.

Deux sources, sélectionnées par GATEWAY_GROUP_SOURCE :
  * "claims" : lit les groupes dans les claims OIDC.
  * "graph" : interroge Microsoft Graph transitiveMemberOf (app-only).
"""
```

## Function Design

**Size:** Prefer small functions (≤30 lines); break large logic into helpers
- Example: `_configure_logging()`, `_build_doc_acl()`, `_salt()`

**Parameters:**
- Positional args for required inputs
- Keyword-only args (after `*`) for optional/configuration: `def claims(*, oid="...", upn="...", groups=None)`
- Type hints mandatory: `def foo(x: str, y: int) -> bool`
- Default args avoid mutable defaults (use `None` + create inside function)

**Return Values:**
- Single return type (no mixed types)
- `None` for side effects only
- Tuples for multiple values: `return str(user_id), (str(upn) if upn else None)`
- Never return `True/False` for error states; raise exceptions instead

**Async/await:**
- Used in FastAPI handlers: `async def endpoint(...) -> Response`
- Event loop in tests: `asyncio.run(coro)` via helper function `run()` in `conftest.py`
- Not used elsewhere (actions/rag tests are synchronous)

## Module Design

**Exports:**
- Private symbols prefixed `_` are not exported
- Public API at module level; internal helpers below
- No `__all__` enforcement (implicit public = not-prefixed-with-underscore)

**Barrel Files:**
- Minimal use; imports explicit (not hidden behind barrel re-exports)
- `__init__.py` files typically empty or have minimal re-exports

**File structure (typical):**
```python
"""Module docstring — purpose and key exports."""
from __future__ import annotations

import logging
from typing import Optional

# ... imports (stdlib → third-party → local)

logger = logging.getLogger("onix.component")

# Configuration / constants
DEFAULT_TTL = 3600

# Private helpers
def _internal_helper() -> str:
    ...

# Public classes
class MyClass:
    ...

# Public functions
def public_function(x: str) -> int:
    ...
```

## Code Organization Principles

**Separation of concerns:**
- Data models in one file: `config.py` (Settings), separate from logic
- Logic in domain files: `identity.py` (resolve principals), `cache.py` (caching logic)
- Tests alongside code: `tests/test_*.py` mirrors `app/*.py`

**Defensive coding:**
- Type hints on every function (static analysis via type checker implied)
- Validate inputs early: `if not x: raise ValueError("x required")`
- Immutable dataclasses for config: `@dataclass(frozen=True) class Settings`

**Code locality:**
- Keep related logic close: cache key generation next to cache lookup
- Avoid deep nesting (max 2-3 levels); extract helpers if deeper

---

*Convention analysis: 2026-06-18*
