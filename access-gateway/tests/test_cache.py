"""Tests du cache applicatif RBAC-safe (`app/cache.py`).

Périmètre couvert (offline, AUCUN réseau) :
  * normalisation déterministe de la question ;
  * isolation RBAC stricte par périmètre (HMAC sur authorized_doc_sets sortés) ;
  * politique de bypass (Cache-Control: no-store, intention d'écriture, stream) ;
  * backend InMemory : roundtrip, éviction LRU, TTL ;
  * backend Redis : fail-soft sur erreur de connexion (jamais d'exception
    propagée vers la requête) — sans lib `fakeredis`, on monkeypatche
    `redis.Redis.from_url` pour ne JAMAIS toucher un vrai socket ;
  * fail-loud : secret HMAC manquant alors que cache_enabled=True ;
  * câblage des métriques (hit / bypass / error) via les helpers exception-safe.

Tous les tests sont indépendants et reset l'env via `monkeypatch`.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

# Rendre le package `app` importable (access-gateway/ est la racine).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import cache as cache_mod  # noqa: E402
from app import config as config_mod  # noqa: E402
from app import metrics as metrics_mod  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures locales.
# ─────────────────────────────────────────────────────────────────────────────
class _Settings:
    """Mini-settings (évite la lecture d'env globale dans chaque test)."""
    def __init__(self, **kw):
        self.cache_enabled = kw.get("cache_enabled", True)
        self.cache_redis_url = kw.get("cache_redis_url", "")
        self.cache_ttl_seconds = kw.get("cache_ttl_seconds", 3600)
        self.cache_max_entries = kw.get("cache_max_entries", 8)
        self.cache_hmac_secret = kw.get("cache_hmac_secret", "secret-fort-de-test-32-octets-au-moins")
        self.cache_locale = kw.get("cache_locale", "fr")


@pytest.fixture()
def settings():
    return _Settings()


# ─────────────────────────────────────────────────────────────────────────────
# normalize_question.
# ─────────────────────────────────────────────────────────────────────────────
class TestNormalizeQuestion:
    """La normalisation est DÉTERMINISTE (lowercase + collapse espaces).

    On vérifie qu'elle est invariante aux blancs/casse — sans toucher au
    contenu sémantique (pas de dé-accentuation : limite assumée). Cette
    garantie est ce qui rend la clé HMAC reproductible inter-clients."""

    def test_idempotence(self):
        assert cache_mod.normalize_question("bonjour") == "bonjour"

    def test_collapse_whitespace_and_casing(self):
        assert cache_mod.normalize_question("  Hello   WORLD\n\t!?  ") == "hello world !?"

    def test_empty_input(self):
        assert cache_mod.normalize_question("") == ""
        assert cache_mod.normalize_question(None) == ""  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# make_cache_key — RBAC isolation.
# ─────────────────────────────────────────────────────────────────────────────
class TestCacheKey:
    """La clé HMAC garantit l'isolation par périmètre. Si on change
    AUTHORIZED_DOC_SETS (et SEULEMENT cela), la clé doit changer — c'est la
    preuve qu'un utilisateur ne peut pas se voir servir le cache d'un autre."""

    def test_deterministic(self, settings):
        k1 = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="quelles sont les echéances",
            authorized_doc_sets=["clients-nord", "internal"],
        )
        k2 = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="quelles sont les echéances",
            authorized_doc_sets=["clients-nord", "internal"],
        )
        assert k1 == k2
        # 64 hex chars = SHA-256 hex.
        assert len(k1) == 64 and all(c in "0123456789abcdef" for c in k1)

    def test_key_isolation_by_perimeter(self, settings):
        """RBAC isolation : différents périmètres → clés différentes."""
        nord = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="x", authorized_doc_sets=["clients-nord"],
        )
        sud = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="x", authorized_doc_sets=["clients-sud"],
        )
        assert nord != sud, (
            "RBAC : un changement de périmètre DOIT changer la clé "
            "(sinon User Sud reçoit potentiellement le cache de User Nord)."
        )

    def test_key_independent_of_set_order(self, settings):
        """Tri stable : l'ordre de présentation des sets autorisés ne change PAS
        la clé (sinon le hit-rate s'effondrerait selon l'humeur du mapping)."""
        k1 = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="x", authorized_doc_sets=["b", "a", "c"],
        )
        k2 = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="x", authorized_doc_sets=["a", "b", "c"],
        )
        assert k1 == k2

    def test_key_sensitive_to_question(self, settings):
        k1 = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="q1", authorized_doc_sets=["a"],
        )
        k2 = cache_mod.make_cache_key(
            settings=settings, principal="u1",
            normalized_question="q2", authorized_doc_sets=["a"],
        )
        assert k1 != k2

    def test_key_sensitive_to_locale_and_extras(self, settings):
        s_fr = _Settings(cache_locale="fr")
        s_en = _Settings(cache_locale="en")
        kfr = cache_mod.make_cache_key(settings=s_fr, principal="u", normalized_question="x", authorized_doc_sets=["a"])
        ken = cache_mod.make_cache_key(settings=s_en, principal="u", normalized_question="x", authorized_doc_sets=["a"])
        assert kfr != ken

        kbase = cache_mod.make_cache_key(settings=s_fr, principal="u", normalized_question="x", authorized_doc_sets=["a"])
        kmodel = cache_mod.make_cache_key(
            settings=s_fr, principal="u", normalized_question="x",
            authorized_doc_sets=["a"], extras={"model": "qwen2.5:7b"},
        )
        assert kbase != kmodel

    def test_key_requires_secret(self):
        s = _Settings(cache_hmac_secret="")
        with pytest.raises(RuntimeError, match="HMAC"):
            cache_mod.make_cache_key(
                settings=s, principal="u", normalized_question="x",
                authorized_doc_sets=["a"],
            )

    def test_extras_unserialisable_raises(self, settings):
        """Un dict d'extras non-JSON casse tôt et fort : on ne tolère pas une
        clé silencieusement non-reproductible."""
        class _NoJson:
            pass
        with pytest.raises(ValueError, match="JSON"):
            cache_mod.make_cache_key(
                settings=settings, principal="u",
                normalized_question="x", authorized_doc_sets=["a"],
                extras={"obj": _NoJson()},
            )


