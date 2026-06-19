# Testing Patterns

**Analysis Date:** 2026-06-19

## Test Framework

**Runner:**
- Framework: `pytest` (latest pinned version in `requirements.txt`)
- Config: No central `pytest.ini` or `setup.cfg`; configuration via Makefile
- Installed via `make rag-deps` or suite-specific `requirements.txt`
- Assertion library: Python standard `assert` statements (pytest native)

**Run Commands:**
```bash
make test              # All three suites: actions, access-gateway, tests/rag
make rag-test          # Contract mode (offline, no LLM) — CI gate
make rag-test-live     # Live mode (real Onyx API, requires ONIX_RAG_LIVE=1)
make rag-eval          # RAGAS evaluation (LLM-judge via Ollama)
make rag-eval-ci       # Full CI: absolute + relative thresholds

# Individual suites
pytest -q actions/tests
pytest -q access-gateway/tests
pytest -q tests/rag

# Coverage (if pytest-cov installed)
pytest --cov=access-gateway access-gateway/tests
pytest --cov=actions actions/tests
```

Watch mode: Not configured.

## Test File Organization

**Location patterns:**
- `access-gateway/tests/` — gateway tests (mirrors `access-gateway/app/`)
- `access-gateway/tests/e2e/` — live access/Fabric e2e harness (standalone scripts)
- `actions/tests/` — actions service tests (mirrors `actions/app/`)
- `tests/rag/` — standalone RAG red-team + contract suite (no LLM by default)
- `tests/rag/ragas_eval/` — RAGAS quality evaluation harness (live LLM-judge)

**Naming:**
- Test modules: `test_<component>.py` (e.g., `test_api.py`, `test_fabric_acl.py`, `test_security_rgpd.py`)
- Standalone runners: `run_<scenario>.py` (e.g., `run_access_e2e.py`, `run_live.py`)
- Test functions: `test_<what>()` (e.g., `test_static_acl_loads_from_file()`)
- Test classes: Not used (tests are flat functions)

**Directory Structure:**
```
access-gateway/
├── app/
│   ├── main.py
│   ├── doc_acl.py
│   ├── fabric_acl.py
│   ├── identity.py
│   └── config.py
└── tests/
    ├── conftest.py              # Shared fixtures
    ├── test_api.py              # Endpoints
    ├── test_doc_acl.py          # Document ACL filtering (FOSS)
    ├── test_fabric_acl.py       # Fabric/OneLake ACL (EE)
    ├── test_fabric_client.py    # Fabric client (connectivity)
    ├── test_graph_acl.py        # SharePoint ACL (Graph)
    ├── test_guardrail.py        # Post-filter guardrails
    ├── test_identity.py         # Identity resolution
    ├── test_cache.py            # RBAC-safe caching
    ├── test_integration_*.py    # Multi-component tests
    └── e2e/
        ├── README_ACCESS_E2E.md  # Live e2e documentation
        ├── run_access_e2e.py     # Live SharePoint + Fabric proof

actions/tests/
├── conftest.py              # Shared fixtures
├── test_api.py              # Endpoints (audit, docgen, admin)
├── test_audit_engine.py     # Audit logic
├── test_security_rgpd.py    # WS2 hardening (PII, HMAC, DLP, retention)
├── test_finops_tokens.py    # Token/cost tracking
└── test_integration_*.py    # Multi-component tests

tests/rag/
├── conftest.py              # Shared fixtures (prompt, dataset, live mode)
├── test_red_team.py         # 20 red-team vectors (LLM01/02/etc.)
├── test_postfilter.py       # Guardrail post-filter logic
├── test_prompt_contract.py  # Prompt syntax + presence of guardrails
├── test_eval_dataset.py     # Dataset consistency
└── ragas_eval/
    ├── judge.py             # Scripted LLM-judge (offline)
    ├── metrics.py           # Faithfulness/context_precision/answer_relevancy
    ├── runner.py            # Main evaluation harness (live)
    ├── test_ragas_eval.py   # Judge + metrics offline tests
    └── golden_fr.json       # Golden eval dataset (FR)
```

## Test Structure

**Suite Organization:**
All tests follow this pattern:

```python
"""Module docstring — scope and coverage."""
from __future__ import annotations

import pytest
from conftest import claims, GROUP_NORD  # Shared fixtures/helpers

def test_something(client):
    """Concise description of what's tested."""
    r = client.get("/endpoint")
    assert r.status_code == 200
    assert r.json()["field"] == "expected"

def test_error_case(client):
    """Error handling."""
    r = client.post("/endpoint", json={})
    assert r.status_code == 400
```

