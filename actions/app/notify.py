"""notify — Notification générique (onix-actions).

Deux providers, configurables, sans dépendance cloud :
  * webhook : POST JSON compatible Slack / Mattermost / Teams (clé `text`) ou
    payload arbitraire vers une URL (`ONIX_NOTIFY_WEBHOOK` par défaut) ;
  * smtp    : email via un serveur SMTP fourni par le client (`ONIX_SMTP_*`).

Ne lève jamais une exception non maîtrisée vers l'appelant : retourne un statut.
Aucune donnée sensible loggée (on ne journalise pas le corps).
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("onix.actions.notify")

# Timeouts par défaut (surchargés par env). Connexion courte pour détecter vite
# une cible injoignable ; lecture plus longue pour la réponse.
_WEBHOOK_CONNECT_TIMEOUT = 5.0
_SMTP_DEFAULT_TIMEOUT = 20.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_webhook() -> Optional[str]:
    url = os.environ.get("ONIX_NOTIFY_WEBHOOK", "").strip()
    return url or None


def _webhook_timeout(default_read: float) -> httpx.Timeout:
    try:
        read = float(os.environ.get("ONIX_NOTIFY_TIMEOUT", default_read))
    except (TypeError, ValueError):
        read = default_read
    return httpx.Timeout(connect=_WEBHOOK_CONNECT_TIMEOUT, read=read, write=10.0, pool=5.0)


def send_webhook(
    text: str,
    *,
    url: Optional[str] = None,
    extra: Optional[dict] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    target = (url or _default_webhook())
    if not target:
        return {"status": "skipped", "reason": "Aucune URL webhook configurée."}
    if not target.lower().startswith(("http://", "https://")):
        return {"status": "error", "reason": "URL webhook invalide."}
    # `text` = format Slack/Mattermost/Teams le plus courant.
    payload: Dict[str, Any] = {"text": text}
    if extra:
        payload.update(extra)
    try:
        resp = httpx.post(target, json=payload, timeout=_webhook_timeout(timeout))
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("Webhook a répondu en erreur (http=%s)", resp.status_code)
        return {"status": "sent" if ok else "error", "http_status": resp.status_code}
    except Exception as e:
        # Cible down / DNS / timeout : on renvoie un statut propre (jamais 500).
        logger.warning("Échec d'envoi webhook: %s", type(e).__name__)
        return {"status": "error", "reason": f"Échec d'envoi: {type(e).__name__}"}


def _smtp_timeout(default: float) -> float:
    try:
        return float(os.environ.get("ONIX_SMTP_TIMEOUT", default))
    except (TypeError, ValueError):
        return default


def send_email(
    subject: str,
    body: str,
    *,
    to: Optional[str] = None,
    timeout: float = _SMTP_DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    host = os.environ.get("ONIX_SMTP_HOST", "").strip()
    if not host:
        return {"status": "skipped", "reason": "SMTP non configuré (ONIX_SMTP_HOST)."}
    timeout = _smtp_timeout(timeout)
    port = int(os.environ.get("ONIX_SMTP_PORT", "587") or "587")
    user = os.environ.get("ONIX_SMTP_USER", "").strip() or None
    password = os.environ.get("ONIX_SMTP_PASSWORD", "") or None
    sender = os.environ.get("ONIX_SMTP_FROM", user or "onix-actions@localhost")
    recipient = to or os.environ.get("ONIX_SMTP_TO", "").strip()
    if not recipient:
        return {"status": "error", "reason": "Aucun destinataire (to / ONIX_SMTP_TO)."}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    use_ssl = _env_bool("ONIX_SMTP_SSL", False)
    # STARTTLS activé par défaut (sécurité), mais désactivable pour un relais
    # interne en clair (ONIX_SMTP_STARTTLS=false). Si demandé mais non annoncé
    # par le serveur, on n'envoie PAS en clair par erreur -> erreur explicite.
    want_starttls = _env_bool("ONIX_SMTP_STARTTLS", True)
    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as s:
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                if want_starttls:
                    if not s.has_extn("starttls"):
                        return {
                            "status": "error",
                            "reason": "Serveur SMTP sans STARTTLS (définir ONIX_SMTP_STARTTLS=false pour un relais en clair).",
                        }
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        return {"status": "sent", "provider": "smtp"}
    except Exception as e:
        # Serveur down / auth / TLS : statut propre, jamais d'exception remontée.
        logger.warning("Échec SMTP: %s", type(e).__name__)
        return {"status": "error", "reason": f"Échec SMTP: {type(e).__name__}"}


def notify(
    *,
    provider: str,
    message: str,
    subject: Optional[str] = None,
    url: Optional[str] = None,
    to: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Dict[str, Any]:
    provider = (provider or "webhook").lower()
    if provider == "webhook":
        return send_webhook(message, url=url, extra=extra)
    if provider == "smtp":
        return send_email(subject or "Notification onix-actions", message, to=to)
    return {"status": "error", "reason": f"Provider inconnu : {provider}"}