# ─────────────────────────────────────────────────────────────────────────────
# should_bypass.
# ─────────────────────────────────────────────────────────────────────────────
class TestShouldBypass:
    """Les raisons retournées sont des littéraux figés (= valeurs du label
    `reason` de `cache_bypassed_total`) — un test « casse-tête » assure qu'on
    ne les renomme pas sans casser explicitement la métrique."""

    def test_no_store_header_short_circuits(self):
        reason = cache_mod.should_bypass(
            payload={"message": "bonjour"},
            headers={"Cache-Control": "no-store"},
        )
        assert reason == "no_store"

    def test_no_store_case_insensitive(self):
        reason = cache_mod.should_bypass(
            payload={"message": "bonjour"},
            headers={"cache-control": "No-Store, max-age=0"},
        )
        assert reason == "no_store"

    def test_write_intent_bypasses(self):
        reason = cache_mod.should_bypass(
            payload={"message": "Supprime le client ABC"}, headers={},
        )
        assert reason == "write_intent"

    def test_streaming_bypasses(self):
        reason = cache_mod.should_bypass(
            payload={"message": "ok", "stream": True}, headers={},
        )
        assert reason == "streaming"

    def test_admin_bypass_header(self):
        reason = cache_mod.should_bypass(
            payload={"message": "ok"},
            headers={"X-Onix-Cache": "bypass"},
            is_admin=True,
        )
        assert reason == "explicit_admin_bypass"

    def test_non_admin_cannot_force_bypass(self):
        """L'header X-Onix-Cache ne doit avoir AUCUN effet pour un non-admin :
        un utilisateur lambda ne doit pas pouvoir contourner le cache (pas
        d'attaque par déni de cache → coût)."""
        reason = cache_mod.should_bypass(
            payload={"message": "ok"},
            headers={"X-Onix-Cache": "bypass"},
            is_admin=False,
        )
        assert reason is None

    def test_normal_message_is_cacheable(self):
        reason = cache_mod.should_bypass(
            payload={"message": "Quelles sont les échéances ?"}, headers={},
        )
        assert reason is None


