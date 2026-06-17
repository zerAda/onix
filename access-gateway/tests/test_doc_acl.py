"""Tests du filtre ACL par-document (chemin RÉPONSE, FOSS).

Couvre :
  * Chargement JSON (`StaticDocACL.from_file` / `from_obj`).
  * `default_policy` deny vs allow pour un doc inconnu.
  * Match par groupe (casse insensible) ; override par utilisateur (UPN/oid).
  * `filter_citations` sur les différentes formes de réponse Onyx connues
    (`top_documents`, `context_docs`, `final_context_docs`, `documents`,
    `source_documents`, `citations`).
  * Substitution du texte de l'assistant si TOUTES les citations sont
    retirées (`strip_uncited=True`) — branchée sur `onyx_proxy.extract_answer`
    et `onyx_proxy.apply_filtered_answer` (forme RÉELLE supportée par le
    proxy, pas un mock inventé).
  * **Isolation RBAC bout-en-bout** : User A (G1) vs User B (G2) sur la MÊME
    réponse → résultats DIFFÉRENTS, drops cohérents.
  * Fail-OPEN sur exception interne (loader cassé) — body inchangé, audit
    logue un `doc_acl_error`.
  * Fail-CLOSED sur doc_id inconnu avec `default_policy=deny`.
  * Audit : un log par drop ET un log de résumé (counts agrégés).
  * Court-circuit `enabled=False` — no-op.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import app.audit as audit
import app.doc_acl as doc_acl
import app.onyx_proxy as onyx_proxy
from app.doc_acl import (
    CompositeDocACL,
    REFUSAL_NO_ACCESSIBLE_SOURCE,
    StaticDocACL,
    filter_citations,
)


# --------------------------------------------------------------------------- #
# Fixtures locales — Principal minimal + audit qui capture.                   #
# --------------------------------------------------------------------------- #
def _principal(*, user_id="u1", upn="alice@contoso.fr", groups=("G1",)):
    return SimpleNamespace(user_id=user_id, upn=upn, group_ids=list(groups))


class _AuditRecorder:
    """Capture les appels à `log_doc_acl_decision` pour vérification."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def log_doc_acl_decision(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        # On retourne un record minimal (le réel passe par audit.pseudonymize).
        return {"event": "doc_acl_decision", **kwargs}


@pytest.fixture()
def acl_simple() -> StaticDocACL:
    """ACL : doc1→G1, doc2→G2, doc3→G1∪G2, docU→user override (alice)."""
    return StaticDocACL.from_obj(
        {
            "doc1": {"groups": ["G1"], "users": []},
            "doc2": {"groups": ["G2"], "users": []},
            "doc3": {"groups": ["G1", "G2"], "users": []},
            "docU": {"groups": ["G3"], "users": ["alice@contoso.fr"]},
        },
        default_policy="deny",
    )


# --------------------------------------------------------------------------- #
# 1. StaticDocACL — chargement + politique par défaut + match groupe / user.  #
# --------------------------------------------------------------------------- #
def test_static_acl_loads_from_file(tmp_path):
    p = tmp_path / "doc_acl.json"
    p.write_text(json.dumps({"doc1": {"groups": ["G1"]}}), encoding="utf-8")
    acl = StaticDocACL.from_file(str(p))
    assert acl.is_authorized("doc1", _principal(groups=["G1"]))
    assert not acl.is_authorized("doc1", _principal(groups=["G2"]))


def test_static_acl_missing_file_returns_empty_deny_by_default(tmp_path):
    acl = StaticDocACL.from_file(str(tmp_path / "absent.json"))
    # default_policy='deny' (défaut) ⇒ tout est refusé.
    assert not acl.is_authorized("doc1", _principal(groups=["G1", "G2"]))


def test_static_acl_default_policy_allow_passes_unknown(acl_simple: StaticDocACL):
    acl = StaticDocACL.from_obj({"doc1": {"groups": ["G1"]}}, default_policy="allow")
    # Doc absent + default=allow ⇒ autorisé.
    assert acl.is_authorized("doc-unknown", _principal(groups=["G99"]))


def test_static_acl_group_match_is_case_insensitive(acl_simple: StaticDocACL):
    acl = StaticDocACL.from_obj({"doc1": {"groups": ["G1"]}})
    # Le mapping et le groupe arrivent en casses différentes : ça doit matcher.
    assert acl.is_authorized("doc1", _principal(groups=["g1"]))


