# SECURITY — modèle de sécurité onix

> Modèle consolidé (tous scopes). Détails : [`docs/SECURITY.md`](docs/SECURITY.md)
> (baseline), [`docs/SECURITY_RGPD_ACTIONS.md`](docs/SECURITY_RGPD_ACTIONS.md),
> [`docs/RBAC.md`](docs/RBAC.md), [`docs/RGPD.md`](docs/RGPD.md), et l'audit
> [`docs/audit-onyx/30-security.md`](docs/audit-onyx/30-security.md) /
> [`50-rgpd-governance.md`](docs/audit-onyx/50-rgpd-governance.md).

## 1. Principes
**Souverain** (rien ne sort de l'infra : LLM + index + fichiers locaux, télémétrie OFF) ·
**fail-closed** (au doute → refus) · **défense en profondeur** (plusieurs couches
indépendantes) · **moindre privilège** · **zéro secret en repo** · **déterminisme des
garde-fous** (contrôles hors-LLM, non manipulables par injection).

## 2. Frontières de confiance & modèle de menace
| Frontière | Menace | Contrôle onix |
|---|---|---|
| Internet → ingress | accès non authentifié | OIDC Entra (oauth2-proxy) ; `X-OIDC-Claims` posé **uniquement** par l'edge vérifié, **strippé** s'il vient du client (anti-spoofing) |
| Utilisateur A → données utilisateur B | exfiltration multi-client | cloisonnement Document Set par groupe + **ACL par-document** (filtre de sortie) + clé de cache HMAC **par périmètre** (un autre périmètre ⇒ clé différente ⇒ pas de fuite) |
| Document indexé → assistant | **injection documentaire** (LLM01) | prompt système durci + **post-filtre déterministe** (hors-LLM) : anti-fuite prompt, non-exécution d'injection, lecture-seule, citation obligatoire — **red-team 21/21** |
| Assistant → systèmes externes | SSRF / exfiltration via outils | **DLP egress allowlist** + anti-SSRF (IP privées/metadata bloquées), `onix-actions` fail-closed |
| Réponse streamée | fuite avant verdict | **garde DUR incrémental** (abort avant le chunk fautif) + override final autoritatif (cf. [`docs/STREAMING.md`](docs/STREAMING.md)) |
| Secrets au repos | creds en clair | secrets générés hors-repo + Key Vault/CMK + `ENCRYPTION_KEY_SECRET` posé (cf. §4) |

## 3. Authentification & autorisation
- **AuthN** : OIDC **Entra ID** (SSO) ; comptes locaux possibles en mono-poste. 1er compte = admin (le créer **immédiatement**).
- **AuthZ** : la passerelle résout les **groupes Entra** (Graph app-only) → **Document Sets** autorisés (deny-by-default) ; puis **ACL par-document** par utilisateur (groups/UPN). `onix-actions` : clé API + identité d'appelant **HMAC** (anti-rejeu) + rate-limit par appelant.
- **RBAC par-doc — réalité FOSS** : c'est un **filtre de SORTIE** (le LLM a vu le périmètre indexé). Le trimming **à la récupération** (zéro-fuite strict) = Onyx **EE** (perm-sync) ou instances par tier. Décision tracée : [`docs/DECISION_RBAC.md`](docs/DECISION_RBAC.md).

## 4. Secrets & chiffrement
- **Zéro secret en repo** : `.env` gitignoré, généré par `scripts/gen-secrets.sh` (chmod 600) ; CI **gitleaks** bloquant.
- **Azure** : secrets dans **Key Vault** (CSI + Workload Identity, zéro creds statiques) ; disques/PG/Blob en **CMK**.
- **⚠️ Critique (audit Onyx)** : Onyx ne chiffre PAS les secrets par défaut (FOSS no-op ; EE silencieux si clé vide) → **toujours poser `ENCRYPTION_KEY_SECRET`** (sinon creds connecteurs/LLM **en clair** en base). Asymétrie Onyx : il échoue sur `USER_AUTH_SECRET` vide mais pas sur `ENCRYPTION_KEY_SECRET`.

## 5. Garde-fous LLM (OWASP LLM Top 10)
Post-filtre **déterministe** déployé dans la passerelle (chemin réponse, après le LLM) :
fuite de prompt, exécution d'injection, écriture simulée (lecture-seule), fait non
sourcé → **substitution d'un refus**. Prouvé **21/21** red-team sur `qwen2.5:7b`
([`docs/QA_GUARDRAILS.md`](docs/QA_GUARDRAILS.md), [`docs/E2E_GUARDRAILS.md`](docs/E2E_GUARDRAILS.md)).
Cache sémantique : **garde anti-divergence** (nombres/dates/entités) pour ne jamais
servir la réponse d'une question factuellement différente.

## 6. RGPD / gouvernance des données
- **Résidence** : tout sur site / tenant UE (France Central) ; **télémétrie OFF** (Onyx l'a ON par défaut).
- **Audit-trail** : journal d'accès **HMAC chaîné** (tamper-evident) — **Onyx n'en a aucun, même en EE** ; onix le fournit.
- **PII** : redaction dans les logs/sorties ; **effacement art.17** + **rétention art.5** via `onix-actions` (Onyx FOSS a un effacement utilisateur cassé — corrigé côté onix).
- Registre de traitements + DPIA : [`docs/REGISTRE_TRAITEMENTS.md`](docs/REGISTRE_TRAITEMENTS.md), [`docs/DPIA_TEMPLATE.md`](docs/DPIA_TEMPLATE.md).

## 7. Audit Onyx → mitigations onix (synthèse)
| Constat audit (FOSS) | Sévérité | Mitigation onix |
|---|---|---|
| Chiffrement secrets off par défaut | 🔴 | `ENCRYPTION_KEY_SECRET` imposé + Key Vault/CMK |
| Pas de RBAC par-document | 🔴 | gateway doc-ACL (sortie) ; EE optionnel |
| Pas d'audit-trail | 🔴 | journal HMAC chaîné (actions) |
| Effacement art.17 cassé | 🟠 | endpoints rétention/erasure |
| Conteneurs root | 🟠 | `runAsNonRoot` (gateway/actions) |
| Custom Tools contournent l'anti-SSRF | 🟠 | DLP egress allowlist + anti-SSRF |
| Télémétrie ON | 🟠 | `DISABLE_TELEMETRY=true` |
| `/health` ment sur la readiness | 🟡 | vraies probes (chart) |

## 8. Chaîne d'approvisionnement & durcissement
Dépendances **épinglées** + relevées dès qu'une CVE paraît ; CI bloquante :
`pip-audit --strict` (0 CVE), `gitleaks` (0 secret), `bandit` (0 medium+), `trivy`
(images), `helm lint`. Conteneurs non-root, images figées (tags épinglés).

## 9. Divulgation responsable
Vulnérabilité ? Ne pas ouvrir d'issue publique. Contact privé du mainteneur du dépôt
(remplacer par l'adresse de l'équipe sécurité). Onyx amont : `SECURITY.md` d'Onyx +
advisories GitHub `onyx-dot-app/onyx`.
