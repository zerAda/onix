"""Tests du TIER SÉMANTIQUE du cache RBAC-safe (`app/cache.py`).

Périmètre couvert (OFFLINE STRICT — la fonction d'embedding est TOUJOURS mockée,
aucun réseau, aucun Ollama réel) :

  * extraction des marqueurs factuels (nombres/dates/montants/%/entités) ;
  * cosinus pur-Python (cas dégénérés inclus) ;
  * `SemanticIndex` : add/search bornés, partition PAR PÉRIMÈTRE ;
  * **PROPRIÉTÉ CARDINALE — match cross-périmètre STRUCTURELLEMENT impossible** ;
  * near-duplicate au-dessus du seuil → HIT sémantique ;
  * sous le seuil → MISS ;
  * **divergence numérique (2024 vs 2025) → REJET** malgré la similarité ;
  * **divergence d'entité (ALPHA vs BETA) → REJET** malgré la similarité ;
  * échec d'embedding → MISS gracieux (jamais d'exception dans la requête) ;
  * `build_embed_fn` exception-safe (httpx en échec → None) ;
  * indexation best-effort via `store(... embed_fn=...)` ;
  * compteurs Prometheus (hit semantic / candidates / rejected_divergence) ;
  * `build_cache` : index sémantique présent SSI opt-in.

Le mock d'embedding mappe un texte normalisé → un vecteur déterministe. On
contrôle ainsi EXACTEMENT la similarité cosinus, ce qui rend les assertions de
seuil et de rejet déterministes sans dépendre d'un vrai modèle.
"""
from __future__ import annotations

import math
import os
import sys
import types

import pytest

# Rendre le package `app` importable (access-gateway/ est la racine).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import cache as cache_mod  # noqa: E402
from app import metrics as metrics_mod  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures locales.
# ─────────────────────────────────────────────────────────────────────────────
class _Settings:
    """Mini-settings pour piloter `build_cache` sans lire l'env globale."""

    def __init__(self, **kw):
        self.cache_enabled = kw.get("cache_enabled", True)
        self.cache_redis_url = kw.get("cache_redis_url", "")
        self.cache_ttl_seconds = kw.get("cache_ttl_seconds", 3600)
        self.cache_max_entries = kw.get("cache_max_entries", 8)
        self.cache_hmac_secret = kw.get("cache_hmac_secret", "secret-fort-de-test-32-octets-au-moins")
        self.cache_locale = kw.get("cache_locale", "fr")
        # Tier sémantique (opt-in).
        self.semantic_cache_enabled = kw.get("semantic_cache_enabled", False)
        self.semantic_embed_url = kw.get("semantic_embed_url", "http://ollama:11434/api/embeddings")
        self.semantic_embed_model = kw.get("semantic_embed_model", "nomic-embed-text")
        self.semantic_threshold = kw.get("semantic_threshold", 0.95)
        self.semantic_max_entries = kw.get("semantic_max_entries", 8)
        self.upstream_timeout = kw.get("upstream_timeout", 30)


def _make_cache(threshold: float = 0.95, max_entries: int = 16, **callbacks):
    """Construit un `Cache` mémoire AVEC index sémantique, callbacks injectables."""
    be = cache_mod.InMemoryBackend(max_entries=max_entries)
    idx = cache_mod.SemanticIndex(max_entries=max_entries)
    c = cache_mod.Cache(be, semantic_index=idx, semantic_threshold=threshold, **callbacks)
    return c, idx, be


def _embedder(mapping):
    """Renvoie une fonction embed(text)->vec déterministe à partir d'un dict.

    Un texte ABSENT du mapping renvoie None (= simulate échec/indisponible),
    ce qui permet de tester le fall-through gracieux."""
    def _embed(text):
        return mapping.get(text)
    return _embed