def test_user_override_beats_group(acl_simple: StaticDocACL):
    # docU est réservé au groupe G3, mais 'alice@contoso.fr' a un override.
    alice = _principal(upn="alice@contoso.fr", groups=["G_other"])
    assert acl_simple.is_authorized("docU", alice)
    # Et un autre utilisateur du groupe G_other reste refusé.
    bob = _principal(user_id="u2", upn="bob@contoso.fr", groups=["G_other"])
    assert not acl_simple.is_authorized("docU", bob)


def test_unknown_doc_with_default_deny_is_refused(acl_simple: StaticDocACL):
    # Doc absent de l'ACL ; default_policy=deny ⇒ refusé.
    assert not acl_simple.is_authorized("doc-mystere", _principal(groups=["G1"]))


def test_static_acl_invalid_default_policy_raises():
    with pytest.raises(ValueError):
        StaticDocACL(entries={}, default_policy="maybe")  # type: ignore[arg-type]


def test_static_acl_invalid_payload_raises():
    with pytest.raises(ValueError):
        StaticDocACL.from_obj([{"doc1": "no"}])  # racine non-objet
    with pytest.raises(ValueError):
        StaticDocACL.from_obj({"doc1": "no"})  # entrée non-objet
    with pytest.raises(ValueError):
        StaticDocACL.from_obj({"doc1": {"groups": "G1"}})  # groups doit être une liste


# --------------------------------------------------------------------------- #
# 2. CompositeDocACL — OR-merge.                                              #
# --------------------------------------------------------------------------- #
def test_composite_or_merges_sources():
    a = StaticDocACL.from_obj({"doc1": {"groups": ["G1"]}})
    b = StaticDocACL.from_obj({"doc1": {"groups": ["G2"]}})
    composite = CompositeDocACL([a, b])
    # G2 n'autoriserait pas via 'a' ; mais via 'b' oui ⇒ OR.
    assert composite.is_authorized("doc1", _principal(groups=["G2"]))
    # G99 dans aucune source ⇒ refus.
    assert not composite.is_authorized("doc1", _principal(groups=["G99"]))


def test_composite_requires_at_least_one_source():
    with pytest.raises(ValueError):
        CompositeDocACL([])


# --------------------------------------------------------------------------- #
# 3. filter_citations — formes Onyx connues.                                   #
# --------------------------------------------------------------------------- #
def test_filter_drops_unauthorized_top_documents(acl_simple: StaticDocACL):
    body = {
        "message": "résumé...",
        "top_documents": [
            {"document_id": "doc1", "blurb": "ok"},
            {"document_id": "doc2", "blurb": "secret"},
        ],
    }
    out, dropped = filter_citations(body, _principal(groups=["G1"]), acl_simple)
    assert [d["document_id"] for d in out["top_documents"]] == ["doc1"]
    assert {d["doc_id"] for d in dropped} == {"doc2"}


def test_filter_handles_all_known_shapes(acl_simple: StaticDocACL):
    body = {
        "message": "x",
        "top_documents": [{"document_id": "doc1"}],
        "context_docs": [{"id": "doc2"}],
        "final_context_docs": [{"source_id": "doc1"}],
        "documents": [{"document_id": "doc2"}],
        "source_documents": [{"id": "doc1"}],
        "citations": [{"document_id": "doc2"}, {"document_id": "doc3"}],
    }
    out, dropped = filter_citations(body, _principal(groups=["G1"]), acl_simple)
    # doc2 retiré partout ; doc1 et doc3 conservés là où ils étaient.
    assert out["top_documents"] == [{"document_id": "doc1"}]
    assert out["context_docs"] == []
    assert out["documents"] == []
    assert [c["document_id"] for c in out["citations"]] == ["doc3"]
    # doc2 retiré N fois (champs différents).
    dropped_ids = sorted(d["doc_id"] for d in dropped)
    assert dropped_ids.count("doc2") >= 2


def test_filter_tolerates_unknown_shape(acl_simple: StaticDocACL):
    # Forme inattendue (liste racine de paquets streaming par exemple) :
    # le filtre ne crashe pas, renvoie le body inchangé.
    body = [{"answer_piece": "x"}]
    out, dropped = filter_citations(body, _principal(groups=["G1"]), acl_simple)
    assert out is body
    assert dropped == []