**Patterns:**
- **Setup** (via fixtures): Isolated environment per test
  - Environment isolation: `monkeypatch.setenv()`, `tmp_path` for temp files/DB
  - Module reloading: `importlib.reload(config); importlib.reload(main)` to pick up env vars
  - HTTP mocking: `monkeypatch.setattr(main.app.state.http, "post", _fake_post)`
- **Execution**: Test function body with assertions
- **Teardown**: Automatic (fixtures cleaned after `yield`)

**Fixture Scopes:**
- `function` (default): Isolated environment per test (most fixtures)
- `session` (rarely): `prompt_md`, `prompt_block`, `dataset` in RAG (immutable data)

## Mocking

**Framework:** `pytest.monkeypatch` (no external mock library)

**Patterns:**

### HTTP Mocking (access-gateway):
```python
async def _fake_post(url, json=None, headers=None, **kwargs):
    captured["url"] = url
    captured["payload"] = json
    return _FakeResponse(200, {"answer": "relayed"})

monkeypatch.setattr(main.app.state.http, "post", _fake_post)
client.last_upstream = captured  # For assertions
```

### Environment Mocking (actions):
```python
monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
monkeypatch.setenv("ONIX_ACTIONS_API_KEY", "test-key-0123456789")
monkeypatch.delenv("ONIX_ACTIONS_ADMIN_KEY", raising=False)
```

### Module State Reloading:
```python
import app.admin_state as admin_state
importlib.reload(admin_state)
for modname in ("app.usage_tracker", "app.tasks", "app.security"):
    importlib.reload(importlib.import_module(modname))
import app.main as main
importlib.reload(main)
```

**What to Mock:**
- External HTTP calls (Graph, Onyx upstream, webhooks)
- Time-dependent behavior (if needed via env)
- HTTP transport (use `_FakeResponse` class, not unittest.mock)

**What NOT to Mock:**
- Core business logic (test real implementation)
- Validation/error handling (test real exceptions)
- Database operations (use real SQLite in tmp_path)
- FastAPI app structure (use TestClient instead)

## Fixtures and Factories

**Access-Gateway Fixtures (`access-gateway/tests/conftest.py`):**

```python
@pytest.fixture()
def mapping_file(tmp_path):
    """Group → Document Set mapping (JSON)."""
    path = tmp_path / "group_map.json"
    path.write_text(json.dumps({
        "version": 1,
        "groups": {GROUP_NORD: {"document_sets": ["clients-nord"]}},
    }), encoding="utf-8")
    return str(path)

@pytest.fixture()
def env(tmp_path, mapping_file, monkeypatch):
    """Isolated environment (claims mode, no Graph)."""
    monkeypatch.setenv("GATEWAY_ONYX_BASE_URL", "http://onyx.test:8080")
    monkeypatch.setenv("GATEWAY_MAPPING_PATH", mapping_file)
    return tmp_path

@pytest.fixture()
def client(env, monkeypatch):
    """TestClient with mocked Onyx upstream."""
    from fastapi.testclient import TestClient
    import app.config as config
    import app.main as main
    
    importlib.reload(config)
    importlib.reload(main)
    
    captured = {}
    async def _fake_post(url, json=None, headers=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse(200, {"answer": "relayed"})
    
    with TestClient(main.app) as c:
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        c.last_upstream = captured
        yield c

@pytest.fixture()
def client_factory(env, monkeypatch):
    """Factory to control upstream response (for post-filter testing)."""
    def _factory(upstream_response, status=200):
        # Returns TestClient with state["upstream"] injectable
        ...
    yield _factory
```

**Actions Fixtures (`actions/tests/conftest.py`):**

```python
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated DB + temp directories + permissive flags (historical compat)."""
    monkeypatch.setenv("ONIX_ACTIONS_API_KEY", "test-key-0123456789")
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("ONIX_ACTIONS_ADMIN_KEY_OPTIONAL", "true")
    # Egress: localhost only, HTTP allowed, private IP allowed
    monkeypatch.setenv("ONIX_EGRESS_ALLOWLIST", "127.0.0.1,localhost")
    monkeypatch.setenv("ONIX_EGRESS_ALLOW_HTTP", "true")
    monkeypatch.setenv("ONIX_EGRESS_ALLOW_PRIVATE_IP", "true")
    return tmp_path

@pytest.fixture()
def client(env):
    """TestClient with isolated DB (startup triggered)."""
    from fastapi.testclient import TestClient
    import app.admin_state
    import app.security
    import app.main
    
    importlib.reload(app.admin_state)
    for modname in ("app.usage_tracker", "app.tasks", "app.security"):
        importlib.reload(importlib.import_module(modname))
    importlib.reload(app.security)
    importlib.reload(app.main)
    app.security.reset_rate_limits()
    
    with TestClient(app.main.app) as c:
        c.headers.update({"X-API-Key": "test-key-0123456789"})
        yield c
```