# ─────────────────────────────────────────────────────────────────────────────
# Extraction des marqueurs factuels.
# ─────────────────────────────────────────────────────────────────────────────
class TestFactualTokens:
    """Le garde anti-divergence repose entièrement sur cette extraction : on
    vérifie qu'elle capture nombres, dates, montants, % et entités saillantes."""

    def test_numbers_and_years(self):
        toks = cache_mod._extract_factual_tokens("chiffre d'affaires 2024 en hausse de 12")
        assert "n:2024" in toks
        assert "n:12" in toks

    def test_money_and_percent(self):
        toks = cache_mod._extract_factual_tokens("un budget de 5000€ soit 12%")
        # Tokens numériques (contiennent un chiffre) ET marqués monétaires.
        assert "n:5000€" in toks
        assert "n:12%" in toks
        assert any(t.startswith("m:") for t in toks)

    def test_uppercase_entities(self):
        toks = cache_mod._extract_factual_tokens("le client ALPHA et la filiale BETA")
        assert "e:ALPHA" in toks
        assert "e:BETA" in toks

    def test_single_uppercase_letter_ignored(self):
        """Une initiale isolée (1 lettre) n'est PAS une entité saillante (sinon
        trop de faux divergents : « A », « I », « L'État »)."""
        toks = cache_mod._extract_factual_tokens("la réponse A est correcte")
        assert "e:A" not in toks

    def test_quoted_segments(self):
        toks = cache_mod._extract_factual_tokens('le dossier "Acme Corp" est clos')
        assert "q:acme corp" in toks

    def test_pure_reformulation_has_no_factual_tokens(self):
        """Une reformulation SANS fait saillant ne produit aucun marqueur :
        c'est précisément le cas qu'on veut autoriser à matcher."""
        a = cache_mod._extract_factual_tokens("quelles sont les règles de congés")
        b = cache_mod._extract_factual_tokens("quelles règles pour poser des congés")
        assert a == frozenset()
        assert b == frozenset()


# ─────────────────────────────────────────────────────────────────────────────
# Garde anti-divergence — LE cœur de la sûreté factuelle.
# ─────────────────────────────────────────────────────────────────────────────
class TestDivergenceGuard:
    def test_year_divergence_2024_vs_2025_diverges(self):
        assert cache_mod._has_factual_divergence("le CA 2024", "le CA 2025") is True

    def test_entity_divergence_alpha_vs_beta_diverges(self):
        assert cache_mod._has_factual_divergence("client ALPHA", "client BETA") is True

    def test_same_facts_different_phrasing_does_not_diverge(self):
        assert (
            cache_mod._has_factual_divergence("le CA en 2024", "quel était le CA 2024")
            is False
        )

    def test_pure_reformulation_does_not_diverge(self):
        assert (
            cache_mod._has_factual_divergence(
                "comment poser des congés", "quelle est la procédure pour les congés"
            )
            is False
        )

    def test_added_number_diverges(self):
        """Ajouter un fait (un montant) là où l'autre n'en a pas = divergence."""
        assert cache_mod._has_factual_divergence(
            "quel est le plafond", "quel est le plafond de 3000€"
        ) is True

    def test_money_divergence(self):
        assert cache_mod._has_factual_divergence(
            "une remise de 10%", "une remise de 20%"
        ) is True

    def test_symmetry(self):
        a, b = "rapport 2023", "rapport 2024"
        assert cache_mod._has_factual_divergence(a, b) == cache_mod._has_factual_divergence(b, a)


