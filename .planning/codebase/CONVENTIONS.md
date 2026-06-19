# Coding Conventions

**Analysis Date:** 2026-06-19

## Naming Patterns

**Files:**
- Lowercase with underscores: `main.py`, `test_api.py`, `safe_logger.py`, `doc_acl.py`, `fabric_acl.py`
- Test files: `test_<module>.py` (e.g., `test_doc_acl.py`, `test_fabric_client.py`)
- Harnais/e2e/runners: `run_<scenario>.py` (e.g., `run_access_e2e.py`, `run_e2e.py`, `run_live.py`)
- Configuration: `conftest.py` (pytest fixtures shared across module)
- No camelCase in filenames; no mixed case

**Functions:**
- Lowercase with underscores: `resolve_principal()`, `normalize_question()`, `log_access_decision()`, `can_principal_read()`
- Private functions: leading underscore `_salt()`, `_as_bool()`, `_build_doc_acl()`
- Test functions: `test_<what_is_tested>()` (e.g., `test_static_acl_loads_from_file()`)
- Async functions: standard `async def` with snake_case name (no `async_` prefix)
- Helper functions: descriptive verbs: `_parse_`, `_build_`, `_configure_`

**Variables:**
- Snake_case: `principal`, `group_ids`, `upstream_timeout`, `doc_id`
- Constants: UPPERCASE: `API_KEY`, `REFUSAL_NO_ACCESSIBLE_SOURCE`, `GOLD_WS`, `GOLDEN_PATH`
- Class attributes: private with leading underscore: `_salt`, `_payload`, `_cache`
- Loop variables: `i`, `j` for indices; meaningful names otherwise: `group in groups`
- Type hints mandatory: `principal: _PrincipalLike`, `groups: list[str]`, `doc_id: str`

**Types & Classes:**
- CamelCase: `Principal`, `StaticDocACL`, `FabricClient`, `Settings`
- Protocol interfaces: CamelCase with leading underscore for internal-only: `_PrincipalLike`
- Dataclasses: `@dataclass(frozen=True)` for immutable value objects
- Exception classes: CamelCase suffixed `Error`: `IdentityError`, `GraphError`, `AccessDenied`

## Code Style

**Formatting:**
- `from __future__ import annotations` at top of every Python file (PEP 563)
- No explicit formatter configured (no `.prettierrc`, `.flake8`, `ruff.toml`)
- Indentation: 4 spaces (PEP 8)
- Line length: inferred ~88–100 characters
- Double quotes for strings: `"string"` (not `'string'`)
- Blank lines: 2 between module-level functions/classes, 1 within classes

**Linting:**
- `bandit`: Security linting (gate in CI via `make test`); blocks medium+ severity
- `gitleaks`: Secret detection (gate in CI, 0 secrets allowed)
- `pip-audit --strict`: CVE scanning (gate in CI, 0 CVE allowed in strict mode)
- No ESLint/Prettier (Python only)

**Import Organization:**
Order (strictly enforced):
1. `from __future__ import annotations` (always first, PEP 563)
2. Standard library: `import asyncio`, `import json`, `import logging`, `from dataclasses import dataclass`
3. Third-party: `import httpx`, `from fastapi import FastAPI`, `from pydantic import BaseModel`
4. Local/relative: `from . import config`, `from .audit import log_access_decision`

**No wildcard imports.** All imports explicit.

**Path Aliases:**
- Not used; imports are explicit relative (`from .`) or absolute stdlib (`import json`)
- Tests import app modules absolutely: `import app.config`, `import app.main`

## Comments & Documentation

**When to Comment (French only — non-negotiable per AGENTS.md §4):**
- Explain **why**, not what
- Document architectural constraints: `# JWT : claim "hasgroups": true, OU "_claim_names"/"_claim_sources"…`
- Flag non-obvious business rules: `# default_policy='deny' (défaut) ⇒ tout est refusé.`
- Mark fail-closed behavior: `# Fail-CLOSED par document inconnu`
- Note temporary workarounds: `# TODO`, `# FIXME`, `# HACK`

