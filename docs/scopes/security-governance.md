# Scope `security-governance` — dossier agent

> **Mission** : la **sécurité transverse** et la **conformité** — modèle de menaces,
> baseline de durcissement, mapping audit→mitigations, RGPD (registre, DPIA, droits),
> et la **vérité FOSS vs EE** (parité réelle vs assistant cloud d'entreprise). C'est
> le scope « gardien » : il relie les contrôles des autres scopes à une posture cohérente.
> **Sous-agent** : sécurité / conformité / architecture. **État** :
> [`../../ralph/state/security-governance.md`](../../ralph/state/security-governance.md).

Routeur : [`README.md`](README.md) · Projet : [`../../AGENTS.md`](../../AGENTS.md).

## 1. Mission & frontière FOSS/EE

| | |
|---|---|
| **Apporte (FOSS)** | posture « sécurité par défaut » (fail-closed, localhost, runAsNonRoot, egress allowlisté, télémétrie OFF) ; RGPD opérationnel (registre, DPIA, droits) ; traçabilité de la parité (chaque claim ↔ code/audit). |
| **Vérité FOSS vs EE** | l'audit byte-level d'Onyx établit ce qui est **EE/Cloud** (perm-sync, SCIM, chiffrement secrets, analytics) vs **absent** (audit-trail) vs **FOSS**. onix comble **en FOSS** ; ce scope **interdit** de présenter une feature EE comme gratuite. |

## 2. Carte (transverse — docs + contrôles dans les autres scopes)

| Chemin | Rôle |
|---|---|
| [`../../SECURITY.md`](../../SECURITY.md) | **Modèle de sécurité** racine (menaces, contrôles, audit→mitigations, RGPD). |
| [`../SECURITY.md`](../SECURITY.md) | Baseline de durcissement (localhost, services, auth). |
| [`../SECURITY_RGPD_ACTIONS.md`](../SECURITY_RGPD_ACTIONS.md) | Sécurité/RGPD applicative (impl. dans le scope `actions`). |
| [`../RGPD.md`](../RGPD.md) · [`../REGISTRE_TRAITEMENTS.md`](../REGISTRE_TRAITEMENTS.md) · [`../DPIA_TEMPLATE.md`](../DPIA_TEMPLATE.md) | Conformité : droits, registre des traitements, DPIA. |
| [`../audit-onyx/`](../audit-onyx/) | **Audit byte-level d'Onyx** (7 dimensions + verdict) — base factuelle FOSS/EE. |
| [`../PARITE_ENTREPRISE.md`](../PARITE_ENTREPRISE.md) · [`../COMPARATIF_COPILOT_AC360.md`](../COMPARATIF_COPILOT_AC360.md) | Parité vs assistant cloud / Copilot. |
| Gates CI | `bandit`, `gitleaks`, `pip-audit --strict`, `trivy` (cf. `make test`). |

> Les **contrôles** vivent dans les scopes concernés : ACL/cloisonnement →
> [`access-gateway.md`](access-gateway.md) ; audit HMAC/PII/DLP/rétention →
> [`actions.md`](actions.md) ; durcissement infra → [`deploy-ops.md`](deploy-ops.md).
> Ce scope en assure la **cohérence et la conformité**.

## 3. Commandes

```bash
make bandit                          # 0 vulnérabilité medium+
make gitleaks                        # 0 secret
make pip-audit                       # 0 CVE (strict) sur les requirements épinglés
make trivy                           # scan FS/image (CI)
make test                            # barrière complète (inclut les 4 ci-dessus)
```

## 4. Tests & preuves

- Gates sécurité **verts** (`bandit`/`gitleaks`/`pip-audit`/`trivy`) — axe A3 de
  [`../../ralph/ORCHESTRATION.md`](../../ralph/ORCHESTRATION.md).
- **Parité ↔ code** : chaque affirmation de parité doit citer un code/test/avis
  (cf. [`../audit-reality/security-governance.md`](../audit-reality/security-governance.md)).
- RGPD : redaction PII testée + rétention/effacement effectifs (impl. `actions`).

## 5. Invariants & pièges

- **FOSS vs EE toujours distingué** — ne jamais présupposer qu'une feature « entreprise »
  est gratuite (cf. l'audit). Whitelabel admin, perm-sync, SCIM = **EE**.
- **Zéro mock présenté comme réel** : si non testé/vérifié, le dire.
- **Fail-closed par défaut** ; **zéro secret en repo** ; **télémétrie OFF**.
- On relève les **pins** dès qu'une CVE apparaît — on ne désactive **jamais** un gate.

## 6. Observabilité

Sécurité observable : journal d'audit (gateway `app/audit.py` + actions audit HMAC),
alertes sécurité (scope [`monitoring.md`](monitoring.md)). Cf. [`../../SECURITY.md`](../../SECURITY.md).

## 7. Docs de fond

[`../../SECURITY.md`](../../SECURITY.md) · [`../SECURITY.md`](../SECURITY.md) ·
[`../SECURITY_RGPD_ACTIONS.md`](../SECURITY_RGPD_ACTIONS.md) · [`../RGPD.md`](../RGPD.md) ·
[`../audit-onyx/00-VERDICT.md`](../audit-onyx/00-VERDICT.md) ·
[`../PARITE_ENTREPRISE.md`](../PARITE_ENTREPRISE.md).

## 8. Audit & journal

[`../audit-reality/security-governance.md`](../audit-reality/security-governance.md) ·
[`../audit-reality/_VERDICT.md`](../audit-reality/_VERDICT.md) ·
[`../../ralph/state/security-governance.md`](../../ralph/state/security-governance.md) ·
[`../../ralph/scopes/security-governance.md`](../../ralph/scopes/security-governance.md).

## 9. Sous-agent

| | |
|---|---|
| Discipline | Sécurité / conformité / architecture |
| Skills | `/security-review`, `deep-research`, `/code-review` |
| MCP | `Microsoft_Learn` (Entra/SSO/Key Vault) ; `github` |
| Cibles de preuve | gates CI sécurité, claims de parité ↔ code |

## 10. Maintenir cette fiche

Touche à la posture sécurité/RGPD ou à un claim de parité ⇒ mets à jour §2, vérifie
les gates, reporte dans
[`../audit-reality/security-governance.md`](../audit-reality/security-governance.md)
et le journal. Vérifie : `make docs-check`.
