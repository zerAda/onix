# Testing Patterns

**Analysis Date:** 2026-06-18

## Test Framework

**Runner:**
- Framework: `pytest` (v9.0.3 as of latest pin)
- Config: No central `pytest.ini` or `setup.cfg`; configuration via Makefile targets
- Installed via `make rag-deps` or `pip install -r <suite>/requirements.txt`

**Assertion Library:**
- Standard: `assert` statements (pytest native)
- No special assertion library; plain comparisons and `assert` expressions

**Run Commands:**
```bash
make test              # Run all three test suites: actions, access-gateway, tests/rag
make rag-test          # Contract mode (offline, hors-LLM) — gate for CI
make rag-test-live     # Live mode (against real Onyx API, ONIX_RAG_LIVE=1 required)
make rag-eval          # RAGAS quality evaluation (LLM-judge Ollama, gate quality)
make rag-eval-ci       # Full CI gate: absolute threshold + anti-regression
```

Individual suite runs:
```bash
pytest -q actions/tests
pytest -q access-gateway/tests
pytest -q tests/rag
```

Watch mode: Not used; no pytest-watch configuration.

## Test File Organization

**Location patterns:**
- `access-gateway/tests/` — mirrors `access-gateway/app/` structure
- `actions/tests/` — mirrors `actions/app/` structure
- `tests/rag/` — standalone RAG quality/red-team suite
- `tests/rag/ragas_eval/` — RAGAS scoring harness (live LLM evaluation)

**Naming:**
- Test modules: `test_*.py` (e.g., `test_api.py`, `test_cache.py`)
- Test classes: `Test*` pattern for grouped tests (e.g., `class TestNormalizeQuestion`)
- Test functions: `test_*` (e.g., `test_health`, `test_user_cannot_widen_scope`)

**Directory structure (example):**
```
access-gateway/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── cache.py
│   └── identity.py
└── tests/
    ├── conftest.py          # Shared fixtures
    ├── test_api.py          # Endpoint tests
    ├── test_cache.py        # Cache logic tests
    ├── test_identity.py     # Identity resolution tests
    └── e2e/                 # End-to-end tests (vectors, streaming)
        ├── llm_relay.py
        └── vectors.py
```

## Test Structure

**Suite Organization:**
All test suites follow this pattern:

```python
"""Module docstring — scope and coverage."""
from __future__ import annotations

import pytest
from conftest import fixture_helpers  # Import shared fixtures

class TestComponent:
    """Grouped tests for a component."""
    
    def test_happy_path(self, client):
        """Test successful operation."""
        r = client.get("/endpoint")
        assert r.status_code == 200
    
    def test_error_case(self, client):
        """Test error handling."""
        r = client.post("/endpoint", json={})
        assert r.status_code == 400
```

**Patterns:**
- **Setup** (fixtures): Isolated environment per test via `@pytest.fixture()`
  - Environment isolation: `monkeypatch.setenv()`, `tmp_path` for temp files/databases
  - Module reloading: `importlib.reload(module)` to pick up modified env vars (see `conftest.py` in actions/access-gateway)
- **Teardown**: Automatic (fixtures cleaned up after yield)
- **Mocking**: Monkeypatching (not mock library)
  - Pattern: `monkeypatch.setattr(target, "attribute", fake_impl)`
  - Example: `monkeypatch.setattr(main.app.state.http, "post", _fake_post)` in `access-gateway/tests/conftest.py`

**Fixture scopes:**
- `function` (default): Isolated per test
- `session` (sparingly): `prompt_md`, `dataset` in RAG tests (immutable data loaded once)

## Mocking

**Framework:** `pytest.monkeypatch` (no external mock library)

**Patterns:**

### Mocking HTTP clients (access-gateway):
```python
async def _fake_post(url, json=None, headers=None, **kwargs):
    captured["url"] = url
    captured["payload"] = json
    return _FakeResponse(200, {"answer": "relayed"})

monkeypatch.setattr(main.app.state.http, "post", _fake_post)
```

### Mocking environment (actions):
```python
monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
monkeypatch.delenv("ONIX_ACTIONS_ADMIN_KEY", raising=False)
```

### Mocking module state:
```python
importlib.reload(config)  # Force re-read of env vars
importlib.reload(main)    # Rebuild app with new config
```

**What to Mock:**
- External HTTP calls (Graph, Onyx upstream, webhooks)
- File system operations (use `tmp_path` instead of mocking `open()`)
- Time-dependent behavior (`time.time()` can be frozen with env setup)
- Database calls (for unit tests; integration tests use real SQLite)

**What NOT to Mock:**
- Core business logic (test the real implementation)
- Validation/error handling (test real exceptions raised)
- FastAPI app/routers (use `TestClient` instead)
- Async event loops (use pytest-asyncio or manual `asyncio.run()`)

## Fixtures and Factories

**Test Data (conftest patterns):**

### Access-gateway fixtures:
```python
@pytest.fixture()
def env(tmp_path, mapping_file, monkeypatch):
    """Isolated environment with test config."""
    monkeypatch.setenv("GATEWAY_ONYX_BASE_URL", "http://onyx.test:8080")
    monkeypatch.setenv("GATEWAY_MAPPING_PATH", mapping_file)
    return tmp_path

@pytest.fixture()
def client(env, monkeypatch):
    """TestClient with mocked upstream."""
    from fastapi.testclient import TestClient
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        yield c
```

### Actions fixtures:
```python
@pytest.fixture()
def client(env):
    """TestClient with isolated DB and auth."""
    from fastapi.testclient import TestClient
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        c.headers.update({"X-API-Key": API_KEY})
        yield c
```

