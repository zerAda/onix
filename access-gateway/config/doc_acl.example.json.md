# `doc_acl.example.json` — ACL par document (FOSS)

Ce fichier illustre la **forme attendue** du fichier `config/doc_acl.json` lu par
`access-gateway/app/doc_acl.py` (via `GATEWAY_DOC_ACL_PATH`).

> Pourquoi un `.md` à côté ? Parce que **JSON ne supporte pas les commentaires
> natifs** : on garde le fichier de prod 100 % JSON valide et on documente le
> sens des entrées ici.

## Forme

Chaque clé est un **identifiant de document** (`document_id`, `id` ou
`source_id` tel qu'Onyx l'expose dans ses réponses `top_documents`/`citations`).
La valeur est un objet `{ "groups": [...], "users": [...] }` :

- **`groups`** : objectIds Entra (GUID) ou displayName de groupes Entra qui
  sont autorisés à VOIR ce document (rendu par citation). Casse insensible.
- **`users`** : UPN ou `oid` de l'utilisateur — **override individuel** qui
  gagne sur les groupes (utile pour des accès nominatifs hors-périmètre).

Un document **non listé** suit la politique `default_policy` (env
`GATEWAY_DOC_ACL_DEFAULT_POLICY`, **deny** par défaut — cohérent avec la
posture deny-by-default de la passerelle).

## Exemple commenté

```jsonc
{
  // Clés '_*' sont ignorées (espace de méta : version, commentaires).
  "_version": 1,

  // Réservé au groupe Nord.
  "doc-nord-001": { "groups": ["11111111-1111-1111-1111-111111111111"], "users": [] },

  // Groupe Nord + override nominatif (la directrice 'dir@contoso.fr' y a
  // accès même si elle n'est pas dans le groupe Nord).
  "doc-nord-002": {
    "groups": ["11111111-1111-1111-1111-111111111111"],
    "users": ["dir@contoso.fr"]
  },

  // Réservé au groupe Sud.
  "doc-sud-001": { "groups": ["22222222-2222-2222-2222-222222222222"], "users": [] },

  // Partagé Nord ∪ Sud (toute la division commerciale).
  "doc-shared-001": {
    "groups": [
      "11111111-1111-1111-1111-111111111111",
      "22222222-2222-2222-2222-222222222222"
    ],
    "users": []
  }
}
```

## Limites honnêtes

- **Filtre de SORTIE.** Onyx FOSS récupère et fait raisonner le LLM sur les
  documents indexés du périmètre Document Set autorisé (cf.
  `onyx_proxy.enforce_document_sets`). Ce filtre retire ensuite les CITATIONS
  vers les documents auxquels l'appelant n'a pas accès individuellement, mais
  le LLM a pu inclure des fragments dans sa réponse en amont. Pour un
  cloisonnement zéro-fuite à la recherche, voir **Onyx EE / Cloud (permission
  sync)** — cf. `docs/DECISION_RBAC.md` §5.
- **Statique, pas synchronisé.** Le fichier doit être maintenu (édition
  manuelle ou pipeline d'export depuis SharePoint). L'extension
  `CompositeDocACL` permet de combiner cette source avec un cache fetché
  depuis Graph (TODO documenté dans `docs/RBAC.md` § « Per-Document Filter »).
