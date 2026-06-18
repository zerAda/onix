{{/*
Helpers communs — noms, labels, sélecteurs. Alignés sur les conventions du
chart Helm officiel Onyx, mais factorisés pour notre déploiement HA.
*/}}

{{- define "onix.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Nom complet (préfixe de toutes les ressources). Respecte fullnameOverride.
*/}}
{{- define "onix.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "onix.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Labels communs (recommandés Kubernetes). Appliqués à TOUTES les ressources.
*/}}
{{- define "onix.labels" -}}
helm.sh/chart: {{ include "onix.chart" . }}
{{ include "onix.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: onix
{{- end -}}

{{- define "onix.selectorLabels" -}}
app.kubernetes.io/name: {{ include "onix.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Labels + sélecteur PAR COMPOSANT. Usage : {{ include "onix.componentLabels" (dict "ctx" . "component" "api") }}
*/}}
{{- define "onix.componentLabels" -}}
{{ include "onix.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "onix.componentSelector" -}}
{{ include "onix.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Nom du Secret applicatif (peut être fourni par l'intégrateur via existingSecret).
*/}}
{{- define "onix.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "onix.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Hôtes data-tier — centralisés. En HA on pointe sur les Services des subcharts /
opérateurs ; surchargeables si l'intégrateur câble un endpoint externe.
*/}}
{{- define "onix.postgresHost" -}}
{{- .Values.postgresql.host | default (printf "%s-postgresql-rw" (include "onix.fullname" .)) -}}
{{- end -}}

{{- define "onix.opensearchHost" -}}
{{- .Values.opensearch.host | default (printf "%s-opensearch" (include "onix.fullname" .)) -}}
{{- end -}}

{{- define "onix.redisHost" -}}
{{- .Values.redis.host | default (printf "%s-redis" (include "onix.fullname" .)) -}}
{{- end -}}

{{- define "onix.minioEndpoint" -}}
{{- .Values.minio.endpoint | default (printf "http://%s-minio:9000" (include "onix.fullname" .)) -}}
{{- end -}}

{{/*
URL du broker AMQP pour la file asynchrone onix-actions (Celery).
*/}}
{{- define "onix.brokerUrl" -}}
{{- printf "amqp://%s:$(BROKER_PASSWORD)@%s-actions-broker:5672//" .Values.actionsQueue.broker.username (include "onix.fullname" .) -}}
{{- end -}}

{{/*
Image (repository:tag) avec repli sur global.imageTag puis appVersion si tag vide.
*/}}
{{- define "onix.image" -}}
{{- $repo := .image.repository -}}
{{- $tag := .image.tag | default .root.Values.global.imageTag | default .root.Chart.AppVersion -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/*
Anti-affinité pod (étale les répliques d'un composant sur des nœuds distincts).
Usage: {{ include "onix.podAntiAffinity" (dict "ctx" . "component" "api") | nindent 6 }}
*/}}
{{- define "onix.podAntiAffinity" -}}
{{- if .ctx.Values.podAntiAffinity.enabled -}}
affinity:
  podAntiAffinity:
  {{- if eq .ctx.Values.podAntiAffinity.type "hard" }}
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: {{ .ctx.Values.podAntiAffinity.topologyKey }}
        labelSelector:
          matchLabels:
            {{- include "onix.componentSelector" (dict "ctx" .ctx "component" .component) | nindent 12 }}
  {{- else }}
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: {{ .ctx.Values.podAntiAffinity.topologyKey }}
          labelSelector:
            matchLabels:
              {{- include "onix.componentSelector" (dict "ctx" .ctx "component" .component) | nindent 14 }}
  {{- end }}
{{- end -}}
{{- end -}}

{{/*
Variables d'environnement SECRÈTES data-tier, injectées depuis le Secret K8s.
Noms de clés FIXES (cf. values.secrets). Réutilisé par api/background/actions/...
Usage: env:\n{{ include "onix.dataTierSecretEnv" . | nindent 12 }}
*/}}
{{- define "onix.dataTierSecretEnv" -}}
{{- $secret := include "onix.secretName" . -}}
- name: POSTGRES_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: POSTGRES_PASSWORD } }
- name: OPENSEARCH_ADMIN_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: OPENSEARCH_ADMIN_PASSWORD } }
- name: REDIS_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: REDIS_PASSWORD } }
- name: S3_AWS_ACCESS_KEY_ID
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: S3_AWS_ACCESS_KEY_ID } }
- name: S3_AWS_SECRET_ACCESS_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: S3_AWS_SECRET_ACCESS_KEY } }
- name: SECRET
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: SECRET } }
- name: USER_AUTH_SECRET
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: USER_AUTH_SECRET } }
{{- end -}}