def test_filter_preserves_items_without_id(acl_simple: StaticDocACL):
    # Un item sans `document_id` ne peut pas être jugé ; on le laisse passer.
    body = {
        "top_documents": [
            {"document_id": "doc2"},
            {"blurb": "sans-id"},  # pas d'identifiant ⇒ conservé
        ]
    }
    out, _ = filter_citations(body, _principal(groups=["G1"]), acl_simple)
    assert {"blurb": "sans-id"} in out["top_documents"]
    assert {"document_id": "doc2"} not in out["top_documents"]


# --------------------------------------------------------------------------- #
# 4. Substitution du texte si toutes les citations sont retirées.             #
# --------------------------------------------------------------------------- #
def test_strip_uncited_substitutes_safe_refusal(acl_simple: StaticDocACL):
    body = {
        "message": "Le client X a payé 12 000 €.",
        "top_documents": [{"document_id": "doc2"}],
        "citations": [{"document_id": "doc2"}],
    }
    out, dropped = filter_citations(
        body,
        _principal(groups=["G1"]),
        acl_simple,
        strip_uncited=True,
        extract_answer=onyx_proxy.extract_answer,
        apply_filtered_answer=onyx_proxy.apply_filtered_answer,
    )
    assert out["message"] == REFUSAL_NO_ACCESSIBLE_SOURCE
    assert dropped  # doc2 a bien été drop


def test_strip_uncited_disabled_keeps_answer(acl_simple: StaticDocACL):
    body = {
        "message": "Le client X a payé 12 000 €.",
        "top_documents": [{"document_id": "doc2"}],
    }
    out, _ = filter_citations(
        body,
        _principal(groups=["G1"]),
        acl_simple,
        strip_uncited=False,
        extract_answer=onyx_proxy.extract_answer,
        apply_filtered_answer=onyx_proxy.apply_filtered_answer,
    )
    # Citations retirées mais le texte de l'assistant n'est PAS substitué.
    assert out["message"] == "Le client X a payé 12 000 €."
    assert out["top_documents"] == []


def test_strip_uncited_not_applied_when_partial_drop(acl_simple: StaticDocACL):
    # doc1 reste autorisé ⇒ il ne faut PAS substituer le message.
    body = {
        "message": "ok",
        "top_documents": [{"document_id": "doc1"}, {"document_id": "doc2"}],
    }
    out, _ = filter_citations(
        body,
        _principal(groups=["G1"]),
        acl_simple,
        strip_uncited=True,
        extract_answer=onyx_proxy.extract_answer,
        apply_filtered_answer=onyx_proxy.apply_filtered_answer,
    )
    assert out["message"] == "ok"


# --------------------------------------------------------------------------- #
# 5. PREUVE D'ISOLATION RBAC — même réponse, deux utilisateurs ⇒ deux vues.   #
# --------------------------------------------------------------------------- #
def test_rbac_isolation_two_users_same_body_different_filtered(acl_simple: StaticDocACL):
    body = {
        "message": "x",
        "top_documents": [
            {"document_id": "doc1", "blurb": "nord"},
            {"document_id": "doc2", "blurb": "sud"},
            {"document_id": "doc3", "blurb": "partage"},
        ],
    }
    user_nord = _principal(user_id="nord", upn="n@x", groups=["G1"])
    user_sud = _principal(user_id="sud", upn="s@x", groups=["G2"])

    out_nord, drop_nord = filter_citations(body, user_nord, acl_simple)
    out_sud, drop_sud = filter_citations(body, user_sud, acl_simple)

    ids_nord = sorted(d["document_id"] for d in out_nord["top_documents"])
    ids_sud = sorted(d["document_id"] for d in out_sud["top_documents"])
    assert ids_nord == ["doc1", "doc3"]
    assert ids_sud == ["doc2", "doc3"]
    # Et chaque user a bien le DOC de l'autre dans ses drops.
    assert {d["doc_id"] for d in drop_nord} == {"doc2"}
    assert {d["doc_id"] for d in drop_sud} == {"doc1"}


