# Démo AC360 + POC réconciliation contrat↔SI Fabric — Design

**Date :** 2026-06-22 · **Objectif :** démo end-to-end onix « Assistant Client 360 » prête pour demain, incluant un **POC de réconciliation contrat** (document SharePoint ↔ SI Fabric), sur la VM Azure recréée.

## Constat de vérification (2 repos GitHub)
- 2e repo = **`zerAda/AC360`** (assistant Copilot Studio + **Azure Functions** Python/Bicep) — la **référence**.
- Flux de réconciliation AC360 (`azure_functions/function_app.py`) : `document SharePoint → OCR → fetch référence Fabric (OneLake Delta) → comparaison (fabric_audit_engine.audit) → verdict → FIC`.
- **onix a déjà ~80 %** : `actions/app/audit_engine.py` EST l'équivalent de `fabric_audit_engine` —
  `extract_canonical_fields`, `compare_name/amount/date/contract`, `audit({document, reference}) → verdict` (`CONFORME`/`ECART`/`INCERTAIN`/`CLIENT_NON_TROUVE`). OCR local (tesseract/poppler) présent. `fabric_client` lit OneLake.
- **Seul manque** : `fetch_client_reference(identity)` qui lit la **référence client dans le SI Fabric (OneLake Delta)** et la mappe au schéma `reference` attendu par `audit_engine.audit`.

## POC réconciliation (la glue à coder)
1. **`fetch_client_reference(identity)`** (nouveau) — via le `fabric_client` (OneLake read, GOLD, read-only) : lit la ligne de référence du client (nom, SIRET, montant cotisation, garanties, date d'effet) depuis la table Delta gold du SI Fabric. Fail-closed : client absent / lecture impossible ⇒ `reference=None` (→ verdict `CLIENT_NON_TROUVE`).
2. **Orchestration** (endpoint onix-actions, ex. `POST /audit/reconcile`) : `doc SharePoint (RBAC user) → OCR → extract_canonical_fields → fetch_client_reference (Fabric) → audit_engine.audit({document, reference}) → verdict d'écarts`.
3. **Réutilise l'existant** : OCR + `audit_engine` + (option) génération de FIC sur verdict `ECART`/`INCERTAIN`. Pas de réécriture du moteur de compa.

### Invariants
- **Read-only** Fabric/SharePoint (aucune écriture sur le SI/tenant réel). **fail-closed**. RBAC user appliquée (le doc n'est lu que si l'utilisateur y a droit). Zéro secret en repo. Commentaires FR.
- **Zéro mock présenté comme réel** : si le SI Fabric n'a pas de table de référence exploitable pour la démo, on le dit ; on peut alimenter une **table de référence de démo** (gold) côté Fabric (workspace de test) plutôt que de simuler.

## Démo end-to-end (6 fonctionnalités, LIVE)
1. **RAG sur vrais docs SharePoint** `Dossiers_Clients_POC` (connecteur Onyx) → réponse sourcée+citée, cloisonnée RBAC.
2. **RBAC par-client** (gateway, SharePoint+Fabric) → grant/deny.
3. **Réconciliation contrat↔SI Fabric** (le POC) → verdict d'écarts réel.
4. **Branding GEREP** visible (couleurs/favicon/titre, vérifié sur le rendu).
5. **Sécurité** : M1 (downgrade détecté), HARD-03 (refus sans clé), M7 (401 anti-spoof), M4 (alertmanager fail-closed).
6. **Souveraineté** : 100 % local (gemma3, index, fichiers sur la VM).

## Environnement
- Recréer `onix-test-rg` + VM **Standard_D16as_v5** (France Central), déployer `prod/cycle1-securite` + gemma3:12b + provider seedé + branding monté + gateway (creds Fabric az + app SharePoint `onix-sec01-sp-test`). Accès UI restreint à l'IP. **Loop** jusqu'à 6/6 ✅.

## Livrable
URL de démo + **runbook** (quoi cliquer/montrer/dire, par fonctionnalité, valeurs attendues).
