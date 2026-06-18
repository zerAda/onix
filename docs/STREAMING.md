# Streaming NDJSON RBAC-safe (`access-gateway`)

Ce document décrit le **moteur de streaming token-par-token** ajouté à la
passerelle `access-gateway/` devant Onyx. Objectif : diviser la **latence
perçue** par ~10 sur un LLM local (CPU) — l'utilisateur voit la réponse se
construire dès le premier mot — **sans perdre** les garanties de sécurité
déterministes déjà prouvées sur le chemin non-streaming :

* le **post-filtre garde-fous** (`app/guardrail.py`, couche 3, hors-LLM) ;
* le **filtre ACL par-document** (`app/doc_acl.py`, RBAC fin FOSS).

> **Position dans la pile** : `Ollama` (génération token-level) ⟶ `Onyx`
> (RAG + paquets NDJSON `/chat/send-message`) ⟶ **`access-gateway` (ce moteur :
> relais + gardes + ACL au fil de l'eau)** ⟶ client. Implémentation :
> [`app/streaming.py`](../access-gateway/app/streaming.py). Code FastAPI
> minimal (l'orchestrateur câble `main.py` ; le moteur n'importe PAS FastAPI).

> **Transport = NDJSON, PAS Server-Sent Events.** Le flux est émis en **NDJSON**
> (un objet JSON par ligne) avec le media-type `application/x-ndjson` — ce n'est
> **pas** du SSE (`text/event-stream`, format `data: …\n\n`). Preuve :
> `access-gateway/app/main.py:391` (`StreamingResponse(..., media_type="application/x-ndjson")`).
> Le terme « SSE » a pu apparaître par abus de langage ; le contrat réel est NDJSON
> (cf. §4 et §6).

---

## 1. Pourquoi streamer (et pourquoi c'est délicat)

En mode NON-streaming, la passerelle attend qu'Onyx ait **tout** généré, applique
ses contrôles sur le corps JSON complet, puis répond. Sur CPU, l'utilisateur fixe
un écran vide pendant plusieurs secondes.

En **streaming**, Onyx émet la réponse par petits morceaux (`answer_piece`). Les
relayer au fil de l'eau supprime l'attente perçue. **Mais** : un relais brut
perdrait les garde-fous (on aurait déjà envoyé le texte avant de pouvoir le
filtrer). Le cache, lui, est déjà correctement neutralisé pour les flux —
`cache.should_bypass` renvoie `"streaming"` pour `stream=True` (on ne met jamais
un flux NDJSON en cache : sémantique incompatible avec un body JSON intégral).

Ce moteur réconcilie latence et sécurité en distinguant **deux familles
d'invariants** selon le moment où on peut les trancher honnêtement.

---

## 2. Le trade-off honnête : DUR incrémental vs MOU final

| Invariant | Nature | Quand on peut le trancher | Action du moteur |
|---|---|---|---|
| Fuite de prompt / bascule de persona (`leaks_prompt_or_persona`) | **DUR** | Dès qu'un marqueur apparaît dans le texte **accumulé** | **Avorte** le flux immédiatement |
| Relais d'un lien d'exfiltration (`relays_exfil_link`) | **DUR** | idem (marqueur partiel détectable tôt) | **Avorte** immédiatement |
| Écriture simulée (`claims_write_action`) | **DUR** | idem | **Avorte** immédiatement |
| Groundedness : fait chiffré **sans** citation (`asserts_a_fact` ∧ ¬`has_citation`) | **MOU** | Seulement quand la **phrase est complète** ET les **citations connues** | **Override final** d'autorité |
| Connaissances générales / lecture-seule sur la *demande* | **MOU** | En fin de réponse (contexte global requis) | **Override final** d'autorité |

### 2.1. Garde DUR incrémental

À **chaque** morceau reçu, le moteur recalcule le texte accumulé et lui applique
les **détecteurs DURS de `guardrail`** (exactement les mêmes fonctions que le
chemin non-streaming — aucune logique de sécurité dupliquée). Au **premier**
déclenchement :

1. le morceau fautif **n'est PAS** relayé ;
2. un paquet `{"override": true, "answer": "<refus>", "rule": "<règle>"}` est émis ;
3. un paquet `{"done": true}` clôt le flux ;
4. le reste de l'amont est **drainé sans rien émettre**.

> **Pourquoi tester l'ACCUMULÉ et pas le morceau seul ?** Un marqueur dangereux
> (« OWASP LLM01 », un lien `exfil.example`) peut être coupé en deux morceaux par
> la tokenisation. Tester la concaténation rattrape les coupures. Coût : O(n) par
> morceau ; négligeable devant le coût LLM, et borné par la longueur de réponse.

### 2.2. Garde MOU final (override d'autorité)

La groundedness ne peut PAS être jugée morceau par morceau : « La cotisation est
de 142 € » paraît non sourcée jusqu'à ce que la phrase suivante cite
`source.pdf`. Trancher trop tôt produirait des **faux refus** massifs.

On laisse donc ces morceaux **passer au fil de l'eau** (le gain de latence est
préservé), puis, **en fin de flux**, on exécute le **post-filtre COMPLET**
(`guardrail.post_filter`) sur le texte accumulé. S'il bloque, on émet un dernier
paquet `{"override": true, "answer": "<refus>", "rule": "<règle>"}`.