# --------------------------------------------------------------------------- #
# 6. Disciplines d'erreur — fail-OPEN interne / fail-CLOSED doc inconnu.      #
# --------------------------------------------------------------------------- #
class _ExplodingACL(doc_acl.DocACL):
    """ACL qui crashe à chaque appel — sert à prouver le fail-OPEN."""

    def is_authorized(self, doc_id: str, principal: Any) -> bool:  # noqa: D401
        raise RuntimeError("loader cassé (simulation)")


def test_fail_open_on_internal_error_returns_body_unchanged():
    body = {"top_documents": [{"document_id": "doc1"}]}
    recorder = _AuditRecorder()
    out, dropped = filter_citations(
        body, _principal(), _ExplodingACL(), audit=recorder
    )
    # Body strictement inchangé (fail-OPEN — disponibilité), aucun drop signalé.
    assert out == body
    assert dropped == []
    # Et l'erreur est journalisée comme `doc_acl_error`.
    assert any(c.get("decision") == "error" for c in recorder.calls)


def test_fail_closed_on_unknown_doc_with_default_deny():
    # ACL VIDE + default_policy=deny ⇒ tout doc inconnu est retiré.
    acl = StaticDocACL.from_obj({}, default_policy="deny")
    body = {"top_documents": [{"document_id": "doc-anywhere"}]}
    out, dropped = filter_citations(body, _principal(groups=["G1"]), acl)
    assert out["top_documents"] == []
    assert {d["doc_id"] for d in dropped} == {"doc-anywhere"}


# --------------------------------------------------------------------------- #
# 7. Audit — un log par drop ET un log de résumé.                              #
# --------------------------------------------------------------------------- #
def test_audit_emits_one_drop_per_doc_plus_summary(acl_simple: StaticDocACL):
    body = {
        "top_documents": [
            {"document_id": "doc1"},
            {"document_id": "doc2"},
            {"document_id": "doc3"},  # doc3 autorisé G1 (autorisé pour G1 user)
        ]
    }
    recorder = _AuditRecorder()
    _, dropped = filter_citations(
        body, _principal(groups=["G1"]), acl_simple, audit=recorder
    )
    # doc2 est le seul drop ici (G1 voit doc1 et doc3).
    assert {d["doc_id"] for d in dropped} == {"doc2"}
    drops = [c for c in recorder.calls if c.get("decision") == "drop"]
    summaries = [c for c in recorder.calls if c.get("decision") == "summary"]
    assert len(drops) == 1
    assert drops[0]["doc_id"] == "doc2"
    assert len(summaries) == 1
    assert summaries[0]["dropped"] == 1
    assert summaries[0]["candidates"] == 3
    assert summaries[0]["allowed"] == 2


def test_audit_log_doc_acl_decision_hashes_actor(monkeypatch):
    """L'audit `log_doc_acl_decision` réutilise la chaîne HMAC d'audit (pas de
    fuite d'identité — cohérent avec `log_access_decision`)."""
    monkeypatch.setenv("GATEWAY_AUDIT_SALT", "unit-salt")
    audit.reset_salt_cache()
    rec = audit.log_doc_acl_decision(
        actor="secret-upn@contoso.fr",
        doc_id="doc1",
        decision="drop",
        reason="not_authorized",
        endpoint="chat/send-message",
        field="top_documents",
    )
    assert rec["actor_hash"] == audit.pseudonymize("secret-upn@contoso.fr")
    assert "secret-upn@contoso.fr" not in json.dumps(rec)
    assert rec["doc_id"] == "doc1"
    assert rec["field"] == "top_documents"


# --------------------------------------------------------------------------- #
# 8. Court-circuit `enabled=False` — no-op.                                    #
# --------------------------------------------------------------------------- #
def test_disabled_is_noop(acl_simple: StaticDocACL):
    body = {"top_documents": [{"document_id": "doc2"}]}
    out, dropped = filter_citations(
        body, _principal(groups=["G1"]), acl_simple, enabled=False
    )
    assert out is body
    assert dropped == []


# --------------------------------------------------------------------------- #
# 9. Body sans candidats — pas de copy inutile, pas d'audit.                   #
# --------------------------------------------------------------------------- #
def test_no_candidates_no_op(acl_simple: StaticDocACL):
    body = {"message": "hello"}
    recorder = _AuditRecorder()
    out, dropped = filter_citations(
        body, _principal(groups=["G1"]), acl_simple, audit=recorder
    )
    assert out is body
    assert dropped == []
    assert recorder.calls == []
