# Registre des traitements — `onix-actions` (RGPD art. 30)

> Registre des activités de traitement au sens de l'**article 30 du RGPD**, pour
> la couche applicative `onix-actions`. À **compléter et tenir à jour** par le
> responsable de traitement / DPO ; doit pouvoir être **présenté à l'autorité de
> contrôle** sur demande. Modèle de fiche — les valeurs entre _(…)_ sont à
> renseigner selon votre déploiement.
>
> Appui à la conformité, **pas un avis juridique**.

---

## Fiche 1 — Audit documentaire & assistance commerciale

| Rubrique (art. 30-1) | Contenu |
|---|---|
| **Responsable de traitement** | _(organisation, coordonnées)_ |
| **DPO** | _(nom, e-mail)_ |
| **Finalité(s)** | Assistance à la préparation commerciale : contrôle de cohérence documentaire (audit OCR), génération de fiches de RDV `.docx`, relances/tâches, notifications. |
| **Base légale** | _(à qualifier : exécution d'un contrat / intérêt légitime)_ |
| **Catégories de personnes** | Collaborateurs (commerciaux, administrateurs) ; personnes concernées par les documents traités (clients / prospects). |
| **Catégories de données** | Identité et données contractuelles présentes dans les documents fournis ; **éventuelles données de santé** (garanties/plafonds — à confirmer) ; identifiants techniques (UPN, **hashés** dans les traces). |
| **Destinataires** | Interne. Systèmes externes UNIQUEMENT via egress **allowlisté** (webhook / SMTP du client). **Aucun fournisseur d'IA externe** (Ollama local). |
| **Transferts hors UE** | **Aucun** (traitement 100 % local/souverain). |
| **Durées de conservation** | Données d'usage / tâches terminées / fichiers `.docx` : **`ONIX_RETENTION_DAYS`** (défaut **365 jours**), purge par âge. Journal d'audit administratif : conservé pour traçabilité (ne contient **que des hash**). Documents sources : gérés côté Onyx (cf. `RGPD.md`). |
| **Mesures de sécurité (art. 32) — renvoi 30-1-g** | Voir tableau ci-dessous. |

### Description générale des mesures techniques & organisationnelles (art. 32-1)

| Domaine | Mesure |
|---|---|
| Confidentialité des traces | **Redaction PII** (JWT/IBAN/NIR/e-mail) sur logs + champs libres ; **identifiants hashés SHA-256** ; requêtes RAG non stockées en clair. |
| Contrôle d'accès | Clé de **service** + **identité d'appelant vérifiée** (HMAC par appel / JWT OIDC) ; **clé admin distincte obligatoire** (fail-closed) ; **gating** kill-switch/flags/blocage ; **rate-limiting** par appelant. |
| Intégrité | **Journal d'audit chaîné HMAC** (tamper-evident) + endpoint de vérification ; **fail-closed** sur configuration inconnue. |
| Chiffrement en transit | **STARTTLS** exigé (SMTP) ; egress **https-only** + **anti-SSRF**. |
| Maîtrise des flux sortants | **DLP egress allowlist** (webhook / `tasks.webhook_url`). |
| Limitation de conservation | **Purge par âge** (TTL configurable). |
| Droit à l'effacement | **Effacement ciblé par sujet** (`POST /admin/retention/erase`, art. 17). |
| Disponibilité / résilience | Stockage local (SQLite/volumes) ; sauvegarde/restauration (`make backup`/`restore`). |
| Souveraineté | Inférence + index + fichiers sur site ; **aucune télémétrie** sortante. |

---

## Fiche 2 — Administration & journalisation de sécurité

| Rubrique | Contenu |
|---|---|
| **Finalité** | Administration du service (kill-switch, activation/désactivation de fonctions, blocage d'utilisateurs) et **journalisation de sécurité** (audit des actions admin, journal d'accès `document_accessed` / `rag_search_executed`). |
| **Base légale** | Intérêt légitime (sécurité du SI, traçabilité) / obligation de sécurité (art. 32). |
| **Catégories de personnes** | Administrateurs ; utilisateurs (via traces d'accès **hashées**). |
| **Catégories de données** | Identifiants **hashés** (admin, cible, utilisateur) ; horodatages ; action/portée/résultat ; motif **redacté**. **Aucune donnée en clair.** |
| **Destinataires** | Administrateurs habilités uniquement. |
| **Transferts hors UE** | Aucun. |
| **Durée de conservation** | Journal d'audit : _(à définir — typiquement 6 à 12 mois ; conservé pour intégrité de la chaîne)_. Journal d'accès (usage) : purgé selon `ONIX_RETENTION_DAYS`. |
| **Mesures de sécurité** | Clé admin distincte obligatoire ; **chaînage HMAC** inviolable ; identifiants hashés ; redaction des champs libres. |

---

## Tenue du registre

- [ ] Revue **au moins annuelle** et à chaque évolution du traitement.
- [ ] Cohérence avec l'[AIPD/DPIA](DPIA_TEMPLATE.md) et la
      [documentation sécurité](SECURITY_RGPD_ACTIONS.md).
- [ ] Validation **DPO** : _(date, version)_.
