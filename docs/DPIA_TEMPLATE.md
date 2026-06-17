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
| Base légale | _(intérêt légitime / contrat / …)_ |
| Minimisation | UPN/clients **hashés** ; requêtes RAG non stockées en clair ; champs libres **redactés** |
| Qualité des données | Audit typé (MATCH/MISMATCH/…), verdict explicite |
| Information des personnes | _(mention d'information / politique de confidentialité)_ |
| Exercice des droits | Accès/rectification : admin Onyx ; **effacement ciblé** : `POST /admin/retention/erase` (art. 17) |
| Sous-traitance | _(le cas échéant ; contrats art. 28)_ |

---

## 3. Mesures de sécurité (art. 32) — déjà en place (WS2)

| Mesure | Mise en œuvre |
|---|---|
| Redaction PII (logs + champs libres) | `safe_logger.redact` (JWT/IBAN/NIR/email) + anti-CRLF |
| Authentification | Clé de service + **identité vérifiée** (HMAC/JWT) ; **clé admin séparée** obligatoire |
| Contrôle d'accès / quota | Gating admin (kill-switch/flags/blocage) **fail-closed** ; rate-limit par appelant |
| Traçabilité inviolable | Journal d'audit **chaîné HMAC** + vérification |
| Chiffrement en transit | **STARTTLS** SMTP exigé ; egress **https-only** + anti-SSRF |
| Contrôle des flux sortants | **DLP allowlist** (webhook/tasks) |
| Limitation de conservation | **Purge TTL** configurable |
| Effacement | **Effacement ciblé par sujet** (hash) |
| Souveraineté | 100 % local (Ollama/OpenSearch/MinIO), aucune télémétrie |
| Chiffrement au repos | _(recommandé : LUKS/BitLocker/FileVault sur l'hôte — à confirmer)_ |

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
