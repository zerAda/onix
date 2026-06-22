# onix — Cycle 1 : Sécurité applicative — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fermer 4 vulnérabilités/blocages de sécurité bloquant l'audit prod, en **fail-closed**, avec tests de non-régression et gates verts.

**Architecture:** Corrections chirurgicales, scope par scope (`actions`, `access-gateway`, supply-chain). Chaque correctif est **fail-closed** (en cas de doute → refus/alerte, jamais d'acceptation silencieuse) et **testé** d'abord (TDD). Chaque modif de code de scope **met à jour** sa doc (`docs/scopes/<scope>.md` + `docs/audit-reality/<scope>.md` + `ralph/state/<scope>.md`) pour passer `make docs-freshness`.

**Tech Stack:** Python 3 (stdlib-first : `hmac`, `hashlib`, `secrets`), FastAPI (access-gateway), SQLite/Postgres (actions audit), pytest. Aucune nouvelle dépendance sauf bump CVE.

**Invariants (rappel CLAUDE.md/SECURITY.md):** fail-closed · zéro secret en repo (`.env` gitignoré) · zéro mock présenté comme réel · commentaires en français.

---

## File Structure

| Fichier | Responsabilité | Action |
|---|---|---|
| `actions/app/audit_log.py` | Chaîne d'audit HMAC tamper-evident | Modifier `verify_chain()` (l.~187-194) : politique fail-closed anti-downgrade |
| `actions/tests/test_audit_log.py` | Tests chaîne d'audit | Ajouter tests downgrade/clé-absente |
| `access-gateway/app/config.py` | `Settings` (env) | Ajouter `proxy_shared_secret` + `allow_unauth_header` |
| `access-gateway/app/identity.py` | Résolution identité/groupes | `resolve_principal()` : exiger preuve proxy (secret partagé), fail-closed |
| `access-gateway/app/main.py` | Endpoints `/v1/*` | Passer l'en-tête `X-OIDC-Proxy-Secret` à `resolve_principal` (4 call-sites) |
| `access-gateway/tests/test_identity.py` | Tests identité | Ajouter tests spoof rejeté / secret OK |
| (M3) filtre citations Fabric | ACL par-doc | À localiser puis câbler (tâche M3-0) |
| `requirements*.txt` / pins | Dépendances | Bump CVE pip-audit |

---

## Task M1 : Audit chain fail-closed (anti algo-downgrade)

**Vuln (M1) :** `verify_chain()` (`actions/app/audit_log.py:189`) recalcule le hash avec l'algo **stocké par ligne** (`row["algo"]`). Un attaquant qui écrit en base met `algo='sha256'` (keyless), recalcule `entry_hash` **sans la clé HMAC**, et la chaîne « vérifie » quand même → la protection HMAC est **silencieusement dégradée en keyless**. Toute la garantie cryptographique tombe.

**Fix :** quand une clé HMAC est configurée (`_audit_secret() is not None`), la chaîne **DOIT** être 100 % `hmac-sha256`. Toute ligne en `sha256` (ou algo autre) = **downgrade détecté = rupture**. Si pas de clé : best-effort `sha256`, mais une ligne `hmac-sha256` (clé disparue) = indéterminable = rupture. **Jamais** laisser l'algo de la ligne réduire la garantie.

**Files:**
- Modify: `actions/app/audit_log.py:179-195` (`verify_chain`)
- Test: `actions/tests/test_audit_log.py`

- [ ] **Step 1 — Test d'échec : downgrade détecté**

```python
def test_verify_chain_detecte_downgrade_keyless_quand_cle_presente(monkeypatch, tmp_path):
    # Base d'audit isolée + clé HMAC configurée.
    monkeypatch.setenv("ONIX_ACTIONS_AUDIT_HMAC_KEY", "cle-de-test-32-octets-minimum!!")
    # (réutiliser le harnais d'isolation DB existant des autres tests du fichier)
    import importlib, app.audit_log as al; importlib.reload(al)
    rec = {k: f"v_{k}" for k in al._SIGNED_FIELDS}
    al.append_audit(rec)  # écrit en hmac-sha256
    # Attaquant : modifie le contenu + réécrit entry_hash en SHA-256 keyless + algo='sha256'.
    forged = dict(rec, action="ESCALADE_PRIV", result="ok")
    forged_hash = al.hashlib.sha256((al._GENESIS + al._canonical(forged)).encode("utf-8")).hexdigest()
    with al._lock, al._connect() as conn:
        conn.execute("UPDATE admin_audit SET action=?, result=?, entry_hash=?, algo='sha256' WHERE seq=1",
                     ("ESCALADE_PRIV", "ok", forged_hash))
        conn.commit()
    res = al.verify_chain()
    assert res["ok"] is False
    assert "downgrade" in (res.get("reason") or "").lower()
```

- [ ] **Step 2 — Lancer le test, vérifier l'échec**

Run: `cd actions && python -m pytest tests/test_audit_log.py -k downgrade -q`
Expected: FAIL (actuellement `verify_chain` honore `algo='sha256'` → `ok=True`).

- [ ] **Step 3 — Implémenter le fix fail-closed**

Remplacer dans `verify_chain()` la résolution `row_algo`/recompute (l.~187-191) par :

```python
        key_present = _audit_secret() is not None
        stored_algo = (row.get("algo") or "").strip().lower()
        if key_present:
            # Clé configurée => politique HMAC stricte. Une ligne keyless (sha256)
            # est une tentative de DOWNGRADE (recalculable sans la clé) -> rupture.
            if stored_algo and stored_algo != "hmac-sha256":
                return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                        "reason": f"algo downgrade détecté (clé présente, ligne en '{stored_algo}')"}
            verify_algo = "hmac-sha256"
        else:
            # Pas de clé : best-effort sha256. Une ligne HMAC est invérifiable -> rupture.
            if stored_algo == "hmac-sha256":
                return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                        "reason": "ligne hmac-sha256 mais clé absente : vérification impossible"}
            verify_algo = "sha256"
        recomputed = compute_entry_hash(prev, row, verify_algo)
```

- [ ] **Step 4 — Lancer les tests du fichier, tout vert**

Run: `cd actions && python -m pytest tests/test_audit_log.py -q`
Expected: PASS (downgrade détecté + non-régression chaîne normale hmac et chaîne keyless pure).

- [ ] **Step 5 — MAJ docs (docs-freshness) + commit**

MAJ : `docs/scopes/actions.md`, `docs/audit-reality/actions.md` (preuve `audit_log.py:verify_chain` fail-closed), `ralph/state/actions.md`.
```bash
git add actions/app/audit_log.py actions/tests/test_audit_log.py docs/scopes/actions.md docs/audit-reality/actions.md ralph/state/actions.md
git commit -m "fix(actions): audit chain fail-closed contre algo-downgrade keyless [M1]"
```

---

## Task M7 : Rejeter X-OIDC-Claims falsifié (preuve proxy obligatoire)

**Vuln (M7) :** `resolve_principal()` (`identity.py:140`) fait confiance à `X-OIDC-Claims` **verbatim**. Tout client atteignant la gateway directement forge `{"oid":"…","groups":["<guid-admin>"]}` → **usurpation d'identité + bypass RBAC total**. Le proxy est « supposé » vérifier mais rien ne le **prouve** côté gateway.

**Fix (fail-closed) :** exiger un **secret partagé** prouvant le transit par le proxy de confiance. Le proxy ajoute `X-OIDC-Proxy-Secret: <secret>` ; la gateway compare en **temps constant** à `GATEWAY_PROXY_SHARED_SECRET`. Si secret configuré et header absent/incorrect → `IdentityError` (refus). Si secret **non** configuré → refus aussi (fail-closed), sauf override dev explicite `GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true`.

**Files:**
- Modify: `access-gateway/app/config.py` (Settings : `proxy_shared_secret`, `allow_unauth_header`)
- Modify: `access-gateway/app/identity.py:128-143` (`resolve_principal` : nouveau param + check)
- Modify: `access-gateway/app/main.py` (4 call-sites : passer le header `X-OIDC-Proxy-Secret`)
- Test: `access-gateway/tests/test_identity.py`

- [ ] **Step 1 — Test d'échec : header forgé sans secret = refus**

```python
import pytest
from app.identity import resolve_principal, IdentityError
from app.config import Settings

@pytest.mark.asyncio
async def test_claims_sans_preuve_proxy_sont_rejetes(monkeypatch):
    monkeypatch.setenv("GATEWAY_PROXY_SHARED_SECRET", "secret-proxy-attendu")
    s = Settings()  # adapter à la construction réelle de Settings du repo
    forged = '{"oid":"attacker","groups":["GUID-ADMIN"]}'
    with pytest.raises(IdentityError):
        await resolve_principal(s, oidc_claims_header=forged, proxy_secret_header=None)
    with pytest.raises(IdentityError):
        await resolve_principal(s, oidc_claims_header=forged, proxy_secret_header="mauvais")

@pytest.mark.asyncio
async def test_claims_avec_bon_secret_passent(monkeypatch):
    monkeypatch.setenv("GATEWAY_PROXY_SHARED_SECRET", "secret-proxy-attendu")
    monkeypatch.setenv("GATEWAY_GROUP_SOURCE", "claims")
    s = Settings()
    ok = '{"oid":"u1","groups":["G1"]}'
    p = await resolve_principal(s, oidc_claims_header=ok, proxy_secret_header="secret-proxy-attendu")
    assert p.user_id == "u1" and "G1" in p.group_ids
```

- [ ] **Step 2 — Lancer, vérifier l'échec**

Run: `cd access-gateway && python -m pytest tests/test_identity.py -k proxy -q`
Expected: FAIL (`resolve_principal` n'a pas encore le param `proxy_secret_header`).

- [ ] **Step 3 — Implémenter le check fail-closed**

Dans `config.py`, ajouter à `Settings` (lire env) : `proxy_shared_secret: Optional[str]` (env `GATEWAY_PROXY_SHARED_SECRET`) et `allow_unauth_header: bool` (env `GATEWAY_ALLOW_UNAUTHENTICATED_HEADER`, défaut False).

Dans `identity.py`, signature + garde en tête de `resolve_principal` :

```python
import hmac as _hmac

async def resolve_principal(
    settings: Settings,
    *,
    oidc_claims_header: Optional[str],
    proxy_secret_header: Optional[str] = None,
    cache: Optional[_TTLCache] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Principal:
    # Anti-spoof : prouver que la requête a transité par le proxy de confiance
    # AVANT de faire confiance au moindre claim (fail-closed).
    expected = (settings.proxy_shared_secret or "").strip()
    if expected:
        if not proxy_secret_header or not _hmac.compare_digest(proxy_secret_header.strip(), expected):
            raise IdentityError("Preuve proxy invalide/absente : X-OIDC-Claims rejeté (anti-spoof).")
    elif not settings.allow_unauth_header:
        raise IdentityError(
            "GATEWAY_PROXY_SHARED_SECRET non configuré : refus de faire confiance à "
            "X-OIDC-Claims (fail-closed). Définir le secret partagé proxy, ou "
            "GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true en dev uniquement."
        )
    claims = parse_oidc_claims(oidc_claims_header)
    ...  # suite inchangée
```

- [ ] **Step 4 — Câbler les 4 call-sites de `main.py`**

Pour chaque endpoint lisant `X-OIDC-Claims` (l.~253, 279/282 via `_principal_and_sets`, 308, 515), ajouter le header et le passer :
```python
    x_proxy_secret: Optional[str] = Header(default=None, alias="X-OIDC-Proxy-Secret"),
    ...
    principal = await resolve_principal(settings, oidc_claims_header=x_oidc_claims,
                                        proxy_secret_header=x_proxy_secret, cache=...)
```
(et propager `proxy_secret_header` dans le helper `_principal_and_sets`).

- [ ] **Step 5 — Tests verts (identité + non-régression fail-closed)**

Run: `cd access-gateway && python -m pytest tests/test_identity.py tests/test_failclosed.py -q`
Expected: PASS.

- [ ] **Step 6 — MAJ docs + déploiement (env template) + commit**

MAJ : `docs/scopes/access-gateway.md`, `docs/audit-reality/access-gateway.md` (preuve `identity.py:resolve_principal` anti-spoof), `ralph/state/access-gateway.md`, et `deploy/prod/env.prod.template` + `deploy/prod/nginx.prod.conf`/`Caddyfile` (le proxy doit injecter `X-OIDC-Proxy-Secret`). **Documenter** : le proxy doit **stripper** tout `X-OIDC-*` entrant client puis ré-injecter.
```bash
git add access-gateway/app/config.py access-gateway/app/identity.py access-gateway/app/main.py access-gateway/tests/test_identity.py docs/scopes/access-gateway.md docs/audit-reality/access-gateway.md ralph/state/access-gateway.md deploy/prod/
git commit -m "fix(access-gateway): exiger preuve proxy pour X-OIDC-Claims, anti-spoof RBAC [M7]"
```

---

## Task M3 : Câbler l'ACL Fabric au filtre de citations

- [ ] **Step 0 — Localiser** le filtre de citations et le point d'application ACL Fabric.
Run: `grep -rniE "fabric|citation|document_set" access-gateway/app | grep -iE "acl|filter|citation"`
Identifier le fichier + la fonction qui filtre les documents/citations renvoyés à l'utilisateur.

- [ ] **Step 1 — Test d'échec** : un document Fabric hors-périmètre de l'utilisateur **ne doit pas** apparaître en citation (deny-by-default). Écrire le test reproduisant une citation fuitée.

- [ ] **Step 2 — Vérifier l'échec** (la citation fuite aujourd'hui).

- [ ] **Step 3 — Implémenter** : appliquer l'ACL par-doc Fabric (groupes du `Principal`) au filtre de citations, **fail-closed** (si ACL indéterminable → exclure).

- [ ] **Step 4 — Tests verts** (`access-gateway/tests`).

- [ ] **Step 5 — MAJ docs (access-gateway scope) + commit** `fix(access-gateway): ACL Fabric appliquée aux citations, deny-by-default [M3]`.

---

## Task SUPPLY : pip-audit --strict vert

- [ ] **Step 0 — Identifier** la CVE : `pip-audit --strict` (ou `make` cible équivalente) → repérer le paquet pinné vulnérable.

- [ ] **Step 1 — Bumper** vers la version patchée minimale dans le pin concerné (`requirements*.txt`/contrainte), sans casser les imports.

- [ ] **Step 2 — Vérifier** : `pip-audit --strict` → **0 CVE** ; suites offline impactées toujours vertes.

- [ ] **Step 3 — Commit** `fix(deps): bump <pkg> pour CVE <id>, pip-audit --strict vert`.

---

## Self-Review (couverture spec)
- M1 (audit downgrade) → Task M1 ✅ · M7 (X-OIDC spoof) → Task M7 ✅ · M3 (ACL citations) → Task M3 ✅ · supply-chain → Task SUPPLY ✅.
- Tous les correctifs : **fail-closed**, **test d'abord**, **docs-freshness** incluse, **commit atomique**.
- Gates de sortie de cycle (lancés au land) : `bandit` 0 medium+ · `gitleaks` 0 · `pip-audit --strict` 0 · suites `actions/tests` + `access-gateway/tests` vertes.