# ─────────────────────────────────────────────────────────────────────────────
# InMemoryBackend.
# ─────────────────────────────────────────────────────────────────────────────
class TestInMemoryBackend:
    """LRU bornée + TTL via source de temps injectée (déterministe)."""

    def test_set_get_roundtrip(self):
        be = cache_mod.InMemoryBackend(max_entries=4)
        be.set("k", b"v", ttl=60)
        assert be.get("k") == b"v"

    def test_missing_key_returns_none(self):
        be = cache_mod.InMemoryBackend(max_entries=4)
        assert be.get("absent") is None

    def test_lru_eviction(self):
        """Quand on dépasse la borne, la clé la PLUS ANCIENNE part."""
        be = cache_mod.InMemoryBackend(max_entries=2)
        be.set("a", b"1", ttl=0)
        be.set("b", b"2", ttl=0)
        # Toucher a → b devient le moins récent.
        assert be.get("a") == b"1"
        be.set("c", b"3", ttl=0)
        assert be.get("b") is None  # évincé
        assert be.get("a") == b"1"
        assert be.get("c") == b"3"
        assert len(be) == 2

    def test_ttl_expiry_with_injected_clock(self):
        """Source de temps injectable : pas de sleep dans les tests."""
        t = {"now": 1000.0}
        be = cache_mod.InMemoryBackend(max_entries=4, time_func=lambda: t["now"])
        be.set("k", b"v", ttl=10)
        assert be.get("k") == b"v"
        t["now"] = 1010.0  # juste à la frontière → expiré (>=)
        assert be.get("k") is None
        # L'entrée a été nettoyée à la lecture.
        assert len(be) == 0

    def test_ttl_zero_keeps_entry(self):
        """ttl<=0 → pas d'expiration : utile pour des entrées « pinned »
        (test des semaines/diagnostic). Sécurisé tant que la LRU borne la mémoire."""
        be = cache_mod.InMemoryBackend(max_entries=4)
        be.set("k", b"v", ttl=0)
        assert be.get("k") == b"v"

    def test_max_entries_invalid(self):
        with pytest.raises(ValueError):
            cache_mod.InMemoryBackend(max_entries=0)


# ─────────────────────────────────────────────────────────────────────────────
# RedisBackend — sans réseau, via monkeypatch.
# ─────────────────────────────────────────────────────────────────────────────
class _FakeRedisOk:
    """Faux client Redis qui « marche ». Suffit pour vérifier get/set/close."""
    def __init__(self):
        self.store = {}
    def get(self, key):
        return self.store.get(key)
    def set(self, key, value, ex=None):
        self.store[key] = value
    def close(self):
        self.closed = True


class _FakeRedisError:
    """Faux client Redis qui lève sur TOUTE opération."""
    def get(self, key):
        raise ConnectionError("redis down")
    def set(self, *a, **kw):
        raise ConnectionError("redis down")
    def close(self):
        pass


class TestRedisBackend:
    def _patch_redis(self, monkeypatch, fake_cls):
        """Remplace redis.Redis.from_url par une factory qui renvoie `fake_cls()`.

        Astuce : on construit (ou attrape) le module `redis` ; s'il n'est pas
        importé, on en pose un stub minimal avant l'import du backend."""
        import types
        fake_module = types.ModuleType("redis")
        class _Redis:
            @staticmethod
            def from_url(url, **kwargs):
                return fake_cls()
        fake_module.Redis = _Redis  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "redis", fake_module)

    def test_get_set_happy_path(self, monkeypatch):
        self._patch_redis(monkeypatch, _FakeRedisOk)
        be = cache_mod.RedisBackend("redis://test/0")
        be.set("k", b"v", ttl=30)
        assert be.get("k") == b"v"

    def test_get_returns_none_on_connection_error(self, monkeypatch):
        """Cœur du contrat : l'erreur Redis se TRADUIT en miss, jamais en
        exception qui remonterait dans la requête."""
        self._patch_redis(monkeypatch, _FakeRedisError)
        errors = []
        be = cache_mod.RedisBackend(
            "redis://test/0", on_error=lambda op, exc: errors.append(op),
        )
        assert be.get("k") is None  # NE LÈVE PAS
        be.set("k", b"v", ttl=10)   # NE LÈVE PAS NON PLUS
        assert "get" in errors and "set" in errors

    def test_decode_responses_str_path(self, monkeypatch):
        """Si un client custom renvoie str (decode_responses=True), on
        ré-encode en bytes pour respecter le contrat de la façade."""
        class _StrClient:
            def get(self, k):
                return "hello"
            def set(self, *a, **kw):
                pass
            def close(self):
                pass
        self._patch_redis(monkeypatch, _StrClient)
        be = cache_mod.RedisBackend("redis://test/0")
        assert be.get("k") == b"hello"