**RAG Fixtures (`tests/rag/conftest.py`):**

```python
@pytest.fixture(scope="session")
def prompt_md() -> str:
    """Raw markdown content of agent_commercial_systeme.md."""
    return read_prompt_markdown()

@pytest.fixture(scope="session")
def prompt_block() -> str:
    """Extracted prompt block (content between ``` markers)."""
    return read_prompt_block()

@pytest.fixture(scope="session")
def dataset() -> dict:
    """Evaluation dataset (golden set)."""
    return load_dataset()

requires_live = pytest.mark.skipif(
    not live_enabled(),
    reason="Mode live disabled (set ONIX_RAG_LIVE=1 + ONIX_API_URL).",
)
```

**Test Data Location:**
- `access-gateway/tests/conftest.py` — gateway-specific fixtures
- `actions/tests/conftest.py` — actions-specific fixtures
- `tests/rag/conftest.py` — RAG fixtures + live mode detection
- `tests/rag/ragas_eval/conftest.py` — judge initialization

## Coverage

**Requirements:** No explicit coverage target enforced (no pytest-cov configuration)

**View Coverage (if pytest-cov installed):**
```bash
pytest --cov=access-gateway access-gateway/tests
pytest --cov=actions actions/tests
```

**Tier breakdown:**
- **Unit tests** (offline): `actions/tests`, `access-gateway/tests`, `tests/rag` (contract mode)
  - No network, no LLM, all dependencies mocked
  - ~2–5 seconds total
- **Integration tests**: `**/test_integration_*.py` + `tests/rag/test_*.py`
  - Real SQLite, real data structures, cross-component calls
  - ~5–10 seconds total
- **E2E/Live tests**: `access-gateway/tests/e2e/run_access_e2e.py`, `tests/rag` (live mode)
  - Real Microsoft Entra / Fabric / Onyx API
  - Manual trigger (`ONIX_RAG_LIVE=1`), not automatic CI
  - 30+ seconds per run

## Test Types

**Unit Tests (most):**
- Scope: Single function/class
- Dependencies: Mocked or stubbed
- Speed: Instant (< 100ms per test)
- Example: `test_redact_text_couvre_jwt_iban_nir_email()` — validates PII redaction patterns
- Example: `test_static_acl_loads_from_file()` — JSON loading + authorization decision

**Integration Tests:**
- Scope: Multiple components (e.g., cache + ACL, audit + logger)
- Dependencies: Real SQLite (tmp_path), real structures
- Speed: < 500ms per test
- Example: `test_integration_cache_acl()` — cache key generation + ACL intersection
- Example: `test_integration_streaming()` — streaming response + post-filter

**Contract Tests (RAG specific):**
- Scope: Prompt structure + dataset consistency + red-team vectors
- Dependencies: None (filesystem only)
- Speed: < 2 seconds
- Runs by default in `make rag-test`
- Purpose: Ensure prompt guardrails are syntactically present before live testing
- Example: `test_RT01_injection_documentaire_defense_present()` — assert defense strings in prompt

**E2E/Live Tests:**
- Scope: Full stack (gateway + Onyx + LLM)
- Dependencies: Real Onyx API, real Ollama LLM, real Microsoft Graph / Fabric API
- Speed: 10+ seconds per test
- Trigger: `ONIX_RAG_LIVE=1 make rag-test-live` or `ONIX_E2E_TENANT_ID=... python run_access_e2e.py`
- Skipped by default (marked with `@requires_live`)
- Purpose: Prove actual connectivity + RBAC fail-closed behavior (not mocked)
- Example: `run_access_e2e.py` — SharePoint + Fabric scenarios A1–A3, B1–B5

**RAGAS Evaluation:**
- Scope: Quality metrics (faithfulness, context_precision, answer_relevancy)
- Dependencies: Live Ollama LLM-judge + real Onyx chat API
- Speed: 60+ seconds (live judgment)
- Trigger: `ONIX_LIVE_OLLAMA=1 make rag-eval`
- Artifacts: `scores.json` (per-item scores), baseline comparison
- Purpose: Detect quality degradation; gate on absolute + relative thresholds

## Common Patterns

**Async Testing:**
```python
def run(coro):
    """Execute coroutine on fresh event loop."""
    import asyncio
    return asyncio.run(coro)

# Usage
def test_resolve_principal(client):
    result = run(resolve_principal(...))
    assert result.user_id == "expected"
```

**Error Testing:**
```python
def test_user_without_mapped_group_is_denied(client):
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers={"X-OIDC-Claims": claims(oid="u2", groups=["unknown"])},
    )
    assert r.status_code == 403
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
    # Verify upstream payload was modified
    relayed = client.last_upstream["payload"]
    assert relayed["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]
```

**Database Testing (actions):**
```python
def test_audit_journal_tamper_evident(client):
    """Verify HMAC chain: record i+1 includes HMAC of record i."""
    r1 = client.post("/audit", json={"document": {...}, "reference": {...}})
    r2 = client.post("/audit", json={"document": {...}, "reference": {...}})
    
    # Fetch from DB, verify chain
    db = sqlite3.connect(db_path)
    records = db.execute("SELECT id, hmac_prev FROM audit_log ORDER BY id").fetchall()
    assert records[1][1] == compute_hmac(records[0])  # Chain verified
```

**Mocking Decisions (audit log testing):**
```python
class _AuditRecorder:
    """Capture audit calls for verification."""
    def __init__(self):
        self.calls = []
    
    def log_doc_acl_decision(self, **kwargs):
        self.calls.append(kwargs)
        return {"event": "doc_acl_decision", **kwargs}

acl = StaticDocACL.from_obj({...})
recorder = _AuditRecorder()
# Pass recorder to function, verify calls
```

**Parametrized Tests:**
Not used extensively. Instead, tests are flat functions for clarity:
```python
def test_case_1(client): ...
def test_case_2(client): ...
def test_case_3(client): ...
```
(vs. `@pytest.mark.parametrize("input,expected", [...])`)

**Environment-Driven Tests (RAG):**
```python
def live_enabled() -> bool:
    return os.getenv("ONIX_RAG_LIVE", "").strip().lower() in {"1", "true", "yes"}

requires_live = pytest.mark.skipif(
    not live_enabled(),
    reason="Live mode disabled (set ONIX_RAG_LIVE=1 + ONIX_API_URL).",
)

@requires_live
def test_live_red_team_vector_rt01(api_url):
    """Inject RT01 payload, verify guardrail blocks output."""
    ...
```

## Quality Gates (CI)

**make test sequence:**
1. `pytest -q access-gateway/tests` — all gateway tests
2. `pytest -q actions/tests` — all actions tests
3. `pytest -q tests/rag` — contract + red-team (offline, no LLM)
4. `bandit -r access-gateway/app actions/app` — security linting (medium+)
5. `pip-audit --strict` — 0 CVE in locked requirements
6. `gitleaks detect --verbose` — 0 secrets in repo
7. `docker compose config -q` — compose validation (all variants)
8. `helm lint deploy/k8s/onix-ha` — Helm chart validation
9. `trivy filesystem .` — container + code vulnerability scan

Exit: 0 = all pass, 1 = any fail (stops pipeline)
Time: ~30 seconds (offline)

**make rag-eval-ci (nightly):**
1. Load golden eval dataset
2. Fire requests against live Onyx API (requires `ONIX_API_URL`)
3. Collect responses, judge with LLM (Ollama)
4. Compute: faithfulness, context_precision, answer_relevancy (per-item + aggregate)
5. Gate logic:
   - Absolute: faithfulness ≥ 0.90, context_precision ≥ 0.70, answer_relevancy ≥ 0.85
   - Relative: compare vs baseline (5% tolerance)
6. Output: `scores.json` (for review), pass/fail decision
7. Exit: 0 = pass, 1 = fail (stops CI)

## Test Execution (E2E Access Harness)

**`access-gateway/tests/e2e/run_access_e2e.py`:**
- Standalone Python script (not pytest)
- **Reuses deployed code**: `app.graph_client`, `app.graph_acl`, `app.fabric_client`, `app.fabric_acl`, `app.config`
- **Zero secrets in repo**: All credentials via env vars (`ONIX_E2E_*`)
- **Scenarios**: SharePoint (A1–A3) + Fabric (B1–B5)
  - A1: Connectivity (jeton Graph, list drive items)
  - A2: RBAC allowed (user with permission → granted)
  - A3: RBAC denied (user without permission → denied, fail-closed)
  - B1: Fabric connectivity (list workspaces/items)
  - B2: OneLake connectivity (list paths, optionally read file)
  - B3: Power BI connectivity (list datasets, optional)
  - B4: RBAC allowed via Fabric (granted)
  - B5: RBAC denied via Fabric (denied, fail-closed)
- **Skip logic**: If env vars missing, scenario marked `SKIP` (rest runs)
- **Exit codes**: 0 = all present blocks pass, 1 = fail, 2 = skip total (not an error)
- **Auth modes**: Azure CLI (default, `az login`) or client credentials

---

*Testing analysis: 2026-06-19*
