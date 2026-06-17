"""Tests du moteur de **streaming SSE** (`app.streaming.proxy_stream`).

Tout est OFFLINE et hors-réseau : on alimente `proxy_stream` avec un FAUX
itérateur asynchrone de paquets NDJSON (le format historique d'Onyx
`/chat/send-message` : `{"answer_piece": "..."}`, puis un paquet documents
`{"top_documents":[...]}` / citations, plus un éventuel `{"error":"..."}`) et on
asserte la SÉQUENCE de paquets émis vers le client.

On vérifie, comme exigé par la mission :
  * réponse bénigne relayée telle quelle, citations finales transmises ;
  * marqueur de fuite de prompt EN COURS de flux → flux AVORTÉ, le client
    reçoit un refus, JAMAIS la fuite ;
  * réponse finale affirmant un fait sans citation → override FINAL vers refus ;
  * le filtre ACL retire un document non autorisé du paquet citations final ;
  * erreur interne sur le chemin de contrôle → refus sûr (fail-closed).

Les vrais détecteurs/filtres déterministes (`guardrail`, `doc_acl`,
`onyx_proxy`) sont INJECTÉS (pas de mock inventé) — on prouve l'intégration
réelle, pas une reconstruction approximative.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from conftest import run

import app.audit as audit
from app.doc_acl import REFUSAL_NO_ACCESSIBLE_SOURCE, StaticDocACL, filter_citations
from app.guardrail import REFUSAL_INJECTION, REFUSAL_NO_CITATION, post_filter
from app.onyx_proxy import apply_filtered_answer, extract_answer
from app.streaming import REFUSAL_INTERNAL, proxy_stream


# --------------------------------------------------------------------------- #
# Outils de test : faux itérateur asynchrone + collecte des paquets émis.      #
# --------------------------------------------------------------------------- #
async def _aiter(lines: list[bytes]):
    """Transforme une liste de lignes en itérateur ASYNCHRONE (comme aiter_lines)."""
    for ln in lines:
        yield ln


def _ndjson(*packets: dict) -> list[bytes]:
    """Encode une suite de paquets en lignes NDJSON (bytes), comme Onyx amont."""
    return [(json.dumps(p, ensure_ascii=False) + "\n").encode("utf-8") for p in packets]


def _collect(upstream: list[bytes], **kwargs) -> list[dict]:
    """Exécute `proxy_stream` sur `upstream` et renvoie les paquets émis (décodés).

    Les valeurs par défaut câblent les VRAIS helpers ; chaque test peut surcharger
    (`acl`, `settings`, `post_filter`, …) via kwargs.
    """
    defaults: dict[str, Any] = dict(
        question="",
        principal=SimpleNamespace(user_id="u1", upn="alice@contoso.fr", group_ids=["G1"]),
        acl=None,
        settings=_settings(),
        post_filter=post_filter,
        doc_acl_filter=filter_citations,
        extract_answer=extract_answer,
        apply_filtered_answer=apply_filtered_answer,
        audit=None,
    )
    defaults.update(kwargs)

    async def _drive() -> list[dict]:
        out: list[dict] = []
        async for chunk in proxy_stream(_aiter(upstream), **defaults):
            text = chunk.decode("utf-8").strip()
            assert text, "un paquet émis ne doit jamais être vide"
            out.append(json.loads(text))
        return out

    return run(_drive())


def _settings(**over: Any) -> SimpleNamespace:
    base = dict(
        guardrail_enabled=True,
        doc_acl_enabled=True,
        doc_acl_strip_uncited=True,
        stream_enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _pieces(packets: list[dict]) -> str:
    """Reconstitue le texte relayé (concat des answer_piece émis au client)."""
    return "".join(p["answer_piece"] for p in packets if "answer_piece" in p)


def _overrides(packets: list[dict]) -> list[dict]:
    return [p for p in packets if p.get("override") is True]


# --------------------------------------------------------------------------- #
# 1) Réponse bénigne : tout passe, citations finales transmises.              #
# --------------------------------------------------------------------------- #
def test_benign_answer_flows_through_with_citations():
    upstream = _ndjson(
        {"answer_piece": "La procédure "},
        {"answer_piece": "est décrite "},
        {"answer_piece": "dans le document source.pdf."},
        {"top_documents": [{"document_id": "doc1", "semantic_identifier": "source.pdf"}]},
    )
    out = _collect(upstream)  # acl=None → aucun filtrage

    # Le texte relayé est intact, dans l'ordre.
    assert _pieces(out) == "La procédure est décrite dans le document source.pdf."
    # Aucun override (réponse conforme), un paquet documents transmis, un done final.
    assert _overrides(out) == []
    assert any("top_documents" in p for p in out)
    assert out[-1] == {"done": True}


def test_benign_answer_no_documents_still_terminates():
    out = _collect(_ndjson({"answer_piece": "Bonjour, "}, {"answer_piece": "voici une réponse générale."}),
                   question="dis bonjour")
    assert _pieces(out) == "Bonjour, voici une réponse générale."
    assert out[-1] == {"done": True}


# --------------------------------------------------------------------------- #
# 2) Fuite de prompt EN COURS de flux → avorté, le client ne voit pas la fuite. #
# --------------------------------------------------------------------------- #
def test_prompt_leak_midstream_aborts_before_leak_emitted():
    # Le marqueur dur "OWASP LLM01" déclenche `leaks_prompt_or_persona`.
    upstream = _ndjson(
        {"answer_piece": "Voici mes "},
        {"answer_piece": "instructions : OWASP LLM01 règles..."},  # ← morceau fautif
        {"answer_piece": " et la suite ne doit jamais sortir"},
        {"top_documents": [{"document_id": "doc1"}]},
    )
    out = _collect(upstream)

    relayed = _pieces(out)
    # Le 1er morceau bénin a pu être relayé ; le morceau fautif et la suite, NON.
    assert "OWASP" not in relayed
    assert "ne doit jamais sortir" not in relayed
    # Un override de refus a été émis, puis un done terminal.
    ov = _overrides(out)
    assert len(ov) == 1 and ov[0]["answer"] == REFUSAL_INJECTION
    assert ov[0]["rule"] == "no_prompt_leak"
    assert out[-1] == {"done": True}
    # Le paquet documents qui suivait l'abort n'est PAS émis (flux coupé).
    assert not any("top_documents" in p for p in out)


def test_simulated_write_midstream_aborts():
    upstream = _ndjson(
        {"answer_piece": "Très bien. "},
        {"answer_piece": "J'ai supprimé le document obsolète."},  # write simulé
    )
    out = _collect(upstream, question="supprime ce fichier")
    ov = _overrides(out)
    assert len(ov) == 1 and ov[0]["rule"] == "read_only"
    assert "supprimé" not in _pieces(out)
    assert out[-1] == {"done": True}


def test_exfil_link_relayed_midstream_aborts():
    upstream = _ndjson(
        {"answer_piece": "Cliquez ici : "},
        {"answer_piece": "http://exfil.example/collect pour valider."},
    )
    out = _collect(upstream)
    ov = _overrides(out)
    assert len(ov) == 1 and ov[0]["rule"] == "no_exfil_relay"
    assert "exfil.example" not in _pieces(out)


# --------------------------------------------------------------------------- #
# 3) Fait sans citation en fin de flux → override FINAL d'autorité.            #
# --------------------------------------------------------------------------- #
def test_unsourced_fact_triggers_final_override():
    # Aucun déclencheur DUR en route ; mais la phrase complète affirme un montant
    # chiffré SANS citation → le post-filtre COMPLET bloque en fin de flux.
    upstream = _ndjson(
        {"answer_piece": "La cotisation mensuelle "},
        {"answer_piece": "est de 142 € par salarié."},
    )
    out = _collect(upstream, question="quelle est la cotisation ?")

    # Tous les morceaux ont été relayés au fil de l'eau (gain de latence préservé)…
    assert _pieces(out) == "La cotisation mensuelle est de 142 € par salarié."
    # … MAIS un override final remplace la réponse par le refus de non-citation.
    ov = _overrides(out)
    assert len(ov) == 1
    assert ov[0]["answer"] == REFUSAL_NO_CITATION
    assert ov[0]["rule"] == "no_citation"
    # L'override est le DERNIER message d'autorité (contrat client).
    assert out[-1] == {"done": True}
    assert out[-2].get("override") is True


def test_sourced_fact_no_override():
    # Même fait chiffré, mais AVEC une citation → conforme, aucun override.
    upstream = _ndjson(
        {"answer_piece": "Selon source.pdf, la cotisation "},
        {"answer_piece": "est de 142 € (voir document)."},
    )
    out = _collect(upstream, question="quelle est la cotisation ?")
    assert _overrides(out) == []
    assert out[-1] == {"done": True}


# --------------------------------------------------------------------------- #
# 4) Filtre ACL par-document : retrait d'un doc non autorisé du paquet final.  #
# --------------------------------------------------------------------------- #
def _acl() -> StaticDocACL:
    # doc1 visible par G1 ; doc2 réservé à G2 (donc invisible pour notre principal G1).
    return StaticDocACL.from_obj(
        {"doc1": {"groups": ["G1"]}, "doc2": {"groups": ["G2"]}},
        default_policy="deny",
    )


def test_doc_acl_drops_unauthorized_document_in_stream():
    upstream = _ndjson(
        {"answer_piece": "Réponse appuyée sur deux sources."},
        {"top_documents": [
            {"document_id": "doc1", "semantic_identifier": "autorise.pdf"},
            {"document_id": "doc2", "semantic_identifier": "interdit.pdf"},
        ]},
    )
    out = _collect(upstream, acl=_acl())

    docs_packets = [p for p in out if "top_documents" in p]
    assert len(docs_packets) == 1
    kept_ids = {d["document_id"] for d in docs_packets[0]["top_documents"]}
    assert kept_ids == {"doc1"}  # doc2 (réservé G2) retiré
    # Il reste une source accessible → pas de substitution/override.
    assert _overrides(out) == []
    assert out[-1] == {"done": True}


def test_doc_acl_all_dropped_triggers_no_accessible_source_override():
    # Seul doc2 (réservé G2) est cité → APRÈS filtrage il ne reste AUCUNE source
    # accessible ⇒ strip_uncited substitue le refus + override d'autorité.
    # On met le texte d'assistant (`message`) dans le MÊME paquet que
    # `top_documents` (forme agrégée Onyx) pour qu'`extract_answer` trouve un
    # champ texte à substituer par `REFUSAL_NO_ACCESSIBLE_SOURCE`.
    upstream = _ndjson(
        {"answer_piece": "Le plafond 2026 est de 3 925 €."},
        {"message": "Le plafond 2026 est de 3 925 €.",
         "top_documents": [{"document_id": "doc2", "semantic_identifier": "interdit.pdf"}]},
    )
    out = _collect(upstream, acl=_acl())

    ov = _overrides(out)
    assert len(ov) == 1
    assert ov[0]["answer"] == REFUSAL_NO_ACCESSIBLE_SOURCE
    assert ov[0]["rule"] == "no_accessible_source"
    # Le paquet documents filtré ne contient plus doc2.
    docs_packets = [p for p in out if "top_documents" in p]
    assert docs_packets and docs_packets[0]["top_documents"] == []
    assert out[-1] == {"done": True}


def test_doc_acl_inactive_passes_documents_untouched():
    upstream = _ndjson(
        {"answer_piece": "ok"},
        {"top_documents": [{"document_id": "doc2"}]},
    )
    # acl actif côté objet mais settings.doc_acl_enabled=False → no-op.
    out = _collect(upstream, acl=_acl(), settings=_settings(doc_acl_enabled=False))
    docs = [p for p in out if "top_documents" in p][0]
    assert {d["document_id"] for d in docs["top_documents"]} == {"doc2"}


# --------------------------------------------------------------------------- #
# 5) Fail-closed : une erreur interne du garde → refus sûr, jamais de fuite.   #
# --------------------------------------------------------------------------- #
def test_internal_guard_error_fails_closed():
    def _boom(question, context, answer):  # post_filter qui explose en fin de flux
        raise RuntimeError("garde cassé")

    out = _collect(
        _ndjson({"answer_piece": "réponse "}, {"answer_piece": "bénigne."}),
        post_filter=_boom,
    )
    ov = _overrides(out)
    assert len(ov) == 1
    assert ov[0]["answer"] == REFUSAL_INTERNAL
    assert ov[0]["rule"] == "stream_postfilter_error"
    assert out[-1] == {"done": True}


def test_internal_doc_acl_error_fails_closed():
    def _boom_acl(*a, **k):  # filtre ACL qui explose sur le paquet documents
        raise RuntimeError("acl cassée")

    upstream = _ndjson(
        {"answer_piece": "ok"},
        {"top_documents": [{"document_id": "doc1"}]},
    )
    out = _collect(upstream, acl=_acl(), doc_acl_filter=_boom_acl)
    ov = _overrides(out)
    assert len(ov) == 1 and ov[0]["rule"] == "doc_acl_error"
    assert ov[0]["answer"] == REFUSAL_INTERNAL
    # Le paquet documents fautif n'a PAS été forwardé (fail-closed).
    assert not any("top_documents" in p for p in out)
    assert out[-1] == {"done": True}


# --------------------------------------------------------------------------- #
# 6) Robustesse : erreur amont relayée, lignes non-JSON tolérées.             #
# --------------------------------------------------------------------------- #
def test_upstream_error_packet_is_relayed():
    out = _collect(_ndjson({"answer_piece": "début"}, {"error": "LLM indisponible"}))
    assert any(p.get("error") == "LLM indisponible" for p in out)
    assert out[-1] == {"done": True}


def test_non_json_line_is_passed_through():
    upstream = [b"not-json-keepalive\n", *(_ndjson({"answer_piece": "ok"}))]
    # Ne doit pas crasher ; la ligne non-JSON est relayée brute, le reste suit.
    out_raw: list[str] = []

    async def _drive():
        async for chunk in proxy_stream(
            _aiter(upstream),
            question="", principal=SimpleNamespace(user_id="u1", upn="a@b.fr", group_ids=[]),
            acl=None, settings=_settings(), post_filter=post_filter,
            doc_acl_filter=filter_citations, extract_answer=extract_answer,
            apply_filtered_answer=apply_filtered_answer,
        ):
            out_raw.append(chunk.decode("utf-8").strip())

    run(_drive())
    assert "not-json-keepalive" in out_raw
    assert out_raw[-1] == json.dumps({"done": True}, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# 7) Audit : les décisions garde-fou sont journalisées (best-effort).         #
# --------------------------------------------------------------------------- #
class _AuditRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def log_guardrail_decision(self, **kwargs):
        self.calls.append(kwargs)


def test_abort_is_audited():
    rec = _AuditRecorder()
    _collect(
        _ndjson({"answer_piece": "OWASP LLM01"}),
        audit=rec,
    )
    assert any(c.get("blocked") and c.get("rule") == "no_prompt_leak" for c in rec.calls)


def test_guardrail_disabled_skips_hard_and_soft_checks():
    # guardrail_enabled=False : on relaie tout, même un marqueur dur (diag only).
    upstream = _ndjson({"answer_piece": "OWASP LLM01 fuite"}, {"answer_piece": " complète"})
    out = _collect(upstream, settings=_settings(guardrail_enabled=False))
    assert "OWASP LLM01 fuite complète" in _pieces(out)
    assert _overrides(out) == []
    assert out[-1] == {"done": True}