# ─────────────────────────────────────────────────────────────────────────────
# Cache façade — JSON roundtrip + exception-safety + intégration métriques.
# ─────────────────────────────────────────────────────────────────────────────
class TestCacheFacade:
    def test_json_roundtrip_is_lossless(self, settings):
        be = cache_mod.InMemoryBackend(max_entries=4)
        c = cache_mod.Cache(be)
        body = {
            "message": "Réponse avec accents éàùç et 🇫🇷",
            "top_documents": [{"semantic_identifier": "doc.pdf", "blurb": "..."}],
            "metadata": {"nested": {"k": 1, "v": [1, 2, 3]}},
        }
        c.store("key", body, ttl=60)
        out = c.lookup("key")
        assert out == body

    def test_lookup_returns_none_on_missing(self, settings):
        be = cache_mod.InMemoryBackend(max_entries=4)
        c = cache_mod.Cache(be)
        assert c.lookup("absent") is None

    def test_backend_failure_is_swallowed(self, settings):
        """Si le backend lève sur get/set, la façade NE lève PAS — c'est la
        promesse du « cache n'est pas une source d'autorité »."""
        class _Boom(cache_mod.CacheBackend):
            def get(self, key): raise RuntimeError("boom")
            def set(self, key, value, ttl): raise RuntimeError("boom")
            def close(self): pass

        errors = []
        c = cache_mod.Cache(_Boom(), on_error=lambda op, exc: errors.append(op))
        assert c.lookup("k") is None
        c.store("k", {"x": 1}, ttl=10)  # ne lève pas
        assert errors.count("get") == 1 and errors.count("set") == 1

    def test_corrupt_payload_is_treated_as_miss(self):
        """Un body non-JSON dans le backend → miss propre (pas d'erreur 500)."""
        be = cache_mod.InMemoryBackend(max_entries=4)
        be.set("k", b"\xff\xfenot json", ttl=10)
        c = cache_mod.Cache(be)
        assert c.lookup("k") is None


# ─────────────────────────────────────────────────────────────────────────────
# build_cache — sélection backend + fail-loud sur secret manquant.
# ─────────────────────────────────────────────────────────────────────────────
class TestBuildCache:
    def test_disabled_returns_none(self):
        s = _Settings(cache_enabled=False)
        assert cache_mod.build_cache(s) is None

    def test_enabled_without_secret_fails_loud(self):
        """Cœur du contrat : si on ACTIVE le cache sans secret HMAC, on REFUSE
        de démarrer. Pas de sel autogénéré (sinon la clé change à chaque
        redémarrage et le hit-rate s'effondre)."""
        s = _Settings(cache_enabled=True, cache_hmac_secret="")
        with pytest.raises(RuntimeError, match="GATEWAY_CACHE_HMAC_SECRET"):
            cache_mod.build_cache(s)

    def test_enabled_in_memory_default(self, settings):
        c = cache_mod.build_cache(settings)
        assert c is not None
        assert isinstance(c._backend, cache_mod.InMemoryBackend)

    def test_enabled_redis_when_url_set(self, monkeypatch):
        """L'URL Redis prime sur l'InMemory (sans toucher au réseau : on
        monkeypatche redis.Redis.from_url)."""
        import types
        fake_module = types.ModuleType("redis")
        class _Redis:
            @staticmethod
            def from_url(url, **kwargs):
                class _C:
                    def get(self, k): return None
                    def set(self, *a, **kw): pass
                    def close(self): pass
                return _C()
        fake_module.Redis = _Redis  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "redis", fake_module)

        s = _Settings(cache_redis_url="redis://x")
        c = cache_mod.build_cache(s)
        assert c is not None
        assert isinstance(c._backend, cache_mod.RedisBackend)