---

## 3. Pourquoi PAS « tout bufferiser » ?

L'alternative simple serait d'accumuler l'intégralité de la réponse, d'appliquer
les contrôles, **puis** d'émettre — mais cela **annule le gain de latence**
(on retombe sur le comportement non-streaming, avec un coût d'ingénierie en plus).

Le choix retenu **émet au fil de l'eau** et n'accepte qu'une seule concession :
pour les invariants **MOUS** (résiduels, ~groundedness), un morceau potentiellement
non sourcé peut transiter avant d'être **désavoué** par un override final. C'est
acceptable car :

* les invariants **DURS** (fuite, exfiltration, write) — les seuls réellement
  dangereux — sont, eux, **coupés AVANT** émission ;
* l'override final est **explicite et déterministe** : un client conforme efface
  l'affichage et montre le refus (cf. §4) ;
* en pratique l'override MOU est rare (la grande majorité des réponses RAG bien
  sourcées passent sans override) — c'est un **filet**, pas le cas nominal.

> **Limite assumée, honnête.** Comme pour le filtre ACL non-streaming
> (cf. [RBAC.md](RBAC.md) §« Per-Document Filter »), c'est un contrôle de
> **SORTIE** : pour une réponse à groundedness molle, l'utilisateur a pu
> *apercevoir* un fragment avant l'override (≤ la durée d'une réponse). Le
> contenu réellement dangereux (DUR), lui, ne sort jamais. Le mode « zéro
> fragment visible » impose de bufferiser (donc de renoncer au streaming) :
> c'est un réglage produit, pas un défaut du moteur.

---

## 4. Contrat client (paquets émis par la passerelle)

Le moteur émet du **NDJSON** (un paquet JSON par ligne, `application/x-ndjson`),
miroir du flux Onyx, augmenté de **deux paquets de contrôle** :

| Paquet | Sens |
|---|---|
| `{"answer_piece": "…"}` | Morceau de réponse, **relayé au fil de l'eau** (latence). |
| `{"top_documents": [...]}` / `{"citations": [...]}` | Documents/citations, émis **APRÈS** le filtre ACL par-document. |
| `{"error": "…"}` | Erreur amont Onyx, relayée telle quelle. |
| `{"override": true, "answer": "<refus>", "rule": "<règle>"}` | **Message FINAL d'autorité** : il **remplace** la réponse affichée. |
| `{"done": true}` | Fin de flux (terminal, toujours le dernier paquet). |

### Règle d'or

> **Le dernier message d'autorité gagne.** En recevant un paquet `override`, un
> client conforme **écarte le texte accumulé** et affiche `answer`. Tant
> qu'aucun `override` n'arrive, le texte est constitué de la concaténation des
> `answer_piece`. `done` clôt toujours le flux.