{{/*
Variables d'environnement SECRÈTES de onix-actions (WS2), injectées depuis le
Secret K8s. Noms de clés FIXES, lus par le code (security.py / audit_log.py /
caller_identity.py). Sans ces clés en HA : /admin/* tombe en 403 fail-closed et
la chaîne d'audit retombe en SHA-256 au lieu du HMAC promis. Les VALEURS viennent
du Secret (existingSecret ou secrets.create) — JAMAIS du repo.
Usage: env:\n{{ include "onix.actionsSecretEnv" . | nindent 12 }}
*/}}
{{- define "onix.actionsSecretEnv" -}}
{{- $secret := include "onix.secretName" . -}}
- name: ONIX_ACTIONS_ADMIN_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: ONIX_ACTIONS_ADMIN_KEY } }
- name: ONIX_ACTIONS_AUDIT_HMAC_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: ONIX_ACTIONS_AUDIT_HMAC_KEY } }
- name: ONIX_ACTIONS_CALLER_HMAC_SECRET
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: ONIX_ACTIONS_CALLER_HMAC_SECRET } }
{{- end -}}

{{/*
Variables d'environnement SECRÈTES de l'access-gateway, injectées depuis le
Secret K8s (mêmes clés que deploy/azure/access-gateway.yaml). Noms FIXES, lus par
le code (config.py / audit.py). GATEWAY_CACHE_REDIS_URL contient la clé Redis (TLS)
=> SECRET, jamais en ConfigMap. Usage: env:\n{{ include "onix.gatewaySecretEnv" . | nindent 12 }}
*/}}
{{- define "onix.gatewaySecretEnv" -}}
{{- $secret := include "onix.secretName" . -}}
- name: GATEWAY_CACHE_HMAC_SECRET
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: GATEWAY_CACHE_HMAC_SECRET } }
- name: GATEWAY_AUDIT_SALT
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: GATEWAY_AUDIT_SALT } }
- name: GATEWAY_ONYX_API_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: GATEWAY_ONYX_API_KEY } }
- name: GATEWAY_GRAPH_CLIENT_ID
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: GATEWAY_GRAPH_CLIENT_ID } }
- name: GATEWAY_GRAPH_CLIENT_SECRET
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: GATEWAY_GRAPH_CLIENT_SECRET } }
- name: GATEWAY_CACHE_REDIS_URL
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: GATEWAY_CACHE_REDIS_URL } }
{{- end -}}

{{/*
HorizontalPodAutoscaler générique (autoscaling/v2).
Usage: {{ include "onix.hpa" (dict "ctx" . "component" "api" "target" "<deploy>" "cfg" .Values.api.autoscaling) }}
*/}}
{{- define "onix.hpa" -}}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ .target }}
  labels:
    {{- include "onix.componentLabels" (dict "ctx" .ctx "component" .component) | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ .target }}
  minReplicas: {{ .cfg.minReplicas }}
  maxReplicas: {{ .cfg.maxReplicas }}
  metrics:
    {{- if .cfg.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .cfg.targetCPUUtilizationPercentage }}
    {{- end }}
    {{- if .cfg.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ .cfg.targetMemoryUtilizationPercentage }}
    {{- end }}
{{- end -}}

{{/*
PodDisruptionBudget générique.
Usage: {{ include "onix.pdb" (dict "ctx" . "component" "api" "cfg" .Values.api.pdb) }}
*/}}
{{- define "onix.pdb" -}}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "onix.fullname" .ctx }}-{{ .component }}
  labels:
    {{- include "onix.componentLabels" (dict "ctx" .ctx "component" .component) | nindent 4 }}
spec:
  {{- if .cfg.minAvailable }}
  minAvailable: {{ .cfg.minAvailable }}
  {{- else if .cfg.maxUnavailable }}
  maxUnavailable: {{ .cfg.maxUnavailable }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "onix.componentSelector" (dict "ctx" .ctx "component" .component) | nindent 6 }}
{{- end -}}