# ─────────────────────────────────────────────────────────────────────────────
# Intégration avec config — fail-loud effectif au niveau de get_settings + build_cache.
# ─────────────────────────────────────────────────────────────────────────────
class TestSettingsIntegration:
    def test_get_settings_missing_secret_then_build_raises(self, monkeypatch):
        """Le contrat « fail-loud à l'init quand cache_enabled=True ET secret
        manquant » est appliqué par `build_cache`, pas par `Settings` (le
        dataclass est validé tardivement, à la construction du cache)."""
        monkeypatch.setenv("GATEWAY_CACHE_ENABLED", "true")
        monkeypatch.delenv("GATEWAY_CACHE_HMAC_SECRET", raising=False)
        config_mod.reset_settings_cache()
        s = config_mod.get_settings()
        assert s.cache_enabled is True
        assert s.cache_hmac_secret == ""
        with pytest.raises(RuntimeError):
            cache_mod.build_cache(s)
        config_mod.reset_settings_cache()  # nettoyage pour les autres tests

    def test_get_settings_disabled(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_CACHE_ENABLED", "false")
        config_mod.reset_settings_cache()
        s = config_mod.get_settings()
        assert s.cache_enabled is False
        assert cache_mod.build_cache(s) is None
        config_mod.reset_settings_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Métriques — les compteurs réagissent aux hits, miss, bypass, erreurs.
# ─────────────────────────────────────────────────────────────────────────────
def _metric_value(name: str, **labels) -> float:
    """Lecture de la valeur courante d'un compteur Prometheus du registre."""
    from prometheus_client import REGISTRY
    val = REGISTRY.get_sample_value(name, labels=labels)
    return float(val) if val is not None else 0.0


class TestMetricsIntegration:
    def test_hit_increments_cache_hits_total(self, settings):
        before = _metric_value("onix_gateway_cache_hits_total", tier="exact")
        c = cache_mod.build_cache(settings)
        assert c is not None
        c.store("k1", {"message": "ok"}, ttl=60)
        out = c.lookup("k1")
        assert out == {"message": "ok"}
        after = _metric_value("onix_gateway_cache_hits_total", tier="exact")
        assert after == before + 1

    def test_miss_increments_cache_misses_total(self, settings):
        before = _metric_value("onix_gateway_cache_misses_total")
        c = cache_mod.build_cache(settings)
        assert c is not None
        assert c.lookup("totally-absent-key") is None
        after = _metric_value("onix_gateway_cache_misses_total")
        assert after == before + 1

    def test_bypass_no_store_increments(self):
        before = _metric_value("onix_gateway_cache_bypassed_total", reason="no_store")
        # Le caller (main.py orchestré) appelle inc_cache_bypassed avec la
        # raison renvoyée par should_bypass. On reproduit ce câblage ici.
        reason = cache_mod.should_bypass(
            payload={"message": "ok"}, headers={"Cache-Control": "no-store"},
        )
        assert reason == "no_store"
        metrics_mod.inc_cache_bypassed(reason)
        after = _metric_value("onix_gateway_cache_bypassed_total", reason="no_store")
        assert after == before + 1

    def test_error_increments_cache_errors_total(self):
        before = _metric_value("onix_gateway_cache_errors_total", op="get")
        metrics_mod.inc_cache_error("get")
        after = _metric_value("onix_gateway_cache_errors_total", op="get")
        assert after == before + 1

    def test_tokens_saved_uses_estimate(self):
        """`estimate_tokens` produit un ordre de grandeur cohérent avec
        chars/4 sur le texte de l'assistant. On vérifie la borne inférieure
        (> 0 quand il y a une réponse)."""
        body = {"message": "abcd" * 10}  # 40 chars → ~10 tokens
        assert cache_mod.estimate_tokens(body) >= 5

    def test_tokens_saved_zero_when_empty(self):
        assert cache_mod.estimate_tokens({}) >= 0  # ne lève pas, retourne >= 0