### RAG fixtures (shared data):
```python
@pytest.fixture(scope="session")
def prompt_block() -> str:
    """Raw prompt text from agent_commercial_systeme.md."""
    return read_prompt_block()

@pytest.fixture(scope="session")
def dataset() -> dict:
    """Evaluation dataset (loaded once per session)."""
    return load_dataset()
```

**Location:**
- `access-gateway/tests/conftest.py` — gateway-specific fixtures
- `actions/tests/conftest.py` — actions-specific fixtures
- `tests/rag/conftest.py` — RAG fixtures + live mode detection

**Factories (when needed):**
Not used explicitly. Fixtures generate test data inline (simple approach).

## Coverage

**Requirements:** No explicit coverage target enforced (no `pytest-cov` config)

**View Coverage:**
```bash
pytest --cov=access-gateway access-gateway/tests  # If pytest-cov installed
pytest --cov=actions actions/tests
```

**Test suites by tier:**
- **Unit tests** (offline): All three suites (`actions/tests`, `access-gateway/tests`, `tests/rag` contract mode)
- **Integration tests**: `access-gateway/tests/test_integration_*.py`, `actions/tests/test_integration_*.py`
- **E2E/Live tests**: `access-gateway/tests/e2e/`, `tests/rag` live mode (skipped without `ONIX_RAG_LIVE=1`)

## Test Types

**Unit Tests:**
- Scope: Single function or class
- Dependencies: Mocked or stubbed
- Speed: Instant (< 1s per test)
- Example: `test_redact_text_couvre_jwt_iban_nir_email()` in `actions/tests/test_security_rgpd.py`
- Example: `test_idempotence()` in `access-gateway/tests/test_cache.py`

**Integration Tests:**
- Scope: Multiple components + real database/cache
- Dependencies: Real SQLite/Redis (in-memory or temp)
- Speed: Moderate (< 5s per test)
- Example: `test_integration_cache_acl()` in `access-gateway/tests/`
- Example: `test_integration_paths()` in `actions/tests/`

**E2E/Live Tests:**
- Scope: Full stack (gateway + Onyx + LLM)
- Dependencies: Real Onyx API + Ollama
- Speed: Slow (10+ seconds per test)
- Triggered by: `ONIX_RAG_LIVE=1 make rag-test-live`
- Skipped: Default (marked with `@requires_live` in `tests/rag/conftest.py`)

**Contract Tests (RAG specific):**
- Scope: Prompt structure + dataset consistency + red-team vectors
- Dependencies: None (no network, no LLM)
- Speed: < 2 seconds
- Default: Run by `make rag-test` (and CI)
- Purpose: Verify prompt guardrails are syntactically present before live testing

## Common Patterns

**Async Testing:**
```python
# Helper in conftest.py
def run(coro):
    """Execute a coroutine (asyncio.run on a fresh loop)."""
    import asyncio
    return asyncio.run(coro)

# Usage in test
def test_async_operation(client):
    result = run(some_async_function())
    assert result == expected
```

**Error Testing:**
```python
def test_user_without_mapped_group_is_denied(client):
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers={"X-OIDC-Claims": claims(oid="u2", groups=["unknown-group"])},
    )
    assert r.status_code == 403  # Expect denied
```

**Request/Response Testing (FastAPI):**
```python
def test_send_message_forces_document_set_filter(client):
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "Résume le client ABC"},
        headers={"X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD])},
    )
    assert r.status_code == 200
    # Verify the payload relayed upstream was modified
    relayed = client.last_upstream["payload"]
    assert relayed["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]
```

**Parametrized Tests:**
Not used. Instead, tests are written as individual functions for clarity (e.g., `test_idempotence`, `test_collapse_whitespace_and_casing` in separate methods of `TestNormalizeQuestion`).

**Pytest markers:**
- `@requires_live` — skipped unless `ONIX_RAG_LIVE=1` (RAG only)
- `@pytest.mark.skipif(condition, reason="...")` — conditional skip

**Environment-driven tests:**
```python
def live_enabled() -> bool:
    return os.getenv("ONIX_RAG_LIVE", "").strip().lower() in {"1", "true", "yes"}

requires_live = pytest.mark.skipif(
    not live_enabled(),
    reason="Mode live disabled (set ONIX_RAG_LIVE=1 + ONIX_API_URL to test against Onyx).",
)

@requires_live
def test_live_agent_response(api_url):
    ...
```

## Quality Gates

**CI tests (make test target):**
1. `pytest` — all three suites (`actions/tests`, `access-gateway/tests`, `tests/rag`)
2. `bandit` — security linting (severity medium+)
3. `pip-audit --strict` — 0 known CVE in dependencies
4. `gitleaks` — 0 secrets in repo
5. `compose config -q` — Docker Compose validation (all variants)
6. `helm lint` — Helm chart validation
7. `trivy` — filesystem + image vulnerability scan

**RAG evaluation gate (make rag-eval-ci target):**
1. Run RAGAS quality scoring against Ollama judge
2. Apply absolute threshold (faithfulness / context_precision / answer_relevancy)
3. Compare against baseline (anti-regression tolerance 5%)
4. Exit code: 0 = pass, 1 = fail

## Test Execution Flow (CI)

**make test sequence:**
1. All pytest suites run in sequence (no parallelization)
2. Failure in any gate stops pipeline (set -e)
3. Output: pytest summaries + gate status
4. Time: ~30 seconds (offline tests only)

**make rag-eval-ci (nightly):**
1. Download golden eval dataset
2. Fire requests against live Onyx + Ollama
3. Collect scores from LLM-judge
4. Compare vs committed baseline
5. Gate decision: absolute + relative thresholds
6. Artifact: `scores.json` with per-question scores (for diff/review)

---

*Testing analysis: 2026-06-18*