**Style:**
- Single-line: `# comment` (space after `#`)
- Docstrings: triple double-quotes `"""…"""` (never `'''` or `r"""`)
- **All docstrings in French** (required)

**Module Docstrings:**
- Verbose (50–200 lines common)
- Explain PURPOSE, INVARIANTS, SECURITY PROPERTIES
- Example: `access-gateway/app/doc_acl.py` — explains FOSS gap, filtering position, fail-closed discipline
- Examples often included: code snippets or JSON structures

**Function Docstrings:**
- 1–3 lines: concise statement of purpose
- Type hints in signature (not repeated in docstring)
- Optional but recommended for public functions
- Example: `"""HMAC-SHA256(sel, actor) tronqué (16 hex)…"""`

**Example from codebase:**
```python
"""Tests du filtre ACL par-document (chemin RÉPONSE, FOSS).

Couvre :
  * Chargement JSON (`StaticDocACL.from_file` / `from_obj`).
  * `default_policy` deny vs allow pour un doc inconnu.
  * Match par groupe (casse insensible) ; override par utilisateur (UPN/oid).
"""
```

## Error Handling

**Patterns:**
- **Fail-CLOSED by default** (deny access on error or missing info)
  - Unknown document + `default_policy="deny"` → access denied
  - No identity header (`X-OIDC-Claims` missing) → 401
  - Unknown group + `GATEWAY_DENY_IF_NO_MATCH=true` → 403
