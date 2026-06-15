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
- Journalisation locale avec rotation (pas de données client en clair attendues
  dans les logs applicatifs — à vérifier selon vos connecteurs).

## 4. Droits des personnes & rétention

- **Effacement** : suppression des documents/chat via l'admin Onyx ; purge
  complète via `make destroy` (supprime les volumes).
- **Rétention** : définissez une politique (durée de conservation des chats,
  des index) ; pas de rétention imposée par l'outil.
- **Minimisation** : n'indexez que les sources nécessaires (connecteurs maîtrisés).

## 5. Points d'attention avant production

- [ ] DPIA/AIPD si données sensibles (santé, etc.) — courant en assurance.
- [ ] Registre des traitements mis à jour (finalité « assistant documentaire »).
- [ ] Si exposition réseau : TLS + SSO Entra ID + journalisation des accès (SECURITY.md §6).
- [ ] Chiffrement au repos du disque hôte (BitLocker/LUKS/FileVault) recommandé.
- [ ] Procédure de sauvegarde/restauration testée et tracée.
