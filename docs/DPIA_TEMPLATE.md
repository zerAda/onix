# Modèle d'AIPD / DPIA — `onix-actions` (RGPD art. 35)

> **AIPD** (Analyse d'Impact relative à la Protection des Données) / **DPIA**
> (Data Protection Impact Assessment). Modèle à compléter par le responsable de
> traitement / DPO **avant** mise en production, en particulier si des **données
> sensibles** (santé — courant en assurance/courtage) sont traitées.
>
> Ce modèle est un **appui méthodologique, pas un avis juridique**. Référez-vous
> à la méthode CNIL (PIA) et faites valider par votre DPO.

---

## 0. Identification

| Champ | Valeur (à compléter) |
|---|---|
| Nom du traitement | Assistant documentaire commercial — couche applicative `onix-actions` |
| Responsable de traitement | _(organisation)_ |
| DPO / contact | _(nom, e-mail)_ |
| Date de l'analyse / version | _(AAAA-MM-JJ / vX)_ |
| Périmètre | Microservice local `onix-actions` (audit OCR, génération `.docx`, tâches, notification, usage/FinOps, administration) |
| Hors périmètre | RAG/indexation Onyx (cf. `RGPD.md`) ; **RBAC par document → WS5** |

---

## 1. Description du traitement

- **Finalités** : assister la préparation commerciale (audit de cohérence
  documentaire, fiches de RDV, relances) sur des documents **fournis par
  l'utilisateur**, en local.
- **Nature des opérations** : extraction OCR locale, comparaison à une
  référence, génération de documents, journalisation d'usage, administration.
- **Catégories de personnes** : collaborateurs (commerciaux/admins), et
  **personnes concernées par les documents traités** (clients/prospects).
- **Catégories de données** : _(à qualifier)_ — identité, données contractuelles
  ; **données potentiellement sensibles** (santé via plafonds/garanties) → à
  confirmer ; identifiants techniques (UPN, hashés côté traces).
- **Destinataires** : interne uniquement ; egress **allowlisté** (webhook/SMTP).
  Aucun fournisseur d'IA externe (Ollama local).
- **Transferts hors UE** : **aucun** par conception (tout local).
- **Durées de conservation** : usage/tâches/`.docx` purgés à `ONIX_RETENTION_DAYS`
  (défaut 365 j) ; journal d'audit conservé (traçabilité), sans donnée en clair.

---

## 2. Nécessité & proportionnalité

| Question | Réponse (à compléter) |
|---|---|
| Base légale | **TODO (décision client)** — intérêt légitime (art. 6-1-f) / contrat (art. 6-1-b) à qualifier par le RT/DPO |
| Minimisation | UPN/clients **hashés SHA-256** ; requêtes RAG non stockées en clair (seule la longueur l'est) ; champs libres **redactés** (`actions/app/safe_logger.py:44-157`) |
| Qualité des données | Audit typé (MATCH/MISMATCH/…), verdict explicite |
| Information des personnes | **TODO (décision client)** — mention d'information / politique de confidentialité à fournir par le RT |
| Exercice des droits | Accès/rectification : admin Onyx ; **effacement ciblé** : `POST /admin/retention/erase` (art. 17 — `actions/app/retention.py:153-209`) |
| Sous-traitance | **Aucun sous-traitant IA/cloud externe** (tout local). Seuls tiers possibles : SMTP/webhook **désignés par le client** via l'allowlist egress (défaut **deny-all**) → contrats art. 28 **TODO (décision client)** s'ils sont activés |

---

## 3. Mesures de sécurité (art. 32) — déjà en place (WS2)

| Mesure | Mise en œuvre (preuve `fichier:ligne`) |
|---|---|
| Redaction PII (logs + champs libres) | `actions/app/safe_logger.py:44-157` (JWT/IBAN/NIR/email/CB/tél) + anti-CRLF |
| Authentification | Clé de service + **clé admin séparée obligatoire** (`actions/app/security.py:177-209`). ⚠️ **Identité vérifiée appelant (HMAC/JWT)** (`actions/app/caller_identity.py:94-120`) : **mécanisme présent mais OFF par défaut** (`ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=false`, fail-open via clé de service) → **à activer en prod** (cf. §5). |
| Contrôle d'accès / quota | Gating admin (kill-switch/flags/blocage) **fail-closed** ; rate-limit par appelant (`actions/app/security.py`) |
| Traçabilité inviolable | Journal d'audit **chaîné HMAC** + vérification `actions/app/audit_log.py:88-195` ; endpoint `actions/app/main.py:872` |
| Chiffrement en transit | **STARTTLS** SMTP exigé `actions/app/notify.py:114-135` ; egress **https-only** + anti-SSRF `actions/app/dlp.py:88-150` |
| Contrôle des flux sortants | **DLP allowlist** (webhook/tasks) `actions/app/dlp.py:88-150`, deny-all par défaut |
| Limitation de conservation | **Purge TTL** configurable (`ONIX_RETENTION_DAYS`, défaut 365 j) `actions/app/retention.py:57-119` |
| Effacement | **Effacement ciblé par sujet** (hash) `actions/app/retention.py:153-209` ; ⚠️ `.docx`/S3 = **best-effort par nom de fichier** (un doc non nommé d'après le sujet peut échapper à l'effacement) |
| Présence clé secrets connecteurs (base Onyx) | `ENCRYPTION_KEY_SECRET` **généré + imposé au boot** : `scripts/gen-secrets.sh:80`, `docker-compose.yml:59,118` (`:?`), `env.template:50`. ⚠️ **FOSS** : `_encrypt_string` = **no-op (identité)** → garantit la *présence* du dispositif, **PAS** un chiffrement AES réel au repos (= Onyx EE). Art. 32 effectif = chiffrement disque (ligne ci-dessous). |
| Souveraineté | 100 % local (Ollama/OpenSearch/MinIO), aucune télémétrie (`DISABLE_TELEMETRY=true`) |
| Chiffrement au repos (disque hôte) | **TODO (décision client)** — recommandé : LUKS/BitLocker/FileVault sur l'hôte (non géré par l'outil) |

---

## 4. Appréciation des risques (à compléter)

Pour chaque risque : **accès illégitime**, **modification non désirée**,
**disparition** de données — évaluer **gravité** et **vraisemblance**
(négligeable / limitée / importante / maximale), puis le **risque résiduel**
après mesures.

| Risque | Sources | Gravité | Vraisemblance | Mesures | Risque résiduel |
|---|---|---|---|---|---|
| Accès illégitime aux documents/traces | _(interne/externe)_ | | | Auth+quota, redaction, hash, DLP | |
| Modification non désirée (audit/état) | | | | Audit chaîné, gating fail-closed | |
| Disparition de données | | | | Sauvegardes (`make backup`), TTL maîtrisé | |
| Exfiltration via egress | | | | Allowlist + anti-SSRF | |

---

## 5. Conclusion / plan d'action

- [ ] Qualifier les **catégories de données** réellement traitées (sensibles ?).
- [ ] Valider la **base légale** et l'**information** des personnes.
- [ ] Définir/valider les **durées** (`ONIX_RETENTION_DAYS`) avec le métier.
- [ ] Activer **identité vérifiée** (`ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=true`)
      et renseigner l'**allowlist egress** en production.
- [ ] Confirmer le **chiffrement au repos** de l'hôte.
- [ ] Mettre à jour le [registre des traitements](REGISTRE_TRAITEMENTS.md).
- [ ] **Avis du DPO** : _(favorable / réservé / défavorable — date)_.