- **Fail-OPEN on internal exception** (surface bug, don't crash service)
  - Pattern: catch exception, log error, return safe default
  - Example: `doc_acl.py` — fail-open on JSON loader crash, log `doc_acl_error`
  - Example: `identity.py` — fail-open on JSON parse error, log warning, return `{}`
- **Custom exceptions:** `IdentityError(RuntimeError)`, `GraphError(RuntimeError)`, `AccessDenied(Exception)`
- **Descriptive messages** with context: `raise IdentityError("Aucun identifiant utilisateur dans les claims (oid/sub/upn).")`
- **No bare `except:`** clauses; always catch specific types
- **Log before raising** (except in tests): `logger.error("detail"); raise CustomError(...)`

**Fail-Closed Examples:**
- `doc_acl.py`: `default_policy="deny"` unknown → `is_authorized()` returns `False`
- `identity.py`: missing claims → raises `IdentityError`
- `main.py`: no identity header → `raise HTTPException(401)`
- `onyx_proxy.py`: unauthorized document set → `raise AccessDenied()`

**Defensive Type Checks:**
```python
if isinstance(val, list):
    groups = [str(g).strip() for g in val if str(g).strip()]
if not actor:
    return "anonymous"
if not raw_header:
    return {}
```

## Logging

**Framework:** Standard Python `logging`

**Logger Names (hierarchical):**
- `onix.gateway` (access-gateway main)
- `onix.gateway.audit` (access decisions, identity, document filtering)
- `onix.gateway.identity` (identity resolution, Graph calls)
- `onix.actions` (actions service main)
- `onix.actions.*` (submodule-specific)

**Configuration:**
- Env vars: `GATEWAY_LOG_LEVEL`, `ONIX_LOG_LEVEL` (default `INFO`)
- Format: `"%(levelname)s:%(name)s:%(message)s"`
- Idempotent: `if not logger.handlers and not logging.getLogger().handlers: handler.addHandler(...)`

**What to Log:**
- Access decisions (allow/deny) with pseudonymized actor hash + reason
- Errors with full context + detail
- State transitions (startup, config loaded, etc.)
- **DO NOT log:** secrets, JWTs, API keys, raw UPN/e-mail, auth tokens, IBAN, NIR

**PII Redaction (onix-actions only):**
- All logs through `onix.actions.*` automatically redacted via `safe_logger.install()`
- Patterns redacted: JWT, IBAN (FR), NIR (FR SSN), e-mail, phone, card numbers, Bearer/API-Key
- Redaction is **irrevocable**: replaced with `[REDACTED_JWT]`, `[REDACTED_EMAIL]`, etc.
- Anti-CRLF: `\n` and `\r` escaped to `\\n`, `\\r` (prevents log forging, CWE-117)

**Example (from codebase):**
```python
logger = logging.getLogger("onix.gateway.audit")
logger.info("Access decision: %s user %s to sets %s", decision, actor_hash, sets)
# Output: "INFO:onix.gateway.audit:Access decision: allow user abc123def456 to sets […]"
```

## Function Design

**Size:**
- Prefer small functions (< 30 lines)
- Complex logic factored into named helpers
- Example: `resolve_principal()` delegates to `_parse_oidc_claims()`, `_user_id_from_claims()`, etc.

**Parameters:**
- Required: positional arguments
- Optional/configuration: keyword-only (after `*`) — `def claims(*, oid="...", upn="...", groups=None)`
- Type hints mandatory: `principal: _PrincipalLike`, `doc_id: str`, `groups: list[str]`
- Defaults document intent: `default_policy="deny"` shows fail-closed default
- Named parameters in calls for clarity: `acl.is_authorized(doc_id, principal)`

**Return Values:**
- Single responsibility: return one thing
- Optional return: `-> Optional[list[str]]` (None = "not exploitable")
- Structured returns: use `@dataclass` or Protocol
- Example: `Principal(user_id, upn, group_ids, source)` — immutable, clear contract
- Tuples for unpacking: `return str(user_id), (str(upn) if upn else None)`
- Never return True/False for errors; raise exceptions

**Async Functions:**
- Used for I/O: HTTP calls, Graph queries, database ops
- Event loop management: tests provide `run()` helper — `run(coro)` = `asyncio.run(coro)` on fresh loop
- Context managers: `async with httpx.AsyncClient() as http:`

## Module Design

**Exports:**
- Private symbols: prefix with underscore: `_FakeResponse`, `_salt()`, `_TTLCache`
- Public API at module level; internal helpers below
- No `__all__` enforcement (implicit: not-prefixed-with-underscore = public)
- Example: `doc_acl` exports `StaticDocACL`, `CompositeDocACL`, `filter_citations`, `REFUSAL_NO_ACCESSIBLE_SOURCE`

**Barrel Files:**
- Minimal use; imports explicit, not hidden
- `__init__.py` typically empty or minimal re-exports (e.g., `__version__`)
- Tests call `importlib.reload(config)` then `importlib.reload(main)` to reset state

**Lifecycle & Singletons:**
- Stateless module imports (no side effects on import)
- Lazy initialization via FastAPI lifespan: `@asynccontextmanager async def _lifespan(app):`
- Read-only singletons cached: `@lru_cache(maxsize=1) def _salt() -> bytes:`
- Database, HTTP, cached group maps opened in lifespan, closed on shutdown
- Example: `admin_state.init_db()`, `usage_tracker.init_db()` called in `_lifespan`

## Stdlib-First Principle

**Non-negotiable (AGENTS.md §4):**
- Use Python standard library before external packages
- Examples in codebase:
  - `re` (not external regex)
  - `json` (not orjson)
  - `logging` standard (not loguru)
  - `dataclasses` (not attrs)
  - `abc.ABC` + `@abstractmethod`

**Exceptions (justified):**
- FastAPI (no sync alternative for async web)
- httpx (async HTTP; urllib is sync)
- Pytest (industry standard testing)
- Pydantic (FastAPI ecosystem)
- prometheus_client (observability gate)

## Dataclass & Type Patterns

**Immutable Value Objects:**
```python
@dataclass(frozen=True)
class Principal:
    user_id: str
    upn: Optional[str]
    group_ids: list[str]
    source: str  # "claims" | "graph"
```

**Protocol for Duck Typing (minimal interface):**
```python
class _PrincipalLike(Protocol):
    user_id: str
    upn: Optional[str]
    group_ids: list[str]
```

**Status/Enum Patterns:**
- Literal strings constrained in docstrings (not `enum.Enum`)
- Example: `decision: str  # "allow" | "deny"`
- Example: `source: str  # "claims" | "graph"`

---

*Convention analysis: 2026-06-19*
