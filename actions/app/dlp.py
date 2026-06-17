"""dlp — Prévention de fuite de données en sortie (egress) (onix-actions).

WS2 — DLP egress : les endpoints qui émettent des requêtes SORTANTES (`/notify`
webhook, `/tasks` `webhook_url`) ne doivent pouvoir contacter QUE des
destinations explicitement autorisées (allowlist). Objectif : empêcher
l'exfiltration de données (un appelant qui pousse un audit vers un domaine
attaquant) et le SSRF (cible interne arbitraire).

Politique (fail-closed et configurable) :
  * `ONIX_EGRESS_ALLOWLIST` : liste d'hôtes/domaines autorisés (séparés par des
    virgules), ex. `hooks.slack.com,mattermost.interne.local,*.corp.local`.
    Un motif `*.corp.local` autorise les sous-domaines ; `corp.local` autorise
    l'hôte exact (et, par commodité, ses sous-domaines).
  * Si l'allowlist est **vide** :
      - `ONIX_EGRESS_DEFAULT_DENY=true` (recommandé, défaut) -> tout egress est
        REFUSÉ (fail-closed) : il faut déclarer explicitement les destinations ;
      - `false` -> compat historique : egress autorisé (à éviter en prod).
  * Schéma : seul `https://` est autorisé par défaut ; `http://` n'est toléré
    que si `ONIX_EGRESS_ALLOW_HTTP=true` (relais interne en clair assumé).
  * Anti-SSRF : par défaut, les cibles qui résolvent vers des IP privées /
    loopback / link-local sont refusées, SAUF si explicitement allowlistées
    (un Mattermost interne légitime peut l'être). `ONIX_EGRESS_ALLOW_PRIVATE_IP`
    bascule ce garde-fou.

`check_egress(url)` lève `EgressDenied` (mappée en 403 par les endpoints) ou
retourne l'URL validée. Pur calcul + résolution DNS optionnelle ; aucune
dépendance externe.

Limite connue (DNS rebinding) : le contrôle anti-SSRF résout l'hôte au moment de
la validation, mais le client HTTP re-résout au moment de la connexion ; un
attaquant maîtrisant un DNS à TTL court pourrait faire pointer l'hôte vers une IP
interne entre les deux. La défense robuste reste l'**allowlist d'hôtes** (qui ne
dépend pas de la résolution) : préférez une allowlist explicite en production
plutôt que de vous reposer sur le seul filtre d'IP privée.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import List, Optional
from urllib.parse import urlparse

# Plages « internes » supplémentaires non couvertes par is_private sur toutes les
# versions de Python : CGNAT (RFC 6598) et la plage de test benchmark (RFC 2544).
_EXTRA_INTERNAL_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT (RFC 6598)
    ipaddress.ip_network("198.18.0.0/15"),   # benchmark (RFC 2544)
)


class EgressDenied(Exception):
    """Destination de sortie refusée par la politique DLP."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def allowlist() -> List[str]:
    raw = _env("ONIX_EGRESS_ALLOWLIST")
    if not raw:
        return []
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _host_matches(host: str, pattern: str) -> bool:
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        # `*.corp.local` -> n'importe quel sous-domaine (pas l'apex).
        return host.endswith(pattern[1:]) and host != pattern[2:]
    # Hôte exact OU sous-domaine de `pattern` (corp.local autorise a.corp.local).
    return host == pattern or host.endswith("." + pattern)


def _is_host_allowlisted(host: str) -> bool:
    return any(_host_matches(host, p) for p in allowlist())


def _is_private_target(host: str) -> bool:
    """Le host résout-il (ou est-il) une IP privée / loopback / link-local ?
    Best-effort : en cas d'échec de résolution, on considère « inconnu » comme
    NON privé (le refus éventuel se fait alors par l'allowlist)."""
    candidates: List[str] = []
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            candidates = [info[4][0] for info in infos]
        except Exception:
            return False
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_unspecified or ip.is_multicast
        ):
            return True
        if any(ip in net for net in _EXTRA_INTERNAL_NETS):
            return True
    return False


def check_egress(url: Optional[str]) -> str:
    """Valide une URL de sortie contre la politique DLP. Lève `EgressDenied`.

    Étapes : (1) URL bien formée + schéma autorisé ; (2) hôte présent ;
    (3) allowlist (fail-closed si vide et default-deny) ; (4) anti-SSRF IP privée.
    """
    if not url or not isinstance(url, str):
        raise EgressDenied("URL de sortie manquante.")
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()

    allow_http = _env_bool("ONIX_EGRESS_ALLOW_HTTP", False)
    allowed_schemes = {"https"} | ({"http"} if allow_http else set())
    if scheme not in allowed_schemes:
        raise EgressDenied(
            f"Schéma de sortie refusé : {scheme or '(aucun)'} "
            f"(autorisés : {', '.join(sorted(allowed_schemes))})."
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise EgressDenied("Hôte de sortie absent.")

    wl = allowlist()
    if wl:
        if not _is_host_allowlisted(host):
            raise EgressDenied(f"Destination hors allowlist : {host}.")
    else:
        # Allowlist vide -> fail-closed par défaut.
        if _env_bool("ONIX_EGRESS_DEFAULT_DENY", True):
            raise EgressDenied(
                "Aucune destination de sortie autorisée (ONIX_EGRESS_ALLOWLIST vide "
                "et politique fail-closed). Déclarez les hôtes autorisés."
            )

    # Anti-SSRF : refuser les cibles internes non explicitement allowlistées.
    if not _env_bool("ONIX_EGRESS_ALLOW_PRIVATE_IP", False):
        if _is_private_target(host) and not _is_host_allowlisted(host):
            raise EgressDenied(
                f"Cible interne/privée refusée (anti-SSRF) : {host}. "
                "Allowlistez-la explicitement si elle est légitime."
            )

    return url.strip()
