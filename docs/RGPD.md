# Note RGPD / Souveraineté des données

Cette stack est conçue pour le **traitement local et souverain** des données :
elle constitue une réponse forte aux exigences de confidentialité et de
non-transfert (utile notamment en contexte assurance / courtage).

> Cette note est un appui à la conformité, **pas un avis juridique**. Faites
> valider par votre DPO les usages réels (catégories de données, finalités).

## 1. Localisation des traitements

- **Inférence LLM** : exécutée **localement** par Ollama. Aucun prompt ni
  document n'est envoyé à un fournisseur d'IA externe (OpenAI, Anthropic, etc.).
- **Indexation & recherche** : OpenSearch + MinIO, **sur la machine**.
- **Aucune télémétrie** : `DISABLE_TELEMETRY=true` ; aucune analytics tierce
  activée. Pas de transfert hors UE/hors site par conception.

## 2. Où sont les données (cartographie)

| Donnée | Emplacement | Volume |
|---|---|---|
| Documents indexés (texte) | OpenSearch | `opensearch-data` |
| Fichiers d'origine | MinIO (S3 local) | `minio_data` |
| Comptes, config, historiques de chat | Postgres | `db_volume` |
| Fichiers de travail Onyx | volume partagé | `file-system` |
| Modèles LLM | Ollama | `ollama_data` |

Tout est sur des **volumes Docker locaux** — aucune externalisation.

## 3. Mesures de sécurité (art. 32 RGPD)

- Accès restreint à `127.0.0.1` (poste local) ; authentification requise.
- Secrets forts générés, `.env` en `chmod 600`, jamais versionnés.
- Surface réduite (services à risque retirés) ; images épinglées.
- Sauvegardes chiffrables (stocker `backups/` sur support chiffré).
- Journalisation locale avec rotation. Côté `onix-actions`, la **redaction PII**
  (JWT/IBAN/NIR/e-mail) est systématique sur les logs et champs libres
  (`actions/app/safe_logger.py`), avec **anti-CRLF** (log forging) ; les
  identifiants sont **hashés**. Les logs des connecteurs Onyx restent à vérifier
  selon vos sources.
- **Traçabilité inviolable** (art. 5-2 accountability) : journal d'audit admin
  **chaîné HMAC** (tamper-evident) + endpoint de vérification
  (`actions/app/audit_log.py:88-195` ; `actions/app/main.py:872`).
- **Présence d'une clé de chiffrement des secrets** (`ENCRYPTION_KEY_SECRET`) :
  **générée et imposée au boot** (fail-loud si vide). ⚠️ **FOSS vs EE (honnêteté
  réglementaire)** : sur le déploiement **Onyx FOSS**, la fonction `_encrypt_string`
  est l'**identité (no-op)** — la clé garantit la *présence* du dispositif et la
  cohérence de rotation, **PAS** un chiffrement AES réel au repos (art. 32), qui
  relève d'Onyx **EE**. Cf. [`SECURITY.md`](../SECURITY.md) §5 et
  `docs/audit-onyx/50-rgpd-governance.md`. → Pour un chiffrement art. 32 **effectif**
  des secrets en FOSS : chiffrement disque/volume (LUKS / Azure Disk Encryption).

## 4. Droits des personnes & rétention

On distingue **deux couches** (cf. [`SECURITY_RGPD_ACTIONS.md`](SECURITY_RGPD_ACTIONS.md)) :

- **Couche Onyx** (documents indexés, historiques de chat) :
  - **Effacement** : suppression des documents/chat via l'admin Onyx ;
    purge **complète** de la stack via `make destroy` (supprime les volumes —
    cf. `Makefile`, cible `destroy` = `down -v`).
  - **Rétention** : pas de TTL natif côté Onyx FOSS → à piloter via une
    politique d'exploitation (durée de conservation des chats/index).
- **Couche applicative `onix-actions`** (audit OCR, docgen, tâches, usage) — la
  rétention et l'effacement art. 17 y sont **réellement implémentés** :
  - **Effacement ciblé (art. 17)** : `POST /admin/retention/erase` efface les
    traces d'un sujet (par identifiant en clair → hashé, ou par hash). ⚠️ Pour les
    `.docx` du sujet en mode S3, le rapprochement est **best-effort par nom de
    fichier** — le code lui-même le disclaim (cf. `docs/audit-reality/actions.md`) :
    un document dont le nom **ne porte pas** l'identifiant peut **échapper** à
    l'effacement automatique (à compléter par une purge manuelle si besoin).
    (`actions/app/retention.py:153-209` `erase_subject` ; endpoint
    `actions/app/main.py:924-931`). Le journal d'audit chaîné est **préservé**
    (il ne contient que des hash).
  - **Purge par âge (TTL)** : `POST /admin/retention/purge` supprime les
    `usage_events`, tâches terminées et `.docx` au-delà de **`ONIX_RETENTION_DAYS`**
    (défaut **365 jours**), objets S3 `jobs/…` périmés compris
    (`actions/app/retention.py:57-119` `purge_by_age`). Le journal d'audit
    n'est **pas** purgé (obligation de traçabilité + intégrité de la chaîne).
- **Minimisation** : n'indexez que les sources nécessaires (connecteurs
  maîtrisés) ; côté `onix-actions`, les identifiants (UPN, clients) sont
  **hashés SHA-256** et les requêtes RAG ne sont jamais stockées en clair.

> Le détail de ces traitements (finalités, durées, base légale) est tenu dans le
> [registre des traitements](REGISTRE_TRAITEMENTS.md) (art. 30).

## 5. Points d'attention avant production

- [ ] DPIA/AIPD si données sensibles (santé, etc.) — courant en assurance.
- [ ] Registre des traitements mis à jour (finalité « assistant documentaire »).
- [ ] Si exposition réseau : TLS + SSO Entra ID + journalisation des accès (SECURITY.md §6).
- [ ] Chiffrement au repos du disque hôte (BitLocker/LUKS/FileVault) recommandé.
- [ ] Procédure de sauvegarde/restauration testée et tracée.
