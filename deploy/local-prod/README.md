# deploy/local-prod — onix en « production sur machine unique »

Ce dossier fournit l'intégration **systemd** qui fait survivre la stack onix aux
redémarrages de la machine, pour une mise en **production sur UN SEUL poste**
(64 Go, 1-2 testeurs) — durcie mais **sans domaine public ni OIDC**.

Il s'appuie sur l'overlay `docker-compose.prod-local.yml` (racine du dépôt) :
healthchecks complets, démarrage ordonné (`depends_on … condition:
service_healthy`) et `restart: always`. Le runbook complet est dans
[`../../docs/PROD_LOCAL.md`](../../docs/PROD_LOCAL.md).

## Contenu

| Fichier | Rôle |
|---|---|
| `onix.service` | Unit systemd : `up -d` au boot, `down` à l'arrêt (Type=oneshot). |

## Installation

```bash
# 0. Pré-requis : .env généré (make secrets) dans le dépôt.
# 1. Adapter WorkingDirectory= dans onix.service au chemin RÉEL du dépôt.
sudo cp deploy/local-prod/onix.service /etc/systemd/system/onix.service
sudo systemctl daemon-reload
sudo systemctl enable docker        # Docker démarre au boot (pré-requis)
sudo systemctl enable --now onix    # active + démarre la stack
```

## Vérification

```bash
systemctl status onix        # doit être « active (exited) »
journalctl -u onix -f        # journaux de démarrage/arrêt
docker compose -f docker-compose.yml -f docker-compose.prod-local.yml ps
```

## Désinstallation

```bash
sudo systemctl disable --now onix
sudo rm /etc/systemd/system/onix.service
sudo systemctl daemon-reload
```

> Le `down` exécuté par l'unit **conserve** les volumes (aucune perte de
> données). Pour repartir de zéro : `make destroy` (⚠ supprime les volumes).