`rule` permet au client/à l'audit de distinguer la cause : `no_prompt_leak`,
`no_exfil_relay`, `read_only` (gardes DURS), `no_citation`/`out_of_context`
(override MOU final), `no_accessible_source` (toutes les citations retirées par
l'ACL), ou un `*_error` (fail-closed, cf. §5).

Pseudo-client minimal :

```javascript
let answer = "";
for await (const line of ndjsonLines(response.body)) {
  const p = JSON.parse(line);
  if (p.override)        { answer = p.answer; render(answer, {final: true}); }
  else if (p.answer_piece) { answer += p.answer_piece; render(answer); }
  else if (p.error)      { showError(p.error); }
  else if (p.done)       break;
  // top_documents / citations : afficher les sources (déjà filtrées ACL).
}
```

---

## 5. Fail-closed (sécurité avant disponibilité)

Sur le **chemin de contrôle** (garde DUR, post-filtre final, filtre ACL), toute
exception interne est traitée en **fail-closed** : on **n'émet jamais** de
contenu non vérifié. Le moteur coupe, émet
`{"override": true, "answer": REFUSAL_INTERNAL, "rule": "<*_error>"}` puis
`{"done": true}`, et logue (`onix.gateway`). Indisponibilité ponctuelle d'une
réponse > fuite d'un contenu non contrôlé.

> Nuance avec le chemin non-streaming : `doc_acl.filter_citations` est
> **fail-OPEN** sur bug interne (disponibilité) car le corps complet reste
> inspectable a posteriori. En **streaming**, une fois un morceau émis il est
> **irrévocable** ; on durcit donc en **fail-CLOSED**. Deux contextes, deux
> arbitrages explicites.

---

## 6. Schéma des paquets Onyx (amont) supposé

Format **historique** d'Onyx/Danswer `/chat/send-message` en streaming :
**NDJSON** (un objet JSON par ligne). Formes consommées :

| Paquet amont | Champ(s) | Modèle Onyx (legacy) |
|---|---|---|
| Morceau de réponse | `{"answer_piece": "<texte>"}` | `DanswerAnswerPiece` |
| Documents/contexte | `{"top_documents": [ … ]}` | `QADocsResponse` (hérite `RetrievalDocs`) |
| Citation | `{"citation_num": N, "document_id": "…"}` | `CitationInfo` |
| Erreur | `{"error": "<message>"}` | `StreamingError` |

C'est le format que `onyx_proxy.extract_answer` lit **déjà** (`answer_piece`) — le
moteur de streaming reste donc cohérent avec le reste de la passerelle.

> **Tolérance aux versions.** Onyx récent expose aussi un schéma typé
> (enum `StreamingType` : `message_delta`, `citation_info`, …). Le moteur ne
> **casse pas** dessus : un paquet de type inconnu est **relayé tel quel**, et
> il reconnaît par tolérance des champs de morceau alternatifs (`content`,
> `text`, `token`). La vérification du format déployé reste à confirmer contre
> l'instance Onyx réelle ; en cas de bascule complète vers le schéma typé, seule
> la table `_PIECE_FIELDS`/`_DOC_LIST_FIELDS` de `streaming.py` serait à étendre.
> Réf. : [docs Onyx — Send a Message](https://docs.onyx.app/developers/guides/chat_new_guide),
> [`streaming_models.py`](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/server/query_and_chat/streaming_models.py).

---

## 7. Configuration

| Variable d'env | Défaut | Sens |
|---|---|---|
| `GATEWAY_STREAM_ENABLED` | `true` | Active le moteur de streaming. À `false`, l'orchestrateur retombe sur le chemin non-streaming. |
| `GATEWAY_STREAM_IDLE_TIMEOUT` | `60` | Délai d'inactivité (s) toléré entre deux paquets amont (à câbler côté read-timeout `httpx`). LLM CPU lent ⇒ valeur généreuse. |

---

## 8. Observabilité

Trois compteurs Prometheus (exception-safe, cf. [OBSERVABILITY.md](OBSERVABILITY.md)) :

| Métrique | Labels | Sens |
|---|---|---|
| `onix_gateway_stream_requests_total` | — | Requêtes traitées en streaming. |
| `onix_gateway_stream_aborted_total` | `reason` | Flux **avortés** par un garde DUR ou une erreur fail-closed. |
| `onix_gateway_stream_overridden_total` | — | Flux dont la réponse finale a été **remplacée** par un override d'autorité. |

`reason` ∈ `no_prompt_leak` | `no_exfil_relay` | `read_only` | `guard_error` |
`doc_acl_error` | `postfilter_error` | `internal_error`.

---

## 9. Intégration `main.py` (câblée par l'orchestrateur)

Le moteur n'importe **pas** FastAPI ; `main.py` le branche avec `httpx.stream` +
`StreamingResponse` (extrait — version complète en docstring de
[`app/streaming.py`](../access-gateway/app/streaming.py)) :

```python
from fastapi.responses import StreamingResponse
from .streaming import proxy_stream

if settings.stream_enabled and payload.get("stream") is True:
    req = request.app.state.http.build_request(
        "POST", f"{settings.onyx_base_url}/chat/send-message",
        json=safe_payload, headers=upstream_headers(settings.onyx_api_key),
    )
    resp = await request.app.state.http.send(req, stream=True)

    async def _gen():
        try:
            async for chunk in proxy_stream(
                resp.aiter_lines(),
                question=question_text, principal=principal, acl=acl,
                settings=settings, post_filter=post_filter,
                doc_acl_filter=filter_citations, extract_answer=extract_answer,
                apply_filtered_answer=apply_filtered_answer, audit=_audit,
            ):
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(_gen(), media_type="application/x-ndjson")
```

---

## 10. Tests

[`access-gateway/tests/test_streaming.py`](../access-gateway/tests/test_streaming.py)
— **offline**, async via le helper `run` (`asyncio.run`), faux itérateur de
paquets NDJSON. Couvre : relais bénin + citations finales ; fuite de prompt
mid-stream → avortée avant émission ; write simulé / lien d'exfiltration mid-stream
avortés ; fait non sourcé → override final ; fait sourcé → pas d'override ; ACL
qui retire un document non autorisé du paquet final ; toutes citations retirées →
override `no_accessible_source` ; erreurs internes (garde, ACL) → fail-closed ;
erreur amont relayée ; ligne non-JSON tolérée ; audit des décisions. Les vrais
`guardrail`/`doc_acl`/`onyx_proxy` sont **injectés** (intégration réelle).
