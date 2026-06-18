# Scope `monitoring` — dossier agent

> **Mission** : **observabilité** de toute la stack — collecte métriques
> (Prometheus + Blackbox), logs (Loki + Promtail), tableaux de bord (Grafana),
> **alertes** (Alertmanager) et **SLO**. Le tout en surcouche Compose dédiée,
> activable indépendamment.
> **Sous-agent** : observabilité / SRE. **État** :
> [`../../ralph/state/monitoring.md`](../../ralph/state/monitoring.md).

Routeur : [`README.md`](README.md) · Projet : [`../../AGENTS.md`](../../AGENTS.md).

## 1. Mission & frontière FOSS/EE

| | |
|---|---|
| **Apporte (FOSS)** | métriques applicatives (gateway `/metrics`, actions `/metrics`) + infra, alertes actionnables + SLO, dashboards Grafana, logs centralisés Loki. Pile 100 % open-source/souveraine. |
| **À ne pas affirmer** | une alerte/dashboard ne vaut que si la **métrique sous-jacente est réellement émise**. Vérifier alerte ↔ métrique (cf. audit-reality). |

## 2. Carte du code — [`../../monitoring/`](../../monitoring/)

| Fichier | Rôle |
|---|---|
| [`docker-compose.monitoring.yml`](../../monitoring/docker-compose.monitoring.yml) | Surcouche Compose de la pile observabilité. |
| [`prometheus/prometheus.yml`](../../monitoring/prometheus/prometheus.yml) | Scrape config (cibles : gateway, actions, infra). |
| [`prometheus/rules/onix-alerts.yml`](../../monitoring/prometheus/rules/onix-alerts.yml) | Règles d'**alerte**. |
| [`prometheus/rules/onix-slo.yml`](../../monitoring/prometheus/rules/onix-slo.yml) | Règles **SLO**. |
| [`alertmanager/alertmanager.yml`](../../monitoring/alertmanager/alertmanager.yml) | Routage des alertes. |
| [`blackbox/blackbox.yml`](../../monitoring/blackbox/blackbox.yml) | Sondes synthétiques (probes HTTP). |
| [`loki/loki-config.yml`](../../monitoring/loki/loki-config.yml) · [`promtail/promtail-config.yml`](../../monitoring/promtail/promtail-config.yml) | Logs centralisés + collecte. |
| [`grafana/provisioning/`](../../monitoring/grafana/provisioning/) | Datasources + provisioning des dashboards. |
| [`grafana/dashboards/`](../../monitoring/grafana/dashboards/) | Dashboards : `onix-gateway.json`, `onix-actions.json`, `onix-infra.json`. |

## 3. Commandes

```bash
make monitor-up                      # démarre la pile observabilité
make monitor-config                  # valide la compose monitoring (config -q)
make monitor-logs                    # logs de la pile
make monitor-down                    # arrête la pile
make lint                            # yamllint (workflows + monitoring)
```

## 4. Tests & preuves

- `make monitor-config` (compose valide) + `make lint` (YAML monitoring valide) — tous
  deux inclus dans `make test`.
- **Cohérence** : chaque alerte/SLO doit référencer une métrique réellement exposée
  (gateway/actions `/metrics`). Preuves : [`../OBSERVABILITY.md`](../OBSERVABILITY.md).

## 5. Invariants & pièges

- **Pas d'alerte fantôme** : ne pas référencer une métrique non émise (faux positif/négatif).
- `/metrics` est **indépendant** de la stack monitoring : la passerelle/actions exposent
  toujours `/metrics` ; la pile monitoring est **opt-in** (ne pas créer de fausse
  dépendance « /metrics nécessite WS2 »).
- YAML valide (`yamllint` relaxed) — le `lint` casse sinon.

## 6. Observabilité

C'est **le** scope d'observabilité. Sources scrappées : gateway (`onix-gateway.json`),
actions (`onix-actions.json`), infra (`onix-infra.json`). Détail :
[`../OBSERVABILITY.md`](../OBSERVABILITY.md).

## 7. Docs de fond

[`../OBSERVABILITY.md`](../OBSERVABILITY.md) · [`../RUNBOOK.md`](../RUNBOOK.md)
(incidents/alertes) · [`../audit-onyx/60-observability-runtime.md`](../audit-onyx/60-observability-runtime.md).

## 8. Audit & journal

[`../audit-reality/monitoring.md`](../audit-reality/monitoring.md) ·
[`../../ralph/state/monitoring.md`](../../ralph/state/monitoring.md) ·
[`../../ralph/scopes/monitoring.md`](../../ralph/scopes/monitoring.md).

## 9. Sous-agent

| | |
|---|---|
| Discipline | Observabilité / SRE |
| Skills | `/code-review`, `/verify` |
| MCP | `Context7` (prometheus, grafana, loki, promtail) ; `github` |
| Cibles de preuve | `make monitor-config`, `make lint`, alertes ↔ métriques émises |

## 10. Maintenir cette fiche

Touche à `monitoring/` ⇒ mets à jour §2, vérifie alerte↔métrique, reporte dans
[`../audit-reality/monitoring.md`](../audit-reality/monitoring.md) et le journal.
Vérifie : `make docs-check`.