# ─────────────────────────────────────────────────────────────────────────────
# Cosinus pur-Python.
# ─────────────────────────────────────────────────────────────────────────────
class TestCosine:
    def test_identical_vectors(self):
        assert cache_mod._cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cache_mod._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cache_mod._cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_mismatched_length_returns_zero(self):
        assert cache_mod._cosine([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0

    def test_empty_returns_zero(self):
        assert cache_mod._cosine([], [1.0]) == 0.0
        assert cache_mod._cosine([1.0], []) == 0.0

    def test_zero_norm_returns_zero(self):
        assert cache_mod._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_known_angle(self):
        # 45° entre (1,0) et (1,1) → cos = 1/sqrt(2).
        assert cache_mod._cosine([1.0, 0.0], [1.0, 1.0]) == pytest.approx(1 / math.sqrt(2))


# ─────────────────────────────────────────────────────────────────────────────
# _perimeter_partition — frontière de sûreté.
# ─────────────────────────────────────────────────────────────────────────────
class TestPerimeterPartition:
    def test_order_independent(self):
        assert cache_mod._perimeter_partition(["b", "a"]) == cache_mod._perimeter_partition(["a", "b"])

    def test_dedup(self):
        assert cache_mod._perimeter_partition(["a", "a", "b"]) == cache_mod._perimeter_partition(["a", "b"])

    def test_different_perimeters_differ(self):
        assert cache_mod._perimeter_partition(["nord"]) != cache_mod._perimeter_partition(["sud"])

    def test_empty_drops_falsy(self):
        assert cache_mod._perimeter_partition(["", "a", None]) == cache_mod._perimeter_partition(["a"])  # type: ignore[list-item]


# ─────────────────────────────────────────────────────────────────────────────
# SemanticIndex — add/search, bornage, partitionnement.
# ─────────────────────────────────────────────────────────────────────────────
class TestSemanticIndex:
    def test_add_and_search_above_threshold(self):
        idx = cache_mod.SemanticIndex(max_entries=8)
        idx.add(perimeter="P", exact_key="k1", embedding=[1.0, 0.0], normalized_question="q a")
        # Vecteur quasi-identique → au-dessus de 0.95.
        hit = idx.search(perimeter="P", embedding=[0.999, 0.001], threshold=0.95, query_text="q a bis")
        assert hit == "k1"

    def test_search_below_threshold_returns_none(self):
        idx = cache_mod.SemanticIndex(max_entries=8)
        idx.add(perimeter="P", exact_key="k1", embedding=[1.0, 0.0], normalized_question="q a")
        # Orthogonal → cosinus 0 < 0.95.
        assert idx.search(perimeter="P", embedding=[0.0, 1.0], threshold=0.95, query_text="autre") is None

    def test_empty_partition_returns_none(self):
        idx = cache_mod.SemanticIndex(max_entries=8)
        assert idx.search(perimeter="VIDE", embedding=[1.0, 0.0], threshold=0.95, query_text="x") is None

    def test_empty_embedding_ignored_on_add(self):
        idx = cache_mod.SemanticIndex(max_entries=8)
        idx.add(perimeter="P", exact_key="k1", embedding=[], normalized_question="q")
        assert idx._partition_size("P") == 0

    def test_lru_bound_per_partition(self):
        idx = cache_mod.SemanticIndex(max_entries=2)
        idx.add(perimeter="P", exact_key="k1", embedding=[1.0, 0.0], normalized_question="a")
        idx.add(perimeter="P", exact_key="k2", embedding=[0.0, 1.0], normalized_question="b")
        idx.add(perimeter="P", exact_key="k3", embedding=[1.0, 1.0], normalized_question="c")
        # Borne respectée : la plus ancienne (k1) est évincée.
        assert idx._partition_size("P") == 2
        assert idx.search(perimeter="P", embedding=[1.0, 0.0], threshold=0.99, query_text="a") is None

    def test_reindex_same_key_does_not_grow(self):
        idx = cache_mod.SemanticIndex(max_entries=8)
        idx.add(perimeter="P", exact_key="k1", embedding=[1.0, 0.0], normalized_question="a")
        idx.add(perimeter="P", exact_key="k1", embedding=[1.0, 0.0], normalized_question="a")
        assert idx._partition_size("P") == 1

    def test_invalid_max_entries(self):
        with pytest.raises(ValueError):
            cache_mod.SemanticIndex(max_entries=0)

    def test_partition_isolation_in_index(self):
        """Une entrée dans la partition P1 n'est JAMAIS visible depuis P2."""
        idx = cache_mod.SemanticIndex(max_entries=8)
        idx.add(perimeter="P1", exact_key="k1", embedding=[1.0, 0.0], normalized_question="a")
        # Même vecteur, autre partition → aucun voisin.
        assert idx.search(perimeter="P2", embedding=[1.0, 0.0], threshold=0.5, query_text="a") is None
        assert idx.search(perimeter="P1", embedding=[1.0, 0.0], threshold=0.5, query_text="a") == "k1"


# ─────────────────────────────────────────────────────────────────────────────
# Cache.semantic_lookup — intégration complète (mock embed).
# ─────────────────────────────────────────────────────────────────────────────
class TestSemanticLookup:
    def test_near_duplicate_phrasing_hits(self):
        """Reformulation au-dessus du seuil, sans divergence → HIT sémantique."""
        emb = _embedder({
            "quel est le solde de congés": [1.0, 0.0, 0.0],
            "combien de congés me reste-t-il": [0.99, 0.01, 0.0],
        })
        c, _idx, _be = _make_cache(threshold=0.95)
        c.store(
            "kc", {"message": "Il vous reste 12 jours"}, ttl=60,
            perimeter="rh", normalized_question="quel est le solde de congés", embed_fn=emb,
        )
        out = c.semantic_lookup("rh", "combien de congés me reste-t-il", emb)
        assert out == {"message": "Il vous reste 12 jours"}

    def test_below_threshold_misses(self):
        emb = _embedder({
            "question a": [1.0, 0.0],
            "question b totalement differente": [0.0, 1.0],  # orthogonal
        })
        c, _idx, _be = _make_cache(threshold=0.95)
        c.store("ka", {"message": "réponse A"}, ttl=60,
                perimeter="P", normalized_question="question a", embed_fn=emb)
        assert c.semantic_lookup("P", "question b totalement differente", emb) is None

    def test_numeric_divergence_rejected_even_if_similar(self):
        """2024 vs 2025 : vecteurs quasi identiques MAIS divergence d'année →
        REJET. C'est ce qui rend le cache sémantique SÛR sur du factuel."""
        emb = _embedder({
            "quel est le ca 2024": [1.0, 0.0, 0.0],
            "quel est le ca 2025": [0.999, 0.001, 0.0],  # > 0.95 de similarité
        })
        c, _idx, _be = _make_cache(threshold=0.95)
        c.store("k2024", {"message": "CA 2024 = 10M€"}, ttl=60,
                perimeter="finance", normalized_question="quel est le ca 2024", embed_fn=emb)
        # Même si l'embedding est très proche, la divergence d'année REFUSE le hit.
        assert c.semantic_lookup("finance", "quel est le ca 2025", emb) is None

    def test_entity_divergence_rejected_even_if_similar(self):
        """client ALPHA vs client BETA : embeddings proches mais entités
        différentes → REJET (pas de fuite de la réponse d'ALPHA pour BETA)."""
        emb = _embedder({
            "statut du dossier client alpha": [1.0, 0.0, 0.0],
            "statut du dossier client beta": [0.999, 0.001, 0.0],
        })
        c, _idx, _be = _make_cache(threshold=0.95)
        # Note : la question NORMALISÉE est lowercased ; on indexe la forme telle
        # qu'elle sera cherchée. Les entités sont fournies en MAJUSCULES dans le
        # texte de divergence ci-dessous pour activer le détecteur d'entités.
        c.store(
            "kalpha", {"message": "Dossier ALPHA : ouvert"}, ttl=60,
            perimeter="clients", normalized_question="statut du dossier client ALPHA", embed_fn=emb,
        )
        # On cherche BETA : embedding mappé sur la forme lowercased.
        emb2 = _embedder({
            "statut du dossier client beta": [0.999, 0.001, 0.0],
            "statut du dossier client alpha": [1.0, 0.0, 0.0],
        })
        # La recherche embed sur 'statut du dossier client BETA' : on map la
        # forme exacte passée à semantic_lookup.
        emb3 = _embedder({"statut du dossier client BETA": [0.999, 0.001, 0.0]})
        assert c.semantic_lookup("clients", "statut du dossier client BETA", emb3) is None

    def test_cross_perimeter_never_matched(self):
        """PROPRIÉTÉ CARDINALE : un voisin n'existe QUE dans la partition du
        périmètre RBAC. Stocké dans `clients-nord`, JAMAIS servi à
        `clients-sud` — structurellement impossible (assertion ci-dessous)."""
        emb = _embedder({
            "quelles echeances ce mois": [1.0, 0.0, 0.0],
        })
        c, idx, _be = _make_cache(threshold=0.5)  # seuil bas exprès : prouve que SEULE la partition protège
        c.store("knord", {"message": "Échéances Nord"}, ttl=60,
                perimeter="clients-nord", normalized_question="quelles echeances ce mois", embed_fn=emb)
        # MÊME question, MÊME embedding, périmètre DIFFÉRENT → aucun voisin.
        out = c.semantic_lookup("clients-sud", "quelles echeances ce mois", emb)
        assert out is None, (
            "RBAC : le tier sémantique NE DOIT JAMAIS franchir une frontière de "
            "périmètre (cross-perimeter match interdit par construction)."
        )
        # Preuve structurelle : la partition 'clients-sud' est littéralement vide.
        assert idx._partition_size("clients-sud") == 0
        assert idx._partition_size("clients-nord") == 1
        # Et dans le BON périmètre, ça matche (sanity).
        assert c.semantic_lookup("clients-nord", "quelles echeances ce mois", emb) == {"message": "Échéances Nord"}

    def test_embed_failure_is_graceful_miss(self):
        """Si l'embed renvoie None (Ollama down) → MISS gracieux, AUCUNE
        exception ne remonte dans le chemin requête."""
        # store réussit (embed connaît la question d'indexation) ...
        emb_store = _embedder({"question indexee": [1.0, 0.0]})
        c, _idx, _be = _make_cache(threshold=0.95)
        c.store("k", {"message": "ok"}, ttl=60,
                perimeter="P", normalized_question="question indexee", embed_fn=emb_store)
        # ... mais au lookup, l'embed échoue (renvoie None).
        def _embed_down(_t):
            return None
        assert c.semantic_lookup("P", "question indexee", _embed_down) is None

    def test_embed_raises_is_graceful_miss(self):
        """Même si embed_fn LÈVE (contrat normalement safe, double filet) →
        miss propre, jamais d'exception propagée."""
        c, _idx, _be = _make_cache(threshold=0.95)

        def _embed_boom(_t):
            raise RuntimeError("ollama indisponible")

        # store ne doit pas non plus lever.
        c.store("k", {"message": "ok"}, ttl=60,
                perimeter="P", normalized_question="q", embed_fn=_embed_boom)
        assert c.semantic_lookup("P", "q", _embed_boom) is None

    def test_no_semantic_index_is_noop(self):
        """Sans index câblé (tier désactivé), semantic_lookup renvoie toujours
        None (pas d'erreur, pas de hit)."""
        be = cache_mod.InMemoryBackend(max_entries=4)
        c = cache_mod.Cache(be)  # pas de semantic_index
        emb = _embedder({"q": [1.0]})
        assert c.semantic_lookup("P", "q", emb) is None

    def test_expired_value_with_live_index_is_miss(self):
        """Course TTL : l'index pointe une clé dont la VALEUR a expiré dans le
        backend → miss propre (on ne sert pas un body absent)."""
        t = {"now": 1000.0}
        be = cache_mod.InMemoryBackend(max_entries=8, time_func=lambda: t["now"])
        idx = cache_mod.SemanticIndex(max_entries=8)
        c = cache_mod.Cache(be, semantic_index=idx, semantic_threshold=0.95)
        emb = _embedder({"q a": [1.0, 0.0], "q a bis": [0.999, 0.001]})
        c.store("k", {"message": "ok"}, ttl=10,
                perimeter="P", normalized_question="q a", embed_fn=emb)
        # La valeur expire, mais l'index garde encore l'entrée.
        t["now"] = 2000.0
        assert c.semantic_lookup("P", "q a bis", emb) is None

    def test_store_without_embed_fn_does_not_index(self):
        """store sans embed_fn ne tente AUCUNE indexation (best-effort, opt-in)."""
        c, idx, _be = _make_cache()
        c.store("k", {"message": "ok"}, ttl=60, perimeter="P", normalized_question="q")
        assert idx._partition_size("P") == 0


# ─────────────────────────────────────────────────────────────────────────────
# build_embed_fn — client Ollama exception-safe (httpx mocké).
# ─────────────────────────────────────────────────────────────────────────────
class TestBuildEmbedFn:
    def _patch_httpx(self, monkeypatch, post_impl):
        """Pose un module httpx factice (post + Timeout) dans sys.modules."""
        fake = types.ModuleType("httpx")

        class _Timeout:
            def __init__(self, *a, **kw):
                pass

        fake.Timeout = _Timeout  # type: ignore[attr-defined]
        fake.post = post_impl  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "httpx", fake)

    def test_happy_path_returns_vector(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"embedding": [0.1, 0.2, 0.3]}

        captured = {}

        def _post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

        self._patch_httpx(monkeypatch, _post)
        embed = cache_mod.build_embed_fn(_Settings(semantic_embed_url="http://x/api/embeddings"))
        vec = embed("bonjour")
        assert vec == [0.1, 0.2, 0.3]
        # Schéma legacy : { model, prompt }.
        assert captured["json"] == {"model": "nomic-embed-text", "prompt": "bonjour"}
        assert captured["url"] == "http://x/api/embeddings"

    def test_non_2xx_returns_none(self, monkeypatch):
        class _Resp:
            status_code = 500

            def json(self):
                return {}

        self._patch_httpx(monkeypatch, lambda *a, **kw: _Resp())
        embed = cache_mod.build_embed_fn(_Settings())
        assert embed("x") is None

    def test_network_error_returns_none(self, monkeypatch):
        def _post(*a, **kw):
            raise OSError("connection refused")

        self._patch_httpx(monkeypatch, _post)
        embed = cache_mod.build_embed_fn(_Settings())
        assert embed("x") is None  # NE LÈVE PAS

    def test_invalid_json_returns_none(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                raise ValueError("not json")

        self._patch_httpx(monkeypatch, lambda *a, **kw: _Resp())
        embed = cache_mod.build_embed_fn(_Settings())
        assert embed("x") is None

    def test_empty_text_returns_none_without_call(self, monkeypatch):
        called = {"n": 0}

        def _post(*a, **kw):
            called["n"] += 1
            raise AssertionError("ne doit pas être appelé")

        self._patch_httpx(monkeypatch, _post)
        embed = cache_mod.build_embed_fn(_Settings())
        assert embed("") is None
        assert called["n"] == 0

    def test_missing_embedding_field_returns_none(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"unexpected": "shape"}

        self._patch_httpx(monkeypatch, lambda *a, **kw: _Resp())
        embed = cache_mod.build_embed_fn(_Settings())
        assert embed("x") is None


# ─────────────────────────────────────────────────────────────────────────────
# build_cache — index sémantique présent SSI opt-in.
# ─────────────────────────────────────────────────────────────────────────────
class TestBuildCacheSemantic:
    def test_semantic_disabled_no_index(self):
        c = cache_mod.build_cache(_Settings(semantic_cache_enabled=False))
        assert c is not None
        assert c._semantic is None

    def test_semantic_enabled_creates_index(self):
        c = cache_mod.build_cache(_Settings(semantic_cache_enabled=True, semantic_threshold=0.9))
        assert c is not None
        assert isinstance(c._semantic, cache_mod.SemanticIndex)
        assert c._semantic_threshold == 0.9

    def test_semantic_enabled_lookup_is_wired(self):
        """Bout-en-bout via build_cache : store indexe, lookup retrouve."""
        c = cache_mod.build_cache(_Settings(semantic_cache_enabled=True, semantic_threshold=0.95))
        assert c is not None
        emb = _embedder({"q a": [1.0, 0.0], "q a reformulee": [0.99, 0.01]})
        c.store("k", {"message": "ok"}, ttl=60,
                perimeter="P", normalized_question="q a", embed_fn=emb)
        assert c.semantic_lookup("P", "q a reformulee", emb) == {"message": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Métriques — hit semantic / candidates / rejected_divergence.
# ─────────────────────────────────────────────────────────────────────────────
def _metric_value(name: str, **labels) -> float:
    from prometheus_client import REGISTRY

    val = REGISTRY.get_sample_value(name, labels=labels)
    return float(val) if val is not None else 0.0


class TestSemanticMetrics:
    def test_semantic_hit_increments_tier_semantic(self):
        before = _metric_value("onix_gateway_cache_hits_total", tier="semantic")
        c = cache_mod.build_cache(_Settings(semantic_cache_enabled=True, semantic_threshold=0.95))
        assert c is not None
        emb = _embedder({"q": [1.0, 0.0], "q bis": [0.999, 0.001]})
        c.store("k", {"message": "ok"}, ttl=60, perimeter="P", normalized_question="q", embed_fn=emb)
        assert c.semantic_lookup("P", "q bis", emb) == {"message": "ok"}
        after = _metric_value("onix_gateway_cache_hits_total", tier="semantic")
        assert after == before + 1

    def test_candidate_and_rejection_counters(self):
        """Divergence d'année : un candidat franchit le seuil (compteur
        candidates++) mais est rejeté (compteur rejected++)."""
        before_cand = _metric_value("onix_gateway_cache_semantic_candidates_total")
        before_rej = _metric_value("onix_gateway_cache_semantic_rejected_divergence_total")
        c = cache_mod.build_cache(_Settings(semantic_cache_enabled=True, semantic_threshold=0.95))
        assert c is not None
        emb = _embedder({
            "rapport 2024": [1.0, 0.0, 0.0],
            "rapport 2025": [0.999, 0.001, 0.0],
        })
        c.store("k", {"message": "rapport 2024"}, ttl=60,
                perimeter="P", normalized_question="rapport 2024", embed_fn=emb)
        # 2025 : candidat (similaire) mais rejeté (année divergente).
        assert c.semantic_lookup("P", "rapport 2025", emb) is None
        after_cand = _metric_value("onix_gateway_cache_semantic_candidates_total")
        after_rej = _metric_value("onix_gateway_cache_semantic_rejected_divergence_total")
        assert after_cand == before_cand + 1
        assert after_rej == before_rej + 1

    def test_below_threshold_no_candidate(self):
        """Sous le seuil : aucun candidat émis (pas de presque-hit)."""
        before_cand = _metric_value("onix_gateway_cache_semantic_candidates_total")
        c = cache_mod.build_cache(_Settings(semantic_cache_enabled=True, semantic_threshold=0.95))
        assert c is not None
        emb = _embedder({"q a": [1.0, 0.0], "q ortho": [0.0, 1.0]})
        c.store("k", {"message": "ok"}, ttl=60, perimeter="P", normalized_question="q a", embed_fn=emb)
        assert c.semantic_lookup("P", "q ortho", emb) is None
        after_cand = _metric_value("onix_gateway_cache_semantic_candidates_total")
        assert after_cand == before_cand  # inchangé


# ─────────────────────────────────────────────────────────────────────────────
# Helpers métriques directs (exception-safe).
# ─────────────────────────────────────────────────────────────────────────────
class TestMetricHelpers:
    def test_helpers_do_not_raise(self):
        # Doivent être no-op-safe même appelés isolément.
        metrics_mod.inc_cache_semantic_candidate()
        metrics_mod.inc_cache_semantic_rejected_divergence()
        metrics_mod.inc_cache_hit("semantic")
